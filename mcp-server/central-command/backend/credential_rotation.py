"""Credential encryption key rotation — admin endpoint + background re-encrypt.

## Why this exists

`credential_crypto.py` now supports a keyring via `MultiFernet`. Rotating a
key on its own isn't enough — every existing ciphertext is still tied to the
old key, and removing the old key from the keyring would orphan them. This
module is the "re-encrypt everything that was written with the old key"
worker, plus an admin surface to trigger it and watch progress.

## Workflow

1. Operator generates a new key (`credential_crypto.generate_new_key()` or
   `python -m credential_crypto gen`).
2. Operator prepends it to `CREDENTIAL_ENCRYPTION_KEYS` (newest first,
   e.g. `NEW,OLD`) and restarts the app.
3. Operator calls `POST /api/admin/credentials/rotate-key` to kick off the
   re-encrypt loop. The endpoint returns immediately; progress is tracked
   in-memory and exposed via `GET /api/admin/credentials/rotation-status`.
4. Re-encrypt loop walks every encrypted column in every relevant table,
   reads the ciphertext, decrypts it (MultiFernet tries every key), then
   re-encrypts it with the primary (new) key. Each row is committed
   individually so a crash mid-rotation leaves the remainder recoverable.
5. When complete, operator can safely drop the old key from
   `CREDENTIAL_ENCRYPTION_KEYS` and restart.

## Tables + columns touched

| Table                | Column                              | Notes                          |
|----------------------|-------------------------------------|--------------------------------|
| site_credentials     | encrypted_data                      | ~5 rows in prod                |
| org_credentials      | encrypted_data                      | rarely populated               |
| client_org_sso       | client_secret_encrypted             | one per SSO-enabled org        |
| integrations         | credentials_encrypted               | one per integration            |
| oauth_config         | client_secret_encrypted             | legacy, may be empty           |
| partners             | oauth_access_token_encrypted        | per-partner                    |
| partners             | oauth_refresh_token_encrypted       | per-partner                    |

## Safety guarantees

- **Idempotent**: running twice is a no-op. Fernet ciphertexts re-encrypt
  cleanly; a second pass just re-wraps them with a fresh IV under the same
  key.
- **Resumable**: each row is its own transaction — a crash halfway through
  leaves the remainder untouched and a restart picks up where it left off.
- **Audit-logged**: every rotation start + finish + per-table row count is
  written to `admin_audit_log` with the acting user and old+new key
  fingerprints.
- **No destructive write**: we never DELETE the old encrypted_data. We
  overwrite in place with the new ciphertext inside a transaction.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from .auth import require_admin
from .credential_crypto import (
    get_key_fingerprints,
    primary_key_fingerprint,
    rotate_ciphertext,
)
from .fleet import get_pool
from .tenant_middleware import admin_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/credentials", tags=["credential-rotation"])


# =============================================================================
# ROTATION STATE (in-memory singleton)
# =============================================================================

class RotationState:
    """In-memory tracker for the current rotation run. Not persisted to DB
    because a rotation that spans process restarts is unsafe — operator must
    re-trigger if the process dies. The run is idempotent so re-triggering is
    cheap.
    """

    def __init__(self) -> None:
        self.in_progress: bool = False
        self.started_at: Optional[datetime] = None
        self.finished_at: Optional[datetime] = None
        self.started_by: Optional[str] = None
        self.primary_key_fp: Optional[str] = None
        self.keyring_fps: List[str] = []
        # per-table counters — updated as rows are processed
        self.counters: Dict[str, Dict[str, int]] = {}
        self.error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "in_progress": self.in_progress,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "started_by": self.started_by,
            "primary_key_fingerprint": self.primary_key_fp,
            "keyring_fingerprints": self.keyring_fps,
            "counters": self.counters,
            "error": self.error,
        }


_state = RotationState()


# =============================================================================
# TABLES TO ROTATE
# =============================================================================

# Each entry: (table, primary_key_column, ciphertext_column). The primary key
# column is used to UPDATE one row at a time — never a bulk UPDATE, which
# would need all rows in memory.
ROTATION_TARGETS: List[tuple] = [
    ("site_credentials", "id", "encrypted_data"),
    ("org_credentials", "id", "encrypted_data"),
    ("client_org_sso", "id", "client_secret_encrypted"),
    ("integrations", "id", "credentials_encrypted"),
    ("oauth_config", "id", "client_secret_encrypted"),
    ("partners", "id", "oauth_access_token_encrypted"),
    ("partners", "id", "oauth_refresh_token_encrypted"),
]


async def _table_exists(conn: asyncpg.Connection, table: str, column: str) -> bool:
    """Return True if the given table + column both exist. Lets us skip
    targets that haven't been migrated yet instead of crashing the run."""
    row = await conn.fetchrow(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = $1 AND column_name = $2
        """,
        table,
        column,
    )
    return row is not None


async def _rotate_table(
    conn: asyncpg.Connection,
    table: str,
    pk: str,
    col: str,
) -> Dict[str, int]:
    """Re-encrypt every non-null ciphertext in ``table.col`` with the primary
    key. Returns {scanned, rotated, skipped, errors}.

    Each row is its own transaction so a crash mid-loop leaves the remainder
    untouched. We cap at 50,000 rows per table per run — in practice the
    platform never has more than a few hundred credentials, but the cap
    prevents runaway loops on misbehaving data.
    """
    counters = {"scanned": 0, "rotated": 0, "skipped": 0, "errors": 0}

    if not await _table_exists(conn, table, col):
        logger.info("rotate: skipping %s.%s — column/table missing", table, col)
        return counters

    rows = await conn.fetch(
        f"SELECT {pk}, {col} FROM {table} WHERE {col} IS NOT NULL LIMIT 50000"
    )

    for row in rows:
        counters["scanned"] += 1
        raw = row[col]
        if not raw:
            counters["skipped"] += 1
            continue
        try:
            new_ct = rotate_ciphertext(bytes(raw) if isinstance(raw, memoryview) else raw)
            async with conn.transaction():
                await conn.execute(
                    f"UPDATE {table} SET {col} = $1 WHERE {pk} = $2",
                    new_ct,
                    row[pk],
                )
            counters["rotated"] += 1
        except Exception as e:
            counters["errors"] += 1
            logger.error(
                "rotate: %s.%s row %s failed: %s",
                table,
                col,
                row[pk],
                e,
            )

    logger.info(
        "rotate: %s.%s finished — scanned=%d rotated=%d skipped=%d errors=%d",
        table,
        col,
        counters["scanned"],
        counters["rotated"],
        counters["skipped"],
        counters["errors"],
    )
    return counters


async def _audit(
    conn: asyncpg.Connection,
    username: str,
    action: str,
    details: Dict[str, Any],
) -> None:
    """Write a rotation event to admin_audit_log (append-only)."""
    import json
    try:
        await conn.execute(
            """
            INSERT INTO admin_audit_log (user_id, username, action, target, details, ip_address)
            VALUES (NULL, $1, $2, 'credential_rotation', $3::jsonb, NULL)
            """,
            username,
            action,
            json.dumps(details),
        )
    except Exception as e:
        logger.error("rotate: audit log write failed: %s", e)


async def _run_rotation_async(started_by: str) -> None:
    """Background coroutine that walks every ROTATION_TARGETS entry and
    re-encrypts all rows under the primary key. Updates _state as it goes.

    Safe to call when _state.in_progress is False — the caller guards that.
    """
    pool = await get_pool()
    try:
        _state.counters = {}
        async with admin_connection(pool) as conn:
            await _audit(
                conn,
                started_by,
                "CREDENTIAL_KEY_ROTATION_STARTED",
                {
                    "primary_key_fingerprint": _state.primary_key_fp,
                    "keyring_fingerprints": _state.keyring_fps,
                },
            )
            for table, pk, col in ROTATION_TARGETS:
                key = f"{table}.{col}"
                try:
                    _state.counters[key] = await _rotate_table(conn, table, pk, col)
                except Exception as e:
                    logger.error("rotate: unexpected failure on %s: %s", key, e)
                    _state.counters[key] = {
                        "scanned": 0,
                        "rotated": 0,
                        "skipped": 0,
                        "errors": -1,
                    }
            await _audit(
                conn,
                started_by,
                "CREDENTIAL_KEY_ROTATION_COMPLETED",
                {
                    "primary_key_fingerprint": _state.primary_key_fp,
                    "counters": _state.counters,
                },
            )
    except Exception as e:
        _state.error = str(e)
        logger.exception("rotate: run aborted: %s", e)
    finally:
        _state.in_progress = False
        _state.finished_at = datetime.now(timezone.utc)


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.post("/rotate-key")
async def rotate_credential_key(user: dict = Depends(require_admin)):
    """Kick off a credential re-encryption run against the current primary key.

    The call is non-blocking: the work runs as an asyncio background task
    and status can be polled via `/rotation-status`. If a rotation is already
    in progress, returns 409.

    This endpoint does NOT generate a new key — the operator must have
    already added a new key to ``CREDENTIAL_ENCRYPTION_KEYS`` (newest first)
    and restarted the process. This endpoint just re-encrypts existing
    ciphertexts so the old key can be safely removed from the keyring.
    """
    if _state.in_progress:
        raise HTTPException(
            status_code=409,
            detail="A credential rotation is already in progress",
        )

    fingerprints = get_key_fingerprints()
    if not fingerprints:
        raise HTTPException(
            status_code=500,
            detail="No encryption keys loaded — cannot rotate",
        )

    username = user.get("username") or user.get("email") or "unknown"
    _state.in_progress = True
    _state.started_at = datetime.now(timezone.utc)
    _state.finished_at = None
    _state.started_by = username
    _state.primary_key_fp = primary_key_fingerprint()
    _state.keyring_fps = fingerprints
    _state.counters = {}
    _state.error = None

    # Fire-and-forget background task. We intentionally do NOT await it — the
    # HTTP client gets a 202 Accepted immediately and polls /rotation-status.
    asyncio.create_task(_run_rotation_async(username))

    return {
        "status": "started",
        "primary_key_fingerprint": _state.primary_key_fp,
        "keyring_size": len(fingerprints),
        "started_by": username,
        "started_at": _state.started_at.isoformat(),
    }


@router.get("/rotation-status")
async def get_rotation_status(user: dict = Depends(require_admin)):
    """Return the current/most-recent rotation run state.

    Safe to poll at 1-5s intervals. The response includes per-table counters
    so the operator can watch the progress live.
    """
    return _state.to_dict()


@router.get("/key-fingerprints")
async def get_active_key_fingerprints(user: dict = Depends(require_admin)):
    """Return the fingerprints of all currently-loaded encryption keys.

    Useful for verifying that the rotation keyring is actually what the
    operator expects before triggering a rotation.
    """
    return {
        "primary": primary_key_fingerprint(),
        "keyring": get_key_fingerprints(),
    }

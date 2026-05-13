"""Startup security-invariant check (Phase 15 enterprise-grade hygiene).

Runs once at lifespan startup. Verifies every security-critical
DB-layer protection is actually installed. Logs ERROR + writes an
admin_audit_log row for each broken invariant, so the operator sees
the failure in their dashboard and the log shipper pages on-call.

Does NOT crash the server. A broken invariant is a CREDIBILITY event,
not an availability event — the code that WRITES privileged rows
still refuses without the chain, and a fail-loud log is stronger than
a fail-closed process that operators will just restart.

Invariants checked:

  INV-CHAIN-175
    Trigger `trg_enforce_privileged_chain` exists on fleet_orders.
    Without it, privileged fleet orders can be INSERTed without an
    attestation bundle (chain-of-custody break).

  INV-CHAIN-176
    Trigger `trg_enforce_privileged_immutability` exists on
    fleet_orders. Without it, attestation_bundle_id / site_id /
    signed_payload can be UPDATEd post-insert (chain mutation).

  INV-EVIDENCE-DELETE
    Trigger `prevent_audit_deletion` exists on compliance_bundles
    (migration 151). Without it, evidence can be DELETEd.

  INV-AUDIT-DELETE
    Same trigger on admin_audit_log / client_audit_log. HIPAA
    §164.316(b)(2)(i) 7-year retention requires append-only.

  INV-COMPLETED-LOCK
    Trigger `prevent_completed_order_modification` exists on
    fleet_orders (migration 151).

  INV-SIGNING-KEY
    Signing key file exists, readable, non-empty.

  INV-MAGIC-LINK-TABLE
    privileged_access_magic_links table exists (migration 178).

Result surfaced via:
  - logger.error(...) per failure (log shipper alerts)
  - Prometheus gauge osiriscare_startup_invariant_ok{name=...} (1|0)
  - admin_audit_log row per failure (operator-visible)
  - Returns list of broken invariants so lifespan can print a
    summary banner at startup.
"""
from __future__ import annotations

import logging
import os
import pathlib
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)


SIGNING_KEY_PATH = os.getenv("SIGNING_KEY_FILE", "/app/secrets/signing.key")


@dataclass
class InvariantResult:
    name: str
    ok: bool
    detail: str = ""


async def _check_trigger_exists(
    conn, trigger_name: str, table_name: str
) -> bool:
    """Verify a pg_trigger row exists for (trigger_name, table_name)."""
    row = await conn.fetchrow(
        """
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        WHERE t.tgname = $1
          AND c.relname = $2
          AND NOT t.tgisinternal
        LIMIT 1
        """,
        trigger_name, table_name,
    )
    return row is not None


async def _check_table_exists(conn, table_name: str) -> bool:
    row = await conn.fetchrow(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_name = $1 LIMIT 1",
        table_name,
    )
    return row is not None


async def check_all_invariants(conn) -> List[InvariantResult]:
    """Run the full invariant suite. Returns one InvariantResult per
    check. Caller decides what to do with failures (log, alert, refuse
    to accept writes, etc.)."""
    results: List[InvariantResult] = []

    # ── Chain enforcement ─────────────────────────────────────────
    ok = await _check_trigger_exists(
        conn, "trg_enforce_privileged_chain", "fleet_orders"
    )
    results.append(InvariantResult(
        "INV-CHAIN-175", ok,
        "" if ok else (
            "Privileged INSERT without attestation_bundle_id is NOT "
            "rejected — reapply migration 175_privileged_chain_enforcement.sql"
        ),
    ))

    ok = await _check_trigger_exists(
        conn, "trg_enforce_privileged_immutability", "fleet_orders"
    )
    results.append(InvariantResult(
        "INV-CHAIN-176", ok,
        "" if ok else (
            "Privileged UPDATE of attestation_bundle_id / site_id / "
            "signed_payload is NOT blocked — reapply migration "
            "176_privileged_chain_update_guard.sql"
        ),
    ))

    # ── Evidence + audit immutability ────────────────────────────
    # Migration 151 uses a shared trigger function
    # `prevent_audit_deletion` across multiple tables. Check one
    # sentinel table per class.
    for table, inv_name in [
        ("compliance_bundles", "INV-EVIDENCE-DELETE"),
        ("admin_audit_log", "INV-AUDIT-DELETE-ADMIN"),
        ("client_audit_log", "INV-AUDIT-DELETE-CLIENT"),
        ("portal_access_log", "INV-AUDIT-DELETE-PORTAL"),
    ]:
        # We don't know the exact trigger name per table from this
        # context, so check that SOME BEFORE-DELETE trigger exists.
        # pg_trigger.tgtype is a bitfield (src/include/catalog/pg_trigger.h):
        #   TRIGGER_TYPE_ROW      1 << 0 = 0x01 (row-level vs statement)
        #   TRIGGER_TYPE_BEFORE   1 << 1 = 0x02 (SET means BEFORE; CLEAR = AFTER)
        #   TRIGGER_TYPE_INSERT   1 << 2 = 0x04
        #   TRIGGER_TYPE_DELETE   1 << 3 = 0x08
        #   TRIGGER_TYPE_UPDATE   1 << 4 = 0x10
        # So BEFORE DELETE trigger = (tgtype & 2) = 2 AND (tgtype & 8) = 8
        # We accept either row or statement granularity.
        row = await conn.fetchrow(
            """
            SELECT COUNT(*)::int AS n
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            WHERE c.relname = $1
              AND (t.tgtype & 2) = 2   -- BEFORE (bit 1 SET)
              AND (t.tgtype & 8) = 8   -- DELETE
              AND NOT t.tgisinternal
            """,
            table,
        )
        ok = bool(row and row["n"] > 0)
        results.append(InvariantResult(
            inv_name, ok,
            "" if ok else (
                f"No BEFORE DELETE trigger on {table} — retention "
                f"immutability compromised. Reapply migration 151."
            ),
        ))

    # ── Completed-order lock ─────────────────────────────────────
    ok = await _check_trigger_exists(
        conn, "trg_prevent_completed_order_modification", "fleet_orders"
    )
    # That's the expected name if 151 wires it with trg_ prefix;
    # some earlier migrations used a different convention. Fall back
    # to any BEFORE UPDATE trigger.
    if not ok:
        # BEFORE UPDATE = (tgtype & 2) = 2 AND (tgtype & 16) = 16
        row = await conn.fetchrow(
            """
            SELECT COUNT(*)::int AS n
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            WHERE c.relname = 'fleet_orders'
              AND (t.tgtype & 2) = 2   -- BEFORE
              AND (t.tgtype & 16) = 16 -- UPDATE
              AND NOT t.tgisinternal
              AND t.tgname LIKE '%complet%'
            """
        )
        ok = bool(row and row["n"] > 0)
    results.append(InvariantResult(
        "INV-COMPLETED-LOCK", ok,
        "" if ok else (
            "No BEFORE UPDATE trigger guarding status='completed' on "
            "fleet_orders — historical orders can be rewritten. "
            "Reapply migration 151."
        ),
    ))

    # ── Signing key presence ─────────────────────────────────────
    try:
        raw = pathlib.Path(SIGNING_KEY_PATH).read_bytes().strip()
        ok = len(raw) >= 16
        detail = "" if ok else f"signing key file exists but is <16 bytes"
    except Exception as e:
        ok = False
        detail = f"signing key unreadable at {SIGNING_KEY_PATH}: {e}"
    results.append(InvariantResult("INV-SIGNING-KEY", ok, detail))

    # ── Vault Transit key-version pinning (Vault Phase C P0 #1) ────
    # Defense against an attacker who compromises the Vault host and
    # rotates the Transit key to one they control. Reads the live key
    # version + pubkey from Vault, compares against the operator-
    # approved known_good=TRUE row in vault_signing_key_versions
    # (migration 311). On bootstrap (no known_good row yet) inserts
    # the observed version with known_good=FALSE and reports DETAIL
    # but counts as OK — operator must then approve via SQL.
    #
    # Only relevant when SIGNING_BACKEND env is 'vault' or 'shadow'.
    # When SIGNING_BACKEND is 'file' (pre-Phase-B) the invariant is
    # a no-op (returns OK with detail='vault backend not configured').
    signing_backend = os.getenv("SIGNING_BACKEND", "file").lower()
    if signing_backend in ("vault", "shadow"):
        try:
            from .signing_backend import get_signing_backend
        except ImportError:
            from signing_backend import get_signing_backend  # type: ignore
        try:
            backend = get_signing_backend()
            # Shadow backend wraps two backends; reach the Vault one.
            vault_backend = getattr(backend, "_shadow", None) or backend
            if hasattr(vault_backend, "key_version_and_pubkey"):
                key_version, pubkey_hex = vault_backend.key_version_and_pubkey()
            else:
                # Pre-instrumentation fallback: read pubkey only, derive
                # version from environment or skip.
                pubkey_hex = vault_backend.public_key().hex()
                key_version = int(os.getenv("VAULT_SIGNING_KEY_VERSION", "1"))
            key_name = os.getenv("VAULT_SIGNING_KEY_NAME", "osiriscare-signing")

            known_good = await conn.fetchrow(
                """
                SELECT key_version, pubkey_hex
                  FROM vault_signing_key_versions
                 WHERE key_name = $1 AND known_good = TRUE
                 ORDER BY approved_at DESC NULLS LAST LIMIT 1
                """,
                key_name,
            )

            if known_good is None:
                # Bootstrap: insert this observation with known_good=FALSE.
                # ON CONFLICT preserves first_observed_at while bumping
                # last_observed_at.
                await conn.execute(
                    """
                    INSERT INTO vault_signing_key_versions
                        (key_name, key_version, pubkey_hex, pubkey_b64)
                    VALUES ($1, $2, $3, encode(decode($3, 'hex'), 'base64'))
                    ON CONFLICT (key_name, key_version) DO UPDATE
                       SET last_observed_at = NOW()
                    """,
                    key_name, key_version, pubkey_hex,
                )
                ok = True
                detail = (
                    f"BOOTSTRAP: vault key v{key_version} observed; row "
                    f"inserted with known_good=FALSE. Operator must "
                    f"approve via: UPDATE vault_signing_key_versions "
                    f"SET known_good=TRUE, approved_by='<email>', "
                    f"approved_at=NOW() WHERE key_name='{key_name}' "
                    f"AND key_version={key_version};"
                )
            elif (
                known_good["key_version"] != key_version
                or known_good["pubkey_hex"] != pubkey_hex
            ):
                ok = False
                detail = (
                    f"DRIFT: vault returned key v{key_version} pubkey "
                    f"{pubkey_hex[:16]}... but known_good row is v"
                    f"{known_good['key_version']} pubkey "
                    f"{known_good['pubkey_hex'][:16]}...  Possible "
                    f"unauthorized rotation. Inspect Vault audit log; "
                    f"approve new version via SQL if legitimate."
                )
                # Insert the unauthorized version for forensic record.
                await conn.execute(
                    """
                    INSERT INTO vault_signing_key_versions
                        (key_name, key_version, pubkey_hex, pubkey_b64)
                    VALUES ($1, $2, $3, encode(decode($3, 'hex'), 'base64'))
                    ON CONFLICT (key_name, key_version) DO UPDATE
                       SET last_observed_at = NOW()
                    """,
                    key_name, key_version, pubkey_hex,
                )
            else:
                ok = True
                detail = ""
        except Exception as e:
            ok = False
            detail = (
                f"vault key-version probe failed: {e}. Check Vault host "
                f"reachability + AppRole credentials."
            )
    else:
        ok = True
        detail = "vault backend not configured (SIGNING_BACKEND=file)"
    results.append(InvariantResult("INV-SIGNING-BACKEND-VAULT", ok, detail))

    # ── Magic-link tracking table ────────────────────────────────
    ok = await _check_table_exists(conn, "privileged_access_magic_links")
    results.append(InvariantResult(
        "INV-MAGIC-LINK-TABLE", ok,
        "" if ok else (
            "privileged_access_magic_links table missing — magic-link "
            "approval flow will fail. Apply migration 178."
        ),
    ))

    # ── ENABLE ALWAYS on chain triggers (session_replication_role
    # bypass defense from migration 179) ─────────────────────────
    # pg_trigger.tgenabled: 'O' origin-only (default; skipped in
    # replica mode), 'A' always (fires regardless). We REQUIRE 'A'
    # on the chain-enforcement triggers so a superuser cannot
    # `SET session_replication_role='replica'` to bypass the chain.
    rows = await conn.fetch(
        """
        SELECT tgname, tgenabled
        FROM pg_trigger
        WHERE tgname IN (
            'trg_enforce_privileged_chain',
            'trg_enforce_privileged_immutability'
        ) AND NOT tgisinternal
        """
    )
    # pg_trigger.tgenabled is 'char' type; asyncpg returns it as bytes.
    # Normalize to str so comparisons work regardless.
    def _tgenabled_str(v):
        if isinstance(v, bytes):
            return v.decode()
        return v
    status = {r["tgname"]: _tgenabled_str(r["tgenabled"]) for r in rows}
    always_ok = (
        status.get("trg_enforce_privileged_chain") == "A"
        and status.get("trg_enforce_privileged_immutability") == "A"
    )
    results.append(InvariantResult(
        "INV-CHAIN-ALWAYS-ENABLED", always_ok,
        "" if always_ok else (
            "Chain triggers are not ENABLE ALWAYS — "
            f"got {status}. A superuser can bypass by setting "
            "session_replication_role='replica'. Apply migration 179."
        ),
    ))

    # ── TRUNCATE defenses on evidence + audit tables ─────────────
    # Migration 179 adds BEFORE TRUNCATE triggers so bulk wipe is
    # blocked even by a superuser. pg_trigger tgtype bit for TRUNCATE
    # is 1 << 5 = 32. BEFORE bit still 2.
    for table, inv in [
        ("compliance_bundles", "INV-TRUNCATE-EVIDENCE"),
        ("admin_audit_log", "INV-TRUNCATE-AUDIT-ADMIN"),
        ("client_audit_log", "INV-TRUNCATE-AUDIT-CLIENT"),
        ("portal_access_log", "INV-TRUNCATE-AUDIT-PORTAL"),
    ]:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*)::int AS n
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            WHERE c.relname = $1
              AND (t.tgtype & 2) = 2    -- BEFORE
              AND (t.tgtype & 32) = 32  -- TRUNCATE
              AND NOT t.tgisinternal
            """,
            table,
        )
        ok = bool(row and row["n"] > 0)
        results.append(InvariantResult(
            inv, ok,
            "" if ok else (
                f"No BEFORE TRUNCATE trigger on {table} — bulk wipe "
                f"bypasses DELETE protection. Apply migration 179."
            ),
        ))

    return results


async def enforce_startup_invariants(conn, metrics_registry=None) -> int:
    """Run the full check, log + audit each failure, update the
    Prometheus gauge. Returns the number of broken invariants (0 = green).

    `metrics_registry` is optional — when provided, must expose
    `.startup_invariant_ok.labels(name).set(value)`.
    """
    import json
    results = await check_all_invariants(conn)

    broken = [r for r in results if not r.ok]
    for r in results:
        if metrics_registry is not None:
            try:
                metrics_registry.startup_invariant_ok.labels(name=r.name).set(
                    1 if r.ok else 0
                )
            except Exception:
                pass

    if not broken:
        logger.info(
            "startup_invariants_ok",
            extra={"checked": len(results)},
        )
        return 0

    # Log ERROR per broken invariant
    for r in broken:
        logger.error(
            "STARTUP_INVARIANT_BROKEN",
            extra={
                "invariant": r.name,
                "detail": r.detail,
            },
        )

    # Persist to admin_audit_log in one batch so a single query covers
    # the whole failed suite (one row per invariant).
    try:
        async with conn.transaction():
            for r in broken:
                await conn.execute(
                    """
                    INSERT INTO admin_audit_log
                    (action, target, details, created_at)
                    VALUES (
                        'STARTUP_INVARIANT_BROKEN',
                        $1,
                        $2::jsonb,
                        NOW()
                    )
                    """,
                    f"invariant:{r.name}",
                    json.dumps({
                        "invariant": r.name,
                        "detail": r.detail,
                        "broken_at_startup": True,
                    }),
                )
    except Exception as e:
        logger.error(
            "startup_invariant_audit_write_failed",
            extra={"error": str(e)},
            exc_info=True,
        )

    # One-shot summary banner for humans at startup
    logger.error(
        "STARTUP_INVARIANTS_DEGRADED",
        extra={
            "broken_count": len(broken),
            "broken": [r.name for r in broken],
            "note": (
                "Server is running but the DB layer is not fully "
                "protecting privileged operations. Investigate before "
                "accepting writes."
            ),
        },
    )

    return len(broken)

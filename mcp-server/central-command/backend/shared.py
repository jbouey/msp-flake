"""
Shared state and dependencies for the MCP Server.

This module holds references to global state (signing keys, MinIO client,
runbooks, Redis, DB session) that are initialized in main.py's lifespan
and consumed by extracted route modules (agent_api, learning_api_main,
infra_api, background_tasks).

Design rationale: Using module-level references avoids circular imports
between main.py and the extracted modules. main.py calls the init_*
functions during lifespan startup; route modules import the accessor
functions.
"""

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional

import structlog
import yaml
from nacl.signing import SigningKey, VerifyKey
from nacl.encoding import HexEncoder
from minio import Minio
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from fastapi import HTTPException, Request

logger = structlog.get_logger()

# ============================================================================
# Configuration constants (re-exported for extracted modules)
# ============================================================================

RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "60"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "300"))
RATE_LIMIT_OVERRIDES = {
    "incidents": 200,
    "drift": 200,
    "evidence": 200,
    # Session 203 Batch 6 H2 — client portal magic link + login.
    # Magic link is per-IP at 5 per 5min (avoid SMTP exhaustion + email
    # enumeration). Login is per-IP at 10 per 5min (gives the user some
    # retries if they typo'd the password but still bounds attacker
    # volume; the per-account 5-attempt lockout in client_users.locked_until
    # provides the second layer).
    "client_magic_link": 5,
    "client_login": 10,
}

ORDER_TTL_SECONDS = int(os.getenv("ORDER_TTL_SECONDS", "900"))
WORM_RETENTION_DAYS = int(os.getenv("WORM_RETENTION_DAYS", "90"))

MINIO_BUCKET = os.getenv("MINIO_BUCKET", "evidence")

SIGNING_KEY_FILE = Path(os.getenv("SIGNING_KEY_FILE", "/app/secrets/signing.key"))
RUNBOOK_DIR = Path(os.getenv("RUNBOOK_DIR", "/app/runbooks"))

# ============================================================================
# Database (initialized by main.py, accessed by extracted modules)
# ============================================================================

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://mcp:mcp@localhost/mcp")
_db_sep = "&" if "?" in DATABASE_URL else "?"
_db_url = DATABASE_URL + _db_sep + "prepared_statement_cache_size=0"

engine = create_async_engine(
    _db_url,
    echo=False,
    pool_size=20,
    max_overflow=30,
    pool_recycle=3600,
    pool_pre_ping=True,
    connect_args={"statement_cache_size": 0},
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# -----------------------------------------------------------------------------
# RLS admin context for SQLAlchemy sessions (Migration 234 follow-up)
#
# Migration 234 flipped the mcp_app role default of `app.is_admin` from 'true'
# to 'false' so any path that forgets to set tenant scope sees zero rows. Every
# SQLAlchemy endpoint today uses `get_db()` as an ADMIN surface — partner
# dashboards, admin consoles, OAuth setup — none of them set tenant context.
# Without this hook the whole SQLAlchemy tree would go blind the moment the
# migration applies.
#
# The hook sets `app.is_admin = 'true'` at session-scope (not transaction-
# scope) so it persists across the autocommit + flush lifecycle SQLAlchemy
# uses internally. PgBouncer's `server_reset_query = DISCARD ALL` strips the
# setting before the backend connection is returned to another borrower, so
# the setting does not leak outside the session that set it.
#
# If a future endpoint needs genuine per-session tenant scope through
# SQLAlchemy it should use an explicit SET LOCAL inside an explicit
# transaction — this event listener only sets the default admin context.
# -----------------------------------------------------------------------------
try:
    from sqlalchemy import event as _sqla_event
except ImportError:
    # Unit tests stub `sys.modules["sqlalchemy"]` with a bare ModuleType
    # that lacks `event`. In those runs the engine above is also a stub
    # and would reject listener registration anyway — safely skip.
    _sqla_event = None  # type: ignore[assignment]

if _sqla_event is not None:
    try:
        @_sqla_event.listens_for(engine.sync_engine, "connect")
        def _set_sqla_admin_context(dbapi_connection, connection_record):
            """Run on EVERY new backend connection checked out into the pool.

            Uses the DB-API cursor directly because SQLAlchemy event listeners
            fire on the sync shim of the async engine. `SET` (no LOCAL) is
            session-level; PgBouncer DISCARD ALL clears it between borrows
            from the front-side pool.
            """
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("SET app.is_admin TO 'true'")
            finally:
                cursor.close()
    except Exception:
        # If engine is a stub (tests), `sync_engine` may not resolve — the
        # missing admin context is fine in unit tests that don't touch RLS.
        pass


async def get_db():
    """Yield a SQLAlchemy async session (FastAPI Depends).

    The session inherits the admin RLS context set by the engine `connect`
    event listener above — there is no per-request SET needed. Tenant-
    scoped code should not use this; it should use `tenant_connection()`
    or `org_connection()` in `tenant_middleware.py` to acquire an asyncpg
    connection with SET LOCAL tenant scope.
    """
    async with async_session() as session:
        yield session


async def execute_with_retry(db, query, params=None, max_retries=2):
    """Execute a SQLAlchemy query with retry on PgBouncer prepared statement errors.

    PgBouncer transaction pooling can cause DuplicatePreparedStatementError
    when recycling server connections. A single retry resolves this.
    """
    from sqlalchemy import text as sa_text
    for attempt in range(max_retries + 1):
        try:
            if isinstance(query, str):
                query = sa_text(query)
            return await db.execute(query, params or {})
        except Exception as e:
            error_str = str(e)
            if "DuplicatePreparedStatement" in error_str and attempt < max_retries:
                # PgBouncer recycled a connection — retry once
                await asyncio.sleep(0.1)
                continue
            raise


# ============================================================================
# Session Token Management (shared across admin, partner, client portals)
# ============================================================================

def generate_session_token() -> str:
    """Generate a cryptographically secure session token."""
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """Hash a session token for storage using HMAC-SHA256.

    Single source of truth for all portals — prevents divergent hashing.
    Requires SESSION_TOKEN_SECRET environment variable.
    """
    secret = os.getenv("SESSION_TOKEN_SECRET", "")
    if not secret:
        raise RuntimeError("SESSION_TOKEN_SECRET must be set for session security")
    return hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()


# ============================================================================
# Redis
# ============================================================================

redis_client: Optional[redis.Redis] = None


def set_redis_client(client: Optional[redis.Redis]):
    global redis_client
    redis_client = client


def get_redis_client() -> Optional[redis.Redis]:
    return redis_client


# ============================================================================
# Rate Limiting
# ============================================================================

async def check_rate_limit(
    site_id: str,
    action: str = "default",
    window_seconds: Optional[int] = None,
    max_requests: Optional[int] = None,
) -> tuple:
    """
    Check if request is rate limited.
    Returns (allowed, remaining_seconds).

    Optional overrides (used by sensitive endpoints like break-glass):
      window_seconds — custom TTL (default: RATE_LIMIT_WINDOW = 300s)
      max_requests   — custom cap (default: RATE_LIMIT_OVERRIDES[action] or RATE_LIMIT_REQUESTS)
    """
    if redis_client is None:
        return True, 0  # No Redis = no rate limiting (test/dev mode)

    window = window_seconds if window_seconds is not None else RATE_LIMIT_WINDOW
    cap = (
        max_requests
        if max_requests is not None
        else RATE_LIMIT_OVERRIDES.get(action, RATE_LIMIT_REQUESTS)
    )

    key = f"rate:{site_id}:{action}"

    count = await redis_client.incr(key)

    if count == 1:
        await redis_client.expire(key, window)
    elif count > cap:
        ttl = await redis_client.ttl(key)
        if ttl < 0:
            await redis_client.expire(key, window)
            ttl = window
        return False, max(0, ttl)

    return True, 0


# ============================================================================
# Ed25519 Signing
# ============================================================================

signing_key: Optional[SigningKey] = None
verify_key: Optional[VerifyKey] = None
previous_signing_key: Optional[SigningKey] = None
previous_verify_key: Optional[VerifyKey] = None


def load_or_create_signing_key():
    global signing_key, verify_key, previous_signing_key, previous_verify_key

    prev_path = SIGNING_KEY_FILE.with_suffix('.key.previous')
    if prev_path.exists():
        try:
            prev_hex = prev_path.read_text().strip()
            previous_signing_key = SigningKey(prev_hex, encoder=HexEncoder)
            previous_verify_key = previous_signing_key.verify_key
            logger.info("Loaded previous signing key for verification")
        except Exception as e:
            logger.warning(f"Failed to load previous signing key: {e}")

    if SIGNING_KEY_FILE.exists():
        key_hex = SIGNING_KEY_FILE.read_text().strip()
        signing_key = SigningKey(key_hex, encoder=HexEncoder)
        logger.info("Loaded existing signing key", path=str(SIGNING_KEY_FILE))
    else:
        signing_key = SigningKey.generate()
        SIGNING_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        SIGNING_KEY_FILE.write_text(signing_key.encode(encoder=HexEncoder).decode())
        SIGNING_KEY_FILE.chmod(0o600)
        logger.info("Generated new signing key", path=str(SIGNING_KEY_FILE))

    verify_key = signing_key.verify_key


def sign_data(data: str) -> str:
    """Sign data and return hex-encoded signature.

    Phase B: routes through signing_backend so shadow/vault mode is
    a one-env-var flip. Byte-identical output in file mode."""
    try:
        from .signing_backend import get_signing_backend
    except ImportError:
        from signing_backend import get_signing_backend
    result = get_signing_backend().sign(data.encode())
    return result.signature.hex()


def get_public_key_hex() -> str:
    """Get hex-encoded public key."""
    return verify_key.encode(encoder=HexEncoder).decode()


def get_all_public_keys_hex() -> list:
    """Return ALL public keys appliances should trust.

    Session 207 Phase C prep: in shadow mode (primary=file, shadow=vault)
    this returns BOTH the file key AND the Vault key, so every
    appliance picks up the Vault key on its next checkin BEFORE we
    flip primary=vault. When the flip happens the Vault key is
    already trusted fleet-wide — zero-downtime cutover.

    Source-of-truth precedence:
      1. signing_backend.get_signing_backend().public_keys_all() when
         available — this is the authoritative view (shadow backend
         returns union, Vault backend its own key, file backend
         current + previous).
      2. Fall back to the top-level verify_key + previous_verify_key
         vars in this module (pre-Phase-B behaviour).
    """
    try:
        try:
            from .signing_backend import get_signing_backend, SigningBackendError
        except ImportError:
            from signing_backend import get_signing_backend, SigningBackendError
        backend = get_signing_backend()
        if hasattr(backend, "public_keys_all"):
            raw = backend.public_keys_all()
            if raw:
                return [k.hex() for k in raw]
    except Exception as e:
        # Fall through to the pre-Phase-B path — signing_backend import
        # failures must not break checkins.
        logger.warning("get_all_public_keys_hex via backend failed, using local keys: %s", e)

    keys = [get_public_key_hex()]
    if previous_verify_key:
        keys.append(previous_verify_key.encode(encoder=HexEncoder).decode())
    return keys


# ============================================================================
# MinIO
# ============================================================================

minio_client: Optional[Minio] = None


def setup_minio():
    global minio_client

    MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
    MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"
    MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY")
    MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY")

    if not MINIO_ACCESS_KEY or not MINIO_SECRET_KEY:
        if os.getenv("ENVIRONMENT", "development") == "production":
            raise RuntimeError("MINIO_ACCESS_KEY and MINIO_SECRET_KEY must be set in production")
        MINIO_ACCESS_KEY = MINIO_ACCESS_KEY or "minio"
        MINIO_SECRET_KEY = MINIO_SECRET_KEY or "minio-password"

    minio_client = Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_SECURE,
    )

    if not minio_client.bucket_exists(MINIO_BUCKET):
        minio_client.make_bucket(MINIO_BUCKET)
        logger.info("Created MinIO bucket", bucket=MINIO_BUCKET)

    logger.info("MinIO client initialized", endpoint=MINIO_ENDPOINT, bucket=MINIO_BUCKET)


def get_minio_client() -> Optional[Minio]:
    return minio_client


# ============================================================================
# Runbook Management
# ============================================================================

RUNBOOKS: Dict[str, Dict] = {}
ALLOWED_RUNBOOKS: set = set()


def load_runbooks():
    global RUNBOOKS, ALLOWED_RUNBOOKS

    if not RUNBOOK_DIR.exists():
        logger.warning("Runbook directory not found", path=str(RUNBOOK_DIR))
        return

    for runbook_file in RUNBOOK_DIR.glob("*.yaml"):
        try:
            with open(runbook_file) as f:
                runbook = yaml.safe_load(f)
                if runbook and "id" in runbook:
                    RUNBOOKS[runbook["id"]] = runbook
                    ALLOWED_RUNBOOKS.add(runbook["id"])
                    logger.info("Loaded runbook", id=runbook["id"])
        except Exception as e:
            logger.error("Failed to load runbook", file=str(runbook_file), error=str(e))

    logger.info("Runbooks loaded", count=len(RUNBOOKS))


# ============================================================================
# Auth Dependencies
# ============================================================================

async def require_appliance_bearer_full(request: Request) -> tuple[str, Optional[str]]:
    """Same as require_appliance_bearer but returns both the site_id AND
    the bearer-bound appliance_id. The appliance_id MAY be None for
    legacy site-level keys — callers that need per-appliance binding
    MUST treat a None return as a failure.

    Session 207 Phase W gate: the watchdog_api surface uses this
    to bind request.appliance_id to the bearer's owning appliance, so
    a compromised main-daemon bearer cannot claim to be a non-existent
    watchdog and poison watchdog_events chains.
    """
    site_id = await require_appliance_bearer(request)
    # The flow above sets `_bearer_aid` on the request state when the
    # bearer binds a specific appliance_id. See the shim below.
    aid = getattr(request.state, "_bearer_aid", None)
    return site_id, aid


async def require_appliance_bearer(request: Request) -> str:
    """Validate appliance Bearer token from Authorization header.

    Auth lookup: api_keys table keyed by key_hash. The appliance_id column
    distinguishes per-appliance keys from legacy site-level keys.
    Returns the site_id associated with the key. Also stashes the
    bearer-bound appliance_id (nullable) in request.state._bearer_aid
    for callers that upgrade to `require_appliance_bearer_full`.

    On failure for a known appliance (identifiable by site_id + mac_address in
    the request body), tracks auth_failure_count so the dashboard can show
    "Auth Failed" instead of generic "Offline", and the daemon can self-rekey.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    api_key = auth_header[7:]
    if not api_key:
        raise HTTPException(status_code=401, detail="Empty API key")

    key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    from sqlalchemy import text
    async with async_session() as db:
        result = await db.execute(
            text("""
                SELECT ak.site_id, ak.appliance_id FROM api_keys ak
                WHERE ak.key_hash = :key_hash AND ak.active = true
                LIMIT 1
            """),
            {"key_hash": key_hash}
        )
        row = result.fetchone()

    if row:
        # Stash bearer-bound appliance_id so callers can upgrade to the
        # _full variant without re-running the lookup.
        try:
            request.state._bearer_aid = row.appliance_id
        except Exception:
            pass  # Non-fatal; _full callers will see None and 403.
        # Clear auth failure tracking on successful auth
        if row.appliance_id:
            try:
                from dashboard_api.fleet import get_pool
                from dashboard_api.tenant_middleware import admin_connection
                pool = await get_pool()
                async with admin_connection(pool) as conn:
                    await conn.execute("""
                        UPDATE site_appliances
                        SET auth_failure_count = 0,
                            auth_failure_since = NULL,
                            last_auth_failure = NULL
                        WHERE appliance_id = $1
                          AND auth_failure_count > 0
                    """, row.appliance_id)
            except Exception:
                pass  # Non-critical
        return row.site_id

    # Auth failed — try to identify the appliance from the request body
    # so we can track auth failures for dashboard visibility.
    try:
        body_bytes = await request.body()
        import json as _json
        body = _json.loads(body_bytes)
        site_id = body.get("site_id")
        mac = body.get("mac_address")
        if site_id and mac:
            from dashboard_api.provisioning import normalize_mac
            mac_norm = normalize_mac(mac)
            appliance_id = f"{site_id}-{mac_norm}"
            from dashboard_api.fleet import get_pool
            from dashboard_api.tenant_middleware import admin_connection
            pool = await get_pool()
            async with admin_connection(pool) as conn:
                await conn.execute("""
                    UPDATE site_appliances
                    SET auth_failure_count = COALESCE(auth_failure_count, 0) + 1,
                        last_auth_failure = NOW(),
                        auth_failure_since = COALESCE(auth_failure_since, NOW())
                    WHERE appliance_id = $1
                """, appliance_id)
            logger.warning(
                "Auth failed for known appliance",
                appliance_id=appliance_id,
                site_id=site_id,
            )
            raise HTTPException(
                status_code=401,
                detail={"error": "API key mismatch", "code": "AUTH_KEY_MISMATCH"}
            )
    except HTTPException:
        raise
    except Exception:
        pass  # Body parsing failed — fall through to generic 401

    raise HTTPException(status_code=401, detail="Invalid API key")

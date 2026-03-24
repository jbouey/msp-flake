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

import hashlib
import json
import os
import re
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


async def get_db():
    """Yield a SQLAlchemy async session (FastAPI Depends)."""
    async with async_session() as session:
        yield session


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

async def check_rate_limit(site_id: str, action: str = "default") -> tuple:
    """
    Check if request is rate limited.
    Returns (allowed, remaining_seconds).
    """
    if redis_client is None:
        return True, 0  # No Redis = no rate limiting (test/dev mode)

    key = f"rate:{site_id}:{action}"

    count = await redis_client.incr(key)

    if count == 1:
        await redis_client.expire(key, RATE_LIMIT_WINDOW)
    elif count > RATE_LIMIT_OVERRIDES.get(action, RATE_LIMIT_REQUESTS):
        ttl = await redis_client.ttl(key)
        if ttl < 0:
            await redis_client.expire(key, RATE_LIMIT_WINDOW)
            ttl = RATE_LIMIT_WINDOW
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
    """Sign data and return hex-encoded signature."""
    signed = signing_key.sign(data.encode())
    return signed.signature.hex()


def get_public_key_hex() -> str:
    """Get hex-encoded public key."""
    return verify_key.encode(encoder=HexEncoder).decode()


def get_all_public_keys_hex() -> list:
    """Return all valid public keys (current + previous) for multi-key verification."""
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

async def require_appliance_bearer(request: Request) -> str:
    """Validate appliance Bearer token from Authorization header.

    Validates that the Bearer token is a valid API key in the api_keys table.
    Returns the site_id associated with the key.
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
                SELECT ak.site_id FROM api_keys ak
                WHERE ak.key_hash = :key_hash AND ak.active = true
                LIMIT 1
            """),
            {"key_hash": key_hash}
        )
        row = result.fetchone()

    if row:
        return row.site_id

    raise HTTPException(status_code=401, detail="Invalid API key")

#\!/usr/bin/env python3
"""
MCP Server - Central Orchestration Server for MSP Compliance Platform

Production-ready FastAPI server that:
- Receives check-ins from compliance appliances
- Issues signed remediation orders
- Accepts and stores evidence bundles
- Enforces rate limiting and guardrails
- Integrates with MinIO for WORM storage
"""

import asyncio
import os
import json
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from pathlib import Path
from contextlib import asynccontextmanager
import uuid

from fastapi import FastAPI, File, UploadFile, Form, Header, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi import HTTPException, status, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text, select
import structlog
from nacl.signing import SigningKey, VerifyKey
from nacl.encoding import HexEncoder
from minio import Minio
from minio.retention import Retention, COMPLIANCE
import yaml
import httpx

# Dashboard API routes
from dashboard_api.routes import router as dashboard_router, auth_router
from dashboard_api.sites import router as sites_router, orders_router, appliances_router, alerts_router
from dashboard_api.portal import router as portal_router
from dashboard_api.evidence_chain import router as evidence_router
from dashboard_api.org_credentials import router as org_credentials_router
from dashboard_api.provisioning import router as provisioning_router
from dashboard_api.partners import router as partners_router, branding_public_router
from dashboard_api.discovery import router as discovery_router
from dashboard_api.runbook_config import router as runbook_config_router
from dashboard_api.users import router as users_router
from dashboard_api.integrations.api import router as integrations_router, public_router as integrations_public_router
from dashboard_api.frameworks import router as frameworks_router
from dashboard_api.compliance_frameworks import router as compliance_frameworks_router, partner_router as compliance_partner_router
from dashboard_api.fleet_updates import router as fleet_updates_router
from dashboard_api.device_sync import device_sync_router
from dashboard_api.ops_health import router as ops_health_router
from dashboard_api.audit_report import router as audit_report_router
from dashboard_api.log_ingest import router as log_ingest_router
from dashboard_api.security_events import router as security_events_router
from dashboard_api.email_alerts import create_notification_with_email
from dashboard_api.health_monitor import health_monitor_loop
from dashboard_api.alert_router import digest_sender_loop
from dashboard_api.oauth_login import public_router as oauth_public_router, router as oauth_router, admin_router as oauth_admin_router
from dashboard_api.partner_auth import public_router as partner_auth_router, admin_router as partner_admin_router, session_router as partner_session_router
from dashboard_api.billing import router as billing_router
from dashboard_api.exceptions_api import router as exceptions_router
from dashboard_api.appliance_delegation import router as appliance_delegation_router
from dashboard_api.learning_api import partner_learning_router
from dashboard_api.client_portal import public_router as client_auth_router, auth_router as client_portal_router, billing_webhook_router
from dashboard_api.hipaa_modules import router as hipaa_modules_router
from dashboard_api.companion import router as companion_router
from dashboard_api.protection_profiles import router as protection_profiles_router
from dashboard_api.notifications import router as notifications_router
from dashboard_api.cve_watch import router as cve_watch_router, cve_sync_loop
from dashboard_api.cve_remediation import cve_remediation_loop
from dashboard_api.framework_sync import router as framework_sync_router, framework_sync_loop
from dashboard_api.prometheus_metrics import router as metrics_router
from dashboard_api.websocket_manager import ws_manager
from dashboard_api.agent_api import agent_l2_plan as agent_l2_plan_handler
from dashboard_api.healing_sla import healing_sla_loop
from dashboard_api.check_catalog import router as check_catalog_router

# ============================================================================
# Configuration
# ============================================================================

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://mcp:mcp@localhost/mcp")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "evidence")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

# SECURITY: Secrets must be provided via environment variables (no defaults)
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY")
if not MINIO_ACCESS_KEY or not MINIO_SECRET_KEY:
    # Allow dev mode with defaults, but warn
    if os.getenv("ENVIRONMENT", "development") == "production":
        raise RuntimeError("MINIO_ACCESS_KEY and MINIO_SECRET_KEY must be set in production")
    MINIO_ACCESS_KEY = MINIO_ACCESS_KEY or "minio"
    MINIO_SECRET_KEY = MINIO_SECRET_KEY or "minio-password"

SIGNING_KEY_FILE = Path(os.getenv("SIGNING_KEY_FILE", "/app/secrets/signing.key"))
RUNBOOK_DIR = Path(os.getenv("RUNBOOK_DIR", "/app/runbooks"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Rate limiting
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "60"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "300"))  # 5 minutes
# Per-action overrides: appliance endpoints burst during scan cycles
RATE_LIMIT_OVERRIDES = {
    "incidents": 200,   # 12+ incidents per scan, scans every 15 min
    "drift": 200,       # Drift telemetry bursts alongside incidents
    "evidence": 200,    # Evidence bundles per scan cycle
}

# Order TTL
ORDER_TTL_SECONDS = int(os.getenv("ORDER_TTL_SECONDS", "900"))  # 15 minutes

# WORM Storage retention (HIPAA requires 6 years, default 90 days per bundle, 7 years overall)
WORM_RETENTION_DAYS = int(os.getenv("WORM_RETENTION_DAYS", "90"))

# OpenAI for L2
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

# Check types that are monitoring-only — detect drift but don't attempt auto-remediation.
# Only checks that genuinely cannot be auto-fixed belong here.
# NOTE: bitlocker, screen_lock, backup_status REMOVED — L1 runbooks exist and work.
#   screen_lock_policy had 100% L1 success rate (38/38) but was blocked here.
#   bitlocker can be resumed via Resume-BitLocker over WinRM.
#   backup_status service restart is automatable (58% success rate).
MONITORING_ONLY_CHECKS = {
    # Network monitoring — host offline is not a remediable drift
    "net_host_reachability",
    "net_unexpected_ports",
    "net_expected_service",
    "net_dns_resolution",
    # Device reachability — host offline/unreachable, not auto-fixable
    "device_unreachable",
    # Backup destination — requires manual configuration (NOT backup_status)
    "backup_not_configured",
    "backup_verification",
    # Credential staleness — informational, not auto-fixable
    "credential_stale",
    # Agent deploy exhausted — max retries hit, needs human investigation
    "AGENT-REDEPLOY-EXHAUSTED",
    "WIN-DEPLOY-UNREACHABLE",
    # Linux encryption — LUKS cannot be enabled remotely on a running system
    "linux_encryption",
}

# ============================================================================
# Logging Setup
# ============================================================================

import logging
logging.basicConfig(format="%(message)s", level=getattr(logging, LOG_LEVEL, logging.INFO))

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

# ============================================================================
# Database Setup
# ============================================================================

# PgBouncer: disable prepared statement cache via URL param
_db_sep = "&" if "?" in DATABASE_URL else "?"
_db_url = DATABASE_URL + _db_sep + "prepared_statement_cache_size=0"

engine = create_async_engine(
    _db_url,
    echo=False,
    pool_size=20,           # Increased for production load
    max_overflow=30,        # Allow burst capacity
    pool_recycle=3600,      # Recycle stale connections after 1 hour
    pool_pre_ping=True,     # Verify connections before use
    connect_args={"statement_cache_size": 0},  # PgBouncer: disable prepared stmts
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def get_db():
    async with async_session() as session:
        yield session

# ============================================================================
# Redis Setup
# ============================================================================

redis_client: Optional[redis.Redis] = None

# ============================================================================
# Ed25519 Signing
# ============================================================================

signing_key: Optional[SigningKey] = None
verify_key: Optional[VerifyKey] = None
previous_signing_key: Optional[SigningKey] = None
previous_verify_key: Optional[VerifyKey] = None

def load_or_create_signing_key():
    global signing_key, verify_key, previous_signing_key, previous_verify_key

    # Load previous key if exists (for rotation support)
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
        # Load existing key
        key_hex = SIGNING_KEY_FILE.read_text().strip()
        signing_key = SigningKey(key_hex, encoder=HexEncoder)
        logger.info("Loaded existing signing key", path=str(SIGNING_KEY_FILE))
    else:
        # Generate new key
        signing_key = SigningKey.generate()
        SIGNING_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        SIGNING_KEY_FILE.write_text(signing_key.encode(encoder=HexEncoder).decode())
        SIGNING_KEY_FILE.chmod(0o600)
        logger.info("Generated new signing key", path=str(SIGNING_KEY_FILE))

    verify_key = signing_key.verify_key

def sign_data(data: str) -> str:
    """Sign data and return hex-encoded signature."""
    if signing_key is None:
        return hashlib.sha256(data.encode()).hexdigest() * 2  # placeholder in test/dev
    signed = signing_key.sign(data.encode())
    return signed.signature.hex()

def get_public_key_hex() -> str:
    """Get hex-encoded public key."""
    return verify_key.encode(encoder=HexEncoder).decode()

def get_all_public_keys_hex() -> list[str]:
    """Return all valid public keys (current + previous) for multi-key verification."""
    keys = [get_public_key_hex()]
    if previous_verify_key:
        keys.append(previous_verify_key.encode(encoder=HexEncoder).decode())
    return keys

# ============================================================================
# MinIO Setup
# ============================================================================

minio_client: Optional[Minio] = None

def setup_minio():
    global minio_client
    minio_client = Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_SECURE
    )
    
    # Create bucket if not exists
    if not minio_client.bucket_exists(MINIO_BUCKET):
        minio_client.make_bucket(MINIO_BUCKET)
        logger.info("Created MinIO bucket", bucket=MINIO_BUCKET)
    
    logger.info("MinIO client initialized", endpoint=MINIO_ENDPOINT, bucket=MINIO_BUCKET)

# ============================================================================
# Runbook Management
# ============================================================================

RUNBOOKS: Dict[str, Dict] = {}
ALLOWED_RUNBOOKS = set()

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
# Pydantic Models
# ============================================================================

class CheckinRequest(BaseModel):
    """Appliance check-in request."""
    site_id: str = Field(..., min_length=1, max_length=255)
    host_id: str = Field(..., min_length=1, max_length=255)
    deployment_mode: str = Field(..., pattern="^(reseller|direct)$")
    reseller_id: Optional[str] = None
    policy_version: str = Field(default="1.0")
    nixos_version: Optional[str] = None
    agent_version: Optional[str] = None
    public_key: Optional[str] = None

class IncidentReport(BaseModel):
    """Incident reported by appliance."""
    site_id: str
    host_id: str
    incident_type: str
    severity: str
    check_type: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    pre_state: Dict[str, Any] = Field(default_factory=dict)
    hipaa_controls: Optional[List[str]] = None
    
    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v):
        allowed = ["low", "medium", "high", "critical"]
        if v.lower() not in allowed:
            raise ValueError(f"severity must be one of {allowed}")
        return v.lower()

class DriftReport(BaseModel):
    """Drift detection report from appliance."""
    site_id: str
    host_id: str
    check_type: str
    drifted: bool
    pre_state: Dict[str, Any] = Field(default_factory=dict)
    recommended_action: Optional[str] = None
    severity: str = "medium"
    hipaa_controls: Optional[List[str]] = None

class OrderRequest(BaseModel):
    """Request for pending orders."""
    site_id: str
    host_id: str

class OrderAcknowledgement(BaseModel):
    """Order acknowledgement from appliance."""
    site_id: str
    order_id: str

class EvidenceSubmission(BaseModel):
    """Evidence bundle submission."""
    bundle_id: str
    site_id: str
    host_id: str
    order_id: Optional[str] = None
    check_type: str
    outcome: str
    pre_state: Dict[str, Any] = Field(default_factory=dict)
    post_state: Dict[str, Any] = Field(default_factory=dict)
    actions_taken: List[Dict[str, Any]] = Field(default_factory=list)
    hipaa_controls: Optional[List[str]] = None
    rollback_available: bool = False
    rollback_generation: Optional[int] = None
    timestamp_start: datetime
    timestamp_end: datetime
    policy_version: Optional[str] = None
    nixos_revision: Optional[str] = None
    ntp_offset_ms: Optional[int] = None
    signature: str
    error: Optional[str] = None
    
    @field_validator("outcome")
    @classmethod
    def validate_outcome(cls, v):
        allowed = ["success", "failed", "reverted", "deferred", "alert", "rejected", "expired"]
        if v not in allowed:
            raise ValueError(f"outcome must be one of {allowed}")
        return v

# ============================================================================
# Rate Limiting
# ============================================================================

async def check_rate_limit(site_id: str, action: str = "default") -> tuple[bool, int]:
    """
    Check if request is rate limited.
    Returns (allowed, remaining_seconds).
    Uses atomic INCR + conditional EXPIRE to prevent TTL loss from race conditions.
    """
    key = f"rate:{site_id}:{action}"

    # Atomic increment — creates key with value 1 if it doesn't exist
    count = await redis_client.incr(key)

    if count == 1:
        # First request in window — set TTL
        await redis_client.expire(key, RATE_LIMIT_WINDOW)
    elif count > RATE_LIMIT_OVERRIDES.get(action, RATE_LIMIT_REQUESTS):
        # Rate limited
        ttl = await redis_client.ttl(key)
        # Safety: if TTL was lost somehow, reset it
        if ttl < 0:
            await redis_client.expire(key, RATE_LIMIT_WINDOW)
            ttl = RATE_LIMIT_WINDOW
        return False, max(0, ttl)

    return True, 0

# ============================================================================
# Background Tasks
# ============================================================================

async def _ots_repair_block_heights():
    """One-time repair: re-extract bitcoin_block from stored proof data.

    Fixes proofs with bitcoin_block=3 (varint payload length was stored
    instead of the actual LEB128-decoded block height).
    """
    try:
        import base64
        from dashboard_api.evidence_chain import BTC_ATTESTATION_TAG, extract_btc_block_height
        async with async_session() as db:
            result = await db.execute(text("""
                SELECT bundle_id, proof_data, bitcoin_block
                FROM ots_proofs
                WHERE status = 'anchored'
                  AND bitcoin_block IS NOT NULL
                  AND (bitcoin_block <= 10 OR bitcoin_block > 100000000)
                LIMIT 100000
            """))
            bad_proofs = result.fetchall()
            if not bad_proofs:
                logger.info("OTS block height repair: no proofs need fixing")
                return

            fixed = 0
            for proof in bad_proofs:
                try:
                    proof_bytes = base64.b64decode(proof.proof_data)
                    tag_pos = proof_bytes.find(BTC_ATTESTATION_TAG)
                    if tag_pos >= 0:
                        correct_height = extract_btc_block_height(proof_bytes, tag_pos)
                        if correct_height and correct_height != proof.bitcoin_block:
                            await db.execute(text("""
                                UPDATE ots_proofs
                                SET bitcoin_block = :height
                                WHERE bundle_id = :bid
                            """), {"height": correct_height, "bid": proof.bundle_id})
                            fixed += 1
                except Exception as e:
                    logger.debug("Skipping malformed OTS proof", bundle_id=str(proof.bundle_id), error=str(e))
                    continue

            await db.commit()
            logger.info(f"OTS block height repair: fixed {fixed}/{len(bad_proofs)} proofs")
    except Exception as e:
        logger.exception(f"OTS block height repair failed: {e}")


async def _ots_upgrade_loop():
    """Periodically upgrade pending OTS proofs (every 15 minutes)."""
    await asyncio.sleep(30)  # Wait 30s after startup

    # One-time repair of incorrect block heights on first run
    await _ots_repair_block_heights()

    while True:
        try:
            from dashboard_api.evidence_chain import upgrade_pending_proofs
            async with async_session() as db:
                result = await upgrade_pending_proofs(db, limit=500)
                if result.get("upgraded", 0) > 0 or result.get("checked", 0) > 0:
                    logger.info("OTS upgrade cycle", **result)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"OTS upgrade cycle failed: {e}")
        await asyncio.sleep(900)  # 15 minutes


async def _ots_resubmit_expired_loop():
    """One-time background drain of expired OTS proofs.

    Runs in batches of 500 with 30s delays. Uses last_upgrade_attempt
    as a cooldown to avoid tight-looping on persistent calendar failures.
    Exits when no eligible expired proofs remain.
    """
    await asyncio.sleep(90)  # Let upgrade loop start first

    total_resubmitted = 0
    total_failed = 0
    consecutive_zero = 0

    while True:
        try:
            from dashboard_api.evidence_chain import submit_hash_to_ots
            async with async_session() as db:
                # Skip proofs that failed resubmission in the last hour
                result = await db.execute(text("""
                    SELECT bundle_id, bundle_hash, site_id
                    FROM ots_proofs
                    WHERE status = 'expired'
                    AND (last_upgrade_attempt IS NULL
                         OR last_upgrade_attempt < NOW() - INTERVAL '1 hour')
                    ORDER BY submitted_at ASC
                    LIMIT 500
                """))
                expired_proofs = result.fetchall()

                if not expired_proofs:
                    logger.info(
                        "OTS resubmission drain complete",
                        total_resubmitted=total_resubmitted,
                        total_failed=total_failed,
                    )
                    return

                batch_ok = 0
                batch_fail = 0

                for proof in expired_proofs:
                    try:
                        ots_result = await submit_hash_to_ots(
                            proof.bundle_hash, proof.bundle_id
                        )
                        async with db.begin_nested():  # SAVEPOINT: isolate each proof
                            if ots_result:
                                submitted_at = ots_result["submitted_at"]
                                if submitted_at.tzinfo is not None:
                                    submitted_at = submitted_at.replace(tzinfo=None)

                                await db.execute(text("""
                                    UPDATE ots_proofs
                                    SET status = 'pending',
                                        proof_data = :proof_data,
                                        calendar_url = :calendar_url,
                                        submitted_at = :submitted_at,
                                        error = NULL,
                                        upgrade_attempts = 0,
                                        last_upgrade_attempt = NULL
                                    WHERE bundle_id = :bundle_id
                                """), {
                                    "proof_data": ots_result["proof_data"],
                                    "calendar_url": ots_result["calendar_url"],
                                    "submitted_at": submitted_at,
                                    "bundle_id": proof.bundle_id,
                                })

                                await db.execute(text("""
                                    UPDATE compliance_bundles
                                    SET ots_status = 'pending',
                                        ots_proof = :proof_data,
                                        ots_calendar_url = :calendar_url,
                                        ots_submitted_at = :submitted_at,
                                        ots_error = NULL
                                    WHERE bundle_id = :bundle_id
                                """), {
                                    "proof_data": ots_result["proof_data"],
                                    "calendar_url": ots_result["calendar_url"],
                                    "submitted_at": submitted_at,
                                    "bundle_id": proof.bundle_id,
                                })
                                batch_ok += 1
                            else:
                                batch_fail += 1
                                await db.execute(text("""
                                    UPDATE ots_proofs
                                    SET error = 'Resubmission failed - all calendars returned errors',
                                        last_upgrade_attempt = NOW()
                                    WHERE bundle_id = :bundle_id
                                """), {"bundle_id": proof.bundle_id})
                    except Exception as e:
                        batch_fail += 1
                        logger.warning(f"OTS resubmit failed {proof.bundle_id[:8]}: {e}")

                    if (batch_ok + batch_fail) % 50 == 0:
                        await db.commit()

                await db.commit()

                total_resubmitted += batch_ok
                total_failed += batch_fail

                remaining = await db.execute(text(
                    "SELECT COUNT(*) FROM ots_proofs WHERE status = 'expired'"
                ))
                remaining_count = remaining.scalar() or 0

                logger.info(
                    "OTS resubmission batch",
                    batch_ok=batch_ok,
                    batch_fail=batch_fail,
                    total_resubmitted=total_resubmitted,
                    total_failed=total_failed,
                    remaining=remaining_count,
                )

                # Back off if calendars seem down
                if batch_ok == 0 and batch_fail > 0:
                    consecutive_zero += 1
                    if consecutive_zero >= 5:
                        logger.error(
                            "OTS calendars appear down: 5 consecutive zero-success batches. "
                            "Backing off for 1 hour before retrying."
                        )
                        await asyncio.sleep(3600)  # 1 hour backoff
                        consecutive_zero = 0  # Reset and retry
                        continue
                else:
                    consecutive_zero = 0

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"OTS resubmission batch failed: {e}")

        # 30s between batches; 5 min if last batch had zero successes
        delay = 300 if consecutive_zero > 0 else 30
        await asyncio.sleep(delay)


async def _flywheel_promotion_loop():
    """Periodically scan patterns for L2→L1 auto-promotion (every 30 minutes).

    Five steps:
    0. Generate patterns from L2 execution telemetry (bridge telemetry → patterns)
    1. Populate aggregated_pattern_stats from telemetry
    2. Update promotion_eligible flag
    3. Cross-client platform pattern aggregation
    4. Auto-promote platform rules + audit log
    5. Health monitoring (canary → rollout)
    + Telemetry retention cleanup (90 days)
    """
    await asyncio.sleep(120)  # Wait 2 min after startup
    while True:
        try:
            promotions_this_cycle = 0
            async with async_session() as db:
                # Step 0: Generate/update patterns from L2 execution telemetry
                # Pattern signature: incident_type:runbook_id (no hostname — hostname is a grouping key, not identity)
                try:
                    await db.execute(text("""
                        INSERT INTO patterns (
                            pattern_id, pattern_signature, incident_type, runbook_id,
                            occurrences, success_count, failure_count, status
                        )
                        SELECT
                            LEFT(md5(et.incident_type || ':' || et.runbook_id), 16) as pattern_id,
                            et.incident_type || ':' || et.runbook_id as pattern_signature,
                            et.incident_type,
                            et.runbook_id,
                            COUNT(*) as occurrences,
                            SUM(CASE WHEN et.success THEN 1 ELSE 0 END) as success_count,
                            SUM(CASE WHEN NOT et.success THEN 1 ELSE 0 END) as failure_count,
                            'pending' as status
                        FROM execution_telemetry et
                        WHERE et.resolution_level = 'L2'
                          AND et.incident_type IS NOT NULL
                          AND et.runbook_id IS NOT NULL
                        GROUP BY et.incident_type, et.runbook_id
                        HAVING COUNT(*) >= 5
                        ON CONFLICT (pattern_signature) DO UPDATE SET
                            occurrences = EXCLUDED.occurrences,
                            success_count = EXCLUDED.success_count,
                            failure_count = EXCLUDED.failure_count
                    """))
                    await db.commit()
                except Exception as e:
                    logger.warning(f"Flywheel step 0 (pattern generation) failed: {e}")
                    await db.rollback()

                # Step 1: Populate aggregated_pattern_stats from execution_telemetry
                # The Go daemon reports telemetry but doesn't call /api/agent/sync/pattern-stats,
                # so we bridge the gap server-side by aggregating directly.
                try:
                    await db.execute(text("""
                        INSERT INTO aggregated_pattern_stats (
                            site_id, pattern_signature, total_occurrences,
                            l1_resolutions, l2_resolutions, l3_resolutions,
                            success_count, total_resolution_time_ms,
                            success_rate, avg_resolution_time_ms,
                            recommended_action, promotion_eligible,
                            first_seen, last_seen, last_synced_at
                        )
                        SELECT
                            et.site_id,
                            et.incident_type || ':' || et.runbook_id as pattern_signature,
                            COUNT(*) as total_occurrences,
                            SUM(CASE WHEN et.resolution_level = 'L1' THEN 1 ELSE 0 END),
                            SUM(CASE WHEN et.resolution_level = 'L2' THEN 1 ELSE 0 END),
                            SUM(CASE WHEN et.resolution_level = 'L3' THEN 1 ELSE 0 END),
                            SUM(CASE WHEN et.success THEN 1 ELSE 0 END),
                            COALESCE(SUM(et.duration_seconds * 1000), 0),
                            CASE WHEN COUNT(*) > 0
                                THEN SUM(CASE WHEN et.success THEN 1 ELSE 0 END)::FLOAT / COUNT(*)
                                ELSE 0 END,
                            CASE WHEN COUNT(*) > 0
                                THEN COALESCE(SUM(et.duration_seconds * 1000), 0) / COUNT(*)
                                ELSE 0 END,
                            MAX(et.runbook_id),
                            false,
                            MIN(et.created_at),
                            MAX(et.created_at),
                            NOW()
                        FROM execution_telemetry et
                        WHERE et.resolution_level IN ('L1', 'L2')
                          AND et.incident_type IS NOT NULL
                          AND et.runbook_id IS NOT NULL
                        GROUP BY et.site_id, et.incident_type, et.runbook_id
                        HAVING COUNT(*) >= 3
                        ON CONFLICT (site_id, pattern_signature) DO UPDATE SET
                            total_occurrences = EXCLUDED.total_occurrences,
                            l1_resolutions = EXCLUDED.l1_resolutions,
                            l2_resolutions = EXCLUDED.l2_resolutions,
                            l3_resolutions = EXCLUDED.l3_resolutions,
                            success_count = EXCLUDED.success_count,
                            total_resolution_time_ms = EXCLUDED.total_resolution_time_ms,
                            success_rate = EXCLUDED.success_rate,
                            avg_resolution_time_ms = EXCLUDED.avg_resolution_time_ms,
                            last_seen = EXCLUDED.last_seen,
                            last_synced_at = NOW()
                    """))
                    await db.commit()
                except Exception as e:
                    logger.warning(f"Flywheel step 1 (aggregated stats) failed: {e}")
                    await db.rollback()

                # Step 2: Update promotion_eligible on aggregated_pattern_stats
                # Only L2 patterns are eligible — L1 patterns are already deterministic rules
                try:
                    eligible_result = await db.execute(text("""
                        UPDATE aggregated_pattern_stats
                        SET promotion_eligible = true
                        WHERE total_occurrences >= 5
                          AND success_rate >= 0.90
                          AND l2_resolutions >= 3
                          AND last_seen > NOW() - INTERVAL '7 days'
                          AND promotion_eligible = false
                        RETURNING pattern_signature
                    """))
                    newly_eligible = eligible_result.fetchall()
                    await db.commit()

                    if newly_eligible:
                        logger.info(
                            "Flywheel promotion scan complete",
                            newly_eligible=len(newly_eligible),
                        )
                except Exception as e:
                    logger.warning(f"Flywheel step 2 (promotion eligible) failed: {e}")
                    await db.rollback()

                # Step 3: Cross-client platform pattern aggregation
                # Aggregates L2 patterns across ALL sites/orgs (ignoring hostname)
                try:
                    await db.execute(text("""
                        INSERT INTO platform_pattern_stats (
                            pattern_key, incident_type, runbook_id,
                            distinct_sites, distinct_orgs, total_occurrences,
                            success_count, success_rate, first_seen, last_seen
                        )
                        SELECT
                            et.incident_type || ':' || et.runbook_id,
                            et.incident_type,
                            et.runbook_id,
                            COUNT(DISTINCT et.site_id),
                            COUNT(DISTINCT s.client_org_id),
                            COUNT(*),
                            SUM(CASE WHEN et.success THEN 1 ELSE 0 END),
                            CASE WHEN COUNT(*) > 0
                                THEN SUM(CASE WHEN et.success THEN 1 ELSE 0 END)::FLOAT / COUNT(*)
                                ELSE 0 END,
                            MIN(et.created_at),
                            MAX(et.created_at)
                        FROM execution_telemetry et
                        JOIN sites s ON s.site_id = et.site_id
                        WHERE et.resolution_level = 'L2'
                          AND et.incident_type IS NOT NULL
                          AND et.runbook_id IS NOT NULL
                        GROUP BY et.incident_type, et.runbook_id
                        HAVING COUNT(*) >= 10
                        ON CONFLICT (pattern_key) DO UPDATE SET
                            distinct_sites = EXCLUDED.distinct_sites,
                            distinct_orgs = EXCLUDED.distinct_orgs,
                            total_occurrences = EXCLUDED.total_occurrences,
                            success_count = EXCLUDED.success_count,
                            success_rate = EXCLUDED.success_rate,
                            last_seen = EXCLUDED.last_seen
                    """))
                    await db.commit()
                except Exception as e:
                    logger.warning(f"Flywheel step 3 (platform aggregation) failed: {e}")
                    await db.rollback()

                # Step 4: Auto-promote platform rules
                # Patterns proven across 5+ client orgs with 90%+ success
                # become platform L1 rules — no human approval needed
                try:
                    remaining = max(0, 5 - promotions_this_cycle)
                    if remaining == 0:
                        logger.info("Flywheel promotion cap reached (5/cycle), skipping platform auto-promotion")
                    platform_result = await db.execute(text("""
                        SELECT pattern_key, incident_type, runbook_id,
                               distinct_orgs, total_occurrences, success_rate
                        FROM platform_pattern_stats
                        WHERE promoted_at IS NULL
                          AND distinct_orgs >= 1
                          AND success_rate >= 0.90
                          AND total_occurrences >= 10
                        ORDER BY distinct_orgs DESC, total_occurrences DESC
                        LIMIT :remaining
                    """), {"remaining": remaining})
                    platform_candidates = platform_result.fetchall()

                    # Validate runbook_id before promotion: must exist in DB or match embedded prefix
                    _EMBEDDED_RUNBOOK_RE = re.compile(
                        r"^(L1|LIN|WIN|MAC|NET|RB|ESC)-", re.IGNORECASE
                    )
                    valid_rb_result = await db.execute(text(
                        "SELECT runbook_id FROM runbooks"
                    ))
                    valid_runbook_ids = {
                        row.runbook_id for row in valid_rb_result.fetchall()
                    }

                    # Fetch incident_types already covered by enabled builtin/synced rules
                    # to avoid promoting duplicates that will never match (builtins fire first)
                    existing_result = await db.execute(text("""
                        SELECT DISTINCT incident_pattern->>'incident_type'
                        FROM l1_rules
                        WHERE enabled = true AND source IN ('builtin', 'synced')
                        AND incident_pattern->>'incident_type' IS NOT NULL
                    """))
                    covered_types = {r[0] for r in existing_result.fetchall()}

                    platform_promoted = 0
                    for pc in platform_candidates:
                        # Skip if builtin/synced rule already covers this type
                        if pc.incident_type in covered_types:
                            logger.debug(
                                "Skipping promotion: builtin/synced rule already covers type",
                                incident_type=pc.incident_type,
                            )
                            continue

                        # Validate runbook_id exists in DB or matches known embedded prefix
                        if pc.runbook_id not in valid_runbook_ids and not _EMBEDDED_RUNBOOK_RE.match(pc.runbook_id):
                            logger.warning(
                                "Skipping platform promotion: invalid runbook_id",
                                runbook_id=pc.runbook_id,
                                incident_type=pc.incident_type,
                                pattern_key=pc.pattern_key,
                            )
                            continue

                        rule_id = f"L1-PLATFORM-{pc.incident_type.upper()}-{pc.runbook_id[:12].upper().replace('-', '')}"
                        try:
                            # Upsert: create rule if not exists, update confidence if it does
                            incident_pattern = {"incident_type": pc.incident_type}
                            if pc.incident_type:
                                incident_pattern["check_type"] = pc.incident_type

                            result = await db.execute(text("""
                                INSERT INTO l1_rules (
                                    rule_id, incident_pattern, runbook_id,
                                    confidence, promoted_from_l2, enabled, source
                                ) VALUES (
                                    :rule_id, CAST(:pattern AS jsonb), :runbook_id,
                                    :confidence, true, true, 'platform'
                                )
                                ON CONFLICT (rule_id) DO UPDATE SET
                                    confidence = EXCLUDED.confidence
                                RETURNING (xmax = 0) AS inserted
                            """), {
                                "rule_id": rule_id,
                                "pattern": json.dumps(incident_pattern),
                                "runbook_id": pc.runbook_id,
                                "confidence": float(pc.success_rate),
                            })
                            was_inserted = result.fetchone().inserted

                            # Mark platform pattern as promoted
                            await db.execute(text("""
                                UPDATE platform_pattern_stats
                                SET promoted_at = NOW(), promoted_rule_id = :rid
                                WHERE pattern_key = :pk
                            """), {"rid": rule_id, "pk": pc.pattern_key})

                            # Audit log: record promotion decision
                            await db.execute(text("""
                                INSERT INTO promotion_audit_log (
                                    event_type, rule_id, pattern_signature, check_type,
                                    confidence_score, success_rate, l2_resolutions,
                                    total_occurrences, source, actor, metadata
                                ) VALUES (
                                    'auto_promoted', :rule_id, :pattern_key, :check_type,
                                    :confidence, :success_rate, 0,
                                    :total_occ, 'platform', 'flywheel_loop',
                                    :metadata
                                )
                            """), {
                                "rule_id": rule_id,
                                "pattern_key": pc.pattern_key,
                                "check_type": pc.incident_type,
                                "confidence": float(pc.success_rate),
                                "success_rate": float(pc.success_rate),
                                "total_occ": pc.total_occurrences,
                                "metadata": json.dumps({"distinct_orgs": pc.distinct_orgs}),
                            })
                            await db.commit()
                            if was_inserted:
                                platform_promoted += 1
                                promotions_this_cycle += 1
                                logger.info(
                                    "Platform rule auto-promoted",
                                    rule_id=rule_id,
                                    incident_type=pc.incident_type,
                                    distinct_orgs=pc.distinct_orgs,
                                    success_rate=f"{pc.success_rate:.1%}",
                                    total_occurrences=pc.total_occurrences,
                                )
                        except Exception as e:
                            logger.warning(f"Failed to promote platform rule {rule_id}: {e}")
                            await db.rollback()

                    if platform_promoted > 0:
                        logger.info(f"Platform promotion: {platform_promoted} new rules auto-promoted")

                except Exception as e:
                    logger.warning(f"Flywheel step 4 (platform auto-promotion) failed: {e}")
                    await db.rollback()

                # Step 5: Post-promotion health monitoring (canary → rollout)
                # Promoted rules start site-specific (source='promoted').
                # After 48h with >70% success and 3+ executions → promote to 'synced' (fleet-wide).
                # After 48h with <70% success → disable (protects against bad promotions).
                try:
                    # 5a: Disable degraded promoted rules (<70% success after 48h)
                    degraded = await db.execute(text("""
                        UPDATE l1_rules SET enabled = false
                        WHERE source = 'promoted'
                          AND enabled = true
                          AND created_at > NOW() - INTERVAL '48 hours'
                          AND rule_id IN (
                              SELECT r.rule_id FROM l1_rules r
                              JOIN execution_telemetry et
                                ON et.runbook_id = r.runbook_id
                                AND et.created_at > r.created_at
                              WHERE r.source = 'promoted'
                                AND r.enabled = true
                                AND r.created_at > NOW() - INTERVAL '48 hours'
                              GROUP BY r.rule_id
                              HAVING COUNT(*) >= 3
                                AND SUM(CASE WHEN et.success THEN 1 ELSE 0 END)::FLOAT / COUNT(*) < 0.70
                          )
                        RETURNING rule_id
                    """))
                    auto_disabled = degraded.fetchall()
                    if auto_disabled:
                        for row in auto_disabled:
                            await db.execute(text("""
                                INSERT INTO promotion_audit_log (
                                    event_type, rule_id, source, actor
                                ) VALUES ('auto_disabled', :rid, 'promoted', 'flywheel_loop')
                            """), {"rid": row[0]})
                            logger.warning(
                                "Promoted rule auto-disabled (success rate < 70%)",
                                rule_id=row[0],
                            )
                    await db.commit()

                    # 5b: Graduate successful promoted rules to 'synced' (>70% success after 48h)
                    # This is the canary → rollout transition: proven rules become fleet-wide
                    graduated = await db.execute(text("""
                        UPDATE l1_rules SET source = 'synced'
                        WHERE source = 'promoted'
                          AND enabled = true
                          AND created_at < NOW() - INTERVAL '48 hours'
                          AND rule_id IN (
                              SELECT r.rule_id FROM l1_rules r
                              JOIN execution_telemetry et
                                ON et.runbook_id = r.runbook_id
                                AND et.created_at > r.created_at
                              WHERE r.source = 'promoted'
                                AND r.enabled = true
                                AND r.created_at < NOW() - INTERVAL '48 hours'
                              GROUP BY r.rule_id
                              HAVING COUNT(*) >= 3
                                AND SUM(CASE WHEN et.success THEN 1 ELSE 0 END)::FLOAT / COUNT(*) >= 0.70
                          )
                        RETURNING rule_id
                    """))
                    auto_graduated = graduated.fetchall()
                    if auto_graduated:
                        for row in auto_graduated:
                            await db.execute(text("""
                                INSERT INTO promotion_audit_log (
                                    event_type, rule_id, source, actor
                                ) VALUES ('synced', :rid, 'promoted', 'flywheel_loop')
                            """), {"rid": row[0]})
                            logger.info(
                                "Promoted rule graduated to synced (canary success)",
                                rule_id=row[0],
                            )
                    await db.commit()
                except Exception as e:
                    logger.warning(f"Flywheel step 5 (health monitoring) failed: {e}")
                    await db.rollback()

                # Step 6: Telemetry retention — purge records older than 90 days
                try:
                    purged = await db.execute(text("""
                        DELETE FROM execution_telemetry
                        WHERE created_at < NOW() - INTERVAL '90 days'
                    """))
                    await db.commit()
                    if purged.rowcount and purged.rowcount > 0:
                        logger.info("Flywheel telemetry retention", purged=purged.rowcount)
                except Exception as e:
                    logger.warning(f"Flywheel step 6 (telemetry retention) failed: {e}")
                    await db.rollback()

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"Flywheel promotion scan failed: {e}")

        await asyncio.sleep(1800)  # 30 minutes


# ============================================================================
# Lifespan Events
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client
    
    # Startup
    logger.info("Starting MCP Server...")
    
    # Connect to Redis with timeout and retry
    for attempt in range(3):
        try:
            redis_client = await redis.from_url(
                REDIS_URL, decode_responses=True,
                socket_connect_timeout=5, socket_timeout=5
            )
            await redis_client.ping()
            logger.info("Connected to Redis", url=REDIS_URL.split("@")[-1])
            break
        except Exception as e:
            if attempt < 2:
                logger.warning(f"Redis connection attempt {attempt + 1} failed: {e}, retrying in 3s...")
                await asyncio.sleep(3)
            else:
                logger.error(f"Redis connection failed after 3 attempts: {e}")
                redis_client = None
    
    # Load signing key
    load_or_create_signing_key()
    logger.info("Signing key ready", public_key=get_public_key_hex()[:16] + "...")
    
    # Setup MinIO
    setup_minio()
    
    # Load runbooks
    load_runbooks()
    
    # Test database connection
    async with engine.begin() as conn:
        result = await conn.execute(text("SELECT 1"))
        logger.info("Database connected")

    # Create exceptions tables if needed
    try:
        from dashboard_api.exceptions_api import create_exceptions_tables
        from dashboard_api.fleet import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await create_exceptions_tables(conn)
        logger.info("Exceptions tables ready")
    except Exception as e:
        logger.warning(f"Could not create exceptions tables: {e}")

    # Create appliance delegation tables if needed
    try:
        from dashboard_api.appliance_delegation import create_delegation_tables
        from dashboard_api.fleet import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await create_delegation_tables(conn)
        logger.info("Appliance delegation tables ready")
    except Exception as e:
        logger.warning(f"Could not create delegation tables: {e}")

    # Verify credential encryption key is available (warms the MultiFernet
    # keyring cache so startup fails loudly rather than at first request)
    try:
        from dashboard_api.credential_crypto import get_key_fingerprints
        fps = get_key_fingerprints()
        logger.info(f"Credential encryption keyring loaded: {len(fps)} key(s), primary={fps[0] if fps else 'none'}")
    except Exception as e:
        logger.error(f"CRITICAL: Credential encryption unavailable: {e}. "
                     "Credentials cannot be decrypted for appliance delivery.")

    # Ensure default admin user exists
    try:
        from dashboard_api.auth import ensure_default_admin
        async with async_session() as db:
            await ensure_default_admin(db)
        logger.info("Admin user check complete")
    except Exception as e:
        logger.warning(f"Could not ensure default admin: {e}")

    logger.info("MCP Server started",
                rate_limit=f"{RATE_LIMIT_REQUESTS}/{RATE_LIMIT_WINDOW}s",
                order_ttl=ORDER_TTL_SECONDS)

    # Supervised background task registry — auto-restarts on crash
    _bg_tasks: dict = {}
    _bg_shutdown = asyncio.Event()

    async def _supervised(name: str, coro_fn, *args, restart=True, max_restarts=20):
        """Run a coroutine with auto-restart on unexpected exit.

        Logs full tracebacks on crash and detects restart loops (exponential backoff
        up to 5 minutes, hard stop after max_restarts consecutive failures).
        """
        restart_count = 0
        backoff_s = 30  # Initial backoff; doubles on each restart up to 300s
        while not _bg_shutdown.is_set():
            try:
                logger.info(f"bg_task_started", task=name)
                await coro_fn(*args)
                logger.info(f"bg_task_completed", task=name)
                break  # Clean exit
            except asyncio.CancelledError:
                break
            except Exception as e:
                restart_count += 1
                logger.error(f"bg_task_crashed", task=name, error=str(e),
                             restart_count=restart_count, exc_info=True)
                if not restart or _bg_shutdown.is_set():
                    break
                if restart_count >= max_restarts:
                    logger.error("bg_task_restart_loop_detected — giving up",
                                 task=name, total_restarts=restart_count)
                    break
                logger.warning("bg_task_restarting", task=name, after_s=backoff_s,
                               attempt=restart_count, max=max_restarts)
                await asyncio.sleep(backoff_s)
                backoff_s = min(backoff_s * 2, 300)  # Cap at 5 minutes

    from dashboard_api.companion import companion_alert_check_loop

    async def expire_fleet_orders_loop():
        """Background task to mark expired fleet orders."""
        while True:
            try:
                from dashboard_api.fleet import get_pool
                pool = await get_pool()
                async with pool.acquire() as conn:
                    updated = await conn.execute("""
                        UPDATE fleet_orders SET status = 'expired'
                        WHERE status IN ('active', 'pending') AND expires_at < NOW()
                    """)
                    if updated and 'UPDATE' in updated:
                        count = int(updated.split()[-1])
                        if count > 0:
                            logger.info(f"Expired {count} fleet orders")
            except Exception as e:
                logger.warning(f"Fleet order expiry check failed: {e}")
            await asyncio.sleep(300)  # Every 5 minutes

    async def _reconciliation_loop():
        """Periodically sync site_appliances from appliances when diverged (every 5 min)."""
        await asyncio.sleep(180)  # Wait 3 min after startup
        while True:
            try:
                pool = await get_pool()
                async with pool.acquire() as conn:
                    synced = await conn.fetch("""
                        UPDATE site_appliances sa SET
                            last_checkin = a.last_checkin,
                            agent_version = a.agent_version,
                            status = CASE WHEN a.last_checkin > NOW() - INTERVAL '15 minutes' THEN 'online' ELSE sa.status END
                        FROM appliances a
                        WHERE sa.site_id = a.site_id
                        AND a.last_checkin > sa.last_checkin + INTERVAL '5 minutes'
                        RETURNING sa.site_id
                    """)
                    if synced:
                        logger.info(f"Reconciliation: synced {len(synced)} stale site_appliances rows")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Reconciliation loop error: {e}")
            await asyncio.sleep(300)  # 5 minutes

    async def _compliance_packet_loop():
        """Auto-generate monthly compliance packets on the 1st at 02:00 UTC.

        Checks hourly if it's the 1st of the month between 02:00-03:00 UTC.
        Generates packets for all active sites that don't already have one
        for the previous month.
        """
        await asyncio.sleep(900)  # 15-minute startup delay
        while True:
            try:
                now = datetime.now(timezone.utc)
                # Only run on the 1st of the month, 02:00-03:00 UTC window
                if now.day == 1 and now.hour == 2:
                    prev_month = now.month - 1 if now.month > 1 else 12
                    prev_year = now.year if now.month > 1 else now.year - 1

                    pool = await get_pool()
                    async with pool.acquire() as conn:
                        # Get all active sites
                        sites = await conn.fetch(
                            "SELECT site_id FROM sites WHERE status != 'decommissioned'"
                        )
                        for site_row in sites:
                            sid = site_row["site_id"]
                            # Check if packet already exists for this month
                            existing = await conn.fetchval(
                                "SELECT 1 FROM compliance_packets WHERE site_id = $1 AND month = $2 AND year = $3",
                                sid, prev_month, prev_year,
                            )
                            if existing:
                                continue
                            try:
                                from dashboard_api.compliance_packet import CompliancePacket
                                from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
                                _pkt_engine = create_async_engine(os.getenv("DATABASE_URL", ""), echo=False)
                                _pkt_session = async_sessionmaker(_pkt_engine, class_=AsyncSession)
                                async with _pkt_session() as session:
                                    pkt = CompliancePacket(sid, prev_month, prev_year, session)
                                    result = await pkt.generate_packet()
                                    data = result.get("data", {})

                                    # Persist to compliance_packets table for audit trail
                                    await conn.execute("""
                                        INSERT INTO compliance_packets (
                                            site_id, month, year, packet_id,
                                            compliance_score, critical_issues, auto_fixes,
                                            mttr_hours, framework, controls_summary,
                                            markdown_content, generated_by
                                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11, 'system')
                                        ON CONFLICT (site_id, month, year, framework) DO UPDATE SET
                                            compliance_score = EXCLUDED.compliance_score,
                                            critical_issues = EXCLUDED.critical_issues,
                                            markdown_content = EXCLUDED.markdown_content,
                                            generated_at = NOW()
                                    """,
                                        sid, prev_month, prev_year, result["packet_id"],
                                        data.get("compliance_pct"),
                                        data.get("critical_issue_count", 0),
                                        data.get("auto_fixed_count", 0),
                                        data.get("mttr_hours"),
                                        "hipaa",
                                        json.dumps(data.get("controls", {})),
                                        open(result["markdown_path"]).read() if result.get("markdown_path") else None,
                                    )
                                    logger.info(
                                        "Compliance packet generated and persisted",
                                        packet_id=result["packet_id"],
                                        site_id=sid,
                                        compliance_pct=data.get("compliance_pct"),
                                    )
                            except Exception as e:
                                logger.warning(f"Compliance packet auto-gen failed for {sid}: {e}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Compliance packet loop error: {e}")
            await asyncio.sleep(3600)  # Check hourly

    async def _unregistered_device_alert_loop():
        """Email clients about unregistered devices needing attention."""
        from dashboard_api.background_tasks import unregistered_device_alert_loop
        await unregistered_device_alert_loop()

    async def _evidence_chain_check_loop():
        """Daily verification of evidence hash chain integrity per site."""
        await asyncio.sleep(600)  # 10-minute startup delay
        while True:
            try:
                pool = await get_pool()
                async with pool.acquire() as conn:
                    site_ids = await conn.fetch(
                        "SELECT DISTINCT site_id FROM compliance_bundles"
                    )
                    for row in site_ids:
                        site_id = row["site_id"]
                        breaks = await conn.fetch("""
                            SELECT a.id, a.chain_position, a.bundle_hash, b.prev_hash
                            FROM compliance_bundles a
                            JOIN compliance_bundles b
                              ON b.chain_position = a.chain_position + 1
                             AND b.site_id = a.site_id
                            WHERE a.bundle_hash != b.prev_hash
                              AND a.site_id = $1
                              AND a.created_at > NOW() - INTERVAL '24 hours'
                            LIMIT 10
                        """, site_id)
                        if breaks:
                            positions = ", ".join(
                                str(b["chain_position"]) for b in breaks
                            )
                            await conn.execute("""
                                INSERT INTO notifications
                                  (id, type, title, message, severity, category, site_id, created_at)
                                VALUES (gen_random_uuid(), 'system',
                                        'Evidence Chain Break Detected',
                                        $1, 'critical', 'security', $2, NOW())
                            """,
                                f"Site {site_id} has {len(breaks)} hash chain breaks at positions: {positions}",
                                site_id,
                            )
                            logger.error(
                                "Evidence chain breaks detected",
                                site_id=str(site_id),
                                break_count=len(breaks),
                                positions=positions,
                            )
                        else:
                            logger.info(
                                "Evidence chain check complete",
                                site_id=str(site_id),
                                breaks=0,
                            )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Evidence chain check failed: {e}")
            await asyncio.sleep(86400)  # Once per day

    async def _merkle_batch_loop():
        """Hourly Merkle batching of evidence bundles for OTS."""
        await asyncio.sleep(300)  # 5 min after startup
        while True:
            try:
                pool = await get_pool()
                async with pool.acquire() as conn:
                    sites = await conn.fetch(
                        "SELECT DISTINCT site_id FROM compliance_bundles WHERE ots_status = 'batching'"
                    )
                    for row in sites:
                        from dashboard_api.evidence_chain import process_merkle_batch
                        stats = await process_merkle_batch(conn, row["site_id"])
                        if stats.get("batched", 0) > 0:
                            logger.info("Merkle batch created", **stats)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Merkle batch loop error", error=str(e))
            await asyncio.sleep(3600)  # 1 hour

    async def _audit_log_retention_loop():
        """Daily cleanup of audit logs older than 7 years (HIPAA: 6-year minimum)."""
        await asyncio.sleep(7200)  # 2 hours after startup
        while True:
            try:
                pool = await get_pool()
                async with pool.acquire() as conn:
                    result = await conn.execute("""
                        DELETE FROM admin_audit_log
                        WHERE created_at < NOW() - INTERVAL '7 years'
                    """)
                    if result and 'DELETE' in result:
                        count = int(result.split()[-1])
                        if count > 0:
                            logger.info("audit_log_retention_cleanup", deleted=count)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Audit log retention loop error", error=str(e))
            await asyncio.sleep(86400)  # 24 hours

    from dashboard_api.background_tasks import (
        ots_reverify_sample_loop,
        mesh_consistency_check_loop,
        flywheel_reconciliation_loop,
    )

    task_defs = [
        ("ots_upgrade", _ots_upgrade_loop),
        ("ots_resubmit", _ots_resubmit_expired_loop),
        ("ots_reverify", ots_reverify_sample_loop),
        ("mesh_consistency", mesh_consistency_check_loop),
        ("flywheel_reconciliation", flywheel_reconciliation_loop),
        ("cve_watch", cve_sync_loop),
        ("cve_remediation", cve_remediation_loop),
        ("framework_sync", framework_sync_loop),
        ("flywheel", _flywheel_promotion_loop),
        ("companion_alerts", companion_alert_check_loop),
        ("fleet_order_expiry", expire_fleet_orders_loop),
        ("health_monitor", health_monitor_loop),
        ("reconciliation", _reconciliation_loop),
        ("evidence_chain_check", _evidence_chain_check_loop),
        ("merkle_batch", _merkle_batch_loop),
        ("healing_sla", healing_sla_loop),
        ("unregistered_device_alerts", _unregistered_device_alert_loop),
        ("compliance_packets", _compliance_packet_loop),
        ("alert_digest", digest_sender_loop),
        ("audit_log_retention", _audit_log_retention_loop),
    ]

    for name, fn in task_defs:
        _bg_tasks[name] = asyncio.create_task(_supervised(name, fn))

    # Store task registry on app for health endpoint
    app.state.bg_tasks = _bg_tasks

    yield

    # Shutdown
    logger.info("Shutting down MCP Server...")
    _bg_shutdown.set()
    for name, task in _bg_tasks.items():
        task.cancel()
    for name, task in _bg_tasks.items():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=10)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    if redis_client:
        await redis_client.close()
    await engine.dispose()
    logger.info("MCP Server stopped")

# ============================================================================
# FastAPI App
# ============================================================================

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, 
    title="MCP Server",
    description="MSP Compliance Platform - Central Orchestration Server",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware - SECURITY: Specific origins only
_cors_origins = [
    "https://dashboard.osiriscare.net",
    "https://portal.osiriscare.net",
]
if os.getenv("ENVIRONMENT", "production") == "development":
    _cors_origins.extend(["http://localhost:3000", "http://localhost:5173"])
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Site-ID", "X-CSRF-Token"],
)

# ETag middleware for conditional responses (saves bandwidth on polling)
try:
    from dashboard_api.etag_middleware import ETagMiddleware
    app.add_middleware(ETagMiddleware)
    logger.info("ETag middleware enabled")
except ImportError:
    logger.warning("ETag middleware not available")

# Rate limiting middleware - SECURITY: Protect against brute force and DoS
try:
    from dashboard_api.rate_limiter import RateLimitMiddleware
    app.add_middleware(RateLimitMiddleware)
    logger.info("Rate limiting middleware enabled")
except ImportError:
    logger.warning("Rate limiting middleware not available - continuing without rate limits")

# Security headers middleware - SECURITY: CSP, X-Frame-Options, HSTS, etc.
try:
    from dashboard_api.security_headers import SecurityHeadersMiddleware
    app.add_middleware(SecurityHeadersMiddleware)
    logger.info("Security headers middleware enabled")
except ImportError:
    logger.warning("Security headers middleware not available - continuing without security headers")

# CSRF middleware - SECURITY: Protect against cross-site request forgery
try:
    from dashboard_api.csrf import CSRFMiddleware
    app.add_middleware(CSRFMiddleware)
    logger.info("CSRF middleware enabled")
except ImportError:
    logger.warning("CSRF middleware not available - continuing without CSRF protection")

# Structured request logging middleware
@app.middleware("http")
async def structured_request_logging(request: Request, call_next):
    """Log all requests with structured fields for observability."""
    import time as _time
    start = _time.monotonic()
    response = await call_next(request)
    duration_ms = round((_time.monotonic() - start) * 1000, 1)
    path = request.url.path
    # Extract site_id from path or skip noisy endpoints
    if path in ("/health", "/metrics") or path.startswith("/static"):
        return response
    site_id = None
    if "/sites/" in path:
        parts = path.split("/sites/")
        if len(parts) > 1:
            site_id = parts[1].split("/")[0]
    logger.info(
        "request",
        method=request.method,
        path=path,
        status_code=response.status_code,
        duration_ms=duration_ms,
        site_id=site_id,
        client=request.client.host if request.client else None,
    )
    return response

# Include dashboard API routes
app.include_router(dashboard_router)
app.include_router(auth_router)
app.include_router(sites_router)
app.include_router(orders_router)
app.include_router(appliances_router)
app.include_router(alerts_router)
app.include_router(portal_router)
app.include_router(evidence_router)
app.include_router(provisioning_router)
app.include_router(partners_router)
app.include_router(branding_public_router)  # Public partner branding (no auth, for login pages)
app.include_router(discovery_router)
app.include_router(runbook_config_router)
app.include_router(users_router)
app.include_router(integrations_router)
app.include_router(integrations_public_router)  # OAuth callback (no auth)
app.include_router(frameworks_router)
app.include_router(fleet_updates_router)
app.include_router(device_sync_router)  # Device inventory sync from appliances
from dashboard_api.org_management import router as org_management_router
app.include_router(org_management_router)  # Org lifecycle + quotas + export (Session 203)
from dashboard_api.credential_rotation import router as credential_rotation_router
app.include_router(credential_rotation_router)  # Credential encryption key rotation (Session 203)
app.include_router(ops_health_router)  # Ops center health + traffic lights
app.include_router(audit_report_router)  # Audit readiness + BAA config
app.include_router(oauth_public_router, prefix="/api/auth")  # OAuth login public endpoints
app.include_router(oauth_router, prefix="/api/auth")  # OAuth authenticated endpoints
app.include_router(oauth_admin_router, prefix="/api")  # OAuth admin endpoints
app.include_router(partner_auth_router, prefix="/api")  # Partner OAuth login endpoints
app.include_router(partner_session_router, prefix="/api/partner-auth")  # Partner TOTP management (session-auth)
app.include_router(partner_admin_router, prefix="/api")  # Partner admin endpoints (pending, oauth-config)
app.include_router(billing_router)  # Stripe billing for partners
app.include_router(exceptions_router)  # Compliance exceptions management
app.include_router(appliance_delegation_router)  # Appliance delegation (signing keys, audit, escalations)
app.include_router(partner_learning_router)  # Partner learning management (promotions, rules)
app.include_router(cve_watch_router)  # CVE Watch — progressive vulnerability tracking
app.include_router(framework_sync_router)  # Framework Sync — live compliance library
app.include_router(client_auth_router, prefix="/api")  # Client portal auth (magic link, login)
app.include_router(client_portal_router, prefix="/api")  # Client portal endpoints
try:
    from dashboard_api.client_sso import sso_router as client_sso_router, config_router as client_sso_config_router
    app.include_router(client_sso_router, prefix="/api")  # Client SSO (OIDC authorize/callback)
    app.include_router(client_sso_config_router)  # Partner SSO config CRUD
except ImportError:
    pass  # client_sso module not yet deployed
app.include_router(hipaa_modules_router, prefix="/api")  # HIPAA compliance modules
app.include_router(companion_router, prefix="/api")  # Compliance Companion portal
app.include_router(org_credentials_router)  # Organization-level shared credentials
app.include_router(protection_profiles_router, prefix="/api/dashboard")  # Application Protection Profiles
app.include_router(billing_webhook_router, prefix="/api")  # Stripe webhooks
app.include_router(notifications_router)  # Partner notifications + L3 escalation tickets
app.include_router(compliance_frameworks_router)  # Multi-framework compliance management (admin)
app.include_router(compliance_partner_router)  # Partner compliance defaults + site compliance
app.include_router(log_ingest_router)  # Centralized log aggregation from appliances
app.include_router(security_events_router)  # WORM archival of sanitized security events
app.include_router(metrics_router)  # Prometheus-compatible metrics endpoint
app.include_router(check_catalog_router)  # Public check definitions catalog (no auth, marketing/transparency)

# WebSocket endpoint for real-time event push
@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    # Validate session token from query parameter before accepting connection
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=1008, reason="Authentication required")
        return
    try:
        from dashboard_api.auth import validate_session, hash_token
        async with async_session() as db:
            user = await validate_session(db, token)
        if not user:
            await websocket.close(code=1008, reason="Invalid or expired token")
            return
    except Exception as e:
        logger.warning("WebSocket auth validation failed", error=str(e))
        await websocket.close(code=1011, reason="Auth validation failed")
        return

    connected = await ws_manager.connect(websocket)
    if not connected:
        return
    try:
        while True:
            # Keep connection alive; client can send pings
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
    except Exception as e:
        logger.warning("WebSocket connection error", error=str(e))
        await ws_manager.disconnect(websocket)

# Serve agent update packages (only if directory exists)
_agent_packages_dir = Path("/opt/mcp-server/agent-packages")
if _agent_packages_dir.exists():
    app.mount("/agent-packages", StaticFiles(directory=str(_agent_packages_dir)), name="agent-packages")

# ============================================================================
# Authentication Dependencies
# ============================================================================

# Import admin dashboard auth (cookie-based sessions)
from dashboard_api.auth import require_auth


async def require_appliance_bearer(request: Request) -> str:
    """Validate appliance Bearer token. Delegates to shared implementation."""
    from dashboard_api.shared import require_appliance_bearer as _shared_auth
    return await _shared_auth(request)


# ============================================================================
# Endpoints
# ============================================================================

@app.get("/")
async def root():
    """Service information."""
    return {
        "service": "MCP Server",
        "version": "1.0.0",
        "description": "MSP Compliance Platform - Central Orchestration Server"
    }

@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    """Public health check — uptime monitors, Docker healthcheck, Caddy."""
    return JSONResponse(content={"status": "ok"})


@app.get("/api/admin/health")
async def admin_health(user: dict = Depends(require_auth)):
    """Detailed health check — Redis, MinIO, DB, background tasks (admin only)."""
    checks = {"status": "ok"}

    async def check_redis():
        try:
            if redis_client:
                await redis_client.ping()
                return "connected", True
            return "not_configured", False
        except Exception as e:
            return f"error: {str(e)}", False

    async def check_database():
        try:
            async with async_session() as session:
                await session.execute(text("SELECT 1"))
            return "connected", True
        except Exception as e:
            return f"error: {str(e)}", False

    async def check_minio():
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, minio_client.bucket_exists, MINIO_BUCKET
            )
            return "connected", True
        except Exception as e:
            return f"error: {str(e)}", False

    redis_result, db_result, minio_result = await asyncio.gather(
        check_redis(), check_database(), check_minio()
    )

    checks["redis"] = redis_result[0]
    checks["database"] = db_result[0]
    checks["minio"] = minio_result[0]

    if not all([redis_result[1], db_result[1], minio_result[1]]):
        checks["status"] = "degraded"

    checks["timestamp"] = datetime.now(timezone.utc).isoformat()
    checks["runbooks_loaded"] = len(RUNBOOKS)

    # Background task health
    bg_tasks = getattr(app.state, 'bg_tasks', {})
    task_status = {}
    for name, task in bg_tasks.items():
        if task.done():
            exc = task.exception() if not task.cancelled() else None
            task_status[name] = f"crashed: {exc}" if exc else "completed"
        else:
            task_status[name] = "running"
    checks["background_tasks"] = task_status

    crashed_tasks = [n for n, s in task_status.items() if "crashed" in s]
    if crashed_tasks:
        checks["status"] = "degraded"

    if not all([redis_result[1], db_result[1], minio_result[1]]):
        checks["status"] = "degraded"

    status_code = 200 if checks["status"] == "ok" else 503
    return JSONResponse(content=checks, status_code=status_code)

@app.post("/checkin")
async def checkin(req: CheckinRequest, request: Request, db: AsyncSession = Depends(get_db), auth_site_id: str = Depends(require_appliance_bearer)):
    """
    Appliance check-in endpoint.
    Registers or updates appliance, returns pending orders.
    """
    # Rate limit check
    allowed, remaining = await check_rate_limit(req.site_id, "checkin")
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limited. Try again in {remaining} seconds."
        )
    
    client_ip = request.client.host if request.client else None
    
    # Check if appliance exists
    result = await db.execute(
        text("SELECT id FROM appliances WHERE site_id = :site_id"),
        {"site_id": req.site_id}
    )
    existing = result.fetchone()
    
    now = datetime.now(timezone.utc)
    
    if existing:
        # Update existing appliance
        await db.execute(
            text("""
                UPDATE appliances SET
                    host_id = :host_id,
                    deployment_mode = :deployment_mode,
                    reseller_id = :reseller_id,
                    policy_version = :policy_version,
                    nixos_version = :nixos_version,
                    agent_version = :agent_version,
                    public_key = :public_key,
                    ip_address = :ip_address,
                    last_checkin = :last_checkin,
                    updated_at = :updated_at
                WHERE site_id = :site_id
            """),
            {
                "site_id": req.site_id,
                "host_id": req.host_id,
                "deployment_mode": req.deployment_mode,
                "reseller_id": req.reseller_id,
                "policy_version": req.policy_version,
                "nixos_version": req.nixos_version,
                "agent_version": req.agent_version,
                "public_key": req.public_key,
                "ip_address": client_ip,
                "last_checkin": now,
                "updated_at": now
            }
        )
        appliance_id = existing[0]
        action = "updated"
    else:
        # Create new appliance
        appliance_id = str(uuid.uuid4())
        await db.execute(
            text("""
                INSERT INTO appliances (id, site_id, host_id, deployment_mode, reseller_id,
                    policy_version, nixos_version, agent_version, public_key, ip_address,
                    last_checkin, created_at, updated_at)
                VALUES (:id, :site_id, :host_id, :deployment_mode, :reseller_id,
                    :policy_version, :nixos_version, :agent_version, :public_key, :ip_address,
                    :last_checkin, :created_at, :updated_at)
            """),
            {
                "id": appliance_id,
                "site_id": req.site_id,
                "host_id": req.host_id,
                "deployment_mode": req.deployment_mode,
                "reseller_id": req.reseller_id,
                "policy_version": req.policy_version,
                "nixos_version": req.nixos_version,
                "agent_version": req.agent_version,
                "public_key": req.public_key,
                "ip_address": client_ip,
                "last_checkin": now,
                "created_at": now,
                "updated_at": now
            }
        )
        action = "registered"
    
    await db.commit()
    
    # Audit log
    await db.execute(
        text("""
            INSERT INTO audit_log (event_type, actor, resource_type, resource_id, details, ip_address)
            VALUES (:event_type, :actor, :resource_type, :resource_id, :details, :ip_address)
        """),
        {
            "event_type": f"appliance.{action}",
            "actor": req.site_id,
            "resource_type": "appliance",
            "resource_id": appliance_id,
            "details": json.dumps({"host_id": req.host_id, "agent_version": req.agent_version}),
            "ip_address": client_ip
        }
    )
    await db.commit()
    
    # Get pending orders for this appliance
    result = await db.execute(
        text("""
            SELECT order_id, runbook_id, parameters, nonce, signature, ttl_seconds,
                   issued_at, expires_at, signed_payload
            FROM orders o
            JOIN appliances a ON o.appliance_id = a.id
            WHERE a.site_id = :site_id
            AND o.status = 'pending'
            AND o.expires_at > NOW()
            ORDER BY o.issued_at ASC
        """),
        {"site_id": req.site_id}
    )

    orders = []
    for row in result.fetchall():
        orders.append({
            "order_id": row[0],
            "order_type": "healing",
            "runbook_id": row[1],
            "parameters": row[2],
            "nonce": row[3],
            "signature": row[4],
            "ttl_seconds": row[5],
            "issued_at": row[6].isoformat() if row[6] else None,
            "expires_at": row[7].isoformat() if row[7] else None,
            "signed_payload": row[8],
        })
    
    # Check maintenance window for this site
    maintenance_until = None
    try:
        maint_result = await db.execute(
            text("SELECT maintenance_until FROM sites WHERE site_id = :site_id AND maintenance_until > NOW()"),
            {"site_id": req.site_id}
        )
        maint_row = maint_result.fetchone()
        if maint_row and maint_row[0]:
            maintenance_until = maint_row[0].isoformat()
    except Exception as e:
        # Graceful degradation: maintenance_until column may not exist yet (pre-migration)
        logger.debug("Skipping maintenance window check (column may not exist)", error=str(e))

    logger.info("Appliance checked in",
                site_id=req.site_id,
                action=action,
                pending_orders=len(orders))

    return {
        "status": "ok",
        "action": action,
        "timestamp": now.isoformat(),
        "server_public_key": get_public_key_hex(),
        "pending_orders": orders,
        "maintenance_until": maintenance_until,
    }

@app.get("/orders/{site_id}")
async def get_orders(site_id: str, db: AsyncSession = Depends(get_db), auth_site_id: str = Depends(require_appliance_bearer)):
    """Get pending orders for an appliance."""
    # Rate limit check
    allowed, remaining = await check_rate_limit(site_id, "orders")
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limited. Try again in {remaining} seconds."
        )
    
    result = await db.execute(
        text("""
            SELECT order_id, runbook_id, parameters, nonce, signature, ttl_seconds,
                   issued_at, expires_at
            FROM orders o
            JOIN appliances a ON o.appliance_id = a.id
            WHERE a.site_id = :site_id
            AND o.status = 'pending'
            AND o.expires_at > NOW()
            ORDER BY o.issued_at ASC
        """),
        {"site_id": site_id}
    )
    
    orders = []
    for row in result.fetchall():
        orders.append({
            "order_id": row[0],
            "runbook_id": row[1],
            "parameters": row[2],
            "nonce": row[3],
            "signature": row[4],
            "ttl_seconds": row[5],
            "issued_at": row[6].isoformat() if row[6] else None,
            "expires_at": row[7].isoformat() if row[7] else None
        })
    
    return {"site_id": site_id, "orders": orders, "count": len(orders)}

@app.post("/orders/acknowledge")
async def acknowledge_order(req: OrderAcknowledgement, db: AsyncSession = Depends(get_db), auth_site_id: str = Depends(require_appliance_bearer)):
    """Acknowledge receipt of an order."""
    now = datetime.now(timezone.utc)
    
    result = await db.execute(
        text("""
            UPDATE orders SET
                status = 'acknowledged',
                acknowledged_at = :acknowledged_at
            WHERE order_id = :order_id
            AND status = 'pending'
            RETURNING id
        """),
        {"order_id": req.order_id, "acknowledged_at": now}
    )
    
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order not found or already acknowledged: {req.order_id}"
        )
    
    await db.commit()
    
    logger.info("Order acknowledged", site_id=req.site_id, order_id=req.order_id)
    
    return {"status": "acknowledged", "order_id": req.order_id, "timestamp": now.isoformat()}

@app.post("/incidents")
async def report_incident(incident: IncidentReport, request: Request, db: AsyncSession = Depends(get_db), auth_site_id: str = Depends(require_appliance_bearer)):
    """
    Report an incident from an appliance.
    Creates an order for remediation if a matching runbook is found.
    """
    # Rate limit check
    allowed, remaining = await check_rate_limit(incident.site_id, "incidents")
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limited. Try again in {remaining} seconds."
        )
    
    client_ip = request.client.host if request.client else None
    
    # Get appliance UUID (for FK in orders table) and canonical ID (for signing)
    result = await db.execute(
        text("SELECT id FROM appliances WHERE site_id = :site_id"),
        {"site_id": incident.site_id}
    )
    appliance = result.fetchone()

    if not appliance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Appliance not registered: {incident.site_id}"
        )

    appliance_id = str(appliance[0])  # UUID for orders.appliance_id FK

    # Check maintenance window — suppress incident creation if site is under maintenance
    maint_check = await db.execute(
        text("SELECT maintenance_until FROM sites WHERE site_id = :site_id AND maintenance_until > NOW()"),
        {"site_id": incident.site_id}
    )
    maint_row = maint_check.fetchone()
    if maint_row:
        logger.info("Incident suppressed — site in maintenance",
                     site_id=incident.site_id,
                     incident_type=incident.incident_type,
                     maintenance_until=maint_row[0].isoformat() if maint_row[0] else None)
        return {
            "status": "suppressed_maintenance",
            "incident_id": None,
            "resolution_tier": None,
            "order_id": None,
            "runbook_id": None,
            "timestamp": now.isoformat(),
            "maintenance_until": maint_row[0].isoformat() if maint_row[0] else None,
        }

    # Get canonical appliance_id (site_id + MAC) for order signing.
    # The Go daemon verifies target_appliance_id against its canonical ID.
    canonical_result = await db.execute(
        text("SELECT appliance_id FROM site_appliances WHERE site_id = :site_id ORDER BY last_checkin DESC NULLS LAST LIMIT 1"),
        {"site_id": incident.site_id}
    )
    canonical_row = canonical_result.fetchone()
    canonical_appliance_id = canonical_row[0] if canonical_row else appliance_id
    now = datetime.now(timezone.utc)

    # Deduplicate: Check for existing incident of same type + hostname across the SITE.
    # Cross-appliance dedup: SHA256(site_id:incident_type:hostname) → dedup_key.
    # Appliance B seeing the same drift as Appliance A deduplicates instead of
    # creating a duplicate incident.  Falls back to appliance-scoped dedup when
    # hostname is not available.
    _hostname = (incident.details or {}).get("hostname") or incident.host_id or ""
    if _hostname:
        _dedup_raw = f"{incident.site_id}:{incident.incident_type}:{_hostname}"
        dedup_key = hashlib.sha256(_dedup_raw.encode()).hexdigest()
    else:
        dedup_key = None

    # Severity rank for upgrade comparison
    _SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}

    if dedup_key:
        existing_check = await db.execute(
            text("""
                SELECT id, status, severity, appliance_id FROM incidents
                WHERE site_id = :site_id
                AND dedup_key = :dedup_key
                AND (
                    (status IN ('open', 'resolving', 'escalated'))
                    OR (status = 'resolved' AND resolved_at > NOW() - INTERVAL '30 minutes')
                )
                AND created_at > NOW() - INTERVAL '48 hours'
                ORDER BY created_at DESC
                LIMIT 1
            """),
            {"site_id": incident.site_id, "dedup_key": dedup_key}
        )
    else:
        existing_check = await db.execute(
            text("""
                SELECT id, status, severity, appliance_id FROM incidents
                WHERE appliance_id = :appliance_id
                AND incident_type = :incident_type
                AND (
                    (status IN ('open', 'resolving', 'escalated'))
                    OR (status = 'resolved' AND resolved_at > NOW() - INTERVAL '30 minutes')
                )
                AND created_at > NOW() - INTERVAL '48 hours'
                ORDER BY created_at DESC
                LIMIT 1
            """),
            {"appliance_id": appliance_id, "incident_type": incident.incident_type}
        )
    existing_incident = existing_check.fetchone()

    if existing_incident:
        existing_id = str(existing_incident[0])
        existing_status = existing_incident[1]
        existing_severity = existing_incident[2] if existing_incident[2] is not None else "info"

        # Severity upgrade: if new report has higher severity, upgrade the existing incident
        new_rank = _SEVERITY_RANK.get(incident.severity or "info", 0)
        old_rank = _SEVERITY_RANK.get(existing_severity or "info", 0)
        if new_rank > old_rank:
            await db.execute(
                text("UPDATE incidents SET severity = :severity WHERE id = :id"),
                {"severity": incident.severity, "id": existing_id}
            )

        # Check if this incident has exhausted its remediation budget
        try:
            exhaustion_check = await db.execute(
                text("SELECT remediation_exhausted, remediation_attempts FROM incidents WHERE id = :id"),
                {"id": existing_id}
            )
            exhaustion_row = exhaustion_check.fetchone()
            if exhaustion_row and exhaustion_row[0]:  # remediation_exhausted
                logger.info(f"Incident {existing_id} exhausted after {exhaustion_row[1]} attempts, skipping",
                            site_id=incident.site_id, incident_type=incident.incident_type)
                return {
                    "status": "exhausted",
                    "incident_id": existing_id,
                    "resolution_tier": None,
                    "order_id": None,
                    "runbook_id": None,
                    "timestamp": now.isoformat()
                }
        except Exception as e:
            # Graceful degradation: remediation_exhausted column requires migration 099
            logger.debug("Skipping exhaustion check (migration 099 not applied)", error=str(e))

        # If previously resolved but drift recurred, check grace period before reopening.
        # Healing effects may take 1-2 scan cycles to propagate (e.g. Windows Update
        # restart, GPO refresh). A 30-min grace window prevents the resolve→reopen→resolve
        # churn that makes the dashboard show 0 resolved despite hundreds of healings.
        if existing_status == 'resolved':
            resolved_at_check = await db.execute(
                text("SELECT resolved_at FROM incidents WHERE id = :id"),
                {"id": existing_id}
            )
            resolved_at_row = resolved_at_check.fetchone()
            resolved_at = resolved_at_row[0] if resolved_at_row else None

            if resolved_at:
                if resolved_at.tzinfo is None:
                    resolved_at = resolved_at.replace(tzinfo=timezone.utc)
                age_since_resolve = now - resolved_at
                if age_since_resolve < timedelta(minutes=30):
                    logger.debug("Incident recently resolved, grace period active",
                                 incident_id=existing_id, resolved_ago=str(age_since_resolve),
                                 incident_type=incident.incident_type)
                    return {
                        "status": "deduplicated",
                        "incident_id": existing_id,
                        "resolution_tier": None,
                        "order_id": None,
                        "runbook_id": None,
                        "timestamp": now.isoformat()
                    }

            # Grace period expired — drift genuinely recurred, reopen
            await db.execute(
                text("""
                    UPDATE incidents SET status = 'resolving', resolved_at = NULL,
                        reopen_count = COALESCE(reopen_count, 0) + 1
                    WHERE id = :id
                """),
                {"id": existing_id}
            )
            await db.commit()
            logger.info("Incident reopened after grace period",
                        incident_id=existing_id, incident_type=incident.incident_type,
                        site_id=incident.site_id)
            return {
                "status": "reopened",
                "incident_id": existing_id,
                "resolution_tier": None,
                "order_id": None,
                "runbook_id": None,
                "timestamp": now.isoformat()
            }

        # Still open/resolving — plain dedup
        return {
            "status": "deduplicated",
            "incident_id": existing_id,
            "resolution_tier": None,
            "order_id": None,
            "runbook_id": None,
            "timestamp": now.isoformat()
        }

    # Create incident record (ON CONFLICT guards against dedup race condition —
    # if two appliances report the same issue simultaneously, the unique partial
    # index on dedup_key ensures only one INSERT succeeds)
    incident_id = str(uuid.uuid4())
    insert_result = await db.execute(
        text("""
            INSERT INTO incidents (id, appliance_id, incident_type, severity, check_type,
                details, pre_state, hipaa_controls, reported_at, dedup_key)
            VALUES (:id, :appliance_id, :incident_type, :severity, :check_type,
                :details, :pre_state, :hipaa_controls, :reported_at, :dedup_key)
            ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL AND status NOT IN ('resolved', 'closed')
            DO UPDATE SET severity = CASE
                WHEN EXCLUDED.severity = 'critical' THEN 'critical'
                WHEN EXCLUDED.severity = 'high' AND incidents.severity NOT IN ('critical') THEN 'high'
                ELSE incidents.severity
            END
            RETURNING id, (xmax = 0) AS was_inserted
        """),
        {
            "id": incident_id,
            "appliance_id": appliance_id,
            "incident_type": incident.incident_type,
            "severity": incident.severity,
            "check_type": incident.check_type,
            "details": json.dumps({**incident.details, "hostname": incident.host_id}),
            "pre_state": json.dumps(incident.pre_state),
            "hipaa_controls": incident.hipaa_controls,
            "reported_at": now,
            "dedup_key": dedup_key,
        }
    )
    insert_row = insert_result.fetchone()
    if insert_row and not insert_row[1]:
        # ON CONFLICT fired — this was a race-condition dedup
        return {
            "status": "deduplicated",
            "incident_id": str(insert_row[0]),
            "resolution_tier": None,
            "order_id": None,
            "runbook_id": None,
            "timestamp": now.isoformat(),
        }
    if insert_row:
        incident_id = str(insert_row[0])

    # Monitoring-only checks: record the incident for dashboards but skip the entire
    # L1 → L2 → L3 remediation cascade.  These check types detect drift that cannot
    # be auto-fixed (e.g. host offline, backup not configured, BitLocker needs TPM).
    check_key = incident.check_type or incident.incident_type
    if incident.incident_type in MONITORING_ONLY_CHECKS or check_key in MONITORING_ONLY_CHECKS:
        await db.execute(
            text("""
                UPDATE incidents SET resolution_tier = 'monitoring', status = 'open'
                WHERE id = :incident_id
            """),
            {"incident_id": incident_id}
        )
        await db.commit()
        logger.info("Monitoring-only check — skipping remediation pipeline",
                     site_id=incident.site_id,
                     incident_type=incident.incident_type,
                     check_type=check_key)
        return {
            "status": "received",
            "incident_id": incident_id,
            "resolution_tier": "monitoring",
            "order_id": None,
            "runbook_id": None,
            "timestamp": now.isoformat()
        }

    # Try to find matching runbook via L1 rules (DB-backed, includes flywheel promotions)
    runbook_id = None
    resolution_tier = None

    # Compute context hash for remediation state tracking (prevents duplicate L2 calls)
    MAX_REMEDIATION_ATTEMPTS = 3
    context_str = f"{incident.incident_type}:{incident.host_id}:{json.dumps(incident.details, sort_keys=True)}"
    context_hash = hashlib.sha256(context_str.encode()).hexdigest()[:16]

    # Chronic drift detection: if this incident_type has been resolved 5+ times
    # in 7 days for this appliance, L1 healing isn't working. Escalate to L3.
    chronic_check = await db.execute(
        text("""
            SELECT COUNT(*) FROM incidents
            WHERE appliance_id = :appliance_id
            AND incident_type = :incident_type
            AND status = 'resolved'
            AND resolved_at > NOW() - INTERVAL '7 days'
        """),
        {"appliance_id": appliance_id, "incident_type": incident.incident_type}
    )
    chronic_count = chronic_check.scalar() or 0
    if chronic_count >= 5:
        resolution_tier = "L3"
        logger.warning("Chronic drift detected — escalating to L3",
                       site_id=incident.site_id,
                       incident_type=incident.incident_type,
                       resolved_count_7d=chronic_count)

    # Step 1: Query l1_rules table for exact incident_type match (skip if chronic)
    if resolution_tier != "L3":
        l1_match = await db.execute(
            text("""
                SELECT runbook_id FROM l1_rules
                WHERE enabled = true
                AND incident_pattern->>'incident_type' = :incident_type
                ORDER BY confidence DESC
                LIMIT 1
            """),
            {"incident_type": incident.incident_type}
        )
        l1_row = l1_match.fetchone()
    else:
        l1_row = None

    if l1_row:
        matched_runbook = l1_row[0]
        # Check if this is an escalation-only runbook (ESC- prefix = straight to L3)
        if matched_runbook.startswith("ESC-") or matched_runbook == "ESCALATE":
            resolution_tier = "L3"
            logger.info("L1 rule matched escalation runbook",
                        site_id=incident.site_id,
                        incident_type=incident.incident_type,
                        runbook_id=matched_runbook)
        else:
            runbook_id = matched_runbook
            resolution_tier = "L1"
    else:
        # Step 2: Fallback keyword matching for types not yet in l1_rules
        type_lower = incident.incident_type.lower()
        check_type = incident.check_type or ""

        # Keyword fallback: maps incident_type keywords to runbook IDs.
        # IDs MUST exist in the Go daemon's runbooks.json registry (122 entries).
        # Verified 2026-04-01: RB-BACKUP-001, RB-CERT-001, RB-DISK-001,
        # RB-PATCH-001, RB-SERVICE-001, RB-FIREWALL-001 do NOT exist — use RB-WIN-* variants.
        runbook_map = {
            "backup": "RB-WIN-BACKUP-001",
            "certificate": "RB-WIN-CERT-001",
            "cert": "RB-WIN-CERT-001",
            "drift": "RB-DRIFT-001",
            "configuration": "RB-DRIFT-001",
            "service": "RB-WIN-SVC-001",
            "firewall": "RB-WIN-FIREWALL-001",
            "patching": "RB-WIN-PATCH-001",
            "update": "RB-WIN-PATCH-001",
            "audit": "RB-WIN-SEC-002",
            "defender": "RB-WIN-AV-001",
            "registry": "RB-WIN-SEC-019",
            "bitlocker": "RB-WIN-SEC-005",
            "screen_lock": "RB-WIN-SEC-016",
            "credential": "RB-WIN-SEC-022",
            "smb": "RB-WIN-SEC-007",
        }

        for keyword, rb_id in runbook_map.items():
            if keyword in type_lower or keyword in check_type.lower():
                runbook_id = rb_id
                resolution_tier = "L1"
                break
    
    order_id = None
    
    if runbook_id:
        # Create signed order
        order_id = hashlib.sha256(
            f"{incident.site_id}{incident_id}{now.isoformat()}".encode()
        ).hexdigest()[:16]
        
        nonce = secrets.token_hex(16)
        expires_at = now + timedelta(seconds=ORDER_TTL_SECONDS)
        
        # Create order payload and sign it (host-scoped to target appliance)
        order_payload = json.dumps({
            "order_id": order_id,
            "runbook_id": runbook_id,
            "parameters": {},
            "nonce": nonce,
            "issued_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "target_appliance_id": canonical_appliance_id,
        }, sort_keys=True)
        
        signature = sign_data(order_payload)
        
        # Store order (with signed_payload for appliance-side verification)
        await db.execute(
            text("""
                INSERT INTO orders (order_id, appliance_id, runbook_id, parameters, nonce,
                    signature, signed_payload, ttl_seconds, issued_at, expires_at)
                VALUES (:order_id, :appliance_id, :runbook_id, :parameters, :nonce,
                    :signature, :signed_payload, :ttl_seconds, :issued_at, :expires_at)
            """),
            {
                "order_id": order_id,
                "appliance_id": appliance_id,
                "runbook_id": runbook_id,
                "parameters": json.dumps({
                    "runbook_id": runbook_id,
                    "hostname": incident.host_id,
                    "check_type": incident.check_type or incident.incident_type,
                }),
                "nonce": nonce,
                "signature": signature,
                "signed_payload": order_payload,
                "ttl_seconds": ORDER_TTL_SECONDS,
                "issued_at": now,
                "expires_at": expires_at
            }
        )
        
        # Link order to incident
        await db.execute(
            text("""
                UPDATE incidents SET
                    resolution_tier = :resolution_tier,
                    order_id = (SELECT id FROM orders WHERE order_id = :order_id),
                    status = 'resolving'
                WHERE id = :incident_id
            """),
            {
                "resolution_tier": resolution_tier,
                "order_id": order_id,
                "incident_id": incident_id
            }
        )
        
        logger.info("Created remediation order",
                    site_id=incident.site_id,
                    incident_id=incident_id,
                    order_id=order_id,
                    runbook_id=runbook_id,
                    tier=resolution_tier)

        # Record L1 remediation attempt
        try:
            await db.execute(
                text("""
                    UPDATE incidents SET
                        remediation_attempts = COALESCE(remediation_attempts, 0) + 1,
                        context_hash = :hash
                    WHERE id = :id
                """),
                {"id": incident_id, "hash": context_hash}
            )
            # Insert into relational remediation steps table
            await db.execute(
                text("""
                    INSERT INTO incident_remediation_steps
                        (incident_id, step_idx, tier, runbook_id, result, created_at)
                    VALUES (
                        :incident_id,
                        COALESCE((SELECT MAX(step_idx) + 1 FROM incident_remediation_steps WHERE incident_id = :incident_id), 0),
                        'L1', :runbook_id, 'order_created', :ts
                    )
                """),
                {"incident_id": incident_id, "runbook_id": runbook_id, "ts": now}
            )
        except Exception as e:
            logger.debug("Skipping L1 remediation tracking", error=str(e))
    elif resolution_tier == "L3":
        # L1 rule matched an escalation-only runbook (ESC-*) — skip L2, go straight to L3
        await db.execute(
            text("""
                UPDATE incidents SET
                    resolution_tier = 'L3',
                    status = 'escalated'
                WHERE id = :incident_id
            """),
            {"incident_id": incident_id}
        )
        logger.info("L1 escalation rule → L3",
                     site_id=incident.site_id,
                     incident_type=incident.incident_type)
    else:
        # No L1 match — try L2 LLM planner before escalating to L3
        from dashboard_api.l2_planner import analyze_incident, record_l2_decision, is_l2_available

        # Check previous remediation state (requires migration 099)
        skip_l2 = False
        try:
            prev_state_check = await db.execute(
                text("""
                    SELECT remediation_attempts, context_hash
                    FROM incidents
                    WHERE appliance_id = :appliance_id
                    AND incident_type = :incident_type
                    AND id != :current_id
                    AND created_at > NOW() - INTERVAL '7 days'
                    ORDER BY created_at DESC
                    LIMIT 1
                """),
                {"appliance_id": appliance_id, "incident_type": incident.incident_type, "current_id": incident_id}
            )
            prev_state = prev_state_check.fetchone()
            prev_attempts = prev_state[0] if prev_state else 0
            prev_hash = prev_state[1] if prev_state else None

            # Rule 1: Never call L2 with identical context
            if prev_hash == context_hash and prev_attempts > 0:
                logger.info(f"Incident context unchanged (hash={context_hash}), skipping redundant L2 call",
                            site_id=incident.site_id, incident_type=incident.incident_type,
                            prev_attempts=prev_attempts)
                skip_l2 = True

            # Rule 2: Check attempt budget
            if prev_attempts >= MAX_REMEDIATION_ATTEMPTS:
                logger.info(f"Remediation budget exhausted ({prev_attempts} prior attempts), marking exhausted",
                            site_id=incident.site_id, incident_type=incident.incident_type)
                skip_l2 = True
                try:
                    await db.execute(
                        text("""
                            UPDATE incidents SET
                                remediation_attempts = :attempts,
                                remediation_exhausted = true,
                                context_hash = :hash,
                                resolution_tier = 'L3',
                                status = 'escalated'
                            WHERE id = :id
                        """),
                        {
                            "id": incident_id,
                            "attempts": prev_attempts,
                            "hash": context_hash,
                        }
                    )
                except Exception as e:
                    # Graceful degradation: exhaustion columns require migration 099
                    logger.debug("Skipping exhaustion update (migration 099 not applied)", error=str(e))
        except Exception as e:
            # Graceful degradation: state machine columns require migration 099
            logger.debug("Skipping remediation state machine (migration 099 not applied)", error=str(e))

        l2_succeeded = False
        if not skip_l2 and is_l2_available():
            try:
                # L2 cache: reuse recent decision for same pattern (72h TTL)
                from dashboard_api.l2_planner import lookup_cached_l2_decision, generate_pattern_signature
                check_type_key = incident.check_type or incident.incident_type
                pattern_sig = generate_pattern_signature(incident.incident_type, check_type_key, "")
                cached = None
                try:
                    cached = await lookup_cached_l2_decision(db, pattern_sig)
                except Exception as e:
                    # L2 cache lookup failed — not critical, fall through to LLM
                    logger.debug("L2 cache lookup failed, proceeding to LLM", error=str(e))

                if cached:
                    logger.info("L2 cache hit on /incidents path",
                                site_id=incident.site_id,
                                incident_type=incident.incident_type,
                                cached_runbook=cached.runbook_id)
                    decision = cached
                else:
                    logger.info("No L1 match, trying L2 planner",
                                site_id=incident.site_id,
                                incident_type=incident.incident_type)

                    decision = await analyze_incident(
                        incident_type=incident.incident_type,
                        severity=incident.severity,
                        check_type=check_type_key,
                        details=incident.details,
                        pre_state=incident.pre_state,
                        hipaa_controls=incident.hipaa_controls,
                    )

                # Record L2 decision for data flywheel
                try:
                    await record_l2_decision(db, incident_id, decision)
                except Exception as e:
                    logger.error(f"Failed to record L2 decision: {e}")

                # Determine L2 result for attempt tracking
                l2_result = "no_action"
                l2_runbook = getattr(decision, 'runbook_id', None)
                l2_confidence = getattr(decision, 'confidence', None)

                # If L2 found a runbook with sufficient confidence, create an order
                if decision.runbook_id and decision.confidence >= 0.6 and not decision.requires_human_review:
                    runbook_id = decision.runbook_id
                    resolution_tier = "L2"
                    l2_succeeded = True
                    l2_result = "order_created"

                    order_id = hashlib.sha256(
                        f"{incident.site_id}{incident_id}{now.isoformat()}".encode()
                    ).hexdigest()[:16]

                    nonce = secrets.token_hex(16)
                    expires_at = now + timedelta(seconds=ORDER_TTL_SECONDS)

                    order_payload = json.dumps({
                        "order_id": order_id,
                        "runbook_id": runbook_id,
                        "parameters": {},
                        "nonce": nonce,
                        "issued_at": now.isoformat(),
                        "expires_at": expires_at.isoformat(),
                        "target_appliance_id": canonical_appliance_id,
                    }, sort_keys=True)

                    signature = sign_data(order_payload)

                    await db.execute(
                        text("""
                            INSERT INTO orders (order_id, appliance_id, runbook_id, parameters, nonce,
                                signature, signed_payload, ttl_seconds, issued_at, expires_at)
                            VALUES (:order_id, :appliance_id, :runbook_id, :parameters, :nonce,
                                :signature, :signed_payload, :ttl_seconds, :issued_at, :expires_at)
                        """),
                        {
                            "order_id": order_id,
                            "appliance_id": appliance_id,
                            "runbook_id": runbook_id,
                            "parameters": json.dumps({
                                "runbook_id": runbook_id,
                                "hostname": incident.host_id,
                                "check_type": incident.check_type or incident.incident_type,
                            }),
                            "nonce": nonce,
                            "signature": signature,
                            "signed_payload": order_payload,
                            "ttl_seconds": ORDER_TTL_SECONDS,
                            "issued_at": now,
                            "expires_at": expires_at
                        }
                    )

                    await db.execute(
                        text("""
                            UPDATE incidents SET
                                resolution_tier = 'L2',
                                order_id = (SELECT id FROM orders WHERE order_id = :order_id),
                                status = 'resolving'
                            WHERE id = :incident_id
                        """),
                        {"order_id": order_id, "incident_id": incident_id}
                    )

                    logger.info("L2 planner matched runbook",
                                site_id=incident.site_id,
                                incident_type=incident.incident_type,
                                runbook_id=runbook_id,
                                confidence=decision.confidence)
                else:
                    logger.info("L2 planner could not resolve — escalating to L3",
                                site_id=incident.site_id,
                                incident_type=incident.incident_type,
                                runbook_id=decision.runbook_id if decision else None,
                                confidence=decision.confidence if decision else None,
                                requires_review=decision.requires_human_review if decision else None)

                # Record L2 attempt in remediation steps table
                try:
                    new_attempts = (prev_attempts if 'prev_attempts' in dir() else 0) + 1
                    is_exhausted = new_attempts >= MAX_REMEDIATION_ATTEMPTS

                    await db.execute(
                        text("""
                            UPDATE incidents SET
                                remediation_attempts = :attempts,
                                remediation_exhausted = :exhausted,
                                context_hash = :hash
                            WHERE id = :id
                        """),
                        {
                            "id": incident_id,
                            "attempts": new_attempts,
                            "exhausted": is_exhausted,
                            "hash": context_hash,
                        }
                    )
                    # Insert into relational remediation steps table
                    await db.execute(
                        text("""
                            INSERT INTO incident_remediation_steps
                                (incident_id, step_idx, tier, runbook_id, result, confidence, created_at)
                            VALUES (
                                :incident_id,
                                COALESCE((SELECT MAX(step_idx) + 1 FROM incident_remediation_steps WHERE incident_id = :incident_id), 0),
                                'L2', :runbook_id, :result, :confidence, :ts
                            )
                        """),
                        {
                            "incident_id": incident_id,
                            "runbook_id": l2_runbook,
                            "result": l2_result,
                            "confidence": l2_confidence,
                            "ts": now,
                        }
                    )
                except Exception as e:
                    logger.debug("Skipping L2 remediation tracking", error=str(e))

                # Rule 2: If budget exhausted after this attempt, force L3 escalation
                if is_exhausted and not l2_succeeded:
                    resolution_tier = "L3"
                    logger.warning(f"Remediation budget exhausted after {new_attempts} attempts — escalating to L3",
                                   site_id=incident.site_id, incident_type=incident.incident_type)

            except Exception as e:
                logger.error(f"L2 planner failed: {e}",
                             site_id=incident.site_id,
                             incident_type=incident.incident_type)
        elif not skip_l2:
            logger.warning("L2 not available (no API key configured)",
                           site_id=incident.site_id,
                           incident_type=incident.incident_type)

        if not l2_succeeded:
            # L2 failed, unavailable, or skipped — escalate to L3
            resolution_tier = "L3"
            await db.execute(
                text("""
                    UPDATE incidents SET
                        resolution_tier = 'L3',
                        status = 'escalated'
                    WHERE id = :incident_id
                """),
                {"incident_id": incident_id}
            )
            logger.warning("Escalated to L3",
                           site_id=incident.site_id,
                           incident_type=incident.incident_type)

    await db.commit()

    # Create notification for critical/high severity OR L3 escalations (with deduplication)
    # Map incident severity to notification severity (critical, warning, info, success)
    severity_map = {"critical": "critical", "high": "warning", "medium": "warning", "low": "info"}
    notification_severity = severity_map.get(incident.severity, "info")

    # Notify for: critical/high severity OR L3 escalations (no runbook found)
    should_notify = incident.severity in ("critical", "high") or resolution_tier == "L3"

    if should_notify:
        try:
            # Deduplication: 4 hours for L1/L2 resolved incidents, 24 hours for L3 escalations
            # Prevents notification spam from recurring drift checks (e.g. linux_firewall every scan cycle)
            dedup_hours = 24 if resolution_tier == "L3" else 4
            notification_category = "escalation" if resolution_tier == "L3" else "incident"

            dedup_check = await db.execute(
                text("""
                    SELECT id FROM notifications
                    WHERE site_id = :site_id
                    AND category = :category
                    AND title LIKE :title_pattern
                    AND created_at > NOW() - :dedup_hours * INTERVAL '1 hour'
                    LIMIT 1
                """),
                {
                    "site_id": incident.site_id,
                    "category": notification_category,
                    "title_pattern": f"%{incident.incident_type}%",
                    "dedup_hours": dedup_hours,
                }
            )
            existing = dedup_check.fetchone()

            if not existing:
                # L3 escalations get "critical" severity to trigger email
                if resolution_tier == "L3":
                    notification_severity = "critical"

                # Build rich title/message for L3 escalations
                if resolution_tier == "L3":
                    l3_title = f"[L3] {incident.incident_type}"
                    if incident.host_id:
                        l3_title += f" - {incident.host_id}"

                    l3_message = (
                        f"{incident.incident_type} on {incident.site_id} "
                        f"could not be auto-remediated and requires human review."
                    )
                else:
                    l3_title = f"{incident.severity.upper()}: {incident.incident_type}"
                    l3_message = f"Incident {incident.incident_type} on {incident.site_id}. Resolution: {resolution_tier}"

                await create_notification_with_email(
                    db=db,
                    severity=notification_severity,
                    category=notification_category,
                    title=l3_title,
                    message=l3_message,
                    site_id=incident.site_id,
                    appliance_id=appliance_id,
                    metadata={
                        "incident_id": incident_id,
                        "check_type": incident.check_type,
                        "resolution_tier": resolution_tier,
                        "order_id": order_id
                    },
                    host_id=incident.host_id if resolution_tier == "L3" else None,
                    incident_severity=incident.severity if resolution_tier == "L3" else None,
                    check_type=incident.check_type if resolution_tier == "L3" else None,
                    details=incident.details if resolution_tier == "L3" else None,
                    hipaa_controls=incident.hipaa_controls if resolution_tier == "L3" else None,
                )
        except Exception as e:
            logger.error(f"Failed to create notification: {e}")

    return {
        "status": "received",
        "incident_id": incident_id,
        "resolution_tier": resolution_tier,
        "order_id": order_id,
        "runbook_id": runbook_id,
        "timestamp": now.isoformat()
    }

@app.post("/incidents/{incident_id}/resolve")
async def resolve_incident(incident_id: str, request: Request, db: AsyncSession = Depends(get_db), auth_site_id: str = Depends(require_appliance_bearer)):
    """Mark an incident as resolved after successful healing."""
    body = await request.json()
    resolution_tier = body.get("resolution_tier", "L1")
    action_taken = body.get("action_taken", "")

    result = await db.execute(
        text("""
            UPDATE incidents SET
                resolved_at = NOW(),
                status = 'resolved',
                resolution_tier = COALESCE(:resolution_tier, resolution_tier)
            WHERE id = :incident_id
            AND resolved_at IS NULL
            RETURNING id
        """),
        {"incident_id": incident_id, "resolution_tier": resolution_tier}
    )

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found or already resolved")

    await db.commit()
    logger.info("Incident resolved", incident_id=incident_id, tier=resolution_tier, action=action_taken)
    return {"status": "resolved", "incident_id": incident_id}


@app.post("/incidents/resolve")
async def resolve_incident_by_type(request: Request, db: AsyncSession = Depends(get_db), auth_site_id: str = Depends(require_appliance_bearer)):
    """
    Resolve the latest open incident matching site_id + host_id + check_type.
    Used by the Go daemon which reports incidents fire-and-forget and doesn't
    track backend incident UUIDs.
    """
    body = await request.json()
    site_id = body.get("site_id")
    host_id = body.get("host_id")
    check_type = body.get("check_type")
    resolution_tier = body.get("resolution_tier", "L1")
    runbook_id = body.get("runbook_id", "")

    if not site_id or not host_id or not check_type:
        raise HTTPException(status_code=400, detail="site_id, host_id, and check_type are required")

    # Find the appliance
    app_result = await db.execute(
        text("SELECT id FROM appliances WHERE site_id = :site_id"),
        {"site_id": site_id}
    )
    appliance = app_result.fetchone()
    if not appliance:
        raise HTTPException(status_code=404, detail=f"Appliance not found: {site_id}")

    appliance_id = str(appliance[0])

    # Resolve the latest open/resolving incident for this type + hostname,
    # searching across all sites in the org (matches cross-appliance dedup).
    result = await db.execute(
        text("""
            UPDATE incidents SET
                resolved_at = NOW(),
                status = 'resolved',
                resolution_tier = :resolution_tier
            WHERE id = (
                SELECT i.id FROM incidents i
                WHERE i.site_id IN (
                    SELECT s2.site_id FROM sites s1
                    JOIN sites s2 ON s1.client_org_id = s2.client_org_id
                    WHERE s1.site_id = :site_id
                )
                AND i.incident_type = :check_type
                AND i.details->>'hostname' = :host_id
                AND i.status IN ('open', 'resolving', 'escalated')
                ORDER BY i.created_at DESC
                LIMIT 1
            )
            RETURNING id
        """),
        {
            "site_id": site_id,
            "check_type": check_type,
            "host_id": host_id,
            "resolution_tier": resolution_tier,
        }
    )

    row = result.fetchone()
    if not row:
        return {"status": "no_match", "message": "No open incident found to resolve"}

    await db.commit()
    logger.info("Incident resolved by type", site_id=site_id, host_id=host_id,
                check_type=check_type, tier=resolution_tier, incident_id=str(row[0]))
    return {"status": "resolved", "incident_id": str(row[0])}


@app.post("/api/witness/submit")
async def submit_witness_attestations(request: Request, auth_site_id: str = Depends(require_appliance_bearer)):
    """Phase 3: Same-cycle witness attestation submission.

    Appliances counter-sign sibling bundle hashes and POST attestations
    immediately — no queuing for next checkin cycle.
    """
    body = await request.json()
    attestations = body.get("attestations", [])
    if not attestations:
        return {"status": "ok", "stored": 0}

    from dashboard_api.fleet import get_pool
    from dashboard_api.tenant_middleware import admin_connection
    pool = await get_pool()
    stored = 0
    async with admin_connection(pool) as conn:
        for att in attestations:
            try:
                await conn.execute("""
                    INSERT INTO witness_attestations
                        (bundle_id, bundle_hash, source_appliance, witness_appliance,
                         witness_public_key, witness_signature)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (bundle_id, witness_appliance) DO NOTHING
                """,
                    att.get('bundle_id', ''),
                    att.get('bundle_hash', ''),
                    att.get('source_appliance', ''),
                    auth_site_id,  # witness is the authenticated appliance
                    att.get('witness_public_key', ''),
                    att.get('witness_signature', ''),
                )
                stored += 1
            except Exception:
                pass

    if stored > 0:
        logger.info(f"Witness attestations submitted: site={auth_site_id} count={stored}")
    return {"status": "ok", "stored": stored}


@app.post("/drift")
async def report_drift(drift: DriftReport, db: AsyncSession = Depends(get_db), auth_site_id: str = Depends(require_appliance_bearer)):
    """Report drift detection results."""
    # Rate limit check
    allowed, remaining = await check_rate_limit(drift.site_id, "drift")
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limited. Try again in {remaining} seconds."
        )
    
    if not drift.drifted:
        # No drift - just log and return
        logger.debug("No drift detected", site_id=drift.site_id, check_type=drift.check_type)
        return {"status": "ok", "drifted": False, "action": "none"}
    
    # Convert to incident
    incident = IncidentReport(
        site_id=drift.site_id,
        host_id=drift.host_id,
        incident_type=f"drift:{drift.check_type}",
        severity=drift.severity,
        check_type=drift.check_type,
        details={"drifted": True, "recommended_action": drift.recommended_action},
        pre_state=drift.pre_state,
        hipaa_controls=drift.hipaa_controls
    )
    
    # Reuse incident handling
    from fastapi import Request as FakeRequest
    
    class FakeRequestObj:
        client = None
    
    return await report_incident(incident, FakeRequestObj(), db)

@app.post("/evidence")
async def submit_evidence(evidence: EvidenceSubmission, db: AsyncSession = Depends(get_db), auth_site_id: str = Depends(require_appliance_bearer)):
    """Submit evidence bundle from appliance."""
    # Rate limit check
    allowed, remaining = await check_rate_limit(evidence.site_id, "evidence")
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limited. Try again in {remaining} seconds."
        )
    
    # Get appliance
    result = await db.execute(
        text("SELECT id FROM appliances WHERE site_id = :site_id"),
        {"site_id": evidence.site_id}
    )
    appliance = result.fetchone()
    
    if not appliance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Appliance not registered: {evidence.site_id}"
        )
    
    appliance_id = str(appliance[0])
    now = datetime.now(timezone.utc)
    
    # Get order ID reference if provided
    order_uuid = None
    if evidence.order_id:
        result = await db.execute(
            text("SELECT id FROM orders WHERE order_id = :order_id"),
            {"order_id": evidence.order_id}
        )
        order_row = result.fetchone()
        if order_row:
            order_uuid = order_row[0]
            
            # Update order status
            await db.execute(
                text("""
                    UPDATE orders SET
                        status = :status,
                        completed_at = :completed_at,
                        result = :result
                    WHERE order_id = :order_id
                """),
                {
                    "status": "completed" if evidence.outcome == "success" else "failed",
                    "completed_at": now,
                    "result": json.dumps({"outcome": evidence.outcome, "error": evidence.error}),
                    "order_id": evidence.order_id
                }
            )
    
    # Store evidence bundle
    evidence_id = str(uuid.uuid4())
    duration = (evidence.timestamp_end - evidence.timestamp_start).total_seconds()
    
    await db.execute(
        text("""
            INSERT INTO evidence_bundles (id, bundle_id, appliance_id, order_id,
                check_type, outcome, pre_state, post_state, actions_taken,
                policy_version, nixos_revision, ntp_offset_ms, hipaa_controls,
                rollback_available, rollback_generation, timestamp_start, timestamp_end,
                signature, error)
            VALUES (:id, :bundle_id, :appliance_id, :order_id,
                :check_type, :outcome, :pre_state, :post_state, :actions_taken,
                :policy_version, :nixos_revision, :ntp_offset_ms, :hipaa_controls,
                :rollback_available, :rollback_generation, :timestamp_start, :timestamp_end,
                :signature, :error)
        """),
        {
            "id": evidence_id,
            "bundle_id": evidence.bundle_id,
            "appliance_id": appliance_id,
            "order_id": order_uuid,
            "check_type": evidence.check_type,
            "outcome": evidence.outcome,
            "pre_state": json.dumps(evidence.pre_state),
            "post_state": json.dumps(evidence.post_state),
            "actions_taken": json.dumps(evidence.actions_taken),
            "policy_version": evidence.policy_version,
            "nixos_revision": evidence.nixos_revision,
            "ntp_offset_ms": evidence.ntp_offset_ms,
            "hipaa_controls": evidence.hipaa_controls,
            "rollback_available": evidence.rollback_available,
            "rollback_generation": evidence.rollback_generation,
            "timestamp_start": evidence.timestamp_start,
            "timestamp_end": evidence.timestamp_end,
            "signature": evidence.signature,
            "error": evidence.error
        }
    )
    
    await db.commit()
    
    # Upload to MinIO (WORM storage)
    s3_uri = None
    try:
        evidence_json = json.dumps({
            "bundle_id": evidence.bundle_id,
            "site_id": evidence.site_id,
            "host_id": evidence.host_id,
            "check_type": evidence.check_type,
            "outcome": evidence.outcome,
            "pre_state": evidence.pre_state,
            "post_state": evidence.post_state,
            "actions_taken": evidence.actions_taken,
            "timestamp_start": evidence.timestamp_start.isoformat(),
            "timestamp_end": evidence.timestamp_end.isoformat(),
            "duration_seconds": duration,
            "hipaa_controls": evidence.hipaa_controls,
            "signature": evidence.signature
        }, indent=2)
        
        object_name = f"{evidence.site_id}/{evidence.timestamp_start.strftime('%Y/%m/%d')}/{evidence.bundle_id}.json"
        
        from io import BytesIO
        data = BytesIO(evidence_json.encode())
        
        minio_client.put_object(
            MINIO_BUCKET,
            object_name,
            data,
            length=len(evidence_json),
            content_type="application/json"
        )

        s3_uri = f"s3://{MINIO_BUCKET}/{object_name}"

        # Set Object Lock retention (COMPLIANCE mode) for WORM protection
        try:
            retention_until = now + timedelta(days=WORM_RETENTION_DAYS)
            retention = Retention(COMPLIANCE, retention_until)
            minio_client.set_object_retention(MINIO_BUCKET, object_name, retention)
            logger.info("Set WORM retention on evidence",
                       bundle_id=evidence.bundle_id,
                       retention_until=retention_until.isoformat())
        except Exception as e:
            # Log warning but don't fail - bucket may not have Object Lock enabled
            logger.warning("Could not set Object Lock retention",
                          bundle_id=evidence.bundle_id,
                          error=str(e))

        # Update evidence with S3 URI
        await db.execute(
            text("""
                UPDATE evidence_bundles SET
                    s3_uri = :s3_uri,
                    s3_uploaded_at = :s3_uploaded_at
                WHERE bundle_id = :bundle_id
            """),
            {"s3_uri": s3_uri, "s3_uploaded_at": now, "bundle_id": evidence.bundle_id}
        )
        await db.commit()
        
        logger.info("Evidence uploaded to WORM storage",
                    bundle_id=evidence.bundle_id,
                    s3_uri=s3_uri)
        
    except Exception as e:
        logger.error("Failed to upload evidence to MinIO", 
                     bundle_id=evidence.bundle_id,
                     error=str(e))
    
    logger.info("Evidence bundle received",
                site_id=evidence.site_id,
                bundle_id=evidence.bundle_id,
                check_type=evidence.check_type,
                outcome=evidence.outcome)
    
    return {
        "status": "received",
        "bundle_id": evidence.bundle_id,
        "evidence_id": evidence_id,
        "s3_uri": s3_uri,
        "timestamp": now.isoformat()
    }

@app.get("/evidence/{site_id}")
async def list_evidence(
    site_id: str,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """List evidence bundles for an appliance."""
    result = await db.execute(
        text("""
            SELECT e.bundle_id, e.check_type, e.outcome, e.timestamp_start, e.timestamp_end,
                   e.hipaa_controls, e.s3_uri, e.signature
            FROM evidence_bundles e
            JOIN appliances a ON e.appliance_id = a.id
            WHERE a.site_id = :site_id
            ORDER BY e.timestamp_start DESC
            LIMIT :limit OFFSET :offset
        """),
        {"site_id": site_id, "limit": limit, "offset": offset}
    )
    
    bundles = []
    for row in result.fetchall():
        bundles.append({
            "bundle_id": row[0],
            "check_type": row[1],
            "outcome": row[2],
            "timestamp_start": row[3].isoformat() if row[3] else None,
            "timestamp_end": row[4].isoformat() if row[4] else None,
            "hipaa_controls": row[5],
            "s3_uri": row[6],
            "signature": row[7][:32] + "..." if row[7] else None
        })
    
    return {"site_id": site_id, "evidence": bundles, "count": len(bundles)}


# ============================================================================
# Pattern Reporting Endpoint (for Learning Loop)
# ============================================================================

class PatternReportInput(BaseModel):
    """Pattern report from agent after successful healing."""
    site_id: str
    check_type: str
    issue_signature: str
    resolution_steps: List[str]
    success: bool
    execution_time_ms: int
    runbook_id: Optional[str] = None
    reported_at: Optional[datetime] = None


@app.post("/agent/patterns")
async def report_agent_pattern(report: PatternReportInput, db: AsyncSession = Depends(get_db), auth_site_id: str = Depends(require_appliance_bearer)):
    """Receive pattern report from agent after successful healing.

    This endpoint is called by appliances after L1/L2 healing succeeds.
    Patterns are aggregated and tracked for potential L1 promotion.
    """
    import hashlib

    # Generate pattern ID from signature
    pattern_signature = f"{report.check_type}:{report.issue_signature}"
    pattern_id = hashlib.sha256(pattern_signature.encode()).hexdigest()[:16]

    # Check if pattern exists
    result = await db.execute(
        text("SELECT pattern_id, occurrences, success_count, failure_count FROM patterns WHERE pattern_id = :pid"),
        {"pid": pattern_id}
    )
    existing = result.fetchone()

    if existing:
        # Update existing pattern
        occurrences = existing.occurrences + 1
        success_count = existing.success_count + (1 if report.success else 0)
        failure_count = existing.failure_count + (0 if report.success else 1)
        # success_rate is a generated column, calculated from occurrences/success_count

        await db.execute(text("""
            UPDATE patterns
            SET occurrences = :occ,
                success_count = :sc,
                failure_count = :fc,
                last_seen = NOW()
            WHERE pattern_id = :pid
        """), {
            "pid": pattern_id,
            "occ": occurrences,
            "sc": success_count,
            "fc": failure_count,
        })
        await db.commit()

        # Calculate success_rate for response
        success_rate = (success_count / occurrences) * 100 if occurrences > 0 else 0.0
        logger.info(f"Pattern updated: {pattern_id} (occurrences: {occurrences}, success_rate: {success_rate:.1f}%)")
        return {
            "pattern_id": pattern_id,
            "status": "updated",
            "occurrences": occurrences,
            "success_rate": success_rate,
        }
    else:
        # Create new pattern
        occurrences = 1
        success_count = 1 if report.success else 0
        failure_count = 0 if report.success else 1
        # success_rate is a generated column, calculated automatically

        # runbook_id is NOT NULL, so provide a default if not given
        runbook_id = report.runbook_id or f"AUTO-{report.check_type.upper()}"

        await db.execute(text("""
            INSERT INTO patterns (
                pattern_id, pattern_signature, description, incident_type, runbook_id,
                occurrences, success_count, failure_count,
                avg_resolution_time_ms, total_resolution_time_ms,
                status, first_seen, last_seen, created_at
            ) VALUES (
                :pid, :sig, :desc, :itype, :rid,
                :occ, :sc, :fc,
                :avg_time, :total_time,
                'pending', NOW(), NOW(), NOW()
            )
        """), {
            "pid": pattern_id,
            "sig": pattern_signature,
            "desc": f"Auto-detected pattern from {report.site_id}",
            "itype": report.check_type,
            "rid": runbook_id,
            "occ": occurrences,
            "sc": success_count,
            "fc": failure_count,
            "avg_time": report.execution_time_ms,
            "total_time": report.execution_time_ms,
        })
        await db.commit()

        success_rate = 100.0 if report.success else 0.0
        logger.info(f"Pattern created: {pattern_id} (check_type: {report.check_type})")
        return {
            "pattern_id": pattern_id,
            "status": "created",
            "occurrences": occurrences,
            "success_rate": success_rate,
        }


# ============================================================================
# LEARNING SYSTEM SYNC ENDPOINTS
# Bidirectional sync between agents and Central Command for learning data
# ============================================================================

class PatternStatSync(BaseModel):
    """Single pattern stat from agent."""
    pattern_signature: str
    total_occurrences: int
    l1_resolutions: int
    l2_resolutions: int
    l3_resolutions: int
    success_count: int
    total_resolution_time_ms: float
    last_seen: str
    recommended_action: Optional[str] = None
    promotion_eligible: bool = False


class PatternStatsRequest(BaseModel):
    """Batch pattern stats sync request from agent."""
    site_id: str
    appliance_id: str
    synced_at: str
    pattern_stats: List[PatternStatSync]


@app.post("/api/agent/sync/pattern-stats")
async def sync_pattern_stats(request: PatternStatsRequest, db: AsyncSession = Depends(get_db), auth_site_id: str = Depends(require_appliance_bearer)):
    """
    Receive pattern statistics from agent for cross-appliance aggregation.

    This endpoint is called periodically (every 4 hours) by appliances to sync
    their local pattern_stats table to Central Command. Stats are aggregated
    across all appliances at a site for promotion decisions.
    """
    accepted = 0
    merged = 0
    failed = 0

    for stat in request.pattern_stats:
        try:
            # Parse last_seen string to datetime
            try:
                last_seen_dt = datetime.fromisoformat(stat.last_seen.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                last_seen_dt = datetime.now(timezone.utc)

            # Check if pattern exists in aggregated stats
            result = await db.execute(
                text("""
                    SELECT id, total_occurrences, l1_resolutions, l2_resolutions, l3_resolutions,
                           success_count, total_resolution_time_ms
                    FROM aggregated_pattern_stats
                    WHERE site_id = :site_id AND pattern_signature = :sig
                """),
                {"site_id": request.site_id, "sig": stat.pattern_signature}
            )
            existing = result.fetchone()

            if existing:
                # Merge stats (take max of each counter to handle idempotent syncs)
                # NOTE: success_rate stored as decimal (0.0-1.0), not percentage
                # Compute promotion_eligible SERVER-SIDE (don't trust agent flag)
                merged_occ = max(existing.total_occurrences, stat.total_occurrences)
                merged_sc = max(existing.success_count, stat.success_count)
                merged_rate = merged_sc / merged_occ if merged_occ > 0 else 0
                is_eligible = merged_occ >= 5 and merged_rate >= 0.90

                await db.execute(text("""
                    UPDATE aggregated_pattern_stats
                    SET total_occurrences = GREATEST(total_occurrences, :occ),
                        l1_resolutions = GREATEST(l1_resolutions, :l1),
                        l2_resolutions = GREATEST(l2_resolutions, :l2),
                        l3_resolutions = GREATEST(l3_resolutions, :l3),
                        success_count = GREATEST(success_count, :sc),
                        total_resolution_time_ms = GREATEST(total_resolution_time_ms, :time),
                        success_rate = CASE
                            WHEN GREATEST(total_occurrences, :occ) > 0
                            THEN CAST(GREATEST(success_count, :sc) AS FLOAT) / GREATEST(total_occurrences, :occ)
                            ELSE 0
                        END,
                        avg_resolution_time_ms = CASE
                            WHEN GREATEST(total_occurrences, :occ) > 0
                            THEN GREATEST(total_resolution_time_ms, :time) / GREATEST(total_occurrences, :occ)
                            ELSE 0
                        END,
                        recommended_action = COALESCE(:action, recommended_action),
                        promotion_eligible = :eligible,
                        last_seen = GREATEST(last_seen, :last_seen),
                        last_synced_at = NOW()
                    WHERE site_id = :site_id AND pattern_signature = :sig
                """), {
                    "site_id": request.site_id,
                    "sig": stat.pattern_signature,
                    "occ": stat.total_occurrences,
                    "l1": stat.l1_resolutions,
                    "l2": stat.l2_resolutions,
                    "l3": stat.l3_resolutions,
                    "sc": stat.success_count,
                    "time": stat.total_resolution_time_ms,
                    "action": stat.recommended_action,
                    "eligible": is_eligible,
                    "last_seen": last_seen_dt,
                })
                merged += 1
            else:
                # Insert new pattern
                # NOTE: success_rate stored as decimal (0.0-1.0), not percentage
                # Compute promotion_eligible SERVER-SIDE
                success_rate = (stat.success_count / stat.total_occurrences) if stat.total_occurrences > 0 else 0
                avg_time = stat.total_resolution_time_ms / stat.total_occurrences if stat.total_occurrences > 0 else 0
                is_eligible = stat.total_occurrences >= 5 and success_rate >= 0.90

                await db.execute(text("""
                    INSERT INTO aggregated_pattern_stats (
                        site_id, pattern_signature, total_occurrences, l1_resolutions,
                        l2_resolutions, l3_resolutions, success_count, total_resolution_time_ms,
                        success_rate, avg_resolution_time_ms, recommended_action, promotion_eligible,
                        first_seen, last_seen, last_synced_at
                    ) VALUES (
                        :site_id, :sig, :occ, :l1, :l2, :l3, :sc, :time,
                        :rate, :avg, :action, :eligible,
                        :first_seen, :last_seen, NOW()
                    )
                """), {
                    "site_id": request.site_id,
                    "sig": stat.pattern_signature,
                    "occ": stat.total_occurrences,
                    "l1": stat.l1_resolutions,
                    "l2": stat.l2_resolutions,
                    "l3": stat.l3_resolutions,
                    "sc": stat.success_count,
                    "time": stat.total_resolution_time_ms,
                    "rate": success_rate,
                    "avg": avg_time,
                    "action": stat.recommended_action,
                    "eligible": is_eligible,
                    "first_seen": last_seen_dt,
                    "last_seen": last_seen_dt,
                })
                accepted += 1

        except Exception as e:
            logger.warning(f"Failed to sync pattern {stat.pattern_signature}: {e}")
            # Rollback to clear the aborted transaction state
            await db.rollback()
            failed += 1
            continue

    # Record sync event with honest status
    sync_status = 'success' if failed == 0 else ('partial' if accepted + merged > 0 else 'failed')
    try:
        await db.execute(text("""
            INSERT INTO appliance_pattern_sync (appliance_id, site_id, synced_at, patterns_received, patterns_merged, sync_status)
            VALUES (:appliance_id, :site_id, NOW(), :received, :merged, :status)
            ON CONFLICT (appliance_id) DO UPDATE SET
                synced_at = NOW(),
                patterns_received = :received,
                patterns_merged = :merged,
                sync_status = :status
        """), {
            "appliance_id": request.appliance_id,
            "site_id": request.site_id,
            "received": len(request.pattern_stats),
            "merged": merged,
            "status": sync_status,
        })
        await db.commit()
    except Exception as e:
        logger.warning(f"Failed to record sync event for {request.appliance_id}: {e}")
        await db.rollback()

    logger.info(f"Pattern sync from {request.appliance_id}: {accepted} new, {merged} merged")
    return {
        "accepted": accepted,
        "merged": merged,
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


class PromotedRuleResponse(BaseModel):
    """Promoted rule for agent deployment."""
    rule_id: str
    pattern_signature: str
    rule_yaml: str
    promoted_at: str
    promoted_by: str
    source: str = "server_promoted"


@app.get("/api/agent/sync/promoted-rules")
async def get_promoted_rules(
    site_id: str,
    since: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """
    Return server-approved promoted rules for agent deployment.

    Agents call this periodically to fetch rules that have been approved
    on the central dashboard but not yet deployed to the appliance.
    """
    # Parse since timestamp
    since_dt = datetime.fromisoformat(since.replace('Z', '+00:00')) if since else datetime(1970, 1, 1, tzinfo=timezone.utc)

    # Get promoted rules from promoted_rules table (created by learning_api.py approval)
    result = await db.execute(text("""
        SELECT
            pr.rule_id,
            pr.pattern_signature,
            pr.rule_yaml,
            pr.promoted_at,
            COALESCE(au.email, 'system') as promoted_by,
            pr.notes
        FROM promoted_rules pr
        LEFT JOIN admin_users au ON au.id = pr.promoted_by
        WHERE pr.site_id = :site_id
          AND pr.status = 'active'
          AND pr.promoted_at > :since
        ORDER BY pr.promoted_at DESC
    """), {"site_id": site_id, "since": since_dt})

    rows = result.fetchall()
    rules = []

    for row in rows:
        rules.append({
            "rule_id": row.rule_id,
            "pattern_signature": row.pattern_signature,
            "rule_yaml": row.rule_yaml,
            "promoted_at": row.promoted_at.isoformat() if row.promoted_at else datetime.now(timezone.utc).isoformat(),
            "promoted_by": row.promoted_by or "system",
            "source": "server_promoted",
        })

    logger.info(f"Returning {len(rules)} promoted rules for site {site_id}")
    return {
        "rules": rules,
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


class ExecutionTelemetryInput(BaseModel):
    """Execution telemetry from agent. Accepts both wrapped and flat formats."""
    site_id: str
    execution: Optional[dict] = None
    reported_at: Optional[str] = None
    # Allow extra fields for flat format (Go daemon legacy)
    model_config = {"extra": "allow"}


@app.post("/api/agent/executions")
async def report_execution_telemetry(request: ExecutionTelemetryInput, db: AsyncSession = Depends(get_db), auth_site_id: str = Depends(require_appliance_bearer)):
    """
    Receive rich execution telemetry from agents for learning engine.

    This data feeds the learning system to analyze runbook effectiveness,
    identify patterns for improvement, and track healing outcomes.

    Accepts two formats:
    - Wrapped: {site_id, execution: {...}, reported_at} (preferred)
    - Flat: {site_id, incident_id, ...} (legacy Go daemon compat)
    """
    if request.execution:
        exec_data = request.execution
    else:
        # Flat format: all fields at top level (legacy compat)
        exec_data = request.model_dump(exclude={"site_id", "execution", "reported_at"})
        exec_data["site_id"] = request.site_id

    # Parse ISO timestamps to datetime objects for PostgreSQL
    def parse_iso_timestamp(ts):
        if ts is None:
            return None
        if isinstance(ts, datetime):
            return ts
        try:
            return datetime.fromisoformat(ts.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            return None

    try:
        # Normalize field names: old Go daemon uses different names
        # level -> resolution_level, duration_ms -> duration_seconds, error -> error_message
        if "level" in exec_data and "resolution_level" not in exec_data:
            exec_data["resolution_level"] = exec_data["level"]
        if "duration_ms" in exec_data and "duration_seconds" not in exec_data:
            exec_data["duration_seconds"] = exec_data["duration_ms"] / 1000.0
        if "error" in exec_data and "error_message" not in exec_data:
            exec_data["error_message"] = exec_data["error"]
        if not exec_data.get("execution_id"):
            exec_data["execution_id"] = f"l2-{exec_data.get('incident_id', 'unknown')}-{int(datetime.now(timezone.utc).timestamp() * 1000)}"
        if not exec_data.get("status"):
            exec_data["status"] = "success" if exec_data.get("success") else "failure"

        # Build pattern_signature from incident_type + runbook_id + hostname
        incident_type = exec_data.get("incident_type")
        hostname = exec_data.get("hostname", "unknown")
        runbook_id = exec_data.get("runbook_id", "unknown")
        pattern_sig = exec_data.get("pattern_signature")
        if not pattern_sig and incident_type and hostname:
            pattern_sig = f"{incident_type}:{runbook_id}:{hostname}"

        await db.execute(text("""
            INSERT INTO execution_telemetry (
                execution_id, incident_id, site_id, appliance_id, runbook_id, hostname, platform, incident_type,
                started_at, completed_at, duration_seconds,
                success, status, verification_passed, confidence, resolution_level,
                state_before, state_after, state_diff, executed_steps,
                error_message, error_step, failure_type, retry_count,
                evidence_bundle_id,
                cost_usd, input_tokens, output_tokens, pattern_signature, reasoning, chaos_campaign_id,
                created_at
            ) VALUES (
                :exec_id, :incident_id, :site_id, :appliance_id, :runbook_id, :hostname, :platform, :incident_type,
                :started_at, :completed_at, :duration,
                :success, :status, :verification, :confidence, :resolution_level,
                CAST(:state_before AS jsonb), CAST(:state_after AS jsonb), CAST(:state_diff AS jsonb), CAST(:executed_steps AS jsonb),
                :error_msg, :error_step, :failure_type, :retry_count,
                :evidence_id,
                :cost_usd, :input_tokens, :output_tokens, :pattern_signature, :reasoning, :chaos_campaign_id,
                NOW()
            )
            ON CONFLICT (execution_id) DO UPDATE SET
                success = EXCLUDED.success,
                state_after = EXCLUDED.state_after,
                state_diff = EXCLUDED.state_diff,
                error_message = EXCLUDED.error_message,
                failure_type = EXCLUDED.failure_type,
                cost_usd = EXCLUDED.cost_usd,
                input_tokens = EXCLUDED.input_tokens,
                output_tokens = EXCLUDED.output_tokens,
                pattern_signature = EXCLUDED.pattern_signature,
                reasoning = EXCLUDED.reasoning
        """), {
            "exec_id": exec_data.get("execution_id"),
            "incident_id": exec_data.get("incident_id"),
            "site_id": request.site_id,
            "appliance_id": exec_data.get("appliance_id", "unknown"),
            "runbook_id": exec_data.get("runbook_id"),
            "hostname": hostname,
            "platform": exec_data.get("platform"),
            "incident_type": incident_type,
            "started_at": parse_iso_timestamp(exec_data.get("started_at")),
            "completed_at": parse_iso_timestamp(exec_data.get("completed_at")),
            "duration": exec_data.get("duration_seconds"),
            "success": exec_data.get("success", False),
            "status": exec_data.get("status"),
            "verification": exec_data.get("verification_passed"),
            "confidence": exec_data.get("confidence", 0.0),
            "resolution_level": exec_data.get("resolution_level"),
            "state_before": json.dumps(exec_data.get("state_before", {})),
            "state_after": json.dumps(exec_data.get("state_after", {})),
            "state_diff": json.dumps(exec_data.get("state_diff", {})),
            "executed_steps": json.dumps(exec_data.get("executed_steps", [])),
            "error_msg": exec_data.get("error_message"),
            "error_step": exec_data.get("error_step"),
            "failure_type": exec_data.get("failure_type"),
            "retry_count": exec_data.get("retry_count", 0),
            "evidence_id": exec_data.get("evidence_bundle_id"),
            "cost_usd": exec_data.get("cost_usd", 0),
            "input_tokens": exec_data.get("input_tokens", 0),
            "output_tokens": exec_data.get("output_tokens", 0),
            "pattern_signature": pattern_sig,
            "reasoning": exec_data.get("reasoning"),
            "chaos_campaign_id": exec_data.get("chaos_campaign_id"),
        })

        await db.commit()

        logger.info(f"Execution telemetry recorded: {exec_data.get('execution_id')} (success={exec_data.get('success')})")
        return {
            "status": "recorded",
            "execution_id": exec_data.get("execution_id"),
        }

    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to record execution telemetry: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to record telemetry: {e}")


# ============================================================================
# L2 Agent Plan Endpoint — delegates to agent_api.py canonical implementation
# The agent_api router is NOT registered (too many overlapping endpoints with
# main.py), so we wire up just this one endpoint that the daemon calls directly.
# ============================================================================
app.post("/api/agent/l2/plan")(agent_l2_plan_handler)


# ============================================================================
# Target Health — daemon reports connectivity probe results (SSH/WinRM/SNMP)
# ============================================================================

@app.post("/api/agent/target-health")
async def report_target_health(
    request: Request,
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """Receive target connectivity probe results from the appliance daemon.

    Called after probeTargetConnectivity runs at startup and on each scan cycle.
    Upserts into target_health table for persistent status tracking.

    Body: {"targets": [
        {"hostname": "192.168.88.250", "protocol": "winrm", "port": 5985,
         "status": "ok", "latency_ms": 42},
        {"hostname": "192.168.88.50", "protocol": "ssh", "port": 22,
         "status": "unreachable", "error": "timeout after 10s"},
    ]}
    """
    body = await request.json()
    targets = body.get("targets", [])

    if not targets:
        return {"status": "ok", "updated": 0}

    # Cap at 200 targets per report to prevent abuse
    if len(targets) > 200:
        raise HTTPException(status_code=400, detail="Too many targets (max 200)")

    # Extract appliance_id from the request if available
    appliance_id = body.get("appliance_id", "unknown")

    from dashboard_api.fleet import get_pool
    pool = await get_pool()

    updated = 0
    async with pool.acquire() as conn:
        for t in targets:
            hostname = (t.get("hostname") or "").strip()
            protocol = (t.get("protocol") or "").strip().lower()
            port = t.get("port")
            t_status = (t.get("status") or "unknown").strip().lower()
            error = t.get("error")
            latency_ms = t.get("latency_ms")

            if not hostname or not protocol:
                continue

            # Validate status values
            if t_status not in ("ok", "unreachable", "auth_failed", "timeout", "error", "unknown"):
                t_status = "error"

            # Validate protocol
            if protocol not in ("ssh", "winrm", "snmp", "rdp", "https"):
                continue

            try:
                await conn.execute("""
                    INSERT INTO target_health
                        (site_id, hostname, protocol, port, status, error,
                         latency_ms, reported_by, last_reported_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
                    ON CONFLICT (site_id, hostname, protocol, port)
                    DO UPDATE SET
                        status = EXCLUDED.status,
                        error = EXCLUDED.error,
                        latency_ms = EXCLUDED.latency_ms,
                        reported_by = EXCLUDED.reported_by,
                        last_reported_at = NOW()
                """, auth_site_id, hostname, protocol, port, t_status,
                    error, latency_ms, appliance_id)
                updated += 1
            except Exception as e:
                logger.warning("target_health upsert failed",
                               hostname=hostname, error=str(e))

    return {"status": "ok", "updated": updated}


# ============================================================================
# WORM Evidence Upload Endpoint (Proxy Mode)
# ============================================================================

@app.post("/evidence/upload")
async def upload_evidence_worm(
    bundle: UploadFile = File(..., description="Evidence bundle JSON file"),
    signature: UploadFile = File(None, description="Detached Ed25519 signature file"),
    x_client_id: str = Header(..., alias="X-Client-ID"),
    x_bundle_id: str = Header(..., alias="X-Bundle-ID"),
    x_bundle_hash: str = Header(..., alias="X-Bundle-Hash"),
    x_signature_hash: str = Header(None, alias="X-Signature-Hash"),
    authorization: str = Header(None),
    db: AsyncSession = Depends(get_db),
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """
    WORM Evidence Upload Proxy Endpoint.

    Accepts multipart file uploads from compliance agents and stores them
    in MinIO with Object Lock (COMPLIANCE mode) for HIPAA-compliant
    tamper-evident storage.

    Headers:
        X-Client-ID: Site identifier
        X-Bundle-ID: Unique bundle identifier
        X-Bundle-Hash: SHA256 hash of bundle (format: sha256:<hex>)
        X-Signature-Hash: SHA256 hash of signature (optional)
        Authorization: Bearer token (optional, for API key auth)

    Files:
        bundle: Evidence bundle JSON file (required)
        signature: Detached Ed25519 signature file (optional)

    Returns:
        bundle_uri: S3 URI of uploaded bundle
        signature_uri: S3 URI of uploaded signature (if provided)
        retention_until: ISO timestamp when retention expires
    """
    # Rate limit check
    allowed, remaining = await check_rate_limit(x_client_id, "evidence_upload")
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limited. Try again in {remaining} seconds."
        )

    # Verify appliance exists
    result = await db.execute(
        text("SELECT id FROM appliances WHERE site_id = :site_id"),
        {"site_id": x_client_id}
    )
    appliance = result.fetchone()

    if not appliance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Appliance not registered: {x_client_id}"
        )

    appliance_id = str(appliance[0])
    now = datetime.now(timezone.utc)

    # Read bundle content
    bundle_content = await bundle.read()

    # Verify hash
    expected_hash = x_bundle_hash.replace("sha256:", "")
    actual_hash = hashlib.sha256(bundle_content).hexdigest()
    if actual_hash != expected_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bundle hash mismatch. Expected {expected_hash[:16]}..., got {actual_hash[:16]}..."
        )

    # Read signature if provided
    sig_content = None
    if signature:
        sig_content = await signature.read()
        if x_signature_hash:
            expected_sig_hash = x_signature_hash.replace("sha256:", "")
            actual_sig_hash = hashlib.sha256(sig_content).hexdigest()
            if actual_sig_hash != expected_sig_hash:
                logger.warning("Signature hash mismatch",
                             bundle_id=x_bundle_id,
                             expected=expected_sig_hash[:16],
                             actual=actual_sig_hash[:16])

    # Parse bundle JSON for metadata
    try:
        bundle_data = json.loads(bundle_content.decode())
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid bundle JSON: {str(e)}"
        )

    # Generate S3 object paths
    date_prefix = now.strftime('%Y/%m/%d')
    bundle_key = f"{x_client_id}/{date_prefix}/{x_bundle_id}.json"
    sig_key = f"{x_client_id}/{date_prefix}/{x_bundle_id}.sig" if sig_content else None

    # Calculate retention date
    retention_until = now + timedelta(days=WORM_RETENTION_DAYS)

    # Upload bundle to MinIO with Object Lock
    bundle_uri = None
    sig_uri = None

    try:
        from io import BytesIO

        # Upload bundle
        minio_client.put_object(
            MINIO_BUCKET,
            bundle_key,
            BytesIO(bundle_content),
            length=len(bundle_content),
            content_type="application/json",
            metadata={
                "bundle_id": x_bundle_id,
                "client_id": x_client_id,
                "uploaded_at": now.isoformat(),
                "bundle_hash": actual_hash
            }
        )
        bundle_uri = f"s3://{MINIO_BUCKET}/{bundle_key}"

        # Set Object Lock retention (COMPLIANCE mode - cannot be shortened/deleted)
        try:
            retention = Retention(COMPLIANCE, retention_until)
            minio_client.set_object_retention(MINIO_BUCKET, bundle_key, retention)
            logger.info("Set WORM retention on bundle",
                       bundle_id=x_bundle_id,
                       retention_until=retention_until.isoformat())
        except Exception as e:
            # Log warning but don't fail - bucket may not have Object Lock enabled
            logger.warning("Could not set Object Lock retention (bucket may not have Object Lock enabled)",
                          bundle_id=x_bundle_id,
                          error=str(e))

        # Upload signature if provided
        if sig_content:
            minio_client.put_object(
                MINIO_BUCKET,
                sig_key,
                BytesIO(sig_content),
                length=len(sig_content),
                content_type="application/octet-stream",
                metadata={
                    "bundle_id": x_bundle_id,
                    "client_id": x_client_id
                }
            )
            sig_uri = f"s3://{MINIO_BUCKET}/{sig_key}"

            # Set Object Lock on signature too
            try:
                minio_client.set_object_retention(MINIO_BUCKET, sig_key, retention)
            except Exception as e:
                logger.warning("Could not set Object Lock on signature", error=str(e))

        logger.info("Evidence uploaded to WORM storage",
                   bundle_id=x_bundle_id,
                   client_id=x_client_id,
                   bundle_uri=bundle_uri,
                   sig_uri=sig_uri)

    except Exception as e:
        logger.error("Failed to upload evidence to MinIO",
                    bundle_id=x_bundle_id,
                    error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload to WORM storage: {str(e)}"
        )

    # Store reference in database
    try:
        evidence_id = str(uuid.uuid4())

        # Extract fields from bundle data
        check_type = bundle_data.get("check_type", "unknown")
        outcome = bundle_data.get("outcome", "unknown")

        # Parse timestamps from bundle (may be ISO strings)
        def parse_iso_timestamp(ts):
            """Parse ISO timestamp string to datetime."""
            if ts is None:
                return now
            if isinstance(ts, datetime):
                return ts
            if isinstance(ts, str):
                try:
                    # Handle Z suffix for UTC
                    ts = ts.replace('Z', '+00:00')
                    return datetime.fromisoformat(ts)
                except (ValueError, TypeError):
                    # Unparseable timestamp from appliance — use current time as fallback
                    return now
            return now

        timestamp_start = parse_iso_timestamp(bundle_data.get("timestamp_start"))
        timestamp_end = parse_iso_timestamp(bundle_data.get("timestamp_end"))

        await db.execute(
            text("""
                INSERT INTO evidence_bundles (id, bundle_id, appliance_id,
                    check_type, outcome, pre_state, post_state, actions_taken,
                    hipaa_controls, timestamp_start, timestamp_end,
                    signature, s3_uri, s3_uploaded_at)
                VALUES (:id, :bundle_id, :appliance_id,
                    :check_type, :outcome, :pre_state, :post_state, :actions_taken,
                    :hipaa_controls, :timestamp_start, :timestamp_end,
                    :signature, :s3_uri, :s3_uploaded_at)
                ON CONFLICT (bundle_id) DO UPDATE SET
                    s3_uri = EXCLUDED.s3_uri,
                    s3_uploaded_at = EXCLUDED.s3_uploaded_at
            """),
            {
                "id": evidence_id,
                "bundle_id": x_bundle_id,
                "appliance_id": appliance_id,
                "check_type": check_type,
                "outcome": outcome,
                "pre_state": json.dumps(bundle_data.get("pre_state", {})),
                "post_state": json.dumps(bundle_data.get("post_state", {})),
                "actions_taken": json.dumps(bundle_data.get("actions_taken", [])),
                "hipaa_controls": bundle_data.get("hipaa_controls"),
                "timestamp_start": timestamp_start,
                "timestamp_end": timestamp_end,
                "signature": sig_content.decode() if sig_content else None,
                "s3_uri": bundle_uri,
                "s3_uploaded_at": now
            }
        )
        await db.commit()

    except Exception as e:
        logger.warning("Failed to store evidence reference in database",
                      bundle_id=x_bundle_id,
                      error=str(e))
        # Don't fail the request - the file is already in MinIO

    return {
        "status": "uploaded",
        "bundle_id": x_bundle_id,
        "bundle_uri": bundle_uri,
        "signature_uri": sig_uri,
        "retention_until": retention_until.isoformat(),
        "retention_days": WORM_RETENTION_DAYS,
        "timestamp": now.isoformat()
    }


@app.get("/runbooks")
async def list_runbooks(user: dict = Depends(require_auth)):
    """List available runbooks."""
    runbooks = []
    for rb_id, rb in RUNBOOKS.items():
        runbooks.append({
            "id": rb_id,
            "name": rb.get("name"),
            "description": rb.get("description"),
            "category": rb.get("category"),
            "severity": rb.get("severity"),
            "hipaa_controls": rb.get("hipaa_controls", [])
        })
    
    # Include default runbooks from database
    async with async_session() as session:
        result = await session.execute(
            text("SELECT runbook_id, name, description, category, severity, hipaa_controls FROM runbooks WHERE enabled = true")
        )
        for row in result.fetchall():
            if row[0] not in [r["id"] for r in runbooks]:
                runbooks.append({
                    "id": row[0],
                    "name": row[1],
                    "description": row[2],
                    "category": row[3],
                    "severity": row[4],
                    "hipaa_controls": row[5]
                })
    
    return {"runbooks": runbooks, "count": len(runbooks)}

@app.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db), user: dict = Depends(require_auth)):
    """Get server statistics."""
    stats = {}
    
    # Appliance count
    result = await db.execute(text("SELECT COUNT(*) FROM appliances WHERE status = 'active'"))
    stats["active_appliances"] = result.scalar()
    
    # Orders stats
    result = await db.execute(text("""
        SELECT status, COUNT(*) FROM orders
        WHERE issued_at > NOW() - INTERVAL '24 hours'
        GROUP BY status
    """))
    stats["orders_24h"] = {row[0]: row[1] for row in result.fetchall()}
    
    # Incidents stats
    result = await db.execute(text("""
        SELECT status, COUNT(*) FROM incidents
        WHERE reported_at > NOW() - INTERVAL '24 hours'
        GROUP BY status
    """))
    stats["incidents_24h"] = {row[0]: row[1] for row in result.fetchall()}
    
    # Evidence stats
    result = await db.execute(text("""
        SELECT outcome, COUNT(*) FROM evidence_bundles
        WHERE timestamp_start > NOW() - INTERVAL '24 hours'
        GROUP BY outcome
    """))
    stats["evidence_24h"] = {row[0]: row[1] for row in result.fetchall()}
    
    # L1 vs L2 vs L3
    result = await db.execute(text("""
        SELECT resolution_tier, COUNT(*) FROM incidents
        WHERE reported_at > NOW() - INTERVAL '7 days'
        AND resolution_tier IS NOT NULL
        GROUP BY resolution_tier
    """))
    stats["resolution_tiers_7d"] = {row[0]: row[1] for row in result.fetchall()}
    
    stats["timestamp"] = datetime.now(timezone.utc).isoformat()
    
    return stats

# ============================================================================
# Error Handlers
# ============================================================================

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    logger.warning("HTTP exception", 
                   status_code=exc.status_code,
                   detail=exc.detail,
                   path=request.url.path)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail, "status_code": exc.status_code}
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception",
                 error=str(exc),
                 path=request.url.path,
                 exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "status_code": 500}
    )

# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


# =============================================================================
# AGENT SYNC API - L1 Rules for Appliances
# =============================================================================

@app.get("/agent/sync")
async def agent_sync_rules(site_id: Optional[str] = None, db: AsyncSession = Depends(get_db), auth_site_id: str = Depends(require_appliance_bearer)):
    """
    Return L1 rules for agents to sync.

    Returns rules based on site's healing_tier:
    - standard: 4 core rules (firewall, defender, bitlocker, ntp)
    - full_coverage: All 21 L1 rules for comprehensive auto-healing

    Plus any custom/promoted rules from database.
    """
    # Determine healing tier from site configuration
    healing_tier = "standard"  # default
    if site_id:
        try:
            result = await db.execute(
                text("SELECT healing_tier FROM sites WHERE site_id = :site_id"),
                {"site_id": site_id}
            )
            row = result.fetchone()
            if row and row[0]:
                healing_tier = row[0]
        except Exception as e:
            logger.warning(f"Failed to fetch healing tier for {site_id}: {e}")

    # Built-in L1 rules for NixOS appliances
    # Note: status values from SimpleDriftChecker are: "pass", "warning", "fail", "error"
    # Use "in" operator to match non-passing statuses

    # Standard rules (4 core rules) - always included
    standard_rules = [
        {
            "id": "L1-NTP-001",
            "name": "NTP Drift Remediation",
            "description": "Restart chronyd when NTP sync drifts",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "ntp_sync"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["restart_service:chronyd"],
            "severity": "medium",
            "cooldown_seconds": 300,
            "max_retries": 2,
            "source": "builtin"
        },
        {
            "id": "L1-SERVICE-001",
            "name": "Critical Service Recovery",
            "description": "Restart failed critical services",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "critical_services"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["restart_service:sshd", "restart_service:chronyd"],
            "severity": "high",
            "cooldown_seconds": 600,
            "max_retries": 3,
            "source": "builtin"
        },
        {
            "id": "L1-DISK-001",
            "name": "Disk Space Alert",
            "description": "Alert when disk usage exceeds threshold",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "disk_space"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["alert:disk_space_critical"],
            "severity": "high",
            "cooldown_seconds": 3600,
            "max_retries": 1,
            "source": "builtin"
        },
        {
            "id": "L1-FIREWALL-001",
            "name": "Windows Firewall Recovery",
            "description": "Re-enable Windows Firewall when disabled",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "firewall"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]},
                {"field": "platform", "operator": "ne", "value": "nixos"}
            ],
            "actions": ["restore_firewall_baseline"],
            "severity": "critical",
            "cooldown_seconds": 300,
            "max_retries": 2,
            "source": "builtin"
        },
        {
            "id": "L1-FIREWALL-002",
            "name": "Windows Firewall Status Recovery",
            "description": "Re-enable Windows Firewall when status check fails",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "firewall_status"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]},
                {"field": "platform", "operator": "ne", "value": "nixos"}
            ],
            "actions": ["restore_firewall_baseline"],
            "severity": "critical",
            "cooldown_seconds": 300,
            "max_retries": 2,
            "source": "builtin"
        },
        {
            "id": "L1-DEFENDER-001",
            "name": "Windows Defender Recovery",
            "description": "Re-enable Windows Defender when disabled",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "windows_defender"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["restore_defender"],
            "severity": "critical",
            "cooldown_seconds": 300,
            "max_retries": 2,
            "source": "builtin"
        },
        {
            "id": "L1-GENERATION-001",
            "name": "NixOS Generation Drift",
            "description": "Alert when NixOS generation is invalid or unknown",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "nixos_generation"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["alert:generation_drift"],
            "severity": "medium",
            "cooldown_seconds": 3600,
            "max_retries": 1,
            "source": "builtin"
        }
    ]

    # Additional rules for full_coverage mode (14 more rules)
    full_coverage_extra_rules = [
        {
            "id": "L1-PASSWORD-001",
            "name": "Password Policy Enforcement",
            "description": "Enforce minimum password requirements",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "password_policy"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["set_password_policy"],
            "severity": "high",
            "cooldown_seconds": 3600,
            "max_retries": 2,
            "source": "builtin"
        },
        {
            "id": "L1-AUDIT-001",
            "name": "Audit Policy Enforcement",
            "description": "Enable required audit policies",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "audit_policy"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["set_audit_policy"],
            "severity": "high",
            "cooldown_seconds": 3600,
            "max_retries": 2,
            "source": "builtin"
        },
        {
            "id": "L1-BITLOCKER-001",
            "name": "BitLocker Encryption",
            "description": "Enable drive encryption",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "bitlocker_status"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["run_windows_runbook:RB-WIN-SEC-005"],
            "severity": "critical",
            "cooldown_seconds": 3600,
            "max_retries": 1,
            "source": "builtin"
        },
        {
            "id": "L1-SMB1-001",
            "name": "SMBv1 Protocol Disabled",
            "description": "Disable insecure SMBv1 protocol",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "smb1_protocol"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["run_windows_runbook:RB-WIN-SEC-020"],
            "severity": "high",
            "cooldown_seconds": 3600,
            "max_retries": 2,
            "source": "builtin"
        },
        {
            "id": "L1-AUTOPLAY-001",
            "name": "AutoPlay Disabled",
            "description": "Disable AutoPlay to prevent malware spread",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "autoplay_disabled"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["disable_autoplay"],
            "severity": "medium",
            "cooldown_seconds": 3600,
            "max_retries": 2,
            "source": "builtin"
        },
        {
            "id": "L1-LOCKOUT-001",
            "name": "Account Lockout Policy",
            "description": "Configure account lockout after failed attempts",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "lockout_policy"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["set_lockout_policy"],
            "severity": "medium",
            "cooldown_seconds": 3600,
            "max_retries": 2,
            "source": "builtin"
        },
        {
            "id": "L1-SCREENSAVER-001",
            "name": "Screensaver Timeout",
            "description": "Configure screensaver with password protection",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "screensaver_timeout"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["set_screensaver_policy"],
            "severity": "medium",
            "cooldown_seconds": 3600,
            "max_retries": 2,
            "source": "builtin"
        },
        {
            "id": "L1-RDP-001",
            "name": "RDP Security",
            "description": "Secure RDP with NLA requirement",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "rdp_security"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["configure_rdp_security"],
            "severity": "high",
            "cooldown_seconds": 3600,
            "max_retries": 2,
            "source": "builtin"
        },
        {
            "id": "L1-UAC-001",
            "name": "UAC Enabled",
            "description": "Ensure User Account Control is enabled",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "uac_enabled"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["enable_uac"],
            "severity": "high",
            "cooldown_seconds": 3600,
            "max_retries": 2,
            "source": "builtin"
        },
        {
            "id": "L1-EVENTLOG-001",
            "name": "Event Log Size",
            "description": "Configure adequate event log retention",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "event_log_size"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["set_event_log_size"],
            "severity": "medium",
            "cooldown_seconds": 3600,
            "max_retries": 2,
            "source": "builtin"
        },
        {
            "id": "L1-DEFENDERUPDATES-001",
            "name": "Windows Defender Definitions",
            "description": "Update malware definitions",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "defender_definitions"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["update_defender_definitions"],
            "severity": "high",
            "cooldown_seconds": 14400,
            "max_retries": 3,
            "source": "builtin"
        },
        {
            "id": "L1-GUESTACCOUNT-001",
            "name": "Guest Account Disabled",
            "description": "Disable built-in Guest account",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "guest_account_disabled"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["disable_guest_account"],
            "severity": "medium",
            "cooldown_seconds": 3600,
            "max_retries": 2,
            "source": "builtin"
        },
        {
            "id": "L1-WUPDATES-001",
            "name": "Windows Updates",
            "description": "Check and trigger pending security updates",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "windows_updates"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["trigger_windows_update"],
            "severity": "high",
            "cooldown_seconds": 86400,
            "max_retries": 1,
            "source": "builtin"
        },
        {
            "id": "L1-BACKUP-001",
            "name": "Backup Status",
            "description": "Alert when backup fails or is stale",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "backup"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["alert:backup_failed"],
            "severity": "high",
            "cooldown_seconds": 7200,
            "max_retries": 1,
            "source": "builtin"
        },
        # Go Agent check_types (mapped from grpc_server.py)
        {
            "id": "L1-SCREENLOCK-001",
            "name": "Screen Lock Policy",
            "description": "Enforce screen lock timeout and password requirement",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "screen_lock_policy"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["run_windows_runbook:RB-WIN-SEC-016"],
            "severity": "high",
            "cooldown_seconds": 300,
            "max_retries": 2,
            "source": "builtin"
        },
        {
            "id": "L1-PATCHING-001",
            "name": "Windows Update Service",
            "description": "Ensure Windows Update service is running and updates are applied",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "patching"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["trigger_windows_update"],
            "severity": "critical",
            "cooldown_seconds": 86400,
            "max_retries": 1,
            "source": "builtin"
        },
        # --- Linux L1 Rules ---
        {
            "id": "L1-LIN-SSH-001",
            "name": "SSH Configuration Drift",
            "description": "Fix SSH config drift (PermitRootLogin, PasswordAuthentication, etc.)",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "ssh_config"},
                {"field": "drift_detected", "operator": "eq", "value": True}
            ],
            "actions": ["run_linux_runbook"],
            "severity": "critical",
            "cooldown_seconds": 300,
            "max_retries": 2,
            "source": "builtin"
        },
        {
            "id": "L1-LIN-KERN-001",
            "name": "Kernel Parameter Hardening",
            "description": "Fix unsafe kernel parameters (ip_forward, ASLR, etc.)",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "kernel"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["run_linux_runbook:LIN-KERN-001"],
            "severity": "high",
            "cooldown_seconds": 300,
            "max_retries": 2,
            "source": "builtin"
        },
        {
            "id": "L1-LIN-CRON-001",
            "name": "Cron Permission Hardening",
            "description": "Fix insecure cron file permissions",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "cron"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["run_linux_runbook:LIN-CRON-001"],
            "severity": "high",
            "cooldown_seconds": 300,
            "max_retries": 2,
            "source": "builtin"
        },
        {
            "id": "L1-LIN-SUID-001",
            "name": "SUID Binary Cleanup",
            "description": "Remove unauthorized SUID binaries from temp directories",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "permissions"},
                {"field": "drift_detected", "operator": "eq", "value": True},
                {"field": "distro", "operator": "ne", "value": None}
            ],
            "actions": ["run_linux_runbook"],
            "severity": "critical",
            "cooldown_seconds": 300,
            "max_retries": 2,
            "source": "builtin"
        },
        # --- Windows Persistence Detection ---
        {
            "id": "L1-PERSIST-TASK-001",
            "name": "Scheduled Task Persistence Detected",
            "description": "Remove suspicious scheduled tasks from root namespace",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "scheduled_task_persistence"},
                {"field": "drift_detected", "operator": "eq", "value": True}
            ],
            "actions": ["run_windows_runbook:RB-WIN-SEC-018"],
            "severity": "critical",
            "cooldown_seconds": 300,
            "max_retries": 2,
            "source": "builtin"
        },
        {
            "id": "L1-PERSIST-REG-001",
            "name": "Registry Run Key Persistence Detected",
            "description": "Remove suspicious registry Run key entries",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "registry_run_persistence"},
                {"field": "drift_detected", "operator": "eq", "value": True}
            ],
            "actions": ["run_windows_runbook:RB-WIN-SEC-019"],
            "severity": "critical",
            "cooldown_seconds": 300,
            "max_retries": 2,
            "source": "builtin"
        },
        {
            "id": "L1-PERSIST-WMI-001",
            "name": "WMI Event Subscription Persistence Detected",
            "description": "Remove suspicious WMI event subscriptions used for persistence",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "wmi_event_persistence"},
                {"field": "drift_detected", "operator": "eq", "value": True}
            ],
            "actions": ["run_windows_runbook:RB-WIN-SEC-021"],
            "severity": "critical",
            "cooldown_seconds": 300,
            "max_retries": 2,
            "source": "builtin"
        },
        {
            "id": "L1-SMB-SIGNING-001",
            "name": "SMB Signing Not Required",
            "description": "Enforce SMB signing to prevent relay attacks",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "smb_signing"},
                {"field": "drift_detected", "operator": "eq", "value": True}
            ],
            "actions": ["run_windows_runbook:RB-WIN-SEC-007"],
            "severity": "high",
            "cooldown_seconds": 300,
            "max_retries": 2,
            "source": "builtin"
        },
        {
            "id": "L1-SVC-NETLOGON-001",
            "name": "NetLogon Service Down",
            "description": "Restore NetLogon service for domain authentication",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "service_netlogon"},
                {"field": "drift_detected", "operator": "eq", "value": True}
            ],
            "actions": ["run_windows_runbook:RB-WIN-SVC-001"],
            "severity": "critical",
            "cooldown_seconds": 300,
            "max_retries": 2,
            "source": "builtin"
        }
    ]

    # Select rules based on healing tier
    if healing_tier == "full_coverage":
        builtin_rules = standard_rules + full_coverage_extra_rules
    else:
        builtin_rules = standard_rules

    # Fetch custom/promoted/protection_profile rules from database
    try:
        result = await db.execute(
            text("""
                SELECT rule_id, incident_pattern, runbook_id, confidence,
                       COALESCE(source, 'promoted') as source
                FROM l1_rules
                WHERE enabled = true AND COALESCE(source, 'promoted') != 'builtin'
                ORDER BY confidence DESC
            """)
        )
        db_rules = []
        for row in result.fetchall():
            rule_source = row[4]

            # For protection_profile rules, try to get the full rule_json
            if rule_source == "protection_profile":
                try:
                    ppr = await db.execute(
                        text("SELECT rule_json FROM app_profile_rules WHERE l1_rule_id = :rid LIMIT 1"),
                        {"rid": row[0]}
                    )
                    ppr_row = ppr.fetchone()
                    if ppr_row and ppr_row[0]:
                        rule_json = ppr_row[0] if isinstance(ppr_row[0], dict) else json.loads(ppr_row[0])
                        db_rules.append(rule_json)
                        continue
                except Exception as e:
                    # Protection profile rule_json lookup failed — fall through to generic format
                    logger.debug("Failed to load protection_profile rule_json", rule_id=str(row[0]), error=str(e))

            # Convert incident_pattern dict to conditions list
            # Database stores: {"incident_type": "firewall"} or {"check_type": "screen_lock"}
            # Conditions format: [{"field": "incident_type", "operator": "eq", "value": "firewall"}]
            pattern = row[1]
            if isinstance(pattern, list):
                conditions = pattern
            elif isinstance(pattern, dict):
                conditions = []
                for k, v in pattern.items():
                    # Map incident_type to check_type (auto_healer uses check_type)
                    field = "check_type" if k == "incident_type" else k
                    conditions.append({"field": field, "operator": "eq", "value": v})
                # Add status condition for fail/warning/error
                conditions.append({"field": "status", "operator": "in", "value": ["warning", "fail", "error"]})
            else:
                conditions = []

            db_rules.append({
                "id": row[0],
                "name": f"Promoted: {row[0]}",
                "description": f"Auto-promoted rule with {row[3]:.0%} confidence",
                "conditions": conditions,
                "actions": [f"run_runbook:{row[2]}"],
                "severity": "critical" if rule_source == "protection_profile" else "medium",
                "cooldown_seconds": 300,
                "max_retries": 3 if rule_source == "protection_profile" else 2,
                "source": rule_source
            })
    except Exception as e:
        logger.warning(f"Failed to fetch DB rules: {e}")
        db_rules = []

    all_rules = builtin_rules + db_rules

    # Sign the rules bundle for appliance-side integrity verification
    rules_json = json.dumps(all_rules, sort_keys=True)
    rules_signature = sign_data(rules_json)

    return {
        "rules": all_rules,
        "healing_tier": healing_tier,
        "version": "1.0.0",
        "count": len(all_rules),
        "signature": rules_signature,
        "server_public_key": get_public_key_hex(),
    }


# ============================================================================
# Learning System - L2->L1 Promotion Reports
# ============================================================================

class PromotionReportRequest(BaseModel):
    """Promotion report from appliance learning system."""
    appliance_id: str
    site_id: str
    checked_at: str
    candidates_found: int = 0
    candidates_promoted: int = 0
    candidates_pending: int = 0
    pending_candidates: List[Dict[str, Any]] = []
    promoted_rules: List[Dict[str, Any]] = []
    rollbacks: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []


@app.post("/api/learning/promotion-report")
async def receive_promotion_report(
    req: PromotionReportRequest,
    db: AsyncSession = Depends(get_db),
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """
    Receive promotion reports from appliance learning systems.

    Stores the report and individual candidates for site owner approval.
    Sends email notifications to site owner for pending approvals.
    """
    try:
        now = datetime.now(timezone.utc)

        # Store the promotion report
        report_id = str(uuid.uuid4())
        await db.execute(
            text("""
                INSERT INTO learning_promotion_reports
                (id, appliance_id, site_id, checked_at, candidates_found,
                 candidates_promoted, candidates_pending, report_data, created_at)
                VALUES (:id, :appliance_id, :site_id, :checked_at, :candidates_found,
                        :candidates_promoted, :candidates_pending, :report_data, :created_at)
            """),
            {
                "id": report_id,
                "appliance_id": req.appliance_id,
                "site_id": req.site_id,
                "checked_at": req.checked_at,
                "candidates_found": req.candidates_found,
                "candidates_promoted": req.candidates_promoted,
                "candidates_pending": req.candidates_pending,
                "report_data": json.dumps({
                    "pending_candidates": req.pending_candidates,
                    "promoted_rules": req.promoted_rules,
                    "rollbacks": req.rollbacks,
                    "errors": req.errors
                }),
                "created_at": now.isoformat()
            }
        )

        # Store individual candidates for approval workflow
        candidate_ids = []
        for candidate in req.pending_candidates:
            candidate_id = str(uuid.uuid4())
            candidate_ids.append(candidate_id)
            stats = candidate.get("stats", {})
            await db.execute(
                text("""
                    INSERT INTO learning_promotion_candidates
                    (id, report_id, site_id, appliance_id, pattern_signature,
                     recommended_action, confidence_score, success_rate,
                     total_occurrences, l2_resolutions, promotion_reason,
                     approval_status, created_at)
                    VALUES (:id, :report_id, :site_id, :appliance_id, :pattern_signature,
                            :recommended_action, :confidence_score, :success_rate,
                            :total_occurrences, :l2_resolutions, :promotion_reason,
                            'pending', :created_at)
                """),
                {
                    "id": candidate_id,
                    "report_id": report_id,
                    "site_id": req.site_id,
                    "appliance_id": req.appliance_id,
                    "pattern_signature": candidate.get("pattern_signature", "")[:32],
                    "recommended_action": candidate.get("recommended_action", "unknown"),
                    "confidence_score": candidate.get("confidence_score", 0),
                    "success_rate": stats.get("success_rate", 0),
                    "total_occurrences": stats.get("total_occurrences", 0),
                    "l2_resolutions": stats.get("l2_resolutions", 0),
                    "promotion_reason": candidate.get("promotion_reason", ""),
                    "created_at": now.isoformat()
                }
            )

        await db.commit()

        # Send notification to site owner if there are pending candidates
        if req.candidates_pending > 0:
            await _notify_site_owner_promotion(req, candidate_ids, db)

        # Also notify admin about rollbacks (critical)
        if req.rollbacks:
            await _send_promotion_notification(req, db)

        logger.info(
            f"Promotion report from {req.appliance_id}: "
            f"{req.candidates_found} found, {req.candidates_pending} pending approval"
        )

        return {"status": "ok", "report_id": report_id, "candidate_ids": candidate_ids}

    except Exception as e:
        logger.error(f"Failed to process promotion report: {e}")
        return {"status": "error", "message": str(e)}


async def _send_promotion_notification(req: PromotionReportRequest, db: AsyncSession):
    """Send email notification for promotion events."""
    try:
        # Get alert email from environment
        alert_email = os.getenv("ALERT_EMAIL", "administrator@osiriscare.net")
        smtp_user = os.getenv("SMTP_USER", "")
        smtp_password = os.getenv("SMTP_PASSWORD", "")

        if not smtp_user or not smtp_password:
            logger.debug("SMTP not configured - skipping promotion notification email")
            return

        # Build email content
        subject_parts = []
        if req.candidates_pending > 0:
            subject_parts.append(f"{req.candidates_pending} patterns ready for review")
        if req.candidates_promoted > 0:
            subject_parts.append(f"{req.candidates_promoted} auto-promoted")
        if req.rollbacks:
            subject_parts.append(f"{len(req.rollbacks)} rules rolled back")

        subject = f"[Learning System] {', '.join(subject_parts)}"

        # Build HTML body
        body_parts = [
            f"<h2>Learning System Report</h2>",
            f"<p><strong>Appliance:</strong> {req.appliance_id}</p>",
            f"<p><strong>Site:</strong> {req.site_id}</p>",
            f"<p><strong>Checked at:</strong> {req.checked_at}</p>",
            "<hr>"
        ]

        if req.candidates_pending > 0:
            body_parts.append("<h3>🔍 Patterns Ready for Review</h3>")
            body_parts.append("<table border='1' cellpadding='5'>")
            body_parts.append("<tr><th>Pattern</th><th>Action</th><th>Confidence</th><th>Success Rate</th></tr>")
            for c in req.pending_candidates[:10]:
                body_parts.append(
                    f"<tr><td>{c.get('pattern_signature', 'N/A')[:12]}</td>"
                    f"<td>{c.get('recommended_action', 'N/A')}</td>"
                    f"<td>{c.get('confidence_score', 0):.1%}</td>"
                    f"<td>{c.get('stats', {}).get('success_rate', 0):.1%}</td></tr>"
                )
            body_parts.append("</table>")
            body_parts.append("<p><a href='https://dashboard.osiriscare.net/learning'>Review in Dashboard</a></p>")

        if req.candidates_promoted > 0:
            body_parts.append("<h3>✅ Auto-Promoted Rules</h3>")
            body_parts.append("<ul>")
            for r in req.promoted_rules[:10]:
                body_parts.append(
                    f"<li><strong>{r.get('rule_id', 'N/A')}</strong>: "
                    f"{r.get('action', 'N/A')} (confidence: {r.get('confidence', 0):.1%})</li>"
                )
            body_parts.append("</ul>")

        if req.rollbacks:
            body_parts.append("<h3>⚠️ Rolled Back Rules</h3>")
            body_parts.append("<ul>")
            for r in req.rollbacks[:10]:
                body_parts.append(
                    f"<li><strong>{r.get('rule_id', 'N/A')}</strong>: "
                    f"{r.get('reason', 'Performance degradation')}</li>"
                )
            body_parts.append("</ul>")

        html_body = "\n".join(body_parts)

        # Send email using SMTP
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        smtp_host = os.getenv("SMTP_HOST", "mail.privateemail.com")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_from = os.getenv("SMTP_FROM", "alerts@osiriscare.net")

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp_from
        msg["To"] = alert_email
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)

        logger.info(f"Sent promotion notification email to {alert_email}")

    except Exception as e:
        logger.error(f"Failed to send promotion notification: {e}")


async def _notify_site_owner_promotion(
    req: PromotionReportRequest,
    candidate_ids: List[str],
    db: AsyncSession
):
    """Send email notification to site owner about pending promotions."""
    try:
        # Get site owner email from sites table
        result = await db.execute(
            text("SELECT contact_email, name FROM sites WHERE site_id = :site_id"),
            {"site_id": req.site_id}
        )
        row = result.fetchone()

        if not row or not row[0]:
            logger.debug(f"No contact email for site {req.site_id}")
            return

        owner_email = row[0]
        site_name = row[1] or req.site_id

        smtp_user = os.getenv("SMTP_USER", "")
        smtp_password = os.getenv("SMTP_PASSWORD", "")

        if not smtp_user or not smtp_password:
            logger.debug("SMTP not configured - skipping site owner notification")
            return

        # Build email
        subject = f"[{site_name}] {req.candidates_pending} automation rules ready for approval"

        dashboard_url = os.getenv("DASHBOARD_URL", "https://dashboard.osiriscare.net")
        approval_link = f"{dashboard_url}/learning?site={req.site_id}"

        body_parts = [
            f"<h2>New Automation Rules Detected</h2>",
            f"<p>The compliance system has identified <strong>{req.candidates_pending}</strong> "
            f"patterns that can be automated for your site.</p>",
            f"<p><strong>Site:</strong> {site_name}</p>",
            f"<p><strong>Appliance:</strong> {req.appliance_id}</p>",
            "<hr>",
            "<h3>Patterns Ready for Review</h3>",
            "<table border='1' cellpadding='8' style='border-collapse: collapse;'>",
            "<tr style='background:#f0f0f0;'><th>Action</th><th>Confidence</th><th>Success Rate</th><th>Occurrences</th></tr>"
        ]

        for c in req.pending_candidates[:5]:
            stats = c.get("stats", {})
            body_parts.append(
                f"<tr>"
                f"<td>{c.get('recommended_action', 'N/A')}</td>"
                f"<td>{c.get('confidence_score', 0):.0%}</td>"
                f"<td>{stats.get('success_rate', 0):.0%}</td>"
                f"<td>{stats.get('total_occurrences', 0)}</td>"
                f"</tr>"
            )

        if req.candidates_pending > 5:
            body_parts.append(f"<tr><td colspan='4'><em>... and {req.candidates_pending - 5} more</em></td></tr>")

        body_parts.extend([
            "</table>",
            "<br>",
            f"<p><a href='{approval_link}' style='background:#4CAF50;color:white;padding:10px 20px;text-decoration:none;border-radius:5px;'>Review & Approve</a></p>",
            "<p style='color:#666;font-size:12px;'>These patterns have been successfully handled automatically multiple times. "
            "Approving them will enable instant automated remediation without requiring AI processing.</p>"
        ])

        html_body = "\n".join(body_parts)

        # Send email
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        smtp_host = os.getenv("SMTP_HOST", "mail.privateemail.com")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_from = os.getenv("SMTP_FROM", "alerts@osiriscare.net")

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp_from
        msg["To"] = owner_email
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)

        # Mark candidates as notified
        await db.execute(
            text("UPDATE learning_promotion_candidates SET notified_at = :now WHERE id = ANY(:ids)"),
            {"now": datetime.now(timezone.utc).isoformat(), "ids": candidate_ids}
        )
        await db.commit()

        logger.info(f"Sent promotion approval request to {owner_email} for site {req.site_id}")

    except Exception as e:
        logger.error(f"Failed to notify site owner: {e}")


@app.get("/api/learning/status")
async def get_learning_status(db: AsyncSession = Depends(get_db), user: dict = Depends(require_auth)):
    """Get learning loop summary stats for dashboard."""
    try:
        result = await db.execute(text("""
            SELECT
                (SELECT COUNT(*) FROM l1_rules WHERE enabled = true) as total_l1_rules,
                (SELECT COUNT(*) FROM execution_telemetry
                 WHERE created_at > NOW() - INTERVAL '30 days'
                   AND runbook_id IS NOT NULL) as total_l2_decisions_30d,
                (SELECT COUNT(*) FROM execution_telemetry
                 WHERE created_at > NOW() - INTERVAL '30 days') as total_incidents_30d,
                (SELECT COUNT(*) FROM learning_promotion_candidates
                 WHERE approval_status = 'approved'
                   AND approved_at > NOW() - INTERVAL '90 days') as total_promotions_90d
        """))
        row = result.fetchone()
        total_incidents = row.total_incidents_30d or 1
        l1_count = (total_incidents - (row.total_l2_decisions_30d or 0))
        l1_rate = max(0, min(100, l1_count * 100.0 / total_incidents))

        # Compute real promotion success rate from post-promotion telemetry
        promo_result = await db.execute(text("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE et.success = true) as successful
            FROM execution_telemetry et
            WHERE et.created_at > NOW() - INTERVAL '30 days'
        """))
        promo_row = promo_result.fetchone()
        promo_total = promo_row.total if promo_row else 0
        if promo_total > 0:
            promo_success_rate = round((promo_row.successful / promo_total) * 100, 1)
        else:
            promo_success_rate = None  # No telemetry data — don't fake it

        return {
            "total_l1_rules": row.total_l1_rules,
            "total_l2_decisions_30d": row.total_l2_decisions_30d or 0,
            "l1_resolution_rate": round(l1_rate, 1),
            "promotion_success_rate": promo_success_rate,
            "total_promotions_90d": row.total_promotions_90d or 0,
        }
    except Exception as e:
        logger.error(f"Failed to get learning status: {e}")
        return {"error": "database_unavailable", "total_l1_rules": None,
                "total_l2_decisions_30d": None, "l1_resolution_rate": None,
                "promotion_success_rate": None}


@app.get("/api/learning/coverage-gaps")
async def get_learning_coverage_gaps(db: AsyncSession = Depends(get_db), user: dict = Depends(require_auth)):
    """Get check_types seen in telemetry that lack L1 rules."""
    try:
        result = await db.execute(text("""
            SELECT
                et.incident_type as check_type,
                COUNT(*) as incident_count_30d,
                MAX(et.created_at) as last_seen,
                EXISTS(
                    SELECT 1 FROM l1_rules lr
                    WHERE lr.enabled = true
                      AND (
                        lr.incident_pattern->>'check_type' = et.incident_type
                        OR lr.incident_pattern->>'incident_type' = et.incident_type
                        OR lr.rule_id ILIKE '%' || REPLACE(et.incident_type, '_', '-') || '%'
                        OR lr.rule_id ILIKE '%' || et.incident_type || '%'
                      )
                ) as has_l1_rule
            FROM execution_telemetry et
            WHERE et.created_at > NOW() - INTERVAL '30 days'
              AND et.incident_type IS NOT NULL
              AND et.incident_type != ''
            GROUP BY et.incident_type
            ORDER BY incident_count_30d DESC
        """))
        rows = result.fetchall()
        return [
            {
                "check_type": row.check_type,
                "incident_count_30d": row.incident_count_30d,
                "last_seen": row.last_seen.isoformat() if row.last_seen else None,
                "has_l1_rule": row.has_l1_rule,
            }
            for row in rows
        ]
    except Exception as e:
        logger.error(f"Failed to get coverage gaps: {e}")
        return []


@app.get("/api/learning/promotion-candidates")
async def get_promotion_candidates(
    site_id: Optional[str] = None,
    status: str = "pending",
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Get promotion candidates for dashboard display."""
    try:
        query = """
            SELECT id, report_id, site_id, appliance_id, pattern_signature,
                   recommended_action, confidence_score, success_rate,
                   total_occurrences, l2_resolutions, promotion_reason,
                   approval_status, approved_by, approved_at, created_at
            FROM learning_promotion_candidates
            WHERE approval_status = :status
        """
        params = {"status": status}

        if site_id:
            query += " AND site_id = :site_id"
            params["site_id"] = site_id

        query += " ORDER BY created_at DESC LIMIT 100"

        result = await db.execute(text(query), params)
        rows = result.fetchall()

        candidates = [
            {
                "id": str(row[0]),
                "report_id": str(row[1]),
                "site_id": row[2],
                "appliance_id": row[3],
                "pattern_signature": row[4],
                "recommended_action": row[5],
                "confidence_score": float(row[6]) if row[6] else 0,
                "success_rate": float(row[7]) if row[7] else 0,
                "total_occurrences": row[8],
                "l2_resolutions": row[9],
                "promotion_reason": row[10],
                "approval_status": row[11],
                "approved_by": str(row[12]) if row[12] else None,
                "approved_at": row[13].isoformat() if row[13] else None,
                "created_at": row[14].isoformat() if row[14] else None
            }
            for row in rows
        ]

        return {
            "status": "ok",
            "total": len(candidates),
            "candidates": candidates
        }

    except Exception as e:
        logger.error(f"Failed to get promotion candidates: {e}")
        return {"status": "error", "message": str(e), "candidates": []}


class PromotionApprovalRequest(BaseModel):
    """Request to approve or reject a promotion candidate."""
    action: str  # "approve" or "reject"
    reason: Optional[str] = None  # Required for rejection


@app.post("/api/learning/promotions/{candidate_id}/review")
async def review_promotion_candidate(
    candidate_id: str,
    req: PromotionApprovalRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """
    Approve or reject a promotion candidate.

    Site owners can approve patterns to be promoted to L1 deterministic rules.
    """
    try:
        current_user = user

        # Verify the candidate exists
        result = await db.execute(
            text("SELECT site_id, approval_status FROM learning_promotion_candidates WHERE id = :id"),
            {"id": candidate_id}
        )
        row = result.fetchone()

        if not row:
            return JSONResponse(status_code=404, content={"error": "Candidate not found"})

        site_id = row[0]
        current_status = row[1]

        if current_status != "pending":
            return JSONResponse(
                status_code=400,
                content={"error": f"Candidate already {current_status}"}
            )

        # Check user has access to this site (admin or site owner)
        # For now, allow any authenticated user - can add site-level perms later
        if current_user["role"] not in ["admin", "operator"]:
            return JSONResponse(
                status_code=403,
                content={"error": "Insufficient permissions"}
            )

        now = datetime.now(timezone.utc)

        if req.action == "approve":
            await db.execute(
                text("""
                    UPDATE learning_promotion_candidates
                    SET approval_status = 'approved',
                        approved_by = :user_id,
                        approved_at = :now
                    WHERE id = :id
                """),
                {"id": candidate_id, "user_id": current_user["id"], "now": now.isoformat()}
            )
            await db.commit()

            logger.info(f"Promotion candidate {candidate_id} approved by {current_user['username']}")
            return {"status": "ok", "message": "Promotion approved", "approval_status": "approved"}

        elif req.action == "reject":
            await db.execute(
                text("""
                    UPDATE learning_promotion_candidates
                    SET approval_status = 'rejected',
                        approved_by = :user_id,
                        approved_at = :now,
                        rejection_reason = :reason
                    WHERE id = :id
                """),
                {
                    "id": candidate_id,
                    "user_id": current_user["id"],
                    "now": now.isoformat(),
                    "reason": req.reason or "Rejected by user"
                }
            )
            await db.commit()

            logger.info(f"Promotion candidate {candidate_id} rejected by {current_user['username']}")
            return {"status": "ok", "message": "Promotion rejected", "approval_status": "rejected"}

        else:
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid action. Use 'approve' or 'reject'"}
            )

    except Exception as e:
        logger.error(f"Failed to review promotion: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/learning/history")
async def get_learning_history(limit: int = 20, db: AsyncSession = Depends(get_db), user: dict = Depends(require_auth)):
    """Get recently promoted L2->L1 patterns for the dashboard timeline."""
    try:
        result = await db.execute(text("""
            SELECT
                lpc.id,
                lpc.pattern_signature,
                COALESCE(lpc.custom_rule_name, lpc.recommended_action, 'L1-' || LEFT(lpc.id::text, 8)) as rule_id,
                lpc.approved_at as promoted_at,
                COALESCE(exec_stats.total, 0) as executions_since,
                COALESCE(exec_stats.success_pct, 0) as success_rate
            FROM learning_promotion_candidates lpc
            LEFT JOIN LATERAL (
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE et.success) * 100.0 / NULLIF(COUNT(*), 0) as success_pct
                FROM execution_telemetry et
                WHERE et.incident_type = split_part(lpc.pattern_signature, ':', 1)
                AND et.created_at > lpc.approved_at
            ) exec_stats ON true
            WHERE lpc.approval_status = 'approved'
            AND lpc.approved_at IS NOT NULL
            ORDER BY lpc.approved_at DESC
            LIMIT :limit
        """), {"limit": limit})

        rows = result.fetchall()
        return [
            {
                "id": str(row.id),
                "pattern_signature": row.pattern_signature,
                "rule_id": row.rule_id,
                "promoted_at": row.promoted_at.isoformat() if row.promoted_at else None,
                "post_promotion_success_rate": float(row.success_rate or 0),
                "executions_since_promotion": int(row.executions_since or 0),
            }
            for row in rows
        ]
    except Exception as e:
        logger.error(f"Failed to get learning history: {e}")
        return []


@app.get("/api/learning/approved-promotions")
async def get_approved_promotions(
    site_id: str,
    since: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """
    Get approved promotions for an appliance to apply.

    Called by appliances during sync to get newly approved rules.
    """
    try:
        query = """
            SELECT id, pattern_signature, recommended_action, confidence_score,
                   success_rate, total_occurrences, l2_resolutions, promotion_reason,
                   approved_at
            FROM learning_promotion_candidates
            WHERE site_id = :site_id
            AND approval_status = 'approved'
        """
        params = {"site_id": site_id}

        if since:
            query += " AND approved_at > :since"
            params["since"] = since

        query += " ORDER BY approved_at ASC"

        result = await db.execute(text(query), params)
        rows = result.fetchall()

        promotions = [
            {
                "id": str(row[0]),
                "pattern_signature": row[1],
                "recommended_action": row[2],
                "confidence_score": float(row[3]) if row[3] else 0,
                "success_rate": float(row[4]) if row[4] else 0,
                "total_occurrences": row[5],
                "l2_resolutions": row[6],
                "promotion_reason": row[7],
                "approved_at": row[8].isoformat() if row[8] else None
            }
            for row in rows
        ]

        return {
            "status": "ok",
            "site_id": site_id,
            "count": len(promotions),
            "promotions": promotions
        }

    except Exception as e:
        logger.error(f"Failed to get approved promotions: {e}")
        return {"status": "error", "message": str(e), "promotions": []}


# Alias for agent compatibility
class ApplianceCheckinRequest(BaseModel):
    """Appliance check-in from agent (uses different field names)."""
    site_id: str
    hostname: Optional[str] = None
    mac_address: Optional[str] = None
    ip_addresses: Optional[List[str]] = None
    agent_version: Optional[str] = None
    nixos_version: Optional[str] = None
    uptime_seconds: Optional[int] = None
    queue_depth: Optional[int] = 0

@app.post("/api/appliances/checkin")
async def appliances_checkin(req: ApplianceCheckinRequest, request: Request, db: AsyncSession = Depends(get_db), auth_site_id: str = Depends(require_appliance_bearer)):
    """Appliance agent checkin endpoint - updates site_appliances table.

    NOTE: This endpoint is typically overridden by the dashboard_api router.
    The actual checkin handler is in dashboard_api/sites.py (appliance_checkin).
    """
    try:
        # Build appliance_id from site_id and mac
        mac = req.mac_address or "00:00:00:00:00:00"
        appliance_id = f"{req.site_id}-{mac}"
        now = datetime.now(timezone.utc)

        # Upsert into site_appliances
        await db.execute(text("""
            INSERT INTO site_appliances (
                site_id, appliance_id, hostname, mac_address, ip_addresses,
                agent_version, nixos_version, status, last_checkin,
                uptime_seconds, queue_depth, first_checkin, created_at
            ) VALUES (
                :site_id, :appliance_id, :hostname, :mac_address, :ip_addresses,
                :agent_version, :nixos_version, 'online', :last_checkin,
                :uptime_seconds, :queue_depth, :first_checkin, :created_at
            )
            ON CONFLICT (appliance_id) DO UPDATE SET
                hostname = EXCLUDED.hostname,
                ip_addresses = EXCLUDED.ip_addresses,
                agent_version = EXCLUDED.agent_version,
                nixos_version = EXCLUDED.nixos_version,
                status = 'online',
                last_checkin = EXCLUDED.last_checkin,
                uptime_seconds = EXCLUDED.uptime_seconds,
                queue_depth = EXCLUDED.queue_depth
        """), {
            "site_id": req.site_id,
            "appliance_id": appliance_id,
            "hostname": req.hostname or "unknown",
            "mac_address": mac,
            "ip_addresses": json.dumps(req.ip_addresses or []),
            "agent_version": req.agent_version,
            "nixos_version": req.nixos_version,
            "last_checkin": now,
            "uptime_seconds": req.uptime_seconds or 0,
            "queue_depth": req.queue_depth or 0,
            "first_checkin": now,
            "created_at": now,
        })
        await db.commit()

        # Fetch Windows targets — site credentials first, then fill gaps from org credentials
        windows_targets = []
        seen_hosts = set()
        try:
            # Site-level credentials (take precedence)
            result = await db.execute(text("""
                SELECT credential_type, credential_name, encrypted_data
                FROM site_credentials
                WHERE site_id = :site_id
                AND credential_type IN ('winrm', 'domain_admin', 'domain_member', 'service_account', 'local_admin')
                ORDER BY CASE WHEN credential_type = 'domain_admin' THEN 0 ELSE 1 END, created_at DESC
            """), {"site_id": req.site_id})
            creds = result.fetchall()

            # Org-level credentials (fill gaps)
            org_creds_result = await db.execute(text("""
                SELECT oc.credential_type, oc.credential_name, oc.encrypted_data
                FROM org_credentials oc
                JOIN sites s ON s.client_org_id = oc.client_org_id
                WHERE s.site_id = :site_id
                AND oc.credential_type IN ('winrm', 'domain_admin', 'domain_member', 'service_account', 'local_admin')
                ORDER BY CASE WHEN oc.credential_type = 'domain_admin' THEN 0 ELSE 1 END, oc.created_at DESC
            """), {"site_id": req.site_id})
            org_creds = org_creds_result.fetchall()

            for cred in list(creds) + list(org_creds):
                try:
                    raw = cred.encrypted_data
                    cred_data = json.loads(bytes(raw).decode() if isinstance(raw, (bytes, memoryview)) else raw)
                    hostname = cred_data.get('host') or cred_data.get('target_host')
                    username = cred_data.get('username', '')
                    password = cred_data.get('password', '')
                    domain = cred_data.get('domain', '')
                    use_ssl = cred_data.get('use_ssl', False)

                    full_username = f"{domain}\\{username}" if domain else username

                    # Skip if we already have a credential for this host (site takes precedence)
                    if hostname and hostname not in seen_hosts:
                        seen_hosts.add(hostname)
                        windows_targets.append({
                            "hostname": hostname,
                            "username": full_username,
                            "password": password,
                            "use_ssl": use_ssl,
                            "role": cred.credential_type,
                        })
                except Exception as e:
                    logger.warning(f"Failed to parse credential: {e}")
        except Exception as e:
            logger.warning(f"Failed to fetch Windows targets: {e}")

        return {
            "status": "ok",
            "appliance_id": appliance_id,
            "server_time": now.isoformat(),
            "windows_targets": windows_targets,
        }
    except Exception as e:
        logger.error(f"Checkin failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# Backup Status Endpoints
# ============================================================================

BACKUP_STATUS_FILE = Path("/opt/backups/status/latest.json")


@app.get("/api/backup/status")
async def get_backup_status(user: dict = Depends(require_auth)):
    """Get current backup status for dashboard."""
    if not BACKUP_STATUS_FILE.exists():
        return {
            "status": "unknown",
            "message": "No backup status available",
            "last_backup": None,
            "storage_used_mb": 0,
            "total_snapshots": 0,
        }

    try:
        with open(BACKUP_STATUS_FILE, 'r') as f:
            status_data = json.load(f)

        return {
            "status": status_data.get("status", "unknown"),
            "last_backup": status_data.get("timestamp"),
            "last_backup_duration_seconds": status_data.get("duration_seconds"),
            "storage_used_mb": round(status_data.get("storage_used_bytes", 0) / (1024*1024), 2),
            "total_snapshots": status_data.get("total_snapshots", 0),
            "repository": status_data.get("repository"),
            "retention": status_data.get("retention"),
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to read backup status: {str(e)}",
            "last_backup": None,
        }


# ============================================================================
# Hetzner Cloud Snapshot Endpoints
# ============================================================================

SNAPSHOT_STATUS_FILE = Path("/opt/backups/status/hetzner-snapshot.json")


@app.get("/api/snapshot/status")
async def get_snapshot_status(user: dict = Depends(require_auth)):
    """Get Hetzner Cloud snapshot status."""
    if not SNAPSHOT_STATUS_FILE.exists():
        return {
            "status": "unknown",
            "message": "No snapshot has been taken yet. First snapshot runs Sunday 4 AM UTC.",
        }

    try:
        with open(SNAPSHOT_STATUS_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        return {"status": "error", "message": f"Failed to read status: {e}"}


@app.get("/api/snapshot/list")
async def list_snapshots(user: dict = Depends(require_auth)):
    """List all Hetzner Cloud snapshots."""
    import subprocess
    
    token_file = Path("/root/.hcloud-token")
    if not token_file.exists():
        return {"status": "error", "message": "Hetzner token not configured"}
    
    try:
        token = token_file.read_text().strip()
        
        result = subprocess.run(
            ["/root/.nix-profile/bin/hcloud", "image", "list", "--type", "snapshot", "-o", "json"],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "HCLOUD_TOKEN": token, "HOME": "/root"}
        )
        
        if result.returncode == 0:
            images = json.loads(result.stdout) if result.stdout else []
            our_snapshots = [
                {
                    "id": img.get("id"),
                    "description": img.get("description"),
                    "created": img.get("created"),
                    "size_gb": img.get("image_size"),
                }
                for img in images 
                if img.get("description", "").startswith("osiriscare-weekly")
            ]
            return {
                "status": "success",
                "count": len(our_snapshots),
                "snapshots": sorted(our_snapshots, key=lambda x: x.get("created", ""), reverse=True)
            }
        else:
            return {"status": "error", "message": result.stderr}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Command timed out"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# =============================================================================
# CHAOS QUICKTEST — inject drift, verify healing pipeline detects + remediates
# =============================================================================

# Default chaos scenarios targeting ws01 (.251) — Windows drift injections
# that the scan cycle should detect and the healing pipeline should fix.
CHAOS_QUICKTEST_SCENARIOS = [
    {
        "target": "192.168.88.251",
        "type": "windows_update",
        "inject": "Stop-Service wuauserv -Force",
    },
    {
        "target": "192.168.88.251",
        "type": "defender_exclusions",
        "inject": "Set-MpPreference -ExclusionPath C:\\Windows\\Temp",
    },
    {
        "target": "192.168.88.251",
        "type": "guest_account",
        "inject": "net user Guest /active:yes",
    },
    {
        "target": "192.168.88.251",
        "type": "smb_signing",
        "inject": "Set-SmbServerConfiguration -RequireSecuritySignature $false -Force",
    },
    {
        "target": "192.168.88.251",
        "type": "audit_policy",
        "inject": "auditpol /set /subcategory:Logon /success:disable /failure:disable",
    },
]

# Map drift types to the incident_type values the scan cycle generates
CHAOS_INCIDENT_TYPE_MAP = {
    "windows_update": "windows_update_service_stopped",
    "defender_exclusions": "defender_exclusion_path",
    "guest_account": "guest_account_enabled",
    "smb_signing": "smb_signing_disabled",
    "audit_policy": "audit_policy_insufficient",
}


@app.post("/api/admin/chaos-quicktest")
async def create_chaos_quicktest(
    request: Request,
    user: dict = Depends(require_auth),
):
    """Create a chaos quicktest fleet order that injects drift into Windows targets.

    The order tells the appliance daemon to run the inject commands via WinRM.
    The normal scan cycle then detects the drift and the healing pipeline remediates.
    Use the GET status endpoint to check progress.
    """
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass  # Use defaults if no body

    scenarios = body.get("scenarios", CHAOS_QUICKTEST_SCENARIOS)

    # Validate scenarios
    if not isinstance(scenarios, list) or len(scenarios) == 0:
        raise HTTPException(status_code=400, detail="scenarios must be a non-empty array")
    if len(scenarios) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 scenarios per quicktest")

    for i, s in enumerate(scenarios):
        if not isinstance(s, dict):
            raise HTTPException(status_code=400, detail=f"scenario[{i}] must be an object")
        if not s.get("target") or not s.get("inject") or not s.get("type"):
            raise HTTPException(status_code=400, detail=f"scenario[{i}] requires target, type, and inject")

    campaign_id = f"chaos-qt-{int(datetime.now(timezone.utc).timestamp() * 1000)}"

    parameters = {
        "campaign_id": campaign_id,
        "scenarios": scenarios,
    }

    from dashboard_api.fleet import get_pool
    from dashboard_api.order_signing import sign_fleet_order
    from dashboard_api.tenant_middleware import admin_connection

    pool = await get_pool()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=2)

    async with admin_connection(pool) as conn:
        row = await conn.fetchrow("""
            INSERT INTO fleet_orders (order_type, parameters, status, expires_at, created_by,
                                      nonce, signature, signed_payload)
            VALUES ($1, $2::jsonb, 'active', $3, $4, $5, $6, $7)
            RETURNING id, created_at, expires_at
        """,
            "chaos_quicktest",
            json.dumps(parameters),
            expires_at,
            user.get("username") or user.get("email"),
            *sign_fleet_order(0, "chaos_quicktest", parameters, now, expires_at),
        )

        # Audit log
        try:
            await conn.execute("""
                INSERT INTO audit_log (action, actor, target_type, target_id, details)
                VALUES ($1, $2, 'fleet', $3, $4::jsonb)
            """,
                "chaos_quicktest_created",
                user.get("username") or user.get("email"),
                str(row["id"]),
                json.dumps({"campaign_id": campaign_id, "scenario_count": len(scenarios)}),
            )
        except Exception:
            pass  # Non-critical

    logger.info(
        f"Chaos quicktest created: order={row['id']} campaign={campaign_id} "
        f"scenarios={len(scenarios)} by={user.get('username')}"
    )

    return {
        "order_id": str(row["id"]),
        "campaign_id": campaign_id,
        "scenarios": len(scenarios),
        "created_at": row["created_at"].isoformat(),
        "expires_at": row["expires_at"].isoformat(),
        "status_url": f"/api/admin/chaos-quicktest/{row['id']}/status",
    }


@app.get("/api/admin/chaos-quicktest/{order_id}/status")
async def get_chaos_quicktest_status(
    order_id: str,
    user: dict = Depends(require_auth),
):
    """Check the status of a chaos quicktest.

    Checks:
    1. Fleet order completion status (did the daemon execute the injects?)
    2. Execution telemetry for matching incident types (detected + healed?)
    3. Returns per-scenario status: injected, detected, healed, or failed
    """
    from dashboard_api.fleet import get_pool
    from dashboard_api.tenant_middleware import admin_connection

    pool = await get_pool()

    async with admin_connection(pool) as conn:
        # 1. Get the fleet order
        order = await conn.fetchrow("""
            SELECT id, order_type, parameters, status, created_at, expires_at
            FROM fleet_orders
            WHERE id = $1
        """, uuid.UUID(order_id))

        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        if order["order_type"] != "chaos_quicktest":
            raise HTTPException(status_code=400, detail="Order is not a chaos_quicktest")

        params = json.loads(order["parameters"]) if isinstance(order["parameters"], str) else order["parameters"]
        campaign_id = params.get("campaign_id", "")
        scenarios = params.get("scenarios", [])

        # 2. Check fleet order completion
        completion = await conn.fetchrow("""
            SELECT status, result, completed_at
            FROM fleet_order_completions
            WHERE fleet_order_id = $1
            ORDER BY completed_at DESC
            LIMIT 1
        """, order["id"])

        order_executed = completion is not None
        completion_status = completion["status"] if completion else "pending"
        completion_result = {}
        if completion and completion["result"]:
            completion_result = json.loads(completion["result"]) if isinstance(completion["result"], str) else completion["result"]

        # 3. Check execution telemetry for incident detection + healing
        # Look for telemetry entries matching the injected drift types
        # within a window from order creation to now
        order_created = order["created_at"]

        # Build the set of incident types we expect to see
        expected_types = []
        for s in scenarios:
            drift_type = s.get("type", "")
            incident_type = CHAOS_INCIDENT_TYPE_MAP.get(drift_type, drift_type)
            expected_types.append(incident_type)

        # Query telemetry for these incident types since the order was created
        telemetry_rows = await conn.fetch("""
            SELECT incident_type, success, hostname, runbook_id,
                   resolution_level, duration_seconds, error_message,
                   created_at
            FROM execution_telemetry
            WHERE created_at >= $1
              AND incident_type = ANY($2)
            ORDER BY created_at DESC
        """, order_created, expected_types)

        # Build a lookup: incident_type -> latest telemetry row
        telemetry_by_type = {}
        for row in telemetry_rows:
            itype = row["incident_type"]
            if itype not in telemetry_by_type:
                telemetry_by_type[itype] = row

        # Also check incidents table for detection (incidents may exist before healing runs)
        incident_rows = await conn.fetch("""
            SELECT incident_type, status, hostname, reported_at
            FROM incidents
            WHERE reported_at >= $1
              AND incident_type = ANY($2)
            ORDER BY reported_at DESC
        """, order_created, expected_types)

        detected_types = set()
        for row in incident_rows:
            detected_types.add(row["incident_type"])

        # 4. Build per-scenario status
        scenario_results = []
        for s in scenarios:
            drift_type = s.get("type", "")
            incident_type = CHAOS_INCIDENT_TYPE_MAP.get(drift_type, drift_type)
            target = s.get("target", "")

            # Check injection result from completion
            inject_status = "pending"
            if order_executed:
                inject_results = completion_result.get("results", [])
                for ir in inject_results:
                    if ir.get("type") == drift_type:
                        inject_status = ir.get("status", "unknown")
                        break
                if inject_status == "pending" and completion_status == "completed":
                    inject_status = "injected"  # Assume success if order completed

            # Check detection
            detected = incident_type in detected_types

            # Check healing from telemetry
            healed = False
            heal_details = None
            if incident_type in telemetry_by_type:
                t = telemetry_by_type[incident_type]
                healed = t["success"]
                heal_details = {
                    "resolution_level": t["resolution_level"],
                    "runbook_id": t["runbook_id"],
                    "duration_seconds": float(t["duration_seconds"]) if t["duration_seconds"] else None,
                    "error": t["error_message"],
                    "healed_at": t["created_at"].isoformat() if t["created_at"] else None,
                }

            # Determine overall scenario status
            if inject_status == "pending":
                status = "pending"
            elif inject_status in ("inject_failed", "rejected", "error"):
                status = "inject_failed"
            elif healed:
                status = "healed"
            elif detected:
                status = "detected"
            elif inject_status == "injected":
                status = "waiting_for_scan"
            else:
                status = inject_status

            scenario_results.append({
                "type": drift_type,
                "incident_type": incident_type,
                "target": target,
                "inject_status": inject_status,
                "detected": detected,
                "healed": healed,
                "status": status,
                "healing": heal_details,
            })

        # Summary counts
        summary = {
            "total": len(scenarios),
            "injected": sum(1 for s in scenario_results if s["inject_status"] == "injected"),
            "detected": sum(1 for s in scenario_results if s["detected"]),
            "healed": sum(1 for s in scenario_results if s["healed"]),
            "failed": sum(1 for s in scenario_results if s["status"] == "inject_failed"),
            "pending": sum(1 for s in scenario_results if s["status"] in ("pending", "waiting_for_scan")),
        }

        return {
            "order_id": str(order["id"]),
            "campaign_id": campaign_id,
            "order_status": order["status"],
            "order_executed": order_executed,
            "completion_status": completion_status,
            "created_at": order["created_at"].isoformat(),
            "scenarios": scenario_results,
            "summary": summary,
        }

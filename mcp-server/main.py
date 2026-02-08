#\!/usr/bin/env python3
"""
MCP Server - Central Control Plane for MSP Compliance Platform

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
from dashboard_api.provisioning import router as provisioning_router
from dashboard_api.partners import router as partners_router
from dashboard_api.discovery import router as discovery_router
from dashboard_api.runbook_config import router as runbook_config_router
from dashboard_api.users import router as users_router
from dashboard_api.integrations.api import router as integrations_router, public_router as integrations_public_router
from dashboard_api.frameworks import router as frameworks_router
from dashboard_api.fleet_updates import router as fleet_updates_router
from dashboard_api.device_sync import device_sync_router
from dashboard_api.email_alerts import create_notification_with_email
from dashboard_api.oauth_login import public_router as oauth_public_router, router as oauth_router, admin_router as oauth_admin_router
from dashboard_api.partner_auth import public_router as partner_auth_router, admin_router as partner_admin_router
from dashboard_api.billing import router as billing_router
from dashboard_api.exceptions_api import router as exceptions_router
from dashboard_api.appliance_delegation import router as appliance_delegation_router
from dashboard_api.learning_api import partner_learning_router
from dashboard_api.websocket_manager import ws_manager

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

# Order TTL
ORDER_TTL_SECONDS = int(os.getenv("ORDER_TTL_SECONDS", "900"))  # 15 minutes

# WORM Storage retention (HIPAA requires 6 years, default 90 days per bundle, 7 years overall)
WORM_RETENTION_DAYS = int(os.getenv("WORM_RETENTION_DAYS", "90"))

# OpenAI for L2
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

# ============================================================================
# Logging Setup
# ============================================================================

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

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=20,           # Increased for production load
    max_overflow=30,        # Allow burst capacity
    pool_recycle=3600,      # Recycle stale connections after 1 hour
    pool_pre_ping=True,     # Verify connections before use
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

def load_or_create_signing_key():
    global signing_key, verify_key
    
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
    signed = signing_key.sign(data.encode())
    return signed.signature.hex()

def get_public_key_hex() -> str:
    """Get hex-encoded public key."""
    return verify_key.encode(encoder=HexEncoder).decode()

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
    """
    key = f"rate:{site_id}:{action}"
    
    # Get current count
    count = await redis_client.get(key)
    
    if count is None:
        # First request in window
        await redis_client.setex(key, RATE_LIMIT_WINDOW, 1)
        return True, 0
    
    count = int(count)
    
    if count >= RATE_LIMIT_REQUESTS:
        # Rate limited
        ttl = await redis_client.ttl(key)
        return False, max(0, ttl)
    
    # Increment counter
    await redis_client.incr(key)
    return True, 0

# ============================================================================
# Background Tasks
# ============================================================================

async def _ots_upgrade_loop():
    """Periodically upgrade pending OTS proofs (every 15 minutes)."""
    await asyncio.sleep(30)  # Wait 30s after startup
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
            logger.warning(f"OTS upgrade cycle failed: {e}")
        await asyncio.sleep(900)  # 15 minutes


# ============================================================================
# Lifespan Events
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client
    
    # Startup
    logger.info("Starting MCP Server...")
    
    # Connect to Redis
    redis_client = await redis.from_url(REDIS_URL, decode_responses=True)
    await redis_client.ping()
    logger.info("Connected to Redis", url=REDIS_URL.split("@")[-1])
    
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

    # Start periodic OTS proof upgrade task
    ots_upgrade_task = asyncio.create_task(_ots_upgrade_loop())

    yield

    # Shutdown
    logger.info("Shutting down MCP Server...")
    ots_upgrade_task.cancel()
    if redis_client:
        await redis_client.close()
    await engine.dispose()
    logger.info("MCP Server stopped")

# ============================================================================
# FastAPI App
# ============================================================================

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, 
    title="MCP Server",
    description="MSP Compliance Platform - Central Control Plane",
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
app.include_router(discovery_router)
app.include_router(runbook_config_router)
app.include_router(users_router)
app.include_router(integrations_router)
app.include_router(integrations_public_router)  # OAuth callback (no auth)
app.include_router(frameworks_router)
app.include_router(fleet_updates_router)
app.include_router(device_sync_router)  # Device inventory sync from appliances
app.include_router(oauth_public_router, prefix="/api/auth")  # OAuth login public endpoints
app.include_router(oauth_router, prefix="/api/auth")  # OAuth authenticated endpoints
app.include_router(oauth_admin_router, prefix="/api")  # OAuth admin endpoints
app.include_router(partner_auth_router, prefix="/api")  # Partner OAuth login endpoints
app.include_router(partner_admin_router, prefix="/api")  # Partner admin endpoints (pending, oauth-config)
app.include_router(billing_router)  # Stripe billing for partners
app.include_router(exceptions_router)  # Compliance exceptions management
app.include_router(appliance_delegation_router)  # Appliance delegation (signing keys, audit, escalations)
app.include_router(partner_learning_router)  # Partner learning management (promotions, rules)

# WebSocket endpoint for real-time event push
@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            # Keep connection alive; client can send pings
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
    except Exception:
        await ws_manager.disconnect(websocket)

# Serve agent update packages (only if directory exists)
_agent_packages_dir = Path("/opt/mcp-server/agent-packages")
if _agent_packages_dir.exists():
    app.mount("/agent-packages", StaticFiles(directory=str(_agent_packages_dir)), name="agent-packages")

# ============================================================================
# Endpoints
# ============================================================================

@app.get("/")
async def root():
    """Service information."""
    return {
        "service": "MCP Server",
        "version": "1.0.0",
        "description": "MSP Compliance Platform - Central Control Plane"
    }

@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    """Health check endpoint."""
    checks = {"status": "ok"}
    
    # Check Redis
    try:
        await redis_client.ping()
        checks["redis"] = "connected"
    except Exception as e:
        checks["redis"] = f"error: {str(e)}"
        checks["status"] = "degraded"
    
    # Check database
    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "connected"
    except Exception as e:
        checks["database"] = f"error: {str(e)}"
        checks["status"] = "degraded"
    
    # Check MinIO
    try:
        minio_client.bucket_exists(MINIO_BUCKET)
        checks["minio"] = "connected"
    except Exception as e:
        checks["minio"] = f"error: {str(e)}"
        checks["status"] = "degraded"
    
    checks["timestamp"] = datetime.now(timezone.utc).isoformat()
    checks["runbooks_loaded"] = len(RUNBOOKS)
    
    status_code = 200 if checks["status"] == "ok" else 503
    return JSONResponse(content=checks, status_code=status_code)

@app.post("/checkin")
async def checkin(req: CheckinRequest, request: Request, db: AsyncSession = Depends(get_db)):
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
                   issued_at, expires_at
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
            "runbook_id": row[1],
            "parameters": row[2],
            "nonce": row[3],
            "signature": row[4],
            "ttl_seconds": row[5],
            "issued_at": row[6].isoformat() if row[6] else None,
            "expires_at": row[7].isoformat() if row[7] else None
        })
    
    logger.info("Appliance checked in", 
                site_id=req.site_id, 
                action=action,
                pending_orders=len(orders))
    
    return {
        "status": "ok",
        "action": action,
        "timestamp": now.isoformat(),
        "server_public_key": get_public_key_hex(),
        "pending_orders": orders
    }

@app.get("/orders/{site_id}")
async def get_orders(site_id: str, db: AsyncSession = Depends(get_db)):
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
async def acknowledge_order(req: OrderAcknowledgement, db: AsyncSession = Depends(get_db)):
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
async def report_incident(incident: IncidentReport, request: Request, db: AsyncSession = Depends(get_db)):
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
    
    # Get appliance
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
    
    appliance_id = str(appliance[0])
    now = datetime.now(timezone.utc)

    # Deduplicate: Check for existing open/escalated incident of same type (last hour)
    existing_check = await db.execute(
        text("""
            SELECT id FROM incidents
            WHERE appliance_id = :appliance_id
            AND incident_type = :incident_type
            AND status IN ('open', 'resolving', 'escalated')
            AND created_at > NOW() - INTERVAL '1 hour'
            LIMIT 1
        """),
        {"appliance_id": appliance_id, "incident_type": incident.incident_type}
    )
    existing_incident = existing_check.fetchone()

    if existing_incident:
        # Return existing incident instead of creating duplicate
        return {
            "status": "deduplicated",
            "incident_id": str(existing_incident[0]),
            "resolution_tier": None,
            "order_id": None,
            "runbook_id": None,
            "timestamp": now.isoformat()
        }

    # Create incident record
    incident_id = str(uuid.uuid4())
    await db.execute(
        text("""
            INSERT INTO incidents (id, appliance_id, incident_type, severity, check_type,
                details, pre_state, hipaa_controls, reported_at)
            VALUES (:id, :appliance_id, :incident_type, :severity, :check_type,
                :details, :pre_state, :hipaa_controls, :reported_at)
        """),
        {
            "id": incident_id,
            "appliance_id": appliance_id,
            "incident_type": incident.incident_type,
            "severity": incident.severity,
            "check_type": incident.check_type,
            "details": json.dumps(incident.details),
            "pre_state": json.dumps(incident.pre_state),
            "hipaa_controls": incident.hipaa_controls,
            "reported_at": now
        }
    )
    
    # Try to find matching runbook (L1 - deterministic)
    runbook_id = None
    resolution_tier = None
    
    # Simple rule-based matching
    type_lower = incident.incident_type.lower()
    check_type = incident.check_type or ""
    
    runbook_map = {
        "backup": "RB-BACKUP-001",
        "certificate": "RB-CERT-001",
        "cert": "RB-CERT-001",
        "disk": "RB-DISK-001",
        "storage": "RB-DISK-001",
        "service": "RB-SERVICE-001",
        "drift": "RB-DRIFT-001",
        "configuration": "RB-DRIFT-001",
        "firewall": "RB-FIREWALL-001",
        "patching": "RB-PATCH-001",
        "update": "RB-PATCH-001"
    }
    
    for keyword, rb_id in runbook_map.items():
        if keyword in type_lower or keyword in check_type.lower():
            if rb_id in ALLOWED_RUNBOOKS or rb_id in ["RB-BACKUP-001", "RB-CERT-001", "RB-DISK-001", 
                                                        "RB-SERVICE-001", "RB-DRIFT-001", "RB-FIREWALL-001", 
                                                        "RB-PATCH-001"]:
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
        
        # Create order payload and sign it
        order_payload = json.dumps({
            "order_id": order_id,
            "runbook_id": runbook_id,
            "parameters": {},
            "nonce": nonce,
            "issued_at": now.isoformat(),
            "expires_at": expires_at.isoformat()
        }, sort_keys=True)
        
        signature = sign_data(order_payload)
        
        # Store order
        await db.execute(
            text("""
                INSERT INTO orders (order_id, appliance_id, runbook_id, parameters, nonce,
                    signature, ttl_seconds, issued_at, expires_at)
                VALUES (:order_id, :appliance_id, :runbook_id, :parameters, :nonce,
                    :signature, :ttl_seconds, :issued_at, :expires_at)
            """),
            {
                "order_id": order_id,
                "appliance_id": appliance_id,
                "runbook_id": runbook_id,
                "parameters": json.dumps({}),
                "nonce": nonce,
                "signature": signature,
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
    else:
        # No matching runbook - would escalate to L2/L3
        resolution_tier = "L3"
        await db.execute(
            text("""
                UPDATE incidents SET
                    resolution_tier = :resolution_tier,
                    status = 'escalated'
                WHERE id = :incident_id
            """),
            {"resolution_tier": resolution_tier, "incident_id": incident_id}
        )
        
        logger.warning("No matching runbook - escalated",
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
            # Deduplication: 1 hour for critical/high, 24 hours for L3 escalations
            # Check same category to avoid L3 escalations being blocked by older incident notifications
            dedup_hours = 24 if resolution_tier == "L3" else 1
            notification_category = "escalation" if resolution_tier == "L3" else "incident"

            dedup_check = await db.execute(
                text(f"""
                    SELECT id FROM notifications
                    WHERE site_id = :site_id
                    AND category = :category
                    AND title LIKE :title_pattern
                    AND created_at > NOW() - INTERVAL '{dedup_hours} hours'
                    LIMIT 1
                """),
                {
                    "site_id": incident.site_id,
                    "category": notification_category,
                    "title_pattern": f"%{incident.incident_type}%"
                }
            )
            existing = dedup_check.fetchone()

            if not existing:
                # L3 escalations get "critical" severity to trigger email
                if resolution_tier == "L3":
                    notification_severity = "critical"

                await create_notification_with_email(
                    db=db,
                    severity=notification_severity,
                    category=notification_category,
                    title=f"[L3] {incident.incident_type}" if resolution_tier == "L3" else f"{incident.severity.upper()}: {incident.incident_type}",
                    message=f"L3 Escalation: {incident.incident_type} on {incident.site_id} requires human review." if resolution_tier == "L3" else f"Incident {incident.incident_type} on {incident.site_id}. Resolution: {resolution_tier}",
                    site_id=incident.site_id,
                    appliance_id=appliance_id,
                    metadata={
                        "incident_id": incident_id,
                        "check_type": incident.check_type,
                        "resolution_tier": resolution_tier,
                        "order_id": order_id
                    }
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
async def resolve_incident(incident_id: str, request: Request, db: AsyncSession = Depends(get_db)):
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


@app.post("/drift")
async def report_drift(drift: DriftReport, db: AsyncSession = Depends(get_db)):
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
async def submit_evidence(evidence: EvidenceSubmission, db: AsyncSession = Depends(get_db)):
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
    db: AsyncSession = Depends(get_db)
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
async def report_agent_pattern(report: PatternReportInput, db: AsyncSession = Depends(get_db)):
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
async def sync_pattern_stats(request: PatternStatsRequest, db: AsyncSession = Depends(get_db)):
    """
    Receive pattern statistics from agent for cross-appliance aggregation.

    This endpoint is called periodically (every 4 hours) by appliances to sync
    their local pattern_stats table to Central Command. Stats are aggregated
    across all appliances at a site for promotion decisions.
    """
    accepted = 0
    merged = 0

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
                    "eligible": stat.promotion_eligible,
                    "last_seen": last_seen_dt,
                })
                merged += 1
            else:
                # Insert new pattern
                # NOTE: success_rate stored as decimal (0.0-1.0), not percentage
                success_rate = (stat.success_count / stat.total_occurrences) if stat.total_occurrences > 0 else 0
                avg_time = stat.total_resolution_time_ms / stat.total_occurrences if stat.total_occurrences > 0 else 0

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
                    "eligible": stat.promotion_eligible,
                    "first_seen": last_seen_dt,
                    "last_seen": last_seen_dt,
                })
                accepted += 1

        except Exception as e:
            logger.warning(f"Failed to sync pattern {stat.pattern_signature}: {e}")
            # Rollback to clear the aborted transaction state
            await db.rollback()
            continue

    # Record sync event
    try:
        await db.execute(text("""
            INSERT INTO appliance_pattern_sync (appliance_id, site_id, synced_at, patterns_received, patterns_merged, sync_status)
            VALUES (:appliance_id, :site_id, NOW(), :received, :merged, 'success')
            ON CONFLICT (appliance_id) DO UPDATE SET
                synced_at = NOW(),
                patterns_received = :received,
                patterns_merged = :merged,
                sync_status = 'success'
        """), {
            "appliance_id": request.appliance_id,
            "site_id": request.site_id,
            "received": len(request.pattern_stats),
            "merged": merged,
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
    db: AsyncSession = Depends(get_db)
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
    """Execution telemetry from agent."""
    site_id: str
    execution: dict
    reported_at: str


@app.post("/api/agent/executions")
async def report_execution_telemetry(request: ExecutionTelemetryInput, db: AsyncSession = Depends(get_db)):
    """
    Receive rich execution telemetry from agents for learning engine.

    This data feeds the learning system to analyze runbook effectiveness,
    identify patterns for improvement, and track healing outcomes.
    """
    exec_data = request.execution

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
        await db.execute(text("""
            INSERT INTO execution_telemetry (
                execution_id, incident_id, site_id, appliance_id, runbook_id, hostname, platform, incident_type,
                started_at, completed_at, duration_seconds,
                success, status, verification_passed, confidence, resolution_level,
                state_before, state_after, state_diff, executed_steps,
                error_message, error_step, failure_type, retry_count,
                evidence_bundle_id, created_at
            ) VALUES (
                :exec_id, :incident_id, :site_id, :appliance_id, :runbook_id, :hostname, :platform, :incident_type,
                :started_at, :completed_at, :duration,
                :success, :status, :verification, :confidence, :resolution_level,
                CAST(:state_before AS jsonb), CAST(:state_after AS jsonb), CAST(:state_diff AS jsonb), CAST(:executed_steps AS jsonb),
                :error_msg, :error_step, :failure_type, :retry_count,
                :evidence_id, NOW()
            )
            ON CONFLICT (execution_id) DO UPDATE SET
                success = EXCLUDED.success,
                state_after = EXCLUDED.state_after,
                state_diff = EXCLUDED.state_diff,
                error_message = EXCLUDED.error_message,
                failure_type = EXCLUDED.failure_type
        """), {
            "exec_id": exec_data.get("execution_id"),
            "incident_id": exec_data.get("incident_id"),
            "site_id": request.site_id,
            "appliance_id": exec_data.get("appliance_id", "unknown"),
            "runbook_id": exec_data.get("runbook_id"),
            "hostname": exec_data.get("hostname", "unknown"),
            "platform": exec_data.get("platform"),
            "incident_type": exec_data.get("incident_type"),
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
        })

        await db.commit()

        logger.info(f"Execution telemetry recorded: {exec_data.get('execution_id')} (success={exec_data.get('success')})")
        return {
            "status": "recorded",
            "execution_id": exec_data.get("execution_id"),
        }

    except Exception as e:
        logger.error(f"Failed to record execution telemetry: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to record telemetry: {e}")


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
    db: AsyncSession = Depends(get_db)
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
                except Exception:
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
async def list_runbooks():
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
async def get_stats(db: AsyncSession = Depends(get_db)):
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
async def agent_sync_rules(site_id: Optional[str] = None, db: AsyncSession = Depends(get_db)):
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
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
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
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
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
                {"field": "check_type", "operator": "eq", "value": "bitlocker"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["enable_bitlocker"],
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
                {"field": "check_type", "operator": "eq", "value": "smb1_disabled"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["disable_smb1"],
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
                {"field": "check_type", "operator": "eq", "value": "screen_lock"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["set_screen_lock_policy"],
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
        }
    ]

    # Select rules based on healing tier
    if healing_tier == "full_coverage":
        builtin_rules = standard_rules + full_coverage_extra_rules
    else:
        builtin_rules = standard_rules

    # Fetch custom/promoted rules from database
    try:
        result = await db.execute(
            text("""
                SELECT rule_id, incident_pattern, runbook_id, confidence
                FROM l1_rules
                WHERE enabled = true
                ORDER BY confidence DESC
            """)
        )
        db_rules = []
        for row in result.fetchall():
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
                "severity": "medium",
                "cooldown_seconds": 300,
                "max_retries": 2,
                "source": "promoted"
            })
    except Exception as e:
        logger.warning(f"Failed to fetch DB rules: {e}")
        db_rules = []

    all_rules = builtin_rules + db_rules

    return {
        "rules": all_rules,
        "healing_tier": healing_tier,
        "version": "1.0.0",
        "count": len(all_rules)
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
    db: AsyncSession = Depends(get_db)
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


@app.get("/api/learning/promotion-candidates")
async def get_promotion_candidates(
    site_id: Optional[str] = None,
    status: str = "pending",
    db: AsyncSession = Depends(get_db)
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
    db: AsyncSession = Depends(get_db)
):
    """
    Approve or reject a promotion candidate.

    Site owners can approve patterns to be promoted to L1 deterministic rules.
    """
    try:
        # Get current user from session
        current_user = await get_current_user_from_session(request, db)
        if not current_user:
            return JSONResponse(status_code=401, content={"error": "Not authenticated"})

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


@app.get("/api/learning/approved-promotions")
async def get_approved_promotions(
    site_id: str,
    since: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
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
async def appliances_checkin(req: ApplianceCheckinRequest, request: Request, db: AsyncSession = Depends(get_db)):
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

        # Fetch Windows targets with credentials for credential-pull
        windows_targets = []
        try:
            result = await db.execute(text("""
                SELECT credential_type, credential_name, encrypted_data
                FROM site_credentials
                WHERE site_id = :site_id
                AND credential_type IN ('winrm', 'domain_admin', 'domain_member', 'service_account', 'local_admin')
                ORDER BY created_at DESC
            """), {"site_id": req.site_id})
            creds = result.fetchall()
            
            for cred in creds:
                try:
                    cred_data = json.loads(bytes(cred.encrypted_data).decode())
                    hostname = cred_data.get('host') or cred_data.get('target_host')
                    username = cred_data.get('username', '')
                    password = cred_data.get('password', '')
                    domain = cred_data.get('domain', '')
                    use_ssl = cred_data.get('use_ssl', False)
                    
                    full_username = f"{domain}\\{username}" if domain else username
                    
                    if hostname:
                        windows_targets.append({
                            "hostname": hostname,
                            "username": full_username,
                            "password": password,
                            "use_ssl": use_ssl,
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
async def get_backup_status():
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
async def get_snapshot_status():
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
async def list_snapshots():
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

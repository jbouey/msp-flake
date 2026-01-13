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

import os
import json
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from pathlib import Path
from contextlib import asynccontextmanager
import uuid

from fastapi import FastAPI, File, UploadFile, Form, Header
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
from dashboard_api.integrations.api import router as integrations_router
from dashboard_api.email_alerts import create_notification_with_email

# ============================================================================
# Configuration
# ============================================================================

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://mcp:mcp@localhost/mcp")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minio")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minio-password")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "evidence")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

SIGNING_KEY_FILE = Path(os.getenv("SIGNING_KEY_FILE", "/app/secrets/signing.key"))
RUNBOOK_DIR = Path(os.getenv("RUNBOOK_DIR", "/app/runbooks"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Rate limiting
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "10"))
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

engine = create_async_engine(DATABASE_URL, echo=False, pool_size=10, max_overflow=20)
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
    
    yield
    
    # Shutdown
    logger.info("Shutting down MCP Server...")
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

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://dashboard.osiriscare.net", "https://portal.osiriscare.net"],  # Configure for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

    # Create notification for critical/high/medium severity incidents
    # Map incident severity to notification severity (critical, warning, info, success)
    severity_map = {"critical": "critical", "high": "warning", "medium": "info", "low": "info"}
    notification_severity = severity_map.get(incident.severity, "info")

    if incident.severity in ("critical", "high", "medium"):
        try:
            await create_notification_with_email(
                db=db,
                severity=notification_severity,
                category="incident",
                title=f"{incident.severity.upper()}: {incident.incident_type}",
                message=f"Incident {incident.incident_type} on {incident.site_id}. Resolution: {resolution_tier}",
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
async def agent_sync_rules(db: AsyncSession = Depends(get_db)):
    """
    Return L1 rules for agents to sync.
    
    Returns built-in rules plus any custom/promoted rules from database.
    """
    # Built-in L1 rules for NixOS appliances
    builtin_rules = [
        {
            "id": "L1-NTP-001",
            "name": "NTP Drift Remediation",
            "description": "Restart chronyd when NTP sync drifts",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "ntp_sync"},
                {"field": "status", "operator": "eq", "value": "non_compliant"}
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
            "description": "Restart failed compliance-agent service",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "services_running"},
                {"field": "status", "operator": "eq", "value": "non_compliant"}
            ],
            "actions": ["restart_service:compliance-agent"],
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
                {"field": "check_type", "operator": "eq", "value": "disk_usage"},
                {"field": "status", "operator": "eq", "value": "non_compliant"}
            ],
            "actions": ["alert:disk_space_critical"],
            "severity": "high",
            "cooldown_seconds": 3600,
            "max_retries": 1,
            "source": "builtin"
        },
        {
            "id": "L1-FIREWALL-001",
            "name": "Firewall Drift",
            "description": "Alert when firewall is disabled",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "firewall_enabled"},
                {"field": "status", "operator": "eq", "value": "non_compliant"}
            ],
            "actions": ["alert:firewall_disabled", "enable_firewall"],
            "severity": "critical",
            "cooldown_seconds": 300,
            "max_retries": 1,
            "source": "builtin"
        },
        {
            "id": "L1-GENERATION-001",
            "name": "NixOS Generation Drift",
            "description": "Alert when NixOS generation changes unexpectedly",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "nixos_generation"},
                {"field": "status", "operator": "eq", "value": "non_compliant"}
            ],
            "actions": ["alert:generation_drift"],
            "severity": "medium",
            "cooldown_seconds": 3600,
            "max_retries": 1,
            "source": "builtin"
        }
    ]
    
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
            db_rules.append({
                "id": row[0],
                "name": f"Promoted: {row[0]}",
                "description": f"Auto-promoted rule with {row[3]:.0%} confidence",
                "conditions": row[1] if isinstance(row[1], list) else [],
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
        "version": "1.0.0",
        "count": len(all_rules)
    }


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
    """Appliance agent checkin endpoint - updates site_appliances table."""
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
                AND credential_type IN ('winrm', 'domain_admin', 'service_account', 'local_admin')
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

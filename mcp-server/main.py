#!/usr/bin/env python3
"""
MCP Server - Central Orchestration Server for MSP Compliance Platform

Production-ready FastAPI server. Endpoint logic has been extracted to:
  - dashboard_api/agent_api.py       (appliance/agent endpoints)
  - dashboard_api/learning_api_main.py (learning system endpoints)
  - dashboard_api/infra_api.py       (stats, runbooks, backup, snapshots)
  - dashboard_api/background_tasks.py (OTS, flywheel, fleet, reconciliation)
  - dashboard_api/shared.py          (DB, signing, MinIO, rate limiting, auth)

This file retains only:
  - App creation, lifespan, middleware, exception handlers
  - Router includes
  - WebSocket endpoint
  - Health endpoint (needs app.state access)
"""

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi import HTTPException
import redis.asyncio as redis
from sqlalchemy import text
import structlog

# ============================================================================
# Dashboard API routes (existing extracted modules)
# ============================================================================
from dashboard_api.routes import router as dashboard_router, auth_router
from dashboard_api.sites import router as sites_router, orders_router, appliances_router, alerts_router
from dashboard_api.portal import router as portal_router
from dashboard_api.evidence_chain import router as evidence_router
from dashboard_api.org_credentials import router as org_credentials_router
from dashboard_api.provisioning import router as provisioning_router
from dashboard_api.partners import router as partners_router
from dashboard_api.discovery import router as discovery_router
from dashboard_api.runbook_config import router as runbook_config_router
from dashboard_api.users import router as users_router
from dashboard_api.integrations.api import router as integrations_router, public_router as integrations_public_router
from dashboard_api.frameworks import router as frameworks_router
from dashboard_api.compliance_frameworks import router as compliance_frameworks_router, partner_router as compliance_partner_router
from dashboard_api.fleet_updates import router as fleet_updates_router
from dashboard_api.device_sync import device_sync_router
from dashboard_api.log_ingest import router as log_ingest_router
from dashboard_api.security_events import router as security_events_router
from dashboard_api.health_monitor import health_monitor_loop
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
from dashboard_api.framework_sync import router as framework_sync_router, framework_sync_loop
from dashboard_api.prometheus_metrics import router as metrics_router
from dashboard_api.websocket_manager import ws_manager

# ============================================================================
# Newly extracted modules from this file
# ============================================================================
from dashboard_api.agent_api import router as agent_router
from dashboard_api.learning_api_main import router as learning_main_router
from dashboard_api.infra_api import router as infra_router
from dashboard_api import background_tasks

# Shared state (DB, signing, MinIO, rate limiting, auth)
# NOTE: Many modules do lazy `from main import async_session`, `from main import redis_client`, etc.
# These re-exports ensure backward compatibility with those lazy imports.
from dashboard_api.shared import (
    async_session,
    engine,
    get_db,
    get_public_key_hex,
    get_all_public_keys_hex,
    load_or_create_signing_key,
    load_runbooks,
    RUNBOOKS,
    set_redis_client,
    setup_minio,
    sign_data,
    MINIO_BUCKET,
    get_minio_client,
    require_appliance_bearer,
    check_rate_limit,
)

# Re-export redis_client as a module-level attribute for `from main import redis_client`
# This property-like access works because shared.redis_client is updated by set_redis_client().
from dashboard_api import shared as _shared

def __getattr__(name):
    """Module-level __getattr__ to support `from main import redis_client` etc."""
    if name == "redis_client":
        return _shared.redis_client
    if name == "minio_client":
        return _shared.minio_client
    if name == "signing_key":
        return _shared.signing_key
    if name == "verify_key":
        return _shared.verify_key
    raise AttributeError(f"module 'main' has no attribute {name!r}")

# ============================================================================
# Configuration
# ============================================================================

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

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
# Lifespan Events
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting MCP Server...")

    # Connect to Redis with timeout and retry
    redis_client = None
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

    set_redis_client(redis_client)

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

    logger.info("MCP Server started")

    # Supervised background task registry — auto-restarts on crash
    _bg_tasks: dict = {}
    _bg_shutdown = asyncio.Event()

    async def _supervised(name: str, coro_fn, *args, restart=True):
        """Run a coroutine with auto-restart on unexpected exit."""
        while not _bg_shutdown.is_set():
            try:
                logger.info(f"bg_task_started", task=name)
                await coro_fn(*args)
                logger.info(f"bg_task_completed", task=name)
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"bg_task_crashed", task=name, error=str(e))
                if not restart or _bg_shutdown.is_set():
                    break
                await asyncio.sleep(30)

    from dashboard_api.companion import companion_alert_check_loop

    task_defs = [
        ("ots_upgrade", background_tasks.ots_upgrade_loop),
        ("ots_resubmit", background_tasks.ots_resubmit_expired_loop),
        ("cve_watch", cve_sync_loop),
        ("framework_sync", framework_sync_loop),
        ("flywheel", background_tasks.flywheel_promotion_loop),
        ("companion_alerts", companion_alert_check_loop),
        ("fleet_order_expiry", background_tasks.expire_fleet_orders_loop),
        ("health_monitor", health_monitor_loop),
        ("reconciliation", background_tasks.reconciliation_loop),
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

# ============================================================================
# Router Includes
# ============================================================================

# Existing dashboard API routes
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
app.include_router(integrations_public_router)
app.include_router(frameworks_router)
app.include_router(fleet_updates_router)
app.include_router(device_sync_router)
app.include_router(oauth_public_router, prefix="/api/auth")
app.include_router(oauth_router, prefix="/api/auth")
app.include_router(oauth_admin_router, prefix="/api")
app.include_router(partner_auth_router, prefix="/api")
app.include_router(partner_session_router, prefix="/api/partner-auth")
app.include_router(partner_admin_router, prefix="/api")
app.include_router(billing_router)
app.include_router(exceptions_router)
app.include_router(appliance_delegation_router)
app.include_router(partner_learning_router)
app.include_router(cve_watch_router)
app.include_router(framework_sync_router)
app.include_router(client_auth_router, prefix="/api")
app.include_router(client_portal_router, prefix="/api")
app.include_router(hipaa_modules_router, prefix="/api")
app.include_router(companion_router, prefix="/api")
app.include_router(org_credentials_router)
app.include_router(protection_profiles_router, prefix="/api/dashboard")
app.include_router(billing_webhook_router, prefix="/api")
app.include_router(notifications_router)
app.include_router(compliance_frameworks_router)
app.include_router(compliance_partner_router)
app.include_router(log_ingest_router)
app.include_router(security_events_router)
app.include_router(metrics_router)

# Newly extracted routers
app.include_router(agent_router)
app.include_router(learning_main_router)
app.include_router(infra_router)

# ============================================================================
# WebSocket
# ============================================================================

@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
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
# Core Endpoints (remain in main.py — need app.state access)
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
    """Health check endpoint — runs all checks in parallel."""
    checks = {"status": "ok"}

    minio_client = get_minio_client()

    async def check_redis():
        try:
            from dashboard_api.shared import get_redis_client
            rc = get_redis_client()
            if rc:
                await rc.ping()
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

# Re-exports for backward compatibility (tests import from main)
from dashboard_api.agent_api import (  # noqa: F401, E402
    CheckinRequest, IncidentReport, DriftReport, OrderRequest,
    OrderAcknowledgement, EvidenceSubmission, PatternReportInput,
    PatternStatsRequest, PatternStatSync, PromotedRuleResponse,
    ExecutionTelemetryInput, L2PlanRequest, ApplianceCheckinRequest,
    submit_evidence, list_evidence, report_incident,
)
from dashboard_api.learning_api_main import (  # noqa: F401, E402
    PromotionReportRequest, PromotionApprovalRequest,
)
from dashboard_api.background_tasks import (  # noqa: F401, E402
    _flywheel_promotion_loop,
)
from dashboard_api.shared import (  # noqa: F401, E402
    get_db, sign_data, get_public_key_hex, get_all_public_keys_hex,
    check_rate_limit, require_appliance_bearer,
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

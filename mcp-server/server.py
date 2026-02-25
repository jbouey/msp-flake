#!/usr/bin/env python3
"""
MCP Server - Central Orchestration Server for MSP Compliance Platform

Receives incidents, uses LLM to select runbooks, manages evidence,
and powers the learning loop (L2 -> L1 promotion).

This is the brain of the MSP Compliance Platform.
"""

import os
import json
import yaml
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any
import asyncio

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator
import redis.asyncio as redis
import aiohttp
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text

# Import our database layer
from database import init_database, get_store, IncidentStore

# ============================================================================
# Configuration
# ============================================================================

REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")  # Required in production
RUNBOOK_DIR = Path(os.getenv("RUNBOOK_DIR", "/var/lib/mcp-server/runbooks"))
EVIDENCE_DIR = Path(os.getenv("EVIDENCE_DIR", "/var/lib/mcp-server/evidence"))
DATABASE_DIR = Path(os.getenv("DATABASE_DIR", "/var/lib/mcp-server"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# OpenAI API (for LLM runbook selection)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")  # Set in production
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

# Rate limiting
RATE_LIMIT_COOLDOWN_SECONDS = 300  # 5 minutes

# PostgreSQL Database URL (for SQLAlchemy async session - used by evidence_chain)
# Constructs async URL from DATABASE_URL environment variable
_base_db_url = os.getenv("DATABASE_URL", "")
if _base_db_url:
    # Convert postgresql:// to postgresql+asyncpg://
    if _base_db_url.startswith("postgresql://"):
        PG_DATABASE_URL = _base_db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif _base_db_url.startswith("postgresql+asyncpg://"):
        PG_DATABASE_URL = _base_db_url
    else:
        PG_DATABASE_URL = f"postgresql+asyncpg://{_base_db_url.split('://', 1)[-1]}"

    # SQLAlchemy async engine and session for routers that need it
    _pg_engine = create_async_engine(PG_DATABASE_URL, echo=False, pool_size=5, max_overflow=10)
    async_session = async_sessionmaker(_pg_engine, class_=AsyncSession, expire_on_commit=False)
else:
    # No database configured - async_session will be None
    _pg_engine = None
    async_session = None

async def get_db():
    """Get database session for SQLAlchemy-based routers."""
    if async_session is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    async with async_session() as session:
        yield session

# Global store reference
store: Optional[IncidentStore] = None

# ============================================================================
# Pydantic Models
# ============================================================================

class IncidentRequest(BaseModel):
    """Incident reported by compliance agent"""
    client_id: str = Field(..., description="Client/site identifier")
    hostname: str = Field(..., description="Hostname of affected system")
    incident_type: str = Field(..., description="Type of incident")
    severity: str = Field(..., description="Severity level")
    details: Dict = Field(default_factory=dict, description="Additional incident details")
    
    @validator('severity')
    def validate_severity(cls, v):
        allowed = ['low', 'medium', 'high', 'critical']
        if v.lower() not in allowed:
            raise ValueError(f'Severity must be one of {allowed}')
        return v.lower()

class RunbookSelection(BaseModel):
    """LLM-selected runbook for incident"""
    runbook_id: str = Field(..., description="Selected runbook ID")
    confidence: float = Field(..., ge=0.0, le=1.0, description="LLM confidence score")
    reasoning: str = Field(..., description="Why this runbook was selected")
    parameters: Dict = Field(default_factory=dict, description="Runbook-specific parameters")

class RemediationOrder(BaseModel):
    """Order sent to compliance agent for execution"""
    order_id: str = Field(..., description="Unique order identifier")
    runbook_id: str = Field(..., description="Runbook to execute")
    runbook_content: Dict = Field(..., description="Full runbook definition")
    parameters: Dict = Field(default_factory=dict, description="Execution parameters")
    expires_at: str = Field(..., description="Order expiration time (ISO format)")
    
class EvidenceBundle(BaseModel):
    """Evidence bundle from completed remediation"""
    bundle_id: str
    order_id: str
    client_id: str
    hostname: str
    runbook_id: str
    executed_at: str
    duration_seconds: int
    outcome: str
    evidence_data: Dict
    
# ============================================================================
# FastAPI App
# ============================================================================

app = FastAPI(
    title="MCP Server",
    description="MSP Compliance Platform - Central Orchestration Server",
    version="1.0.0"
)

# Include routers from central-command backend
import sys
# Try both possible paths (local dev and container deployment)
_backend_paths = [
    os.path.join(os.path.dirname(__file__), "central-command", "backend"),
    os.path.join(os.path.dirname(__file__), "dashboard_api"),
]
for _backend_path in _backend_paths:
    if os.path.isdir(_backend_path) and _backend_path not in sys.path:
        sys.path.insert(0, _backend_path)
        break

# Add rate limiting middleware for DoS/brute force protection
try:
    from dashboard_api.rate_limiter import RateLimitMiddleware, RateLimiter
    # Create rate limiter with appropriate limits
    # Auth endpoints: 5 attempts per minute (handled internally with "auth:" prefix)
    # Standard rate: 60 req/min, 1000 req/hour, burst: 10
    _rate_limiter = RateLimiter(
        requests_per_minute=60,
        requests_per_hour=1000,
        burst_limit=10
    )
    app.add_middleware(RateLimitMiddleware, rate_limiter=_rate_limiter)
    print("✓ Rate limiting middleware enabled (60 req/min, 10 burst)")
except ImportError as e:
    print(f"⚠ Rate limiting middleware not available - continuing without rate limits: {e}")

try:
    from dashboard_api.routes import router as dashboard_router, auth_router
    from dashboard_api.portal import router as portal_router
    from dashboard_api.sites import router as sites_router, orders_router, appliances_router, alerts_router
    from dashboard_api.partners import router as partners_router
    from dashboard_api.partner_auth import public_router as partner_auth_router, admin_router as partner_admin_router
    from dashboard_api.discovery import router as discovery_router
    from dashboard_api.provisioning import router as provisioning_router
    from dashboard_api.runbook_config import router as runbook_config_router
    from dashboard_api.sensors import router as sensors_router
    from dashboard_api.notifications import router as notifications_router, escalations_router
    from dashboard_api.users import router as users_router
    from dashboard_api.frameworks import router as frameworks_router
    from dashboard_api.integrations.api import router as integrations_router
    from dashboard_api.evidence_chain import router as evidence_chain_router
    from dashboard_api.fleet_updates import router as fleet_updates_router
    from dashboard_api.client_portal import public_router as client_auth_router, auth_router as client_portal_router
    from dashboard_api.hipaa_modules import router as hipaa_modules_router
    from dashboard_api.compliance_frameworks import router as compliance_frameworks_router, partner_router as partner_compliance_router
    from dashboard_api.companion import router as companion_router
    app.include_router(dashboard_router)
    app.include_router(auth_router)  # Admin authentication endpoints
    app.include_router(users_router)  # User management (RBAC)
    app.include_router(frameworks_router)  # Multi-framework compliance
    app.include_router(portal_router)
    app.include_router(sites_router)
    app.include_router(orders_router)  # Order lifecycle (acknowledge/complete)
    app.include_router(appliances_router)  # Smart appliance checkin with deduplication
    app.include_router(alerts_router)  # Email alerts for L3 escalations
    app.include_router(partners_router)
    app.include_router(partner_auth_router, prefix="/api")  # Partner OAuth login
    app.include_router(partner_admin_router, prefix="/api")  # Partner admin endpoints
    app.include_router(discovery_router)
    app.include_router(provisioning_router)
    app.include_router(runbook_config_router)  # Runbook enable/disable config
    app.include_router(sensors_router)  # Sensor management for dual-mode architecture
    app.include_router(notifications_router)  # Partner notification settings
    app.include_router(escalations_router)  # Agent L3 escalations to partners
    app.include_router(integrations_router)  # Cloud integrations (AWS, Google, Okta, Azure)
    app.include_router(evidence_chain_router)  # Evidence chain with Ed25519 signature verification
    app.include_router(fleet_updates_router)  # Fleet updates and rollout management
    app.include_router(client_auth_router, prefix="/api")  # Client portal auth (magic link, login)
    app.include_router(client_portal_router, prefix="/api")  # Client portal endpoints
    app.include_router(hipaa_modules_router, prefix="/api")  # HIPAA compliance modules
    app.include_router(compliance_frameworks_router)  # Multi-framework compliance management
    app.include_router(partner_compliance_router)  # Partner compliance settings
    app.include_router(companion_router, prefix="/api")  # Compliance Companion portal
    print("✓ Included central-command routers (dashboard, portal, sites, orders, appliances, alerts, partners, discovery, provisioning, runbook_config, sensors, notifications, escalations, users, frameworks, integrations, evidence_chain, fleet_updates, client_portal, compliance_frameworks, companion)")
except ImportError as e:
    print(f"⚠ Could not import central-command routers: {e}")

# Global Redis connection
redis_client: Optional[redis.Redis] = None

# ============================================================================
# Startup / Shutdown
# ============================================================================

@app.on_event("startup")
async def startup():
    """Initialize Redis connection and database"""
    global redis_client, store

    # Initialize Redis
    redis_client = await redis.from_url(
        f"redis://{REDIS_HOST}:{REDIS_PORT}",
        password=REDIS_PASSWORD,
        decode_responses=True
    )
    print(f"✓ Connected to Redis at {REDIS_HOST}:{REDIS_PORT}")

    # Ensure directories exist
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    DATABASE_DIR.mkdir(parents=True, exist_ok=True)

    # Initialize database
    db_path = DATABASE_DIR / "mcp.db"
    store = init_database(f"sqlite:///{db_path}")
    print(f"✓ Database initialized: {db_path}")

    print(f"✓ Evidence directory: {EVIDENCE_DIR}")
    print(f"✓ Runbook directory: {RUNBOOK_DIR}")
    print(f"✓ MCP Server started")

@app.on_event("shutdown")
async def shutdown():
    """Close Redis connection"""
    if redis_client:
        await redis_client.close()
    print("✓ MCP Server stopped")

# ============================================================================
# Runbook Management
# ============================================================================

def load_runbooks() -> Dict[str, Dict]:
    """Load all runbooks from disk"""
    runbooks = {}
    
    if not RUNBOOK_DIR.exists():
        print(f"⚠ Runbook directory not found: {RUNBOOK_DIR}")
        return runbooks
    
    for runbook_file in RUNBOOK_DIR.glob("*.yaml"):
        try:
            with open(runbook_file, 'r') as f:
                runbook = yaml.safe_load(f)
                runbook_id = runbook.get('id')
                if runbook_id:
                    runbooks[runbook_id] = runbook
                    print(f"  Loaded runbook: {runbook_id}")
        except Exception as e:
            print(f"✗ Failed to load {runbook_file}: {e}")
    
    return runbooks

# Load runbooks at startup
RUNBOOKS = load_runbooks()

# ============================================================================
# Rate Limiting
# ============================================================================

async def check_rate_limit(client_id: str, hostname: str, action: str) -> bool:
    """Check if action is rate limited"""
    rate_key = f"rate:{client_id}:{hostname}:{action}"
    
    if await redis_client.exists(rate_key):
        return False  # Rate limited
    
    # Set cooldown
    await redis_client.setex(rate_key, RATE_LIMIT_COOLDOWN_SECONDS, "1")
    return True  # Allowed

async def get_remaining_cooldown(client_id: str, hostname: str, action: str) -> int:
    """Get remaining cooldown seconds"""
    rate_key = f"rate:{client_id}:{hostname}:{action}"
    ttl = await redis_client.ttl(rate_key)
    return max(0, ttl)

# ============================================================================
# LLM Integration
# ============================================================================

async def select_runbook_with_llm(incident: IncidentRequest) -> RunbookSelection:
    """Use GPT-4o to select appropriate runbook for incident"""
    
    # Build prompt with incident details and available runbooks
    runbook_descriptions = "\n".join([
        f"- {rb_id}: {rb.get('name', '')} - {rb.get('description', '')}"
        for rb_id, rb in RUNBOOKS.items()
    ])
    
    prompt = f"""You are an expert system administrator tasked with selecting the best remediation runbook for a compliance incident.

Incident Details:
- Type: {incident.incident_type}
- Severity: {incident.severity}
- Hostname: {incident.hostname}
- Client: {incident.client_id}
- Details: {json.dumps(incident.details, indent=2)}

Available Runbooks:
{runbook_descriptions}

Analyze the incident and select the SINGLE most appropriate runbook. Consider:
1. Incident type match with runbook category
2. Severity alignment
3. Likelihood of success
4. Risk of making things worse

Respond with JSON only (no markdown, no explanations):
{{
  "runbook_id": "<runbook_id>",
  "confidence": <0.0-1.0>,
  "reasoning": "<brief explanation>",
  "parameters": {{"key": "value"}}
}}
"""

    if not OPENAI_API_KEY:
        # Fallback: Simple rule-based selection for testing
        print("⚠ No OpenAI API key - using fallback rule-based selection")
        return await select_runbook_fallback(incident)
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": OPENAI_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 300,
                    "temperature": 0.1
                }
            ) as resp:
                if resp.status != 200:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="LLM service unavailable"
                    )
                
                result = await resp.json()
                llm_response = result['choices'][0]['message']['content'].strip()
                
                # Parse JSON response
                selection = json.loads(llm_response)
                return RunbookSelection(**selection)
                
    except Exception as e:
        print(f"✗ LLM selection failed: {e}")
        return await select_runbook_fallback(incident)

async def select_runbook_fallback(incident: IncidentRequest) -> RunbookSelection:
    """Simple rule-based runbook selection (no LLM)"""
    
    # Map incident types to runbooks
    incident_type_lower = incident.incident_type.lower()
    
    mapping = {
        'backup': 'RB-BACKUP-001',
        'certificate': 'RB-CERT-001',
        'cert': 'RB-CERT-001',
        'disk': 'RB-DISK-001',
        'storage': 'RB-DISK-001',
        'service': 'RB-SERVICE-001',
        'crash': 'RB-SERVICE-001',
        'drift': 'RB-DRIFT-001',
        'configuration': 'RB-DRIFT-001',
    }
    
    # Find matching runbook
    for keyword, runbook_id in mapping.items():
        if keyword in incident_type_lower:
            return RunbookSelection(
                runbook_id=runbook_id,
                confidence=0.8,
                reasoning=f"Rule-based match: '{keyword}' in incident type",
                parameters={}
            )
    
    # Default fallback
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"No runbook found for incident type: {incident.incident_type}"
    )

# ============================================================================
# API Endpoints
# ============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        await redis_client.ping()
        redis_status = "connected"
    except:
        redis_status = "disconnected"

    # Database status
    db_status = "connected" if store is not None else "disconnected"

    return {
        "status": "healthy",
        "redis": redis_status,
        "database": db_status,
        "runbooks_loaded": len(RUNBOOKS),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.get("/runbooks")
async def list_runbooks():
    """List all available runbooks"""
    return {
        "runbooks": [
            {
                "id": rb_id,
                "name": rb.get("name"),
                "description": rb.get("description"),
                "severity": rb.get("severity"),
                "category": rb.get("category"),
                "hipaa_controls": rb.get("hipaa_controls", [])
            }
            for rb_id, rb in RUNBOOKS.items()
        ],
        "total": len(RUNBOOKS)
    }

@app.get("/runbooks/{runbook_id}")
async def get_runbook(runbook_id: str):
    """Get specific runbook details"""
    if runbook_id not in RUNBOOKS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Runbook not found: {runbook_id}"
        )
    
    return RUNBOOKS[runbook_id]

@app.post("/chat")
async def process_incident(incident: IncidentRequest):
    """
    Main endpoint: Receive incident, select runbook, create remediation order.

    This is the L2 decision path - uses LLM to select runbook.
    Every decision here feeds the learning loop for potential L1 promotion.
    """

    # Check rate limit
    allowed = await check_rate_limit(
        incident.client_id,
        incident.hostname,
        incident.incident_type
    )

    if not allowed:
        remaining = await get_remaining_cooldown(
            incident.client_id,
            incident.hostname,
            incident.incident_type
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limited. Try again in {remaining} seconds."
        )

    # Store incident in database
    db_incident = None
    if store:
        db_incident = store.create_incident(
            site_id=incident.client_id,
            hostname=incident.hostname,
            incident_type=incident.incident_type,
            severity=incident.severity,
            details=incident.details
        )
        print(f"✓ Created incident {db_incident.incident_id}")

    # First, check if we have an L1 rule for this incident type
    resolution_level = "L2"  # Default to LLM-assisted
    if store:
        l1_rules = store.get_rules_for_incident_type(incident.incident_type)
        if l1_rules:
            # We have an L1 rule! Use it directly (no LLM cost)
            rule = l1_rules[0]
            selection = RunbookSelection(
                runbook_id=rule.runbook_id,
                confidence=0.95,
                reasoning=f"L1 rule match: {rule.name}",
                parameters=rule.parameters or {}
            )
            resolution_level = "L1"
            print(f"✓ L1 rule matched: {rule.rule_id}")
        else:
            # No L1 rule - use LLM (L2)
            selection = await select_runbook_with_llm(incident)
    else:
        selection = await select_runbook_with_llm(incident)

    # Verify runbook exists
    if selection.runbook_id not in RUNBOOKS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Selected runbook not found: {selection.runbook_id}"
        )

    # Create remediation order
    order_id = hashlib.sha256(
        f"{incident.client_id}{incident.hostname}{datetime.now(timezone.utc).isoformat()}".encode()
    ).hexdigest()[:16]

    order = RemediationOrder(
        order_id=order_id,
        runbook_id=selection.runbook_id,
        runbook_content=RUNBOOKS[selection.runbook_id],
        parameters=selection.parameters,
        expires_at=(datetime.now(timezone.utc).isoformat())
    )

    # Store order in Redis with incident reference
    order_key = f"order:{incident.client_id}:{order_id}"
    order_data = order.dict()
    order_data["incident_id"] = db_incident.incident_id if db_incident else None
    order_data["resolution_level"] = resolution_level

    await redis_client.setex(
        order_key,
        900,  # 15 minutes TTL
        json.dumps(order_data)
    )

    print(f"✓ Created order {order_id} for {incident.client_id} ({selection.runbook_id}) [{resolution_level}]")

    return {
        "status": "order_created",
        "order_id": order_id,
        "incident_id": db_incident.incident_id if db_incident else None,
        "runbook_id": selection.runbook_id,
        "resolution_level": resolution_level,
        "confidence": selection.confidence,
        "reasoning": selection.reasoning,
        "order": order.dict()
    }

@app.post("/evidence")
async def submit_evidence(evidence: EvidenceBundle):
    """
    Accept evidence bundle from compliance agent.

    This is the completion of the incident lifecycle:
    1. Incident reported -> /chat
    2. Runbook executed by agent
    3. Evidence submitted -> /evidence (here)
    4. Learning engine analyzes result
    5. Patterns are aggregated for L1 promotion
    """

    # Save evidence to disk
    evidence_file = EVIDENCE_DIR / f"{evidence.bundle_id}.json"
    with open(evidence_file, 'w') as f:
        json.dump(evidence.dict(), f, indent=2)

    print(f"✓ Stored evidence bundle: {evidence.bundle_id}")

    # Record execution in database
    execution = None
    if store:
        success = evidence.outcome.lower() in ["success", "resolved", "fixed"]
        execution = store.record_execution(
            runbook_id=evidence.runbook_id,
            site_id=evidence.client_id,
            hostname=evidence.hostname,
            success=success,
            incident_id=evidence.evidence_data.get("incident_id"),
            incident_type=evidence.evidence_data.get("incident_type"),
            duration_seconds=evidence.duration_seconds,
            status="success" if success else "failure",
            evidence_bundle_id=evidence.bundle_id,
            state_before=evidence.evidence_data.get("state_before", {}),
            state_after=evidence.evidence_data.get("state_after", {}),
            state_diff=evidence.evidence_data.get("state_diff", {}),
            executed_steps=evidence.evidence_data.get("executed_steps", []),
            error_message=evidence.evidence_data.get("error_message"),
        )
        print(f"✓ Recorded execution {execution.execution_id} (success={success})")

        # Resolve the incident if execution was successful
        incident_id = evidence.evidence_data.get("incident_id")
        if incident_id and success:
            resolution_level = evidence.evidence_data.get("resolution_level", "L2")
            store.resolve_incident(
                incident_id=incident_id,
                resolution_level=resolution_level,
                runbook_id=evidence.runbook_id,
                evidence_bundle_id=evidence.bundle_id
            )
            print(f"✓ Resolved incident {incident_id} via {resolution_level}")

    return {
        "status": "evidence_received",
        "bundle_id": evidence.bundle_id,
        "execution_id": execution.execution_id if execution else None,
        "stored_at": str(evidence_file),
        "learning_updated": execution is not None
    }

# ============================================================================
# Learning Loop & Rule Distribution Endpoints
# ============================================================================

@app.get("/rules")
async def get_active_rules():
    """
    Get all active L1 rules.

    Agents call this to sync their local L1 rule cache.
    These rules run locally without server calls ($0, <100ms).
    """
    if not store:
        return {"rules": [], "total": 0}

    rules = store.get_active_rules()
    return {
        "rules": [
            {
                "rule_id": r.rule_id,
                "name": r.name,
                "incident_type": r.incident_type,
                "runbook_id": r.runbook_id,
                "match_conditions": r.match_conditions,
                "parameters": r.parameters,
                "hipaa_controls": r.hipaa_controls,
                "version": r.version,
            }
            for r in rules
        ],
        "total": len(rules),
        "synced_at": datetime.now(timezone.utc).isoformat()
    }


@app.get("/learning/status")
async def get_learning_status():
    """Get learning loop statistics for dashboard."""
    if not store:
        return {
            "total_l1_rules": 0,
            "total_l2_decisions_30d": 0,
            "patterns_awaiting_promotion": 0,
            "recently_promoted_count": 0,
            "promotion_success_rate": 0.0,
        }

    return store.get_learning_status()


@app.get("/learning/candidates")
async def get_promotion_candidates(
    min_occurrences: int = 5,
    min_success_rate: float = 90.0
):
    """
    Get patterns eligible for L1 promotion.

    Criteria:
    - min_occurrences: Minimum times the pattern has been seen
    - min_success_rate: Minimum success rate (%)
    """
    if not store:
        return {"candidates": [], "total": 0}

    candidates = store.get_promotion_candidates(min_occurrences, min_success_rate)
    return {
        "candidates": [
            {
                "pattern_id": p.pattern_id,
                "pattern_signature": p.pattern_signature,
                "description": p.description,
                "incident_type": p.incident_type,
                "runbook_id": p.runbook_id,
                "occurrences": p.occurrences,
                "success_rate": p.success_rate,
                "avg_resolution_time_ms": p.avg_resolution_time_ms,
                "proposed_rule": p.proposed_rule,
                "first_seen": p.first_seen.isoformat() if p.first_seen else None,
                "last_seen": p.last_seen.isoformat() if p.last_seen else None,
            }
            for p in candidates
        ],
        "total": len(candidates)
    }


@app.post("/learning/promote/{pattern_id}")
async def promote_pattern(pattern_id: str, promoted_by: str = "admin"):
    """
    Promote a pattern to L1 rule.

    This creates a new deterministic rule from a successful L2 pattern.
    Once promoted, future incidents of this type will be handled locally
    by agents without server calls.
    """
    if not store:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not initialized"
        )

    rule = store.promote_pattern(pattern_id, promoted_by)
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pattern not found: {pattern_id}"
        )

    print(f"✓ Promoted pattern {pattern_id} -> rule {rule.rule_id}")

    return {
        "status": "promoted",
        "pattern_id": pattern_id,
        "new_rule_id": rule.rule_id,
        "message": f"Pattern promoted to L1 rule: {rule.name}"
    }


@app.get("/learning/history")
async def get_promotion_history(limit: int = 20):
    """Get recently promoted patterns."""
    if not store:
        return {"history": [], "total": 0}

    history = store.get_promotion_history(limit)
    return {
        "history": [
            {
                "pattern_id": p.pattern_id,
                "pattern_signature": p.pattern_signature,
                "rule_id": p.promoted_to_rule_id,
                "promoted_at": p.promoted_at.isoformat() if p.promoted_at else None,
            }
            for p in history
        ],
        "total": len(history)
    }


# ============================================================================
# Agent Registration & Health Reporting
# ============================================================================

class AgentCheckin(BaseModel):
    """Health check-in from compliance agent"""
    appliance_id: str
    site_id: str
    hostname: str
    version: Optional[str] = None
    ip_address: Optional[str] = None
    health_metrics: Dict[str, Any] = Field(default_factory=dict)


@app.post("/agent/checkin")
async def agent_checkin(checkin: AgentCheckin):
    """
    Receive health check-in from agent.

    Agents call this periodically to report their status.
    This updates the dashboard's fleet view.

    Returns pending orders for the appliance to execute.
    """
    if store:
        store.register_appliance(
            appliance_id=checkin.appliance_id,
            site_id=checkin.site_id,
            hostname=checkin.hostname,
            version=checkin.version,
            ip_address=checkin.ip_address,
        )
        store.appliance_checkin(checkin.appliance_id, checkin.health_metrics)

    # Query for pending orders from PostgreSQL
    pending_orders = []
    try:
        from fleet import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Get pending orders for this appliance (not expired)
            rows = await conn.fetch("""
                SELECT order_id, order_type, parameters, priority, created_at, expires_at
                FROM admin_orders
                WHERE appliance_id = $1
                AND status = 'pending'
                AND expires_at > NOW()
                ORDER BY priority DESC, created_at ASC
            """, checkin.appliance_id)

            pending_orders = [
                {
                    "order_id": row["order_id"],
                    "order_type": row["order_type"],
                    "parameters": row["parameters"] or {},
                    "priority": row["priority"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
                }
                for row in rows
            ]
    except Exception as e:
        # Log but don't fail the checkin if orders query fails
        print(f"⚠ Failed to query orders for {checkin.appliance_id}: {e}")

    # Fetch Windows targets with credentials for this site
    windows_targets = []
    try:
        import json as json_module
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Get WinRM credentials for this site (stored as JSON in encrypted_data)
            creds = await conn.fetch("""
                SELECT credential_type, credential_name, encrypted_data
                FROM site_credentials
                WHERE site_id = $1
                AND credential_type IN ('winrm', 'domain_admin', 'service_account', 'local_admin')
                ORDER BY created_at DESC
            """, checkin.site_id)

            # Get site internal ID for discovered assets query
            site = await conn.fetchrow("""
                SELECT id FROM sites WHERE site_id = $1
            """, checkin.site_id)

            assets = []
            if site:
                # Get discovered assets with WinRM ports
                assets = await conn.fetch("""
                    SELECT ip_address, hostname, open_ports
                    FROM discovered_assets
                    WHERE site_id = $1
                    AND (
                        5985 = ANY(open_ports) OR
                        5986 = ANY(open_ports) OR
                        asset_type IN ('domain_controller', 'windows_server', 'windows_workstation')
                    )
                    AND monitoring_status = 'monitored'
                """, site['id'])

            # Build windows_targets list
            for cred in creds:
                try:
                    # Decrypt credential data (stored as JSON in encrypted_data)
                    cred_data = json_module.loads(bytes(cred['encrypted_data']).decode())
                    hostname = cred_data.get('host') or cred_data.get('target_host')
                    username = cred_data.get('username', '')
                    password = cred_data.get('password', '')
                    domain = cred_data.get('domain', '')
                    use_ssl = cred_data.get('use_ssl', False)

                    full_username = f"{domain}\\{username}" if domain else username

                    if hostname:
                        # Credential specifies target host
                        windows_targets.append({
                            "hostname": hostname,
                            "username": full_username,
                            "password": password,
                            "use_ssl": use_ssl,
                        })
                    elif assets:
                        # Use discovered assets with this credential
                        for asset in assets:
                            asset_ssl = 5986 in (asset['open_ports'] or [])
                            windows_targets.append({
                                "hostname": str(asset['ip_address']),
                                "username": full_username,
                                "password": password,
                                "use_ssl": asset_ssl,
                            })
                except Exception as e:
                    print(f"⚠ Failed to parse credential: {e}")
                    continue
    except Exception as e:
        print(f"⚠ Failed to fetch Windows targets for {checkin.site_id}: {e}")

    # Fetch Linux targets with SSH credentials for this site
    linux_targets = []
    try:
        import json as json_module
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Get SSH credentials for this site
            linux_creds = await conn.fetch("""
                SELECT credential_type, credential_name, encrypted_data
                FROM site_credentials
                WHERE site_id = $1
                AND credential_type IN ('ssh_password', 'ssh_key')
                ORDER BY created_at DESC
            """, checkin.site_id)

            # Get site internal ID for discovered assets query
            site = await conn.fetchrow("""
                SELECT id FROM sites WHERE site_id = $1
            """, checkin.site_id)

            linux_assets = []
            if site:
                # Get discovered assets with SSH port or linux asset type
                linux_assets = await conn.fetch("""
                    SELECT ip_address, hostname, open_ports
                    FROM discovered_assets
                    WHERE site_id = $1
                    AND (
                        22 = ANY(open_ports) OR
                        asset_type IN ('linux_server', 'unix_server', 'appliance')
                    )
                    AND monitoring_status = 'monitored'
                """, site['id'])

            # Build linux_targets list
            for cred in linux_creds:
                try:
                    cred_data = json_module.loads(bytes(cred['encrypted_data']).decode())
                    hostname = cred_data.get('host') or cred_data.get('target_host')
                    port = cred_data.get('port', 22)
                    username = cred_data.get('username', 'root')
                    password = cred_data.get('password')
                    private_key = cred_data.get('private_key')
                    distro = cred_data.get('distro')

                    target_entry = {
                        "hostname": hostname,
                        "port": port,
                        "username": username,
                    }
                    if password:
                        target_entry["password"] = password
                    if private_key:
                        target_entry["private_key"] = private_key
                    if distro:
                        target_entry["distro"] = distro

                    if hostname:
                        linux_targets.append(target_entry)
                    elif linux_assets:
                        # Use discovered assets with this credential
                        for asset in linux_assets:
                            linux_targets.append({
                                **target_entry,
                                "hostname": str(asset['ip_address']),
                            })
                except Exception as e:
                    print(f"⚠ Failed to parse Linux credential: {e}")
                    continue
    except Exception as e:
        print(f"⚠ Failed to fetch Linux targets for {checkin.site_id}: {e}")

    return {
        "status": "ok",
        "server_time": datetime.now(timezone.utc).isoformat(),
        "orders": pending_orders,
        "windows_targets": windows_targets,
        "linux_targets": linux_targets,
    }


@app.get("/stats")
async def get_global_stats():
    """Get aggregate statistics for dashboard."""
    if not store:
        return {
            "total_clients": 0,
            "total_appliances": 0,
            "online_appliances": 0,
            "incidents_24h": 0,
            "l1_resolution_rate": 0.0,
        }

    return store.get_global_stats()


# ============================================================================
# Backup Status Endpoint
# ============================================================================

BACKUP_STATUS_FILE = Path("/opt/backups/status/latest.json")


@app.get("/api/backup/status")
async def get_backup_status():
    """
    Get current backup status for dashboard.

    Returns latest backup information including:
    - Last successful backup timestamp
    - Repository health status
    - Storage usage
    - Recent backup history
    """
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
            "storage_used_mb": status_data.get("storage_used_mb", 0),
            "total_snapshots": status_data.get("total_snapshots", 0),
            "repository": status_data.get("repository"),
            "retention": status_data.get("retention"),
            "last_error": status_data.get("error"),
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to read backup status: {str(e)}",
            "last_backup": None,
        }


@app.get("/api/backup/snapshots")
async def list_backup_snapshots():
    """
    List available backup snapshots.

    Returns list of snapshots from Restic repository.
    """
    import subprocess

    try:
        # Run restic snapshots command
        result = subprocess.run(
            [
                "/root/.nix-profile/bin/restic",
                "-r", "sftp:u526501@u526501.your-storagebox.de:backups",
                "-o", "sftp.command=ssh storagebox -s sftp",
                "--password-file", "/root/.restic-password",
                "snapshots", "--json"
            ],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "HOME": "/root"}
        )

        if result.returncode != 0:
            return {
                "status": "error",
                "message": f"Restic failed: {result.stderr}",
                "snapshots": []
            }

        snapshots = json.loads(result.stdout) if result.stdout else []

        return {
            "status": "ok",
            "total": len(snapshots),
            "snapshots": [
                {
                    "id": s.get("short_id", s.get("id", "")[:8]),
                    "time": s.get("time"),
                    "hostname": s.get("hostname"),
                    "tags": s.get("tags", []),
                    "paths": s.get("paths", []),
                }
                for s in snapshots[-20:]  # Return last 20 snapshots
            ]
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "message": "Restic command timed out",
            "snapshots": []
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to list snapshots: {str(e)}",
            "snapshots": []
        }


# ============================================================================
# Agent Sync Endpoint
# ============================================================================

@app.get("/agent/sync")
async def agent_sync(site_id: Optional[str] = None, current_rules_version: Optional[str] = None):
    """
    Full sync endpoint for agents.

    Returns:
        - All active L1 rules
        - Current configuration
        - Server timestamp

    Agents should call this periodically (default: every hour) to sync their
    local rule cache. The rules_version field can be used for quick change
    detection to avoid re-parsing rules if nothing changed.
    """
    from agent_sync import build_sync_response, compute_rules_version, get_rules_for_agent

    rules = get_rules_for_agent(store, site_id)
    rules_version = compute_rules_version(rules)

    # If agent already has current version, return minimal response
    if current_rules_version and current_rules_version == rules_version:
        return {
            "server_time": datetime.now(timezone.utc).isoformat(),
            "rules_version": rules_version,
            "rules_changed": False,
            "message": "Rules are up to date"
        }

    # Return full sync response
    response = build_sync_response(store, site_id)
    return {
        "server_time": response.server_time,
        "rules": [r.dict() for r in response.rules],
        "rules_version": response.rules_version,
        "rules_changed": True,
        "config": response.config.dict(),
        "message": f"Synced {len(response.rules)} L1 rules"
    }


# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    
    host = os.getenv("MCP_API_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_API_PORT", "8000"))
    
    print(f"Starting MCP Server on {host}:{port}...")
    
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=LOG_LEVEL.lower()
    )

#!/usr/bin/env python3
"""
MCP Server - Central Control Plane for MSP Compliance Platform
Receives incidents, uses LLM to select runbooks, manages evidence
"""

import os
import json
import yaml
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import asyncio

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator
import redis.asyncio as redis
import aiohttp

# ============================================================================
# Configuration
# ============================================================================

REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "mcp-redis-password")
RUNBOOK_DIR = Path(os.getenv("RUNBOOK_DIR", "/var/lib/mcp-server/runbooks"))
EVIDENCE_DIR = Path(os.getenv("EVIDENCE_DIR", "/var/lib/mcp-server/evidence"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# OpenAI API (for LLM runbook selection)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")  # Set in production
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

# Rate limiting
RATE_LIMIT_COOLDOWN_SECONDS = 300  # 5 minutes

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
    description="MSP Compliance Platform - Central Control Plane",
    version="1.0.0"
)

# Global Redis connection
redis_client: Optional[redis.Redis] = None

# ============================================================================
# Startup / Shutdown
# ============================================================================

@app.on_event("startup")
async def startup():
    """Initialize Redis connection"""
    global redis_client
    redis_client = await redis.from_url(
        f"redis://{REDIS_HOST}:{REDIS_PORT}",
        password=REDIS_PASSWORD,
        decode_responses=True
    )
    print(f"✓ Connected to Redis at {REDIS_HOST}:{REDIS_PORT}")
    
    # Ensure directories exist
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
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
    
    return {
        "status": "healthy",
        "redis": redis_status,
        "runbooks_loaded": len(RUNBOOKS),
        "timestamp": datetime.utcnow().isoformat()
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
    Main endpoint: Receive incident, select runbook, create remediation order
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
    
    # Use LLM to select runbook
    selection = await select_runbook_with_llm(incident)
    
    # Verify runbook exists
    if selection.runbook_id not in RUNBOOKS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Selected runbook not found: {selection.runbook_id}"
        )
    
    # Create remediation order
    order_id = hashlib.sha256(
        f"{incident.client_id}{incident.hostname}{datetime.utcnow().isoformat()}".encode()
    ).hexdigest()[:16]
    
    order = RemediationOrder(
        order_id=order_id,
        runbook_id=selection.runbook_id,
        runbook_content=RUNBOOKS[selection.runbook_id],
        parameters=selection.parameters,
        expires_at=(datetime.utcnow().isoformat())
    )
    
    # Store order in Redis
    order_key = f"order:{incident.client_id}:{order_id}"
    await redis_client.setex(
        order_key,
        900,  # 15 minutes TTL
        order.json()
    )
    
    print(f"✓ Created order {order_id} for {incident.client_id} ({selection.runbook_id})")
    
    return {
        "status": "order_created",
        "order_id": order_id,
        "runbook_id": selection.runbook_id,
        "confidence": selection.confidence,
        "reasoning": selection.reasoning,
        "order": order.dict()
    }

@app.post("/evidence")
async def submit_evidence(evidence: EvidenceBundle):
    """Accept evidence bundle from compliance agent"""
    
    # Save evidence to disk
    evidence_file = EVIDENCE_DIR / f"{evidence.bundle_id}.json"
    with open(evidence_file, 'w') as f:
        json.dump(evidence.dict(), f, indent=2)
    
    print(f"✓ Stored evidence bundle: {evidence.bundle_id}")
    
    return {
        "status": "evidence_received",
        "bundle_id": evidence.bundle_id,
        "stored_at": str(evidence_file)
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

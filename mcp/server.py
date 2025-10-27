"""
MCP Server - Orchestrates planner, executor, and guardrails
Main API server for the MSP automation platform
"""
import os
from fastapi import FastAPI, HTTPException, Body
from datetime import datetime
from typing import Dict, Optional
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from planner import RunbookPlanner
from executor import RunbookExecutor
from guardrails.validation import validate_action_params
from guardrails.rate_limits import AdaptiveRateLimiter


# Configuration
REDIS_URL = os.getenv("REDIS_URL", "")
ENABLE_RATE_LIMITING = os.getenv("ENABLE_RATE_LIMITING", "true").lower() == "true"
ENABLE_GUARDRAILS = os.getenv("ENABLE_GUARDRAILS", "true").lower() == "true"


# Initialize components
app = FastAPI(title="MSP Automation Platform - MCP Server")
planner = RunbookPlanner()
executor = RunbookExecutor()

# Rate limiter (with Redis if available)
try:
    if REDIS_URL and ENABLE_RATE_LIMITING:
        import redis
        redis_client = redis.from_url(REDIS_URL)
        rate_limiter = AdaptiveRateLimiter(redis_client)
        print(f"[mcp] Rate limiter initialized with Redis: {REDIS_URL}")
    else:
        rate_limiter = AdaptiveRateLimiter()
        print(f"[mcp] Rate limiter initialized (local mode)")
except Exception as e:
    print(f"[mcp] Rate limiter fallback to local mode: {e}")
    rate_limiter = AdaptiveRateLimiter()


@app.get("/")
def root():
    """API info"""
    return {
        "service": "MSP Automation Platform",
        "component": "MCP Server",
        "version": "1.0.0",
        "endpoints": {
            "/diagnose": "Analyze incident and select runbook",
            "/remediate": "Execute runbook with guardrails",
            "/execute": "Direct runbook execution",
            "/health": "Health check",
            "/status": "System status"
        }
    }


@app.post("/diagnose")
def diagnose(incident: Dict = Body(...)):
    """
    Analyze incident and select appropriate runbook

    Request body:
    {
        "snippet": "error log excerpt",
        "meta": {
            "hostname": "server01",
            "logfile": "/var/log/app.log",
            "timestamp": 1234567890,
            "client_id": "clinic-001"
        }
    }

    Returns:
    {
        "runbook_id": "RB-BACKUP-001",
        "action": "execute_runbook",
        "confidence": 0.95,
        "reasoning": "...",
        "params": {}
    }
    """
    print(f"\n[{datetime.now()}] DIAGNOSE REQUEST from {incident.get('meta', {}).get('hostname', 'unknown')}")

    try:
        # Use planner to select runbook
        selection = planner.select_runbook(incident)

        if not selection:
            return {
                "action": "escalate",
                "reason": "No runbook selection available"
            }

        # Check if runbook was selected
        if selection.get("escalate"):
            return {
                "action": "escalate",
                "reason": selection.get("reasoning"),
                "confidence": selection.get("confidence", 0.0)
            }

        # Extract metadata
        meta = incident.get("meta", {})
        client_id = meta.get("client_id", "unknown")
        hostname = meta.get("hostname", "unknown")
        runbook_id = selection.get("runbook_id")

        # Check rate limits (if enabled)
        if ENABLE_RATE_LIMITING and runbook_id:
            rate_check = rate_limiter.check_and_set(client_id, hostname, runbook_id)

            if not rate_check["allowed"]:
                return {
                    "action": "cooldown_active",
                    "runbook_id": runbook_id,
                    "reason": rate_check["reason"],
                    "retry_after_seconds": rate_check["retry_after_seconds"]
                }

        # Return runbook selection
        return {
            "runbook_id": runbook_id,
            "action": "execute_runbook",
            "confidence": selection.get("confidence"),
            "reasoning": selection.get("reasoning"),
            "params": selection.get("params", {}),
            "fallback": selection.get("fallback", False)
        }

    except Exception as e:
        print(f"[mcp] Diagnose error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/remediate")
def remediate(request: Dict = Body(...)):
    """
    Execute remediation action (legacy endpoint for compatibility)

    Request body:
    {
        "action": "execute_runbook",
        "runbook_id": "RB-BACKUP-001",
        "params": {},
        "meta": {
            "hostname": "server01",
            "client_id": "clinic-001"
        }
    }
    """
    print(f"\n[{datetime.now()}] REMEDIATION TRIGGERED")

    try:
        action = request.get("action")
        runbook_id = request.get("runbook_id")
        params = request.get("params", {})
        meta = request.get("meta", {})

        if action == "execute_runbook" and runbook_id:
            # Redirect to /execute endpoint
            return execute_runbook_endpoint({
                "runbook_id": runbook_id,
                "params": params,
                "client_id": meta.get("client_id", "unknown"),
                "hostname": meta.get("hostname", "unknown")
            })
        else:
            return {
                "status": "acknowledged",
                "action": action,
                "timestamp": datetime.now().isoformat()
            }

    except Exception as e:
        print(f"[mcp] Remediation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/execute")
def execute_runbook_endpoint(request: Dict = Body(...)):
    """
    Execute a runbook with full guardrails

    Request body:
    {
        "runbook_id": "RB-BACKUP-001",
        "params": {},
        "client_id": "clinic-001",
        "hostname": "server01"
    }

    Returns:
    {
        "execution_id": "EXE-20251024-123456-RB-BACKUP-001",
        "status": "success",
        "duration_seconds": 12.5,
        "evidence_bundle_id": "EB-20251024-123456-RB-BACKUP-001",
        "evidence_bundle_hash": "sha256:..."
    }
    """
    print(f"\n[{datetime.now()}] EXECUTE REQUEST")

    try:
        runbook_id = request.get("runbook_id")
        params = request.get("params", {})
        client_id = request.get("client_id", "unknown")
        hostname = request.get("hostname", "unknown")

        if not runbook_id:
            raise HTTPException(status_code=400, detail="runbook_id required")

        # Validate parameters (if guardrails enabled)
        if ENABLE_GUARDRAILS and params:
            # Note: Full parameter validation would happen in executor
            # This is a basic check at the API level
            print(f"[mcp] Guardrails enabled - parameters will be validated")

        # Execute runbook
        result = executor.execute_runbook(runbook_id, params)

        # Record result for adaptive rate limiting
        if ENABLE_RATE_LIMITING:
            success = result.get("status") == "success"
            rate_limiter.record_execution_result(
                client_id, hostname, runbook_id, success
            )

        return result

    except Exception as e:
        print(f"[mcp] Execution error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "MCP Server",
        "timestamp": datetime.now().isoformat()
    }


@app.get("/status")
def status():
    """Detailed status information"""
    return {
        "ok": True,
        "service": "MCP Server",
        "version": "1.0.0",
        "components": {
            "planner": "initialized",
            "executor": "initialized",
            "rate_limiter": "enabled" if ENABLE_RATE_LIMITING else "disabled",
            "guardrails": "enabled" if ENABLE_GUARDRAILS else "disabled"
        },
        "config": {
            "redis_connected": bool(REDIS_URL),
            "runbooks_available": len(planner.available_runbooks)
        },
        "timestamp": datetime.now().isoformat()
    }


# Main entry point
if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("MCP_PORT", "8000"))
    host = os.getenv("MCP_HOST", "0.0.0.0")

    print(f"""
╔═══════════════════════════════════════════════════════════╗
║   MSP Automation Platform - MCP Server                   ║
║   HIPAA-Compliant Infrastructure Automation              ║
╠═══════════════════════════════════════════════════════════╣
║   Planner:        LLM-driven runbook selection           ║
║   Executor:       Structured remediation with evidence   ║
║   Guardrails:     {"✅ ENABLED" if ENABLE_GUARDRAILS else "⚠️  DISABLED"}                               ║
║   Rate Limiting:  {"✅ ENABLED" if ENABLE_RATE_LIMITING else "⚠️  DISABLED"}                               ║
╠═══════════════════════════════════════════════════════════╣
║   Listening:      {host}:{port}                        ║
╚═══════════════════════════════════════════════════════════╝
    """)

    uvicorn.run(app, host=host, port=port)

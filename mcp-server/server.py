"""
MCP Server - Main FastAPI server for incident processing

Architecture:
1. Receives incidents via /chat endpoint
2. Passes to Planner for runbook selection (LLM)
3. Passes to Executor for runbook execution
4. Returns result with evidence bundle

This is the orchestration layer that connects:
- Event queue → Planner → Executor → Evidence writer

HIPAA Compliance:
- All requests/responses logged (§164.312(b))
- Rate limiting prevents abuse
- API key authentication
- No PHI processing
"""

from fastapi import FastAPI, HTTPException, Depends, Header, status
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Dict, Optional, List
from datetime import datetime
import logging
import asyncio
import json

from planner import Planner, Incident, RunbookSelection
from executor import Executor, ExecutionResult
from guardrails import RateLimiter, InputValidator, CircuitBreaker

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Pydantic models for API
class IncidentRequest(BaseModel):
    """Request model for /chat endpoint"""
    client_id: str = Field(..., description="Client identifier")
    hostname: str = Field(..., description="Hostname where incident occurred")
    incident_type: str = Field(..., description="Type of incident")
    severity: str = Field(..., description="Severity: critical, high, medium, low")
    details: Dict = Field(default_factory=dict, description="Additional incident details")
    metadata: Dict = Field(default_factory=dict, description="System metadata")


class RemediationResponse(BaseModel):
    """Response model for /chat endpoint"""
    status: str = Field(..., description="Status: success, failed, rate_limited, requires_approval")
    incident_id: str = Field(..., description="Unique incident ID")
    runbook_id: Optional[str] = Field(None, description="Selected runbook ID")
    confidence: Optional[float] = Field(None, description="Planner confidence score")
    execution_result: Optional[Dict] = Field(None, description="Execution result details")
    evidence_bundle_id: Optional[str] = Field(None, description="Evidence bundle ID")
    message: str = Field(..., description="Human-readable status message")
    requires_human_approval: bool = Field(default=False, description="Whether human approval needed")


# Initialize FastAPI app
app = FastAPI(
    title="MSP MCP Server",
    description="Model Context Protocol server for HIPAA-compliant infrastructure automation",
    version="1.0.0"
)

# Add CORS middleware (configure for production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API key security
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


# Global instances
planner: Optional[Planner] = None
executor: Optional[Executor] = None
rate_limiter: Optional[RateLimiter] = None
input_validator: Optional[InputValidator] = None
circuit_breaker: Optional[CircuitBreaker] = None


@app.on_event("startup")
async def startup_event():
    """Initialize components on startup"""
    global planner, executor, rate_limiter, input_validator, circuit_breaker

    logger.info("Starting MCP Server...")

    # Initialize planner
    planner = Planner(
        runbooks_dir="./runbooks",
        model="gpt-4o",
        temperature=0.1
    )
    logger.info("Planner initialized")

    # Initialize executor
    executor = Executor(
        runbooks_dir="./runbooks",
        scripts_dir="./scripts"
    )
    logger.info("Executor initialized")

    # Initialize guardrails
    rate_limiter = RateLimiter(redis_url="redis://localhost:6379")
    input_validator = InputValidator()
    circuit_breaker = CircuitBreaker(failure_threshold=5, timeout_seconds=60)
    logger.info("Guardrails initialized")

    logger.info("MCP Server started successfully")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("Shutting down MCP Server...")


def verify_api_key(api_key: str = Depends(api_key_header)) -> str:
    """Verify API key authentication"""
    # TODO: Implement proper API key validation from secrets manager
    # For now, accept any non-empty key (development only)
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key"
        )

    # In production, validate against database/secrets manager
    # valid_keys = await secrets_manager.get_valid_api_keys()
    # if api_key not in valid_keys:
    #     raise HTTPException(status_code=401, detail="Invalid API key")

    return api_key


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "components": {
            "planner": "ready" if planner else "not_initialized",
            "executor": "ready" if executor else "not_initialized",
            "rate_limiter": "ready" if rate_limiter else "not_initialized"
        }
    }


@app.post("/chat", response_model=RemediationResponse)
async def process_incident(
    incident_request: IncidentRequest,
    api_key: str = Depends(verify_api_key)
):
    """
    Main endpoint for incident processing

    Flow:
    1. Validate input
    2. Check rate limits
    3. Check circuit breaker
    4. Plan: Select runbook via LLM
    5. Execute: Run runbook steps
    6. Generate evidence bundle
    7. Return result

    HIPAA Controls:
    - §164.312(b): Audit trail logged
    - §164.308(a)(1)(ii)(D): Automated system activity review
    """

    # Generate incident ID
    incident_id = f"INC-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{incident_request.client_id}"

    logger.info(f"Processing incident {incident_id}: {incident_request.incident_type}")

    try:
        # Step 1: Validate input
        validation_result = input_validator.validate_incident(incident_request.dict())
        if not validation_result.is_valid:
            logger.warning(f"Invalid incident: {validation_result.errors}")
            return RemediationResponse(
                status="failed",
                incident_id=incident_id,
                message=f"Validation failed: {validation_result.errors}",
                requires_human_approval=False
            )

        # Step 2: Check rate limits
        rate_check = await rate_limiter.check_rate_limit(
            client_id=incident_request.client_id,
            hostname=incident_request.hostname,
            action="remediation"
        )

        if not rate_check.allowed:
            logger.warning(f"Rate limited: {incident_request.client_id}/{incident_request.hostname}")
            return RemediationResponse(
                status="rate_limited",
                incident_id=incident_id,
                message=f"Rate limited. Retry after {rate_check.retry_after_seconds}s",
                requires_human_approval=False
            )

        # Step 3: Check circuit breaker
        if circuit_breaker.is_open():
            logger.error("Circuit breaker is open - too many recent failures")
            return RemediationResponse(
                status="failed",
                incident_id=incident_id,
                message="Service temporarily unavailable due to repeated failures",
                requires_human_approval=True
            )

        # Step 4: Planning phase - Select runbook
        incident = Incident(
            client_id=incident_request.client_id,
            hostname=incident_request.hostname,
            incident_type=incident_request.incident_type,
            severity=incident_request.severity,
            timestamp=datetime.utcnow().isoformat(),
            details=incident_request.details,
            metadata=incident_request.metadata
        )

        try:
            selection: RunbookSelection = await planner.select_runbook(incident)

            logger.info(
                f"Runbook selected: {selection.runbook_id} "
                f"(confidence: {selection.confidence:.2%})"
            )

            # Check if human approval required
            if selection.requires_human_approval:
                logger.info(f"Human approval required for {incident_id}")
                return RemediationResponse(
                    status="requires_approval",
                    incident_id=incident_id,
                    runbook_id=selection.runbook_id,
                    confidence=selection.confidence,
                    message=f"Runbook {selection.runbook_id} requires human approval",
                    requires_human_approval=True
                )

        except Exception as e:
            logger.error(f"Planning failed: {e}")
            circuit_breaker.record_failure()
            return RemediationResponse(
                status="failed",
                incident_id=incident_id,
                message=f"Planning failed: {str(e)}",
                requires_human_approval=True
            )

        # Step 5: Execution phase - Run runbook
        try:
            execution_result: ExecutionResult = await executor.execute_runbook(
                runbook_id=selection.runbook_id,
                incident=incident,
                incident_id=incident_id
            )

            logger.info(
                f"Execution completed: {execution_result.status} "
                f"({execution_result.steps_completed}/{execution_result.total_steps} steps)"
            )

            # Record success in circuit breaker
            if execution_result.status == "success":
                circuit_breaker.record_success()
            else:
                circuit_breaker.record_failure()

            # Step 6: Generate evidence bundle (done by executor)
            # Evidence bundle ID is in execution_result.evidence_bundle_id

            # Step 7: Return result
            return RemediationResponse(
                status=execution_result.status,
                incident_id=incident_id,
                runbook_id=selection.runbook_id,
                confidence=selection.confidence,
                execution_result=execution_result.dict(),
                evidence_bundle_id=execution_result.evidence_bundle_id,
                message=f"Remediation {execution_result.status}: {execution_result.summary}",
                requires_human_approval=False
            )

        except Exception as e:
            logger.error(f"Execution failed: {e}")
            circuit_breaker.record_failure()
            return RemediationResponse(
                status="failed",
                incident_id=incident_id,
                runbook_id=selection.runbook_id,
                confidence=selection.confidence,
                message=f"Execution failed: {str(e)}",
                requires_human_approval=True
            )

    except Exception as e:
        logger.error(f"Unexpected error processing incident {incident_id}: {e}")
        return RemediationResponse(
            status="failed",
            incident_id=incident_id,
            message=f"Internal error: {str(e)}",
            requires_human_approval=True
        )


@app.post("/execute/{runbook_id}")
async def execute_runbook_direct(
    runbook_id: str,
    incident_request: IncidentRequest,
    api_key: str = Depends(verify_api_key)
):
    """
    Direct runbook execution (bypasses planning)

    Use this for:
    - Manual remediation
    - Testing specific runbooks
    - Pre-approved actions

    Requires explicit runbook_id, no LLM selection
    """

    incident_id = f"INC-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{incident_request.client_id}"

    logger.info(f"Direct execution requested: {runbook_id}")

    try:
        # Validate runbook exists
        runbook = executor.get_runbook(runbook_id)
        if not runbook:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Runbook {runbook_id} not found"
            )

        # Create incident
        incident = Incident(
            client_id=incident_request.client_id,
            hostname=incident_request.hostname,
            incident_type=incident_request.incident_type,
            severity=incident_request.severity,
            timestamp=datetime.utcnow().isoformat(),
            details=incident_request.details,
            metadata=incident_request.metadata
        )

        # Execute
        execution_result = await executor.execute_runbook(
            runbook_id=runbook_id,
            incident=incident,
            incident_id=incident_id
        )

        return RemediationResponse(
            status=execution_result.status,
            incident_id=incident_id,
            runbook_id=runbook_id,
            confidence=1.0,  # Direct execution = full confidence
            execution_result=execution_result.dict(),
            evidence_bundle_id=execution_result.evidence_bundle_id,
            message=f"Direct execution {execution_result.status}",
            requires_human_approval=False
        )

    except Exception as e:
        logger.error(f"Direct execution failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@app.get("/runbooks")
async def list_runbooks(api_key: str = Depends(verify_api_key)):
    """List all available runbooks"""

    runbooks = []

    for rb_id, rb_data in planner.library.runbooks.items():
        runbooks.append({
            "id": rb_id,
            "name": rb_data['name'],
            "description": rb_data['description'],
            "severity": rb_data['severity'],
            "hipaa_controls": rb_data.get('hipaa_controls', []),
            "auto_fix_enabled": rb_data.get('auto_fix', {}).get('enabled', False),
            "steps_count": len(rb_data.get('steps', []))
        })

    return {
        "runbooks": runbooks,
        "total": len(runbooks)
    }


@app.get("/runbooks/{runbook_id}")
async def get_runbook(
    runbook_id: str,
    api_key: str = Depends(verify_api_key)
):
    """Get details for specific runbook"""

    runbook = planner.get_runbook_metadata(runbook_id)

    if not runbook:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Runbook {runbook_id} not found"
        )

    return runbook


@app.get("/incidents/history/{client_id}")
async def get_incident_history(
    client_id: str,
    days: int = 30,
    api_key: str = Depends(verify_api_key)
):
    """Get incident history for a client"""

    # TODO: Implement incident history retrieval from database
    # For now, return placeholder

    return {
        "client_id": client_id,
        "days": days,
        "incidents": [],
        "message": "History retrieval not yet implemented"
    }


@app.get("/evidence/{evidence_bundle_id}")
async def get_evidence_bundle(
    evidence_bundle_id: str,
    api_key: str = Depends(verify_api_key)
):
    """Retrieve evidence bundle by ID"""

    # TODO: Implement evidence bundle retrieval from WORM storage
    # For now, return placeholder

    return {
        "evidence_bundle_id": evidence_bundle_id,
        "message": "Evidence retrieval not yet implemented"
    }


# Development/testing endpoints
@app.get("/debug/rate-limits/{client_id}")
async def debug_rate_limits(
    client_id: str,
    api_key: str = Depends(verify_api_key)
):
    """Debug endpoint to check rate limit status"""

    return await rate_limiter.get_rate_limit_status(client_id)


@app.post("/debug/reset-circuit-breaker")
async def debug_reset_circuit_breaker(api_key: str = Depends(verify_api_key)):
    """Debug endpoint to reset circuit breaker"""

    circuit_breaker.reset()
    return {"status": "Circuit breaker reset"}


if __name__ == "__main__":
    import uvicorn

    # Run server
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,  # Auto-reload on code changes (development only)
        log_level="info"
    )

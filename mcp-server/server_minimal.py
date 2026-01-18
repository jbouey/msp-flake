"""
Minimal MCP Server for Demo
This is a simplified version that provides health checks and basic endpoints for the demo
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
import json
from pathlib import Path
from typing import Dict
import os


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        # Security headers
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "frame-ancestors 'none'; "
            "form-action 'self'"
        )
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Strict-Transport-Security"] = "max-age=86400"

        return response


app = FastAPI(title="MSP Compliance Server - Demo Mode")

# Add security headers middleware
app.add_middleware(SecurityHeadersMiddleware)

# Add CORS middleware
# SECURITY: Restrict CORS for demo - use specific origins in production
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,  # Don't use wildcard with credentials
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],  # Be specific
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],  # Be specific
)

# In-memory state for demo
demo_state = {
    "incidents": [],
    "compliance_score": 100.0,
    "controls_passing": 8,
    "controls_total": 8
}

@app.get("/")
async def root():
    return {
        "service": "MSP HIPAA Compliance Platform",
        "version": "0.1.0-demo",
        "mode": "demo",
        "status": "running"
    }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "mcp-server",
        "mode": "demo"
    }

@app.get("/status")
async def get_status():
    """Get current compliance status"""
    return {
        "compliance_score": demo_state["compliance_score"],
        "controls_passing": demo_state["controls_passing"],
        "controls_total": demo_state["controls_total"],
        "incidents_count": len(demo_state["incidents"]),
        "last_incident": demo_state["incidents"][-1] if demo_state["incidents"] else None
    }

@app.get("/incidents")
async def list_incidents():
    """List all incidents"""
    return {
        "incidents": demo_state["incidents"],
        "count": len(demo_state["incidents"])
    }

@app.post("/incidents")
async def create_incident(incident: Dict):
    """Create a new incident (for demo CLI)"""
    demo_state["incidents"].append(incident)

    # Update compliance score based on incident
    if incident.get("resolved", False):
        demo_state["controls_passing"] = min(8, demo_state["controls_passing"] + 1)
    else:
        demo_state["controls_passing"] = max(0, demo_state["controls_passing"] - 1)

    demo_state["compliance_score"] = (demo_state["controls_passing"] / demo_state["controls_total"]) * 100

    return {
        "success": True,
        "incident_id": len(demo_state["incidents"]),
        "compliance_score": demo_state["compliance_score"]
    }

@app.post("/reset")
async def reset_demo():
    """Reset demo state"""
    demo_state["incidents"] = []
    demo_state["compliance_score"] = 100.0
    demo_state["controls_passing"] = 8
    demo_state["controls_total"] = 8

    return {
        "success": True,
        "message": "Demo state reset"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

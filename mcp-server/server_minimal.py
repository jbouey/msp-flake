"""
Minimal MCP Server for Demo
This is a simplified version that provides health checks and basic endpoints for the demo
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import json
from pathlib import Path
from typing import Dict

app = FastAPI(title="MSP Compliance Server - Demo Mode")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

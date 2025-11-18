#!/usr/bin/env python3
"""
Minimal MCP Server for Critical Path Testing
Single tool: restart_service
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, validator
from datetime import datetime
import json
import logging
from typing import Dict, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(title="MSP MCP Test Server", version="0.1.0")

# Simple in-memory rate limiting (replace with Redis in production)
recent_actions = {}

class IncidentRequest(BaseModel):
    client_id: str
    hostname: str
    incident_type: str
    severity: str
    details: dict

class ToolExecution(BaseModel):
    tool_name: str
    params: dict

    @validator('tool_name')
    def validate_tool(cls, v):
        allowed_tools = ['restart_service']
        if v not in allowed_tools:
            raise ValueError(f'Tool must be one of {allowed_tools}')
        return v

class RestartServiceParams(BaseModel):
    service_name: str

    @validator('service_name')
    def validate_service(cls, v):
        # Whitelist for testing
        allowed_services = ['nginx', 'test-service', 'postgresql', 'redis']
        if v not in allowed_services:
            raise ValueError(f'Service {v} not in whitelist')
        return v

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "0.1.0"
    }

@app.get("/tools")
async def list_tools():
    """List available tools"""
    return {
        "tools": [
            {
                "name": "restart_service",
                "description": "Restart a systemd service",
                "params": {
                    "service_name": "string (required)"
                },
                "hipaa_control": "164.308(a)(1)(ii)(D)"
            }
        ]
    }

@app.post("/execute")
async def execute_tool(execution: ToolExecution):
    """Execute a tool and return evidence bundle"""

    logger.info(f"Received execution request: {execution.tool_name}")

    # Simple rate limiting (5 minutes cooldown)
    rate_key = f"{execution.tool_name}:{execution.params.get('service_name', 'unknown')}"
    if rate_key in recent_actions:
        time_since = (datetime.utcnow() - recent_actions[rate_key]).total_seconds()
        if time_since < 300:  # 5 minutes
            raise HTTPException(
                status_code=429,
                detail=f"Rate limited. Try again in {int(300 - time_since)} seconds"
            )

    # Validate params for restart_service
    if execution.tool_name == "restart_service":
        params = RestartServiceParams(**execution.params)

        # Generate mock evidence bundle
        evidence = {
            "bundle_id": f"EB-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}",
            "timestamp_start": datetime.utcnow().isoformat(),
            "tool_name": "restart_service",
            "params": execution.params,
            "result": "success",
            "actions_taken": [
                {
                    "step": 1,
                    "action": "check_service_status",
                    "result": "failed"
                },
                {
                    "step": 2,
                    "action": "restart_service",
                    "command": f"systemctl restart {params.service_name}",
                    "result": "success"
                },
                {
                    "step": 3,
                    "action": "verify_service_running",
                    "result": "success"
                }
            ],
            "timestamp_end": datetime.utcnow().isoformat(),
            "hipaa_control": "164.308(a)(1)(ii)(D)",
            "evidence_complete": True
        }

        # Update rate limiting
        recent_actions[rate_key] = datetime.utcnow()

        logger.info(f"Successfully executed {execution.tool_name}")
        logger.info(f"Evidence bundle: {evidence['bundle_id']}")

        return evidence

    raise HTTPException(status_code=400, detail="Unknown tool")

@app.post("/incident")
async def handle_incident(incident: IncidentRequest):
    """
    Simplified incident handler
    Returns tool execution recommendation
    """

    logger.info(f"Received incident: {incident.incident_type} from {incident.hostname}")

    # Simple rule-based routing (replace with LLM in production)
    tool_mapping = {
        "service_down": {
            "tool_name": "restart_service",
            "params": {
                "service_name": incident.details.get("service_name", "unknown")
            }
        }
    }

    if incident.incident_type not in tool_mapping:
        raise HTTPException(
            status_code=400,
            detail=f"No handler for incident type: {incident.incident_type}"
        )

    recommendation = tool_mapping[incident.incident_type]

    return {
        "incident_id": f"INC-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}",
        "recommended_action": recommendation,
        "severity": incident.severity,
        "auto_execute": True  # For testing
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")

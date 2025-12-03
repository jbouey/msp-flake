#!/usr/bin/env python3
"""
MCP Server Stub for Demo/Testing

This is a minimal MCP server that implements the core endpoints needed
for the compliance agent to function. It stores state in Redis and
provides endpoints for:

- GET /health - Health check
- GET /orders - Get pending orders for a site
- POST /orders - Submit a new order
- GET /orders/{order_id} - Get order status
- POST /evidence - Upload evidence bundle
- GET /runbooks/{runbook_id} - Get runbook definition

DEV ONLY - Not for production use.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import redis
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
RUNBOOKS_DIR = Path(__file__).parent / "runbooks"

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("mcp-stub")

# -----------------------------------------------------------------------------
# Redis Connection
# -----------------------------------------------------------------------------

redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------

class Order(BaseModel):
    order_id: str
    site_id: str
    host_id: str
    runbook_id: str
    params: dict = {}
    status: str = "pending"
    created_at: str
    completed_at: Optional[str] = None
    result: Optional[dict] = None


class OrderRequest(BaseModel):
    site_id: str
    host_id: str
    runbook_id: str
    params: dict = {}


class EvidenceBundle(BaseModel):
    bundle_id: str
    site_id: str
    host_id: str
    check_type: str
    outcome: str
    pre_state: dict
    post_state: dict
    action_taken: str
    timestamp: str
    signature: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    timestamp: str
    version: str = "1.0.0-demo"
    redis_connected: bool


# -----------------------------------------------------------------------------
# FastAPI App
# -----------------------------------------------------------------------------

app = FastAPI(
    title="MCP Server (Demo Stub)",
    description="Development-only MCP server for testing the compliance agent",
    version="1.0.0-demo"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------------------------------------
# Health Endpoint
# -----------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    try:
        redis_client.ping()
        redis_ok = True
    except Exception:
        redis_ok = False

    return HealthResponse(
        status="healthy" if redis_ok else "degraded",
        timestamp=datetime.now(timezone.utc).isoformat(),
        redis_connected=redis_ok
    )


# -----------------------------------------------------------------------------
# Orders Endpoints
# -----------------------------------------------------------------------------

@app.get("/orders")
async def get_orders(site_id: str, host_id: Optional[str] = None):
    """Get pending orders for a site/host"""
    pattern = f"order:*"
    orders = []

    for key in redis_client.scan_iter(pattern):
        order_data = redis_client.get(key)
        if order_data:
            order = json.loads(order_data)
            if order.get("site_id") == site_id:
                if host_id is None or order.get("host_id") == host_id:
                    if order.get("status") == "pending":
                        orders.append(order)

    logger.info(f"Returning {len(orders)} pending orders for site={site_id}")
    return {"orders": orders}


@app.post("/orders")
async def create_order(request: OrderRequest):
    """Create a new order"""
    order_id = str(uuid.uuid4())

    order = Order(
        order_id=order_id,
        site_id=request.site_id,
        host_id=request.host_id,
        runbook_id=request.runbook_id,
        params=request.params,
        status="pending",
        created_at=datetime.now(timezone.utc).isoformat()
    )

    redis_client.set(
        f"order:{order_id}",
        order.model_dump_json(),
        ex=3600  # Expire after 1 hour
    )

    logger.info(f"Created order {order_id} for runbook {request.runbook_id}")
    return {"order_id": order_id, "status": "created"}


@app.get("/orders/{order_id}")
async def get_order(order_id: str):
    """Get order status"""
    order_data = redis_client.get(f"order:{order_id}")

    if not order_data:
        raise HTTPException(status_code=404, detail="Order not found")

    return json.loads(order_data)


@app.patch("/orders/{order_id}")
async def update_order(order_id: str, status: str, result: Optional[dict] = None):
    """Update order status (called by agent after execution)"""
    order_data = redis_client.get(f"order:{order_id}")

    if not order_data:
        raise HTTPException(status_code=404, detail="Order not found")

    order = json.loads(order_data)
    order["status"] = status
    order["completed_at"] = datetime.now(timezone.utc).isoformat()
    if result:
        order["result"] = result

    redis_client.set(f"order:{order_id}", json.dumps(order), ex=3600)

    logger.info(f"Updated order {order_id} to status={status}")
    return order


# -----------------------------------------------------------------------------
# Evidence Endpoints
# -----------------------------------------------------------------------------

@app.post("/evidence")
async def upload_evidence(
    bundle: str = Form(...),
    signature: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None)
):
    """Upload evidence bundle"""
    try:
        evidence_data = json.loads(bundle)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in bundle")

    bundle_id = evidence_data.get("bundle_id", str(uuid.uuid4()))

    # Store in Redis
    redis_client.set(
        f"evidence:{bundle_id}",
        json.dumps({
            "bundle": evidence_data,
            "signature": signature,
            "received_at": datetime.now(timezone.utc).isoformat()
        }),
        ex=86400 * 90  # Keep for 90 days
    )

    logger.info(f"Received evidence bundle {bundle_id}")

    return {
        "status": "accepted",
        "bundle_id": bundle_id,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/evidence/{bundle_id}")
async def get_evidence(bundle_id: str):
    """Get evidence bundle by ID"""
    evidence_data = redis_client.get(f"evidence:{bundle_id}")

    if not evidence_data:
        raise HTTPException(status_code=404, detail="Evidence bundle not found")

    return json.loads(evidence_data)


@app.get("/evidence")
async def list_evidence(site_id: Optional[str] = None, limit: int = 100):
    """List evidence bundles"""
    pattern = "evidence:*"
    bundles = []

    for key in redis_client.scan_iter(pattern):
        if len(bundles) >= limit:
            break
        evidence_data = redis_client.get(key)
        if evidence_data:
            data = json.loads(evidence_data)
            if site_id is None or data.get("bundle", {}).get("site_id") == site_id:
                bundles.append({
                    "bundle_id": key.replace("evidence:", ""),
                    "received_at": data.get("received_at"),
                    "outcome": data.get("bundle", {}).get("outcome")
                })

    return {"bundles": bundles, "count": len(bundles)}


# -----------------------------------------------------------------------------
# Runbook Endpoints
# -----------------------------------------------------------------------------

@app.get("/runbooks/{runbook_id}")
async def get_runbook(runbook_id: str):
    """Get runbook definition"""
    import yaml

    runbook_path = RUNBOOKS_DIR / f"{runbook_id}.yaml"

    if not runbook_path.exists():
        raise HTTPException(status_code=404, detail=f"Runbook {runbook_id} not found")

    try:
        with open(runbook_path) as f:
            runbook = yaml.safe_load(f)
        return runbook
    except Exception as e:
        logger.error(f"Error loading runbook {runbook_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error loading runbook: {e}")


@app.get("/runbooks")
async def list_runbooks():
    """List available runbooks"""
    import yaml

    runbooks = []

    if RUNBOOKS_DIR.exists():
        for path in RUNBOOKS_DIR.glob("*.yaml"):
            try:
                with open(path) as f:
                    rb = yaml.safe_load(f)
                    runbooks.append({
                        "id": rb.get("id", path.stem),
                        "name": rb.get("name", path.stem),
                        "description": rb.get("description", ""),
                        "severity": rb.get("severity", "medium")
                    })
            except Exception as e:
                logger.warning(f"Error loading runbook {path}: {e}")

    return {"runbooks": runbooks, "count": len(runbooks)}


# -----------------------------------------------------------------------------
# Demo/Test Endpoints
# -----------------------------------------------------------------------------

@app.post("/demo/inject-order")
async def inject_order(runbook_id: str, site_id: str = "demo-site-001", host_id: str = "demo-host-001"):
    """Inject a test order for demo purposes"""
    return await create_order(OrderRequest(
        site_id=site_id,
        host_id=host_id,
        runbook_id=runbook_id,
        params={"demo": True}
    ))


@app.get("/demo/stats")
async def demo_stats():
    """Get demo statistics"""
    order_count = len(list(redis_client.scan_iter("order:*")))
    evidence_count = len(list(redis_client.scan_iter("evidence:*")))

    return {
        "orders": order_count,
        "evidence_bundles": evidence_count,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.delete("/demo/reset")
async def reset_demo():
    """Reset all demo data"""
    for key in redis_client.scan_iter("order:*"):
        redis_client.delete(key)
    for key in redis_client.scan_iter("evidence:*"):
        redis_client.delete(key)

    logger.info("Demo data reset")
    return {"status": "reset", "timestamp": datetime.now(timezone.utc).isoformat()}


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)

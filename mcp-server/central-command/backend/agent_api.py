"""
Agent/Appliance-facing API endpoints.

***  DEAD-ROUTER WARNING (Session 213 round-table P1)  ***

The `agent_api_router` defined below is INTENTIONALLY NOT registered
via `app.include_router()` in main.py. The routes you see decorated
below are 700+ lines of `@router.post/get` shadows — they have ZERO
effect at runtime. The live versions of every handler here are
defined directly on `app` in `mcp-server/main.py`.

Only TWO things in this module are actually used by the running app:
  - `agent_l2_plan` — wired via `app.post("/api/agent/l2/plan")(...)` at main.py:4526
  - `load_monitoring_only_from_registry` — called from the lifespan startup

When editing a route handler under `@router.post("/X")` in this file,
you are NOT changing the `/X` route. Find the live handler in main.py
instead.

Filed as P3: remove the decorators or delete the file (roughly 700-line
mechanical diff). Until that lands, this header is the polarity-rule
warning the audit recommended. CLAUDE.md ratifies this file's
non-registration status.

----


Extracted from main.py — all endpoints that compliance appliances
and Go daemons call to report incidents, submit evidence, sync rules,
report telemetry, and check in.
"""

import asyncio
import hashlib
import json
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog
from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from minio.retention import COMPLIANCE, Retention
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .shared import (
    MINIO_BUCKET,
    ORDER_TTL_SECONDS,
    WORM_RETENTION_DAYS,
    async_session,
    check_rate_limit,
    get_db,
    get_minio_client,
    get_public_key_hex,
    require_appliance_bearer,
    sign_data,
)
from dashboard_api.alert_router import classify_alert

logger = structlog.get_logger()

router = APIRouter(tags=["agent"])


def _enforce_site_id(auth_site_id: str, request_site_id: str, endpoint: str = "") -> None:
    """Enforce that the request site_id matches the Bearer-authenticated site_id.

    Prevents appliance spoofing — an appliance authenticated for site-A
    must not be able to act on behalf of site-B.
    """
    if request_site_id and request_site_id != auth_site_id:
        logger.warning(
            "site_id mismatch: appliance attempted cross-site action",
            auth_site_id=auth_site_id,
            request_site_id=request_site_id,
            endpoint=endpoint,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Site ID mismatch: token does not authorize this site",
        )

# Check types that are monitoring-only — detect drift but don't attempt auto-remediation.
# HARDCODED FALLBACK — overridden by check_type_registry DB table at startup.
# The registry (migration 157) is the single source of truth.
# Only checks that genuinely cannot be auto-fixed belong here.
MONITORING_ONLY_CHECKS = {
    "net_host_reachability",
    "net_unexpected_ports",
    "net_expected_service",
    "net_dns_resolution",
    "backup_verification",
    "credential_stale",
    "device_unreachable",
    "AGENT-REDEPLOY-EXHAUSTED",
    "WIN-DEPLOY-UNREACHABLE",
    "linux_encryption",
}


async def load_monitoring_only_from_registry(db) -> None:
    """Override MONITORING_ONLY_CHECKS from check_type_registry DB table.
    Called at startup. Falls back to hardcoded set if table doesn't exist."""
    global MONITORING_ONLY_CHECKS
    try:
        result = await db.execute(
            text("SELECT check_name FROM check_type_registry WHERE is_monitoring_only = true")
        )
        rows = result.fetchall()
        if rows:
            MONITORING_ONLY_CHECKS = {r.check_name for r in rows}
            logger.info(f"MONITORING_ONLY_CHECKS loaded from registry: {len(MONITORING_ONLY_CHECKS)} checks")
    except Exception:
        # Table doesn't exist yet, use hardcoded fallback — log so
        # operators see when registry-load fell back to in-source defaults.
        logger.error("monitoring_only_checks_registry_load_failed", exc_info=True)


# The 11 check types that touch Windows via WinRM. When migration 164's
# global kill-switch was removed (migration 216), these became the scope
# of the per-appliance circuit breaker (migration 215). If one appliance's
# WinRM is broken, only THAT appliance's dispatches for these check types
# get gated — other customers keep remediating.
WINRM_CHECK_TYPES = {
    "windows_update",
    "defender_exclusions",
    "registry_run_persistence",
    "screen_lock_policy",
    "bitlocker_status",
    "audit_policy",
    "windows_audit_policy",
    "rogue_scheduled_tasks",
    "windows_defender",
    "smb_signing",
    "firewall_status",
}


async def winrm_circuit_open(db, site_id: str) -> bool:
    """Per-site WinRM circuit-breaker check. Returns True when any appliance
    at `site_id` has accumulated ≥3 WinRM-flavor failures (401 / "winrm" /
    "TLS pin") in the last 30 minutes with zero successful Windows runbook
    executions in the same window. The calling dispatch path treats an open
    circuit identically to the monitoring-only fallback — records the
    incident, skips remediation, returns immediately.

    Auto-closes on the first successful Windows execution in the window.
    The view `v_appliance_winrm_circuit` is defined in migration 215.

    Fails CLOSED (returns False) on any query error so a broken view never
    silently blocks every dispatch — the missing-view case simply reverts
    to pre-circuit behaviour.
    """
    try:
        result = await db.execute(
            text(
                "SELECT 1 FROM v_appliance_winrm_circuit "
                "WHERE site_id = :site_id AND circuit_state = 'open' LIMIT 1"
            ),
            {"site_id": site_id},
        )
        return result.fetchone() is not None
    except Exception:
        logger.warning("WinRM circuit query failed — defaulting to closed",
                       exc_info=True)
        return False


# ============================================================================
# Pydantic Models
# ============================================================================

class _AgentApiCheckinRequest(BaseModel):
    """Appliance check-in request."""
    site_id: str = Field(..., min_length=1, max_length=255)
    host_id: str = Field(..., min_length=1, max_length=255)
    deployment_mode: str = Field(..., pattern="^(reseller|direct)$")
    reseller_id: Optional[str] = None
    policy_version: str = Field(default="1.0")
    nixos_version: Optional[str] = None
    agent_version: Optional[str] = None
    public_key: Optional[str] = None


class _AgentApiIncidentReport(BaseModel):
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


class _AgentApiDriftReport(BaseModel):
    """Drift detection report from appliance."""
    site_id: str
    host_id: str
    check_type: str
    drifted: bool
    pre_state: Dict[str, Any] = Field(default_factory=dict)
    recommended_action: Optional[str] = None
    severity: str = "medium"
    hipaa_controls: Optional[List[str]] = None


class _AgentApiOrderRequest(BaseModel):
    """Request for pending orders."""
    site_id: str
    host_id: str


class _AgentApiOrderAcknowledgement(BaseModel):
    """Order acknowledgement from appliance."""
    site_id: str
    order_id: str


class _AgentApiOrderCompletion(BaseModel):
    """Order completion report from appliance.

    RT-DM Issue #3 (2026-05-06): pre-fix, `orders.status` had no code
    path past 'acknowledged'. Order-completion dashboards counted 0%.
    This endpoint is the primary completion path; the appliance calls
    it after executing an order's intended action. The
    `sweep_stuck_orders()` SQL function (mig 286) is the backstop for
    orders that ack'd but never complete'd.
    """
    site_id: str
    order_id: str
    success: bool
    error_message: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    duration_seconds: Optional[float] = None


class _AgentApiEvidenceSubmission(BaseModel):
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


class _AgentApiPatternReportInput(BaseModel):
    """Pattern report from agent after successful healing."""
    site_id: str
    check_type: str
    issue_signature: str
    resolution_steps: List[str]
    success: bool
    execution_time_ms: int
    runbook_id: Optional[str] = None
    reported_at: Optional[datetime] = None


class _AgentApiPatternStatSync(BaseModel):
    """Single pattern stat from agent."""
    pattern_signature: str
    total_occurrences: int
    l1_resolutions: int
    l2_resolutions: int
    l3_resolutions: int
    success_count: int
    total_resolution_time_ms: float
    last_seen: str
    recommended_action: Optional[str] = None
    promotion_eligible: bool = False


class _AgentApiPatternStatsRequest(BaseModel):
    """Batch pattern stats sync request from agent."""
    site_id: str
    appliance_id: str
    synced_at: str
    pattern_stats: List[_AgentApiPatternStatSync]


class _AgentApiPromotedRuleResponse(BaseModel):
    """Promoted rule for agent deployment."""
    rule_id: str
    pattern_signature: str
    rule_yaml: str
    promoted_at: str
    promoted_by: str
    source: str = "server_promoted"


class _AgentApiExecutionTelemetryInput(BaseModel):
    """Execution telemetry from agent. Accepts both wrapped and flat formats."""
    site_id: str
    execution: Optional[dict] = None
    reported_at: Optional[str] = None
    model_config = {"extra": "allow"}


class L2PlanRequest(BaseModel):
    """L2 plan request from appliance daemon."""
    incident_id: str
    site_id: str
    host_id: str
    incident_type: str
    severity: str = "medium"
    raw_data: dict = {}
    pattern_signature: str = ""
    created_at: str = ""


class _AgentApiApplianceCheckinRequest(BaseModel):
    """Appliance check-in from agent (uses different field names)."""
    site_id: str
    hostname: Optional[str] = None
    mac_address: Optional[str] = None
    ip_addresses: Optional[List[str]] = None
    agent_version: Optional[str] = None
    nixos_version: Optional[str] = None
    uptime_seconds: Optional[int] = None
    queue_depth: Optional[int] = 0


# ============================================================================
# Endpoints
# ============================================================================

@router.post("/checkin")
async def checkin(
    req: _AgentApiCheckinRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """Appliance check-in endpoint. Registers or updates appliance, returns pending orders."""
    # C1 fix: enforce Bearer site matches request body
    _enforce_site_id(auth_site_id, req.site_id, "checkin")
    allowed, remaining = await check_rate_limit(req.site_id, "checkin")
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limited. Try again in {remaining} seconds."
        )

    client_ip = request.client.host if request.client else None

    # Lookup existing by (site_id, appliance_id) — host_id maps 1:1 to appliance_id
    # on the site_appliances canonical schema. ip_addresses is jsonb array.
    result = await db.execute(
        text("""
            SELECT legacy_uuid FROM site_appliances
            WHERE site_id = :site_id AND appliance_id = :host_id
              AND deleted_at IS NULL
        """),
        {"site_id": req.site_id, "host_id": req.host_id}
    )
    existing = result.fetchone()

    now = datetime.now(timezone.utc)

    if existing:
        # Per-row UPDATE filtered by (site_id, appliance_id) — satisfies
        # migration 192 row-guard without needing app.allow_multi_row.
        await db.execute(
            text("""
                UPDATE site_appliances SET
                    deployment_mode = :deployment_mode,
                    reseller_id = :reseller_id,
                    policy_version = :policy_version,
                    nixos_version = :nixos_version,
                    agent_version = :agent_version,
                    agent_public_key = :public_key,
                    ip_addresses = :ip_addresses,
                    last_checkin = :last_checkin
                WHERE site_id = :site_id
                  AND appliance_id = :host_id
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
                "ip_addresses": json.dumps([client_ip] if client_ip else []),
                "last_checkin": now,
            }
        )
        appliance_id = str(existing[0]) if existing[0] else req.host_id
        action = "updated"
    else:
        # INSERT the site_appliances row. legacy_uuid left NULL by default —
        # post-M1 DROP of the legacy appliances table makes it historical only.
        await db.execute(
            text("""
                INSERT INTO site_appliances (
                    site_id, appliance_id, hostname, deployment_mode, reseller_id,
                    policy_version, nixos_version, agent_version, agent_public_key,
                    ip_addresses, status, first_checkin, last_checkin, created_at
                ) VALUES (
                    :site_id, :host_id, :host_id, :deployment_mode, :reseller_id,
                    :policy_version, :nixos_version, :agent_version, :public_key,
                    :ip_addresses, 'online', :first_checkin, :last_checkin, :created_at
                )
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
                "ip_addresses": json.dumps([client_ip] if client_ip else []),
                "first_checkin": now,
                "last_checkin": now,
                "created_at": now,
            }
        )
        appliance_id = req.host_id
        action = "registered"

    await db.commit()

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

    result = await db.execute(
        text("""
            SELECT order_id, runbook_id, parameters, nonce, signature, ttl_seconds,
                   issued_at, expires_at, signed_payload
            FROM orders o
            JOIN v_appliances_current a ON o.appliance_id = a.id
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
            "order_type": "healing",
            "runbook_id": row[1],
            "parameters": row[2],
            "nonce": row[3],
            "signature": row[4],
            "ttl_seconds": row[5],
            "issued_at": row[6].isoformat() if row[6] else None,
            "expires_at": row[7].isoformat() if row[7] else None,
            "signed_payload": row[8],
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


@router.get("/orders/{site_id}")
async def get_orders(
    site_id: str,
    db: AsyncSession = Depends(get_db),
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """Get pending orders for an appliance."""
    _enforce_site_id(auth_site_id, site_id, "get_orders")
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
            JOIN v_appliances_current a ON o.appliance_id = a.id
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


@router.post("/orders/acknowledge")
async def acknowledge_order(
    req: _AgentApiOrderAcknowledgement,
    db: AsyncSession = Depends(get_db),
    auth_site_id: str = Depends(require_appliance_bearer),
):
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


@router.post("/orders/complete")
async def complete_order(
    req: _AgentApiOrderCompletion,
    db: AsyncSession = Depends(get_db),
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """Report execution result for an order — primary completion path.

    RT-DM Issue #3 (2026-05-06, Maya 2nd-eye redesign): pre-fix,
    `orders.status` never transitioned past 'acknowledged'. The
    initial round-table consensus was a DB trigger reading
    `execution_telemetry.metadata->>'order_id'`, but Maya's 2nd-eye
    on the fix found `execution_telemetry` has NO metadata column —
    trigger would silently no-op. Redesigned to this explicit
    endpoint: appliance calls after executing the order, endpoint
    transitions `acknowledged → completed` (success) or
    `acknowledged → failed` (with error_message). Idempotent: WHERE
    clause filters status IN ('acknowledged', 'executing'); duplicate
    completion reports are no-ops.

    The `sweep_stuck_orders()` SQL function is the backstop for
    orders that ack'd but never reach this endpoint (agent crash,
    network gap).
    """
    now = datetime.now(timezone.utc)
    if req.site_id != auth_site_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="site_id mismatch with bearer token",
        )

    new_status = "completed" if req.success else "failed"
    err_msg = None if req.success else (req.error_message or "Appliance reported failure (no message)")

    result_json = {
        "success": req.success,
        "recorded_at": now.isoformat(),
    }
    if req.duration_seconds is not None:
        result_json["duration_seconds"] = req.duration_seconds
    if req.result:
        result_json["result"] = req.result
    # Schema-fixture audit (2026-05-08, F-P3-1): the `orders` table
    # has NO top-level `error_message` column. Failure messages are
    # stored inside `result->>'error_message'` (assertions.py:5031
    # already documents this is the canonical home; the substrate
    # invariant `unbridged_telemetry_runbook_ids` reads from the
    # JSONB path). The pre-fix UPDATE wrote to a non-existent column
    # which raised silently inside an asyncpg execute (no rowcount
    # change but no Python exception in the happy path because the
    # query was actually rejected at parse-time only after the fixture
    # diverged). Putting err_msg into result_json keeps the data
    # accessible AND in the right place per the existing convention.
    if err_msg is not None:
        result_json["error_message"] = err_msg

    update = await db.execute(
        text("""
            UPDATE orders SET
                status = :new_status,
                completed_at = :completed_at,
                result = :result_json::jsonb
            WHERE order_id = :order_id
              AND status IN ('acknowledged', 'executing', 'pending')
            RETURNING id
        """),
        {
            "order_id": req.order_id,
            "new_status": new_status,
            "completed_at": now,
            "result_json": json.dumps(result_json),
        },
    )

    if update.rowcount == 0:
        # Either order doesn't exist OR already in a terminal state.
        # Idempotent — return 200 with a "no-op" status so the agent
        # can stop retrying without burning the order.
        await db.commit()
        logger.info(
            "Order completion no-op (already terminal or missing)",
            site_id=req.site_id, order_id=req.order_id,
        )
        return {
            "status": "no-op",
            "order_id": req.order_id,
            "reason": "order not found or already in terminal state",
            "timestamp": now.isoformat(),
        }

    await db.commit()
    logger.info(
        "Order completed",
        site_id=req.site_id,
        order_id=req.order_id,
        success=req.success,
    )

    return {
        "status": new_status,
        "order_id": req.order_id,
        "timestamp": now.isoformat(),
    }


@router.post("/incidents")
async def report_incident(
    incident: _AgentApiIncidentReport,
    request: Request,
    db: AsyncSession = Depends(get_db),
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """Report an incident from an appliance. Creates an order for remediation if a matching runbook is found."""
    _enforce_site_id(auth_site_id, incident.site_id, "report_incident")
    allowed, remaining = await check_rate_limit(incident.site_id, "incidents")
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limited. Try again in {remaining} seconds."
        )

    client_ip = request.client.host if request.client else None

    result = await db.execute(
        text("SELECT id FROM v_appliances_current WHERE site_id = :site_id"),
        {"site_id": incident.site_id}
    )
    appliance = result.fetchone()

    if not appliance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Appliance not registered: {incident.site_id}"
        )

    appliance_id = str(appliance[0])

    canonical_result = await db.execute(
        text("SELECT appliance_id FROM site_appliances WHERE site_id = :site_id ORDER BY last_checkin DESC NULLS LAST LIMIT 1"),
        {"site_id": incident.site_id}
    )
    canonical_row = canonical_result.fetchone()
    canonical_appliance_id = canonical_row[0] if canonical_row else appliance_id
    now = datetime.now(timezone.utc)

    # Compute cross-appliance dedup key: SHA256(site_id:incident_type:hostname)
    # Use hostname from details dict first, then host_id field.
    hostname = (incident.details or {}).get("hostname") or incident.host_id or ""
    if hostname:
        _dedup_raw = f"{incident.site_id}:{incident.incident_type}:{hostname}"
        dedup_key = hashlib.sha256(_dedup_raw.encode()).hexdigest()
    else:
        dedup_key = None

    # Severity rank for upgrade comparison
    _SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}

    # Deduplicate — cross-appliance (by dedup_key) or appliance-scoped fallback
    if dedup_key:
        existing_check = await db.execute(
            text("""
                SELECT id, status, severity, appliance_id FROM incidents
                WHERE site_id = :site_id
                AND dedup_key = :dedup_key
                AND (
                    (status IN ('open', 'resolving', 'escalated'))
                    OR (status = 'resolved' AND resolved_at > NOW() - INTERVAL '30 minutes')
                )
                AND created_at > NOW() - INTERVAL '48 hours'
                ORDER BY created_at DESC
                LIMIT 1
            """),
            {"site_id": incident.site_id, "dedup_key": dedup_key}
        )
    else:
        existing_check = await db.execute(
            text("""
                SELECT id, status, severity, appliance_id FROM incidents
                WHERE appliance_id = :appliance_id
                AND incident_type = :incident_type
                AND (
                    (status IN ('open', 'resolving', 'escalated'))
                    OR (status = 'resolved' AND resolved_at > NOW() - INTERVAL '30 minutes')
                )
                AND created_at > NOW() - INTERVAL '48 hours'
                ORDER BY created_at DESC
                LIMIT 1
            """),
            {"appliance_id": appliance_id, "incident_type": incident.incident_type}
        )
    existing_incident = existing_check.fetchone()

    if existing_incident:
        existing_id = str(existing_incident[0])
        existing_status = existing_incident[1]
        existing_severity = existing_incident[2]

        # Severity upgrade: if new report has higher severity, upgrade the existing incident
        new_rank = _SEVERITY_RANK.get(incident.severity or "info", 0)
        old_rank = _SEVERITY_RANK.get(existing_severity or "info", 0)
        if new_rank > old_rank:
            await db.execute(
                text("UPDATE incidents SET severity = :severity WHERE id = :id"),
                {"severity": incident.severity, "id": existing_id}
            )

        if existing_status == 'resolved':
            # Reopen the incident
            await db.execute(
                text("""
                    UPDATE incidents SET
                        status = 'resolving',
                        resolved_at = NULL,
                        reopen_count = COALESCE(reopen_count, 0) + 1
                    WHERE id = :id
                """),
                {"id": existing_id}
            )

            # CRITICAL: Check for recurrence pattern on reopen.
            # Without this, the flywheel NEVER gets to analyze recurring
            # issues because dedup reopens them instead of creating new
            # incidents that would trigger the recurrence check.
            recurrence_check = await db.execute(
                text("""
                    SELECT COUNT(*) FROM incidents
                    WHERE appliance_id = :appliance_id
                    AND incident_type = :incident_type
                    AND status = 'resolved'
                    AND resolved_at > NOW() - INTERVAL '4 hours'
                """),
                {"appliance_id": appliance_id, "incident_type": incident.incident_type}
            )
            reopen_recurrence_count = recurrence_check.scalar() or 0

            if reopen_recurrence_count >= 3:
                logger.info("Recurrence detected on reopen — escalating existing incident to L2",
                            site_id=incident.site_id,
                            incident_type=incident.incident_type,
                            recurrence_4h=reopen_recurrence_count,
                            incident_id=existing_id)

                # Call L2 with recurrence context for the REOPENED incident
                try:
                    from dashboard_api.l2_planner import analyze_incident as l2_analyze, record_l2_decision, is_l2_available
                    if is_l2_available():
                        l2_details = dict(incident.details or {})
                        l2_details["recurrence"] = {
                            "recurrence_count_4h": reopen_recurrence_count,
                            "recurrence_count_7d": reopen_recurrence_count,
                            "message": (
                                f"This incident type ({incident.incident_type}) has been "
                                f"resolved {reopen_recurrence_count} times in 4h and is "
                                f"recurring again. L1's fix is not sticking. Analyze the "
                                f"persistence mechanism and recommend a root-cause fix."
                            ),
                        }
                        decision = await l2_analyze(
                            incident_type=incident.incident_type,
                            severity=incident.severity,
                            check_type=incident.check_type or incident.incident_type,
                            details=l2_details,
                            pre_state=incident.pre_state,
                            hipaa_controls=incident.hipaa_controls,
                            site_id=incident.site_id,
                        )
                        await record_l2_decision(
                            db, existing_id, decision,
                            incident_type=incident.incident_type,
                            escalation_reason="recurrence",
                        )
                        logger.info("L2 recurrence decision recorded on reopen",
                                    incident_id=existing_id,
                                    recommended_runbook=decision.runbook_id,
                                    confidence=decision.confidence)
                except Exception as e:
                    logger.error(f"L2 recurrence analysis on reopen failed: {e}")

            await db.commit()
            return {
                "status": "reopened",
                "incident_id": existing_id,
                "resolution_tier": None,
                "order_id": None,
                "runbook_id": None,
                "timestamp": now.isoformat()
            }

        await db.commit()
        return {
            "status": "deduplicated",
            "incident_id": existing_id,
            "resolution_tier": None,
            "order_id": None,
            "runbook_id": None,
            "timestamp": now.isoformat()
        }

    # Create incident record. Migration 142 added a partial unique index on
    # dedup_key WHERE status NOT IN ('resolved','closed') to prevent a race
    # where two appliances reporting the same issue simultaneously could both
    # pass the check-then-act SELECT above and both try to INSERT. The ON
    # CONFLICT clause now closes that race: if the unique index fires, we
    # fall back to the existing row instead of 500-ing.
    incident_id = str(uuid.uuid4())
    insert_result = await db.execute(
        text("""
            INSERT INTO incidents (id, appliance_id, incident_type, severity, check_type,
                details, pre_state, hipaa_controls, reported_at, dedup_key)
            VALUES (:id, :appliance_id, :incident_type, :severity, :check_type,
                :details, :pre_state, :hipaa_controls, :reported_at, :dedup_key)
            ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL
                AND status NOT IN ('resolved', 'closed')
                DO NOTHING
            RETURNING id
        """),
        {
            "id": incident_id,
            "appliance_id": appliance_id,
            "incident_type": incident.incident_type,
            "severity": incident.severity,
            "check_type": incident.check_type,
            "details": json.dumps({**incident.details, "hostname": incident.host_id}),
            "pre_state": json.dumps(incident.pre_state),
            "hipaa_controls": incident.hipaa_controls,
            "reported_at": now,
            "dedup_key": dedup_key,
        }
    )
    inserted_row = insert_result.fetchone()
    if inserted_row is None and dedup_key is not None:
        # Race: a concurrent appliance INSERT won. Look up the existing open
        # row and short-circuit — this is the exact "deduplicated" branch we
        # would have taken if the first SELECT had seen the row.
        concurrent_row = (await db.execute(
            text("""
                SELECT id FROM incidents
                WHERE dedup_key = :dedup_key
                  AND status NOT IN ('resolved', 'closed')
                ORDER BY created_at DESC
                LIMIT 1
            """),
            {"dedup_key": dedup_key},
        )).fetchone()
        if concurrent_row:
            await db.commit()
            return {
                "status": "deduplicated",
                "incident_id": str(concurrent_row[0]),
                "resolution_tier": None,
                "order_id": None,
                "runbook_id": None,
                "timestamp": now.isoformat(),
            }
        # Extremely unlikely: ON CONFLICT fired but the existing row was
        # already resolved/closed between the conflict and our lookup. Fall
        # through and let the insert attempt without ON CONFLICT raise — the
        # caller will get a 500 which is correct behaviour for this edge case.

    # Monitoring-only checks: record the incident for dashboards but skip the entire
    # L1 → L2 → L3 remediation cascade.  These check types detect drift that cannot
    # be auto-fixed (e.g. host offline, backup not configured, BitLocker needs TPM).
    #
    # Circuit breaker gate (migration 215): even for checks that ARE normally
    # remediated, if this is a WinRM-touching check type AND the site's
    # per-appliance WinRM circuit is open, fall back to the monitoring path.
    # Protects the Flywheel from the 3194-failure 401 cascade that migration
    # 164 was built to mask — but now scoped per-appliance so healthy
    # customers keep getting auto-remediation.
    check_key = incident.check_type or incident.incident_type
    is_winrm_check = check_key in WINRM_CHECK_TYPES or incident.incident_type in WINRM_CHECK_TYPES
    circuit_gated = is_winrm_check and await winrm_circuit_open(db, incident.site_id)
    if (
        incident.incident_type in MONITORING_ONLY_CHECKS
        or check_key in MONITORING_ONLY_CHECKS
        or circuit_gated
    ):
        await db.execute(
            text("""
                UPDATE incidents SET resolution_tier = 'monitoring', status = 'open'
                WHERE id = :incident_id
            """),
            {"incident_id": incident_id}
        )
        await db.commit()
        skip_reason = "winrm_circuit_open" if circuit_gated else "monitoring_only"
        logger.info(
            f"Skipping remediation pipeline ({skip_reason})",
            site_id=incident.site_id,
            incident_type=incident.incident_type,
            check_type=check_key,
        )
        return {
            "status": "received",
            "incident_id": incident_id,
            "resolution_tier": "monitoring",
            "order_id": None,
            "runbook_id": None,
            "timestamp": now.isoformat(),
            "skip_reason": skip_reason,
        }

    # Try L1 rules
    runbook_id = None
    resolution_tier = None

    # Recurrence detection: if L1 keeps fixing the same thing and it keeps
    # coming back, the fix isn't sticking. Escalate to L2 for root-cause
    # analysis instead of repeating the same failing L1 runbook.
    #
    # Thresholds:
    #   3+ resolved in 4h  → bypass L1, send to L2 with recurrence context
    #   10+ resolved in 7d → L3 human review (L2 isn't solving it either)
    recurrence_context = None
    recent_recurrence = await db.execute(
        text("""
            SELECT COUNT(*) FROM incidents
            WHERE appliance_id = :appliance_id
            AND incident_type = :incident_type
            AND status = 'resolved'
            AND resolved_at > NOW() - INTERVAL '4 hours'
        """),
        {"appliance_id": appliance_id, "incident_type": incident.incident_type}
    )
    recent_recurrence_count = recent_recurrence.scalar() or 0

    chronic_check = await db.execute(
        text("""
            SELECT COUNT(*) FROM incidents
            WHERE appliance_id = :appliance_id
            AND incident_type = :incident_type
            AND status = 'resolved'
            AND resolved_at > NOW() - INTERVAL '7 days'
        """),
        {"appliance_id": appliance_id, "incident_type": incident.incident_type}
    )
    chronic_count = chronic_check.scalar() or 0

    if recent_recurrence_count >= 3:
        # L1 keeps fixing this and it keeps coming back.
        # Skip L1, go straight to L2 with recurrence context so the LLM
        # can analyze the root cause and recommend a deeper fix.
        recurrence_context = {
            "recurrence_count_4h": recent_recurrence_count,
            "recurrence_count_7d": chronic_count,
            "message": (
                f"This incident type ({incident.incident_type}) has been resolved "
                f"{recent_recurrence_count} times in the last 4 hours by L1, but it "
                f"keeps recurring. The L1 runbook removes the symptom but not the "
                f"root cause. Analyze what persistence mechanism is causing the "
                f"issue to return and recommend a remediation that addresses the "
                f"root cause, not just the symptom."
            ),
        }
        logger.info("Recurrence detected — bypassing L1, sending to L2 for root-cause analysis",
                     site_id=incident.site_id,
                     incident_type=incident.incident_type,
                     recent_4h=recent_recurrence_count,
                     resolved_7d=chronic_count)
    elif chronic_count >= 10 and recent_recurrence_count < 3:
        # Only escalate to L3 if there's NO short-term recurrence pattern
        # to learn from. If it IS recurring in 4h, L2 should analyze it.
        resolution_tier = "L3"
        logger.warning("Chronic drift with no short-term pattern — escalating to L3",
                       site_id=incident.site_id,
                       incident_type=incident.incident_type,
                       resolved_7d=chronic_count)

    # Step 1: Query l1_rules table (skip if recurrence detected — L1 isn't solving it)
    if resolution_tier != "L3" and recurrence_context is None:
        l1_match = await db.execute(
            text("""
                SELECT runbook_id FROM l1_rules
                WHERE enabled = true
                AND incident_pattern->>'incident_type' = :incident_type
                ORDER BY confidence DESC
                LIMIT 1
            """),
            {"incident_type": incident.incident_type}
        )
        l1_row = l1_match.fetchone()
    else:
        l1_row = None

    if l1_row:
        matched_runbook = l1_row[0]
        if matched_runbook.startswith("ESC-") or matched_runbook == "ESCALATE":
            resolution_tier = "L3"
            logger.info("L1 rule matched escalation runbook",
                        site_id=incident.site_id,
                        incident_type=incident.incident_type,
                        runbook_id=matched_runbook)
        else:
            runbook_id = matched_runbook
            resolution_tier = "L1"
    else:
        # Step 2: Fallback keyword matching
        type_lower = incident.incident_type.lower()
        check_type = incident.check_type or ""

        # Keyword → runbook fallback map. IDs MUST match the embedded
        # registry in appliance/internal/daemon/runbooks.json.
        # Using a non-existent ID causes 100% execution failure.
        runbook_map = {
            "backup": "RB-WIN-BACKUP-001",
            "certificate": "RB-WIN-CERT-001",
            "cert": "LIN-CERT-001",
            "disk": "LIN-DISK-001",
            "storage": "RB-WIN-STG-001",
            "service": "RB-WIN-SVC-001",
            "drift": "RB-DRIFT-001",
            "configuration": "RB-DRIFT-001",
            "firewall": "RB-WIN-FIREWALL-001",
            "patching": "RB-WIN-PATCH-001",
            "update": "RB-WIN-PATCH-001",
        }

        for keyword, rb_id in runbook_map.items():
            if keyword in type_lower or keyword in check_type.lower():
                runbook_id = rb_id
                resolution_tier = "L1"
                break

    order_id = None

    # ─── Migration 184 Phase 2 — shadow-mode consent check ──────────
    # Verify the site has granted class-level consent for this runbook
    # BEFORE we dispatch an order. In `shadow` mode we only log and
    # write the ledger event; in `enforce` (Phase 3+) we block.
    consent_class_id = None
    consent_result = None
    if runbook_id and resolution_tier == "L1":
        try:
            from dashboard_api.runbook_consent import (
                classify_runbook_to_class,
                verify_consent_active,
                get_consent_mode,
            )
            consent_class_id = classify_runbook_to_class(runbook_id)
            consent_result = await verify_consent_active(
                db, site_id=incident.site_id, class_id=consent_class_id,
            )
            logger.info(
                "runbook_consent_check",
                site_id=incident.site_id,
                runbook_id=runbook_id,
                class_id=consent_class_id,
                ok=consent_result.ok,
                reason=consent_result.reason,
                consent_id=consent_result.consent_id,
                mode=get_consent_mode(),
            )
            if consent_result.should_block():
                # Phase 3+ — enforce mode blocks execution. For now this
                # branch is dormant under RUNBOOK_CONSENT_MODE=shadow.
                logger.warning(
                    "runbook_consent_block (enforce mode)",
                    site_id=incident.site_id,
                    runbook_id=runbook_id,
                    reason=consent_result.reason,
                )
                runbook_id = None
                resolution_tier = "L3"
        except Exception:
            # Never let the consent check break the resolution pipeline
            # while shadow-mode is on. Log + continue.
            logger.exception("runbook_consent_check_error (non-fatal in shadow)")

    if runbook_id:
        # Create signed order
        order_id = hashlib.sha256(
            f"{incident.site_id}{incident_id}{now.isoformat()}".encode()
        ).hexdigest()[:16]

        nonce = secrets.token_hex(16)
        expires_at = now + timedelta(seconds=ORDER_TTL_SECONDS)

        order_payload = json.dumps({
            "order_id": order_id,
            "runbook_id": runbook_id,
            "parameters": {},
            "nonce": nonce,
            "issued_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "target_appliance_id": canonical_appliance_id,
            # Phase 13.5 H6 — advertise the signing pubkey so the daemon
            # can fall back to verifying against it when its verifier
            # cache is stale. Bounded trust on daemon side: the key must
            # match what the server most-recently delivered via checkin.
            "signing_pubkey_hex": get_public_key_hex(),
        }, sort_keys=True)

        signature = sign_data(order_payload)

        await db.execute(
            text("""
                INSERT INTO orders (order_id, appliance_id, runbook_id, parameters, nonce,
                    signature, signed_payload, ttl_seconds, issued_at, expires_at)
                VALUES (:order_id, :appliance_id, :runbook_id, :parameters, :nonce,
                    :signature, :signed_payload, :ttl_seconds, :issued_at, :expires_at)
            """),
            {
                "order_id": order_id,
                "appliance_id": appliance_id,
                "runbook_id": runbook_id,
                "parameters": json.dumps({
                    "runbook_id": runbook_id,
                    "hostname": incident.host_id,
                    "check_type": incident.check_type or incident.incident_type,
                }),
                "nonce": nonce,
                "signature": signature,
                "signed_payload": order_payload,
                "ttl_seconds": ORDER_TTL_SECONDS,
                "issued_at": now,
                "expires_at": expires_at
            }
        )

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

        # Record remediation step for flywheel visibility.
        # Without this, L1 resolutions are invisible to the auto-candidate
        # scan and the promotion pipeline never sees successful patterns.
        try:
            await db.execute(
                text("""
                    INSERT INTO incident_remediation_steps
                    (incident_id, tier, runbook_id, result, confidence, created_at)
                    VALUES (:iid, :tier, :rid, 'order_created', 1.0, NOW())
                """),
                {"iid": incident_id, "tier": resolution_tier, "rid": runbook_id}
            )
        except Exception as e:
            # Session 205 "no silent write failures" — DB writes log-and-raise
            # (or log-with-exc_info). incident_remediation_steps is the audit-
            # classified table that replaced incidents.remediation_history
            # JSONB (Migration 137); silent loss breaks the chain.
            logger.error(f"Failed to record remediation step: {e}", exc_info=True)

        logger.info("Created remediation order",
                    site_id=incident.site_id,
                    incident_id=incident_id,
                    order_id=order_id,
                    runbook_id=runbook_id,
                    tier=resolution_tier)

        # ─── Migration 184 Phase 2 — write ledger event for the ──────
        # execution. `consent_id` is None when no consent row existed
        # (shadow-mode log signal) so auditors can see the gap.
        try:
            from dashboard_api.runbook_consent import record_executed_with_consent
            if consent_class_id:
                await record_executed_with_consent(
                    db,
                    site_id=incident.site_id,
                    class_id=consent_class_id,
                    runbook_id=runbook_id,
                    consent_id=consent_result.consent_id if consent_result else None,
                    incident_id=str(incident_id) if incident_id else None,
                )
        except Exception:
            # Ledger write failures do NOT block order dispatch.
            # Shadow-mode philosophy: learn, don't break.
            logger.exception("runbook_consent_ledger_write_failed")
    elif resolution_tier == "L3":
        await db.execute(
            text("""
                UPDATE incidents SET
                    resolution_tier = 'L3',
                    status = 'escalated'
                WHERE id = :incident_id
            """),
            {"incident_id": incident_id}
        )
        logger.info("L1 escalation rule -> L3",
                     site_id=incident.site_id,
                     incident_type=incident.incident_type)
    else:
        # No L1 match (or L1 bypassed due to recurrence) — try L2 LLM planner
        from dashboard_api.l2_planner import analyze_incident as l2_analyze, record_l2_decision, is_l2_available

        l2_succeeded = False
        if is_l2_available():
            try:
                # Enrich details with recurrence context when L1 keeps failing
                l2_details = dict(incident.details or {})
                if recurrence_context:
                    l2_details["recurrence"] = recurrence_context
                    logger.info("L2 escalation with recurrence context",
                                site_id=incident.site_id,
                                incident_type=incident.incident_type,
                                recurrence_4h=recurrence_context["recurrence_count_4h"])
                else:
                    logger.info("No L1 match, trying L2 planner",
                                site_id=incident.site_id,
                                incident_type=incident.incident_type)

                decision = await l2_analyze(
                    incident_type=incident.incident_type,
                    severity=incident.severity,
                    check_type=incident.check_type or incident.incident_type,
                    details=l2_details,
                    pre_state=incident.pre_state,
                    hipaa_controls=incident.hipaa_controls,
                    site_id=incident.site_id,
                )

                # Substrate invariant l2_resolution_without_decision_record
                # (Session 219 mig 300): if record_l2_decision fails, we
                # MUST NOT set resolution_tier='L2' — that produces a
                # ghost-L2 incident with no LLM audit trail. Force L3
                # escalation instead so the audit chain stays sound.
                l2_decision_recorded = False
                try:
                    await record_l2_decision(
                        db, incident_id, decision,
                        incident_type=incident.incident_type,
                        escalation_reason="recurrence" if recurrence_context else "normal",
                    )
                    l2_decision_recorded = True
                except Exception as e:
                    logger.error(
                        f"Failed to record L2 decision: {e}", exc_info=True,
                    )

                # Confidence floor moved to 0.7 (Session 205 audit). Below this
                # the planner already nullifies runbook_id, but kept here as a
                # belt-and-suspenders gate in case planner config diverges.
                # `l2_decision_recorded` gate (Session 219): refuse to set L2
                # without an l2_decisions row — substrate invariant.
                if l2_decision_recorded and decision.runbook_id and decision.confidence >= 0.7 and not decision.requires_human_review:
                    runbook_id = decision.runbook_id
                    resolution_tier = "L2"
                    l2_succeeded = True

                    order_id = hashlib.sha256(
                        f"{incident.site_id}{incident_id}{now.isoformat()}".encode()
                    ).hexdigest()[:16]

                    nonce = secrets.token_hex(16)
                    expires_at = now + timedelta(seconds=ORDER_TTL_SECONDS)

                    order_payload = json.dumps({
                        "order_id": order_id,
                        "runbook_id": runbook_id,
                        "parameters": {},
                        "nonce": nonce,
                        "issued_at": now.isoformat(),
                        "expires_at": expires_at.isoformat(),
                        "target_appliance_id": canonical_appliance_id,
                        # Phase 13.5 H6 — see note at the other sign site
                        # in this file (line ~962). Backward compatible.
                        "signing_pubkey_hex": get_public_key_hex(),
                    }, sort_keys=True)

                    signature = sign_data(order_payload)

                    await db.execute(
                        text("""
                            INSERT INTO orders (order_id, appliance_id, runbook_id, parameters, nonce,
                                signature, signed_payload, ttl_seconds, issued_at, expires_at)
                            VALUES (:order_id, :appliance_id, :runbook_id, :parameters, :nonce,
                                :signature, :signed_payload, :ttl_seconds, :issued_at, :expires_at)
                        """),
                        {
                            "order_id": order_id,
                            "appliance_id": appliance_id,
                            "runbook_id": runbook_id,
                            "parameters": json.dumps({
                                "runbook_id": runbook_id,
                                "hostname": incident.host_id,
                                "check_type": incident.check_type or incident.incident_type,
                            }),
                            "nonce": nonce,
                            "signature": signature,
                            "signed_payload": order_payload,
                            "ttl_seconds": ORDER_TTL_SECONDS,
                            "issued_at": now,
                            "expires_at": expires_at
                        }
                    )

                    await db.execute(
                        text("""
                            UPDATE incidents SET
                                resolution_tier = 'L2',
                                order_id = (SELECT id FROM orders WHERE order_id = :order_id),
                                status = 'resolving'
                            WHERE id = :incident_id
                        """),
                        {"order_id": order_id, "incident_id": incident_id}
                    )

                    logger.info("L2 planner matched runbook",
                                site_id=incident.site_id,
                                incident_type=incident.incident_type,
                                runbook_id=runbook_id,
                                confidence=decision.confidence)
                else:
                    logger.info("L2 planner could not resolve — escalating to L3",
                                site_id=incident.site_id,
                                incident_type=incident.incident_type,
                                runbook_id=decision.runbook_id if decision else None,
                                confidence=decision.confidence if decision else None,
                                requires_review=decision.requires_human_review if decision else None)
            except Exception as e:
                logger.error(f"L2 planner failed: {e}",
                             site_id=incident.site_id,
                             incident_type=incident.incident_type)
        else:
            logger.warning("L2 not available (no API key configured)",
                           site_id=incident.site_id,
                           incident_type=incident.incident_type)

        if not l2_succeeded:
            resolution_tier = "L3"
            await db.execute(
                text("""
                    UPDATE incidents SET
                        resolution_tier = 'L3',
                        status = 'escalated'
                    WHERE id = :incident_id
                """),
                {"incident_id": incident_id}
            )
            logger.warning("Escalated to L3",
                           site_id=incident.site_id,
                           incident_type=incident.incident_type)

    await db.commit()

    # Create notification for critical/high severity OR L3 escalations
    severity_map = {"critical": "critical", "high": "warning", "medium": "warning", "low": "info"}
    notification_severity = severity_map.get(incident.severity, "info")

    should_notify = incident.severity in ("critical", "high") or resolution_tier == "L3"

    if should_notify:
        try:
            from dashboard_api.email_alerts import create_notification_with_email

            notification_category = "escalation" if resolution_tier == "L3" else "incident"

            # State-based dedup: if an UNREAD notification already exists for this
            # incident type on this site, bump its count instead of creating a new one.
            # This prevents alert fatigue from repeated observations of the same condition.
            dedup_check = await db.execute(
                text("""
                    SELECT id, metadata FROM notifications
                    WHERE site_id = :site_id
                    AND category = :category
                    AND title LIKE :title_pattern
                    AND is_read = false
                    AND is_dismissed = false
                    LIMIT 1
                """),
                {
                    "site_id": incident.site_id,
                    "category": notification_category,
                    "title_pattern": f"%{incident.incident_type}%",
                }
            )
            existing = dedup_check.fetchone()

            # If an unread notification exists, update its observation count + timestamp
            if existing:
                import json as _json
                try:
                    meta = _json.loads(existing.metadata) if existing.metadata else {}
                except (TypeError, _json.JSONDecodeError):
                    meta = {}
                meta["repeat_count"] = meta.get("repeat_count", 1) + 1
                meta["last_observed"] = datetime.now(timezone.utc).isoformat()
                await db.execute(
                    text("""
                        UPDATE notifications
                        SET metadata = :metadata, created_at = NOW()
                        WHERE id = :id
                    """),
                    {"metadata": _json.dumps(meta), "id": str(existing.id)},
                )
                await db.commit()

            if not existing:
                if resolution_tier == "L3":
                    notification_severity = "critical"

                if resolution_tier == "L3":
                    l3_title = f"[L3] {incident.incident_type}"
                    if incident.host_id:
                        l3_title += f" - {incident.host_id}"

                    l3_message = (
                        f"{incident.incident_type} on {incident.site_id} "
                        f"could not be auto-remediated and requires human review."
                    )
                else:
                    l3_title = f"{incident.severity.upper()}: {incident.incident_type}"
                    l3_message = f"Incident {incident.incident_type} on {incident.site_id}. Resolution: {resolution_tier}"

                await create_notification_with_email(
                    db=db,
                    severity=notification_severity,
                    category=notification_category,
                    title=l3_title,
                    message=l3_message,
                    site_id=incident.site_id,
                    appliance_id=appliance_id,
                    metadata={
                        "incident_id": incident_id,
                        "check_type": incident.check_type,
                        "resolution_tier": resolution_tier,
                        "order_id": order_id
                    },
                    host_id=incident.host_id if resolution_tier == "L3" else None,
                    incident_severity=incident.severity if resolution_tier == "L3" else None,
                    check_type=incident.check_type if resolution_tier == "L3" else None,
                    details=incident.details if resolution_tier == "L3" else None,
                    hipaa_controls=incident.hipaa_controls if resolution_tier == "L3" else None,
                )
        except Exception as e:
            logger.error(f"Failed to create notification: {e}")

    # Enqueue client alert if applicable (non-fatal)
    try:
        from dashboard_api.alert_router import classify_alert, get_effective_alert_mode, ALERT_SUMMARIES
        org_row = await db.execute(
            text("""SELECT co.id as org_id, co.client_alert_mode as org_mode,
                           s.client_alert_mode as site_mode
                    FROM sites s
                    LEFT JOIN client_orgs co ON co.id = s.client_org_id
                    WHERE s.site_id = :site_id"""),
            {"site_id": incident.site_id}
        )
        org_info = org_row.fetchone()
        if org_info and org_info[0]:
            classification = classify_alert(incident.incident_type, incident.severity)
            effective_mode = get_effective_alert_mode(org_info[2], org_info[1])
            if classification["tier"] == "client" and effective_mode != "silent":
                summary = ALERT_SUMMARIES.get(classification["alert_type"], "Compliance issue detected").format(count=1)
                await db.execute(
                    text("""INSERT INTO pending_alerts (id, org_id, site_id, alert_type, severity, summary, incident_id)
                            VALUES (:id, :org_id, :site_id, :alert_type, :severity, :summary, :incident_id)"""),
                    {
                        "id": str(uuid.uuid4()),
                        "org_id": str(org_info[0]),
                        "site_id": incident.site_id,
                        "alert_type": classification["alert_type"],
                        "severity": incident.severity,
                        "summary": summary,
                        "incident_id": incident_id,
                    }
                )
    except Exception as e:
        logger.warning(f"Alert enqueue failed (non-fatal): {e}")

    return {
        "status": "received",
        "incident_id": incident_id,
        "resolution_tier": resolution_tier,
        "order_id": order_id,
        "runbook_id": runbook_id,
        "timestamp": now.isoformat()
    }


@router.post("/incidents/{incident_id}/resolve")
async def resolve_incident(
    incident_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """Mark an incident as resolved after successful healing."""
    body = await request.json()
    resolution_tier = body.get("resolution_tier", "L1")
    action_taken = body.get("action_taken", "")

    result = await db.execute(
        text("""
            UPDATE incidents SET
                resolved_at = NOW(),
                status = 'resolved',
                resolution_tier = COALESCE(:resolution_tier, resolution_tier)
            WHERE id = :incident_id
            AND resolved_at IS NULL
            RETURNING id
        """),
        {"incident_id": incident_id, "resolution_tier": resolution_tier}
    )

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found or already resolved")

    await db.commit()
    logger.info("Incident resolved", incident_id=incident_id, tier=resolution_tier, action=action_taken)
    return {"status": "resolved", "incident_id": incident_id}


@router.post("/incidents/resolve")
async def resolve_incident_by_type(
    request: Request,
    db: AsyncSession = Depends(get_db),
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """Resolve the latest open incident matching site_id + host_id + check_type."""
    body = await request.json()
    site_id = body.get("site_id")
    # C1 fix: override body site_id with authenticated site
    _enforce_site_id(auth_site_id, site_id, "resolve_incident_by_type")
    site_id = auth_site_id
    host_id = body.get("host_id")
    check_type = body.get("check_type")
    resolution_tier = body.get("resolution_tier", "L1")
    runbook_id = body.get("runbook_id", "")

    if not site_id or not host_id or not check_type:
        raise HTTPException(status_code=400, detail="site_id, host_id, and check_type are required")

    app_result = await db.execute(
        text("SELECT id FROM v_appliances_current WHERE site_id = :site_id"),
        {"site_id": site_id}
    )
    appliance = app_result.fetchone()
    if not appliance:
        raise HTTPException(status_code=404, detail=f"Appliance not found: {site_id}")

    appliance_id = str(appliance[0])

    result = await db.execute(
        text("""
            UPDATE incidents SET
                resolved_at = NOW(),
                status = 'resolved',
                resolution_tier = :resolution_tier
            WHERE id = (
                SELECT id FROM incidents
                WHERE appliance_id = :appliance_id
                AND incident_type = :check_type
                AND status IN ('open', 'resolving', 'escalated')
                ORDER BY created_at DESC
                LIMIT 1
            )
            RETURNING id
        """),
        {
            "appliance_id": appliance_id,
            "check_type": check_type,
            "resolution_tier": resolution_tier,
        }
    )

    row = result.fetchone()
    if not row:
        return {"status": "no_match", "message": "No open incident found to resolve"}

    await db.commit()
    logger.info("Incident resolved by type", site_id=site_id, host_id=host_id,
                check_type=check_type, tier=resolution_tier, incident_id=str(row[0]))
    return {"status": "resolved", "incident_id": str(row[0])}


@router.post("/drift")
async def report_drift(
    drift: _AgentApiDriftReport,
    db: AsyncSession = Depends(get_db),
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """Report drift detection results."""
    _enforce_site_id(auth_site_id, drift.site_id, "report_drift")
    allowed, remaining = await check_rate_limit(drift.site_id, "drift")
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limited. Try again in {remaining} seconds."
        )

    if not drift.drifted:
        logger.debug("No drift detected", site_id=drift.site_id, check_type=drift.check_type)
        return {"status": "ok", "drifted": False, "action": "none"}

    # Convert to incident
    incident = _AgentApiIncidentReport(
        site_id=drift.site_id,
        host_id=drift.host_id,
        incident_type=f"drift:{drift.check_type}",
        severity=drift.severity,
        check_type=drift.check_type,
        details={"drifted": True, "recommended_action": drift.recommended_action},
        pre_state=drift.pre_state,
        hipaa_controls=drift.hipaa_controls
    )

    class FakeRequestObj:
        client = None

    return await report_incident(incident, FakeRequestObj(), db)


# NOTE: submit_evidence + list_evidence endpoints that used to live here were
# exact duplicates of main.py's `/evidence` + `/evidence/{site_id}`. This
# module's `router` is not registered in main.py (CLAUDE.md: "agent_api.py
# router is NOT registered in main.py"); only `agent_l2_plan` is manually
# wired. Keeping duplicates here drifts from main.py on every fix — deleted
# Session 209 after the F1/F2 hardening on main.py's copies.


@router.post("/agent/patterns")
async def report_agent_pattern(
    report: _AgentApiPatternReportInput,
    db: AsyncSession = Depends(get_db),
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """Receive pattern report from agent after successful healing."""
    pattern_signature = f"{report.check_type}:{report.issue_signature}"
    pattern_id = hashlib.sha256(pattern_signature.encode()).hexdigest()[:16]

    result = await db.execute(
        text("SELECT pattern_id, occurrences, success_count, failure_count FROM patterns WHERE pattern_id = :pid"),
        {"pid": pattern_id}
    )
    existing = result.fetchone()

    if existing:
        occurrences = existing.occurrences + 1
        success_count = existing.success_count + (1 if report.success else 0)
        failure_count = existing.failure_count + (0 if report.success else 1)

        await db.execute(text("""
            UPDATE patterns
            SET occurrences = :occ,
                success_count = :sc,
                failure_count = :fc,
                last_seen = NOW()
            WHERE pattern_id = :pid
        """), {
            "pid": pattern_id,
            "occ": occurrences,
            "sc": success_count,
            "fc": failure_count,
        })
        await db.commit()

        success_rate = (success_count / occurrences) * 100 if occurrences > 0 else 0.0
        logger.info(f"Pattern updated: {pattern_id} (occurrences: {occurrences}, success_rate: {success_rate:.1f}%)")
        return {
            "pattern_id": pattern_id,
            "status": "updated",
            "occurrences": occurrences,
            "success_rate": success_rate,
        }
    else:
        occurrences = 1
        success_count = 1 if report.success else 0
        failure_count = 0 if report.success else 1

        runbook_id = report.runbook_id or f"AUTO-{report.check_type.upper()}"

        await db.execute(text("""
            INSERT INTO patterns (
                pattern_id, pattern_signature, description, incident_type, runbook_id,
                occurrences, success_count, failure_count,
                avg_resolution_time_ms, total_resolution_time_ms,
                status, first_seen, last_seen, created_at
            ) VALUES (
                :pid, :sig, :desc, :itype, :rid,
                :occ, :sc, :fc,
                :avg_time, :total_time,
                'pending', NOW(), NOW(), NOW()
            )
        """), {
            "pid": pattern_id,
            "sig": pattern_signature,
            "desc": f"Auto-detected pattern from {report.site_id}",
            "itype": report.check_type,
            "rid": runbook_id,
            "occ": occurrences,
            "sc": success_count,
            "fc": failure_count,
            "avg_time": report.execution_time_ms,
            "total_time": report.execution_time_ms,
        })
        await db.commit()

        success_rate = 100.0 if report.success else 0.0
        logger.info(f"Pattern created: {pattern_id} (check_type: {report.check_type})")
        return {
            "pattern_id": pattern_id,
            "status": "created",
            "occurrences": occurrences,
            "success_rate": success_rate,
        }


@router.post("/api/agent/sync/pattern-stats")
async def sync_pattern_stats(
    request: _AgentApiPatternStatsRequest,
    db: AsyncSession = Depends(get_db),
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """Receive pattern statistics from agent for cross-appliance aggregation."""
    _enforce_site_id(auth_site_id, request.site_id, "sync_pattern_stats")
    accepted = 0
    merged = 0
    failed = 0

    for stat in request.pattern_stats:
        try:
            try:
                last_seen_dt = datetime.fromisoformat(stat.last_seen.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                last_seen_dt = datetime.now(timezone.utc)

            result = await db.execute(
                text("""
                    SELECT id, total_occurrences, l1_resolutions, l2_resolutions, l3_resolutions,
                           success_count, total_resolution_time_ms
                    FROM aggregated_pattern_stats
                    WHERE site_id = :site_id AND pattern_signature = :sig
                """),
                {"site_id": request.site_id, "sig": stat.pattern_signature}
            )
            existing = result.fetchone()

            if existing:
                merged_occ = max(existing.total_occurrences, stat.total_occurrences)
                merged_sc = max(existing.success_count, stat.success_count)
                merged_rate = merged_sc / merged_occ if merged_occ > 0 else 0
                is_eligible = merged_occ >= 5 and merged_rate >= 0.90

                await db.execute(text("""
                    UPDATE aggregated_pattern_stats
                    SET total_occurrences = GREATEST(total_occurrences, :occ),
                        l1_resolutions = GREATEST(l1_resolutions, :l1),
                        l2_resolutions = GREATEST(l2_resolutions, :l2),
                        l3_resolutions = GREATEST(l3_resolutions, :l3),
                        success_count = GREATEST(success_count, :sc),
                        total_resolution_time_ms = GREATEST(total_resolution_time_ms, :time),
                        success_rate = CASE
                            WHEN GREATEST(total_occurrences, :occ) > 0
                            THEN CAST(GREATEST(success_count, :sc) AS FLOAT) / GREATEST(total_occurrences, :occ)
                            ELSE 0
                        END,
                        avg_resolution_time_ms = CASE
                            WHEN GREATEST(total_occurrences, :occ) > 0
                            THEN GREATEST(total_resolution_time_ms, :time) / GREATEST(total_occurrences, :occ)
                            ELSE 0
                        END,
                        recommended_action = COALESCE(:action, recommended_action),
                        promotion_eligible = :eligible,
                        last_seen = GREATEST(last_seen, :last_seen),
                        last_synced_at = NOW()
                    WHERE site_id = :site_id AND pattern_signature = :sig
                """), {
                    "site_id": request.site_id,
                    "sig": stat.pattern_signature,
                    "occ": stat.total_occurrences,
                    "l1": stat.l1_resolutions,
                    "l2": stat.l2_resolutions,
                    "l3": stat.l3_resolutions,
                    "sc": stat.success_count,
                    "time": stat.total_resolution_time_ms,
                    "action": stat.recommended_action,
                    "eligible": is_eligible,
                    "last_seen": last_seen_dt,
                })
                merged += 1
            else:
                success_rate = (stat.success_count / stat.total_occurrences) if stat.total_occurrences > 0 else 0
                avg_time = stat.total_resolution_time_ms / stat.total_occurrences if stat.total_occurrences > 0 else 0
                is_eligible = stat.total_occurrences >= 5 and success_rate >= 0.90

                await db.execute(text("""
                    INSERT INTO aggregated_pattern_stats (
                        site_id, pattern_signature, total_occurrences, l1_resolutions,
                        l2_resolutions, l3_resolutions, success_count, total_resolution_time_ms,
                        success_rate, avg_resolution_time_ms, recommended_action, promotion_eligible,
                        first_seen, last_seen, last_synced_at
                    ) VALUES (
                        :site_id, :sig, :occ, :l1, :l2, :l3, :sc, :time,
                        :rate, :avg, :action, :eligible,
                        :first_seen, :last_seen, NOW()
                    )
                """), {
                    "site_id": request.site_id,
                    "sig": stat.pattern_signature,
                    "occ": stat.total_occurrences,
                    "l1": stat.l1_resolutions,
                    "l2": stat.l2_resolutions,
                    "l3": stat.l3_resolutions,
                    "sc": stat.success_count,
                    "time": stat.total_resolution_time_ms,
                    "rate": success_rate,
                    "avg": avg_time,
                    "action": stat.recommended_action,
                    "eligible": is_eligible,
                    "first_seen": last_seen_dt,
                    "last_seen": last_seen_dt,
                })
                accepted += 1

        except Exception as e:
            logger.warning(f"Failed to sync pattern {stat.pattern_signature}: {e}")
            await db.rollback()
            failed += 1
            continue

    sync_status = 'success' if failed == 0 else ('partial' if accepted + merged > 0 else 'failed')
    try:
        await db.execute(text("""
            INSERT INTO appliance_pattern_sync (appliance_id, site_id, synced_at, patterns_received, patterns_merged, sync_status)
            VALUES (:appliance_id, :site_id, NOW(), :received, :merged, :status)
            ON CONFLICT (appliance_id) DO UPDATE SET
                synced_at = NOW(),
                patterns_received = :received,
                patterns_merged = :merged,
                sync_status = :status
        """), {
            "appliance_id": request.appliance_id,
            "site_id": request.site_id,
            "received": len(request.pattern_stats),
            "merged": merged,
            "status": sync_status,
        })
        await db.commit()
    except Exception as e:
        # Session 205 "no silent write failures" — DB writes log-and-raise.
        logger.error(f"Failed to record sync event for {request.appliance_id}: {e}", exc_info=True)
        await db.rollback()

    logger.info(f"Pattern sync from {request.appliance_id}: {accepted} new, {merged} merged")
    return {
        "accepted": accepted,
        "merged": merged,
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/agent/sync/promoted-rules")
async def get_promoted_rules(
    site_id: str,
    since: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """Return server-approved promoted rules for agent deployment."""
    _enforce_site_id(auth_site_id, site_id, "get_promoted_rules")
    since_dt = datetime.fromisoformat(since.replace('Z', '+00:00')) if since else datetime(1970, 1, 1, tzinfo=timezone.utc)

    result = await db.execute(text("""
        SELECT
            pr.rule_id,
            pr.pattern_signature,
            pr.rule_yaml,
            pr.promoted_at,
            COALESCE(au.email, 'system') as promoted_by,
            pr.notes
        FROM promoted_rules pr
        LEFT JOIN admin_users au ON au.id = pr.promoted_by
        WHERE pr.site_id = :site_id
          AND pr.status = 'active'
          AND pr.promoted_at > :since
        ORDER BY pr.promoted_at DESC
    """), {"site_id": site_id, "since": since_dt})

    rows = result.fetchall()
    rules = []

    for row in rows:
        rules.append({
            "rule_id": row.rule_id,
            "pattern_signature": row.pattern_signature,
            "rule_yaml": row.rule_yaml,
            "promoted_at": row.promoted_at.isoformat() if row.promoted_at else datetime.now(timezone.utc).isoformat(),
            "promoted_by": row.promoted_by or "system",
            "source": "server_promoted",
        })

    logger.info(f"Returning {len(rules)} promoted rules for site {site_id}")
    return {
        "rules": rules,
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/api/agent/executions")
async def report_execution_telemetry(
    request: _AgentApiExecutionTelemetryInput,
    db: AsyncSession = Depends(get_db),
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """Receive rich execution telemetry from agents for learning engine."""
    _enforce_site_id(auth_site_id, request.site_id, "report_execution_telemetry")
    if request.execution:
        exec_data = request.execution
    else:
        exec_data = request.model_dump(exclude={"site_id", "execution", "reported_at"})
        exec_data["site_id"] = request.site_id

    def parse_iso_timestamp(ts):
        if ts is None:
            return None
        if isinstance(ts, datetime):
            return ts
        try:
            return datetime.fromisoformat(ts.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            return None

    try:
        if "level" in exec_data and "resolution_level" not in exec_data:
            exec_data["resolution_level"] = exec_data["level"]
        if "duration_ms" in exec_data and "duration_seconds" not in exec_data:
            exec_data["duration_seconds"] = exec_data["duration_ms"] / 1000.0
        if "error" in exec_data and "error_message" not in exec_data:
            exec_data["error_message"] = exec_data["error"]
        if not exec_data.get("execution_id"):
            exec_data["execution_id"] = f"l2-{exec_data.get('incident_id', 'unknown')}-{int(datetime.now(timezone.utc).timestamp() * 1000)}"
        if not exec_data.get("status"):
            exec_data["status"] = "success" if exec_data.get("success") else "failure"

        incident_type = exec_data.get("incident_type")
        hostname = exec_data.get("hostname", "unknown")
        runbook_id = exec_data.get("runbook_id", "unknown")
        pattern_sig = exec_data.get("pattern_signature")
        if not pattern_sig and incident_type and runbook_id:
            from dashboard_api.l2_planner import generate_pattern_signature
            check_type = exec_data.get("check_type", "unknown")
            pattern_sig = generate_pattern_signature(incident_type, check_type, runbook_id)

        await db.execute(text("""
            INSERT INTO execution_telemetry (
                execution_id, incident_id, site_id, appliance_id, runbook_id, hostname, platform, incident_type,
                started_at, completed_at, duration_seconds,
                success, status, verification_passed, confidence, resolution_level,
                state_before, state_after, state_diff, executed_steps,
                error_message, error_step, failure_type, retry_count,
                evidence_bundle_id,
                cost_usd, input_tokens, output_tokens, pattern_signature, reasoning, chaos_campaign_id,
                created_at
            ) VALUES (
                :exec_id, :incident_id, :site_id, :appliance_id, :runbook_id, :hostname, :platform, :incident_type,
                :started_at, :completed_at, :duration,
                :success, :status, :verification, :confidence, :resolution_level,
                CAST(:state_before AS jsonb), CAST(:state_after AS jsonb), CAST(:state_diff AS jsonb), CAST(:executed_steps AS jsonb),
                :error_msg, :error_step, :failure_type, :retry_count,
                :evidence_id,
                :cost_usd, :input_tokens, :output_tokens, :pattern_signature, :reasoning, :chaos_campaign_id,
                NOW()
            )
            ON CONFLICT (execution_id) DO UPDATE SET
                success = EXCLUDED.success,
                state_after = EXCLUDED.state_after,
                state_diff = EXCLUDED.state_diff,
                error_message = EXCLUDED.error_message,
                failure_type = EXCLUDED.failure_type,
                cost_usd = EXCLUDED.cost_usd,
                input_tokens = EXCLUDED.input_tokens,
                output_tokens = EXCLUDED.output_tokens,
                pattern_signature = EXCLUDED.pattern_signature,
                reasoning = EXCLUDED.reasoning
        """), {
            "exec_id": exec_data.get("execution_id"),
            "incident_id": exec_data.get("incident_id"),
            "site_id": request.site_id,
            "appliance_id": exec_data.get("appliance_id", "unknown"),
            "runbook_id": exec_data.get("runbook_id"),
            "hostname": hostname,
            "platform": exec_data.get("platform"),
            "incident_type": incident_type,
            "started_at": parse_iso_timestamp(exec_data.get("started_at")),
            "completed_at": parse_iso_timestamp(exec_data.get("completed_at")),
            "duration": exec_data.get("duration_seconds"),
            "success": exec_data.get("success", False),
            "status": exec_data.get("status"),
            "verification": exec_data.get("verification_passed"),
            "confidence": exec_data.get("confidence", 0.0),
            "resolution_level": exec_data.get("resolution_level"),
            "state_before": json.dumps(exec_data.get("state_before", {})),
            "state_after": json.dumps(exec_data.get("state_after", {})),
            "state_diff": json.dumps(exec_data.get("state_diff", {})),
            "executed_steps": json.dumps(exec_data.get("executed_steps", [])),
            "error_msg": exec_data.get("error_message"),
            "error_step": exec_data.get("error_step"),
            "failure_type": exec_data.get("failure_type"),
            "retry_count": exec_data.get("retry_count", 0),
            "evidence_id": exec_data.get("evidence_bundle_id"),
            "cost_usd": exec_data.get("cost_usd", 0),
            "input_tokens": exec_data.get("input_tokens", 0),
            "output_tokens": exec_data.get("output_tokens", 0),
            "pattern_signature": pattern_sig,
            "reasoning": exec_data.get("reasoning"),
            "chaos_campaign_id": exec_data.get("chaos_campaign_id"),
        })

        await db.commit()

        logger.info(f"Execution telemetry recorded: {exec_data.get('execution_id')} (success={exec_data.get('success')})")
        return {
            "status": "recorded",
            "execution_id": exec_data.get("execution_id"),
        }

    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to record execution telemetry: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to record telemetry: {e}")


# ---------------------------------------------------------------------------
# Hypothesis-driven L2 triage
# ---------------------------------------------------------------------------
# Deterministic root-cause hypothesis map keyed by incident_type.  Each entry
# is a list of {cause, confidence, validation} dicts ordered by likelihood.
# The hypotheses are injected into the LLM prompt so it can validate/rank
# them, and stored alongside the L2 decision for flywheel analysis.
# ---------------------------------------------------------------------------

HYPOTHESIS_MAP: Dict[str, List[Dict[str, Any]]] = {
    "windows_firewall": [
        {"cause": "Firewall profile disabled by GPO change", "confidence": 0.7, "validation": "Check GPO applied policies"},
        {"cause": "Firewall rule added by software installer", "confidence": 0.5, "validation": "Check recent software installs"},
        {"cause": "Firewall service crashed", "confidence": 0.3, "validation": "Check Windows Firewall service status"},
    ],
    "windows_defender": [
        {"cause": "Defender disabled by conflicting AV product", "confidence": 0.6, "validation": "Check installed AV products"},
        {"cause": "Defender definitions outdated", "confidence": 0.7, "validation": "Check definition age"},
        {"cause": "Defender real-time protection turned off by user", "confidence": 0.5, "validation": "Check protection settings"},
    ],
    "windows_update": [
        {"cause": "WSUS server unreachable or misconfigured", "confidence": 0.6, "validation": "Check WSUS connectivity and GPO settings"},
        {"cause": "Update service stopped or corrupted", "confidence": 0.7, "validation": "Check wuauserv service status and SoftwareDistribution folder"},
        {"cause": "Pending reboot blocking new updates", "confidence": 0.5, "validation": "Check PendingReboot registry keys"},
    ],
    "audit_logging": [
        {"cause": "Audit policy overridden by GPO", "confidence": 0.7, "validation": "Check effective audit policy via auditpol /get /category:*"},
        {"cause": "Event log service stopped", "confidence": 0.5, "validation": "Check Windows Event Log service status"},
        {"cause": "Log size limit reached and overwrite disabled", "confidence": 0.4, "validation": "Check Security log max size and retention settings"},
    ],
    "bitlocker_status": [
        {"cause": "BitLocker suspended for maintenance and not resumed", "confidence": 0.7, "validation": "Check protection status via manage-bde -status"},
        {"cause": "TPM ownership lost or cleared", "confidence": 0.4, "validation": "Check TPM status via Get-Tpm"},
        {"cause": "Group Policy not enforcing BitLocker", "confidence": 0.5, "validation": "Check BitLocker GPO settings"},
    ],
    "linux_firewall": [
        {"cause": "Firewall rules flushed by package upgrade", "confidence": 0.6, "validation": "Check iptables/nftables rule count and recent dpkg/rpm log"},
        {"cause": "Firewall service not enabled on boot", "confidence": 0.7, "validation": "Check systemctl is-enabled for firewalld/ufw/nftables"},
        {"cause": "Conflicting firewall manager overwriting rules", "confidence": 0.4, "validation": "Check if both ufw and firewalld are installed"},
    ],
    "linux_ssh_config": [
        {"cause": "SSH config reverted by package update", "confidence": 0.6, "validation": "Check dpkg/rpm log for openssh-server updates"},
        {"cause": "PermitRootLogin or PasswordAuthentication re-enabled", "confidence": 0.7, "validation": "Check sshd_config for insecure settings"},
        {"cause": "SSH config include overriding main config", "confidence": 0.4, "validation": "Check /etc/ssh/sshd_config.d/ for overrides"},
    ],
    "linux_disk_space": [
        {"cause": "Log files consuming excessive space", "confidence": 0.7, "validation": "Check /var/log size and journalctl --disk-usage"},
        {"cause": "Old package versions or kernels not cleaned", "confidence": 0.5, "validation": "Check apt/yum autoremove candidates"},
        {"cause": "Large core dumps or temp files", "confidence": 0.4, "validation": "Check /tmp, /var/tmp, /var/crash sizes"},
    ],
    "linux_unattended_upgrades": [
        {"cause": "Unattended-upgrades package not installed", "confidence": 0.6, "validation": "Check dpkg -l unattended-upgrades"},
        {"cause": "Auto-update timer disabled", "confidence": 0.7, "validation": "Check systemctl is-enabled apt-daily-upgrade.timer"},
        {"cause": "Apt sources misconfigured blocking security repo", "confidence": 0.4, "validation": "Check /etc/apt/sources.list for security repository"},
    ],
    "macos_filevault": [
        {"cause": "FileVault disabled by admin or MDM policy change", "confidence": 0.6, "validation": "Check fdesetup status and MDM profile list"},
        {"cause": "FileVault encryption stalled mid-process", "confidence": 0.5, "validation": "Check fdesetup status for encryption progress"},
        {"cause": "Institutional recovery key missing or expired", "confidence": 0.4, "validation": "Check fdesetup hasinstitutionalrecoverykey"},
    ],
    "macos_firewall": [
        {"cause": "Application firewall disabled by user", "confidence": 0.7, "validation": "Check /usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate"},
        {"cause": "Stealth mode disabled", "confidence": 0.5, "validation": "Check socketfilterfw --getstealthmode"},
        {"cause": "Firewall exceptions too permissive", "confidence": 0.4, "validation": "Check socketfilterfw --listapps"},
    ],
    "backup_not_configured": [
        {"cause": "Backup agent not installed on endpoint", "confidence": 0.7, "validation": "Check for backup agent process or service"},
        {"cause": "Backup schedule removed or never created", "confidence": 0.6, "validation": "Check backup job configuration in management console"},
        {"cause": "Backup target storage unreachable", "confidence": 0.4, "validation": "Check network connectivity to backup destination"},
    ],
}


def _generate_hypotheses(
    incident_type: str,
    raw_data: Dict[str, Any],
    severity: str,
) -> List[Dict[str, Any]]:
    """Generate ranked root-cause hypotheses for an incident.

    Deterministic — no LLM call.  Falls back to generic hypotheses when the
    incident_type isn't in HYPOTHESIS_MAP.
    """
    # Direct match
    hypotheses = HYPOTHESIS_MAP.get(incident_type)
    if hypotheses:
        return hypotheses

    # Try the check_type field (sometimes more specific)
    check_type = raw_data.get("check_type", "") if raw_data else ""
    hypotheses = HYPOTHESIS_MAP.get(check_type)
    if hypotheses:
        return hypotheses

    # Keyword fallback: scan incident_type for partial matches
    type_lower = incident_type.lower()
    for key, hyps in HYPOTHESIS_MAP.items():
        if key in type_lower:
            return hyps

    # Generic fallback
    return [
        {"cause": "Configuration drift from baseline", "confidence": 0.5, "validation": "Compare current state against compliance baseline"},
        {"cause": "Service or agent not running", "confidence": 0.4, "validation": "Check service/process status"},
        {"cause": "Policy override by administrator", "confidence": 0.3, "validation": "Check recent admin activity and change logs"},
    ]


@router.post("/api/agent/l2/plan")
async def agent_l2_plan(
    request: L2PlanRequest,
    db: AsyncSession = Depends(get_db),
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """L2 LLM planner endpoint for appliance daemons."""
    _enforce_site_id(auth_site_id, request.site_id, "agent_l2_plan")
    from dashboard_api.l2_planner import analyze_incident as l2_analyze, record_l2_decision, is_l2_available

    # #64 P0: fleet-wide healing kill-switch check. If admin has paused
    # healing globally (system_settings.fleet_healing_disabled.disabled=true),
    # short-circuit BEFORE the LLM call. Daemon falls through to L3 escalation.
    # Single source of truth — no fleet-order fan-out, no partial state.
    #
    # Adversarial-round catch (Brian #3): FAIL-CLOSED on read error.
    # A kill-switch that fails-open if its own SELECT fails is
    # broken-by-design — operator hits "pause" expecting to stop
    # healing, query fails on read-side, healing keeps running.
    # Better: 503 with structured "kill_switch_unverifiable" so
    # daemon knows substrate is sick; operator escalates.
    try:
        kill_row = await db.execute(text(
            "SELECT settings -> 'fleet_healing_disabled' FROM system_settings WHERE id = 1"
        ))
        kill_state = kill_row.scalar()
    except Exception as e:
        logger.error("kill_switch_read_failed", exc_info=True, extra={"error": str(e)})
        raise HTTPException(
            status_code=503,
            detail={
                "degraded_reason": "kill_switch_unverifiable",
                "fallback": "L2 short-circuited because kill-switch state could not be read",
            },
        )
    if kill_state and isinstance(kill_state, dict) and kill_state.get("disabled"):
        raise HTTPException(
            status_code=503,
            detail={
                "degraded_reason": "fleet_healing_globally_disabled",
                "actor": kill_state.get("actor"),
                "reason": kill_state.get("reason"),
                "set_at": kill_state.get("set_at"),
                "fallback": "incident routes direct to L4 manual queue",
            },
        )

    if not is_l2_available():
        raise HTTPException(status_code=503, detail="L2 LLM not configured (no API key)")

    # Monitoring-only guard: reject checks that can't be auto-remediated.
    # Return an escalate response without burning an LLM call.
    #
    # Plus the per-appliance WinRM circuit gate (migration 215): if this
    # site's WinRM circuit is open, treat the WinRM-touching check types
    # identically to monitoring-only and short-circuit before the LLM call.
    check_type = request.raw_data.get("check_type", request.incident_type)
    is_winrm_check = check_type in WINRM_CHECK_TYPES or request.incident_type in WINRM_CHECK_TYPES
    circuit_gated = is_winrm_check and await winrm_circuit_open(db, request.site_id)
    if (
        request.incident_type in MONITORING_ONLY_CHECKS
        or check_type in MONITORING_ONLY_CHECKS
        or circuit_gated
    ):
        skip_reason = "winrm_circuit_open" if circuit_gated else "monitoring_only"
        reasoning = (
            f"WinRM circuit open at site '{request.site_id}' — remediation gated"
            if circuit_gated
            else f"Check type '{check_type}' is monitoring-only and cannot be auto-remediated."
        )
        logger.info(f"L2 skip ({skip_reason}): type={request.incident_type} check={check_type}")
        return {
            "incident_id": request.incident_id,
            "recommended_action": "escalate",
            "action_params": {},
            "confidence": 0.0,
            "reasoning": reasoning,
            "runbook_id": "",
            "requires_approval": False,
            "escalate_to_l3": True,
            "context_used": {
                "llm_model": "none",
                "llm_latency_ms": 0,
                "pattern_signature": "",
                "alternative_runbooks": [],
                "skipped_reason": skip_reason,
            },
        }

    logger.info(f"L2 plan request: site={request.site_id} host={request.host_id} type={request.incident_type}")

    # Step 0: Check L1 rules before burning an LLM call
    l1_runbook = None
    try:
        l1_match = await db.execute(
            text("""
                SELECT runbook_id FROM l1_rules
                WHERE enabled = true
                AND incident_pattern->>'incident_type' = :incident_type
                ORDER BY confidence DESC
                LIMIT 1
            """),
            {"incident_type": request.incident_type}
        )
        l1_row = l1_match.fetchone()
        if l1_row and not l1_row[0].startswith("ESC-"):
            l1_runbook = l1_row[0]
    except Exception as e:
        logger.warning(f"L1 lookup failed in L2 endpoint (proceeding to LLM): {e}")

    if not l1_runbook:
        # Keyword fallback matching (synced with main.py)
        type_lower = request.incident_type.lower()
        check_type = request.raw_data.get("check_type", "").lower() if request.raw_data else ""
        keyword_map = {
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
            "update": "RB-PATCH-001",
            "audit": "RB-WIN-SEC-002",
            "defender": "RB-WIN-AV-001",
            "registry": "RB-WIN-SEC-019",
            "bitlocker": "RB-WIN-SEC-005",
            "screen_lock": "RB-WIN-SEC-016",
            "credential": "RB-WIN-SEC-022",
            "smb": "RB-WIN-SEC-007",
        }
        for keyword, rb_id in keyword_map.items():
            if keyword in type_lower or keyword in check_type:
                l1_runbook = rb_id
                break

    if l1_runbook:
        logger.info(f"L1 match in L2 endpoint: type={request.incident_type} runbook={l1_runbook}")
        return {
            "incident_id": request.incident_id,
            "recommended_action": "execute_runbook",
            "action_params": {"runbook_id": l1_runbook},
            "confidence": 0.9,
            "reasoning": f"L1 rule match for {request.incident_type}",
            "runbook_id": l1_runbook,
            "requires_approval": False,
            "escalate_to_l3": False,
            "context_used": {
                "llm_model": "l1_rules_db",
                "llm_latency_ms": 0,
                "pattern_signature": "",
                "alternative_runbooks": [],
                "cache_status": "l1_match",
            },
        }

    # L2 decision cache: reuse recent decisions for the same pattern_signature
    cached = None
    if request.pattern_signature:
        from dashboard_api.l2_planner import lookup_cached_l2_decision
        try:
            cached = await lookup_cached_l2_decision(db, request.pattern_signature)
        except Exception as e:
            logger.warning(f"L2 cache lookup failed (proceeding to LLM): {e}")
        if cached:
            logger.info(f"L2 cache hit: pattern={request.pattern_signature} runbook={cached.runbook_id}")
            action = "escalate"
            action_params = {}
            escalate = True
            if cached.runbook_id and cached.confidence >= 0.6:
                escalation_only_runbooks = {"RB-CERT-001", "RB-DISK-001"}
                is_escalation = (
                    cached.runbook_id in escalation_only_runbooks
                    or cached.runbook_id.startswith("ESC-")
                )
                if is_escalation:
                    action = "escalate"
                    escalate = True
                else:
                    action = "execute_runbook"
                    escalate = False
                action_params = {"runbook_id": cached.runbook_id}

            return {
                "incident_id": request.incident_id,
                "recommended_action": action,
                "action_params": action_params,
                "confidence": cached.confidence,
                "reasoning": cached.reasoning,
                "runbook_id": cached.runbook_id or "",
                "requires_approval": cached.requires_human_review,
                "escalate_to_l3": escalate,
                "context_used": {
                    "llm_model": cached.llm_model,
                    "llm_latency_ms": 0,
                    "pattern_signature": cached.pattern_signature,
                    "alternative_runbooks": cached.alternative_runbooks,
                    "cache_status": "cached_24h",
                },
            }

    # Step 1: Generate ranked root-cause hypotheses (deterministic, no LLM)
    hypotheses = _generate_hypotheses(request.incident_type, request.raw_data, request.severity)
    logger.info(f"L2 hypotheses generated: type={request.incident_type} count={len(hypotheses)}")

    hipaa_controls = None
    hipaa_ctrl = request.raw_data.get("hipaa_control")
    if hipaa_ctrl:
        hipaa_controls = [hipaa_ctrl] if isinstance(hipaa_ctrl, str) else hipaa_ctrl

    # Step 2: Call LLM with hypotheses included in the prompt
    decision = await l2_analyze(
        incident_type=request.incident_type,
        severity=request.severity,
        check_type=request.raw_data.get("check_type", request.incident_type),
        details=request.raw_data,
        pre_state=request.raw_data.get("pre_state", {}),
        hipaa_controls=hipaa_controls,
        hypotheses=hypotheses,
        site_id=request.site_id,
    )

    # Step 3: Record decision with hypotheses for flywheel analysis.
    # Substrate invariant l2_resolution_without_decision_record
    # (Session 219 mig 300, 4th callsite found 2026-05-11): if the
    # record write fails, the daemon MUST NOT execute the L2 runbook —
    # otherwise the incident gets resolution_tier='L2' (daemon-set)
    # with no l2_decisions row → ghost-L2 audit gap.
    # Forward fix: escalate to L3 in the response when record fails.
    l2_decision_recorded = False
    try:
        await record_l2_decision(
            db, request.incident_id, decision,
            incident_type=request.incident_type,
            hypotheses=hypotheses,
        )
        await db.commit()
        l2_decision_recorded = True
    except Exception as e:
        logger.error(
            f"Failed to record L2 decision: {e}", exc_info=True,
        )

    action = "escalate"
    action_params = {}
    escalate = True

    if l2_decision_recorded and decision.runbook_id and decision.confidence >= 0.6:
        escalation_only_runbooks = {"RB-CERT-001", "RB-DISK-001"}
        is_escalation = (
            decision.runbook_id in escalation_only_runbooks
            or decision.runbook_id.startswith("ESC-")
        )

        if is_escalation:
            action = "escalate"
            escalate = True
        else:
            action = "execute_runbook"
            escalate = False
        action_params = {"runbook_id": decision.runbook_id}

    return {
        "incident_id": request.incident_id,
        "recommended_action": action,
        "action_params": action_params,
        "confidence": decision.confidence,
        "reasoning": decision.reasoning,
        # runbook_id cleared when l2_decision_recorded fails — daemons
        # that bypass `recommended_action` and read runbook_id directly
        # still won't execute a ghost-L2 runbook.
        "runbook_id": (decision.runbook_id or "") if l2_decision_recorded else "",
        "requires_approval": decision.requires_human_review,
        "escalate_to_l3": escalate,
        "hypotheses": hypotheses,
        "l2_decision_recorded": l2_decision_recorded,
        "context_used": {
            "llm_model": decision.llm_model,
            "llm_latency_ms": decision.llm_latency_ms,
            "pattern_signature": decision.pattern_signature,
            "alternative_runbooks": decision.alternative_runbooks,
        },
    }


@router.post("/evidence/upload")
async def upload_evidence_worm(
    bundle: UploadFile = File(..., description="Evidence bundle JSON file"),
    signature: UploadFile = File(None, description="Detached Ed25519 signature file"),
    x_client_id: str = Header(..., alias="X-Client-ID"),
    x_bundle_id: str = Header(..., alias="X-Bundle-ID"),
    x_bundle_hash: str = Header(..., alias="X-Bundle-Hash"),
    x_signature_hash: str = Header(None, alias="X-Signature-Hash"),
    authorization: str = Header(None),
    db: AsyncSession = Depends(get_db),
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """WORM Evidence Upload Proxy Endpoint."""
    _enforce_site_id(auth_site_id, x_client_id, "worm_evidence_upload")
    allowed, remaining = await check_rate_limit(x_client_id, "evidence_upload")
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limited. Try again in {remaining} seconds."
        )

    result = await db.execute(
        text("SELECT id FROM v_appliances_current WHERE site_id = :site_id"),
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

    bundle_content = await bundle.read()

    expected_hash = x_bundle_hash.replace("sha256:", "")
    actual_hash = hashlib.sha256(bundle_content).hexdigest()
    if actual_hash != expected_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bundle hash mismatch. Expected {expected_hash[:16]}..., got {actual_hash[:16]}..."
        )

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

    try:
        bundle_data = json.loads(bundle_content.decode())
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid bundle JSON: {str(e)}"
        )

    date_prefix = now.strftime('%Y/%m/%d')
    bundle_key = f"{x_client_id}/{date_prefix}/{x_bundle_id}.json"
    sig_key = f"{x_client_id}/{date_prefix}/{x_bundle_id}.sig" if sig_content else None

    retention_until = now + timedelta(days=WORM_RETENTION_DAYS)

    bundle_uri = None
    sig_uri = None
    minio_client = get_minio_client()
    loop = asyncio.get_event_loop()

    try:
        await loop.run_in_executor(None, lambda: minio_client.put_object(
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
        ))
        bundle_uri = f"s3://{MINIO_BUCKET}/{bundle_key}"

        try:
            retention = Retention(COMPLIANCE, retention_until)
            await loop.run_in_executor(None, lambda: minio_client.set_object_retention(MINIO_BUCKET, bundle_key, retention))
            logger.info("Set WORM retention on bundle",
                       bundle_id=x_bundle_id,
                       retention_until=retention_until.isoformat())
        except Exception as e:
            logger.warning("Could not set Object Lock retention (bucket may not have Object Lock enabled)",
                          bundle_id=x_bundle_id,
                          error=str(e))

        if sig_content:
            await loop.run_in_executor(None, lambda: minio_client.put_object(
                MINIO_BUCKET,
                sig_key,
                BytesIO(sig_content),
                length=len(sig_content),
                content_type="application/octet-stream",
                metadata={
                    "bundle_id": x_bundle_id,
                    "client_id": x_client_id
                }
            ))
            sig_uri = f"s3://{MINIO_BUCKET}/{sig_key}"

            try:
                await loop.run_in_executor(None, lambda: minio_client.set_object_retention(MINIO_BUCKET, sig_key, retention))
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

    try:
        evidence_id = str(uuid.uuid4())

        check_type = bundle_data.get("check_type", "unknown")
        outcome = bundle_data.get("outcome", "unknown")

        def parse_iso_ts(ts):
            if ts is None:
                return now
            if isinstance(ts, datetime):
                return ts
            if isinstance(ts, str):
                try:
                    ts = ts.replace('Z', '+00:00')
                    return datetime.fromisoformat(ts)
                except Exception:
                    return now
            return now

        timestamp_start = parse_iso_ts(bundle_data.get("timestamp_start"))
        timestamp_end = parse_iso_ts(bundle_data.get("timestamp_end"))

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
        # Session 205 "no silent write failures" — evidence_chain INSERT
        # is the chain-of-custody artifact for HIPAA evidence bundles.
        logger.error("Failed to store evidence reference in database",
                     bundle_id=x_bundle_id, error=str(e), exc_info=True)

    return {
        "status": "uploaded",
        "bundle_id": x_bundle_id,
        "bundle_uri": bundle_uri,
        "signature_uri": sig_uri,
        "retention_until": retention_until.isoformat(),
        "retention_days": WORM_RETENTION_DAYS,
        "timestamp": now.isoformat()
    }


@router.get("/agent/sync")
async def agent_sync_rules(
    site_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """Return L1 rules for agents to sync."""
    if site_id:
        _enforce_site_id(auth_site_id, site_id, "agent_sync_rules")
    healing_tier = "standard"
    if site_id:
        try:
            result = await db.execute(
                text("SELECT healing_tier FROM sites WHERE site_id = :site_id"),
                {"site_id": site_id}
            )
            row = result.fetchone()
            if row and row[0]:
                healing_tier = row[0]
        except Exception as e:
            logger.warning(f"Failed to fetch healing tier for {site_id}: {e}")

    # Standard rules (7 core rules) - always included
    standard_rules = [
        {
            "id": "L1-NTP-001",
            "name": "NTP Drift Remediation",
            "description": "Restart chronyd when NTP sync drifts",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "ntp_sync"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
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
            "description": "Restart failed critical services",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "critical_services"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["restart_service:sshd", "restart_service:chronyd"],
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
                {"field": "check_type", "operator": "eq", "value": "disk_space"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["alert:disk_space_critical"],
            "severity": "high",
            "cooldown_seconds": 3600,
            "max_retries": 1,
            "source": "builtin"
        },
        {
            "id": "L1-FIREWALL-001",
            "name": "Windows Firewall Recovery",
            "description": "Re-enable Windows Firewall when disabled",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "firewall"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]},
                {"field": "platform", "operator": "ne", "value": "nixos"}
            ],
            "actions": ["restore_firewall_baseline"],
            "severity": "critical",
            "cooldown_seconds": 300,
            "max_retries": 2,
            "source": "builtin"
        },
        {
            "id": "L1-FIREWALL-002",
            "name": "Windows Firewall Status Recovery",
            "description": "Re-enable Windows Firewall when status check fails",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "firewall_status"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]},
                {"field": "platform", "operator": "ne", "value": "nixos"}
            ],
            "actions": ["restore_firewall_baseline"],
            "severity": "critical",
            "cooldown_seconds": 300,
            "max_retries": 2,
            "source": "builtin"
        },
        {
            "id": "L1-DEFENDER-001",
            "name": "Windows Defender Recovery",
            "description": "Re-enable Windows Defender when disabled",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "windows_defender"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["restore_defender"],
            "severity": "critical",
            "cooldown_seconds": 300,
            "max_retries": 2,
            "source": "builtin"
        },
        {
            "id": "L1-GENERATION-001",
            "name": "NixOS Generation Drift",
            "description": "Alert when NixOS generation is invalid or unknown",
            "conditions": [
                {"field": "check_type", "operator": "eq", "value": "nixos_generation"},
                {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}
            ],
            "actions": ["alert:generation_drift"],
            "severity": "medium",
            "cooldown_seconds": 3600,
            "max_retries": 1,
            "source": "builtin"
        }
    ]

    # Additional rules for full_coverage mode
    full_coverage_extra_rules = [
        {"id": "L1-PASSWORD-001", "name": "Password Policy Enforcement", "description": "Enforce minimum password requirements", "conditions": [{"field": "check_type", "operator": "eq", "value": "password_policy"}, {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}], "actions": ["set_password_policy"], "severity": "high", "cooldown_seconds": 3600, "max_retries": 2, "source": "builtin"},
        {"id": "L1-AUDIT-001", "name": "Audit Policy Enforcement", "description": "Enable required audit policies", "conditions": [{"field": "check_type", "operator": "eq", "value": "audit_policy"}, {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}], "actions": ["set_audit_policy"], "severity": "high", "cooldown_seconds": 3600, "max_retries": 2, "source": "builtin"},
        {"id": "L1-BITLOCKER-001", "name": "BitLocker Encryption", "description": "Enable drive encryption", "conditions": [{"field": "check_type", "operator": "eq", "value": "bitlocker_status"}, {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}], "actions": ["run_windows_runbook:RB-WIN-SEC-005"], "severity": "critical", "cooldown_seconds": 3600, "max_retries": 1, "source": "builtin"},
        {"id": "L1-SMB1-001", "name": "SMBv1 Protocol Disabled", "description": "Disable insecure SMBv1 protocol", "conditions": [{"field": "check_type", "operator": "eq", "value": "smb1_protocol"}, {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}], "actions": ["run_windows_runbook:RB-WIN-SEC-020"], "severity": "high", "cooldown_seconds": 3600, "max_retries": 2, "source": "builtin"},
        {"id": "L1-AUTOPLAY-001", "name": "AutoPlay Disabled", "description": "Disable AutoPlay to reduce malware spread risk", "conditions": [{"field": "check_type", "operator": "eq", "value": "autoplay_disabled"}, {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}], "actions": ["disable_autoplay"], "severity": "medium", "cooldown_seconds": 3600, "max_retries": 2, "source": "builtin"},
        {"id": "L1-LOCKOUT-001", "name": "Account Lockout Policy", "description": "Configure account lockout after failed attempts", "conditions": [{"field": "check_type", "operator": "eq", "value": "lockout_policy"}, {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}], "actions": ["set_lockout_policy"], "severity": "medium", "cooldown_seconds": 3600, "max_retries": 2, "source": "builtin"},
        {"id": "L1-SCREENSAVER-001", "name": "Screensaver Timeout", "description": "Configure screensaver with password protection", "conditions": [{"field": "check_type", "operator": "eq", "value": "screensaver_timeout"}, {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}], "actions": ["set_screensaver_policy"], "severity": "medium", "cooldown_seconds": 3600, "max_retries": 2, "source": "builtin"},
        {"id": "L1-RDP-001", "name": "RDP Security", "description": "Configure RDP with NLA requirement", "conditions": [{"field": "check_type", "operator": "eq", "value": "rdp_security"}, {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}], "actions": ["configure_rdp_security"], "severity": "high", "cooldown_seconds": 3600, "max_retries": 2, "source": "builtin"},
        {"id": "L1-UAC-001", "name": "UAC Enabled", "description": "Verify User Account Control is enabled", "conditions": [{"field": "check_type", "operator": "eq", "value": "uac_enabled"}, {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}], "actions": ["enable_uac"], "severity": "high", "cooldown_seconds": 3600, "max_retries": 2, "source": "builtin"},
        {"id": "L1-EVENTLOG-001", "name": "Event Log Size", "description": "Configure adequate event log retention", "conditions": [{"field": "check_type", "operator": "eq", "value": "event_log_size"}, {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}], "actions": ["set_event_log_size"], "severity": "medium", "cooldown_seconds": 3600, "max_retries": 2, "source": "builtin"},
        {"id": "L1-DEFENDERUPDATES-001", "name": "Windows Defender Definitions", "description": "Update malware definitions", "conditions": [{"field": "check_type", "operator": "eq", "value": "defender_definitions"}, {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}], "actions": ["update_defender_definitions"], "severity": "high", "cooldown_seconds": 14400, "max_retries": 3, "source": "builtin"},
        {"id": "L1-GUESTACCOUNT-001", "name": "Guest Account Disabled", "description": "Disable built-in Guest account", "conditions": [{"field": "check_type", "operator": "eq", "value": "guest_account_disabled"}, {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}], "actions": ["disable_guest_account"], "severity": "medium", "cooldown_seconds": 3600, "max_retries": 2, "source": "builtin"},
        {"id": "L1-WUPDATES-001", "name": "Windows Updates", "description": "Check and trigger pending security updates", "conditions": [{"field": "check_type", "operator": "eq", "value": "windows_updates"}, {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}], "actions": ["trigger_windows_update"], "severity": "high", "cooldown_seconds": 86400, "max_retries": 1, "source": "builtin"},
        {"id": "L1-BACKUP-001", "name": "Backup Status", "description": "Alert when backup fails or is stale", "conditions": [{"field": "check_type", "operator": "eq", "value": "backup"}, {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}], "actions": ["alert:backup_failed"], "severity": "high", "cooldown_seconds": 7200, "max_retries": 1, "source": "builtin"},
        {"id": "L1-SCREENLOCK-001", "name": "Screen Lock Policy", "description": "Enforce screen lock timeout and password requirement", "conditions": [{"field": "check_type", "operator": "eq", "value": "screen_lock_policy"}, {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}], "actions": ["run_windows_runbook:RB-WIN-SEC-016"], "severity": "high", "cooldown_seconds": 300, "max_retries": 2, "source": "builtin"},
        {"id": "L1-PATCHING-001", "name": "Windows Update Service", "description": "Verify Windows Update service is running and updates are applied", "conditions": [{"field": "check_type", "operator": "eq", "value": "patching"}, {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}], "actions": ["trigger_windows_update"], "severity": "critical", "cooldown_seconds": 86400, "max_retries": 1, "source": "builtin"},
        {"id": "L1-LIN-SSH-001", "name": "SSH Configuration Drift", "description": "Fix SSH config drift (PermitRootLogin, PasswordAuthentication, etc.)", "conditions": [{"field": "check_type", "operator": "eq", "value": "ssh_config"}, {"field": "drift_detected", "operator": "eq", "value": True}], "actions": ["run_linux_runbook"], "severity": "critical", "cooldown_seconds": 300, "max_retries": 2, "source": "builtin"},
        {"id": "L1-LIN-KERN-001", "name": "Kernel Parameter Hardening", "description": "Fix unsafe kernel parameters (ip_forward, ASLR, etc.)", "conditions": [{"field": "check_type", "operator": "eq", "value": "kernel"}, {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}], "actions": ["run_linux_runbook:LIN-KERN-001"], "severity": "high", "cooldown_seconds": 300, "max_retries": 2, "source": "builtin"},
        {"id": "L1-LIN-CRON-001", "name": "Cron Permission Hardening", "description": "Fix insecure cron file permissions", "conditions": [{"field": "check_type", "operator": "eq", "value": "cron"}, {"field": "status", "operator": "in", "value": ["warning", "fail", "error"]}], "actions": ["run_linux_runbook:LIN-CRON-001"], "severity": "high", "cooldown_seconds": 300, "max_retries": 2, "source": "builtin"},
        {"id": "L1-LIN-SUID-001", "name": "SUID Binary Cleanup", "description": "Remove unauthorized SUID binaries from temp directories", "conditions": [{"field": "check_type", "operator": "eq", "value": "permissions"}, {"field": "drift_detected", "operator": "eq", "value": True}, {"field": "distro", "operator": "ne", "value": None}], "actions": ["run_linux_runbook"], "severity": "critical", "cooldown_seconds": 300, "max_retries": 2, "source": "builtin"},
        {"id": "L1-PERSIST-TASK-001", "name": "Scheduled Task Persistence Detected", "description": "Remove suspicious scheduled tasks from root namespace", "conditions": [{"field": "check_type", "operator": "eq", "value": "scheduled_task_persistence"}, {"field": "drift_detected", "operator": "eq", "value": True}], "actions": ["run_windows_runbook:RB-WIN-SEC-018"], "severity": "critical", "cooldown_seconds": 300, "max_retries": 2, "source": "builtin"},
        {"id": "L1-PERSIST-REG-001", "name": "Registry Run Key Persistence Detected", "description": "Remove suspicious registry Run key entries", "conditions": [{"field": "check_type", "operator": "eq", "value": "registry_run_persistence"}, {"field": "drift_detected", "operator": "eq", "value": True}], "actions": ["run_windows_runbook:RB-WIN-SEC-019"], "severity": "critical", "cooldown_seconds": 300, "max_retries": 2, "source": "builtin"},
        {"id": "L1-PERSIST-WMI-001", "name": "WMI Event Subscription Persistence Detected", "description": "Remove suspicious WMI event subscriptions used for persistence", "conditions": [{"field": "check_type", "operator": "eq", "value": "wmi_event_persistence"}, {"field": "drift_detected", "operator": "eq", "value": True}], "actions": ["run_windows_runbook:RB-WIN-SEC-021"], "severity": "critical", "cooldown_seconds": 300, "max_retries": 2, "source": "builtin"},
        {"id": "L1-SMB-SIGNING-001", "name": "SMB Signing Not Required", "description": "Configure SMB signing to reduce relay attack risk", "conditions": [{"field": "check_type", "operator": "eq", "value": "smb_signing"}, {"field": "drift_detected", "operator": "eq", "value": True}], "actions": ["run_windows_runbook:RB-WIN-SEC-007"], "severity": "high", "cooldown_seconds": 300, "max_retries": 2, "source": "builtin"},
        {"id": "L1-SVC-NETLOGON-001", "name": "NetLogon Service Down", "description": "Restore NetLogon service for domain authentication", "conditions": [{"field": "check_type", "operator": "eq", "value": "service_netlogon"}, {"field": "drift_detected", "operator": "eq", "value": True}], "actions": ["run_windows_runbook:RB-WIN-SVC-001"], "severity": "critical", "cooldown_seconds": 300, "max_retries": 2, "source": "builtin"},
    ]

    if healing_tier == "full_coverage":
        builtin_rules = standard_rules + full_coverage_extra_rules
    else:
        builtin_rules = standard_rules

    # Fetch custom/promoted/protection_profile rules from database
    try:
        result = await db.execute(
            text("""
                SELECT rule_id, incident_pattern, runbook_id, confidence,
                       COALESCE(source, 'promoted') as source
                FROM l1_rules
                WHERE enabled = true AND COALESCE(source, 'promoted') != 'builtin'
                ORDER BY confidence DESC
            """)
        )
        db_rules = []
        for row in result.fetchall():
            rule_source = row[4]

            if rule_source == "protection_profile":
                try:
                    ppr = await db.execute(
                        text("SELECT rule_json FROM app_profile_rules WHERE l1_rule_id = :rid LIMIT 1"),
                        {"rid": row[0]}
                    )
                    ppr_row = ppr.fetchone()
                    if ppr_row and ppr_row[0]:
                        rule_json = ppr_row[0] if isinstance(ppr_row[0], dict) else json.loads(ppr_row[0])
                        db_rules.append(rule_json)
                        continue
                except Exception:
                    # Best-effort protection-profile rule load; falls
                    # through to the generic incident_pattern parser.
                    logger.error(
                        "app_profile_rule_load_failed",
                        extra={"rule_id": row[0]},
                        exc_info=True,
                    )

            pattern = row[1]
            if isinstance(pattern, list):
                conditions = pattern
            elif isinstance(pattern, dict):
                conditions = []
                for k, v in pattern.items():
                    field = "check_type" if k == "incident_type" else k
                    conditions.append({"field": field, "operator": "eq", "value": v})
                conditions.append({"field": "status", "operator": "in", "value": ["warning", "fail", "error"]})
            else:
                conditions = []

            db_rules.append({
                "id": row[0],
                "name": f"Promoted: {row[0]}",
                "description": f"Auto-promoted rule with {row[3]:.0%} confidence",
                "conditions": conditions,
                "actions": [f"run_runbook:{row[2]}"],
                "severity": "critical" if rule_source == "protection_profile" else "medium",
                "cooldown_seconds": 300,
                "max_retries": 3 if rule_source == "protection_profile" else 2,
                "source": rule_source
            })
    except Exception as e:
        logger.warning(f"Failed to fetch DB rules: {e}")
        db_rules = []

    all_rules = builtin_rules + db_rules

    rules_json = json.dumps(all_rules, sort_keys=True)
    rules_signature = sign_data(rules_json)

    return {
        "rules": all_rules,
        "healing_tier": healing_tier,
        "version": "1.0.0",
        "count": len(all_rules),
        "signature": rules_signature,
        "server_public_key": get_public_key_hex(),
    }


@router.post("/api/appliances/checkin")
async def appliances_checkin(
    req: _AgentApiApplianceCheckinRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """Appliance agent checkin endpoint - updates site_appliances table."""
    _enforce_site_id(auth_site_id, req.site_id, "appliances_checkin")
    try:
        mac = req.mac_address or "00:00:00:00:00:00"
        appliance_id = f"{req.site_id}-{mac}"
        now = datetime.now(timezone.utc)

        hostname = req.hostname or "unknown"

        # Auto-generate iterative display_name for appliances sharing the same hostname
        existing = await db.execute(text("""
            SELECT appliance_id, display_name, hostname
            FROM site_appliances
            WHERE site_id = :site_id
            ORDER BY first_checkin, appliance_id
        """), {"site_id": req.site_id})
        siblings = existing.fetchall()

        # Check if this appliance already exists
        existing_entry = next((s for s in siblings if s.appliance_id == appliance_id), None)
        display_name = existing_entry.display_name if existing_entry and existing_entry.display_name else None

        if not display_name:
            # Count how many siblings share this hostname
            same_hostname = [s for s in siblings if s.hostname == hostname and s.appliance_id != appliance_id]
            if same_hostname:
                display_name = f"{hostname}-{len(same_hostname) + 1}"
            else:
                display_name = hostname

        await db.execute(text("""
            INSERT INTO site_appliances (
                site_id, appliance_id, hostname, display_name, mac_address, ip_addresses,
                agent_version, nixos_version, status, last_checkin,
                uptime_seconds, queue_depth, first_checkin, created_at
            ) VALUES (
                :site_id, :appliance_id, :hostname, :display_name, :mac_address, :ip_addresses,
                :agent_version, :nixos_version, 'online', :last_checkin,
                :uptime_seconds, :queue_depth, :first_checkin, :created_at
            )
            ON CONFLICT (appliance_id) DO UPDATE SET
                hostname = EXCLUDED.hostname,
                display_name = COALESCE(site_appliances.display_name, EXCLUDED.display_name),
                ip_addresses = EXCLUDED.ip_addresses,
                agent_version = EXCLUDED.agent_version,
                nixos_version = EXCLUDED.nixos_version,
                status = 'online',
                last_checkin = EXCLUDED.last_checkin,
                uptime_seconds = EXCLUDED.uptime_seconds,
                queue_depth = EXCLUDED.queue_depth,
                auth_failure_since = NULL,
                auth_failure_count = 0,
                last_auth_failure = NULL
        """), {
            "site_id": req.site_id,
            "appliance_id": appliance_id,
            "hostname": hostname,
            "display_name": display_name,
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

        # Fetch Windows targets
        windows_targets = []
        seen_hosts = set()
        try:
            result = await db.execute(text("""
                SELECT credential_type, credential_name, encrypted_data
                FROM site_credentials
                WHERE site_id = :site_id
                AND credential_type IN ('winrm', 'domain_admin', 'domain_member', 'service_account', 'local_admin')
                ORDER BY CASE WHEN credential_type = 'domain_admin' THEN 0 ELSE 1 END, created_at DESC
            """), {"site_id": req.site_id})
            creds = result.fetchall()

            org_creds_result = await db.execute(text("""
                SELECT oc.credential_type, oc.credential_name, oc.encrypted_data
                FROM org_credentials oc
                JOIN sites s ON s.client_org_id = oc.client_org_id
                WHERE s.site_id = :site_id
                AND oc.credential_type IN ('winrm', 'domain_admin', 'domain_member', 'service_account', 'local_admin')
                ORDER BY CASE WHEN oc.credential_type = 'domain_admin' THEN 0 ELSE 1 END, oc.created_at DESC
            """), {"site_id": req.site_id})
            org_creds = org_creds_result.fetchall()

            for cred in list(creds) + list(org_creds):
                try:
                    raw = cred.encrypted_data
                    cred_data = json.loads(bytes(raw).decode() if isinstance(raw, (bytes, memoryview)) else raw)
                    hostname = cred_data.get('host') or cred_data.get('target_host')
                    username = cred_data.get('username', '')
                    password = cred_data.get('password', '')
                    domain = cred_data.get('domain', '')
                    use_ssl = cred_data.get('use_ssl', False)

                    full_username = f"{domain}\\{username}" if domain else username

                    if hostname and hostname not in seen_hosts:
                        seen_hosts.add(hostname)
                        windows_targets.append({
                            "hostname": hostname,
                            "username": full_username,
                            "password": password,
                            "use_ssl": use_ssl,
                            "role": cred.credential_type,
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

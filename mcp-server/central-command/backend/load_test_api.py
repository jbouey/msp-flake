"""Load harness run-ledger API (Task #62 v2.1 Commits 2 + 3, 2026-05-16).

Admin-only endpoints over the `load_test_runs` table (mig 316). The
run-ledger is the server-side anchor the AlertManager-driven abort
path needs — when a wave-1 endpoint's 5xx rate spikes during a load
run, AlertManager POSTs `/api/admin/load-test/{run_id}/abort`, the row
flips to status='aborting', k6 polls `/api/admin/load-test/status`
every iteration and exits within 30s when it sees the transition.

Spec: `.agent/plans/40-load-testing-harness-design-v2.1-2026-05-16.md`
Gate A: `audit/coach-62-load-harness-v1-gate-a-2026-05-16.md`
   (APPROVE-WITH-FIXES; v2.1 closed 3 P0s + 7 P1s structurally)
Gate B (C2): `audit/coach-93-c2-and-62-c2-gate-b-2026-05-16.md`
   (APPROVE-WITH-FIXES; 1 P1 + 3 P2 closed in Commit 3)

Endpoint summary:
  POST /api/admin/load-test/runs               — start (k6 wrapper)
  POST /api/admin/load-test/{run_id}/started   — flip starting→running (k6 post-warmup)
  POST /api/admin/load-test/{run_id}/abort     — abort (operator or AM)
  POST /api/admin/load-test/{run_id}/complete  — complete (k6 wrapper)
  GET  /api/admin/load-test/status             — current active run
  GET  /api/admin/load-test/runs               — history (paginated)

Authorization:
  All endpoints require `Depends(require_admin)`. The AlertManager
  rule POSTs with a service-account bearer that resolves to an admin
  user with a named human email (on-call rotation's primary). Every
  audit row carries the bearer's authenticated email — no body-
  supplied actor_email overrides (Gate B P2 #108 closure).

NOT a privileged-chain endpoint:
  These endpoints mutate synthetic-load test infrastructure only —
  no customer evidence, no signing keys, no identity. Per the
  privileged-access chain rules (CLAUDE.md §"Privileged-Access Chain
  of Custody"), the lockstep registration is NOT required. admin_
  audit_log rows still capture the chain-of-action for ops review.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .auth import require_admin
from .fleet import get_pool
from .tenant_middleware import admin_connection, admin_transaction

logger = logging.getLogger("load_test_api")

router = APIRouter(prefix="/api/admin/load-test", tags=["load-test"])


# Wave-1 endpoint allowlist — v2.1 spec table. New endpoints require
# the CI gate `tests/test_load_harness_wave1_paths_exist.py` to be
# updated in lockstep + v2.1 spec doc bump to v2.2.
_WAVE1_ALLOWED_ENDPOINTS = {
    "/api/appliances/checkin",
    "/api/appliances/orders",  # tail-segment match; real path is /orders/{site_id}
    "/api/journal/upload",
    "/health",
}


# ---------------------------------------------------------------- models


class StartRunRequest(BaseModel):
    scenario_sha: str = Field(
        ..., min_length=8, max_length=64,
        description="SHA of the k6 script being run. Pins exact load shape "
                    "for reproducibility."
    )
    target_endpoints: List[str] = Field(
        ..., min_length=1, max_length=10,
        description="Wave-1 endpoint paths the run targets. Must all be "
                    "in the allowlist."
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional k6 args / config snapshot. Stored in metadata "
                    "JSONB for post-run review."
    )


class AbortRunRequest(BaseModel):
    reason: str = Field(
        ..., min_length=10, max_length=500,
        description="Why this run is being aborted. AlertManager rules "
                    "send the rule name + threshold; operator-initiated "
                    "aborts describe what was observed."
    )
    # Gate B P2 #108 closure: actor_email is NO LONGER a body parameter.
    # The audit row always uses the bearer's authenticated email. For
    # AlertManager-driven aborts, the AlertManager bearer must be issued
    # to the on-call rotation's named human — same person, same email,
    # no body-side spoofing surface. Carry-forward task #108 closed.


class CompleteRunRequest(BaseModel):
    final_status: str = Field(
        default="completed",
        description="completed | failed — k6 wrapper sets failed on non-"
                    "zero exit code."
    )
    metrics_summary: Optional[Dict[str, Any]] = Field(
        default=None,
        description="k6 output summary (p95/p99/error-rate by endpoint). "
                    "Merged into metadata JSONB."
    )
    revoke_bearer_appliance_id: Optional[str] = Field(
        default=None,
        description="If the k6 wrapper used a synthetic appliance bearer, "
                    "pass the appliance_id here so the /complete handler "
                    "flips site_appliances.bearer_revoked=TRUE (mig 324). "
                    "Real-appliance bearer rotation does NOT use this path."
    )


# ---------------------------------------------------------------- helpers


def _admin_email(admin: Dict[str, Any]) -> str:
    email = admin.get("email")
    if not email or "@" not in email:
        raise HTTPException(
            status_code=403,
            detail="admin identity missing a named human email — "
                   "load-test endpoints require a named operator",
        )
    return email


async def _audit(
    conn,
    *,
    actor_email: str,
    action: str,
    target: str,
    details: Dict[str, Any],
    ip_address: Optional[str],
) -> None:
    """Write a structured admin_audit_log row. Failure logs at ERROR
    but does NOT block — audit-log infra issues must not interrupt
    abort flow (P1-5 mirrors privileged-access pattern)."""
    try:
        await conn.execute(
            """
            INSERT INTO admin_audit_log
                (user_id, username, action, target, details, ip_address)
            VALUES (NULL, $1, $2, $3, $4::jsonb, $5)
            """,
            actor_email,
            action,
            target,
            json.dumps(details),
            ip_address,
        )
    except Exception:
        logger.exception(
            "admin_audit_log write failed for load-test action %s target %s",
            action, target,
        )


# ---------------------------------------------------------------- endpoints


@router.post("/runs")
async def start_run(
    req: StartRunRequest,
    request: Request,
    admin: Dict[str, Any] = Depends(require_admin),
) -> Dict[str, Any]:
    """Start a new load-test run. Returns the run_id k6 must pass back
    to /abort + /complete. Rejects if another run is active (partial
    unique index enforces ≤1 active run)."""
    actor = _admin_email(admin)

    bad = [e for e in req.target_endpoints if e not in _WAVE1_ALLOWED_ENDPOINTS]
    if bad:
        raise HTTPException(
            status_code=400,
            detail=(
                f"target_endpoints not in Wave-1 allowlist: {bad}. "
                f"Allowed: {sorted(_WAVE1_ALLOWED_ENDPOINTS)}. To expand "
                f"the wave, ship a v2.2 spec doc + CI gate update."
            ),
        )

    run_id = str(uuid.uuid4())
    pool = await get_pool()
    async with admin_transaction(pool) as conn:
        try:
            await conn.execute(
                """
                INSERT INTO load_test_runs
                    (run_id, started_by, scenario_sha, target_endpoints,
                     status, metadata)
                VALUES ($1::uuid, $2, $3, $4::text[], 'starting', $5::jsonb)
                """,
                run_id,
                actor,
                req.scenario_sha,
                req.target_endpoints,
                json.dumps(req.metadata or {}),
            )
        except asyncpg.UniqueViolationError:
            # Gate B P2 #107 closure: asyncpg-typed check instead of
            # substring-matching the error string. SQLSTATE 23505 +
            # UniqueViolationError class is the contract; index name
            # no longer appears in source.
            raise HTTPException(
                status_code=409,
                detail=(
                    "another load-test run is currently active. "
                    "Abort or complete it before starting a new one."
                ),
            )

        await _audit(
            conn,
            actor_email=actor,
            action="load_test_run_started",
            target=f"load_test_runs/{run_id}",
            details={
                "run_id": run_id,
                "scenario_sha": req.scenario_sha,
                "target_endpoints": req.target_endpoints,
            },
            ip_address=(request.client.host if request.client else None),
        )

    return {
        "run_id": run_id,
        "status": "starting",
        "started_by": actor,
        "scenario_sha": req.scenario_sha,
        "target_endpoints": req.target_endpoints,
    }


@router.post("/{run_id}/started")
async def mark_run_running(
    run_id: str,
    request: Request,
    admin: Dict[str, Any] = Depends(require_admin),
) -> Dict[str, Any]:
    """k6 wrapper calls this after the warmup phase completes to
    transition starting → running. The substrate invariant
    `load_test_run_stuck_active` (Commit 5) scans status IN
    ('starting','running') — without this transition the runtime
    state machine is incomplete (Gate B P1 #105 closure).

    Idempotent: already-running runs return 200 with noop=True.
    Refuses to transition out of terminal states.
    """
    actor = _admin_email(admin)
    pool = await get_pool()
    async with admin_transaction(pool) as conn:
        row = await conn.fetchrow(
            "SELECT status FROM load_test_runs WHERE run_id = $1::uuid",
            run_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
        if row["status"] == "running":
            return {"run_id": run_id, "status": "running", "noop": True}
        if row["status"] != "starting":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"cannot transition to running from status "
                    f"{row['status']!r} — only 'starting' is valid"
                ),
            )

        await conn.execute(
            "UPDATE load_test_runs SET status = 'running' "
            "WHERE run_id = $1::uuid AND status = 'starting'",
            run_id,
        )

        await _audit(
            conn,
            actor_email=actor,
            action="load_test_run_running",
            target=f"load_test_runs/{run_id}",
            details={"run_id": run_id},
            ip_address=(request.client.host if request.client else None),
        )

    return {"run_id": run_id, "status": "running"}


@router.post("/{run_id}/abort")
async def abort_run(
    run_id: str,
    req: AbortRunRequest,
    request: Request,
    admin: Dict[str, Any] = Depends(require_admin),
) -> Dict[str, Any]:
    """Mark a run for abort. k6 polls status every iteration; exits
    within 30s of seeing 'aborting'. Idempotent — already-aborting
    runs return 200 with no-op. Actor email is ALWAYS the bearer's
    authenticated email — Gate B P2 #108 closed the body-supplied
    actor_email spoofing surface."""
    actor = _admin_email(admin)

    pool = await get_pool()
    async with admin_transaction(pool) as conn:
        row = await conn.fetchrow(
            "SELECT status FROM load_test_runs WHERE run_id = $1::uuid",
            run_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
        if row["status"] in ("aborted", "completed", "failed"):
            return {
                "run_id": run_id,
                "status": row["status"],
                "noop": True,
                "message": f"run already in terminal state {row['status']!r}",
            }

        await conn.execute(
            """
            UPDATE load_test_runs
               SET status = 'aborting',
                   abort_requested_at = COALESCE(abort_requested_at, now()),
                   abort_requested_by = COALESCE(abort_requested_by, $2),
                   abort_reason = COALESCE(abort_reason, $3)
             WHERE run_id = $1::uuid
            """,
            run_id, actor, req.reason,
        )

        await _audit(
            conn,
            actor_email=actor,
            action="load_test_run_abort_requested",
            target=f"load_test_runs/{run_id}",
            details={"run_id": run_id, "reason": req.reason},
            ip_address=(request.client.host if request.client else None),
        )

    return {"run_id": run_id, "status": "aborting", "abort_reason": req.reason}


@router.post("/{run_id}/complete")
async def complete_run(
    run_id: str,
    req: CompleteRunRequest,
    request: Request,
    admin: Dict[str, Any] = Depends(require_admin),
) -> Dict[str, Any]:
    """k6 wrapper calls this on exit. final_status='completed' for
    clean exit, 'failed' for non-zero. If the run was already in
    'aborting', final_status is forced to 'aborted'."""
    actor = _admin_email(admin)
    if req.final_status not in ("completed", "failed"):
        raise HTTPException(
            status_code=400,
            detail="final_status must be 'completed' or 'failed'",
        )

    pool = await get_pool()
    async with admin_transaction(pool) as conn:
        row = await conn.fetchrow(
            "SELECT status, metadata FROM load_test_runs WHERE run_id = $1::uuid",
            run_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")

        # If aborting, the terminal status MUST be 'aborted' regardless
        # of what k6 reports — the abort decision wins over k6's view.
        if row["status"] == "aborting":
            final = "aborted"
        else:
            final = req.final_status

        # Merge metrics_summary into metadata.
        new_metadata = dict(json.loads(row["metadata"] or "{}")) if isinstance(row["metadata"], str) else dict(row["metadata"] or {})
        if req.metrics_summary:
            new_metadata["metrics_summary"] = req.metrics_summary

        await conn.execute(
            """
            UPDATE load_test_runs
               SET status = $2,
                   completed_at = now(),
                   metadata = $3::jsonb
             WHERE run_id = $1::uuid
            """,
            run_id, final, json.dumps(new_metadata),
        )

        # Commit 3 / Commit 5a Gate B P1-A closure: synthetic bearer
        # revocation, GATED to sites.synthetic=TRUE only. Without this
        # gate, an admin typo on revoke_bearer_appliance_id could
        # silently revoke a REAL customer appliance's bearer, taking
        # the customer's daemon offline. The JOIN to sites.synthetic
        # (mig 315) blocks the revoke at the SQL level — no row
        # update + audit row carries `bearer_revoked_appliance_id`
        # null with `revoke_rejected_reason` so operators can see the
        # refusal in the run history. Closes audit/coach-62-c3-gate-b-
        # 2026-05-16.md §P1-A.
        bearer_revoked_appliance: Optional[str] = None
        revoke_rejected_reason: Optional[str] = None
        if req.revoke_bearer_appliance_id:
            try:
                result = await conn.execute(
                    """
                    UPDATE site_appliances sa
                       SET bearer_revoked = TRUE
                      FROM sites s
                     WHERE sa.appliance_id = $1
                       AND sa.bearer_revoked = FALSE
                       AND sa.site_id = s.site_id
                       AND s.synthetic = TRUE
                    """,
                    req.revoke_bearer_appliance_id,
                )
                # asyncpg execute returns 'UPDATE N' — extract N.
                if isinstance(result, str) and result.startswith("UPDATE "):
                    n = int(result.split(" ")[1])
                    if n > 0:
                        bearer_revoked_appliance = req.revoke_bearer_appliance_id
                    else:
                        # Zero rows updated — either appliance not
                        # found, already revoked, or (the security-
                        # relevant case) site is NOT synthetic.
                        revoke_rejected_reason = (
                            "no synthetic appliance matched the supplied "
                            "appliance_id (either non-existent, already "
                            "revoked, OR site.synthetic=FALSE — load-test "
                            "revocation refuses real customer appliances)"
                        )
            except Exception:
                logger.exception(
                    "bearer_revoke failed for appliance_id %s on run %s",
                    req.revoke_bearer_appliance_id, run_id,
                )
                revoke_rejected_reason = "exception during revoke; see logs"

        await _audit(
            conn,
            actor_email=actor,
            action=f"load_test_run_{final}",
            target=f"load_test_runs/{run_id}",
            details={
                "run_id": run_id,
                "final_status": final,
                "had_metrics_summary": bool(req.metrics_summary),
                "bearer_revoked_appliance_id": bearer_revoked_appliance,
                "revoke_rejected_reason": revoke_rejected_reason,
            },
            ip_address=(request.client.host if request.client else None),
        )

    return {
        "run_id": run_id,
        "status": final,
        "bearer_revoked_appliance_id": bearer_revoked_appliance,
        "revoke_rejected_reason": revoke_rejected_reason,
    }


@router.get("/status")
async def current_status(
    admin: Dict[str, Any] = Depends(require_admin),
) -> Dict[str, Any]:
    """Return the currently-active run (status IN starting/running/
    aborting) or {active: false} if no run is in flight. This is the
    endpoint k6 polls every iteration to detect abort requests."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        row = await conn.fetchrow(
            """
            SELECT run_id, started_at, started_by, scenario_sha,
                   target_endpoints, status, abort_requested_at,
                   abort_requested_by, abort_reason
              FROM load_test_runs
             WHERE status IN ('starting','running','aborting')
             ORDER BY started_at DESC
             LIMIT 1
            """,
        )
    if row is None:
        return {"active": False}
    return {
        "active": True,
        "run_id": str(row["run_id"]),
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "started_by": row["started_by"],
        "scenario_sha": row["scenario_sha"],
        "target_endpoints": list(row["target_endpoints"] or []),
        "status": row["status"],
        "abort_requested_at": row["abort_requested_at"].isoformat() if row["abort_requested_at"] else None,
        "abort_requested_by": row["abort_requested_by"],
        "abort_reason": row["abort_reason"],
    }


@router.get("/runs")
async def list_runs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    admin: Dict[str, Any] = Depends(require_admin),
) -> Dict[str, Any]:
    """Paginated history of runs, newest first. For the admin
    dashboard's load-test panel."""
    pool = await get_pool()
    async with admin_transaction(pool) as conn:
        rows = await conn.fetch(
            """
            SELECT run_id, started_at, started_by, scenario_sha,
                   target_endpoints, status, abort_requested_at,
                   abort_reason, completed_at
              FROM load_test_runs
             ORDER BY started_at DESC
             LIMIT $1::int OFFSET $2::int
            """,
            limit, offset,
        )
        total = await conn.fetchval("SELECT COUNT(*) FROM load_test_runs")
    return {
        "runs": [
            {
                "run_id": str(r["run_id"]),
                "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                "started_by": r["started_by"],
                "scenario_sha": r["scenario_sha"],
                "target_endpoints": list(r["target_endpoints"] or []),
                "status": r["status"],
                "abort_requested_at": r["abort_requested_at"].isoformat() if r["abort_requested_at"] else None,
                "abort_reason": r["abort_reason"],
                "completed_at": r["completed_at"].isoformat() if r["completed_at"] else None,
            }
            for r in rows
        ],
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
    }

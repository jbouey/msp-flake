"""
Compliance Companion Portal API.

Provides endpoints for the external compliance companion to guide
healthcare practice clients through the 10 HIPAA administrative modules.

Companion users have cross-org visibility — they can access any client org's
HIPAA modules. Auth: require_companion (companion + admin roles).
"""

import logging
import uuid as _uuid
from datetime import datetime, timezone, date
from typing import Optional, List
from decimal import Decimal

from fastapi import APIRouter, Request, HTTPException, Depends, Query
from pydantic import BaseModel

from .auth import require_companion
from .fleet import get_pool
from .hipaa_modules import (
    SRACreate, SRAResponseBatch, PolicyCreate, PolicyUpdate,
    TrainingRecord, BAARecord, IRPlanCreate, BreachRecord,
    ContingencyCreate, WorkforceRecord, PhysicalSafeguardBatch,
    OfficerUpsert, GapResponseBatch,
    _row_dict, _rows_list, _uid,
)
from .hipaa_templates import (
    SRA_QUESTIONS, POLICY_TEMPLATES, IR_PLAN_TEMPLATE,
    PHYSICAL_SAFEGUARD_ITEMS, GAP_ANALYSIS_QUESTIONS,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/companion", tags=["companion"])


# =============================================================================
# HELPERS
# =============================================================================

async def _log_activity(pool, user_id, org_id, action, module_key=None, details=None, ip=None):
    """Log companion activity."""
    if org_id is None:
        return  # org_id is NOT NULL in DB; skip logging for org-less actions
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO companion_activity_log
                (companion_user_id, org_id, action, module_key, details, ip_address)
            VALUES ($1, $2, $3, $4, $5, $6)
        """, _uid(user_id), _uid(org_id), action, module_key, details, ip)


async def _verify_org(pool, org_id: str):
    """Verify org exists. Returns asyncpg Record with id (UUID), name, status."""
    uid = _uid(org_id)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, status FROM client_orgs WHERE id = $1", uid
        )
    if not row:
        raise HTTPException(status_code=404, detail="Client organization not found")
    return row


async def _compute_overview(conn, org_id):
    """Compute HIPAA compliance overview for a single org. Shared logic."""
    sra_row = await conn.fetchrow("""
        SELECT status, overall_risk_score, expires_at, findings_count
        FROM hipaa_sra_assessments
        WHERE org_id = $1 ORDER BY started_at DESC LIMIT 1
    """, _uid(org_id))

    policy_counts = await conn.fetchrow("""
        SELECT COUNT(*) as total,
               COUNT(*) FILTER (WHERE status = 'active') as active,
               COUNT(*) FILTER (WHERE review_due < CURRENT_DATE AND status = 'active') as review_due
        FROM hipaa_policies WHERE org_id = $1
    """, _uid(org_id))

    training_counts = await conn.fetchrow("""
        SELECT COUNT(*) as total,
               COUNT(*) FILTER (WHERE status = 'completed') as compliant,
               COUNT(*) FILTER (WHERE status = 'overdue' OR (status = 'pending' AND due_date < CURRENT_DATE)) as overdue
        FROM hipaa_training_records WHERE org_id = $1
    """, _uid(org_id))

    baa_counts = await conn.fetchrow("""
        SELECT COUNT(*) as total,
               COUNT(*) FILTER (WHERE status = 'active') as active,
               COUNT(*) FILTER (WHERE status = 'active' AND expiry_date < CURRENT_DATE + INTERVAL '90 days') as expiring_soon
        FROM hipaa_baas WHERE org_id = $1
    """, _uid(org_id))

    ir_row = await conn.fetchrow("""
        SELECT status, last_tested FROM hipaa_ir_plans
        WHERE org_id = $1 ORDER BY created_at DESC LIMIT 1
    """, _uid(org_id))
    breach_count = await conn.fetchval(
        "SELECT COUNT(*) FROM hipaa_breach_log WHERE org_id = $1", org_id
    )

    contingency_row = await conn.fetchrow("""
        SELECT COUNT(*) as plans, BOOL_AND(last_tested IS NOT NULL) as all_tested
        FROM hipaa_contingency_plans WHERE org_id = $1
    """, _uid(org_id))

    workforce_row = await conn.fetchrow("""
        SELECT COUNT(*) FILTER (WHERE status = 'active') as active,
               COUNT(*) FILTER (WHERE status = 'terminated' AND access_revoked_date IS NULL) as pending_termination
        FROM hipaa_workforce_access WHERE org_id = $1
    """, _uid(org_id))

    physical_row = await conn.fetchrow("""
        SELECT COUNT(*) FILTER (WHERE status != 'not_assessed') as assessed,
               COUNT(*) FILTER (WHERE status = 'compliant') as compliant,
               COUNT(*) FILTER (WHERE status IN ('non_compliant', 'partial')) as gaps
        FROM hipaa_physical_safeguards WHERE org_id = $1
    """, _uid(org_id))

    officers = await conn.fetch(
        "SELECT role_type, name FROM hipaa_officers WHERE org_id = $1", org_id
    )
    officer_map = {r["role_type"]: r["name"] for r in officers}

    gap_row = await conn.fetchrow("""
        SELECT COUNT(*) FILTER (WHERE response IS NOT NULL) as answered,
               COUNT(*) as total,
               COALESCE(AVG(maturity_level) FILTER (WHERE maturity_level > 0), 0) as maturity_avg
        FROM hipaa_gap_responses WHERE org_id = $1
    """, _uid(org_id))

    # Calculate overall readiness
    scores = []
    if sra_row and sra_row["status"] == "completed":
        scores.append(max(0, 100 - float(sra_row["overall_risk_score"] or 50)))
    if policy_counts and policy_counts["total"] > 0:
        scores.append(float(policy_counts["active"]) / max(float(policy_counts["total"]), 1) * 100)
    if training_counts and training_counts["total"] > 0:
        scores.append(float(training_counts["compliant"]) / max(float(training_counts["total"]), 1) * 100)
    if baa_counts and baa_counts["total"] > 0:
        scores.append(float(baa_counts["active"]) / max(float(baa_counts["total"]), 1) * 100)
    if officer_map.get("privacy_officer") and officer_map.get("security_officer"):
        scores.append(100)
    elif officer_map:
        scores.append(50)
    if gap_row and gap_row["total"] > 0:
        scores.append(float(gap_row["answered"]) / max(float(gap_row["total"]), 1) * 100)
    overall = round(sum(scores) / max(len(scores), 1), 1) if scores else 0

    return {
        "sra": {
            "status": sra_row["status"] if sra_row else "not_started",
            "risk_score": float(sra_row["overall_risk_score"]) if sra_row and sra_row["overall_risk_score"] else None,
            "expires_at": sra_row["expires_at"].isoformat() if sra_row and sra_row["expires_at"] else None,
            "findings": sra_row["findings_count"] if sra_row else 0,
        },
        "policies": {
            "total": policy_counts["total"] if policy_counts else 0,
            "active": policy_counts["active"] if policy_counts else 0,
            "review_due": policy_counts["review_due"] if policy_counts else 0,
        },
        "training": {
            "total_employees": training_counts["total"] if training_counts else 0,
            "compliant": training_counts["compliant"] if training_counts else 0,
            "overdue": training_counts["overdue"] if training_counts else 0,
        },
        "baas": {
            "total": baa_counts["total"] if baa_counts else 0,
            "active": baa_counts["active"] if baa_counts else 0,
            "expiring_soon": baa_counts["expiring_soon"] if baa_counts else 0,
        },
        "ir_plan": {
            "status": ir_row["status"] if ir_row else "not_started",
            "last_tested": ir_row["last_tested"].isoformat() if ir_row and ir_row["last_tested"] else None,
            "breaches": breach_count or 0,
        },
        "contingency": {
            "plans": contingency_row["plans"] if contingency_row else 0,
            "all_tested": bool(contingency_row["all_tested"]) if contingency_row and contingency_row["plans"] > 0 else False,
        },
        "workforce": {
            "active": workforce_row["active"] if workforce_row else 0,
            "pending_termination": workforce_row["pending_termination"] if workforce_row else 0,
        },
        "physical": {
            "assessed": physical_row["assessed"] if physical_row else 0,
            "compliant": physical_row["compliant"] if physical_row else 0,
            "gaps": physical_row["gaps"] if physical_row else 0,
        },
        "officers": {
            "privacy_officer": officer_map.get("privacy_officer"),
            "security_officer": officer_map.get("security_officer"),
        },
        "gap_analysis": {
            "completion": round(float(gap_row["answered"]) / max(float(gap_row["total"]), 1) * 100, 1) if gap_row and gap_row["total"] > 0 else 0,
            "maturity_avg": round(float(gap_row["maturity_avg"]), 1) if gap_row else 0,
        },
        "overall_readiness": overall,
    }


# =============================================================================
# CLIENT LISTING
# =============================================================================

@router.get("/clients")
async def list_clients(
    request: Request,
    user: dict = Depends(require_companion),
):
    """List all client orgs with HIPAA compliance overview."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        orgs = await conn.fetch("""
            SELECT id, name, primary_email, practice_type, provider_count,
                   status, onboarded_at, created_at
            FROM client_orgs
            WHERE status = 'active'
            ORDER BY name
        """)

        clients = []
        for org in orgs:
            overview = await _compute_overview(conn, org["id"])
            clients.append({
                "id": str(org["id"]),
                "name": org["name"],
                "primary_email": org["primary_email"],
                "practice_type": org["practice_type"],
                "provider_count": org["provider_count"],
                "onboarded_at": org["onboarded_at"].isoformat() if org["onboarded_at"] else None,
                "overview": overview,
            })

    await _log_activity(pool, user["id"], None, "viewed_client_list", ip=request.client.host if request.client else None)
    return {"clients": clients}


@router.get("/clients/{org_id}/overview")
async def get_client_overview(
    org_id: str,
    request: Request,
    user: dict = Depends(require_companion),
):
    """Get HIPAA compliance overview for a specific client org."""
    pool = await get_pool()
    org = await _verify_org(pool, _uid(org_id))

    async with pool.acquire() as conn:
        overview = await _compute_overview(conn, _uid(org_id))

    await _log_activity(pool, user["id"], _uid(org_id),"viewed_overview", ip=request.client.host if request.client else None)
    return {"org_name": org["name"], **overview}


# =============================================================================
# STATS — aggregate across all clients
# =============================================================================

@router.get("/stats")
async def get_companion_stats(user: dict = Depends(require_companion)):
    """Get aggregate stats across all clients."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        total_clients = await conn.fetchval(
            "SELECT COUNT(*) FROM client_orgs WHERE status = 'active'"
        )

        # Per-module completion counts
        sra_completed = await conn.fetchval("""
            SELECT COUNT(DISTINCT org_id) FROM hipaa_sra_assessments WHERE status = 'completed'
        """)
        policies_active = await conn.fetchval("""
            SELECT COUNT(DISTINCT org_id) FROM hipaa_policies WHERE status = 'active'
        """)
        training_done = await conn.fetchval("""
            SELECT COUNT(DISTINCT org_id) FROM hipaa_training_records WHERE status = 'completed'
        """)
        baas_active = await conn.fetchval("""
            SELECT COUNT(DISTINCT org_id) FROM hipaa_baas WHERE status = 'active'
        """)
        ir_plans = await conn.fetchval("""
            SELECT COUNT(DISTINCT org_id) FROM hipaa_ir_plans
        """)
        contingency_plans = await conn.fetchval("""
            SELECT COUNT(DISTINCT org_id) FROM hipaa_contingency_plans
        """)
        workforce_tracked = await conn.fetchval("""
            SELECT COUNT(DISTINCT org_id) FROM hipaa_workforce_access
        """)
        physical_assessed = await conn.fetchval("""
            SELECT COUNT(DISTINCT org_id) FROM hipaa_physical_safeguards WHERE status != 'not_assessed'
        """)
        officers_designated = await conn.fetchval("""
            SELECT COUNT(DISTINCT org_id) FROM hipaa_officers
        """)
        gap_started = await conn.fetchval("""
            SELECT COUNT(DISTINCT org_id) FROM hipaa_gap_responses WHERE response IS NOT NULL
        """)

        # Recent companion activity
        recent_activity = await conn.fetchval("""
            SELECT COUNT(*) FROM companion_activity_log
            WHERE created_at > NOW() - INTERVAL '7 days'
        """)

        # Notes count
        total_notes = await conn.fetchval("SELECT COUNT(*) FROM companion_notes")

    return {
        "total_clients": total_clients or 0,
        "modules": {
            "sra": {"clients_completed": sra_completed or 0},
            "policies": {"clients_with_active": policies_active or 0},
            "training": {"clients_with_records": training_done or 0},
            "baas": {"clients_with_active": baas_active or 0},
            "ir_plan": {"clients_with_plan": ir_plans or 0},
            "contingency": {"clients_with_plans": contingency_plans or 0},
            "workforce": {"clients_tracking": workforce_tracked or 0},
            "physical": {"clients_assessed": physical_assessed or 0},
            "officers": {"clients_designated": officers_designated or 0},
            "gap_analysis": {"clients_started": gap_started or 0},
        },
        "companion_activity_7d": recent_activity or 0,
        "total_notes": total_notes or 0,
    }


# =============================================================================
# 1. SRA PROXY
# =============================================================================

@router.get("/clients/{org_id}/sra")
async def list_sra(org_id: str, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM hipaa_sra_assessments
            WHERE org_id = $1 ORDER BY started_at DESC
        """, _uid(org_id))
    await _log_activity(pool, user["id"], _uid(org_id),"viewed_sra", "sra", ip=request.client.host if request.client else None)
    return {"assessments": _rows_list(rows)}


@router.post("/clients/{org_id}/sra")
async def create_sra(org_id: str, body: SRACreate, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO hipaa_sra_assessments (org_id, title, total_questions, created_by)
            VALUES ($1, $2, $3, $4) RETURNING *
        """, _uid(org_id),body.title, len(SRA_QUESTIONS), user.get("displayName") or user.get("username"))
    await _log_activity(pool, user["id"], _uid(org_id),"created_sra", "sra", ip=request.client.host if request.client else None)
    return _row_dict(row)


@router.get("/clients/{org_id}/sra/{assessment_id}")
async def get_sra(org_id: str, assessment_id: str, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        assessment = await conn.fetchrow("""
            SELECT * FROM hipaa_sra_assessments WHERE id = $1 AND org_id = $2
        """, _uid(assessment_id), _uid(org_id))
        if not assessment:
            raise HTTPException(status_code=404, detail="Assessment not found")
        responses = await conn.fetch("""
            SELECT * FROM hipaa_sra_responses WHERE assessment_id = $1 ORDER BY question_key
        """, _uid(assessment_id))
    return {"assessment": _row_dict(assessment), "responses": _rows_list(responses), "questions": SRA_QUESTIONS}


@router.put("/clients/{org_id}/sra/{assessment_id}/responses")
async def save_sra_responses(org_id: str, assessment_id: str, body: SRAResponseBatch, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        owner = await conn.fetchval("SELECT org_id FROM hipaa_sra_assessments WHERE id = $1", _uid(assessment_id))
        if str(owner) != str(org_id):
            raise HTTPException(status_code=403, detail="Access denied")
        for resp in body.responses:
            q = next((q for q in SRA_QUESTIONS if q["key"] == resp.get("question_key")), None)
            if not q:
                continue
            await conn.execute("""
                INSERT INTO hipaa_sra_responses
                    (assessment_id, question_key, category, hipaa_reference, response, risk_level,
                     remediation_plan, remediation_due, notes, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
                ON CONFLICT (assessment_id, question_key) DO UPDATE SET
                    response = EXCLUDED.response, risk_level = EXCLUDED.risk_level,
                    remediation_plan = EXCLUDED.remediation_plan, remediation_due = EXCLUDED.remediation_due,
                    notes = EXCLUDED.notes, updated_at = NOW()
            """, _uid(assessment_id), resp["question_key"], q["category"], q["hipaa_reference"],
                resp.get("response"), resp.get("risk_level", "not_assessed"),
                resp.get("remediation_plan"), resp.get("remediation_due"), resp.get("notes"))
        await conn.execute("""
            UPDATE hipaa_sra_assessments
            SET answered_questions = (SELECT COUNT(*) FROM hipaa_sra_responses WHERE assessment_id = $1 AND response IS NOT NULL),
                findings_count = (SELECT COUNT(*) FROM hipaa_sra_responses WHERE assessment_id = $1 AND risk_level IN ('high', 'critical'))
            WHERE id = $1
        """, _uid(assessment_id))
    await _log_activity(pool, user["id"], _uid(org_id),"edited_sra_responses", "sra", ip=request.client.host if request.client else None)
    return {"status": "saved"}


@router.post("/clients/{org_id}/sra/{assessment_id}/complete")
async def complete_sra(org_id: str, assessment_id: str, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        owner = await conn.fetchval("SELECT org_id FROM hipaa_sra_assessments WHERE id = $1", _uid(assessment_id))
        if str(owner) != str(org_id):
            raise HTTPException(status_code=403, detail="Access denied")
        responses = await conn.fetch("SELECT risk_level FROM hipaa_sra_responses WHERE assessment_id = $1", _uid(assessment_id))
        total = len(SRA_QUESTIONS)
        critical = sum(1 for r in responses if r["risk_level"] == "critical")
        high = sum(1 for r in responses if r["risk_level"] == "high")
        medium = sum(1 for r in responses if r["risk_level"] == "medium")
        low = sum(1 for r in responses if r["risk_level"] == "low")
        risk_score = round((critical * 10 + high * 5 + medium * 2 + low * 0.5) / max(total, 1) * 100, 2)
        now = datetime.now(timezone.utc)
        row = await conn.fetchrow("""
            UPDATE hipaa_sra_assessments
            SET status = 'completed', completed_at = $2, expires_at = $2 + INTERVAL '1 year',
                overall_risk_score = $3, findings_count = $4
            WHERE id = $1 RETURNING *
        """, _uid(assessment_id), now, risk_score, critical + high)
    await _log_activity(pool, user["id"], _uid(org_id),"completed_sra", "sra", ip=request.client.host if request.client else None)
    return _row_dict(row)


# =============================================================================
# 2. POLICIES PROXY
# =============================================================================

@router.get("/clients/{org_id}/policies")
async def list_policies(org_id: str, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM hipaa_policies WHERE org_id = $1 ORDER BY policy_key, version DESC
        """, _uid(org_id))
    await _log_activity(pool, user["id"], _uid(org_id),"viewed_policies", "policies", ip=request.client.host if request.client else None)
    return {"policies": _rows_list(rows), "available_templates": list(POLICY_TEMPLATES.keys())}


@router.post("/clients/{org_id}/policies")
async def create_policy(org_id: str, body: PolicyCreate, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    template = POLICY_TEMPLATES.get(body.policy_key)
    title = body.title or (template["title"] if template else body.policy_key)
    content = body.content or ""
    if template and not body.content:
        async with pool.acquire() as conn:
            org = await conn.fetchrow("SELECT name FROM client_orgs WHERE id = $1", _uid(org_id))
            officers = await conn.fetch("SELECT role_type, name FROM hipaa_officers WHERE org_id = $1", _uid(org_id))
        officer_map = {r["role_type"]: r["name"] for r in officers}
        content = template["content"]
        content = content.replace("{{ORG_NAME}}", org["name"] if org else "")
        content = content.replace("{{EFFECTIVE_DATE}}", date.today().isoformat())
        content = content.replace("{{SECURITY_OFFICER}}", officer_map.get("security_officer", "[Not Designated]"))
        content = content.replace("{{PRIVACY_OFFICER}}", officer_map.get("privacy_officer", "[Not Designated]"))
    hipaa_refs = template["hipaa_references"] if template else []
    async with pool.acquire() as conn:
        max_ver = await conn.fetchval("""
            SELECT COALESCE(MAX(version), 0) FROM hipaa_policies WHERE org_id = $1 AND policy_key = $2
        """, _uid(org_id),body.policy_key)
        row = await conn.fetchrow("""
            INSERT INTO hipaa_policies (org_id, policy_key, title, content, version, hipaa_references)
            VALUES ($1, $2, $3, $4, $5, $6) RETURNING *
        """, _uid(org_id),body.policy_key, title, content, max_ver + 1, hipaa_refs)
    await _log_activity(pool, user["id"], _uid(org_id),"created_policy", "policies", ip=request.client.host if request.client else None)
    return _row_dict(row)


@router.put("/clients/{org_id}/policies/{policy_id}")
async def update_policy(org_id: str, policy_id: str, body: PolicyUpdate, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE hipaa_policies SET content = $3, title = COALESCE($4, title), updated_at = NOW()
            WHERE id = $1 AND org_id = $2 RETURNING *
        """, _uid(policy_id), _uid(org_id),body.content, body.title)
        if not row:
            raise HTTPException(status_code=404, detail="Policy not found")
    await _log_activity(pool, user["id"], _uid(org_id),"edited_policy", "policies", ip=request.client.host if request.client else None)
    return _row_dict(row)


@router.post("/clients/{org_id}/policies/{policy_id}/approve")
async def approve_policy(org_id: str, policy_id: str, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE hipaa_policies
            SET status = 'active', approved_by = $3, approved_at = $4,
                effective_date = CURRENT_DATE, review_due = CURRENT_DATE + INTERVAL '1 year', updated_at = NOW()
            WHERE id = $1 AND org_id = $2 RETURNING *
        """, _uid(policy_id), _uid(org_id),user.get("displayName") or user.get("username"), now)
        if not row:
            raise HTTPException(status_code=404, detail="Policy not found")
    await _log_activity(pool, user["id"], _uid(org_id),"approved_policy", "policies", ip=request.client.host if request.client else None)
    return _row_dict(row)


# =============================================================================
# 3. TRAINING PROXY
# =============================================================================

@router.get("/clients/{org_id}/training")
async def list_training(org_id: str, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM hipaa_training_records WHERE org_id = $1 ORDER BY due_date DESC", _uid(org_id))
    await _log_activity(pool, user["id"], _uid(org_id),"viewed_training", "training", ip=request.client.host if request.client else None)
    return {"records": _rows_list(rows)}


@router.post("/clients/{org_id}/training")
async def create_training(org_id: str, body: TrainingRecord, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO hipaa_training_records
                (org_id, employee_name, employee_email, employee_role, training_type,
                 training_topic, completed_date, due_date, status, certificate_ref, trainer, notes)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12) RETURNING *
        """, _uid(org_id),body.employee_name, body.employee_email, body.employee_role,
            body.training_type, body.training_topic,
            date.fromisoformat(body.completed_date) if body.completed_date else None,
            date.fromisoformat(body.due_date), body.status, body.certificate_ref, body.trainer, body.notes)
    await _log_activity(pool, user["id"], _uid(org_id),"created_training", "training", ip=request.client.host if request.client else None)
    return _row_dict(row)


@router.put("/clients/{org_id}/training/{record_id}")
async def update_training(org_id: str, record_id: str, body: TrainingRecord, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE hipaa_training_records
            SET employee_name=$3, employee_email=$4, employee_role=$5, training_type=$6, training_topic=$7,
                completed_date=$8, due_date=$9, status=$10, certificate_ref=$11, trainer=$12, notes=$13
            WHERE id = $1 AND org_id = $2 RETURNING *
        """, _uid(record_id), _uid(org_id),body.employee_name, body.employee_email, body.employee_role,
            body.training_type, body.training_topic,
            date.fromisoformat(body.completed_date) if body.completed_date else None,
            date.fromisoformat(body.due_date), body.status, body.certificate_ref, body.trainer, body.notes)
        if not row:
            raise HTTPException(status_code=404, detail="Training record not found")
    await _log_activity(pool, user["id"], _uid(org_id),"edited_training", "training", ip=request.client.host if request.client else None)
    return _row_dict(row)


@router.delete("/clients/{org_id}/training/{record_id}")
async def delete_training(org_id: str, record_id: str, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM hipaa_training_records WHERE id = $1 AND org_id = $2", _uid(record_id), _uid(org_id))
    await _log_activity(pool, user["id"], _uid(org_id),"deleted_training", "training", ip=request.client.host if request.client else None)
    return {"deleted": "DELETE 1" in result}


# =============================================================================
# 4. BAA PROXY
# =============================================================================

@router.get("/clients/{org_id}/baas")
async def list_baas(org_id: str, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM hipaa_baas WHERE org_id = $1 ORDER BY associate_name", _uid(org_id))
    await _log_activity(pool, user["id"], _uid(org_id),"viewed_baas", "baas", ip=request.client.host if request.client else None)
    return {"baas": _rows_list(rows)}


@router.post("/clients/{org_id}/baas")
async def create_baa(org_id: str, body: BAARecord, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO hipaa_baas
                (org_id, associate_name, associate_type, contact_name, contact_email,
                 signed_date, expiry_date, auto_renew, status, phi_types, services_description, notes)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12) RETURNING *
        """, _uid(org_id),body.associate_name, body.associate_type, body.contact_name, body.contact_email,
            date.fromisoformat(body.signed_date) if body.signed_date else None,
            date.fromisoformat(body.expiry_date) if body.expiry_date else None,
            body.auto_renew, body.status, body.phi_types or [], body.services_description, body.notes)
    await _log_activity(pool, user["id"], _uid(org_id),"created_baa", "baas", ip=request.client.host if request.client else None)
    return _row_dict(row)


@router.put("/clients/{org_id}/baas/{baa_id}")
async def update_baa(org_id: str, baa_id: str, body: BAARecord, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE hipaa_baas
            SET associate_name=$3, associate_type=$4, contact_name=$5, contact_email=$6,
                signed_date=$7, expiry_date=$8, auto_renew=$9, status=$10,
                phi_types=$11, services_description=$12, notes=$13, updated_at=NOW()
            WHERE id = $1 AND org_id = $2 RETURNING *
        """, _uid(baa_id), _uid(org_id),body.associate_name, body.associate_type, body.contact_name, body.contact_email,
            date.fromisoformat(body.signed_date) if body.signed_date else None,
            date.fromisoformat(body.expiry_date) if body.expiry_date else None,
            body.auto_renew, body.status, body.phi_types or [], body.services_description, body.notes)
        if not row:
            raise HTTPException(status_code=404, detail="BAA not found")
    await _log_activity(pool, user["id"], _uid(org_id),"edited_baa", "baas", ip=request.client.host if request.client else None)
    return _row_dict(row)


@router.delete("/clients/{org_id}/baas/{baa_id}")
async def delete_baa(org_id: str, baa_id: str, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM hipaa_baas WHERE id = $1 AND org_id = $2", _uid(baa_id), _uid(org_id))
    await _log_activity(pool, user["id"], _uid(org_id),"deleted_baa", "baas", ip=request.client.host if request.client else None)
    return {"deleted": "DELETE 1" in result}


# =============================================================================
# 5. IR PLAN + BREACH LOG PROXY
# =============================================================================

@router.get("/clients/{org_id}/ir-plan")
async def get_ir_plan(org_id: str, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        plan = await conn.fetchrow("SELECT * FROM hipaa_ir_plans WHERE org_id = $1 ORDER BY version DESC LIMIT 1", _uid(org_id))
        breaches = await conn.fetch("SELECT * FROM hipaa_breach_log WHERE org_id = $1 ORDER BY incident_date DESC", _uid(org_id))
    await _log_activity(pool, user["id"], _uid(org_id),"viewed_ir_plan", "ir-plan", ip=request.client.host if request.client else None)
    return {"plan": _row_dict(plan), "breaches": _rows_list(breaches), "template": IR_PLAN_TEMPLATE}


@router.post("/clients/{org_id}/ir-plan")
async def create_ir_plan(org_id: str, body: IRPlanCreate, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        max_ver = await conn.fetchval("SELECT COALESCE(MAX(version), 0) FROM hipaa_ir_plans WHERE org_id = $1", _uid(org_id))
        row = await conn.fetchrow("""
            INSERT INTO hipaa_ir_plans (org_id, title, content, version) VALUES ($1, $2, $3, $4) RETURNING *
        """, _uid(org_id),body.title, body.content, max_ver + 1)
    await _log_activity(pool, user["id"], _uid(org_id),"created_ir_plan", "ir-plan", ip=request.client.host if request.client else None)
    return _row_dict(row)


@router.post("/clients/{org_id}/breaches")
async def create_breach(org_id: str, body: BreachRecord, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO hipaa_breach_log
                (org_id, incident_date, discovered_date, description, phi_involved,
                 individuals_affected, breach_type, notification_required, root_cause, corrective_actions, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) RETURNING *
        """, _uid(org_id),date.fromisoformat(body.incident_date), date.fromisoformat(body.discovered_date),
            body.description, body.phi_involved, body.individuals_affected,
            body.breach_type, body.notification_required, body.root_cause, body.corrective_actions, body.status)
    await _log_activity(pool, user["id"], _uid(org_id),"created_breach", "ir-plan", ip=request.client.host if request.client else None)
    return _row_dict(row)


@router.put("/clients/{org_id}/breaches/{breach_id}")
async def update_breach(org_id: str, breach_id: str, body: BreachRecord, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE hipaa_breach_log
            SET incident_date=$3, discovered_date=$4, description=$5, phi_involved=$6,
                individuals_affected=$7, breach_type=$8, notification_required=$9,
                root_cause=$10, corrective_actions=$11, status=$12, updated_at=NOW()
            WHERE id = $1 AND org_id = $2 RETURNING *
        """, _uid(breach_id), _uid(org_id),date.fromisoformat(body.incident_date), date.fromisoformat(body.discovered_date),
            body.description, body.phi_involved, body.individuals_affected,
            body.breach_type, body.notification_required, body.root_cause, body.corrective_actions, body.status)
        if not row:
            raise HTTPException(status_code=404, detail="Breach record not found")
    await _log_activity(pool, user["id"], _uid(org_id),"edited_breach", "ir-plan", ip=request.client.host if request.client else None)
    return _row_dict(row)


# =============================================================================
# 6. CONTINGENCY PROXY
# =============================================================================

@router.get("/clients/{org_id}/contingency")
async def list_contingency(org_id: str, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM hipaa_contingency_plans WHERE org_id = $1 ORDER BY plan_type", _uid(org_id))
    await _log_activity(pool, user["id"], _uid(org_id),"viewed_contingency", "contingency", ip=request.client.host if request.client else None)
    return {"plans": _rows_list(rows)}


@router.post("/clients/{org_id}/contingency")
async def create_contingency(org_id: str, body: ContingencyCreate, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO hipaa_contingency_plans (org_id, plan_type, title, content, rto_hours, rpo_hours)
            VALUES ($1, $2, $3, $4, $5, $6) RETURNING *
        """, _uid(org_id),body.plan_type, body.title, body.content, body.rto_hours, body.rpo_hours)
    await _log_activity(pool, user["id"], _uid(org_id),"created_contingency", "contingency", ip=request.client.host if request.client else None)
    return _row_dict(row)


@router.put("/clients/{org_id}/contingency/{plan_id}")
async def update_contingency(org_id: str, plan_id: str, body: ContingencyCreate, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE hipaa_contingency_plans
            SET plan_type=$3, title=$4, content=$5, rto_hours=$6, rpo_hours=$7, updated_at=NOW()
            WHERE id = $1 AND org_id = $2 RETURNING *
        """, _uid(plan_id), _uid(org_id),body.plan_type, body.title, body.content, body.rto_hours, body.rpo_hours)
        if not row:
            raise HTTPException(status_code=404, detail="Plan not found")
    await _log_activity(pool, user["id"], _uid(org_id),"edited_contingency", "contingency", ip=request.client.host if request.client else None)
    return _row_dict(row)


# =============================================================================
# 7. WORKFORCE PROXY
# =============================================================================

@router.get("/clients/{org_id}/workforce")
async def list_workforce(org_id: str, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM hipaa_workforce_access WHERE org_id = $1 ORDER BY employee_name", _uid(org_id))
    await _log_activity(pool, user["id"], _uid(org_id),"viewed_workforce", "workforce", ip=request.client.host if request.client else None)
    return {"workforce": _rows_list(rows)}


@router.post("/clients/{org_id}/workforce")
async def create_workforce(org_id: str, body: WorkforceRecord, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO hipaa_workforce_access
                (org_id, employee_name, employee_role, department, access_level, systems,
                 start_date, termination_date, access_revoked_date, status, supervisor, notes)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12) RETURNING *
        """, _uid(org_id),body.employee_name, body.employee_role, body.department, body.access_level,
            body.systems or [], date.fromisoformat(body.start_date),
            date.fromisoformat(body.termination_date) if body.termination_date else None,
            date.fromisoformat(body.access_revoked_date) if body.access_revoked_date else None,
            body.status, body.supervisor, body.notes)
    await _log_activity(pool, user["id"], _uid(org_id),"created_workforce", "workforce", ip=request.client.host if request.client else None)
    return _row_dict(row)


@router.put("/clients/{org_id}/workforce/{member_id}")
async def update_workforce(org_id: str, member_id: str, body: WorkforceRecord, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE hipaa_workforce_access
            SET employee_name=$3, employee_role=$4, department=$5, access_level=$6, systems=$7,
                start_date=$8, termination_date=$9, access_revoked_date=$10, status=$11, supervisor=$12, notes=$13, updated_at=NOW()
            WHERE id = $1 AND org_id = $2 RETURNING *
        """, _uid(member_id), _uid(org_id),body.employee_name, body.employee_role, body.department,
            body.access_level, body.systems or [], date.fromisoformat(body.start_date),
            date.fromisoformat(body.termination_date) if body.termination_date else None,
            date.fromisoformat(body.access_revoked_date) if body.access_revoked_date else None,
            body.status, body.supervisor, body.notes)
        if not row:
            raise HTTPException(status_code=404, detail="Workforce member not found")
    await _log_activity(pool, user["id"], _uid(org_id),"edited_workforce", "workforce", ip=request.client.host if request.client else None)
    return _row_dict(row)


# =============================================================================
# 8. PHYSICAL SAFEGUARDS PROXY
# =============================================================================

@router.get("/clients/{org_id}/physical")
async def get_physical(org_id: str, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM hipaa_physical_safeguards WHERE org_id = $1 ORDER BY category, item_key", _uid(org_id))
    await _log_activity(pool, user["id"], _uid(org_id),"viewed_physical", "physical", ip=request.client.host if request.client else None)
    return {"items": _rows_list(rows), "template_items": PHYSICAL_SAFEGUARD_ITEMS}


@router.put("/clients/{org_id}/physical")
async def save_physical(org_id: str, body: PhysicalSafeguardBatch, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        for item in body.items:
            await conn.execute("""
                INSERT INTO hipaa_physical_safeguards
                    (org_id, category, item_key, description, status, hipaa_reference, notes, last_assessed, assessed_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, CURRENT_DATE, $8)
                ON CONFLICT (org_id, category, item_key) DO UPDATE SET
                    description=EXCLUDED.description, status=EXCLUDED.status, hipaa_reference=EXCLUDED.hipaa_reference,
                    notes=EXCLUDED.notes, last_assessed=CURRENT_DATE, assessed_by=EXCLUDED.assessed_by, updated_at=NOW()
            """, _uid(org_id),item["category"], item["item_key"], item["description"],
                item.get("status", "not_assessed"), item.get("hipaa_reference"), item.get("notes"),
                item.get("assessed_by") or user.get("displayName") or user.get("username"))
    await _log_activity(pool, user["id"], _uid(org_id),"edited_physical", "physical", ip=request.client.host if request.client else None)
    return {"status": "saved"}


# =============================================================================
# 9. OFFICERS PROXY
# =============================================================================

@router.get("/clients/{org_id}/officers")
async def get_officers(org_id: str, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM hipaa_officers WHERE org_id = $1 ORDER BY role_type", _uid(org_id))
    await _log_activity(pool, user["id"], _uid(org_id),"viewed_officers", "officers", ip=request.client.host if request.client else None)
    return {"officers": _rows_list(rows)}


@router.put("/clients/{org_id}/officers")
async def upsert_officers(org_id: str, body: OfficerUpsert, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        for officer in body.officers:
            await conn.execute("""
                INSERT INTO hipaa_officers (org_id, role_type, name, title, email, phone, appointed_date, notes)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (org_id, role_type) DO UPDATE SET
                    name=EXCLUDED.name, title=EXCLUDED.title, email=EXCLUDED.email,
                    phone=EXCLUDED.phone, appointed_date=EXCLUDED.appointed_date, notes=EXCLUDED.notes, updated_at=NOW()
            """, _uid(org_id),officer["role_type"], officer["name"],
                officer.get("title"), officer.get("email"), officer.get("phone"),
                date.fromisoformat(officer["appointed_date"]), officer.get("notes"))
    await _log_activity(pool, user["id"], _uid(org_id),"edited_officers", "officers", ip=request.client.host if request.client else None)
    return {"status": "saved"}


# =============================================================================
# 10. GAP ANALYSIS PROXY
# =============================================================================

@router.get("/clients/{org_id}/gap-analysis")
async def get_gap_analysis(org_id: str, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM hipaa_gap_responses WHERE org_id = $1 ORDER BY section, question_key", _uid(org_id))
    await _log_activity(pool, user["id"], _uid(org_id),"viewed_gap_analysis", "gap-analysis", ip=request.client.host if request.client else None)
    return {"responses": _rows_list(rows), "questions": GAP_ANALYSIS_QUESTIONS}


@router.put("/clients/{org_id}/gap-analysis")
async def save_gap_analysis(org_id: str, body: GapResponseBatch, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        for resp in body.responses:
            await conn.execute("""
                INSERT INTO hipaa_gap_responses
                    (org_id, question_key, section, hipaa_reference, response, maturity_level, notes, evidence_ref, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
                ON CONFLICT (org_id, questionnaire_version, question_key) DO UPDATE SET
                    response=EXCLUDED.response, maturity_level=EXCLUDED.maturity_level,
                    notes=EXCLUDED.notes, evidence_ref=EXCLUDED.evidence_ref, updated_at=NOW()
            """, _uid(org_id),resp["question_key"], resp.get("section"), resp.get("hipaa_reference"),
                resp.get("response"), resp.get("maturity_level", 0), resp.get("notes"), resp.get("evidence_ref"))
    await _log_activity(pool, user["id"], _uid(org_id),"edited_gap_analysis", "gap-analysis", ip=request.client.host if request.client else None)
    return {"status": "saved"}


# =============================================================================
# COMPANION NOTES
# =============================================================================

class NoteCreate(BaseModel):
    note: str

class NoteUpdate(BaseModel):
    note: str


@router.get("/clients/{org_id}/notes")
async def list_notes(org_id: str, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT cn.*, au.display_name as companion_name
            FROM companion_notes cn
            JOIN admin_users au ON au.id = cn.companion_user_id
            WHERE cn.org_id = $1
            ORDER BY cn.updated_at DESC
        """, _uid(org_id))
    return {"notes": _rows_list(rows)}


@router.get("/clients/{org_id}/notes/{module_key}")
async def list_module_notes(org_id: str, module_key: str, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT cn.*, au.display_name as companion_name
            FROM companion_notes cn
            JOIN admin_users au ON au.id = cn.companion_user_id
            WHERE cn.org_id = $1 AND cn.module_key = $2
            ORDER BY cn.created_at DESC
        """, _uid(org_id),module_key)
    return {"notes": _rows_list(rows)}


@router.post("/clients/{org_id}/notes/{module_key}")
async def create_note(org_id: str, module_key: str, body: NoteCreate, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO companion_notes (companion_user_id, org_id, module_key, note)
            VALUES ($1, $2, $3, $4) RETURNING *
        """, _uid(user["id"]), _uid(org_id), module_key, body.note)
    await _log_activity(pool, user["id"], _uid(org_id),"added_note", module_key, ip=request.client.host if request.client else None)
    return _row_dict(row)


@router.put("/notes/{note_id}")
async def update_note(note_id: str, body: NoteUpdate, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE companion_notes SET note = $2, updated_at = NOW()
            WHERE id = $1 AND companion_user_id = $3 RETURNING *
        """, _uid(note_id), body.note, _uid(user["id"]))
        if not row:
            raise HTTPException(status_code=404, detail="Note not found or not yours")
    await _log_activity(pool, user["id"], row["org_id"], "edited_note", row["module_key"], ip=request.client.host if request.client else None)
    return _row_dict(row)


@router.delete("/notes/{note_id}")
async def delete_note(note_id: str, request: Request, user: dict = Depends(require_companion)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT org_id, module_key FROM companion_notes WHERE id = $1 AND companion_user_id = $2",
            _uid(note_id), _uid(user["id"])
        )
        if not row:
            raise HTTPException(status_code=404, detail="Note not found or not yours")
        await conn.execute("DELETE FROM companion_notes WHERE id = $1", _uid(note_id))
    await _log_activity(pool, user["id"], row["org_id"], "deleted_note", row["module_key"], ip=request.client.host if request.client else None)
    return {"deleted": True}


# =============================================================================
# ACTIVITY LOG
# =============================================================================

@router.get("/activity")
async def list_all_activity(
    user: dict = Depends(require_companion),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    org_id: Optional[str] = None,
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        if org_id:
            rows = await conn.fetch("""
                SELECT cal.*, au.display_name as companion_name, co.name as org_name
                FROM companion_activity_log cal
                JOIN admin_users au ON au.id = cal.companion_user_id
                JOIN client_orgs co ON co.id = cal.org_id
                WHERE cal.org_id = $1
                ORDER BY cal.created_at DESC LIMIT $2 OFFSET $3
            """, _uid(org_id),limit, offset)
        else:
            rows = await conn.fetch("""
                SELECT cal.*, au.display_name as companion_name, co.name as org_name
                FROM companion_activity_log cal
                JOIN admin_users au ON au.id = cal.companion_user_id
                LEFT JOIN client_orgs co ON co.id = cal.org_id
                ORDER BY cal.created_at DESC LIMIT $1 OFFSET $2
            """, limit, offset)
    return {"activity": _rows_list(rows)}


@router.get("/clients/{org_id}/activity")
async def list_client_activity(
    org_id: str,
    user: dict = Depends(require_companion),
    limit: int = Query(50, le=200),
):
    pool = await get_pool()
    await _verify_org(pool, _uid(org_id))
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT cal.*, au.display_name as companion_name
            FROM companion_activity_log cal
            JOIN admin_users au ON au.id = cal.companion_user_id
            WHERE cal.org_id = $1
            ORDER BY cal.created_at DESC LIMIT $2
        """, _uid(org_id),limit)
    return {"activity": _rows_list(rows)}

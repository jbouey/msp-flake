"""
HIPAA Administrative Compliance Modules.

Provides API endpoints for 10 HIPAA gap-closing modules:
1. Security Risk Assessment (SRA)
2. Policy Library
3. Training Tracker
4. BAA Inventory
5. Incident Response Plan + Breach Log
6. Contingency / DR Plans
7. Workforce Access Lifecycle
8. Physical Safeguards Checklist
9. Privacy/Security Officer Designation
10. Gap Analysis Questionnaire

All endpoints are org-scoped via client session auth.
"""

import logging
import uuid as _uuid
from datetime import datetime, timezone, date
from typing import Optional, List
from decimal import Decimal

from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel

from .fleet import get_pool
from .client_portal import require_client_user
from .hipaa_templates import (
    SRA_QUESTIONS,
    POLICY_TEMPLATES,
    IR_PLAN_TEMPLATE,
    PHYSICAL_SAFEGUARD_ITEMS,
    GAP_ANALYSIS_QUESTIONS,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/client/compliance", tags=["client-compliance"])


# =============================================================================
# REQUEST MODELS
# =============================================================================

class SRACreate(BaseModel):
    title: str = "Annual Security Risk Assessment"

class SRAResponseBatch(BaseModel):
    responses: list  # list of {question_key, response, risk_level, remediation_plan, remediation_due, notes}

class PolicyCreate(BaseModel):
    policy_key: str
    title: Optional[str] = None
    content: Optional[str] = None

class PolicyUpdate(BaseModel):
    content: str
    title: Optional[str] = None

class TrainingRecord(BaseModel):
    employee_name: str
    employee_email: Optional[str] = None
    employee_role: Optional[str] = None
    training_type: str
    training_topic: str
    completed_date: Optional[str] = None
    due_date: str
    status: str = "pending"
    certificate_ref: Optional[str] = None
    trainer: Optional[str] = None
    notes: Optional[str] = None

class BAARecord(BaseModel):
    associate_name: str
    associate_type: str
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    signed_date: Optional[str] = None
    expiry_date: Optional[str] = None
    auto_renew: bool = False
    status: str = "pending"
    phi_types: Optional[List[str]] = None
    services_description: Optional[str] = None
    notes: Optional[str] = None

class IRPlanCreate(BaseModel):
    title: str = "Incident Response Plan"
    content: str

class BreachRecord(BaseModel):
    incident_date: str
    discovered_date: str
    description: str
    phi_involved: bool = False
    individuals_affected: int = 0
    breach_type: Optional[str] = None
    notification_required: bool = False
    hhs_notified: bool = False
    hhs_notified_date: Optional[str] = None
    individuals_notified: bool = False
    individuals_notified_date: Optional[str] = None
    root_cause: Optional[str] = None
    corrective_actions: Optional[str] = None
    status: str = "investigating"

class ContingencyCreate(BaseModel):
    plan_type: str
    title: str
    content: str
    rto_hours: Optional[int] = None
    rpo_hours: Optional[int] = None

class WorkforceRecord(BaseModel):
    employee_name: str
    employee_role: Optional[str] = None
    department: Optional[str] = None
    access_level: str
    systems: Optional[List[str]] = None
    start_date: str
    termination_date: Optional[str] = None
    access_revoked_date: Optional[str] = None
    status: str = "active"
    supervisor: Optional[str] = None
    notes: Optional[str] = None

class PhysicalSafeguardBatch(BaseModel):
    items: list  # list of {category, item_key, description, status, hipaa_reference, notes, assessed_by}

class OfficerUpsert(BaseModel):
    officers: list  # list of {role_type, name, title, email, phone, appointed_date, notes}

class GapResponseBatch(BaseModel):
    responses: list  # list of {question_key, section, hipaa_reference, response, maturity_level, notes, evidence_ref}


# =============================================================================
# HELPER
# =============================================================================

def _uid(s) -> _uuid.UUID:
    """Convert string path param to UUID for asyncpg. Passes UUID objects through."""
    if isinstance(s, _uuid.UUID):
        return s
    try:
        return _uuid.UUID(str(s))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid ID format")


def _row_dict(row):
    """Convert asyncpg Record to dict with JSON-safe values."""
    if row is None:
        return None
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, _uuid.UUID):
            d[k] = str(v)
        elif isinstance(v, datetime):
            d[k] = v.isoformat()
        elif isinstance(v, date):
            d[k] = v.isoformat()
        elif isinstance(v, Decimal):
            d[k] = float(v)
    return d


def _rows_list(rows):
    return [_row_dict(r) for r in rows]


# =============================================================================
# OVERVIEW — aggregate dashboard across all modules
# =============================================================================

@router.get("/overview")
async def get_compliance_overview(user: dict = Depends(require_client_user)):
    pool = await get_pool()
    org_id = user["org_id"]

    async with pool.acquire() as conn:
        # SRA
        sra_row = await conn.fetchrow("""
            SELECT status, overall_risk_score, expires_at, findings_count
            FROM hipaa_sra_assessments
            WHERE org_id = $1 ORDER BY started_at DESC LIMIT 1
        """, org_id)

        # Policies
        policy_counts = await conn.fetchrow("""
            SELECT COUNT(*) as total,
                   COUNT(*) FILTER (WHERE status = 'active') as active,
                   COUNT(*) FILTER (WHERE review_due < CURRENT_DATE AND status = 'active') as review_due
            FROM hipaa_policies WHERE org_id = $1
        """, org_id)

        # Training
        training_counts = await conn.fetchrow("""
            SELECT COUNT(*) as total,
                   COUNT(*) FILTER (WHERE status = 'completed') as compliant,
                   COUNT(*) FILTER (WHERE status = 'overdue' OR (status = 'pending' AND due_date < CURRENT_DATE)) as overdue
            FROM hipaa_training_records WHERE org_id = $1
        """, org_id)

        # BAAs
        baa_counts = await conn.fetchrow("""
            SELECT COUNT(*) as total,
                   COUNT(*) FILTER (WHERE status = 'active') as active,
                   COUNT(*) FILTER (WHERE status = 'active' AND expiry_date < CURRENT_DATE + INTERVAL '90 days') as expiring_soon
            FROM hipaa_baas WHERE org_id = $1
        """, org_id)

        # IR Plan
        ir_row = await conn.fetchrow("""
            SELECT status, last_tested FROM hipaa_ir_plans
            WHERE org_id = $1 ORDER BY created_at DESC LIMIT 1
        """, org_id)
        breach_count = await conn.fetchval("""
            SELECT COUNT(*) FROM hipaa_breach_log WHERE org_id = $1
        """, org_id)

        # Contingency
        contingency_row = await conn.fetchrow("""
            SELECT COUNT(*) as plans,
                   BOOL_AND(last_tested IS NOT NULL) as all_tested
            FROM hipaa_contingency_plans WHERE org_id = $1
        """, org_id)

        # Workforce
        workforce_row = await conn.fetchrow("""
            SELECT COUNT(*) FILTER (WHERE status = 'active') as active,
                   COUNT(*) FILTER (WHERE status = 'terminated' AND access_revoked_date IS NULL) as pending_termination
            FROM hipaa_workforce_access WHERE org_id = $1
        """, org_id)

        # Physical
        physical_row = await conn.fetchrow("""
            SELECT COUNT(*) FILTER (WHERE status != 'not_assessed') as assessed,
                   COUNT(*) FILTER (WHERE status = 'compliant') as compliant,
                   COUNT(*) FILTER (WHERE status IN ('non_compliant', 'partial')) as gaps
            FROM hipaa_physical_safeguards WHERE org_id = $1
        """, org_id)

        # Officers
        officers = await conn.fetch("""
            SELECT role_type, name FROM hipaa_officers WHERE org_id = $1
        """, org_id)
        officer_map = {r["role_type"]: r["name"] for r in officers}

        # Gap Analysis
        gap_row = await conn.fetchrow("""
            SELECT COUNT(*) FILTER (WHERE response IS NOT NULL) as answered,
                   COUNT(*) as total,
                   COALESCE(AVG(maturity_level) FILTER (WHERE maturity_level > 0), 0) as maturity_avg
            FROM hipaa_gap_responses WHERE org_id = $1
        """, org_id)

    # Calculate overall readiness (weighted composite)
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
# 1. SRA (Security Risk Assessment)
# =============================================================================

@router.get("/sra")
async def list_sra_assessments(user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM hipaa_sra_assessments
            WHERE org_id = $1 ORDER BY started_at DESC
        """, user["org_id"])
    return {"assessments": _rows_list(rows)}


@router.post("/sra")
async def create_sra_assessment(body: SRACreate, user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO hipaa_sra_assessments (org_id, title, total_questions, created_by)
            VALUES ($1, $2, $3, $4)
            RETURNING *
        """, user["org_id"], body.title, len(SRA_QUESTIONS), user["user_id"])
    return _row_dict(row)


@router.get("/sra/{assessment_id}")
async def get_sra_assessment(assessment_id: str, user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        assessment = await conn.fetchrow("""
            SELECT * FROM hipaa_sra_assessments
            WHERE id = $1 AND org_id = $2
        """, _uid(assessment_id), user["org_id"])
        if not assessment:
            raise HTTPException(status_code=404, detail="Assessment not found")

        responses = await conn.fetch("""
            SELECT * FROM hipaa_sra_responses
            WHERE assessment_id = $1 ORDER BY question_key
        """, _uid(assessment_id))

    return {
        "assessment": _row_dict(assessment),
        "responses": _rows_list(responses),
        "questions": SRA_QUESTIONS,
    }


@router.put("/sra/{assessment_id}/responses")
async def save_sra_responses(assessment_id: str, body: SRAResponseBatch, user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Verify ownership
        owner = await conn.fetchval("""
            SELECT org_id FROM hipaa_sra_assessments WHERE id = $1
        """, _uid(assessment_id))
        if str(owner) != str(user["org_id"]):
            raise HTTPException(status_code=403, detail="Access denied")

        answered = 0
        findings = 0
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
                    response = EXCLUDED.response,
                    risk_level = EXCLUDED.risk_level,
                    remediation_plan = EXCLUDED.remediation_plan,
                    remediation_due = EXCLUDED.remediation_due,
                    notes = EXCLUDED.notes,
                    updated_at = NOW()
            """, _uid(assessment_id),resp["question_key"], q["category"], q["hipaa_reference"],
                resp.get("response"), resp.get("risk_level", "not_assessed"),
                resp.get("remediation_plan"), resp.get("remediation_due"), resp.get("notes"))
            if resp.get("response"):
                answered += 1
            if resp.get("risk_level") in ("high", "critical"):
                findings += 1

        # Update assessment counters
        await conn.execute("""
            UPDATE hipaa_sra_assessments
            SET answered_questions = (SELECT COUNT(*) FROM hipaa_sra_responses WHERE assessment_id = $1 AND response IS NOT NULL),
                findings_count = (SELECT COUNT(*) FROM hipaa_sra_responses WHERE assessment_id = $1 AND risk_level IN ('high', 'critical'))
            WHERE id = $1
        """, _uid(assessment_id))

    return {"status": "saved"}


@router.post("/sra/{assessment_id}/complete")
async def complete_sra_assessment(assessment_id: str, user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        owner = await conn.fetchval("""
            SELECT org_id FROM hipaa_sra_assessments WHERE id = $1
        """, _uid(assessment_id))
        if str(owner) != str(user["org_id"]):
            raise HTTPException(status_code=403, detail="Access denied")

        # Calculate risk score
        responses = await conn.fetch("""
            SELECT risk_level FROM hipaa_sra_responses WHERE assessment_id = $1
        """, _uid(assessment_id))

        total = len(SRA_QUESTIONS)
        critical = sum(1 for r in responses if r["risk_level"] == "critical")
        high = sum(1 for r in responses if r["risk_level"] == "high")
        medium = sum(1 for r in responses if r["risk_level"] == "medium")
        low = sum(1 for r in responses if r["risk_level"] == "low")
        risk_score = round((critical * 10 + high * 5 + medium * 2 + low * 0.5) / max(total, 1) * 100, 2)

        now = datetime.now(timezone.utc)
        row = await conn.fetchrow("""
            UPDATE hipaa_sra_assessments
            SET status = 'completed',
                completed_at = $2,
                expires_at = $2 + INTERVAL '1 year',
                overall_risk_score = $3,
                findings_count = $4
            WHERE id = $1
            RETURNING *
        """, _uid(assessment_id),now, risk_score, critical + high)

    return _row_dict(row)


# =============================================================================
# 2. POLICY LIBRARY
# =============================================================================

@router.get("/policies")
async def list_policies(user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM hipaa_policies
            WHERE org_id = $1 ORDER BY policy_key, version DESC
        """, user["org_id"])
    return {
        "policies": _rows_list(rows),
        "available_templates": list(POLICY_TEMPLATES.keys()),
    }


@router.post("/policies")
async def create_policy(body: PolicyCreate, user: dict = Depends(require_client_user)):
    pool = await get_pool()

    template = POLICY_TEMPLATES.get(body.policy_key)
    title = body.title or (template["title"] if template else body.policy_key)
    content = body.content or ""

    if template and not body.content:
        # Fill template placeholders
        async with pool.acquire() as conn:
            org = await conn.fetchrow("SELECT name FROM client_orgs WHERE id = $1", user["org_id"])
            officers = await conn.fetch("SELECT role_type, name FROM hipaa_officers WHERE org_id = $1", user["org_id"])
        officer_map = {r["role_type"]: r["name"] for r in officers}
        content = template["content"]
        content = content.replace("{{ORG_NAME}}", org["name"] if org else "")
        content = content.replace("{{EFFECTIVE_DATE}}", date.today().isoformat())
        content = content.replace("{{SECURITY_OFFICER}}", officer_map.get("security_officer", "[Not Designated]"))
        content = content.replace("{{PRIVACY_OFFICER}}", officer_map.get("privacy_officer", "[Not Designated]"))

    hipaa_refs = template["hipaa_references"] if template else []

    async with pool.acquire() as conn:
        # Get next version
        max_ver = await conn.fetchval("""
            SELECT COALESCE(MAX(version), 0) FROM hipaa_policies
            WHERE org_id = $1 AND policy_key = $2
        """, user["org_id"], body.policy_key)

        row = await conn.fetchrow("""
            INSERT INTO hipaa_policies (org_id, policy_key, title, content, version, hipaa_references)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING *
        """, user["org_id"], body.policy_key, title, content, max_ver + 1, hipaa_refs)

    return _row_dict(row)


@router.put("/policies/{policy_id}")
async def update_policy(policy_id: str, body: PolicyUpdate, user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE hipaa_policies
            SET content = $3, title = COALESCE($4, title), updated_at = NOW()
            WHERE id = $1 AND org_id = $2
            RETURNING *
        """, _uid(policy_id),user["org_id"], body.content, body.title)
        if not row:
            raise HTTPException(status_code=404, detail="Policy not found")
    return _row_dict(row)


@router.post("/policies/{policy_id}/approve")
async def approve_policy(policy_id: str, user: dict = Depends(require_client_user)):
    pool = await get_pool()
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE hipaa_policies
            SET status = 'active',
                approved_by = $3,
                approved_at = $4,
                effective_date = CURRENT_DATE,
                review_due = CURRENT_DATE + INTERVAL '1 year',
                updated_at = NOW()
            WHERE id = $1 AND org_id = $2
            RETURNING *
        """, _uid(policy_id),user["org_id"], user.get("name") or user.get("email"), now)
        if not row:
            raise HTTPException(status_code=404, detail="Policy not found")
    return _row_dict(row)


# =============================================================================
# 3. TRAINING TRACKER
# =============================================================================

@router.get("/training")
async def list_training(user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM hipaa_training_records
            WHERE org_id = $1 ORDER BY due_date DESC
        """, user["org_id"])
    return {"records": _rows_list(rows)}


@router.post("/training")
async def create_training(body: TrainingRecord, user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO hipaa_training_records
                (org_id, employee_name, employee_email, employee_role, training_type,
                 training_topic, completed_date, due_date, status, certificate_ref, trainer, notes)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            RETURNING *
        """, user["org_id"], body.employee_name, body.employee_email, body.employee_role,
            body.training_type, body.training_topic,
            date.fromisoformat(body.completed_date) if body.completed_date else None,
            date.fromisoformat(body.due_date),
            body.status, body.certificate_ref, body.trainer, body.notes)
    return _row_dict(row)


@router.put("/training/{record_id}")
async def update_training(record_id: str, body: TrainingRecord, user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE hipaa_training_records
            SET employee_name = $3, employee_email = $4, employee_role = $5,
                training_type = $6, training_topic = $7, completed_date = $8,
                due_date = $9, status = $10, certificate_ref = $11, trainer = $12, notes = $13
            WHERE id = $1 AND org_id = $2
            RETURNING *
        """, _uid(record_id),user["org_id"], body.employee_name, body.employee_email,
            body.employee_role, body.training_type, body.training_topic,
            date.fromisoformat(body.completed_date) if body.completed_date else None,
            date.fromisoformat(body.due_date),
            body.status, body.certificate_ref, body.trainer, body.notes)
        if not row:
            raise HTTPException(status_code=404, detail="Training record not found")
    return _row_dict(row)


@router.delete("/training/{record_id}")
async def delete_training(record_id: str, user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("""
            DELETE FROM hipaa_training_records WHERE id = $1 AND org_id = $2
        """, _uid(record_id),user["org_id"])
    return {"deleted": "DELETE 1" in result}


# =============================================================================
# 4. BAA TRACKER
# =============================================================================

@router.get("/baas")
async def list_baas(user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM hipaa_baas
            WHERE org_id = $1 ORDER BY associate_name
        """, user["org_id"])
    return {"baas": _rows_list(rows)}


@router.post("/baas")
async def create_baa(body: BAARecord, user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO hipaa_baas
                (org_id, associate_name, associate_type, contact_name, contact_email,
                 signed_date, expiry_date, auto_renew, status, phi_types,
                 services_description, notes)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            RETURNING *
        """, user["org_id"], body.associate_name, body.associate_type,
            body.contact_name, body.contact_email,
            date.fromisoformat(body.signed_date) if body.signed_date else None,
            date.fromisoformat(body.expiry_date) if body.expiry_date else None,
            body.auto_renew, body.status, body.phi_types or [],
            body.services_description, body.notes)
    return _row_dict(row)


@router.put("/baas/{baa_id}")
async def update_baa(baa_id: str, body: BAARecord, user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE hipaa_baas
            SET associate_name = $3, associate_type = $4, contact_name = $5,
                contact_email = $6, signed_date = $7, expiry_date = $8,
                auto_renew = $9, status = $10, phi_types = $11,
                services_description = $12, notes = $13, updated_at = NOW()
            WHERE id = $1 AND org_id = $2
            RETURNING *
        """, _uid(baa_id),user["org_id"], body.associate_name, body.associate_type,
            body.contact_name, body.contact_email,
            date.fromisoformat(body.signed_date) if body.signed_date else None,
            date.fromisoformat(body.expiry_date) if body.expiry_date else None,
            body.auto_renew, body.status, body.phi_types or [],
            body.services_description, body.notes)
        if not row:
            raise HTTPException(status_code=404, detail="BAA not found")
    return _row_dict(row)


@router.delete("/baas/{baa_id}")
async def delete_baa(baa_id: str, user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("""
            DELETE FROM hipaa_baas WHERE id = $1 AND org_id = $2
        """, _uid(baa_id),user["org_id"])
    return {"deleted": "DELETE 1" in result}


# =============================================================================
# 5. INCIDENT RESPONSE PLAN + BREACH LOG
# =============================================================================

@router.get("/ir-plan")
async def get_ir_plan(user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        plan = await conn.fetchrow("""
            SELECT * FROM hipaa_ir_plans
            WHERE org_id = $1 ORDER BY version DESC LIMIT 1
        """, user["org_id"])
        breaches = await conn.fetch("""
            SELECT * FROM hipaa_breach_log
            WHERE org_id = $1 ORDER BY incident_date DESC
        """, user["org_id"])
    return {
        "plan": _row_dict(plan),
        "breaches": _rows_list(breaches),
        "template": IR_PLAN_TEMPLATE,
    }


@router.post("/ir-plan")
async def create_ir_plan(body: IRPlanCreate, user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        max_ver = await conn.fetchval("""
            SELECT COALESCE(MAX(version), 0) FROM hipaa_ir_plans WHERE org_id = $1
        """, user["org_id"])
        row = await conn.fetchrow("""
            INSERT INTO hipaa_ir_plans (org_id, title, content, version)
            VALUES ($1, $2, $3, $4)
            RETURNING *
        """, user["org_id"], body.title, body.content, max_ver + 1)
    return _row_dict(row)


@router.post("/breaches")
async def create_breach(body: BreachRecord, user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO hipaa_breach_log
                (org_id, incident_date, discovered_date, description, phi_involved,
                 individuals_affected, breach_type, notification_required,
                 hhs_notified, hhs_notified_date, individuals_notified, individuals_notified_date,
                 root_cause, corrective_actions, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
            RETURNING *
        """, user["org_id"],
            date.fromisoformat(body.incident_date),
            date.fromisoformat(body.discovered_date),
            body.description, body.phi_involved, body.individuals_affected,
            body.breach_type, body.notification_required,
            body.hhs_notified,
            date.fromisoformat(body.hhs_notified_date) if body.hhs_notified_date else None,
            body.individuals_notified,
            date.fromisoformat(body.individuals_notified_date) if body.individuals_notified_date else None,
            body.root_cause, body.corrective_actions, body.status)
    return _row_dict(row)


@router.put("/breaches/{breach_id}")
async def update_breach(breach_id: str, body: BreachRecord, user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE hipaa_breach_log
            SET incident_date = $3, discovered_date = $4, description = $5,
                phi_involved = $6, individuals_affected = $7, breach_type = $8,
                notification_required = $9, hhs_notified = $10, hhs_notified_date = $11,
                individuals_notified = $12, individuals_notified_date = $13,
                root_cause = $14, corrective_actions = $15, status = $16, updated_at = NOW()
            WHERE id = $1 AND org_id = $2
            RETURNING *
        """, _uid(breach_id), user["org_id"],
            date.fromisoformat(body.incident_date),
            date.fromisoformat(body.discovered_date),
            body.description, body.phi_involved, body.individuals_affected,
            body.breach_type, body.notification_required,
            body.hhs_notified,
            date.fromisoformat(body.hhs_notified_date) if body.hhs_notified_date else None,
            body.individuals_notified,
            date.fromisoformat(body.individuals_notified_date) if body.individuals_notified_date else None,
            body.root_cause, body.corrective_actions, body.status)
        if not row:
            raise HTTPException(status_code=404, detail="Breach record not found")
    return _row_dict(row)


# =============================================================================
# 6. CONTINGENCY / DR PLANS
# =============================================================================

@router.get("/contingency")
async def list_contingency(user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM hipaa_contingency_plans
            WHERE org_id = $1 ORDER BY plan_type
        """, user["org_id"])
    return {"plans": _rows_list(rows)}


@router.post("/contingency")
async def create_contingency(body: ContingencyCreate, user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO hipaa_contingency_plans
                (org_id, plan_type, title, content, rto_hours, rpo_hours)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING *
        """, user["org_id"], body.plan_type, body.title, body.content,
            body.rto_hours, body.rpo_hours)
    return _row_dict(row)


@router.put("/contingency/{plan_id}")
async def update_contingency(plan_id: str, body: ContingencyCreate, user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE hipaa_contingency_plans
            SET plan_type = $3, title = $4, content = $5,
                rto_hours = $6, rpo_hours = $7, updated_at = NOW()
            WHERE id = $1 AND org_id = $2
            RETURNING *
        """, _uid(plan_id),user["org_id"], body.plan_type, body.title, body.content,
            body.rto_hours, body.rpo_hours)
        if not row:
            raise HTTPException(status_code=404, detail="Plan not found")
    return _row_dict(row)


# =============================================================================
# 7. WORKFORCE ACCESS LIFECYCLE
# =============================================================================

@router.get("/workforce")
async def list_workforce(user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM hipaa_workforce_access
            WHERE org_id = $1 ORDER BY employee_name
        """, user["org_id"])
    return {"workforce": _rows_list(rows)}


@router.post("/workforce")
async def create_workforce(body: WorkforceRecord, user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO hipaa_workforce_access
                (org_id, employee_name, employee_role, department, access_level,
                 systems, start_date, termination_date, access_revoked_date,
                 status, supervisor, notes)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            RETURNING *
        """, user["org_id"], body.employee_name, body.employee_role, body.department,
            body.access_level, body.systems or [],
            date.fromisoformat(body.start_date),
            date.fromisoformat(body.termination_date) if body.termination_date else None,
            date.fromisoformat(body.access_revoked_date) if body.access_revoked_date else None,
            body.status, body.supervisor, body.notes)
    return _row_dict(row)


@router.put("/workforce/{member_id}")
async def update_workforce(member_id: str, body: WorkforceRecord, user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE hipaa_workforce_access
            SET employee_name = $3, employee_role = $4, department = $5,
                access_level = $6, systems = $7, start_date = $8,
                termination_date = $9, access_revoked_date = $10,
                status = $11, supervisor = $12, notes = $13, updated_at = NOW()
            WHERE id = $1 AND org_id = $2
            RETURNING *
        """, _uid(member_id),user["org_id"], body.employee_name, body.employee_role,
            body.department, body.access_level, body.systems or [],
            date.fromisoformat(body.start_date),
            date.fromisoformat(body.termination_date) if body.termination_date else None,
            date.fromisoformat(body.access_revoked_date) if body.access_revoked_date else None,
            body.status, body.supervisor, body.notes)
        if not row:
            raise HTTPException(status_code=404, detail="Workforce member not found")
    return _row_dict(row)


# =============================================================================
# 8. PHYSICAL SAFEGUARDS
# =============================================================================

@router.get("/physical")
async def get_physical_safeguards(user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM hipaa_physical_safeguards
            WHERE org_id = $1 ORDER BY category, item_key
        """, user["org_id"])
    return {
        "items": _rows_list(rows),
        "template_items": PHYSICAL_SAFEGUARD_ITEMS,
    }


@router.put("/physical")
async def save_physical_safeguards(body: PhysicalSafeguardBatch, user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        for item in body.items:
            await conn.execute("""
                INSERT INTO hipaa_physical_safeguards
                    (org_id, category, item_key, description, status, hipaa_reference,
                     notes, last_assessed, assessed_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, CURRENT_DATE, $8)
                ON CONFLICT (org_id, category, item_key) DO UPDATE SET
                    description = EXCLUDED.description,
                    status = EXCLUDED.status,
                    hipaa_reference = EXCLUDED.hipaa_reference,
                    notes = EXCLUDED.notes,
                    last_assessed = CURRENT_DATE,
                    assessed_by = EXCLUDED.assessed_by,
                    updated_at = NOW()
            """, user["org_id"], item["category"], item["item_key"],
                item["description"], item.get("status", "not_assessed"),
                item.get("hipaa_reference"), item.get("notes"),
                item.get("assessed_by") or user.get("name") or user.get("email"))
    return {"status": "saved"}


# =============================================================================
# 9. OFFICER DESIGNATION
# =============================================================================

@router.get("/officers")
async def get_officers(user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM hipaa_officers WHERE org_id = $1 ORDER BY role_type
        """, user["org_id"])
    return {"officers": _rows_list(rows)}


@router.put("/officers")
async def upsert_officers(body: OfficerUpsert, user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        for officer in body.officers:
            await conn.execute("""
                INSERT INTO hipaa_officers
                    (org_id, role_type, name, title, email, phone, appointed_date, notes)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (org_id, role_type) DO UPDATE SET
                    name = EXCLUDED.name,
                    title = EXCLUDED.title,
                    email = EXCLUDED.email,
                    phone = EXCLUDED.phone,
                    appointed_date = EXCLUDED.appointed_date,
                    notes = EXCLUDED.notes,
                    updated_at = NOW()
            """, user["org_id"], officer["role_type"], officer["name"],
                officer.get("title"), officer.get("email"), officer.get("phone"),
                date.fromisoformat(officer["appointed_date"]),
                officer.get("notes"))
    return {"status": "saved"}


# =============================================================================
# 10. GAP ANALYSIS
# =============================================================================

@router.get("/gap-analysis")
async def get_gap_analysis(user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM hipaa_gap_responses
            WHERE org_id = $1 ORDER BY section, question_key
        """, user["org_id"])
    return {
        "responses": _rows_list(rows),
        "questions": GAP_ANALYSIS_QUESTIONS,
    }


@router.put("/gap-analysis")
async def save_gap_analysis(body: GapResponseBatch, user: dict = Depends(require_client_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        for resp in body.responses:
            await conn.execute("""
                INSERT INTO hipaa_gap_responses
                    (org_id, question_key, section, hipaa_reference, response,
                     maturity_level, notes, evidence_ref, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
                ON CONFLICT (org_id, questionnaire_version, question_key) DO UPDATE SET
                    response = EXCLUDED.response,
                    maturity_level = EXCLUDED.maturity_level,
                    notes = EXCLUDED.notes,
                    evidence_ref = EXCLUDED.evidence_ref,
                    updated_at = NOW()
            """, user["org_id"], resp["question_key"], resp["section"],
                resp["hipaa_reference"], resp.get("response"),
                resp.get("maturity_level", 0), resp.get("notes"),
                resp.get("evidence_ref"))
    return {"status": "saved"}

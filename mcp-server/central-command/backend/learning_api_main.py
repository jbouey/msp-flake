"""
Learning system endpoints from main.py.

Handles promotion reports from appliances, promotion candidate review,
learning status/coverage/history, and approved-promotion delivery.

Note: dashboard_api/learning_api.py holds the *partner* learning endpoints.
This module holds the main.py learning endpoints (admin + agent facing).
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_auth
from .shared import get_db, require_appliance_bearer

logger = structlog.get_logger()

router = APIRouter(tags=["learning"])


# ============================================================================
# Pydantic Models
# ============================================================================

class PromotionReportRequest(BaseModel):
    """Promotion report from appliance learning system."""
    appliance_id: str
    site_id: str
    checked_at: str
    candidates_found: int = 0
    candidates_promoted: int = 0
    candidates_pending: int = 0
    pending_candidates: List[Dict[str, Any]] = []
    promoted_rules: List[Dict[str, Any]] = []
    rollbacks: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []


class PromotionApprovalRequest(BaseModel):
    """Request to approve or reject a promotion candidate."""
    action: str  # "approve" or "reject"
    reason: Optional[str] = None


# ============================================================================
# Helper Functions
# ============================================================================

async def _send_promotion_notification(req: PromotionReportRequest, db: AsyncSession):
    """Send email notification for promotion events."""
    try:
        alert_email = os.getenv("ALERT_EMAIL", "administrator@osiriscare.net")
        smtp_user = os.getenv("SMTP_USER", "")
        smtp_password = os.getenv("SMTP_PASSWORD", "")

        if not smtp_user or not smtp_password:
            logger.debug("SMTP not configured - skipping promotion notification email")
            return

        subject_parts = []
        if req.candidates_pending > 0:
            subject_parts.append(f"{req.candidates_pending} patterns ready for review")
        if req.candidates_promoted > 0:
            subject_parts.append(f"{req.candidates_promoted} auto-promoted")
        if req.rollbacks:
            subject_parts.append(f"{len(req.rollbacks)} rules rolled back")

        subject = f"[Learning System] {', '.join(subject_parts)}"

        body_parts = [
            f"<h2>Learning System Report</h2>",
            f"<p><strong>Appliance:</strong> {req.appliance_id}</p>",
            f"<p><strong>Site:</strong> {req.site_id}</p>",
            f"<p><strong>Checked at:</strong> {req.checked_at}</p>",
            "<hr>"
        ]

        if req.candidates_pending > 0:
            body_parts.append("<h3>Patterns Ready for Review</h3>")
            body_parts.append("<table border='1' cellpadding='5'>")
            body_parts.append("<tr><th>Pattern</th><th>Action</th><th>Confidence</th><th>Success Rate</th></tr>")
            for c in req.pending_candidates[:10]:
                body_parts.append(
                    f"<tr><td>{c.get('pattern_signature', 'N/A')[:12]}</td>"
                    f"<td>{c.get('recommended_action', 'N/A')}</td>"
                    f"<td>{c.get('confidence_score', 0):.1%}</td>"
                    f"<td>{c.get('stats', {}).get('success_rate', 0):.1%}</td></tr>"
                )
            body_parts.append("</table>")
            body_parts.append("<p><a href='https://dashboard.osiriscare.net/learning'>Review in Dashboard</a></p>")

        if req.candidates_promoted > 0:
            body_parts.append("<h3>Auto-Promoted Rules</h3>")
            body_parts.append("<ul>")
            for r in req.promoted_rules[:10]:
                body_parts.append(
                    f"<li><strong>{r.get('rule_id', 'N/A')}</strong>: "
                    f"{r.get('action', 'N/A')} (confidence: {r.get('confidence', 0):.1%})</li>"
                )
            body_parts.append("</ul>")

        if req.rollbacks:
            body_parts.append("<h3>Rolled Back Rules</h3>")
            body_parts.append("<ul>")
            for r in req.rollbacks[:10]:
                body_parts.append(
                    f"<li><strong>{r.get('rule_id', 'N/A')}</strong>: "
                    f"{r.get('reason', 'Performance degradation')}</li>"
                )
            body_parts.append("</ul>")

        html_body = "\n".join(body_parts)

        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        smtp_host = os.getenv("SMTP_HOST", "mail.privateemail.com")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_from = os.getenv("SMTP_FROM", "alerts@osiriscare.net")

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp_from
        msg["To"] = alert_email
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)

        logger.info(f"Sent promotion notification email to {alert_email}")

    except Exception as e:
        logger.error(f"Failed to send promotion notification: {e}")


async def _notify_site_owner_promotion(
    req: PromotionReportRequest,
    candidate_ids: List[str],
    db: AsyncSession
):
    """Send email notification to site owner about pending promotions."""
    try:
        result = await db.execute(
            text("SELECT contact_email, name FROM sites WHERE site_id = :site_id"),
            {"site_id": req.site_id}
        )
        row = result.fetchone()

        if not row or not row[0]:
            logger.debug(f"No contact email for site {req.site_id}")
            return

        owner_email = row[0]
        site_name = row[1] or req.site_id

        smtp_user = os.getenv("SMTP_USER", "")
        smtp_password = os.getenv("SMTP_PASSWORD", "")

        if not smtp_user or not smtp_password:
            logger.debug("SMTP not configured - skipping site owner notification")
            return

        subject = f"[{site_name}] {req.candidates_pending} automation rules ready for approval"

        dashboard_url = os.getenv("DASHBOARD_URL", "https://dashboard.osiriscare.net")
        approval_link = f"{dashboard_url}/learning?site={req.site_id}"

        body_parts = [
            f"<h2>New Automation Rules Detected</h2>",
            f"<p>The compliance system has identified <strong>{req.candidates_pending}</strong> "
            f"patterns that can be automated for your site.</p>",
            f"<p><strong>Site:</strong> {site_name}</p>",
            f"<p><strong>Appliance:</strong> {req.appliance_id}</p>",
            "<hr>",
            "<h3>Patterns Ready for Review</h3>",
            "<table border='1' cellpadding='8' style='border-collapse: collapse;'>",
            "<tr style='background:#f0f0f0;'><th>Action</th><th>Confidence</th><th>Success Rate</th><th>Occurrences</th></tr>"
        ]

        for c in req.pending_candidates[:5]:
            stats = c.get("stats", {})
            body_parts.append(
                f"<tr>"
                f"<td>{c.get('recommended_action', 'N/A')}</td>"
                f"<td>{c.get('confidence_score', 0):.0%}</td>"
                f"<td>{stats.get('success_rate', 0):.0%}</td>"
                f"<td>{stats.get('total_occurrences', 0)}</td>"
                f"</tr>"
            )

        if req.candidates_pending > 5:
            body_parts.append(f"<tr><td colspan='4'><em>... and {req.candidates_pending - 5} more</em></td></tr>")

        body_parts.extend([
            "</table>",
            "<br>",
            f"<p><a href='{approval_link}' style='background:#4CAF50;color:white;padding:10px 20px;text-decoration:none;border-radius:5px;'>Review & Approve</a></p>",
            "<p style='color:#666;font-size:12px;'>These patterns have been successfully handled automatically multiple times. "
            "Approving them will enable instant automated remediation without requiring AI processing.</p>"
        ])

        html_body = "\n".join(body_parts)

        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        smtp_host = os.getenv("SMTP_HOST", "mail.privateemail.com")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_from = os.getenv("SMTP_FROM", "alerts@osiriscare.net")

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp_from
        msg["To"] = owner_email
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)

        await db.execute(
            text("UPDATE learning_promotion_candidates SET notified_at = :now WHERE id = ANY(:ids)"),
            {"now": datetime.now(timezone.utc).isoformat(), "ids": candidate_ids}
        )
        await db.commit()

        logger.info(f"Sent promotion approval request to {owner_email} for site {req.site_id}")

    except Exception as e:
        logger.error(f"Failed to notify site owner: {e}")


# ============================================================================
# Endpoints
# ============================================================================

@router.post("/api/learning/promotion-report")
async def receive_promotion_report(
    req: PromotionReportRequest,
    db: AsyncSession = Depends(get_db),
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """Receive promotion reports from appliance learning systems."""
    try:
        now = datetime.now(timezone.utc)

        report_id = str(uuid.uuid4())
        await db.execute(
            text("""
                INSERT INTO learning_promotion_reports
                (id, appliance_id, site_id, checked_at, candidates_found,
                 candidates_promoted, candidates_pending, report_data, created_at)
                VALUES (:id, :appliance_id, :site_id, :checked_at, :candidates_found,
                        :candidates_promoted, :candidates_pending, :report_data, :created_at)
            """),
            {
                "id": report_id,
                "appliance_id": req.appliance_id,
                "site_id": req.site_id,
                "checked_at": req.checked_at,
                "candidates_found": req.candidates_found,
                "candidates_promoted": req.candidates_promoted,
                "candidates_pending": req.candidates_pending,
                "report_data": json.dumps({
                    "pending_candidates": req.pending_candidates,
                    "promoted_rules": req.promoted_rules,
                    "rollbacks": req.rollbacks,
                    "errors": req.errors
                }),
                "created_at": now.isoformat()
            }
        )

        candidate_ids = []
        for candidate in req.pending_candidates:
            candidate_id = str(uuid.uuid4())
            candidate_ids.append(candidate_id)
            stats = candidate.get("stats", {})
            await db.execute(
                text("""
                    INSERT INTO learning_promotion_candidates
                    (id, report_id, site_id, appliance_id, pattern_signature,
                     recommended_action, confidence_score, success_rate,
                     total_occurrences, l2_resolutions, promotion_reason,
                     approval_status, created_at)
                    VALUES (:id, :report_id, :site_id, :appliance_id, :pattern_signature,
                            :recommended_action, :confidence_score, :success_rate,
                            :total_occurrences, :l2_resolutions, :promotion_reason,
                            'pending', :created_at)
                """),
                {
                    "id": candidate_id,
                    "report_id": report_id,
                    "site_id": req.site_id,
                    "appliance_id": req.appliance_id,
                    "pattern_signature": candidate.get("pattern_signature", "")[:32],
                    "recommended_action": candidate.get("recommended_action", "unknown"),
                    "confidence_score": candidate.get("confidence_score", 0),
                    "success_rate": stats.get("success_rate", 0),
                    "total_occurrences": stats.get("total_occurrences", 0),
                    "l2_resolutions": stats.get("l2_resolutions", 0),
                    "promotion_reason": candidate.get("promotion_reason", ""),
                    "created_at": now.isoformat()
                }
            )

        await db.commit()

        if req.candidates_pending > 0:
            await _notify_site_owner_promotion(req, candidate_ids, db)

        if req.rollbacks:
            await _send_promotion_notification(req, db)

        logger.info(
            f"Promotion report from {req.appliance_id}: "
            f"{req.candidates_found} found, {req.candidates_pending} pending approval"
        )

        return {"status": "ok", "report_id": report_id, "candidate_ids": candidate_ids}

    except Exception as e:
        logger.error(f"Failed to process promotion report: {e}")
        return {"status": "error", "message": str(e)}


@router.get("/api/learning/status")
async def get_learning_status(db: AsyncSession = Depends(get_db), user: dict = Depends(require_auth)):
    """Get learning loop summary stats for dashboard."""
    try:
        result = await db.execute(text("""
            SELECT
                (SELECT COUNT(*) FROM l1_rules WHERE enabled = true) as total_l1_rules,
                (SELECT COUNT(*) FROM execution_telemetry
                 WHERE created_at > NOW() - INTERVAL '30 days'
                   AND runbook_id IS NOT NULL) as total_l2_decisions_30d,
                (SELECT COUNT(*) FROM execution_telemetry
                 WHERE created_at > NOW() - INTERVAL '30 days') as total_incidents_30d,
                (SELECT COUNT(*) FROM learning_promotion_candidates
                 WHERE approval_status = 'approved'
                   AND approved_at > NOW() - INTERVAL '90 days') as total_promotions_90d
        """))
        row = result.fetchone()
        total_incidents = row.total_incidents_30d or 1
        l1_count = (total_incidents - (row.total_l2_decisions_30d or 0))
        l1_rate = max(0, min(100, l1_count * 100.0 / total_incidents))

        promo_result = await db.execute(text("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE et.success = true) as successful
            FROM execution_telemetry et
            WHERE et.created_at > NOW() - INTERVAL '30 days'
        """))
        promo_row = promo_result.fetchone()
        promo_total = promo_row.total if promo_row else 0
        if promo_total > 0:
            promo_success_rate = round((promo_row.successful / promo_total) * 100, 1)
        else:
            promo_success_rate = None

        return {
            "total_l1_rules": row.total_l1_rules,
            "total_l2_decisions_30d": row.total_l2_decisions_30d or 0,
            "l1_resolution_rate": round(l1_rate, 1),
            "promotion_success_rate": promo_success_rate,
            "total_promotions_90d": row.total_promotions_90d or 0,
        }
    except Exception as e:
        logger.error(f"Failed to get learning status: {e}")
        return {"error": "database_unavailable", "total_l1_rules": None,
                "total_l2_decisions_30d": None, "l1_resolution_rate": None,
                "promotion_success_rate": None}


@router.get("/api/learning/coverage-gaps")
async def get_learning_coverage_gaps(db: AsyncSession = Depends(get_db), user: dict = Depends(require_auth)):
    """Get check_types seen in telemetry that lack L1 rules."""
    try:
        result = await db.execute(text("""
            SELECT
                et.incident_type as check_type,
                COUNT(*) as incident_count_30d,
                MAX(et.created_at) as last_seen,
                EXISTS(
                    SELECT 1 FROM l1_rules lr
                    WHERE lr.enabled = true
                      AND (
                        lr.incident_pattern->>'check_type' = et.incident_type
                        OR lr.incident_pattern->>'incident_type' = et.incident_type
                        OR lr.rule_id ILIKE '%' || REPLACE(et.incident_type, '_', '-') || '%'
                        OR lr.rule_id ILIKE '%' || et.incident_type || '%'
                      )
                ) as has_l1_rule
            FROM execution_telemetry et
            WHERE et.created_at > NOW() - INTERVAL '30 days'
              AND et.incident_type IS NOT NULL
              AND et.incident_type != ''
            GROUP BY et.incident_type
            ORDER BY incident_count_30d DESC
        """))
        rows = result.fetchall()
        return [
            {
                "check_type": row.check_type,
                "incident_count_30d": row.incident_count_30d,
                "last_seen": row.last_seen.isoformat() if row.last_seen else None,
                "has_l1_rule": row.has_l1_rule,
            }
            for row in rows
        ]
    except Exception as e:
        logger.error(f"Failed to get coverage gaps: {e}")
        return []


@router.get("/api/learning/promotion-candidates")
async def get_promotion_candidates(
    site_id: Optional[str] = None,
    status: str = "pending",
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Get promotion candidates for dashboard display."""
    try:
        query = """
            SELECT id, report_id, site_id, appliance_id, pattern_signature,
                   recommended_action, confidence_score, success_rate,
                   total_occurrences, l2_resolutions, promotion_reason,
                   approval_status, approved_by, approved_at, created_at
            FROM learning_promotion_candidates
            WHERE approval_status = :status
        """
        params = {"status": status}

        if site_id:
            query += " AND site_id = :site_id"
            params["site_id"] = site_id

        query += " ORDER BY created_at DESC LIMIT 100"

        result = await db.execute(text(query), params)
        rows = result.fetchall()

        candidates = [
            {
                "id": str(row[0]),
                "report_id": str(row[1]),
                "site_id": row[2],
                "appliance_id": row[3],
                "pattern_signature": row[4],
                "recommended_action": row[5],
                "confidence_score": float(row[6]) if row[6] else 0,
                "success_rate": float(row[7]) if row[7] else 0,
                "total_occurrences": row[8],
                "l2_resolutions": row[9],
                "promotion_reason": row[10],
                "approval_status": row[11],
                "approved_by": str(row[12]) if row[12] else None,
                "approved_at": row[13].isoformat() if row[13] else None,
                "created_at": row[14].isoformat() if row[14] else None
            }
            for row in rows
        ]

        return {
            "status": "ok",
            "total": len(candidates),
            "candidates": candidates
        }

    except Exception as e:
        logger.error(f"Failed to get promotion candidates: {e}")
        return {"status": "error", "message": str(e), "candidates": []}


@router.post("/api/learning/promotions/{candidate_id}/review")
async def review_promotion_candidate(
    candidate_id: str,
    req: PromotionApprovalRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Approve or reject a promotion candidate."""
    try:
        current_user = user

        result = await db.execute(
            text("SELECT site_id, approval_status FROM learning_promotion_candidates WHERE id = :id"),
            {"id": candidate_id}
        )
        row = result.fetchone()

        if not row:
            return JSONResponse(status_code=404, content={"error": "Candidate not found"})

        site_id = row[0]
        current_status = row[1]

        if current_status != "pending":
            return JSONResponse(
                status_code=400,
                content={"error": f"Candidate already {current_status}"}
            )

        if current_user["role"] not in ["admin", "operator"]:
            return JSONResponse(
                status_code=403,
                content={"error": "Insufficient permissions"}
            )

        now = datetime.now(timezone.utc)

        if req.action == "approve":
            await db.execute(
                text("""
                    UPDATE learning_promotion_candidates
                    SET approval_status = 'approved',
                        approved_by = :user_id,
                        approved_at = :now
                    WHERE id = :id
                """),
                {"id": candidate_id, "user_id": current_user["id"], "now": now.isoformat()}
            )
            await db.commit()

            logger.info(f"Promotion candidate {candidate_id} approved by {current_user['username']}")
            return {"status": "ok", "message": "Promotion approved", "approval_status": "approved"}

        elif req.action == "reject":
            await db.execute(
                text("""
                    UPDATE learning_promotion_candidates
                    SET approval_status = 'rejected',
                        approved_by = :user_id,
                        approved_at = :now,
                        rejection_reason = :reason
                    WHERE id = :id
                """),
                {
                    "id": candidate_id,
                    "user_id": current_user["id"],
                    "now": now.isoformat(),
                    "reason": req.reason or "Rejected by user"
                }
            )
            await db.commit()

            logger.info(f"Promotion candidate {candidate_id} rejected by {current_user['username']}")
            return {"status": "ok", "message": "Promotion rejected", "approval_status": "rejected"}

        else:
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid action. Use 'approve' or 'reject'"}
            )

    except Exception as e:
        logger.error(f"Failed to review promotion: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/api/learning/history")
async def get_learning_history(limit: int = 20, db: AsyncSession = Depends(get_db), user: dict = Depends(require_auth)):
    """Get recently promoted L2->L1 patterns for the dashboard timeline."""
    try:
        result = await db.execute(text("""
            SELECT
                lpc.id,
                lpc.pattern_signature,
                COALESCE(lpc.custom_rule_name, lpc.recommended_action, 'L1-' || LEFT(lpc.id::text, 8)) as rule_id,
                lpc.approved_at as promoted_at,
                COALESCE(exec_stats.total, 0) as executions_since,
                COALESCE(exec_stats.success_pct, 0) as success_rate
            FROM learning_promotion_candidates lpc
            LEFT JOIN LATERAL (
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE et.success) * 100.0 / NULLIF(COUNT(*), 0) as success_pct
                FROM execution_telemetry et
                WHERE et.incident_type = split_part(lpc.pattern_signature, ':', 1)
                AND et.created_at > lpc.approved_at
            ) exec_stats ON true
            WHERE lpc.approval_status = 'approved'
            AND lpc.approved_at IS NOT NULL
            ORDER BY lpc.approved_at DESC
            LIMIT :limit
        """), {"limit": limit})

        rows = result.fetchall()
        return [
            {
                "id": str(row.id),
                "pattern_signature": row.pattern_signature,
                "rule_id": row.rule_id,
                "promoted_at": row.promoted_at.isoformat() if row.promoted_at else None,
                "post_promotion_success_rate": float(row.success_rate or 0),
                "executions_since_promotion": int(row.executions_since or 0),
            }
            for row in rows
        ]
    except Exception as e:
        logger.error(f"Failed to get learning history: {e}")
        return []


@router.get("/api/learning/approved-promotions")
async def get_approved_promotions(
    site_id: str,
    since: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """Get approved promotions for an appliance to apply."""
    try:
        query = """
            SELECT id, pattern_signature, recommended_action, confidence_score,
                   success_rate, total_occurrences, l2_resolutions, promotion_reason,
                   approved_at
            FROM learning_promotion_candidates
            WHERE site_id = :site_id
            AND approval_status = 'approved'
        """
        params = {"site_id": site_id}

        if since:
            query += " AND approved_at > :since"
            params["since"] = since

        query += " ORDER BY approved_at ASC"

        result = await db.execute(text(query), params)
        rows = result.fetchall()

        promotions = [
            {
                "id": str(row[0]),
                "pattern_signature": row[1],
                "recommended_action": row[2],
                "confidence_score": float(row[3]) if row[3] else 0,
                "success_rate": float(row[4]) if row[4] else 0,
                "total_occurrences": row[5],
                "l2_resolutions": row[6],
                "promotion_reason": row[7],
                "approved_at": row[8].isoformat() if row[8] else None
            }
            for row in rows
        ]

        return {
            "status": "ok",
            "site_id": site_id,
            "count": len(promotions),
            "promotions": promotions
        }

    except Exception as e:
        logger.error(f"Failed to get approved promotions: {e}")
        return {"status": "error", "message": str(e), "promotions": []}

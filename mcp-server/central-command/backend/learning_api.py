"""
Partner Learning Management API.

Endpoints for partners to view and manage pattern promotions:
- View promotion-eligible patterns across their sites
- Approve/reject patterns for L1 promotion
- View promoted rules and their deployment status
- View execution history and learning stats

Security Notes:
- All endpoints require partner authentication
- Database operations use proper transactions with commit/rollback
- Pattern signatures are validated (16 hex chars)
- No PII in logs (use truncated IDs)
"""

import json
import logging
import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, field_validator
from asyncpg.exceptions import PostgresError, LockNotAvailableError

from .partners import require_partner
from .fleet import get_pool
from .partner_activity_logger import log_partner_learning_action, PartnerEventType

logger = logging.getLogger(__name__)

# Pattern signature format: 16 hex characters
PATTERN_SIGNATURE_REGEX = re.compile(r'^[a-fA-F0-9]{16}$')


def validate_pattern_signature(sig: str) -> bool:
    """Validate pattern signature format (16 hex chars)."""
    return bool(PATTERN_SIGNATURE_REGEX.match(sig))


def redact_partner_id(partner_id: str) -> str:
    """Redact partner ID for safe logging."""
    if not partner_id or len(partner_id) < 8:
        return "***"
    return f"{partner_id[:4]}...{partner_id[-4:]}"

partner_learning_router = APIRouter(
    prefix="/api/partners/me/learning",
    tags=["partner-learning"]
)


# ============================================================================
# Pydantic Models
# ============================================================================

class LearningStats(BaseModel):
    pending_candidates: int
    active_promoted_rules: int
    total_executions_30d: int
    l1_resolution_rate: float
    l2_resolution_rate: float
    l3_escalation_rate: float
    avg_success_rate: float


class PromotionCandidate(BaseModel):
    id: str
    pattern_signature: str
    site_id: str
    site_name: str
    total_occurrences: int
    l1_resolutions: int
    l2_resolutions: int
    l3_resolutions: int
    success_rate: float
    avg_resolution_time_ms: Optional[float]
    recommended_action: Optional[str]
    first_seen: Optional[str]
    last_seen: Optional[str]
    approval_status: str


class PromotedRule(BaseModel):
    id: str
    rule_id: str
    pattern_signature: str
    site_id: str
    site_name: Optional[str]
    status: str
    deployment_count: int
    promoted_at: str
    last_deployed_at: Optional[str]
    notes: Optional[str]


class ApproveRequest(BaseModel):
    deploy_immediately: bool = True
    custom_name: Optional[str] = None
    notes: Optional[str] = None


class RejectRequest(BaseModel):
    reason: str


class BulkApproveRequest(BaseModel):
    pattern_ids: List[str]
    deploy_immediately: bool = True
    notes: Optional[str] = None


class BulkRejectRequest(BaseModel):
    pattern_ids: List[str]
    reason: str


# ============================================================================
# Rule Generation
# ============================================================================

# Mapping from check_type to runbook IDs (mirrors learning_loop.py)
CHECK_TYPE_TO_RUNBOOK = {
    # Legacy NixOS/Python agent check types
    "firewall": "RB-WIN-FIREWALL-001",
    "antivirus": "RB-WIN-AV-001",
    "av_edr": "RB-WIN-AV-001",
    "bitlocker": "RB-WIN-ENCRYPTION-001",
    "encryption": "RB-WIN-ENCRYPTION-001",
    "backup": "RB-WIN-BACKUP-001",
    "patching": "RB-WIN-PATCH-001",
    "patches": "RB-WIN-PATCH-001",
    "logging": "RB-WIN-LOGGING-001",
    "audit_policy": "RB-WIN-LOGGING-001",
    "screen_lock": "RB-WIN-SCREENLOCK-001",
    "screenlock": "RB-WIN-SCREENLOCK-001",
    # Go daemon Windows scanner check types
    "firewall_status": "RB-WIN-SEC-001",
    "windows_defender": "RB-WIN-SEC-003",
    "windows_update": "RB-WIN-SVC-001",
    "audit_logging": "RB-WIN-SEC-002",
    "bitlocker_status": "RB-WIN-SEC-005",
    "smb_signing": "RB-WIN-SEC-007",
    "smb1_protocol": "RB-WIN-SEC-020",
    "screen_lock_policy": "RB-WIN-SEC-016",
    "defender_exclusions": "RB-WIN-SEC-017",
    "dns_config": "RB-WIN-NET-001",
    "network_profile": "RB-WIN-NET-003",
    "service_dns": "RB-WIN-SVC-001",
    "service_netlogon": "RB-WIN-SVC-001",
    "password_policy": "RB-WIN-SEC-022",
    "rdp_nla": "RB-WIN-SEC-023",
    "guest_account": "RB-WIN-SEC-024",
    # Go daemon Linux scanner check types
    "linux_firewall": "LIN-FW-001",
    "linux_ssh_config": "LIN-SSH-001",
    "linux_failed_services": "LIN-SVC-001",
    "linux_audit_logging": "LIN-AUDIT-001",
    "linux_kernel_params": "LIN-KERN-001",
    "linux_log_forwarding": "LIN-LOG-001",
    "linux_file_permissions": "LIN-PERM-001",
    "linux_suid_binaries": "LIN-SUID-001",
    "linux_cron_review": "LIN-CRON-001",
    "linux_disk_space": "LIN-DISK-001",
    "linux_ntp_sync": "LIN-NTP-001",
    "linux_unattended_upgrades": "LIN-UPGRADES-001",
    "linux_cert_expiry": "LIN-CERT-001",
}


def map_action_to_runbook(action: Optional[str], check_type: Optional[str]) -> str:
    """Map an action or check_type to a runbook ID."""
    if check_type and check_type.lower() in CHECK_TYPE_TO_RUNBOOK:
        return CHECK_TYPE_TO_RUNBOOK[check_type.lower()]

    # Try to extract from action string
    if action:
        action_lower = action.lower()
        for key, runbook_id in CHECK_TYPE_TO_RUNBOOK.items():
            if key in action_lower:
                return runbook_id

    # Default fallback
    return "RB-WIN-GENERIC-001"


def generate_rule_from_pattern(pattern: dict, custom_name: Optional[str] = None) -> dict:
    """Generate L1 rule from pattern stats."""

    rule_id = f"L1-PROMOTED-{pattern['pattern_signature'].upper()}"

    # Build conditions from pattern
    conditions = []

    # Add check_type condition if available
    check_type = pattern.get('check_type')
    if check_type:
        conditions.append({
            "field": "check_type",
            "operator": "eq",
            "value": check_type
        })

    # Default condition if none built
    if not conditions:
        conditions.append({
            "field": "drift_detected",
            "operator": "eq",
            "value": True
        })

    # Map to runbook
    runbook_id = map_action_to_runbook(
        pattern.get('recommended_action'),
        check_type
    )

    # Build success rate description
    success_pct = (pattern.get('success_rate') or 0) * 100
    occurrences = pattern.get('total_occurrences', 0)

    rule = {
        "id": rule_id,
        "name": custom_name or f"Auto-promoted: {pattern.get('recommended_action', 'heal')}",
        "description": f"Promoted from L2 with {success_pct:.0f}% success rate over {occurrences} occurrences",
        "conditions": conditions,
        "action": "run_windows_runbook",
        "action_params": {
            "runbook_id": runbook_id,
            "phases": ["remediate", "verify"]
        },
        "hipaa_controls": [],
        "priority": 50,
        "cooldown_seconds": 300,
        "source": "promoted",
        "enabled": True
    }

    return rule


def rule_to_yaml(rule: dict) -> str:
    """Convert rule dict to YAML string."""
    import yaml
    return yaml.dump(rule, default_flow_style=False, sort_keys=False)


# ============================================================================
# API Endpoints
# ============================================================================

@partner_learning_router.get("/stats")
async def get_learning_stats(partner=Depends(require_partner)):
    """Get learning statistics for partner's sites."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        partner_id = partner['id']

        # Get stats from view or calculate
        stats_row = await conn.fetchrow("""
            SELECT
                pending_candidates,
                active_promoted_rules,
                avg_success_rate,
                total_l1_resolutions,
                total_l2_resolutions,
                total_l3_resolutions,
                total_incidents
            FROM v_partner_learning_stats
            WHERE partner_id = $1
        """, partner_id)

        if stats_row:
            total = stats_row['total_incidents'] or 1
            l1 = stats_row['total_l1_resolutions'] or 0
            l2 = stats_row['total_l2_resolutions'] or 0
            l3 = stats_row['total_l3_resolutions'] or 0

            return {
                "pending_candidates": stats_row['pending_candidates'] or 0,
                "active_promoted_rules": stats_row['active_promoted_rules'] or 0,
                "total_executions_30d": total,
                "l1_resolution_rate": round(l1 / total, 3) if total > 0 else 0,
                "l2_resolution_rate": round(l2 / total, 3) if total > 0 else 0,
                "l3_escalation_rate": round(l3 / total, 3) if total > 0 else 0,
                "avg_success_rate": round(stats_row['avg_success_rate'] or 0, 3)
            }

        # Fallback to empty stats
        return {
            "pending_candidates": 0,
            "active_promoted_rules": 0,
            "total_executions_30d": 0,
            "l1_resolution_rate": 0,
            "l2_resolution_rate": 0,
            "l3_escalation_rate": 0,
            "avg_success_rate": 0
        }


@partner_learning_router.get("/candidates")
async def get_promotion_candidates(
    status: Optional[str] = Query(None, description="Filter by approval_status"),
    partner=Depends(require_partner)
):
    """Get promotion-eligible patterns for partner's sites."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        partner_id = partner['id']

        query = """
            SELECT
                vpc.id::text,
                vpc.pattern_signature,
                vpc.site_id,
                vpc.site_name,
                vpc.total_occurrences,
                vpc.l1_resolutions,
                vpc.l2_resolutions,
                vpc.l3_resolutions,
                vpc.success_rate,
                vpc.avg_resolution_time_ms,
                vpc.recommended_action,
                vpc.first_seen::text,
                vpc.last_seen::text,
                vpc.approval_status,
                COALESCE(s.healing_tier, 'standard') as healing_tier,
                lpc.client_endorsed_at IS NOT NULL as client_endorsed,
                lpc.client_endorsed_at::text as client_endorsed_at
            FROM v_partner_promotion_candidates vpc
            JOIN sites s ON s.site_id = vpc.site_id
            LEFT JOIN learning_promotion_candidates lpc
                ON lpc.pattern_signature = vpc.pattern_signature
                AND lpc.site_id = vpc.site_id
            WHERE vpc.partner_id = $1
        """
        params: list = [partner_id]

        if status:
            query += " AND vpc.approval_status = $2"
            params.append(status)

        query += " ORDER BY vpc.success_rate DESC, vpc.total_occurrences DESC"

        rows = await conn.fetch(query, *params)

        candidates = []
        for row in rows:
            candidates.append({
                "id": row['id'],
                "pattern_signature": row['pattern_signature'],
                "site_id": row['site_id'],
                "site_name": row['site_name'],
                "total_occurrences": row['total_occurrences'],
                "l1_resolutions": row['l1_resolutions'],
                "l2_resolutions": row['l2_resolutions'],
                "l3_resolutions": row['l3_resolutions'],
                "success_rate": float(row['success_rate']) if row['success_rate'] else 0,
                "avg_resolution_time_ms": float(row['avg_resolution_time_ms']) if row['avg_resolution_time_ms'] else None,
                "recommended_action": row['recommended_action'],
                "first_seen": row['first_seen'],
                "last_seen": row['last_seen'],
                "approval_status": row['approval_status'] or 'not_submitted',
                "healing_tier": row['healing_tier'],
                "client_endorsed": bool(row['client_endorsed']),
                "client_endorsed_at": row['client_endorsed_at'],
            })

        return {"candidates": candidates, "total": len(candidates)}


@partner_learning_router.get("/candidates/{pattern_id}")
async def get_candidate_details(
    pattern_id: str,
    partner=Depends(require_partner)
):
    """Get detailed information about a specific promotion candidate."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        partner_id = partner['id']

        # Get candidate details
        candidate = await conn.fetchrow("""
            SELECT
                id::text,
                pattern_signature,
                site_id,
                site_name,
                total_occurrences,
                l1_resolutions,
                l2_resolutions,
                l3_resolutions,
                success_rate,
                avg_resolution_time_ms,
                recommended_action,
                first_seen::text,
                last_seen::text,
                approval_status
            FROM v_partner_promotion_candidates
            WHERE partner_id = $1 AND id::text = $2
        """, partner_id, pattern_id)

        if not candidate:
            raise HTTPException(status_code=404, detail="Candidate not found")

        # Get recent execution history for this pattern
        executions = await conn.fetch("""
            SELECT
                execution_id,
                incident_id,
                runbook_id,
                success,
                resolution_level,
                created_at::text
            FROM execution_telemetry
            WHERE site_id = $1
            ORDER BY created_at DESC
            LIMIT 20
        """, candidate['site_id'])

        return {
            "candidate": dict(candidate),
            "execution_history": [dict(e) for e in executions],
            "proposed_rule": generate_rule_from_pattern(dict(candidate))
        }


@partner_learning_router.post("/candidates/{pattern_id}/approve")
async def approve_candidate(
    pattern_id: str,
    request: ApproveRequest,
    http_request: Request,
    partner=Depends(require_partner)
):
    """Approve a pattern for L1 promotion.

    Uses explicit transaction with rollback on failure to ensure
    all-or-nothing promotion (rule + candidate + commands).
    """
    try:
        pool = await get_pool()
    except Exception as e:
        logger.error(f"Database pool unavailable: {e}")
        raise HTTPException(status_code=503, detail="Database temporarily unavailable")

    async with pool.acquire() as conn:
        partner_id = partner['id']

        # Start explicit transaction
        transaction = conn.transaction()
        await transaction.start()

        try:
            # Step 1: Verify partner owns this candidate (read from view, no lock)
            candidate = await conn.fetchrow("""
                SELECT
                    id,
                    pattern_signature,
                    site_id,
                    site_name,
                    check_type,
                    total_occurrences,
                    success_rate,
                    recommended_action
                FROM v_partner_promotion_candidates
                WHERE partner_id = $1 AND id::text = $2
            """, partner_id, pattern_id)

            if not candidate:
                await transaction.rollback()
                raise HTTPException(status_code=404, detail="Candidate not found or not owned by partner")

            # Step 2: Lock the base table row to prevent concurrent approvals
            locked = await conn.fetchrow("""
                SELECT id FROM aggregated_pattern_stats
                WHERE site_id = $1 AND pattern_signature = $2
                FOR UPDATE NOWAIT
            """, candidate['site_id'], candidate['pattern_signature'])

            if not locked:
                await transaction.rollback()
                raise HTTPException(status_code=409, detail="Pattern no longer available")

            # Step 3: Check if already promoted (prevent duplicate)
            existing = await conn.fetchrow("""
                SELECT rule_id, status FROM promoted_rules
                WHERE pattern_signature = $1 AND site_id = $2
            """, candidate['pattern_signature'], candidate['site_id'])

            if existing and existing['status'] == 'active':
                await transaction.rollback()
                raise HTTPException(status_code=409, detail=f"Pattern already promoted as {existing['rule_id']}")

            # Validate pattern signature format
            if not validate_pattern_signature(candidate['pattern_signature']):
                await transaction.rollback()
                raise HTTPException(status_code=400, detail="Invalid pattern signature format")

            # Generate rule
            rule = generate_rule_from_pattern(dict(candidate), request.custom_name)
            rule_yaml = rule_to_yaml(rule)

            # Insert promoted rule
            await conn.execute("""
                INSERT INTO promoted_rules (
                    rule_id, pattern_signature, site_id, partner_id,
                    rule_yaml, rule_json, notes, promoted_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                ON CONFLICT (rule_id) DO UPDATE SET
                    status = 'active',
                    notes = EXCLUDED.notes,
                    promoted_at = NOW()
            """,
                rule['id'],
                candidate['pattern_signature'],
                candidate['site_id'],
                partner_id,
                rule_yaml,
                json.dumps(rule),
                request.notes
            )

            # Auto-create runbook entry for the promoted pattern
            # This ensures the Runbook Library stays in sync with L1 rules
            check_type = candidate.get('check_type') or rule['action_params'].get('runbook_id', 'general')
            promoted_name = request.custom_name or f"Auto-Promoted: {candidate.get('recommended_action', check_type)}"
            promoted_desc = f"L2→L1 promoted pattern ({candidate.get('success_rate', 0) * 100:.0f}% success over {candidate.get('total_occurrences', 0)} occurrences)"
            await conn.execute("""
                INSERT INTO runbooks (runbook_id, name, description, category, check_type, severity, is_disruptive, hipaa_controls, steps)
                VALUES ($1, $2, $3, $4, $5, 'medium', false, ARRAY[]::text[], '[]'::jsonb)
                ON CONFLICT (runbook_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    updated_at = NOW()
            """,
                rule['id'],
                promoted_name,
                promoted_desc,
                rule.get('action_params', {}).get('runbook_id', 'general'),
                check_type
            )

            # Map promoted rule_id to canonical runbook for telemetry correlation
            await conn.execute("""
                INSERT INTO runbook_id_mapping (l1_rule_id, runbook_id)
                VALUES ($1, $2)
                ON CONFLICT (l1_rule_id) DO NOTHING
            """, rule['id'], rule['id'])

            # Update candidate status
            await conn.execute("""
                INSERT INTO learning_promotion_candidates (
                    id, site_id, pattern_signature, approval_status,
                    approved_at, custom_rule_name, approval_notes
                ) VALUES ($1, $2, $3, 'approved', NOW(), $4, $5)
                ON CONFLICT (site_id, pattern_signature) DO UPDATE SET
                    approval_status = 'approved',
                    approved_at = NOW(),
                    custom_rule_name = EXCLUDED.custom_rule_name,
                    approval_notes = EXCLUDED.approval_notes
            """,
                str(uuid.uuid4()),
                candidate['site_id'],
                candidate['pattern_signature'],
                request.custom_name,
                request.notes
            )

            deployed_count = 0
            failed_appliances = []

            # Dispatch to appliances if requested
            if request.deploy_immediately:
                appliances = await conn.fetch("""
                    SELECT appliance_id FROM site_appliances WHERE site_id = $1
                """, candidate['site_id'])

                command_params = json.dumps({
                    "rule_id": rule['id'],
                    "rule_yaml": rule_yaml,
                    "promoted_at": datetime.now(timezone.utc).isoformat()
                })

                for appliance in appliances:
                    try:
                        # ON CONFLICT prevents duplicate commands on retry
                        await conn.execute("""
                            INSERT INTO appliance_commands (
                                appliance_id, command_type, params, created_at
                            ) VALUES ($1, 'sync_promoted_rule', $2, NOW())
                            ON CONFLICT (appliance_id, command_type, params) DO NOTHING
                        """,
                            appliance['appliance_id'],
                            command_params
                        )
                        deployed_count += 1
                    except Exception as e:
                        failed_appliances.append(appliance['appliance_id'])
                        logger.warning(f"Failed to queue command for appliance {appliance['appliance_id']}: {e}")

                if failed_appliances:
                    logger.error(f"Failed to deploy rule {rule['id']} to {len(failed_appliances)} appliances")

                # Update deployment count
                await conn.execute("""
                    UPDATE promoted_rules
                    SET deployment_count = $1, last_deployed_at = NOW()
                    WHERE rule_id = $2
                """, deployed_count, rule['id'])

            # Commit all changes atomically
            await transaction.commit()

            logger.info(f"Pattern {candidate['pattern_signature'][:8]} promoted to rule {rule['id']} by partner {redact_partner_id(partner_id)}")

            await log_partner_learning_action(
                partner_id=str(partner['id']),
                event_type=PartnerEventType.PATTERN_APPROVED,
                pattern_id=candidate['pattern_signature'],
                event_data={"rule_id": str(rule['id']), "deploy_immediately": request.deploy_immediately},
                ip_address=http_request.client.host if http_request.client else None,
                user_agent=http_request.headers.get("user-agent", "")[:500],
            )

            return {
                "status": "approved",
                "rule_id": rule['id'],
                "pattern_signature": candidate['pattern_signature'],
                "site_id": candidate['site_id'],
                "rule_yaml": rule_yaml,
                "deployed_to": deployed_count,
                "failed_appliances": len(failed_appliances),
                "message": f"Rule promoted and deployed to {deployed_count} appliances" if deployed_count else "Rule promoted (pending deployment)"
            }

        except HTTPException:
            # Re-raise HTTP exceptions (already rolled back)
            raise
        except LockNotAvailableError:
            await transaction.rollback()
            raise HTTPException(status_code=409, detail="Pattern is being promoted by another request, please retry")
        except PostgresError as e:
            await transaction.rollback()
            logger.error(f"Database error during promotion: {e}")
            raise HTTPException(status_code=500, detail="Database error during promotion")
        except Exception as e:
            await transaction.rollback()
            logger.error(f"Unexpected error during promotion: {e}")
            raise HTTPException(status_code=500, detail="Failed to promote pattern")


@partner_learning_router.post("/candidates/{pattern_id}/reject")
async def reject_candidate(
    pattern_id: str,
    request: RejectRequest,
    http_request: Request,
    partner=Depends(require_partner)
):
    """Reject a pattern from L1 promotion."""
    try:
        pool = await get_pool()
    except Exception as e:
        logger.error(f"Database pool unavailable: {e}")
        raise HTTPException(status_code=503, detail="Database temporarily unavailable")

    async with pool.acquire() as conn:
        partner_id = partner['id']

        # Start transaction
        transaction = conn.transaction()
        await transaction.start()

        try:
            # Verify ownership
            candidate = await conn.fetchrow("""
                SELECT id, pattern_signature, site_id
                FROM v_partner_promotion_candidates
                WHERE partner_id = $1 AND id::text = $2
            """, partner_id, pattern_id)

            if not candidate:
                await transaction.rollback()
                raise HTTPException(status_code=404, detail="Candidate not found or not owned by partner")

            # Update or insert rejection
            await conn.execute("""
                INSERT INTO learning_promotion_candidates (
                    id, site_id, pattern_signature, approval_status,
                    rejection_reason, approved_at
                ) VALUES ($1, $2, $3, 'rejected', $4, NOW())
                ON CONFLICT (site_id, pattern_signature) DO UPDATE SET
                    approval_status = 'rejected',
                    rejection_reason = EXCLUDED.rejection_reason,
                    approved_at = NOW()
            """,
                str(uuid.uuid4()),
                candidate['site_id'],
                candidate['pattern_signature'],
                request.reason
            )

            await transaction.commit()

            # Don't log rejection reason (may contain PII)
            logger.info(f"Pattern {candidate['pattern_signature'][:8]} rejected by partner {redact_partner_id(partner_id)}")

            await log_partner_learning_action(
                partner_id=str(partner['id']),
                event_type=PartnerEventType.PATTERN_REJECTED,
                pattern_id=candidate['pattern_signature'],
                event_data={"reason": request.reason},
                ip_address=http_request.client.host if http_request.client else None,
                user_agent=http_request.headers.get("user-agent", "")[:500],
            )

            return {
                "status": "rejected",
                "pattern_id": pattern_id
            }

        except HTTPException:
            raise
        except PostgresError as e:
            await transaction.rollback()
            logger.error(f"Database error during rejection: {e}")
            raise HTTPException(status_code=500, detail="Database error")
        except Exception as e:
            await transaction.rollback()
            logger.error(f"Unexpected error during rejection: {e}")
            raise HTTPException(status_code=500, detail="Failed to reject pattern")


@partner_learning_router.get("/promoted-rules")
async def get_promoted_rules(
    status: Optional[str] = Query(None, description="Filter by status (active, disabled, archived)"),
    partner=Depends(require_partner)
):
    """Get all promoted rules for partner's sites."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        partner_id = partner['id']

        query = """
            SELECT
                pr.id::text,
                pr.rule_id,
                pr.pattern_signature,
                pr.site_id,
                s.clinic_name as site_name,
                pr.status,
                pr.deployment_count,
                pr.promoted_at::text,
                pr.last_deployed_at::text,
                pr.notes
            FROM promoted_rules pr
            JOIN sites s ON s.site_id = pr.site_id
            WHERE pr.partner_id = $1
        """
        params = [partner_id]

        if status:
            query += " AND pr.status = $2"
            params.append(status)

        query += " ORDER BY pr.promoted_at DESC"

        rows = await conn.fetch(query, *params)

        return {
            "rules": [dict(row) for row in rows],
            "total": len(rows)
        }


@partner_learning_router.patch("/promoted-rules/{rule_id}/status")
async def update_rule_status(
    rule_id: str,
    request: Request,
    status: str = Query(..., description="New status: active, disabled, archived"),
    partner=Depends(require_partner)
):
    """Update the status of a promoted rule."""
    if status not in ('active', 'disabled', 'archived'):
        raise HTTPException(status_code=400, detail="Invalid status. Must be: active, disabled, archived")

    try:
        pool = await get_pool()
    except Exception as e:
        logger.error(f"Database pool unavailable: {e}")
        raise HTTPException(status_code=503, detail="Database temporarily unavailable")

    async with pool.acquire() as conn:
        partner_id = partner['id']

        # Start transaction
        transaction = conn.transaction()
        await transaction.start()

        try:
            # Verify ownership and update
            result = await conn.execute("""
                UPDATE promoted_rules
                SET status = $1
                WHERE rule_id = $2 AND partner_id = $3
            """, status, rule_id, partner_id)

            if result == "UPDATE 0":
                await transaction.rollback()
                raise HTTPException(status_code=404, detail="Rule not found or not owned by partner")

            await transaction.commit()

            logger.info(f"Rule {rule_id} status changed to {status} by partner {redact_partner_id(partner_id)}")

            await log_partner_learning_action(
                partner_id=str(partner['id']),
                event_type=PartnerEventType.RULE_STATUS_CHANGED,
                pattern_id=rule_id,
                event_data={"new_status": status},
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent", "")[:500],
            )

            return {"status": "updated", "rule_id": rule_id, "new_status": status}

        except HTTPException:
            raise
        except PostgresError as e:
            await transaction.rollback()
            logger.error(f"Database error updating rule status: {e}")
            raise HTTPException(status_code=500, detail="Database error")
        except Exception as e:
            await transaction.rollback()
            logger.error(f"Unexpected error updating rule status: {e}")
            raise HTTPException(status_code=500, detail="Failed to update rule status")


@partner_learning_router.get("/execution-history")
async def get_execution_history(
    site_id: Optional[str] = Query(None, description="Filter by site"),
    limit: int = Query(50, le=200, ge=1),
    partner=Depends(require_partner)
):
    """Get recent execution history across partner's sites."""
    try:
        pool = await get_pool()
    except Exception as e:
        logger.error(f"Database pool unavailable: {e}")
        raise HTTPException(status_code=503, detail="Database temporarily unavailable")

    async with pool.acquire() as conn:
        partner_id = partner['id']

        # Build query with parameterized LIMIT (avoid SQL injection)
        if site_id:
            query = """
                SELECT
                    et.execution_id,
                    et.incident_id,
                    et.site_id,
                    s.clinic_name as site_name,
                    et.runbook_id,
                    et.success,
                    et.resolution_level,
                    et.created_at::text
                FROM execution_telemetry et
                JOIN sites s ON s.site_id = et.site_id
                WHERE s.partner_id = $1 AND et.site_id = $2
                ORDER BY et.created_at DESC
                LIMIT $3
            """
            rows = await conn.fetch(query, partner_id, site_id, limit)
        else:
            query = """
                SELECT
                    et.execution_id,
                    et.incident_id,
                    et.site_id,
                    s.clinic_name as site_name,
                    et.runbook_id,
                    et.success,
                    et.resolution_level,
                    et.created_at::text
                FROM execution_telemetry et
                JOIN sites s ON s.site_id = et.site_id
                WHERE s.partner_id = $1
                ORDER BY et.created_at DESC
                LIMIT $2
            """
            rows = await conn.fetch(query, partner_id, limit)

        return {
            "executions": [dict(row) for row in rows],
            "total": len(rows)
        }


@partner_learning_router.post("/candidates/bulk-approve")
async def bulk_approve_candidates(
    request: BulkApproveRequest,
    http_request: Request,
    partner=Depends(require_partner)
):
    """Bulk approve multiple promotion candidates."""
    if not request.pattern_ids:
        raise HTTPException(status_code=400, detail="No pattern IDs provided")
    if len(request.pattern_ids) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 patterns per bulk operation")

    try:
        pool = await get_pool()
    except Exception as e:
        logger.error(f"Database pool unavailable: {e}")
        raise HTTPException(status_code=503, detail="Database temporarily unavailable")

    approved = 0
    failed = 0
    errors: List[str] = []

    async with pool.acquire() as conn:
        partner_id = partner['id']

        for pattern_id in request.pattern_ids:
            transaction = conn.transaction()
            await transaction.start()

            try:
                # Verify ownership
                candidate = await conn.fetchrow("""
                    SELECT id, pattern_signature, site_id, site_name,
                           check_type, total_occurrences, success_rate,
                           recommended_action
                    FROM v_partner_promotion_candidates
                    WHERE partner_id = $1 AND id::text = $2
                """, partner_id, pattern_id)

                if not candidate:
                    await transaction.rollback()
                    errors.append(f"{pattern_id}: not found")
                    failed += 1
                    continue

                # Lock the row
                locked = await conn.fetchrow("""
                    SELECT id FROM aggregated_pattern_stats
                    WHERE site_id = $1 AND pattern_signature = $2
                    FOR UPDATE NOWAIT
                """, candidate['site_id'], candidate['pattern_signature'])

                if not locked:
                    await transaction.rollback()
                    errors.append(f"{pattern_id}: unavailable")
                    failed += 1
                    continue

                # Check if already promoted
                existing = await conn.fetchrow("""
                    SELECT rule_id, status FROM promoted_rules
                    WHERE pattern_signature = $1 AND site_id = $2
                """, candidate['pattern_signature'], candidate['site_id'])

                if existing and existing['status'] == 'active':
                    await transaction.rollback()
                    errors.append(f"{pattern_id}: already promoted as {existing['rule_id']}")
                    failed += 1
                    continue

                # Generate and insert rule
                rule = generate_rule_from_pattern(dict(candidate))
                rule_yaml = rule_to_yaml(rule)

                await conn.execute("""
                    INSERT INTO promoted_rules (
                        rule_id, pattern_signature, site_id, partner_id,
                        rule_yaml, rule_json, notes, promoted_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                    ON CONFLICT (rule_id) DO UPDATE SET
                        status = 'active', notes = EXCLUDED.notes, promoted_at = NOW()
                """,
                    rule['id'], candidate['pattern_signature'],
                    candidate['site_id'], partner_id,
                    rule_yaml, json.dumps(rule), request.notes
                )

                # Create runbook entry
                check_type = candidate.get('check_type') or rule['action_params'].get('runbook_id', 'general')
                promoted_name = f"Auto-Promoted: {candidate.get('recommended_action', check_type)}"
                promoted_desc = f"L2→L1 promoted pattern ({(candidate.get('success_rate', 0) or 0) * 100:.0f}% success over {candidate.get('total_occurrences', 0)} occurrences)"
                await conn.execute("""
                    INSERT INTO runbooks (runbook_id, name, description, category, check_type, severity, is_disruptive, hipaa_controls, steps)
                    VALUES ($1, $2, $3, $4, $5, 'medium', false, ARRAY[]::text[], '[]'::jsonb)
                    ON CONFLICT (runbook_id) DO UPDATE SET
                        name = EXCLUDED.name, description = EXCLUDED.description, updated_at = NOW()
                """, rule['id'], promoted_name, promoted_desc,
                    rule.get('action_params', {}).get('runbook_id', 'general'), check_type
                )

                # Map rule ID
                await conn.execute("""
                    INSERT INTO runbook_id_mapping (l1_rule_id, runbook_id)
                    VALUES ($1, $2) ON CONFLICT (l1_rule_id) DO NOTHING
                """, rule['id'], rule['id'])

                # Update candidate status
                await conn.execute("""
                    INSERT INTO learning_promotion_candidates (
                        id, site_id, pattern_signature, approval_status,
                        approved_at, approval_notes
                    ) VALUES ($1, $2, $3, 'approved', NOW(), $4)
                    ON CONFLICT (site_id, pattern_signature) DO UPDATE SET
                        approval_status = 'approved', approved_at = NOW(),
                        approval_notes = EXCLUDED.approval_notes
                """,
                    str(uuid.uuid4()), candidate['site_id'],
                    candidate['pattern_signature'], request.notes
                )

                # Deploy commands
                if request.deploy_immediately:
                    appliances = await conn.fetch("""
                        SELECT appliance_id FROM site_appliances WHERE site_id = $1
                    """, candidate['site_id'])
                    command_params = json.dumps({
                        "rule_id": rule['id'],
                        "rule_yaml": rule_yaml,
                        "promoted_at": datetime.now(timezone.utc).isoformat()
                    })
                    for appliance in appliances:
                        await conn.execute("""
                            INSERT INTO appliance_commands (
                                appliance_id, command_type, params, created_at
                            ) VALUES ($1, 'sync_promoted_rule', $2, NOW())
                            ON CONFLICT (appliance_id, command_type, params) DO NOTHING
                        """, appliance['appliance_id'], command_params)

                await transaction.commit()
                approved += 1

            except LockNotAvailableError:
                await transaction.rollback()
                errors.append(f"{pattern_id}: locked by another request")
                failed += 1
            except PostgresError as e:
                await transaction.rollback()
                errors.append(f"{pattern_id}: database error")
                failed += 1
                logger.error(f"Bulk approve DB error for {pattern_id}: {e}")
            except Exception as e:
                await transaction.rollback()
                errors.append(f"{pattern_id}: unexpected error")
                failed += 1
                logger.error(f"Bulk approve error for {pattern_id}: {e}")

    logger.info(
        f"Bulk approve by partner {redact_partner_id(str(partner['id']))}: "
        f"{approved} approved, {failed} failed"
    )

    return {
        "approved": approved,
        "failed": failed,
        "errors": errors
    }


@partner_learning_router.post("/candidates/bulk-reject")
async def bulk_reject_candidates(
    request: BulkRejectRequest,
    http_request: Request,
    partner=Depends(require_partner)
):
    """Bulk reject multiple promotion candidates."""
    if not request.pattern_ids:
        raise HTTPException(status_code=400, detail="No pattern IDs provided")
    if len(request.pattern_ids) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 patterns per bulk operation")
    if not request.reason.strip():
        raise HTTPException(status_code=400, detail="Rejection reason is required")

    try:
        pool = await get_pool()
    except Exception as e:
        logger.error(f"Database pool unavailable: {e}")
        raise HTTPException(status_code=503, detail="Database temporarily unavailable")

    rejected = 0
    failed = 0
    errors: List[str] = []

    async with pool.acquire() as conn:
        partner_id = partner['id']

        for pattern_id in request.pattern_ids:
            try:
                candidate = await conn.fetchrow("""
                    SELECT id, pattern_signature, site_id
                    FROM v_partner_promotion_candidates
                    WHERE partner_id = $1 AND id::text = $2
                """, partner_id, pattern_id)

                if not candidate:
                    errors.append(f"{pattern_id}: not found")
                    failed += 1
                    continue

                await conn.execute("""
                    INSERT INTO learning_promotion_candidates (
                        id, site_id, pattern_signature, approval_status,
                        rejection_reason, approved_at
                    ) VALUES ($1, $2, $3, 'rejected', $4, NOW())
                    ON CONFLICT (site_id, pattern_signature) DO UPDATE SET
                        approval_status = 'rejected',
                        rejection_reason = EXCLUDED.rejection_reason,
                        approved_at = NOW()
                """,
                    str(uuid.uuid4()), candidate['site_id'],
                    candidate['pattern_signature'], request.reason
                )

                rejected += 1

            except PostgresError as e:
                errors.append(f"{pattern_id}: database error")
                failed += 1
                logger.error(f"Bulk reject DB error for {pattern_id}: {e}")
            except Exception as e:
                errors.append(f"{pattern_id}: unexpected error")
                failed += 1
                logger.error(f"Bulk reject error for {pattern_id}: {e}")

    logger.info(
        f"Bulk reject by partner {redact_partner_id(str(partner['id']))}: "
        f"{rejected} rejected, {failed} failed"
    )

    return {
        "rejected": rejected,
        "failed": failed,
        "errors": errors
    }

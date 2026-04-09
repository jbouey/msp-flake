"""Unified flywheel promotion logic.

Single source of truth for approving a learning_promotion_candidate and
creating the full set of derived rows. Both admin (routes.py) and partner
(learning_api.py) paths call this module to ensure identical side effects.

Tables touched (all in one transaction):
1. learning_promotion_candidates — approval_status = 'approved'
2. l1_rules — site-specific + synced (cross-site) version
3. promoted_rules — immutable record of the promotion
4. runbooks — auto-generated entry for the new rule
5. runbook_id_mapping — l1_rule_id → canonical runbook
6. promotion_audit_log — append-only audit trail

This module is the ONLY place that writes to promoted_rules + audit log.
If you're tempted to duplicate this logic — don't. Call promote_candidate()
or promote_pattern_directly().
"""

import json
import logging
from typing import Any, Dict, Optional

import asyncpg

logger = logging.getLogger(__name__)


async def promote_candidate(
    conn: asyncpg.Connection,
    candidate: Dict[str, Any],
    actor: str,
    actor_type: str = "partner",
    custom_name: Optional[str] = None,
    notes: Optional[str] = None,
    rule_yaml: Optional[str] = None,
    rule_json: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Promote a learning_promotion_candidate to a live L1 rule.

    Args:
        conn: asyncpg connection (caller owns transaction)
        candidate: dict with id, site_id, pattern_signature, check_type,
                   success_rate, total_occurrences, l2_resolutions,
                   recommended_action
        actor: username/email of the approver
        actor_type: 'admin' or 'partner' (for audit log)
        custom_name: optional custom rule name
        notes: optional approval notes
        rule_yaml: optional pre-built YAML (partner path has it)
        rule_json: optional pre-built JSON (partner path has it)

    Returns:
        dict with rule_id, synced_rule_id, promoted_at
    """
    pattern_sig = candidate["pattern_signature"]
    site_id = candidate["site_id"]
    check_type = candidate.get("check_type") or ""

    # Parse pattern_signature if it's in "check_type:runbook_id" format
    parts = pattern_sig.split(":", 1) if pattern_sig else []
    incident_type = parts[0] if parts else check_type
    parsed_runbook = parts[1] if len(parts) > 1 else (candidate.get("recommended_action") or "")

    # Rule IDs
    if custom_name:
        rule_id = f"L1-CUSTOM-{_slugify(custom_name)[:30]}"
    else:
        rule_id = f"L1-AUTO-{_slugify(incident_type).upper()[:20]}"
    synced_rule_id = f"SYNC-{rule_id}"

    # Pattern for matching in L1 engine (incident_type is required)
    incident_pattern = {"incident_type": incident_type}
    if check_type and check_type != incident_type:
        incident_pattern["check_type"] = check_type

    # Confidence from candidate's measured success rate, default 0.9
    confidence = float(candidate.get("success_rate") or 0.9)
    runbook_id = parsed_runbook or "general"

    # Step 1: L1 rule (site-specific canary)
    await conn.execute("""
        INSERT INTO l1_rules (
            rule_id, incident_pattern, runbook_id,
            confidence, promoted_from_l2, enabled, source
        ) VALUES ($1, $2::jsonb, $3, $4, true, true, 'promoted')
        ON CONFLICT (rule_id) DO UPDATE SET
            confidence = EXCLUDED.confidence,
            enabled = true
    """, rule_id, json.dumps(incident_pattern), runbook_id, confidence)

    # Step 2: L1 rule (synced fleet-wide version) — ensures cross-site learning
    await conn.execute("""
        INSERT INTO l1_rules (
            rule_id, incident_pattern, runbook_id,
            confidence, promoted_from_l2, enabled, source
        ) VALUES ($1, $2::jsonb, $3, $4, true, true, 'synced')
        ON CONFLICT (rule_id) DO NOTHING
    """, synced_rule_id, json.dumps(incident_pattern), runbook_id, confidence)

    # Step 3: promoted_rules — immutable record
    partner_id = candidate.get("partner_id")
    effective_yaml = rule_yaml or _build_minimal_yaml(rule_id, incident_type, runbook_id)
    effective_json = rule_json or {
        "id": rule_id,
        "incident_pattern": incident_pattern,
        "runbook_id": runbook_id,
        "confidence": confidence,
    }

    await conn.execute("""
        INSERT INTO promoted_rules (
            rule_id, pattern_signature, site_id, partner_id,
            rule_yaml, rule_json, notes, status, promoted_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, 'active', NOW())
        ON CONFLICT (rule_id) DO UPDATE SET
            status = 'active',
            notes = EXCLUDED.notes,
            promoted_at = NOW()
    """, rule_id, pattern_sig, site_id, partner_id,
        effective_yaml, json.dumps(effective_json), notes)

    # Step 4: runbooks library entry
    promoted_name = custom_name or f"Auto-Promoted: {incident_type}"
    promoted_desc = (
        f"L2→L1 promoted pattern "
        f"({confidence * 100:.0f}% success over "
        f"{candidate.get('total_occurrences') or 0} occurrences)"
    )
    await conn.execute("""
        INSERT INTO runbooks (
            runbook_id, name, description, category, check_type,
            severity, is_disruptive, hipaa_controls, steps
        ) VALUES ($1, $2, $3, $4, $5, 'medium', false, ARRAY[]::text[], '[]'::jsonb)
        ON CONFLICT (runbook_id) DO UPDATE SET
            name = EXCLUDED.name,
            description = EXCLUDED.description,
            updated_at = NOW()
    """, rule_id, promoted_name, promoted_desc,
        runbook_id or "general", check_type or incident_type)

    # Step 5: runbook_id_mapping — l1 rule → canonical runbook
    await conn.execute("""
        INSERT INTO runbook_id_mapping (l1_rule_id, runbook_id)
        VALUES ($1, $2)
        ON CONFLICT (l1_rule_id) DO NOTHING
    """, rule_id, rule_id)

    # Step 6: candidate status
    await conn.execute("""
        UPDATE learning_promotion_candidates
        SET approval_status = 'approved',
            approved_at = NOW(),
            custom_rule_name = COALESCE($1, custom_rule_name),
            approval_notes = COALESCE($2, approval_notes)
        WHERE id = $3
    """, custom_name, notes, candidate["id"])

    # Step 7: append-only audit log (WORM-style)
    try:
        await conn.execute("""
            INSERT INTO promotion_audit_log (
                event_type, rule_id, pattern_signature, check_type,
                site_id, confidence_score, success_rate,
                l2_resolutions, total_occurrences, source, actor, metadata
            ) VALUES (
                'approved', $1, $2, $3, $4, $5, $6, $7, $8,
                $9, $10, $11
            )
        """,
            rule_id,
            pattern_sig,
            check_type or incident_type,
            site_id,
            confidence,
            confidence,
            int(candidate.get("l2_resolutions") or 0),
            int(candidate.get("total_occurrences") or 0),
            actor_type,
            actor,
            json.dumps({
                "synced_rule_id": synced_rule_id,
                "custom_name": custom_name,
                "notes": notes,
            }),
        )
    except Exception as e:
        # Audit log failures must not block promotion — but log loudly
        logger.error(f"promotion_audit_log write failed for {rule_id}: {e}")

    logger.info(
        f"Promoted candidate rule_id={rule_id} site={site_id} "
        f"actor={actor} ({actor_type}) confidence={confidence:.2f}"
    )

    return {
        "rule_id": rule_id,
        "synced_rule_id": synced_rule_id,
        "pattern_signature": pattern_sig,
    }


def _slugify(s: str) -> str:
    """Convert a string to a rule-id-safe slug."""
    if not s:
        return "UNKNOWN"
    return "".join(c if c.isalnum() else "-" for c in s).strip("-").upper()


def _build_minimal_yaml(rule_id: str, incident_type: str, runbook_id: str) -> str:
    """Build a minimal L1 rule YAML when partner didn't provide one."""
    return (
        f"id: {rule_id}\n"
        f"name: {incident_type} auto-remediation\n"
        f"description: Promoted from L2 resolutions\n"
        f"action: execute_runbook\n"
        f"conditions:\n"
        f"  - field: incident_type\n"
        f"    operator: eq\n"
        f"    value: {incident_type}\n"
        f"runbook_id: {runbook_id}\n"
    )

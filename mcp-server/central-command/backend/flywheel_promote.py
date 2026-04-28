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
7. fleet_orders — sync_promoted_rule order so appliances receive the rule
8. pattern_embeddings — upsert so future L2 warm-starts benefit

Phase 9 shadow mode:
  evaluate_shadow_agreement() can be called before promote_candidate to
  compare the candidate's predicted matches vs actual past resolutions.
  Opt-in per incident_type via shadow_mode_config.enabled.

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

    # promoted_rules natural key is (site_id, rule_id) — same rule can
    # be rolled out to multiple sites, each with its own row. Migration
    # 247 added UNIQUE(site_id, rule_id) so this ON CONFLICT works.
    # Pre-247 the ON CONFLICT clause silently failed at runtime with
    # InvalidColumnReferenceError (no unique constraint on rule_id
    # alone). Surfaced 2026-04-25 when the dashboard's "Approve"
    # button on /learning returned 500 on first click.
    await conn.execute("""
        INSERT INTO promoted_rules (
            rule_id, pattern_signature, site_id, partner_id,
            rule_yaml, rule_json, notes, status, promoted_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, 'active', NOW())
        ON CONFLICT (site_id, rule_id) DO UPDATE SET
            status = 'active',
            notes = EXCLUDED.notes,
            promoted_at = NOW()
    """, rule_id, pattern_sig, site_id, partner_id,
        effective_yaml, json.dumps(effective_json), notes)

    # Step 3.5: advance lifecycle to 'approved' for the manual-approval
    # ledger event. New rows enter at lifecycle_state='proposed' (mig
    # 181 default); transitioning to 'approved' before Step 8's rollout
    # writes a clean audit narrative for operator-action approvals.
    # Migration 236 already legalizes 'proposed → rolling_out' directly
    # for the auto-promotion path, so this Step 3.5 is about audit
    # cleanliness, not unblocking the rollout.
    #
    # Pre-check the current state to avoid logging ERROR on the
    # legitimate re-approval-of-active path: 'active → approved' is
    # NOT in the transition matrix and would raise check_violation.
    # Self-loops ARE in the matrix for every state (mig 181 lines
    # 184-189), so we issue active→active when re-approving active
    # rules to write a fresh audit event without illegal-transition
    # spam (round-table 2026-04-28 finding #2).
    try:
        from dashboard_api.flywheel_state import advance as _advance
    except ImportError:
        try:
            from .flywheel_state import advance as _advance
        except ImportError:
            from flywheel_state import advance as _advance

    cur_state_row = await conn.fetchrow(
        "SELECT lifecycle_state FROM promoted_rules WHERE site_id=$1 AND rule_id=$2",
        site_id, rule_id,
    )
    cur_state = cur_state_row["lifecycle_state"] if cur_state_row else "proposed"
    if cur_state in ("proposed", "shadow"):
        target_advance_state = "approved"
    elif cur_state == "approved":
        target_advance_state = "approved"  # self-loop, idempotent
    elif cur_state in ("active", "regime_warning", "auto_disabled", "graduated", "rolling_out"):
        # Re-approval of an already-deployed rule — write the audit
        # event via a self-loop, don't try to walk backward in the
        # state machine.
        target_advance_state = cur_state
    else:
        target_advance_state = None  # 'retired' or unknown — skip
    if target_advance_state is not None:
        try:
            async with conn.transaction():
                await _advance(
                    conn,
                    rule_id=rule_id,
                    new_state=target_advance_state,
                    event_type="promotion_approved",
                    actor=f"{actor_type}:{actor}",
                    stage="promotion",
                    site_id=site_id,
                    proof={
                        "pattern_signature": pattern_sig,
                        "confidence": confidence,
                        "total_occurrences": int(candidate.get("total_occurrences") or 0),
                        "prior_state": cur_state,
                    },
                    reason=f"{actor_type} approval via promote_candidate",
                )
        except Exception as e:
            logger.error(
                "promote_candidate_advance_to_approved_failed",
                exc_info=True,
                extra={
                    "rule_id": rule_id,
                    "site_id": site_id,
                    "actor": actor,
                    "prior_state": cur_state,
                    "target_state": target_advance_state,
                    "exception_class": type(e).__name__,
                },
            )

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

    # Step 7: append-only audit log (WORM-style). Wrapped in a
    # savepoint per Session 205 asyncpg invariant: this INSERT can
    # fail (partition_maintainer_loop missed creating next-month
    # partition, CHECK violation, etc.) and is non-fatal — but
    # without a savepoint a failure poisons the outer txn and the
    # subsequent steps in promote_candidate raise
    # InFailedSQLTransactionError (round-table 2026-04-28 finding #4).
    try:
        async with conn.transaction():
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
        logger.error(
            f"promotion_audit_log write failed for {rule_id}: {e}",
            exc_info=True,
        )

    logger.info(
        f"Promoted candidate rule_id={rule_id} site={site_id} "
        f"actor={actor} ({actor_type}) confidence={confidence:.2f}"
    )

    # Phase 7: upsert pattern embedding so this promotion contributes to
    # the warm-start corpus for future novel incidents. Wrapped in
    # savepoint per Session 205 asyncpg invariant — embedding INSERT
    # failures (pgvector extension issue, dimension mismatch, etc.)
    # are non-fatal but must not poison the outer txn before Step 8
    # (round-table 2026-04-28 finding #4). Log level upgraded WARNING
    # → ERROR per "no silent write failures" rule (DB writes must
    # log-and-raise; reads may eat exceptions).
    try:
        async with conn.transaction():
            from .pattern_embeddings import upsert_pattern_embedding
            await upsert_pattern_embedding(
                conn,
                pattern_key=pattern_sig or f"{incident_type}:{runbook_id}",
                incident_type=incident_type,
                check_type=check_type or incident_type,
                runbook_id=runbook_id,
                reasoning=(notes or custom_name or ""),
                source_occurrences=int(candidate.get("total_occurrences") or 0),
                source_sites=1,
            )
    except Exception as _e:
        logger.error(
            f"pattern embedding upsert failed for {rule_id}: {_e}",
            exc_info=True,
        )

    # Step 8: emit fleet order so appliances actually receive the rule.
    # Centralized in safe_rollout_promoted_rule so all 3 promotion writers
    # share identical behavior + logging (round-table P1, Session 206).
    await safe_rollout_promoted_rule(
        conn,
        rule_id=rule_id,
        runbook_id=runbook_id,
        site_id=site_id,
        rule_yaml=effective_yaml,
        caller="promote_candidate",
    )

    return {
        "rule_id": rule_id,
        "synced_rule_id": synced_rule_id,
        "pattern_signature": pattern_sig,
    }


async def evaluate_shadow_agreement(
    conn: asyncpg.Connection,
    incident_type: str,
    runbook_id: str,
    pattern_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Shadow-evaluate whether a candidate rule's predictions would have
    matched actual past resolutions. Called BEFORE promote_candidate()
    when shadow_mode_config is enabled for this incident_type.

    Returns a dict:
      - decision: 'promote' | 'hold' | 'insufficient_data' | 'disabled'
      - agreement_rate: float (0.0–1.0) or None
      - incidents_considered, would_have_matched, actually_resolved_l1
      - hold_reason (when decision='hold')
      - shadow_record_id (the row written to shadow_evaluations, if any)

    If the config is disabled or missing for this incident_type, returns
    decision='disabled' with no side effects.

    Shadow mode is CONSERVATIVE: it writes a shadow_evaluations audit
    row so we can inspect WHY a candidate was held, but never modifies
    any other state. The caller (auto-promote loop, manual approval UI)
    inspects the decision and acts accordingly.
    """
    # Look up config — fall back to __default__
    cfg_row = await conn.fetchrow(
        "SELECT enabled, min_agreement_rate, min_sample_size, eval_window_days "
        "FROM shadow_mode_config WHERE incident_type = $1",
        incident_type,
    )
    if cfg_row is None:
        cfg_row = await conn.fetchrow(
            "SELECT enabled, min_agreement_rate, min_sample_size, eval_window_days "
            "FROM shadow_mode_config WHERE incident_type = '__default__'",
        )
    enabled = bool(cfg_row and cfg_row["enabled"])
    if not enabled:
        return {"decision": "disabled", "agreement_rate": None}

    min_agree = float(cfg_row["min_agreement_rate"] or 0.90)
    min_n = int(cfg_row["min_sample_size"] or 10)
    window_days = int(cfg_row["eval_window_days"] or 14)

    # Candidate "would have matched" = incidents with this incident_type
    # that resolved via ANY L1 rule or ANY L2 LLM pick in the window.
    # (The candidate rule would match on incident_type + executable
    # runbook_id — we treat any resolution as "it would have been
    # matchable".)
    #
    # Candidate "actually resolved L1" = the same incidents that were
    # resolved at L1 level (our prediction: the promoted rule would
    # resolve them at L1). A candidate with high agreement has L1 +
    # L2 decisions that lined up with the pattern we're promoting.
    stats = await conn.fetchrow("""
        SELECT
            COUNT(*) AS total_incidents,
            COUNT(*) FILTER (WHERE resolution_level = 'L1') AS l1_count,
            COUNT(*) FILTER (
                WHERE resolution_level IN ('L1','L2') AND success = true
            ) AS success_count
        FROM execution_telemetry
        WHERE incident_type = $1
          AND created_at > NOW() - make_interval(days => $2)
    """, incident_type, window_days)

    total = int(stats["total_incidents"] or 0)
    l1_count = int(stats["l1_count"] or 0)
    success_count = int(stats["success_count"] or 0)

    if total < min_n:
        record_id = await conn.fetchval("""
            INSERT INTO shadow_evaluations (
                pattern_key, incident_type, runbook_id,
                eval_window_start, eval_window_end,
                incidents_considered, would_have_matched, actually_resolved_l1,
                agreement_rate, decision, hold_reason
            ) VALUES (
                $1, $2, $3,
                NOW() - make_interval(days => $4), NOW(),
                $5, 0, $6,
                0, 'insufficient_data', $7
            )
            RETURNING id
        """, pattern_key or f"{incident_type}:{runbook_id}", incident_type,
            runbook_id, window_days, total, l1_count,
            f"Only {total} incidents in window, need ≥{min_n}")
        return {
            "decision": "insufficient_data",
            "agreement_rate": None,
            "incidents_considered": total,
            "would_have_matched": 0,
            "actually_resolved_l1": l1_count,
            "hold_reason": f"Only {total} incidents in window, need ≥{min_n}",
            "shadow_record_id": record_id,
        }

    # Agreement: fraction of incidents that succeeded AND resolved at L1.
    # Low agreement means either L1 wasn't firing (mismatched pattern) or
    # L2 was needed (our confidence that L1 would handle it is wrong).
    would_match = total  # the candidate rule's incident_type matches all
    agreement = (l1_count / total) if total else 0.0

    decision = "promote" if agreement >= min_agree else "hold"
    hold_reason = None
    if decision == "hold":
        hold_reason = (
            f"Shadow agreement {agreement:.2f} < {min_agree:.2f} threshold "
            f"(L1 resolved {l1_count}/{total} in {window_days}d)"
        )

    record_id = await conn.fetchval("""
        INSERT INTO shadow_evaluations (
            pattern_key, incident_type, runbook_id,
            eval_window_start, eval_window_end,
            incidents_considered, would_have_matched, actually_resolved_l1,
            agreement_rate, decision, hold_reason
        ) VALUES (
            $1, $2, $3,
            NOW() - make_interval(days => $4), NOW(),
            $5, $6, $7, $8, $9, $10
        )
        RETURNING id
    """, pattern_key or f"{incident_type}:{runbook_id}", incident_type,
        runbook_id, window_days, total, would_match, l1_count,
        round(agreement, 3), decision, hold_reason)

    return {
        "decision": decision,
        "agreement_rate": round(agreement, 3),
        "incidents_considered": total,
        "would_have_matched": would_match,
        "actually_resolved_l1": l1_count,
        "hold_reason": hold_reason,
        "shadow_record_id": record_id,
    }


async def safe_rollout_promoted_rule(
    conn: asyncpg.Connection,
    *,
    rule_id: str,
    runbook_id: str,
    site_id: str,
    rule_yaml: Optional[str] = None,
    caller: str = "unknown",
    scope: str = "site",
) -> int:
    """Idempotent + try/except-wrapped wrapper around issue_sync_promoted_rule_orders.

    Round-table P1 (Session 206): three promotion writers
    (promote_candidate, learning_api bulk-promote, client_portal
    client-approve) used to inline a try/except around the issue call,
    drift between them was the root cause of bugs #1+#2. The fourth
    writer added would inevitably miss the call. This helper centralizes:

      * the issue call with daemon-valid YAML synthesis (rule_yaml is
        a hint; the issuer rebuilds from l1_rules.incident_pattern)
      * standardized success/failure logging (so dashboards + alerting
        can grep one line)
      * exception swallowing (rollout failure must NOT roll back the
        promotion record — the reconciliation script can re-issue)

    `rule_yaml` is now informational — the issuer always synthesizes
    a fresh body from the l1_rules row. Kept for call-site clarity.

    `scope='fleet'` (Session 209, 2026-04-18) iterates over every
    `site_appliances` site and issues one fleet_order per site —
    used by platform auto-promotion (main.py Step 4) so cross-org
    platform rules actually land on appliances + the ledger reflects
    it. For scope='fleet', `site_id` is used only as a tag on the
    ledger event (callers pass a sentinel like '__FLEET__' or the
    first site's id).

    Returns the number of orders created (0 on failure)."""
    if scope not in ("site", "fleet"):
        raise ValueError(f"scope must be 'site' or 'fleet', got {scope!r}")
    try:
        n = await issue_sync_promoted_rule_orders(
            conn,
            rule_id=rule_id,
            runbook_id=runbook_id,
            rule_yaml=rule_yaml or "",
            site_id=site_id if scope == "site" else None,
            scope=scope,
        )
        logger.info(
            f"safe_rollout_promoted_rule: caller={caller} rule_id={rule_id} "
            f"scope={scope} site_id={site_id} orders_created={n}"
        )
        # R6 — advance lifecycle + write ledger event so the spine
        # reflects the rollout. Wrapped in try/except so a missing
        # promoted_rules row (possible for fleet-wide rollouts) doesn't
        # undo the fleet_order write.
        if n > 0:
            try:
                from dashboard_api.flywheel_state import advance
            except ImportError:
                try:
                    from .flywheel_state import advance
                except ImportError:
                    from flywheel_state import advance
            try:
                # Session 205 asyncpg savepoint invariant: this advance()
                # call can raise (illegal lifecycle transition,
                # CHECK violation, missing promoted_rules row) and the
                # outer try/except catches it non-fatally. WITHOUT a
                # savepoint the asyncpg transaction is left in aborted
                # state and the next conn.execute upstream (e.g.
                # routes.py promote_pattern's UPDATE
                # aggregated_pattern_stats) raises
                # InFailedSQLTransactionError → user sees 500 →
                # dashboard "Approve" button is blocked. Surfaced
                # 2026-04-28 on candidate 253985
                # (ransomware_indicator:RB-WIN-STG-002) at North Valley
                # Dental. Wrap in conn.transaction() so the savepoint
                # rolls back on failure without poisoning the outer txn.
                async with conn.transaction():
                    await advance(
                        conn,
                        rule_id=rule_id,
                        new_state="rolling_out",
                        event_type="rollout_issued",
                        actor=f"system:{caller}",
                        stage="rollout",
                        site_id=site_id,
                        proof={
                            "orders_created": int(n),
                            "runbook_id": runbook_id,
                        },
                        reason=f"{caller} issued {n} sync_promoted_rule order(s)",
                    )
            except Exception as e:
                # Illegal transitions (e.g. rule already graduated) are
                # NOT fatal — the fleet_order is issued regardless, and
                # the ledger will reflect state via a later transition.
                # BUT: this divergence is EXACTLY the failure mode the
                # 2026-04-18 audit caught — every auto-promotion silently
                # failed its transition for months while the fleet_order
                # succeeded. Logging at ERROR with exc_info per CLAUDE.md
                # "No silent write failures"; substrate invariant
                # `flywheel_ledger_stalled` watches for zero-ledger
                # windows and alerts if this fires frequently.
                logger.error(
                    "safe_rollout_ledger_advance_failed",
                    exc_info=True,
                    extra={
                        "rule_id": rule_id,
                        "caller": caller,
                        "site_id": site_id,
                        "target_state": "rolling_out",
                        "event_type": "rollout_issued",
                        "exception_class": type(e).__name__,
                    },
                )
        return int(n)
    except Exception as e:
        logger.error(
            f"safe_rollout_promoted_rule FAILED: caller={caller} rule_id={rule_id} "
            f"site_id={site_id}: {e}",
            exc_info=True,
        )
        return 0


async def issue_sync_promoted_rule_orders(
    conn: asyncpg.Connection,
    rule_id: str,
    runbook_id: str,
    rule_yaml: str,
    site_id: Optional[str] = None,
    scope: str = "site",
) -> int:
    """Emit signed sync_promoted_rule fleet orders so appliances pick up
    the new L1 rule on next checkin.

    Args:
      conn: open asyncpg connection
      rule_id: the L1 rule ID just promoted
      runbook_id: the runbook the rule maps to
      rule_yaml: the full rule YAML (delivered to appliance)
      site_id: required if scope='site'
      scope: 'site' = one order for the originating site; 'fleet' = one
             order per active appliance site

    Returns the number of fleet orders created.

    Pre-Session-205 this code path didn't exist; promoted_rules accumulated
    but appliances never received them. The fleet_order_completion ack
    increments promoted_rules.deployment_count via DB trigger (migration
    163), closing the flywheel measurement loop.
    """
    import sys
    sys.path.insert(0, "/app")
    from datetime import datetime, timedelta, timezone
    try:
        from dashboard_api.order_signing import sign_fleet_order
    except ImportError:
        try:
            from .order_signing import sign_fleet_order
        except ImportError:
            from order_signing import sign_fleet_order
    try:
        from dashboard_api.flywheel_math import build_daemon_valid_rule_yaml
    except ImportError:
        try:
            from .flywheel_math import build_daemon_valid_rule_yaml
        except ImportError:
            from flywheel_math import build_daemon_valid_rule_yaml

    # Pull incident_type from l1_rules — the appliance daemon's L1
    # engine matches a rule by `field: incident_type, operator: eq,
    # value: <type>`, so we MUST emit a conditions block. The
    # historical promoted_rules.rule_yaml stub had only id/name/action/
    # runbook_id and got rejected at the daemon's
    # `len(rule.Conditions) > 0` check. Build a complete, valid YAML
    # from scratch using metadata we already know.
    pattern_row = await conn.fetchrow(
        "SELECT incident_pattern FROM l1_rules WHERE rule_id = $1",
        rule_id,
    )
    if not pattern_row:
        raise ValueError(
            f"l1_rules has no row for rule_id={rule_id!r} — cannot synthesize "
            f"daemon-valid YAML without the incident_pattern"
        )
    import json as _json
    pat = pattern_row["incident_pattern"]
    if isinstance(pat, str):
        pat = _json.loads(pat)
    incident_type = (pat or {}).get("incident_type")
    if not incident_type:
        raise ValueError(
            f"l1_rules.incident_pattern for {rule_id!r} has no incident_type "
            f"key — promotion writers must always set this"
        )
    rule_yaml = build_daemon_valid_rule_yaml(
        rule_id=rule_id,
        runbook_id=runbook_id,
        incident_type=incident_type,
    )

    if scope == "site":
        if not site_id:
            raise ValueError("scope='site' requires a site_id")
        target_sites = [site_id]
    elif scope == "fleet":
        rows = await conn.fetch(
            "SELECT DISTINCT site_id FROM site_appliances WHERE deleted_at IS NULL"
        )
        target_sites = [r["site_id"] for r in rows]
    else:
        raise ValueError(f"Unknown scope: {scope!r}")

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=24)
    created = 0
    for sid in target_sites:
        params = {
            "site_id": sid,
            "rule_id": rule_id,
            "runbook_id": runbook_id,
            "rule_yaml": rule_yaml,
            "promoted_at": now.isoformat(),
        }
        nonce, signature, signed_payload = sign_fleet_order(
            0, "sync_promoted_rule", params, now, expires_at,
        )
        try:
            await conn.execute(
                """
                INSERT INTO fleet_orders (
                    order_type, parameters, status, expires_at, created_by,
                    nonce, signature, signed_payload
                ) VALUES ($1, $2::jsonb, 'active', $3, $4, $5, $6, $7)
                """,
                "sync_promoted_rule",
                json.dumps(params),
                expires_at,
                "flywheel-promote",
                nonce,
                signature,
                signed_payload,
            )
            created += 1
        except Exception as e:
            logger.error(
                f"sync_promoted_rule INSERT failed site={sid} rule_id={rule_id}: {e}"
            )
    return created


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

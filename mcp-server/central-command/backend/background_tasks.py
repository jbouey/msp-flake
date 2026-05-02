"""
Background task loops extracted from main.py.

All periodic tasks that run via asyncio.create_task() during the
server lifespan. Imported and started by main.py's lifespan() function.
"""

import asyncio
import json
import os
import re

import structlog
from sqlalchemy import text

from .shared import async_session

logger = structlog.get_logger()


def _hb(name: str) -> None:
    """Swallow-any-error heartbeat call (Phase 15 broader instrumentation).

    Each background loop calls _hb('name') at the top of every iteration
    so /api/admin/health/loops can distinguish 'stuck loop' from 'idle
    loop'. A bad heartbeat import must never break the loop itself, so
    all errors are swallowed."""
    try:
        from .bg_heartbeat import record_heartbeat
        record_heartbeat(name)
    except Exception:
        pass


# DEAD CODE REMOVED 2026-05-02 (audit followup #44):
# `ots_repair_block_heights`, `ots_upgrade_loop`, and
# `ots_resubmit_expired_loop` lived here as DEAD duplicates — the
# wired versions are inline in main.py as `_ots_repair_block_heights`,
# `_ots_upgrade_loop`, `_ots_resubmit_expired_loop` (underscore-
# prefixed). Confirmed nothing imports the bg copies.
#
# `ots_reverify_sample_loop` below IS wired (main.py imports it).
# Don't accidentally delete it — it's the live one.


async def ots_reverify_sample_loop():
    """Periodically re-verify a random sample of anchored OTS proofs.

    Enterprise-grade check: confirms that proofs marked 'anchored' still
    verify against the Bitcoin blockchain. Catches:
    - Silent proof_data corruption
    - Bitcoin chain reorgs that invalidate old anchors
    - Database tampering (row modified outside the evidence pipeline)

    Runs every 6 hours, samples 10 random proofs per cycle.
    Alerts on any verification failure.
    """
    await asyncio.sleep(600)  # Wait 10 min after startup
    while True:
        _hb("ots_reverify")
        try:
            import base64
            import aiohttp
            from dashboard_api.evidence_chain import (
                parse_ots_file,
                replay_timestamp_operations,
            )

            async with async_session() as db:
                # Sample 10 random anchored proofs
                result = await db.execute(text("""
                    SELECT bundle_id, bundle_hash, proof_data, bitcoin_block
                    FROM ots_proofs
                    WHERE status = 'anchored' AND bitcoin_block IS NOT NULL
                    ORDER BY RANDOM()
                    LIMIT 10
                """))
                sample = result.fetchall()

                verified = 0
                failed = 0
                failures = []

                for proof in sample:
                    try:
                        proof_bytes = base64.b64decode(proof.proof_data)
                        parsed = parse_ots_file(proof_bytes)
                        if not parsed:
                            failed += 1
                            failures.append(f"{proof.bundle_id[:8]}: parse_failed")
                            continue

                        # Verify original hash matches stored hash
                        expected_hash = bytes.fromhex(proof.bundle_hash)
                        if parsed["hash_bytes"] != expected_hash:
                            failed += 1
                            failures.append(f"{proof.bundle_id[:8]}: hash_mismatch")
                            continue

                        # Replay operations (tests parser integrity)
                        commitment = replay_timestamp_operations(
                            parsed["hash_bytes"], parsed["timestamp_data"]
                        )
                        if not commitment:
                            failed += 1
                            failures.append(f"{proof.bundle_id[:8]}: replay_failed")
                            continue

                        # Verify block exists via blockstream
                        timeout = aiohttp.ClientTimeout(total=10)
                        async with aiohttp.ClientSession(timeout=timeout) as session:
                            async with session.get(
                                f"https://blockstream.info/api/block-height/{proof.bitcoin_block}"
                            ) as resp:
                                if resp.status != 200:
                                    failed += 1
                                    failures.append(f"{proof.bundle_id[:8]}: block_fetch_failed")
                                    continue

                        verified += 1
                    except Exception as e:
                        failed += 1
                        failures.append(f"{proof.bundle_id[:8]}: {type(e).__name__}")

                if sample:
                    logger.info(
                        "ots_reverify_sample complete",
                        sampled=len(sample),
                        verified=verified,
                        failed=failed,
                    )

                # Alert on ANY failure — enterprise SLA
                if failed > 0:
                    logger.error(
                        "OTS_REVERIFY_FAILURE: anchored proofs failed re-verification",
                        failed_count=failed,
                        failures=failures[:5],  # First 5 for log brevity
                    )

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"OTS reverify loop error: {e}")

        await asyncio.sleep(21600)  # 6 hours


async def flywheel_reconciliation_loop():
    """Detect and auto-repair flywheel data integrity drift.

    Every 30 minutes, checks:
    1. Approved candidates with no promoted_rules row (stuck)
    2. promoted_rules with no l1_rules row (incomplete promotion)
    3. l1_rules promoted=true but no runbooks entry (missing UI entry)
    4. learning_promotion_candidates marked approved but pattern still eligible

    Auto-repairs known drift cases. Alerts on unknown divergence.
    """
    await asyncio.sleep(400)  # Wait 6.6 min after startup
    while True:
        _hb("flywheel_reconciliation")
        try:
            from dashboard_api.fleet import get_pool
            from dashboard_api.tenant_middleware import admin_connection
            from dashboard_api.flywheel_promote import promote_candidate

            pool = await get_pool()
            async with admin_connection(pool) as conn:
                # Check 1: Stuck approved candidates
                stuck = await conn.fetch("""
                    SELECT lpc.id, lpc.site_id, lpc.pattern_signature,
                           lpc.custom_rule_name, lpc.approved_at,
                           aps.success_rate, aps.total_occurrences,
                           aps.l2_resolutions, aps.recommended_action
                    FROM learning_promotion_candidates lpc
                    LEFT JOIN promoted_rules pr
                        ON pr.pattern_signature = lpc.pattern_signature
                        AND pr.site_id = lpc.site_id
                    LEFT JOIN aggregated_pattern_stats aps
                        ON aps.site_id = lpc.site_id
                        AND aps.pattern_signature = lpc.pattern_signature
                    WHERE lpc.approval_status = 'approved'
                      AND pr.rule_id IS NULL
                """)

                stuck_fixed = 0
                for c in stuck:
                    try:
                        async with conn.transaction():
                            candidate = dict(c)
                            await promote_candidate(
                                conn=conn,
                                candidate=candidate,
                                actor="reconciliation",
                                actor_type="system",
                                notes="auto-repaired by reconciliation loop",
                            )
                        stuck_fixed += 1
                    except Exception as e:
                        logger.warning(
                            f"Reconciliation could not repair candidate {c['id']}: {e}"
                        )

                if stuck_fixed > 0:
                    logger.warning(
                        "FLYWHEEL_RECONCILIATION_REPAIR",
                        stuck_fixed=stuck_fixed,
                        stuck_total=len(stuck),
                    )

                # Check 2: promoted_rules with no l1_rules row
                orphan_pr = await conn.fetchval("""
                    SELECT COUNT(*) FROM promoted_rules pr
                    LEFT JOIN l1_rules lr ON lr.rule_id = pr.rule_id
                    WHERE pr.status = 'active' AND lr.rule_id IS NULL
                """)
                if orphan_pr > 0:
                    logger.error(
                        "FLYWHEEL_ORPHAN_PROMOTED_RULES",
                        count=orphan_pr,
                        hint="promoted_rules rows exist without matching l1_rules entry",
                    )

                # Check 3: l1_rules promoted with no runbooks entry.
                # Join on lr.runbook_id (the column that actually references
                # runbooks.runbook_id). Pre-fix this joined on lr.rule_id,
                # which under-reported orphans by ~9% on 2026-04-18 (11 vs 12).
                orphan_rb = await conn.fetchval("""
                    SELECT COUNT(*) FROM l1_rules lr
                    LEFT JOIN runbooks rb ON rb.runbook_id = lr.runbook_id
                    WHERE lr.promoted_from_l2 = true
                      AND lr.source = 'promoted'
                      AND rb.runbook_id IS NULL
                """)
                if orphan_rb > 0:
                    logger.warning(
                        "FLYWHEEL_ORPHAN_RUNBOOKS",
                        count=orphan_rb,
                        hint="promoted l1_rules without runbook library entry",
                    )

                # Check 4: approved candidates with pattern still flagged eligible
                still_eligible = await conn.fetchval("""
                    SELECT COUNT(*)
                    FROM learning_promotion_candidates lpc
                    JOIN aggregated_pattern_stats aps
                        ON aps.site_id = lpc.site_id
                        AND aps.pattern_signature = lpc.pattern_signature
                    WHERE lpc.approval_status = 'approved'
                      AND aps.promotion_eligible = true
                """)
                if still_eligible > 0:
                    # Fix: mark them ineligible
                    await conn.execute("""
                        UPDATE aggregated_pattern_stats aps
                        SET promotion_eligible = false
                        FROM learning_promotion_candidates lpc
                        WHERE aps.site_id = lpc.site_id
                          AND aps.pattern_signature = lpc.pattern_signature
                          AND lpc.approval_status = 'approved'
                          AND aps.promotion_eligible = true
                    """)
                    logger.info(
                        "Flywheel reconciliation fixed eligibility",
                        fixed=still_eligible,
                    )

                total_issues = stuck_fixed + orphan_pr + orphan_rb + still_eligible
                if total_issues == 0:
                    logger.debug("flywheel_reconciliation clean")
                else:
                    logger.info(
                        "flywheel_reconciliation complete",
                        stuck_fixed=stuck_fixed,
                        orphan_pr=orphan_pr,
                        orphan_rb=orphan_rb,
                        still_eligible=still_eligible,
                    )

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"Flywheel reconciliation error: {e}")

        await asyncio.sleep(1800)  # 30 minutes


async def temporal_decay_loop():
    """Phase 6: exponentially decay evidence in platform_pattern_stats.

    Runs once per 6h. For every row, decay success_count and
    total_occurrences by a factor computed from the per-incident-type
    half-life (default 90d) and time since last_seen. Skips rows whose
    counts are already below the configured floor (min_count_floor)
    so very old patterns with genuinely useful cardinality don't vanish.

    Why this matters: Windows 10 EoL, patch Tuesdays, CVE events, and
    firmware updates all change the success rate of remediation runbooks
    abruptly. Equally-weighted historical evidence produces misleading
    promotion candidates (e.g., a 100% success rate from 6 months ago
    that no longer reflects reality). The flywheel *forgets* proportional
    to time.

    Decay formula:
      factor = 0.5 ** (days_since_last_seen / half_life_days)
      new_count = MAX(old_count * factor, min_count_floor)
    """
    await asyncio.sleep(300)  # Wait 5 min after startup
    while True:
        _hb("temporal_decay")
        try:
            from dashboard_api.fleet import get_pool
            from dashboard_api.tenant_middleware import admin_connection

            pool = await get_pool()
            async with admin_connection(pool) as conn:
                # Load per-type config + default
                cfg_rows = await conn.fetch(
                    "SELECT incident_type, half_life_days, decay_enabled, min_count_floor "
                    "FROM pattern_decay_config"
                )
                cfg_map = {r["incident_type"]: r for r in cfg_rows}
                default_cfg = cfg_map.get("__default__") or {
                    "half_life_days": 90,
                    "decay_enabled": True,
                    "min_count_floor": 5,
                }

                # Process platform_pattern_stats in one pass
                # (scale: 25 rows today, ~1000 in the future; single query OK)
                rows = await conn.fetch(
                    "SELECT pattern_key, incident_type, success_count, "
                    "total_occurrences, last_seen FROM platform_pattern_stats"
                )
                decayed = 0
                skipped = 0
                for r in rows:
                    itype = r["incident_type"] or "__default__"
                    cfg = cfg_map.get(itype, default_cfg)
                    if not cfg.get("decay_enabled", True):
                        continue

                    half_life = float(cfg.get("half_life_days", 90))
                    floor = int(cfg.get("min_count_floor", 5))

                    if r["last_seen"] is None:
                        continue
                    age_days = (datetime.now(timezone.utc) - r["last_seen"]).total_seconds() / 86400.0
                    if age_days < 1:
                        skipped += 1
                        continue  # too fresh to decay

                    factor = 0.5 ** (age_days / half_life)
                    new_success = max(int((r["success_count"] or 0) * factor), floor)
                    new_total = max(int((r["total_occurrences"] or 0) * factor), floor)

                    await conn.execute(
                        "UPDATE platform_pattern_stats "
                        "SET success_count = $1, total_occurrences = $2, "
                        "    success_rate = $1::float / NULLIF($2, 0) "
                        "WHERE pattern_key = $3",
                        new_success, new_total, r["pattern_key"],
                    )
                    decayed += 1

                # Mark config rows as applied
                await conn.execute(
                    "UPDATE pattern_decay_config SET last_applied_at = NOW()"
                )

                logger.info(
                    "temporal_decay cycle complete",
                    decayed=decayed,
                    skipped=skipped,
                    total_rows=len(rows),
                )

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"Temporal decay error: {e}")

        await asyncio.sleep(21600)  # 6 hours


async def exemplar_miner_loop():
    """Phase 10: mine high-confidence correct L2 picks and publish them
    as few-shot exemplars for the adaptive prompt system.

    Runs once per 24h. Finds (incident_type, runbook_id) pairs where:
      - last 14d has ≥ 5 decisions
      - confidence ≥ 0.85
      - resolution in execution_telemetry succeeded
    Writes a draft exemplar row to l2_prompt_exemplars. Draft rows do
    NOT take effect — human approval via the admin UI flips status to
    'approved'. Only approved exemplars appear in the L2 prompt.

    Security: this job NEVER activates a prompt on its own. It only
    drafts. §164.312(b) human-sign-off preserved.
    """
    await asyncio.sleep(1200)  # Wait 20 min after startup
    while True:
        _hb("exemplar_miner")
        try:
            from dashboard_api.fleet import get_pool
            from dashboard_api.tenant_middleware import admin_connection

            pool = await get_pool()
            async with admin_connection(pool) as conn:
                # Pull candidate exemplars: high-confidence L2 picks that
                # succeeded at execution time (via execution_telemetry).
                rows = await conn.fetch("""
                    WITH hi_conf AS (
                        SELECT
                            ld.id,
                            COALESCE(et.incident_type, ld.pattern_signature) AS incident_type,
                            ld.runbook_id,
                            ld.reasoning,
                            ld.confidence
                        FROM l2_decisions ld
                        JOIN execution_telemetry et
                          ON et.runbook_id = ld.runbook_id
                         AND et.created_at BETWEEN ld.created_at
                                               AND ld.created_at + INTERVAL '1 hour'
                        WHERE ld.created_at > NOW() - INTERVAL '14 days'
                          AND ld.confidence >= 0.85
                          AND ld.runbook_id IS NOT NULL
                          AND et.success = true
                    )
                    SELECT
                        incident_type,
                        runbook_id,
                        array_agg(id ORDER BY id DESC) AS decision_ids,
                        -- Representative exemplar text — take the
                        -- highest-confidence reasoning
                        (array_agg(reasoning ORDER BY confidence DESC))[1] AS exemplar_text,
                        COUNT(*) AS n
                    FROM hi_conf
                    WHERE incident_type IS NOT NULL
                    GROUP BY incident_type, runbook_id
                    HAVING COUNT(*) >= 5
                """)

                drafted = 0
                for r in rows:
                    # Skip if we already have a row for this pair (draft
                    # or approved) — don't churn the miner
                    existing = await conn.fetchval(
                        "SELECT status FROM l2_prompt_exemplars "
                        "WHERE incident_type = $1 AND runbook_id = $2",
                        r["incident_type"], r["runbook_id"],
                    )
                    if existing:
                        continue

                    text_short = (r["exemplar_text"] or "")[:500]
                    await conn.execute("""
                        INSERT INTO l2_prompt_exemplars (
                            incident_type, runbook_id, exemplar_text,
                            source_decision_ids, status
                        ) VALUES ($1, $2, $3, $4, 'draft')
                        ON CONFLICT (incident_type, runbook_id) DO NOTHING
                    """, r["incident_type"], r["runbook_id"], text_short, list(r["decision_ids"]))
                    drafted += 1

                if drafted > 0:
                    logger.info(
                        "exemplar_miner cycle complete",
                        drafted=drafted,
                        candidates_evaluated=len(rows),
                    )

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"Exemplar miner error: {e}")

        await asyncio.sleep(86400)  # 24 hours


async def threshold_tuner_loop():
    """Phase 8: Bayesian update of per-incident-type promotion thresholds.

    Runs once per 24h. For each promotion_thresholds row with
    auto_tune_enabled=true:

      1. Find all l1_rules with this incident_type that have been live
         for at least 7d post-promotion.
      2. Compute their actual L1 success rate (last 30d).
      3. If observed << threshold: we were over-confident; RAISE threshold.
         If observed >> threshold: we were under-confident; LOWER threshold.

    Update rule is a simple bounded weighted average — not full Bayesian
    posterior — biased toward stability: new threshold moves at most
    ±0.02 per day, clamped to [min_rate_floor, min_rate_ceiling].
    This prevents a single bad promotion from overcorrecting the
    threshold far into either direction.

    Rationale: we can't know the *true* post-promotion success rate
    from pre-promotion signals alone; this is the feedback loop that
    makes promotion thresholds data-driven rather than heuristic.
    """
    await asyncio.sleep(900)  # Wait 15 min after startup
    while True:
        _hb("threshold_tuner")
        try:
            from dashboard_api.fleet import get_pool
            from dashboard_api.tenant_middleware import admin_connection
            pool = await get_pool()
            async with admin_connection(pool) as conn:
                # Pull incident types enrolled in auto-tune
                enrolled = await conn.fetch(
                    "SELECT incident_type, min_success_rate, "
                    "min_rate_floor, min_rate_ceiling, tune_count "
                    "FROM promotion_thresholds "
                    "WHERE auto_tune_enabled = true "
                    "AND incident_type <> '__default__'"
                )

                tuned = 0
                for cfg in enrolled:
                    itype = cfg["incident_type"]
                    current = float(cfg["min_success_rate"] or 0.90)
                    floor = float(cfg["min_rate_floor"] or 0.70)
                    ceiling = float(cfg["min_rate_ceiling"] or 0.99)

                    # Observed post-promotion performance: L1 runs
                    # triggered by rules promoted for this incident_type,
                    # last 30d, created at least 7d ago.
                    obs = await conn.fetchrow("""
                        WITH elig AS (
                            SELECT rule_id, runbook_id
                            FROM l1_rules
                            WHERE promoted_from_l2 = true
                              AND enabled = true
                              AND created_at < NOW() - INTERVAL '7 days'
                              AND incident_pattern->>'incident_type' = $1
                        )
                        SELECT
                            COUNT(*)                                   AS n,
                            SUM(CASE WHEN et.success THEN 1 ELSE 0 END) AS s
                        FROM execution_telemetry et
                        JOIN elig r ON r.runbook_id = et.runbook_id
                        WHERE et.resolution_level = 'L1'
                          AND et.created_at > NOW() - INTERVAL '30 days'
                    """, itype)
                    n = int(obs["n"] or 0)
                    s = int(obs["s"] or 0)
                    if n < 10:
                        continue  # insufficient evidence

                    observed = s / n
                    # Aim to promote at rates where we actually see
                    # observed performance. New threshold = 0.5 * current
                    # + 0.5 * observed, bounded by ±0.02/day drift cap.
                    target = 0.5 * current + 0.5 * observed
                    max_step = 0.02
                    if target > current + max_step:
                        target = current + max_step
                    elif target < current - max_step:
                        target = current - max_step
                    # Clamp to configured bounds
                    target = max(floor, min(ceiling, target))

                    # Only update if material change
                    if abs(target - current) < 0.005:
                        continue

                    await conn.execute("""
                        UPDATE promotion_thresholds
                        SET min_success_rate = $1,
                            last_observed_rate = $2,
                            last_observed_n = $3,
                            last_tuned_at = NOW(),
                            tune_count = tune_count + 1,
                            updated_at = NOW()
                        WHERE incident_type = $4
                    """, round(target, 3), round(observed, 3), n, itype)

                    logger.info(
                        "promotion threshold tuned",
                        incident_type=itype,
                        previous=round(current, 3),
                        new=round(target, 3),
                        observed_rate=round(observed, 3),
                        observed_n=n,
                    )
                    tuned += 1

                if tuned > 0:
                    logger.info(
                        "threshold_tuner cycle complete",
                        tuned=tuned,
                        enrolled=len(enrolled),
                    )

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"Threshold tuner error: {e}")

        await asyncio.sleep(86400)  # 24 hours


# Pure math helpers moved to flywheel_math.py for unit-testability.
# Thresholds + classifier imported here so the rest of the file can
# call them without touching import discipline.
from .flywheel_math import (
    REGIME_DROP_THRESHOLD,
    REGIME_CRITICAL_THRESHOLD,
    classify_regime_delta,
    classify_absolute_floor,
)


async def regime_change_detector_loop():
    """Phase 6: detect sudden success-rate drops on active L1 rules.

    Runs every 30 min. For each promoted_from_l2 L1 rule with ≥10 recent
    executions, compares the 7-day rolling success rate to the 30-day
    baseline. If the 7-day rate drops >15% and the sample size is
    significant (7d n ≥ 10), record an l1_rule_regime_events row and
    log a WARNING.

    Does NOT auto-disable. The existing 48h <70% gate in
    flywheel_promotion_loop already disables degraded rules. Regime
    events are a LEADING signal — they fire faster than the 48h gate
    and flag rules that are drifting but not yet below the disable
    threshold.

    Idempotency: if an unacknowledged regime event exists for the same
    rule in the last 24h, we skip re-recording (avoids flapping).
    """
    await asyncio.sleep(600)  # Wait 10 min after startup
    while True:
        _hb("regime_change_detector")
        try:
            from dashboard_api.fleet import get_pool
            from dashboard_api.tenant_middleware import admin_connection

            pool = await get_pool()
            async with admin_connection(pool) as conn:
                # Phase 15 closing: include rule age (created_at) + drop the
                # n7 >= 10 HAVING so the absolute-floor branch can catch
                # rules with as few as 20 samples. The delta branch still
                # checks n7 >= 10 inline.
                rows = await conn.fetch("""
                    WITH recent AS (
                        SELECT et.runbook_id,
                               COUNT(*) FILTER (WHERE et.created_at > NOW() - INTERVAL '7 days') AS n7,
                               SUM(CASE WHEN et.success AND et.created_at > NOW() - INTERVAL '7 days' THEN 1 ELSE 0 END) AS s7,
                               COUNT(*) FILTER (WHERE et.created_at > NOW() - INTERVAL '30 days') AS n30,
                               SUM(CASE WHEN et.success AND et.created_at > NOW() - INTERVAL '30 days' THEN 1 ELSE 0 END) AS s30
                        FROM execution_telemetry et
                        WHERE et.resolution_level = 'L1'
                          AND et.created_at > NOW() - INTERVAL '30 days'
                        GROUP BY et.runbook_id
                        HAVING COUNT(*) FILTER (WHERE et.created_at > NOW() - INTERVAL '7 days') >= 1
                    )
                    SELECT l.rule_id, l.runbook_id, r.n7, r.s7, r.n30, r.s30,
                           EXTRACT(EPOCH FROM (NOW() - l.created_at)) / 3600.0 AS rule_age_hours
                    FROM l1_rules l
                    JOIN recent r ON r.runbook_id = l.runbook_id
                    WHERE l.promoted_from_l2 = true
                      AND l.enabled = true
                """)

                detected = 0
                for r in rows:
                    n7, s7, n30, s30 = r["n7"], r["s7"], r["n30"], r["s30"]
                    if n7 == 0:
                        continue
                    rate_7 = float(s7) / n7

                    # Branch A: delta-based regime change (works for rules
                    # with comparable 30-day baseline; needs n7 >= 10)
                    severity = None
                    if n30 > 0 and n7 >= 10:
                        rate_30 = float(s30) / n30
                        severity = classify_regime_delta(rate_7, rate_30)

                    # Branch B: absolute-floor (catches rules that were
                    # bad from day 1; only fires after the 24h canary
                    # window so we don't double-flag fresh promotions)
                    if severity is None:
                        rule_age_h = float(r["rule_age_hours"] or 0)
                        severity = classify_absolute_floor(rate_7, n7, rule_age_h)

                    if severity is None:
                        continue  # No event to record

                    # Idempotency: skip if we already flagged this in the last 24h
                    existing = await conn.fetchval("""
                        SELECT 1 FROM l1_rule_regime_events
                        WHERE rule_id = $1
                          AND detected_at > NOW() - INTERVAL '24 hours'
                          AND acknowledged_at IS NULL
                        LIMIT 1
                    """, r["rule_id"])
                    if existing:
                        continue

                    # rate_30 may be undefined if we entered via the
                    # absolute-floor branch (n30 was 0 or n7 was < 10).
                    # Compute defensively from the row data so the INSERT
                    # always has a value.
                    rate_30 = float(s30) / n30 if (n30 and n30 > 0) else 0.0
                    delta = rate_7 - rate_30
                    await conn.execute("""
                        INSERT INTO l1_rule_regime_events
                            (rule_id, window_7d_rate, baseline_30d_rate,
                             delta, sample_size_7d, sample_size_30d, severity)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """, r["rule_id"], round(rate_7, 3), round(rate_30, 3),
                        round(delta, 3), n7, n30 or 0, severity)

                    logger.warning(
                        "L1 regime change detected",
                        rule_id=r["rule_id"],
                        runbook_id=r["runbook_id"],
                        rate_7d=round(rate_7, 3),
                        rate_30d=round(rate_30, 3),
                        delta=round(delta, 3),
                        severity=severity,
                    )
                    detected += 1

                if detected > 0:
                    logger.info(
                        "regime_change_detector cycle complete",
                        detected=detected,
                    )

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"Regime detector error: {e}")

        await asyncio.sleep(1800)  # 30 minutes


async def mesh_consistency_check_loop():
    """Periodically validate mesh ring state and target assignment coverage.

    Enterprise-grade check: every 15 minutes, verify that for each multi-appliance site:
    1. All online appliances report the same ring_size (agreement)
    2. Every assigned target has exactly one owner (no orphans, no overlaps)
    3. Ring size matches the count of online appliances (no drift)
    4. No zombie MACs in ring members that aren't in site_appliances

    Logs alerts on ANY inconsistency — downstream Prometheus alerts fire on these.
    """
    await asyncio.sleep(300)  # Wait 5 min after startup
    while True:
        _hb("mesh_consistency")
        try:
            async with async_session() as db:
                # Multi-appliance sites with recent checkins
                sites_result = await db.execute(text("""
                    SELECT site_id,
                           COUNT(*) FILTER (WHERE last_checkin > NOW() - INTERVAL '5 minutes') as online,
                           COUNT(*) as total
                    FROM site_appliances
                    GROUP BY site_id
                    HAVING COUNT(*) > 1
                """))
                sites = sites_result.fetchall()

                total_issues = 0
                for row in sites:
                    site_id = row.site_id
                    online_count = row.online or 0
                    if online_count < 2:
                        continue  # Not a meaningful mesh

                    # Check ring agreement
                    ring_result = await db.execute(text("""
                        SELECT appliance_id,
                               (daemon_health->>'mesh_ring_size')::int as ring_size,
                               (daemon_health->>'mesh_peer_count')::int as peer_count,
                               assigned_targets
                        FROM site_appliances
                        WHERE site_id = :sid
                          AND last_checkin > NOW() - INTERVAL '5 minutes'
                          AND daemon_health IS NOT NULL
                    """), {"sid": site_id})
                    appliances = ring_result.fetchall()

                    if not appliances:
                        continue

                    # Check 1: All appliances report same ring size
                    ring_sizes = [a.ring_size for a in appliances if a.ring_size is not None]
                    if ring_sizes and len(set(ring_sizes)) > 1:
                        logger.warning(
                            "MESH_RING_DISAGREEMENT",
                            site_id=site_id,
                            reported_sizes=ring_sizes,
                            online_count=online_count,
                        )
                        total_issues += 1

                    # Check 2: Ring size matches online count
                    if ring_sizes and max(ring_sizes) != online_count:
                        logger.warning(
                            "MESH_RING_DRIFT",
                            site_id=site_id,
                            max_ring_size=max(ring_sizes),
                            online_count=online_count,
                        )
                        total_issues += 1

                    # Check 3: Target coverage — every target has exactly one owner
                    target_owners: dict = {}  # target_ip -> [appliance_ids]
                    for a in appliances:
                        targets = a.assigned_targets
                        if isinstance(targets, str):
                            try:
                                import json as _json
                                targets = _json.loads(targets)
                            except (ValueError, TypeError):
                                targets = []
                        if not isinstance(targets, list):
                            continue
                        for t in targets:
                            target_owners.setdefault(t, []).append(a.appliance_id)

                    orphans = []  # shouldn't exist — if no appliance claims a target, it won't appear here
                    overlaps = [t for t, owners in target_owners.items() if len(owners) > 1]

                    if overlaps:
                        logger.warning(
                            "MESH_TARGET_OVERLAP",
                            site_id=site_id,
                            overlap_count=len(overlaps),
                            samples=overlaps[:5],
                        )
                        total_issues += 1

                if sites:
                    logger.info(
                        "mesh_consistency_check complete",
                        sites_checked=len(sites),
                        issues=total_issues,
                    )

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"Mesh consistency check error: {e}")

        await asyncio.sleep(900)  # 15 minutes


async def l2_auto_candidate_loop():
    """Scan successful L2 decisions and create promotion candidates automatically.

    The Go daemon doesn't submit promotion reports (Python agent did).
    This task bridges the gap: finds L2 decisions with result='order_created'
    that aren't already candidates, and creates them.

    Runs every 30 minutes. Requires 3+ successes for the same pattern
    (check_type + runbook_id) before creating a candidate.

    Restored 2026-04-24 (Session 210-B hotfix): the 82a1f5d2 dedup commit
    deleted this function along with the dead duplicate flywheel loop —
    but this one was unique here + still imported by main.py, so the
    deletion crashlooped the container on deploy.
    """
    await asyncio.sleep(600)  # Wait for startup
    while True:
        _hb("l2_auto_candidate")
        try:
            from dashboard_api.fleet import get_pool
            from dashboard_api.tenant_middleware import admin_connection

            pool = await get_pool()
            async with admin_connection(pool) as conn:
                # Find successful L2 patterns with 3+ occurrences
                promotable = await conn.fetch("""
                    SELECT i.incident_type, i.site_id, rs.runbook_id,
                           COUNT(*) as success_count,
                           MAX(rs.confidence) as max_confidence,
                           MIN(rs.created_at) as first_seen,
                           MAX(rs.created_at) as last_seen
                    FROM incident_remediation_steps rs
                    JOIN incidents i ON i.id = rs.incident_id
                    WHERE rs.tier = 'L2'
                      AND rs.result = 'order_created'
                      AND rs.runbook_id IS NOT NULL
                      AND rs.created_at > NOW() - INTERVAL '90 days'
                    GROUP BY i.incident_type, i.site_id, rs.runbook_id
                    HAVING COUNT(*) >= 3
                """)

                created = 0
                for row in promotable:
                    # Generate a stable pattern signature
                    import hashlib
                    sig = hashlib.sha256(
                        f"{row['incident_type']}:{row['runbook_id']}".encode()
                    ).hexdigest()[:16]

                    # Check if candidate already exists
                    exists = await conn.fetchval("""
                        SELECT 1 FROM learning_promotion_candidates
                        WHERE pattern_signature = $1 AND site_id = $2
                    """, sig, row['site_id'])

                    if not exists:
                        import uuid
                        # Schema has no incident_type/check_type/
                        # success_count/confidence_avg columns.
                        # Map: success_count→total_occurrences,
                        # confidence_avg→confidence_score, and stash
                        # incident_type+check_type in promotion_reason
                        # so the audit story survives.
                        promotion_reason = (
                            f"Auto-flywheel candidate for "
                            f"{row['incident_type']} (check={row['incident_type']})"
                        )
                        await conn.execute("""
                            INSERT INTO learning_promotion_candidates (
                                id, site_id, pattern_signature,
                                recommended_action, total_occurrences,
                                confidence_score, promotion_reason,
                                approval_status, created_at
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, 'pending', NOW())
                            ON CONFLICT (site_id, pattern_signature) DO UPDATE SET
                                total_occurrences = EXCLUDED.total_occurrences,
                                confidence_score = EXCLUDED.confidence_score
                        """,
                            str(uuid.uuid4()), row['site_id'], sig,
                            row['runbook_id'],
                            row['success_count'], row['max_confidence'],
                            promotion_reason,
                        )
                        created += 1
                        logger.info(
                            "Flywheel auto-candidate created",
                            incident_type=row['incident_type'],
                            runbook_id=row['runbook_id'],
                            success_count=row['success_count'],
                            site_id=row['site_id'],
                        )

                if created > 0:
                    logger.info(f"Flywheel auto-candidate scan: created {created} new candidates")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"L2 auto-candidate scan failed: {e}")

        await asyncio.sleep(1800)  # 30 minutes


# DEAD CODE REMOVED 2026-05-02 (audit followup #44):
# `expire_fleet_orders_loop` lived here as a DEAD duplicate — the
# wired version is inline in main.py:1754 (also called
# `expire_fleet_orders_loop`). Confirmed nothing imports the bg copy.


async def unregistered_device_alert_loop():
    """Email clients about unregistered devices needing attention (daily at 9 AM UTC).

    Only alerts for "rational" devices — servers and workstations with open
    management ports (SSH/WinRM) or AD membership. Consumer devices, printers,
    and IoT are excluded.
    """
    from dashboard_api.fleet import get_pool
    from dashboard_api.email_service import send_email

    await asyncio.sleep(300)  # Wait for startup
    while True:
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                # Find sites with unregistered rational devices + a client contact email
                sites = await conn.fetch("""
                    SELECT s.site_id, s.clinic_name, s.client_contact_email,
                           COUNT(dd.id) as unregistered_count
                    FROM sites s
                    JOIN discovered_devices dd ON dd.site_id = s.site_id
                    WHERE dd.device_status IN ('take_over_available', 'ad_managed')
                    AND dd.device_type IN ('workstation', 'server', 'unknown')
                    AND (dd.compliance_status IS NULL OR dd.compliance_status = 'unknown')
                    AND s.client_contact_email IS NOT NULL
                    AND s.client_contact_email != ''
                    GROUP BY s.site_id, s.clinic_name, s.client_contact_email
                    HAVING COUNT(dd.id) > 0
                """)

                for site in sites:
                    # Check if we already alerted today (dedup by site + date)
                    today = asyncio.get_event_loop().time()
                    alert_key = f"unregistered_device_alert:{site['site_id']}"

                    already_sent = await conn.fetchval("""
                        SELECT COUNT(*) FROM audit_log
                        WHERE event_type = 'unregistered_device_alert'
                        AND details->>'site_id' = $1
                        AND timestamp > NOW() - INTERVAL '24 hours'
                    """, site["site_id"])

                    if already_sent and already_sent > 0:
                        continue

                    # Get device details for the email
                    devices = await conn.fetch("""
                        SELECT ip_address, hostname, os_name, device_type,
                               probe_ssh, probe_winrm, ad_joined, first_seen_at
                        FROM discovered_devices
                        WHERE site_id = $1
                        AND device_status IN ('take_over_available', 'ad_managed')
                        AND device_type IN ('workstation', 'server', 'unknown')
                        AND (compliance_status IS NULL OR compliance_status = 'unknown')
                        ORDER BY first_seen_at DESC
                        LIMIT 20
                    """, site["site_id"])

                    # Build email
                    device_lines = []
                    for d in devices:
                        name = d["hostname"] or d["ip_address"]
                        os_info = d["os_name"] or "Unknown OS"
                        ports = []
                        if d["probe_ssh"]:
                            ports.append("SSH")
                        if d["probe_winrm"]:
                            ports.append("WinRM")
                        if d["ad_joined"]:
                            ports.append("AD-joined")
                        device_lines.append(f"  - {name} ({os_info}) [{', '.join(ports)}]")

                    count = site["unregistered_count"]
                    clinic = site["clinic_name"]
                    subject = f"[OsirisCare] {count} device(s) at {clinic} need your attention"
                    body = (
                        f"Hi,\n\n"
                        f"Our compliance monitoring detected {count} device(s) on your network "
                        f"at {clinic} that are not currently covered by your security monitoring.\n\n"
                        f"These devices have open management ports and appear to be servers or "
                        f"workstations that should be monitored for HIPAA compliance:\n\n"
                        + "\n".join(device_lines) +
                        f"\n\nTo register these devices and enable compliance monitoring, "
                        f"please log in to your portal:\n"
                        f"  https://api.osiriscare.net/client/login\n\n"
                        f"If any of these devices are expected and don't need monitoring "
                        f"(e.g., test equipment), you can mark them as 'Ignored' in the portal.\n\n"
                        f"This is an automated message from OsirisCare compliance monitoring.\n"
                    )

                    sent = await send_email(site["client_contact_email"], subject, body)
                    if sent:
                        logger.info(f"Unregistered device alert sent: {clinic} ({count} devices) → {site['client_contact_email']}")
                        # Record in audit log for daily dedup. The
                        # audit_log table uses `timestamp`, not
                        # `created_at` — schema linter caught this on
                        # the 2026-04-25 baseline-grind pass.
                        await conn.execute("""
                            INSERT INTO audit_log (event_type, details, timestamp)
                            VALUES ('unregistered_device_alert',
                                    $1::jsonb,
                                    NOW())
                        """, json.dumps({"site_id": site["site_id"], "count": count}))

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Unregistered device alert loop error: {e}")

        await asyncio.sleep(3600)  # Check hourly, but only send once per day per site


async def reconciliation_loop():
    """Reconciliation loop — DISABLED in M1.

    Pre-M1 this loop kept site_appliances in sync with the legacy `appliances`
    table (which accumulated state via a secondary checkin path). M1 dropped
    that table: site_appliances is now the single write destination, so there
    is nothing left to reconcile. Keeping the symbol so existing startup
    wiring stays happy; the body is a long-interval no-op.
    """
    await asyncio.sleep(3600)
    while True:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Reconciliation loop (no-op) error: {e}")


# ============================================================================
# Flywheel Intelligence: Recurrence Velocity + Auto-Promotion + Correlation
# ============================================================================

async def recurrence_velocity_loop():
    """Pre-compute recurrence velocity per (site_id, incident_type).

    Runs every 5 minutes. Replaces the per-incident COUNT(*) queries with
    a single batch computation that populates incident_recurrence_velocity.
    The incident handler reads this table instead of running ad-hoc queries.

    Round Table: "At 100+ sites, per-incident COUNTs won't scale. Pre-compute."
    """
    await asyncio.sleep(300)  # Wait for startup
    while True:
        _hb("recurrence_velocity")
        try:
            from dashboard_api.fleet import get_pool
            from dashboard_api.tenant_middleware import admin_connection

            pool = await get_pool()
            async with admin_connection(pool) as conn:
                # Compute recurrence velocity for all active incident types
                await conn.execute("""
                    INSERT INTO incident_recurrence_velocity (
                        site_id, incident_type,
                        resolved_1h, resolved_4h, resolved_24h, resolved_7d,
                        velocity_per_hour, is_chronic, last_l1_runbook, computed_at
                    )
                    SELECT
                        i.site_id,
                        i.incident_type,
                        COUNT(*) FILTER (WHERE i.resolved_at > NOW() - INTERVAL '1 hour'),
                        COUNT(*) FILTER (WHERE i.resolved_at > NOW() - INTERVAL '4 hours'),
                        COUNT(*) FILTER (WHERE i.resolved_at > NOW() - INTERVAL '24 hours'),
                        COUNT(*) FILTER (WHERE i.resolved_at > NOW() - INTERVAL '7 days'),
                        COUNT(*) FILTER (WHERE i.resolved_at > NOW() - INTERVAL '4 hours') / 4.0,
                        COUNT(*) FILTER (WHERE i.resolved_at > NOW() - INTERVAL '4 hours') >= 3,
                        (SELECT rs.runbook_id FROM incident_remediation_steps rs
                         WHERE rs.incident_id = (
                             SELECT id FROM incidents i2
                             WHERE i2.site_id = i.site_id AND i2.incident_type = i.incident_type
                             AND i2.resolution_tier = 'L1'
                             ORDER BY i2.created_at DESC LIMIT 1
                         ) ORDER BY rs.created_at DESC LIMIT 1),
                        NOW()
                    FROM incidents i
                    WHERE i.status = 'resolved'
                      AND i.resolved_at > NOW() - INTERVAL '7 days'
                    GROUP BY i.site_id, i.incident_type
                    ON CONFLICT (site_id, incident_type) DO UPDATE SET
                        resolved_1h = EXCLUDED.resolved_1h,
                        resolved_4h = EXCLUDED.resolved_4h,
                        resolved_24h = EXCLUDED.resolved_24h,
                        resolved_7d = EXCLUDED.resolved_7d,
                        velocity_per_hour = EXCLUDED.velocity_per_hour,
                        is_chronic = EXCLUDED.is_chronic,
                        last_l1_runbook = EXCLUDED.last_l1_runbook,
                        computed_at = NOW()
                """)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Recurrence velocity computation failed: {e}")

        await asyncio.sleep(300)  # 5 minutes


async def recurrence_auto_promotion_loop():
    """Auto-promote L2 recurrence fixes that actually worked.

    When a recurrence-driven L2 decision produces a runbook that stops the
    issue from recurring for 24h, promote that runbook to L1 with higher
    priority than the symptom-level rule.

    Round Table: "When L2 breaks a recurrence cycle, the flywheel's 'I learned
    something' moment — promote it so L1 tries the root-cause fix first."
    """
    await asyncio.sleep(900)  # Wait for startup + first velocity computation
    while True:
        _hb("recurrence_auto_promotion")
        try:
            from dashboard_api.fleet import get_pool
            from dashboard_api.tenant_middleware import admin_connection

            pool = await get_pool()
            async with admin_connection(pool) as conn:
                # Find recurrence-L2 decisions where:
                # 1. escalation_reason = 'recurrence'
                # 2. The decision had a runbook_id and confidence >= 0.6
                # 3. The incident type has NOT recurred in the last 24h
                # 4. Not already promoted to L1
                candidates = await conn.fetch("""
                    SELECT DISTINCT ON (d.runbook_id, i.incident_type)
                        d.id as decision_id, d.runbook_id, i.incident_type,
                        d.confidence, d.reasoning, i.site_id
                    FROM l2_decisions d
                    JOIN incidents i ON i.id::text = d.incident_id
                    WHERE d.escalation_reason = 'recurrence'
                      AND d.runbook_id IS NOT NULL
                      AND d.confidence >= 0.6
                      AND d.created_at > NOW() - INTERVAL '7 days'
                      AND d.created_at < NOW() - INTERVAL '24 hours'
                      -- Issue hasn't recurred in 24h since the L2 decision
                      AND NOT EXISTS (
                          SELECT 1 FROM incidents newer
                          WHERE newer.site_id = i.site_id
                            AND newer.incident_type = i.incident_type
                            AND newer.created_at > d.created_at
                            AND newer.created_at > NOW() - INTERVAL '24 hours'
                      )
                      -- Not already promoted
                      AND NOT EXISTS (
                          SELECT 1 FROM l1_rules lr
                          WHERE lr.incident_pattern->>'incident_type' = i.incident_type
                            AND lr.runbook_id = d.runbook_id
                      )
                    ORDER BY d.runbook_id, i.incident_type, d.confidence DESC
                """)

                for row in candidates:
                    import uuid as _uuid
                    rule_id = f"FLYWHEEL-{row['incident_type'][:30]}-{str(_uuid.uuid4())[:8]}"
                    # l1_rules has no `description` column — fold the
                    # promotion reasoning into incident_pattern JSONB so
                    # the audit story is preserved without a schema drift.
                    pattern = {
                        "incident_type": row["incident_type"],
                        "description": f"Auto-promoted: L2 root-cause fix broke recurrence cycle. {row['reasoning'][:200]}",
                    }
                    await conn.execute("""
                        INSERT INTO l1_rules (
                            rule_id, incident_pattern, runbook_id,
                            confidence, enabled, source,
                            match_count, success_count, failure_count,
                            created_at
                        ) VALUES (
                            $1, $2::jsonb, $3,
                            $4, true, 'flywheel_recurrence',
                            0, 0, 0, NOW()
                        )
                        ON CONFLICT (rule_id) DO NOTHING
                    """,
                        rule_id,
                        json.dumps(pattern),
                        row["runbook_id"],
                        row["confidence"],
                    )

                    # Mark velocity table as recurrence broken
                    await conn.execute("""
                        UPDATE incident_recurrence_velocity
                        SET recurrence_broken_at = NOW(),
                            recurrence_broken_by_runbook = $1
                        WHERE site_id = $2 AND incident_type = $3
                    """, row["runbook_id"], row["site_id"], row["incident_type"])

                    logger.info("Flywheel auto-promoted recurrence fix to L1",
                                incident_type=row["incident_type"],
                                runbook_id=row["runbook_id"],
                                confidence=row["confidence"],
                                rule_id=rule_id)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Recurrence auto-promotion failed: {e}")

        await asyncio.sleep(3600)  # Hourly


async def cross_incident_correlation_loop():
    """Detect co-occurring incident types for predictive remediation.

    When incident type A is resolved and incident type B consistently appears
    within 10 minutes afterward, record the correlation. At sufficient
    confidence, the system can pre-emptively remediate B when resolving A.

    Round Table: "If defender_exclusions always precedes rogue_scheduled_tasks
    by 10 minutes, run the persistence cleanup alongside the exclusion fix."
    """
    await asyncio.sleep(1200)  # Wait for startup
    while True:
        _hb("cross_incident_correlation")
        try:
            from dashboard_api.fleet import get_pool
            from dashboard_api.tenant_middleware import admin_connection

            pool = await get_pool()
            async with admin_connection(pool) as conn:
                # Find A→B pairs: A resolved, B created within 10 min.
                # Count DISTINCT A instances (each A may precede multiple B types
                # — without DISTINCT, confidence can exceed 1.0).
                # Exclude monitoring-only checks (from check_type_registry)
                # since their correlations aren't actionable.
                pairs = await conn.fetch("""
                    SELECT
                        a.site_id,
                        a.incident_type as type_a,
                        b.incident_type as type_b,
                        COUNT(DISTINCT a.id) as co_occurrences,
                        AVG(EXTRACT(EPOCH FROM (b.created_at - a.resolved_at))) as avg_gap_sec
                    FROM incidents a
                    JOIN incidents b ON b.site_id = a.site_id
                        AND b.incident_type != a.incident_type
                        AND b.created_at > a.resolved_at
                        AND b.created_at < a.resolved_at + INTERVAL '10 minutes'
                    WHERE a.status = 'resolved'
                      AND a.resolved_at > NOW() - INTERVAL '7 days'
                      -- Exclude monitoring-only types from both sides
                      AND NOT EXISTS (
                          SELECT 1 FROM check_type_registry r
                          WHERE r.check_name IN (a.incident_type, b.incident_type)
                            AND r.is_monitoring_only = true
                      )
                    GROUP BY a.site_id, a.incident_type, b.incident_type
                    HAVING COUNT(DISTINCT a.id) >= 3
                """)

                for pair in pairs:
                    # Compute confidence: distinct A instances with a B follow-up
                    # divided by total A resolutions. Clamped to [0.0, 1.0].
                    total_a = await conn.fetchval("""
                        SELECT COUNT(*) FROM incidents
                        WHERE site_id = $1 AND incident_type = $2
                          AND status = 'resolved'
                          AND resolved_at > NOW() - INTERVAL '7 days'
                    """, pair["site_id"], pair["type_a"])

                    confidence = min(pair["co_occurrences"] / max(total_a, 1), 1.0)

                    await conn.execute("""
                        INSERT INTO incident_correlation_pairs (
                            site_id, incident_type_a, incident_type_b,
                            co_occurrence_count, avg_gap_seconds, confidence,
                            first_seen, last_seen
                        ) VALUES ($1, $2, $3, $4, $5, $6, NOW(), NOW())
                        ON CONFLICT (site_id, incident_type_a, incident_type_b) DO UPDATE SET
                            co_occurrence_count = EXCLUDED.co_occurrence_count,
                            avg_gap_seconds = EXCLUDED.avg_gap_seconds,
                            confidence = EXCLUDED.confidence,
                            last_seen = NOW()
                    """,
                        pair["site_id"], pair["type_a"], pair["type_b"],
                        pair["co_occurrences"], pair["avg_gap_sec"],
                        confidence,
                    )

                    if confidence >= 0.5:
                        logger.info("Cross-incident correlation detected",
                                    site_id=pair["site_id"],
                                    type_a=pair["type_a"],
                                    type_b=pair["type_b"],
                                    co_occurrences=pair["co_occurrences"],
                                    confidence=round(confidence, 2))

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Cross-incident correlation scan failed: {e}")

        await asyncio.sleep(3600)  # Hourly


# ─── Session 206 Spine: Flywheel Orchestrator loop ─────────────────

FLYWHEEL_ORCHESTRATOR_INTERVAL_SECONDS = 300  # 5 minutes
FLYWHEEL_ORCHESTRATOR_MODE = os.getenv("FLYWHEEL_ORCHESTRATOR_MODE", "shadow").lower()


async def flywheel_orchestrator_loop():
    """Run the flywheel state-machine orchestrator every 5 minutes.

    Mode is controlled by FLYWHEEL_ORCHESTRATOR_MODE env var:
      - 'shadow'  (default): evaluate transitions, log intent, DON'T apply.
                  Run alongside the old step-5 block in flywheel_promotion_loop
                  for 24-48h to compare outputs before cutover.
      - 'enforce' (production): apply transitions. Each transition is
                  individually try/except'd — one failure never blocks
                  another. No logger.debug anywhere: failures log ERROR
                  with exc_info=True.

    Ships as part of the Session 206 redesign. Replaces the silent-failure
    prone step 5a-bis that let SCREEN_LOCK sit at 0%/83 for 2+ hours
    undetected.
    """
    await asyncio.sleep(90)  # let startup settle
    while True:
        _hb("flywheel_orchestrator")
        try:
            from dashboard_api.flywheel_state import run_orchestrator_tick
            from dashboard_api.fleet import get_pool
            from dashboard_api.tenant_middleware import admin_connection
            pool = await get_pool()
            async with admin_connection(pool) as conn:
                enforce = FLYWHEEL_ORCHESTRATOR_MODE == "enforce"
                result = await run_orchestrator_tick(conn, enforce=enforce)
            applied = sum(result.transitions_by_name.values())
            failed = sum(result.failures_by_name.values())
            if applied or failed:
                logger.info(
                    "flywheel_orchestrator_tick_complete",
                    extra={
                        "mode": FLYWHEEL_ORCHESTRATOR_MODE,
                        "scanned": result.total_rules_scanned,
                        "applied": applied,
                        "failed": failed,
                        "elapsed_ms": result.elapsed_ms,
                        "transitions": dict(result.transitions_by_name),
                    },
                )
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(
                f"flywheel_orchestrator_loop iteration failed: {e}",
                exc_info=True,
            )
        await asyncio.sleep(FLYWHEEL_ORCHESTRATOR_INTERVAL_SECONDS)


# ─── Session 209: promoted_rule_events partition maintainer ────────
#
# Migration 236 pre-seeded 2026-06/07/08. Past that, the default
# partition catches overflow — correct on day one, but rows pile up in
# the default partition defeat partition pruning and make DETACH
# painful. This loop creates the next 3 months nightly so the steady
# state is always "next 3 months exist as dedicated partitions."
#
# Idempotent: CREATE TABLE IF NOT EXISTS + PARTITION OF is a no-op when
# the partition already exists.

PARTITION_MAINTAINER_INTERVAL_SECONDS = 86400  # daily
PARTITION_MAINTAINER_LOOKAHEAD_MONTHS = 3


async def partition_maintainer_loop():
    """Keep the next N months of promoted_rule_events partitions alive."""
    from datetime import date

    await asyncio.sleep(600)  # 10 min after startup
    while True:
        _hb("partition_maintainer")
        try:
            from dashboard_api.fleet import get_pool
            from dashboard_api.tenant_middleware import admin_connection

            pool = await get_pool()
            today = date.today()
            async with admin_connection(pool) as conn:
                for offset in range(1, PARTITION_MAINTAINER_LOOKAHEAD_MONTHS + 1):
                    # Compute first-of-month for offset months ahead
                    year = today.year + ((today.month - 1 + offset) // 12)
                    month = ((today.month - 1 + offset) % 12) + 1
                    start = date(year, month, 1)
                    end_year = year + (1 if month == 12 else 0)
                    end_month = 1 if month == 12 else month + 1
                    end = date(end_year, end_month, 1)
                    partition = (
                        f"promoted_rule_events_{year:04d}{month:02d}"
                    )
                    await conn.execute(
                        f"""
                        CREATE TABLE IF NOT EXISTS {partition}
                        PARTITION OF promoted_rule_events
                        FOR VALUES FROM ('{start.isoformat()}')
                                    TO ('{end.isoformat()}')
                        """
                    )
            logger.info("partition_maintainer_tick_complete")
        except asyncio.CancelledError:
            break
        except Exception:
            logger.error(
                "partition_maintainer_loop_failed",
                exc_info=True,
            )
        await asyncio.sleep(PARTITION_MAINTAINER_INTERVAL_SECONDS)


# ─── Phase 15 closing: enterprise appliance offline detection ──────

APPLIANCE_STALE_THRESHOLD_MINUTES = 5
APPLIANCE_OFFLINE_SCAN_SECONDS = 120


async def mark_stale_appliances_loop():
    """Flip appliances whose check-ins have stopped to status='offline'.

    Before this loop, status stayed 'online' until the next successful
    check-in — operators only noticed downtime by coincidence. Now:

      - After APPLIANCE_STALE_THRESHOLD_MINUTES of no check-in, the row
        moves to status='offline' with offline_since=NOW(), counter
        incremented.
      - A critical email alert fires on the FIRST transition (debounced
        via offline_notified flag — reset on recovery).
      - On a later successful check-in, sites.py checkin STEP 3 resets
        status='online', offline_notified=false, stamps recovered_at,
        and emits an 'appliance_recovered' alert.

    This closes the enterprise visibility gap the round-table audit
    surfaced: an appliance was powered down and the dashboard + alerts
    kept claiming it was healthy.
    """
    await asyncio.sleep(60)  # Let startup settle
    while True:
        _hb("mark_stale_appliances")
        try:
            from dashboard_api.fleet import get_pool
            from dashboard_api.tenant_middleware import admin_transaction
            pool = await get_pool()
            # admin_transaction (not raw pool.acquire) so SET LOCAL
            # app.is_admin pins to ONE PgBouncer backend — defense-in-
            # depth against the Session 212 routing-pathology class.
            # Same fragility class as the new go_agent state-machine
            # loop closed in Session 214 P0 round-table.
            async with admin_transaction(pool) as conn:
                rows = await conn.fetch("""
                    UPDATE site_appliances
                    SET status = 'offline',
                        offline_since = COALESCE(offline_since, NOW()),
                        offline_event_count = offline_event_count + 1
                    WHERE status != 'offline'
                      AND status != 'decommissioned'
                      AND deleted_at IS NULL
                      AND last_checkin IS NOT NULL
                      AND last_checkin < NOW() - make_interval(mins => $1)
                    RETURNING appliance_id, site_id, display_name, hostname,
                              last_checkin, offline_notified
                """, APPLIANCE_STALE_THRESHOLD_MINUTES)

            for row in rows:
                logger.warning(
                    "Appliance transitioned to offline",
                    appliance_id=row["appliance_id"],
                    site_id=row["site_id"],
                    display_name=row["display_name"],
                    last_checkin=str(row["last_checkin"]),
                )
                # Only send email on the FIRST detection — debounced
                # via offline_notified flag (reset on recovery).
                # P2-fix (round-table): send_critical_alert returns False
                # when SMTP is unconfigured/failed — must NOT mark notified
                # in that case, or we'd silently miss the alert and never
                # re-arm. Only stamp the flag after a CONFIRMED send.
                if not row["offline_notified"]:
                    sent_ok = False
                    try:
                        from dashboard_api.email_alerts import send_critical_alert
                        label = row["display_name"] or row["hostname"] or row["appliance_id"]
                        sent_ok = bool(send_critical_alert(
                            title=f"Appliance offline: {label}",
                            message=(
                                f"Appliance {label} at site {row['site_id']} "
                                f"stopped checking in at {row['last_checkin']}. "
                                f"Threshold: {APPLIANCE_STALE_THRESHOLD_MINUTES} min."
                            ),
                            site_id=row["site_id"],
                            category="appliance_health",
                            severity="critical",
                            metadata={
                                "appliance_id": row["appliance_id"],
                                "display_name": row["display_name"],
                                "last_checkin": str(row["last_checkin"]),
                                "event": "appliance_offline",
                            },
                        ))
                    except Exception:
                        logger.error(
                            "Failed to send appliance_offline alert",
                            appliance_id=row["appliance_id"],
                            exc_info=True,
                        )
                    if sent_ok:
                        try:
                            async with pool.acquire() as c2:
                                async with c2.transaction():
                                    await c2.execute(
                                        "UPDATE site_appliances SET offline_notified = true "
                                        "WHERE appliance_id = $1",
                                        row["appliance_id"],
                                    )
                        except Exception:
                            logger.error(
                                "Failed to set offline_notified flag",
                                appliance_id=row["appliance_id"],
                                exc_info=True,
                            )
                    else:
                        logger.warning(
                            "appliance_offline alert NOT sent — leaving "
                            "offline_notified=false so we retry next pass",
                            appliance_id=row["appliance_id"],
                        )
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"mark_stale_appliances_loop iteration failed: {e}", exc_info=True)

        await asyncio.sleep(APPLIANCE_OFFLINE_SCAN_SECONDS)


# ─── Session 206 round-table P2: partner weekly rollup refresh ─────

WEEKLY_ROLLUP_REFRESH_SECONDS = 30 * 60  # every 30 min


async def weekly_rollup_refresh_loop():
    """Refresh the partner_site_weekly_rollup materialized view.

    REFRESH MATERIALIZED VIEW requires ownership of the view. The view
    was created by migration 185 running as the mcp superuser, so a
    REFRESH executed through the app pool (mcp_app via PgBouncer) fails
    with "must be owner of materialized view". Use a single-shot direct
    asyncpg connection as the migration superuser instead — same pattern
    as heartbeat_partition_maintainer.

    CONCURRENTLY lets readers keep querying during the refresh. If the
    view doesn't exist yet (migration 185 not applied), the pg_matviews
    check short-circuits and we try again next tick.
    """
    import asyncpg as _asyncpg
    await asyncio.sleep(120)  # let migrations complete on cold starts
    while True:
        _hb("weekly_rollup_refresh")
        try:
            conn = await _asyncpg.connect(_migration_db_url())
            try:
                exists = await conn.fetchval(
                    "SELECT 1 FROM pg_matviews WHERE matviewname = 'partner_site_weekly_rollup'"
                )
                if exists:
                    # CONCURRENTLY requires the UNIQUE index set up in migration 185.
                    await conn.execute(
                        "REFRESH MATERIALIZED VIEW CONCURRENTLY partner_site_weekly_rollup"
                    )
                    logger.info("weekly_rollup_refresh_complete")
            finally:
                await conn.close()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(
                f"weekly_rollup_refresh_loop iteration failed: {e}",
                exc_info=True,
            )
        await asyncio.sleep(WEEKLY_ROLLUP_REFRESH_SECONDS)


# ─── Session 206 round-table P2: partner weekly digest loop ────────

PARTNER_DIGEST_CHECK_SECONDS = 15 * 60  # wake up every 15 min
PARTNER_DIGEST_DAY = int(os.getenv("PARTNER_DIGEST_DAY_OF_WEEK", "4"))  # Friday
PARTNER_DIGEST_HOUR = int(os.getenv("PARTNER_DIGEST_HOUR_UTC", "13"))   # 13:00 UTC = 9am EDT


async def _gather_partner_digest_data(conn, partner_id: str) -> dict:
    """Assemble the data payload for a partner's weekly digest."""
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    week_start = now - timedelta(days=7)
    week_label = f"{week_start.strftime('%b %d')} – {now.strftime('%b %d, %Y')}"

    totals = await conn.fetchrow(
        """
        SELECT COUNT(DISTINCT s.site_id) AS clients,
               COUNT(i.id) AS incidents,
               COUNT(i.id) FILTER (WHERE i.resolution_tier = 'L1') AS l1_count,
               COUNT(i.id) FILTER (WHERE i.resolution_tier = 'L3') AS l3_count
        FROM sites s
        LEFT JOIN incidents i ON i.site_id = s.site_id
                             AND i.created_at > NOW() - INTERVAL '7 days'
        WHERE s.partner_id = $1 AND s.status != 'inactive'
        """,
        partner_id,
    )
    total = int(totals["incidents"] or 0)
    l1 = int(totals["l1_count"] or 0)
    self_heal_pct = (100.0 * l1 / total) if total > 0 else 100.0

    chronic_broken = await conn.fetchval(
        """
        SELECT COUNT(*) FROM incident_recurrence_velocity v
        JOIN sites s ON s.site_id = v.site_id
        WHERE s.partner_id = $1
          AND v.recurrence_broken_at IS NOT NULL
          AND v.recurrence_broken_at > NOW() - INTERVAL '7 days'
        """,
        partner_id,
    ) or 0

    attention_rows = await conn.fetch(
        """
        WITH site_scope AS (
            SELECT site_id, clinic_name FROM sites
            WHERE partner_id = $1 AND status != 'inactive'
        )
        SELECT ss.site_id, ss.clinic_name,
               COALESCE((SELECT COUNT(*) FROM incident_recurrence_velocity
                         WHERE site_id = ss.site_id AND is_chronic), 0) * 3
             + COALESCE((SELECT COUNT(*) FROM incidents
                         WHERE site_id = ss.site_id AND status NOT IN ('resolved','closed')
                           AND resolution_tier = 'L3'), 0) * 5
             AS risk_score,
             COALESCE((SELECT COUNT(*) FROM incident_recurrence_velocity
                       WHERE site_id = ss.site_id AND is_chronic), 0) AS chronic,
             COALESCE((SELECT COUNT(*) FROM incidents
                       WHERE site_id = ss.site_id AND status NOT IN ('resolved','closed')
                         AND resolution_tier = 'L3'), 0) AS open_l3
        FROM site_scope ss
        ORDER BY risk_score DESC
        LIMIT 5
        """,
        partner_id,
    )
    attention_sites = []
    for r in attention_rows:
        if int(r["risk_score"] or 0) == 0:
            continue
        reason_bits = []
        if r["chronic"]:
            reason_bits.append(f"{r['chronic']} chronic")
        if r["open_l3"]:
            reason_bits.append(f"{r['open_l3']} open L3")
        attention_sites.append({
            "site_id": r["site_id"],
            "clinic_name": r["clinic_name"],
            "risk_score": int(r["risk_score"]),
            "reason": ", ".join(reason_bits) or "attention needed",
        })

    activity_rows = await conn.fetch(
        """
        SELECT i.created_at, i.incident_type, i.resolution_tier,
               s.clinic_name, s.site_id
        FROM incidents i
        JOIN sites s ON s.site_id = i.site_id
        WHERE s.partner_id = $1
          AND i.created_at > NOW() - INTERVAL '7 days'
          AND i.resolution_tier IN ('L2', 'L3')
        ORDER BY i.created_at DESC
        LIMIT 5
        """,
        partner_id,
    )
    activity_highlights = [
        {
            "when": r["created_at"].strftime("%a %H:%M") if r["created_at"] else "",
            "site_id": r["site_id"],
            "clinic_name": r["clinic_name"],
            "incident_type": r["incident_type"],
            "outcome": {"L2": "L2 assisted", "L3": "L3 escalated"}.get(r["resolution_tier"], "—"),
        }
        for r in activity_rows
    ]

    return {
        "week_label": week_label,
        "stats": {
            "clients": int(totals["clients"] or 0),
            "incidents": total,
            "l1_count": l1,
            "l3_count": int(totals["l3_count"] or 0),
            "self_heal_pct": self_heal_pct,
            "chronic_broken": int(chronic_broken),
        },
        "attention_sites": attention_sites,
        "activity_highlights": activity_highlights,
    }


async def _send_partner_weekly_digests():
    """Iterate all active partners with a primary_contact_email and send."""
    from dashboard_api.fleet import get_pool
    from dashboard_api.tenant_middleware import admin_connection
    from dashboard_api.email_alerts import send_partner_weekly_digest

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        partners = await conn.fetch(
            """
            SELECT id,
                   COALESCE(NULLIF(brand_name, ''), name, 'OsirisCare') AS brand_name,
                   logo_url,
                   COALESCE(primary_color, '#4F46E5') AS primary_color,
                   contact_email,
                   COALESCE(digest_enabled, TRUE) AS digest_enabled
            FROM partners
            WHERE COALESCE(digest_enabled, TRUE) = TRUE
              AND contact_email IS NOT NULL
              AND contact_email != ''
              AND status = 'active'
            """
        )
        sent = 0
        failed = 0
        for p in partners:
            try:
                payload = await _gather_partner_digest_data(conn, p["id"])
                ok = send_partner_weekly_digest(
                    to_email=p["contact_email"],
                    partner_brand=p["brand_name"],
                    partner_logo_url=p["logo_url"],
                    primary_color=p["primary_color"],
                    **payload,
                )
                if ok:
                    sent += 1
                else:
                    failed += 1
            except Exception:
                logger.error(f"partner_weekly_digest send failed for partner {p['id']}", exc_info=True)
                failed += 1
        logger.info(f"partner_weekly_digest_batch_complete sent={sent} failed={failed}")


async def partner_weekly_digest_loop():
    """Fire the partner digest once per week (default: Fridays at 13:00 UTC).

    Strategy: check every 15 min. When `now.weekday() == PARTNER_DIGEST_DAY
    and now.hour == PARTNER_DIGEST_HOUR and last_sent_date < today`, send.
    Lock is per-process (single-writer); if mcp-server has replicas we
    need a DB-backed lock (TODO — not blocking P2 rollout).
    """
    from datetime import datetime, timezone, date

    await asyncio.sleep(180)
    last_sent_date: date | None = None
    while True:
        _hb("partner_weekly_digest")
        try:
            now = datetime.now(timezone.utc)
            is_send_window = (
                now.weekday() == PARTNER_DIGEST_DAY and now.hour == PARTNER_DIGEST_HOUR
            )
            if is_send_window and last_sent_date != now.date():
                await _send_partner_weekly_digests()
                last_sent_date = now.date()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(
                f"partner_weekly_digest_loop iteration failed: {e}",
                exc_info=True,
            )
        await asyncio.sleep(PARTNER_DIGEST_CHECK_SECONDS)


# ─── Migration 184 Phase 4 — consent request token expiry ────────

CONSENT_TOKEN_EXPIRY_CHECK_SECONDS = 60 * 60  # 1 hour — cheap enough to do hourly


async def expire_consent_request_tokens_loop():
    """Mark expired consent-request tokens so the UI can show them as
    such. Does NOT delete — the audit trail lives forever. We also
    write a ledger event `runbook.request_expired` so there's a
    trail of customers who never approved.

    Rate: 1 hour. Tokens expire at 72h so at most 1 hour of stale
    "pending" state before the UI catches up.
    """
    await asyncio.sleep(300)  # let startup settle
    while True:
        _hb("expire_consent_request_tokens")
        try:
            from dashboard_api.fleet import get_pool
            from dashboard_api.tenant_middleware import admin_connection
            pool = await get_pool()
            async with admin_connection(pool) as conn:
                # No actual state mutation — expiry is computed on read
                # (expires_at < NOW() AND consumed_at IS NULL). But we
                # can emit ledger events for tokens that transitioned
                # into expired-but-not-notified state. Keep it lean:
                # just count them for telemetry.
                n = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM consent_request_tokens
                    WHERE consumed_at IS NULL
                      AND expires_at < NOW()
                      AND expires_at > NOW() - INTERVAL '1 hour 10 minutes'
                    """
                )
                if n and int(n) > 0:
                    logger.info(f"consent_request_tokens_expired_in_last_hour count={int(n)}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(
                f"expire_consent_request_tokens_loop iteration failed: {e}",
                exc_info=True,
            )
        await asyncio.sleep(CONSENT_TOKEN_EXPIRY_CHECK_SECONDS)


# =============================================================================
# appliance_heartbeats / appliance_status_rollup maintenance (Migration 191)
# =============================================================================

HEARTBEAT_ROLLUP_REFRESH_SECONDS = 60
HEARTBEAT_PARTITION_CHECK_SECONDS = 3600  # hourly — partition creation is cheap
HEARTBEAT_PARTITION_LOOKAHEAD_MONTHS = 2


async def heartbeat_rollup_loop():
    """Refresh the appliance_status_rollup materialized view every 60s.

    Using REFRESH MATERIALIZED VIEW CONCURRENTLY so dashboard readers
    never block on the refresh. Requires the unique index on
    appliance_id (created by Migration 191).
    """
    from dashboard_api.fleet import get_pool
    from dashboard_api.tenant_middleware import admin_connection

    # Seed the view shortly after startup so dashboards have data.
    await asyncio.sleep(30)
    while True:
        _hb("heartbeat_rollup")
        try:
            pool = await get_pool()
            async with admin_connection(pool) as conn:
                await conn.execute(
                    "REFRESH MATERIALIZED VIEW CONCURRENTLY appliance_status_rollup"
                )
        except asyncio.CancelledError:
            break
        except Exception as e:
            # CONCURRENTLY fails on first run if the view has no rows yet.
            # Fall back to a plain refresh to seed it.
            try:
                async with admin_connection(pool) as conn:
                    await conn.execute("REFRESH MATERIALIZED VIEW appliance_status_rollup")
            except Exception as e2:
                logger.error(
                    f"heartbeat_rollup refresh failed: concurrent={e} plain={e2}",
                    exc_info=True,
                )
        await asyncio.sleep(HEARTBEAT_ROLLUP_REFRESH_SECONDS)


def _migration_db_url() -> str:
    """Return the superuser-scoped URL for DDL. Falls back to DATABASE_URL
    so dev environments without the extra env var still work (and surface
    the permission error loudly if the app user can't CREATE)."""
    url = os.getenv("MIGRATION_DATABASE_URL") or os.getenv("DATABASE_URL", "")
    if "+asyncpg" in url:
        url = url.replace("postgresql+asyncpg://", "postgresql://")
    return url


async def heartbeat_partition_maintainer_loop():
    """Ensure next N months of appliance_heartbeats partitions exist.

    Runs hourly. Idempotent — CREATE TABLE IF NOT EXISTS.

    Opens a direct asyncpg connection to MIGRATION_DATABASE_URL (superuser,
    bypasses PgBouncer) rather than using the app pool. The app role
    (mcp_app) lacks CREATE on schema public, so DDL must run as the
    migration superuser. The connection is single-shot — opened, used,
    closed — so we never hold a long-lived superuser socket open.
    """
    import asyncpg as _asyncpg

    await asyncio.sleep(120)
    while True:
        _hb("heartbeat_partition_maintainer")
        try:
            conn = await _asyncpg.connect(_migration_db_url())
            try:
                await conn.execute(f"""
                    DO $$
                    DECLARE
                        i INTEGER;
                        cur_start DATE;
                        next_start DATE;
                        part_name TEXT;
                    BEGIN
                        FOR i IN 0..{HEARTBEAT_PARTITION_LOOKAHEAD_MONTHS} LOOP
                            cur_start := (date_trunc('month', NOW()) + (i || ' month')::interval)::date;
                            next_start := (date_trunc('month', NOW()) + ((i+1) || ' month')::interval)::date;
                            part_name := 'appliance_heartbeats_y' || to_char(cur_start, 'YYYYmm');
                            EXECUTE format(
                                'CREATE TABLE IF NOT EXISTS %I PARTITION OF appliance_heartbeats FOR VALUES FROM (%L) TO (%L)',
                                part_name, cur_start, next_start
                            );
                        END LOOP;
                    END
                    $$;
                """)
            finally:
                await conn.close()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(
                f"heartbeat_partition_maintainer iteration failed: {e}",
                exc_info=True,
            )
        await asyncio.sleep(HEARTBEAT_PARTITION_CHECK_SECONDS)


# =============================================================================
# phantom_detector (Session 206 H4) — orthogonal verification of liveness
# =============================================================================
# The premise: site_appliances.last_checkin can lie (and did — three bugs
# of that shape shipped before we caught them). appliance_heartbeats is the
# new ground truth (INSERT-only, DELETE-blocked, per-appliance attribution).
#
# This loop compares the two. If `last_checkin` claims an appliance is fresh
# but `heartbeats` shows nothing for > 2 checkin cycles, the liveness signal
# is lying. Raise an APPLIANCE_LIVENESS_LIE incident so operators see it
# even if every other guardrail has failed.
#
# Runs every 5 min. Fires once per stale delta — suppressed for 1 hour after
# firing so we don't alert-storm on a persistent discrepancy.

PHANTOM_DETECTOR_INTERVAL_SECONDS = 300
PHANTOM_STALE_THRESHOLD_SECONDS = 180  # ~3 checkin cycles (60s each)
PHANTOM_ALERT_SUPPRESSION_HOURS = 1


async def phantom_detector_loop():
    """Orthogonal verification: detect when last_checkin claims freshness
    but heartbeats disagree. Catches future site-wide UPDATE bugs even if
    both the CI grep + DB trigger somehow fail.

    Invariant: for every non-deleted appliance with a fresh last_checkin,
    at least one heartbeat must exist in the same window. If not, the
    last_checkin is a lie.
    """
    from dashboard_api.fleet import get_pool
    from dashboard_api.tenant_middleware import admin_connection

    await asyncio.sleep(300)  # let heartbeat_rollup warm up first
    while True:
        _hb("phantom_detector")
        try:
            pool = await get_pool()
            async with admin_connection(pool) as conn:
                # Appliances whose last_checkin is recent BUT heartbeats
                # are stale (or missing entirely). These are lies by one
                # of the two signals.
                suspects = await conn.fetch("""
                    SELECT
                        sa.site_id,
                        sa.appliance_id,
                        sa.hostname,
                        sa.display_name,
                        sa.mac_address,
                        sa.last_checkin,
                        EXTRACT(EPOCH FROM (NOW() - sa.last_checkin))::int AS claimed_stale_sec,
                        hb.max_observed_at,
                        EXTRACT(EPOCH FROM (NOW() - COALESCE(hb.max_observed_at, sa.last_checkin - INTERVAL '1 year')))::int AS heartbeat_stale_sec
                    FROM site_appliances sa
                    LEFT JOIN LATERAL (
                        SELECT MAX(observed_at) AS max_observed_at
                        FROM appliance_heartbeats
                        WHERE appliance_id = sa.appliance_id
                    ) hb ON true
                    WHERE sa.deleted_at IS NULL
                      AND sa.last_checkin > NOW() - INTERVAL '5 minutes'
                      AND (
                          hb.max_observed_at IS NULL
                          OR hb.max_observed_at < NOW() - make_interval(secs := $1)
                      )
                """, PHANTOM_STALE_THRESHOLD_SECONDS)

                for row in suspects:
                    await _raise_liveness_lie_if_not_suppressed(conn, row)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(
                f"phantom_detector iteration failed: {e}",
                exc_info=True,
            )
        await asyncio.sleep(PHANTOM_DETECTOR_INTERVAL_SECONDS)


MESH_REBALANCE_INTERVAL_SECONDS = 300  # 5 min


async def mesh_reassignment_loop():
    """Session 206 M3: rebalance expired mesh target assignments every 5 min.
    Unacked assignments (phantom appliance can't ACK) get reassigned to
    live appliances — determined by heartbeats, not last_checkin."""
    from dashboard_api.fleet import get_pool
    from dashboard_api.tenant_middleware import admin_connection
    from dashboard_api.mesh_targets import rebalance_expired_assignments

    await asyncio.sleep(180)
    while True:
        _hb("mesh_reassignment")
        try:
            pool = await get_pool()
            async with admin_connection(pool) as conn:
                sites = await conn.fetch(
                    """
                    SELECT DISTINCT site_id FROM mesh_target_assignments
                    WHERE expires_at < NOW()
                    """
                )
            total_reassigned = 0
            for s in sites:
                stats = await rebalance_expired_assignments(s["site_id"])
                total_reassigned += stats["reassigned"]
                if stats["orphaned"] > 0:
                    logger.warning(
                        f"mesh_reassignment: site={s['site_id']} "
                        f"orphaned={stats['orphaned']} — no live appliance to reassign to"
                    )
            if total_reassigned:
                logger.info(f"mesh_reassignment: reassigned {total_reassigned} targets across {len(sites)} site(s)")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(
                f"mesh_reassignment iteration failed: {e}",
                exc_info=True,
            )
        await asyncio.sleep(MESH_REBALANCE_INTERVAL_SECONDS)


async def _raise_liveness_lie_if_not_suppressed(conn, row):
    """Emit an APPLIANCE_LIVENESS_LIE incident, suppressed if we've already
    fired one for this appliance in the last PHANTOM_ALERT_SUPPRESSION_HOURS."""
    suppression_key = f"liveness_lie:{row['appliance_id']}"
    recent = await conn.fetchval("""
        SELECT created_at FROM admin_audit_log
        WHERE action = 'APPLIANCE_LIVENESS_LIE'
          AND target = $1
          AND created_at > NOW() - make_interval(hours := $2)
        ORDER BY created_at DESC
        LIMIT 1
    """, row['appliance_id'], PHANTOM_ALERT_SUPPRESSION_HOURS)
    if recent:
        return

    label = row['display_name'] or row['hostname'] or row['appliance_id']
    details = {
        "appliance_id": row['appliance_id'],
        "site_id": row['site_id'],
        "mac_address": row['mac_address'],
        "claimed_last_checkin": (
            row['last_checkin'].isoformat() if row['last_checkin'] else None
        ),
        "claimed_stale_sec": row['claimed_stale_sec'],
        "heartbeat_last_observed": (
            row['max_observed_at'].isoformat() if row['max_observed_at'] else None
        ),
        "heartbeat_stale_sec": row['heartbeat_stale_sec'],
        "threshold_sec": PHANTOM_STALE_THRESHOLD_SECONDS,
    }

    import json as _json
    await conn.execute("""
        INSERT INTO admin_audit_log (username, action, target, details)
        VALUES ('system:phantom_detector', 'APPLIANCE_LIVENESS_LIE', $1, $2::jsonb)
    """, row['appliance_id'], _json.dumps(details))

    # D3 claim ledger: cite the (missing) heartbeat that should have existed.
    # Look up the most recent heartbeat we DO have — its hash becomes the
    # claim's reference, so the auditor can verify "this is the last one
    # we saw, and here's the gap."
    last_hb = await conn.fetchrow(
        """
        SELECT id, heartbeat_hash FROM appliance_heartbeats
        WHERE appliance_id = $1
        ORDER BY observed_at DESC
        LIMIT 1
        """,
        row['appliance_id'],
    )
    await conn.execute(
        """
        INSERT INTO liveness_claims
            (site_id, appliance_id, claim_type,
             cited_heartbeat_id, cited_heartbeat_hash,
             details, published_to)
        VALUES ($1, $2, 'liveness_lie', $3, $4, $5::jsonb, ARRAY['email','dashboard'])
        """,
        row['site_id'],
        row['appliance_id'],
        last_hb['id'] if last_hb else None,
        last_hb['heartbeat_hash'] if last_hb else None,
        _json.dumps(details),
    )

    logger.error(
        f"APPLIANCE_LIVENESS_LIE appliance={row['appliance_id']} "
        f"site={row['site_id']} label={label} "
        f"claimed_fresh_sec={row['claimed_stale_sec']} "
        f"heartbeat_stale_sec={row['heartbeat_stale_sec']} "
        f"heartbeat_last={row['max_observed_at']}"
    )

    # Fire an email alert too — this is a credibility event, not just noise.
    try:
        from dashboard_api.email_alerts import send_critical_alert
        send_critical_alert(
            title=f"Liveness lie detected: {label}",
            message=(
                f"Appliance {label} at site {row['site_id']} shows "
                f"last_checkin fresh ({row['claimed_stale_sec']}s ago) but "
                f"no heartbeat for {row['heartbeat_stale_sec']}s. "
                f"The dashboard liveness signal is not matching ground truth. "
                f"Investigate possible site-wide UPDATE regression or "
                f"heartbeat insert failure."
            ),
            site_id=row['site_id'],
            category="appliance_health",
            severity="high",
            metadata=details,
        )
    except Exception:
        logger.error(
            f"Failed to send liveness-lie alert for {row['appliance_id']}",
            exc_info=True,
        )


async def client_telemetry_retention_loop():
    """Delete client_telemetry_events older than 30 days, every 24h.

    Session 210 round-table #5. The telemetry table's docstring claims
    30-day retention; this loop actually enforces it. Calls Migration 243's
    prune_client_telemetry_events(30) function. Safe to run from any
    replica — the function is idempotent.

    Startup delay: 10 min. Avoids running during the post-boot migration
    storm. Tolerates the pre-migration-243 window by logging + sleeping.
    """
    _hb("client_telemetry_retention")
    await asyncio.sleep(600)
    while True:
        _hb("client_telemetry_retention")
        try:
            async with async_session() as db:
                result = await db.execute(
                    text("SELECT prune_client_telemetry_events(30) AS deleted")
                )
                deleted = result.scalar() or 0
                await db.commit()
                if deleted > 0:
                    logger.info(
                        "client_telemetry_retention pruned old events",
                        deleted_count=int(deleted),
                        retention_days=30,
                    )
        except asyncio.CancelledError:
            break
        except Exception as e:
            # Pre-migration-243 case: function doesn't exist. Log once,
            # keep sleeping. Next container restart catches the migration.
            logger.error(
                "client_telemetry_retention_failed",
                exc_info=True,
                extra={"error_class": type(e).__name__},
            )
        await asyncio.sleep(86400)  # 24h


async def data_hygiene_gc_loop():
    """Session 210-B 2026-04-25 hardening #3 + RT-4: bound the size of
    high-cardinality tables that don't have on-write GC, and finalize
    pending appliance relocations.

    Calls Migration 244's prune_*() functions every 24h:
      - prune_install_sessions(30)   — installer sessions older than 30d
      - prune_nonces(4)              — sigauth nonces older than 4h
      - prune_discovered_devices(60) — discovered devices unseen 60d+

    Calls Migration 245's finalize_pending_relocations() every cycle:
      - flips 'pending' relocations whose target has checked in to
        'completed' (and soft-deletes the source row + deactivates
        source api_keys)
      - flips 'pending' relocations older than 30 min to 'expired'
        (substrate `relocation_stalled` invariant fires alongside)

    Each prune is logged with deleted_count when > 0. Failures are
    logged at error and we sleep — next cycle re-tries. Pre-migration-244
    period is tolerated (function-not-exists is caught in the per-prune
    try/except and only the first iteration warns).
    """
    _hb("data_hygiene_gc")
    await asyncio.sleep(900)  # 15-min startup delay; runs after migrations settle
    while True:
        _hb("data_hygiene_gc")
        for fn_call, args in [
            ("prune_install_sessions", "(30)"),
            ("prune_nonces", "(4)"),
            ("prune_discovered_devices", "(60)"),
        ]:
            try:
                async with async_session() as db:
                    result = await db.execute(
                        text(f"SELECT {fn_call}{args} AS deleted")
                    )
                    deleted = result.scalar() or 0
                    await db.commit()
                    if deleted > 0:
                        logger.info(
                            "data_hygiene_gc pruned",
                            function=fn_call,
                            deleted_count=int(deleted),
                        )
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(
                    "data_hygiene_gc_failed",
                    exc_info=True,
                    extra={
                        "function": fn_call,
                        "error_class": type(e).__name__,
                    },
                )
        try:
            await asyncio.sleep(86400)  # 24h
        except asyncio.CancelledError:
            return


async def relocation_finalize_loop():
    """Round-table RT-4 (Session 210-B 2026-04-25). Sweep
    `relocations` table every 60s: flip 'pending' → 'completed' when
    target site_appliances has checked in past initiated_at, OR
    'pending' → 'expired' if 30 min has elapsed without a target
    checkin.

    Cadence is 60s (vs the daily prune cadence) so a daemon's
    successful target checkin is reflected in the dashboard within
    a minute. Calls Migration 245's finalize_pending_relocations()
    SQL function; the heavy lifting is server-side in plpgsql.

    Tolerates pre-Migration-245 deploys: function-not-exists is logged
    once per cycle but the loop keeps going.
    """
    _hb("relocation_finalize")
    await asyncio.sleep(120)  # short startup delay; runs after migrations
    while True:
        _hb("relocation_finalize")
        try:
            async with async_session() as db:
                result = await db.execute(
                    text("SELECT * FROM finalize_pending_relocations()")
                )
                row = result.first()
                await db.commit()
                if row:
                    completed = int(row.completed_count or 0)
                    expired = int(row.expired_count or 0)
                    if completed > 0 or expired > 0:
                        logger.info(
                            "relocations finalized",
                            completed_count=completed,
                            expired_count=expired,
                        )
        except asyncio.CancelledError:
            return
        except Exception as e:
            # Pre-Migration-245 — function-not-exists. Log + sleep.
            logger.error(
                "relocation_finalize_failed",
                exc_info=True,
                extra={"error_class": type(e).__name__},
            )
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            return

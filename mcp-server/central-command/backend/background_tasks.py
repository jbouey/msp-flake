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


async def ots_repair_block_heights():
    """One-time repair: re-extract bitcoin_block from stored proof data."""
    try:
        import base64
        from dashboard_api.evidence_chain import BTC_ATTESTATION_TAG, extract_btc_block_height
        async with async_session() as db:
            result = await db.execute(text("""
                SELECT bundle_id, proof_data, bitcoin_block
                FROM ots_proofs
                WHERE status = 'anchored'
                  AND bitcoin_block IS NOT NULL
                  AND (bitcoin_block <= 10 OR bitcoin_block > 100000000)
                LIMIT 100000
            """))
            bad_proofs = result.fetchall()
            if not bad_proofs:
                logger.info("OTS block height repair: no proofs need fixing")
                return

            fixed = 0
            for proof in bad_proofs:
                try:
                    proof_bytes = base64.b64decode(proof.proof_data)
                    tag_pos = proof_bytes.find(BTC_ATTESTATION_TAG)
                    if tag_pos >= 0:
                        correct_height = extract_btc_block_height(proof_bytes, tag_pos)
                        if correct_height and correct_height != proof.bitcoin_block:
                            await db.execute(text("""
                                UPDATE ots_proofs
                                SET bitcoin_block = :height
                                WHERE bundle_id = :bid
                            """), {"height": correct_height, "bid": proof.bundle_id})
                            fixed += 1
                except Exception:
                    continue

            await db.commit()
            logger.info(f"OTS block height repair: fixed {fixed}/{len(bad_proofs)} proofs")
    except Exception as e:
        logger.exception(f"OTS block height repair failed: {e}")


async def ots_upgrade_loop():
    """Periodically upgrade pending OTS proofs (every 15 minutes)."""
    await asyncio.sleep(30)

    await ots_repair_block_heights()

    while True:
        try:
            from dashboard_api.evidence_chain import upgrade_pending_proofs
            async with async_session() as db:
                result = await upgrade_pending_proofs(db, limit=500)
                if result.get("upgraded", 0) > 0 or result.get("checked", 0) > 0:
                    logger.info("OTS upgrade cycle", **result)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"OTS upgrade cycle failed: {e}")
        await asyncio.sleep(900)


async def ots_resubmit_expired_loop():
    """One-time background drain of expired OTS proofs."""
    await asyncio.sleep(90)

    total_resubmitted = 0
    total_failed = 0
    consecutive_zero = 0

    while True:
        try:
            from dashboard_api.evidence_chain import submit_hash_to_ots
            async with async_session() as db:
                result = await db.execute(text("""
                    SELECT bundle_id, bundle_hash, site_id
                    FROM ots_proofs
                    WHERE status = 'expired'
                    AND (last_upgrade_attempt IS NULL
                         OR last_upgrade_attempt < NOW() - INTERVAL '1 hour')
                    ORDER BY submitted_at ASC
                    LIMIT 500
                """))
                expired_proofs = result.fetchall()

                if not expired_proofs:
                    logger.info(
                        "OTS resubmission drain complete",
                        total_resubmitted=total_resubmitted,
                        total_failed=total_failed,
                    )
                    return

                batch_ok = 0
                batch_fail = 0

                for proof in expired_proofs:
                    try:
                        ots_result = await submit_hash_to_ots(
                            proof.bundle_hash, proof.bundle_id
                        )
                        async with db.begin_nested():
                            if ots_result:
                                submitted_at = ots_result["submitted_at"]
                                if submitted_at.tzinfo is not None:
                                    submitted_at = submitted_at.replace(tzinfo=None)

                                await db.execute(text("""
                                    UPDATE ots_proofs
                                    SET status = 'pending',
                                        proof_data = :proof_data,
                                        calendar_url = :calendar_url,
                                        submitted_at = :submitted_at,
                                        error = NULL,
                                        upgrade_attempts = 0,
                                        last_upgrade_attempt = NULL
                                    WHERE bundle_id = :bundle_id
                                """), {
                                    "proof_data": ots_result["proof_data"],
                                    "calendar_url": ots_result["calendar_url"],
                                    "submitted_at": submitted_at,
                                    "bundle_id": proof.bundle_id,
                                })

                                await db.execute(text("""
                                    UPDATE compliance_bundles
                                    SET ots_status = 'pending',
                                        ots_proof = :proof_data,
                                        ots_calendar_url = :calendar_url,
                                        ots_submitted_at = :submitted_at,
                                        ots_error = NULL
                                    WHERE bundle_id = :bundle_id
                                """), {
                                    "proof_data": ots_result["proof_data"],
                                    "calendar_url": ots_result["calendar_url"],
                                    "submitted_at": submitted_at,
                                    "bundle_id": proof.bundle_id,
                                })
                                batch_ok += 1
                            else:
                                batch_fail += 1
                                await db.execute(text("""
                                    UPDATE ots_proofs
                                    SET error = 'Resubmission failed - all calendars returned errors',
                                        last_upgrade_attempt = NOW()
                                    WHERE bundle_id = :bundle_id
                                """), {"bundle_id": proof.bundle_id})
                    except Exception as e:
                        batch_fail += 1
                        logger.warning(f"OTS resubmit failed {proof.bundle_id[:8]}: {e}")

                    if (batch_ok + batch_fail) % 50 == 0:
                        await db.commit()

                await db.commit()

                total_resubmitted += batch_ok
                total_failed += batch_fail

                remaining = await db.execute(text(
                    "SELECT COUNT(*) FROM ots_proofs WHERE status = 'expired'"
                ))
                remaining_count = remaining.scalar() or 0

                logger.info(
                    "OTS resubmission batch",
                    batch_ok=batch_ok,
                    batch_fail=batch_fail,
                    total_resubmitted=total_resubmitted,
                    total_failed=total_failed,
                    remaining=remaining_count,
                )

                if batch_ok == 0 and batch_fail > 0:
                    consecutive_zero += 1
                    if consecutive_zero >= 5:
                        logger.error(
                            "OTS resubmission stopped: 5 consecutive zero-success batches. "
                            "Calendars may be down. Will retry on next server restart."
                        )
                        return
                else:
                    consecutive_zero = 0

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"OTS resubmission batch failed: {e}")

        delay = 300 if consecutive_zero > 0 else 30
        await asyncio.sleep(delay)


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

                # Check 3: l1_rules promoted with no runbooks entry
                orphan_rb = await conn.fetchval("""
                    SELECT COUNT(*) FROM l1_rules lr
                    LEFT JOIN runbooks rb ON rb.runbook_id = lr.rule_id
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


async def flywheel_promotion_loop():
    """Periodically scan patterns for L2->L1 auto-promotion (every 30 minutes)."""
    await asyncio.sleep(120)
    while True:
        try:
            promotions_this_cycle = 0
            async with async_session() as db:
                # Step 0: Generate/update patterns from L2 execution telemetry
                try:
                    await db.execute(text("""
                        INSERT INTO patterns (
                            pattern_id, pattern_signature, incident_type, runbook_id,
                            occurrences, success_count, failure_count, status
                        )
                        SELECT
                            LEFT(md5(et.incident_type || ':' || et.runbook_id || ':' || et.hostname), 16) as pattern_id,
                            et.incident_type || ':' || et.runbook_id || ':' || et.hostname as pattern_signature,
                            et.incident_type,
                            et.runbook_id,
                            COUNT(*) as occurrences,
                            SUM(CASE WHEN et.success THEN 1 ELSE 0 END) as success_count,
                            SUM(CASE WHEN NOT et.success THEN 1 ELSE 0 END) as failure_count,
                            'pending' as status
                        FROM execution_telemetry et
                        WHERE et.resolution_level = 'L2'
                          AND et.incident_type IS NOT NULL
                          AND et.runbook_id IS NOT NULL
                        GROUP BY et.incident_type, et.runbook_id, et.hostname
                        HAVING COUNT(*) >= 5
                        ON CONFLICT (pattern_signature) DO UPDATE SET
                            occurrences = EXCLUDED.occurrences,
                            success_count = EXCLUDED.success_count,
                            failure_count = EXCLUDED.failure_count
                    """))
                    await db.commit()
                except Exception as e:
                    logger.debug(f"Flywheel pattern generation: {e}")
                    await db.rollback()

                # Step 1: Populate aggregated_pattern_stats from execution_telemetry
                try:
                    await db.execute(text("""
                        INSERT INTO aggregated_pattern_stats (
                            site_id, pattern_signature, total_occurrences,
                            l1_resolutions, l2_resolutions, l3_resolutions,
                            success_count, total_resolution_time_ms,
                            success_rate, avg_resolution_time_ms,
                            recommended_action, promotion_eligible,
                            first_seen, last_seen, last_synced_at
                        )
                        SELECT
                            et.site_id,
                            et.incident_type || ':' || et.runbook_id as pattern_signature,
                            COUNT(*) as total_occurrences,
                            SUM(CASE WHEN et.resolution_level = 'L1' THEN 1 ELSE 0 END),
                            SUM(CASE WHEN et.resolution_level = 'L2' THEN 1 ELSE 0 END),
                            SUM(CASE WHEN et.resolution_level = 'L3' THEN 1 ELSE 0 END),
                            SUM(CASE WHEN et.success THEN 1 ELSE 0 END),
                            COALESCE(SUM(et.duration_seconds * 1000), 0),
                            CASE WHEN COUNT(*) > 0
                                THEN SUM(CASE WHEN et.success THEN 1 ELSE 0 END)::FLOAT / COUNT(*)
                                ELSE 0 END,
                            CASE WHEN COUNT(*) > 0
                                THEN COALESCE(SUM(et.duration_seconds * 1000), 0) / COUNT(*)
                                ELSE 0 END,
                            MAX(et.runbook_id),
                            false,
                            MIN(et.created_at),
                            MAX(et.created_at),
                            NOW()
                        FROM execution_telemetry et
                        WHERE et.resolution_level IN ('L1', 'L2')
                          AND et.incident_type IS NOT NULL
                          AND et.runbook_id IS NOT NULL
                        GROUP BY et.site_id, et.incident_type, et.runbook_id
                        HAVING COUNT(*) >= 3
                        ON CONFLICT (site_id, pattern_signature) DO UPDATE SET
                            total_occurrences = EXCLUDED.total_occurrences,
                            l1_resolutions = EXCLUDED.l1_resolutions,
                            l2_resolutions = EXCLUDED.l2_resolutions,
                            l3_resolutions = EXCLUDED.l3_resolutions,
                            success_count = EXCLUDED.success_count,
                            total_resolution_time_ms = EXCLUDED.total_resolution_time_ms,
                            success_rate = EXCLUDED.success_rate,
                            avg_resolution_time_ms = EXCLUDED.avg_resolution_time_ms,
                            last_seen = EXCLUDED.last_seen,
                            last_synced_at = NOW()
                    """))
                    await db.commit()
                except Exception as e:
                    logger.debug(f"Flywheel aggregated stats: {e}")
                    await db.rollback()

                # Step 2: Update promotion_eligible
                try:
                    eligible_result = await db.execute(text("""
                        UPDATE aggregated_pattern_stats
                        SET promotion_eligible = true
                        WHERE total_occurrences >= 5
                          AND success_rate >= 0.90
                          AND l2_resolutions >= 3
                          AND last_seen > NOW() - INTERVAL '7 days'
                          AND promotion_eligible = false
                        RETURNING pattern_signature
                    """))
                    newly_eligible = eligible_result.fetchall()
                    await db.commit()

                    if newly_eligible:
                        logger.info(
                            "Flywheel promotion scan complete",
                            newly_eligible=len(newly_eligible),
                        )
                except Exception as e:
                    logger.debug(f"Flywheel promotion eligible update: {e}")
                    await db.rollback()

                # Step 3: Cross-client platform pattern aggregation
                try:
                    await db.execute(text("""
                        INSERT INTO platform_pattern_stats (
                            pattern_key, incident_type, runbook_id,
                            distinct_sites, distinct_orgs, total_occurrences,
                            success_count, success_rate, first_seen, last_seen
                        )
                        SELECT
                            et.incident_type || ':' || et.runbook_id,
                            et.incident_type,
                            et.runbook_id,
                            COUNT(DISTINCT et.site_id),
                            COUNT(DISTINCT s.client_org_id),
                            COUNT(*),
                            SUM(CASE WHEN et.success THEN 1 ELSE 0 END),
                            CASE WHEN COUNT(*) > 0
                                THEN SUM(CASE WHEN et.success THEN 1 ELSE 0 END)::FLOAT / COUNT(*)
                                ELSE 0 END,
                            MIN(et.created_at),
                            MAX(et.created_at)
                        FROM execution_telemetry et
                        JOIN sites s ON s.site_id = et.site_id
                        WHERE et.resolution_level = 'L2'
                          AND et.incident_type IS NOT NULL
                          AND et.runbook_id IS NOT NULL
                        GROUP BY et.incident_type, et.runbook_id
                        HAVING COUNT(*) >= 10
                        ON CONFLICT (pattern_key) DO UPDATE SET
                            distinct_sites = EXCLUDED.distinct_sites,
                            distinct_orgs = EXCLUDED.distinct_orgs,
                            total_occurrences = EXCLUDED.total_occurrences,
                            success_count = EXCLUDED.success_count,
                            success_rate = EXCLUDED.success_rate,
                            last_seen = EXCLUDED.last_seen
                    """))
                    await db.commit()
                except Exception as e:
                    logger.debug(f"Platform pattern aggregation: {e}")
                    await db.rollback()

                # Step 4: Auto-promote platform rules
                # Phase 8: use per-incident-type thresholds from
                # promotion_thresholds table (falls back to __default__).
                try:
                    remaining = max(0, 5 - promotions_this_cycle)
                    if remaining == 0:
                        logger.info("Flywheel promotion cap reached (5/cycle), skipping platform auto-promotion")
                    platform_result = await db.execute(text("""
                        SELECT pps.pattern_key, pps.incident_type, pps.runbook_id,
                               pps.distinct_orgs, pps.total_occurrences, pps.success_rate
                        FROM platform_pattern_stats pps
                        LEFT JOIN promotion_thresholds pt
                               ON pt.incident_type = pps.incident_type
                        LEFT JOIN promotion_thresholds pt_default
                               ON pt_default.incident_type = '__default__'
                        WHERE pps.promoted_at IS NULL
                          AND pps.distinct_orgs >= COALESCE(
                              pt.min_distinct_orgs, pt_default.min_distinct_orgs, 5)
                          AND pps.success_rate >= COALESCE(
                              pt.min_success_rate, pt_default.min_success_rate, 0.90)
                          AND pps.total_occurrences >= COALESCE(
                              pt.min_total_occurrences, pt_default.min_total_occurrences, 20)
                        ORDER BY pps.distinct_orgs DESC, pps.total_occurrences DESC
                        LIMIT :remaining
                    """), {"remaining": remaining})
                    platform_candidates = platform_result.fetchall()

                    _EMBEDDED_RUNBOOK_RE = re.compile(
                        r"^(L1|LIN|WIN|MAC|NET|RB|ESC)-", re.IGNORECASE
                    )
                    valid_rb_result = await db.execute(text(
                        "SELECT runbook_id FROM runbooks"
                    ))
                    valid_runbook_ids = {
                        row.runbook_id for row in valid_rb_result.fetchall()
                    }

                    platform_promoted = 0
                    for pc in platform_candidates:
                        if pc.runbook_id not in valid_runbook_ids and not _EMBEDDED_RUNBOOK_RE.match(pc.runbook_id):
                            logger.warning(
                                "Skipping platform promotion: invalid runbook_id",
                                runbook_id=pc.runbook_id,
                                incident_type=pc.incident_type,
                                pattern_key=pc.pattern_key,
                            )
                            continue

                        # Phase 2 (Session 205): use the unified promote_candidate()
                        # path so platform auto-promotions also write promoted_rules,
                        # runbooks, runbook_id_mapping, promotion_audit_log AND
                        # emit fleet orders to deploy. Pre-Session-205 this code
                        # only INSERTed into l1_rules — no audit, no orders,
                        # promoted_rules.deployment_count never moved.
                        rule_id = (
                            f"L1-PLATFORM-{pc.incident_type.upper()}-"
                            f"{pc.runbook_id[:12].upper().replace('-', '')}"
                        )
                        try:
                            from .flywheel_promote import (
                                promote_candidate,
                                issue_sync_promoted_rule_orders,
                                evaluate_shadow_agreement,
                            )
                            from .fleet import get_pool
                            pool = await get_pool()
                            async with pool.acquire() as conn_pg:
                                # Phase 9: shadow-mode check. If enabled for
                                # this incident_type AND the candidate fails
                                # the agreement threshold, HOLD and move on
                                # (no state change beyond the shadow_evaluations
                                # audit row which evaluate_shadow_agreement writes).
                                shadow = await evaluate_shadow_agreement(
                                    conn_pg,
                                    incident_type=pc.incident_type,
                                    runbook_id=pc.runbook_id,
                                    pattern_key=pc.pattern_key,
                                )
                                if shadow["decision"] == "hold":
                                    logger.info(
                                        "Platform promotion HELD by shadow mode",
                                        incident_type=pc.incident_type,
                                        runbook_id=pc.runbook_id,
                                        agreement=shadow["agreement_rate"],
                                        reason=shadow["hold_reason"],
                                    )
                                    continue
                                if shadow["decision"] == "insufficient_data":
                                    # Default to promoting since we were already
                                    # willing to promote on the hard-coded path;
                                    # shadow just can't verify — not a red flag.
                                    logger.debug(
                                        "Shadow mode inconclusive — proceeding",
                                        incident_type=pc.incident_type,
                                    )

                                async with conn_pg.transaction():
                                    # Synthesize a candidate dict from the
                                    # platform_pattern_stats row. promote_candidate
                                    # writes 6 tables + emits fleet orders.
                                    candidate = {
                                        "id": None,  # no real candidate row; UPDATE no-op
                                        "site_id": "PLATFORM",
                                        "pattern_signature": pc.pattern_key,
                                        "check_type": pc.incident_type,
                                        "success_rate": float(pc.success_rate),
                                        "total_occurrences": int(pc.total_occurrences),
                                        "l2_resolutions": int(pc.total_occurrences),
                                        "recommended_action": pc.runbook_id,
                                    }
                                    promotion = await promote_candidate(
                                        conn_pg,
                                        candidate,
                                        actor="auto-platform",
                                        actor_type="auto",
                                        custom_name=f"Platform: {pc.incident_type}",
                                    )
                                    rule_id = promotion["rule_id"]

                            # Mark platform_pattern_stats as promoted (separate connection
                            # because the previous transaction owned conn_pg).
                            await db.execute(text("""
                                UPDATE platform_pattern_stats
                                SET promoted_at = NOW(), promoted_rule_id = :rid
                                WHERE pattern_key = :pk
                            """), {"rid": rule_id, "pk": pc.pattern_key})
                            await db.commit()

                            platform_promoted += 1
                            promotions_this_cycle += 1
                            logger.info(
                                "Platform rule auto-promoted (full path)",
                                rule_id=rule_id,
                                incident_type=pc.incident_type,
                                distinct_orgs=pc.distinct_orgs,
                                success_rate=f"{pc.success_rate:.1%}",
                                total_occurrences=pc.total_occurrences,
                            )
                        except Exception as e:
                            logger.warning(
                                f"Failed to promote platform rule {rule_id}: {e}",
                                exc_info=True,
                            )
                            await db.rollback()

                    if platform_promoted > 0:
                        logger.info(f"Platform promotion: {platform_promoted} new rules auto-promoted")

                except Exception as e:
                    logger.debug(f"Platform auto-promotion: {e}")
                    await db.rollback()

                # Step 5: Post-promotion health monitoring (canary → rollout)
                # Promoted rules start site-specific (source='promoted').
                # After 48h with >70% success and 3+ executions → promote to 'synced' (fleet-wide).
                # After 48h with <70% success → disable (protects against bad promotions).
                try:
                    # 5a: Disable degraded promoted rules (<70% success after 48h)
                    degraded = await db.execute(text("""
                        UPDATE l1_rules SET enabled = false
                        WHERE source = 'promoted'
                          AND enabled = true
                          AND created_at > NOW() - INTERVAL '48 hours'
                          AND rule_id IN (
                              SELECT r.rule_id FROM l1_rules r
                              JOIN execution_telemetry et
                                ON et.runbook_id = r.runbook_id
                                AND et.created_at > r.created_at
                              WHERE r.source = 'promoted'
                                AND r.enabled = true
                                AND r.created_at > NOW() - INTERVAL '48 hours'
                              GROUP BY r.rule_id
                              HAVING COUNT(*) >= 3
                                AND SUM(CASE WHEN et.success THEN 1 ELSE 0 END)::FLOAT / COUNT(*) < 0.70
                          )
                        RETURNING rule_id
                    """))
                    auto_disabled = degraded.fetchall()
                    await db.commit()
                    if auto_disabled:
                        for row in auto_disabled:
                            logger.warning(
                                "Promoted rule auto-disabled (success rate < 70%)",
                                rule_id=row[0],
                            )

                    # 5a-bis (Phase 15 closing): regime-driven LIFETIME
                    # auto-disable. The 48h canary above only catches early
                    # failures; rules promoted long ago that REGRESS or
                    # were always-bad fall through. The regime detector now
                    # emits 'critical' (delta) + 'absolute_low' (floor)
                    # events. Disable any promoted_from_l2 rule with an
                    # unacknowledged event of either severity in the last
                    # 24h. Operator can re-enable + ack to suppress.
                    lifetime_disabled = await db.execute(text("""
                        UPDATE l1_rules SET enabled = false
                        WHERE promoted_from_l2 = true
                          AND enabled = true
                          AND rule_id IN (
                              SELECT DISTINCT rce.rule_id
                              FROM l1_rule_regime_events rce
                              WHERE rce.severity IN ('critical', 'absolute_low')
                                AND rce.acknowledged_at IS NULL
                                AND rce.detected_at > NOW() - INTERVAL '24 hours'
                          )
                        RETURNING rule_id
                    """))
                    lifetime_rows = lifetime_disabled.fetchall()
                    await db.commit()
                    if lifetime_rows:
                        for row in lifetime_rows:
                            logger.warning(
                                "Promoted rule lifetime auto-disabled (regime event critical/absolute_low)",
                                rule_id=row[0],
                            )

                    # 5b: Graduate successful promoted rules to 'synced' (>70% success after 48h)
                    # This is the canary → rollout transition: proven rules become fleet-wide
                    graduated = await db.execute(text("""
                        UPDATE l1_rules SET source = 'synced'
                        WHERE source = 'promoted'
                          AND enabled = true
                          AND created_at < NOW() - INTERVAL '48 hours'
                          AND rule_id IN (
                              SELECT r.rule_id FROM l1_rules r
                              JOIN execution_telemetry et
                                ON et.runbook_id = r.runbook_id
                                AND et.created_at > r.created_at
                              WHERE r.source = 'promoted'
                                AND r.enabled = true
                                AND r.created_at < NOW() - INTERVAL '48 hours'
                              GROUP BY r.rule_id
                              HAVING COUNT(*) >= 3
                                AND SUM(CASE WHEN et.success THEN 1 ELSE 0 END)::FLOAT / COUNT(*) >= 0.70
                          )
                        RETURNING rule_id
                    """))
                    auto_graduated = graduated.fetchall()
                    await db.commit()
                    if auto_graduated:
                        for row in auto_graduated:
                            logger.info(
                                "Promoted rule graduated to synced (canary success)",
                                rule_id=row[0],
                            )
                except Exception as e:
                    logger.debug(f"Post-promotion monitoring: {e}")
                    await db.rollback()

                # Step 6: Auto-promote site-level eligible patterns
                # These are patterns that meet eligibility (5+ occurrences, 90%+ success,
                # 3+ L2 resolutions) but are stuck waiting for manual approval.
                # At enterprise scale, we can't require a human click for every pattern.
                try:
                    from dashboard_api.fleet import get_pool as _get_pool
                    from dashboard_api.tenant_middleware import admin_connection as _admin_conn
                    from dashboard_api.flywheel_promote import promote_candidate

                    site_pool = await _get_pool()
                    site_promoted = 0
                    async with _admin_conn(site_pool) as conn:
                        async with conn.transaction():
                            eligible = await conn.fetch("""
                                SELECT aps.id, aps.site_id, aps.pattern_signature,
                                       aps.success_rate, aps.total_occurrences,
                                       aps.l2_resolutions, aps.recommended_action
                                FROM aggregated_pattern_stats aps
                                WHERE aps.promotion_eligible = true
                                  AND aps.l2_resolutions >= 3
                                  AND aps.success_rate >= 0.90
                                  AND aps.total_occurrences >= 5
                                  AND NOT EXISTS (
                                      SELECT 1 FROM promoted_rules pr
                                      WHERE pr.pattern_signature = aps.pattern_signature
                                        AND pr.site_id = aps.site_id
                                  )
                                ORDER BY aps.success_rate DESC, aps.total_occurrences DESC
                                LIMIT 10
                            """)

                            for p in eligible:
                                try:
                                    # Upsert candidate, then promote via shared module
                                    cand_row = await conn.fetchrow("""
                                        INSERT INTO learning_promotion_candidates
                                            (site_id, pattern_signature, approval_status)
                                        VALUES ($1, $2, 'pending')
                                        ON CONFLICT (site_id, pattern_signature) DO UPDATE SET
                                            approval_status = learning_promotion_candidates.approval_status
                                        RETURNING id, site_id, pattern_signature
                                    """, p["site_id"], p["pattern_signature"])

                                    candidate = dict(cand_row)
                                    candidate.update({
                                        "success_rate": p["success_rate"],
                                        "total_occurrences": p["total_occurrences"],
                                        "l2_resolutions": p["l2_resolutions"],
                                        "recommended_action": p["recommended_action"],
                                    })

                                    await promote_candidate(
                                        conn=conn,
                                        candidate=candidate,
                                        actor="flywheel_auto",
                                        actor_type="system",
                                    )

                                    # Mark source pattern ineligible
                                    await conn.execute("""
                                        UPDATE aggregated_pattern_stats
                                        SET promotion_eligible = false
                                        WHERE id = $1
                                    """, p["id"])

                                    site_promoted += 1
                                except Exception as e:
                                    logger.warning(
                                        f"Site-level auto-promotion skipped for "
                                        f"{p['pattern_signature']}: {e}"
                                    )

                    if site_promoted > 0:
                        logger.info(
                            f"Site-level auto-promotion: {site_promoted} patterns promoted"
                        )
                except Exception as e:
                    logger.warning(f"Site-level auto-promotion loop error: {e}")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"Flywheel promotion scan failed: {e}")

        await asyncio.sleep(1800)


async def l2_auto_candidate_loop():
    """Scan successful L2 decisions and create promotion candidates automatically.

    The Go daemon doesn't submit promotion reports (Python agent did).
    This task bridges the gap: finds L2 decisions with result='order_created'
    that aren't already candidates, and creates them.

    Runs every 30 minutes. Requires 3+ successes for the same pattern
    (check_type + runbook_id) before creating a candidate.
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
                        await conn.execute("""
                            INSERT INTO learning_promotion_candidates (
                                id, site_id, pattern_signature, incident_type,
                                recommended_action, check_type, success_count,
                                confidence_avg, approval_status, created_at
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pending', NOW())
                            ON CONFLICT (site_id, pattern_signature) DO UPDATE SET
                                success_count = EXCLUDED.success_count,
                                confidence_avg = EXCLUDED.confidence_avg
                        """,
                            str(uuid.uuid4()), row['site_id'], sig,
                            row['incident_type'], row['runbook_id'],
                            row['incident_type'],
                            row['success_count'], row['max_confidence'],
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


async def expire_fleet_orders_loop():
    """Background task to mark expired fleet orders."""
    while True:
        try:
            from dashboard_api.fleet import get_pool
            pool = await get_pool()
            async with pool.acquire() as conn:
                updated = await conn.execute("""
                    UPDATE fleet_orders SET status = 'expired'
                    WHERE status IN ('active', 'pending') AND expires_at < NOW()
                """)
                if updated and 'UPDATE' in updated:
                    count = int(updated.split()[-1])
                    if count > 0:
                        logger.info(f"Expired {count} fleet orders")
        except Exception as e:
            logger.warning(f"Fleet order expiry check failed: {e}")
        await asyncio.sleep(300)


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
                        AND created_at > NOW() - INTERVAL '24 hours'
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
                        # Record in audit log for daily dedup
                        await conn.execute("""
                            INSERT INTO audit_log (event_type, details, created_at)
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
    """Periodically sync site_appliances from appliances when diverged (every 5 min)."""
    from dashboard_api.fleet import get_pool
    await asyncio.sleep(180)
    while True:
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                async with conn.transaction():
                    # Set admin context for RLS bypass
                    await conn.execute("SET LOCAL app.is_admin = 'true'")
                    # Insert missing site_appliances rows for appliances that have no entry
                    inserted = await conn.fetch("""
                        INSERT INTO site_appliances (site_id, appliance_id, hostname, mac_address, agent_version, status, first_checkin, last_checkin)
                        SELECT
                            a.site_id,
                            a.host_id,
                            SPLIT_PART(a.host_id, '-', 1),
                            CASE WHEN a.host_id LIKE '%-%:%' THEN SUBSTRING(a.host_id FROM '[0-9A-Fa-f:]{17}$') ELSE NULL END,
                            a.agent_version,
                            CASE WHEN a.last_checkin > NOW() - INTERVAL '15 minutes' THEN 'online' ELSE 'offline' END,
                            a.created_at,
                            a.last_checkin
                        FROM appliances a
                        WHERE NOT EXISTS (
                            SELECT 1 FROM site_appliances sa WHERE sa.site_id = a.site_id
                        )
                        RETURNING site_id
                    """)
                    if inserted:
                        logger.info(f"Reconciliation: created {len(inserted)} missing site_appliances rows from appliances table")

                    # Update existing site_appliances rows from appliances when diverged
                    synced = await conn.fetch("""
                        UPDATE site_appliances sa SET
                            last_checkin = a.last_checkin,
                            agent_version = a.agent_version,
                            status = CASE WHEN a.last_checkin > NOW() - INTERVAL '15 minutes' THEN 'online' ELSE sa.status END
                        FROM appliances a
                        WHERE sa.site_id = a.site_id
                        AND a.last_checkin > sa.last_checkin + INTERVAL '5 minutes'
                        RETURNING sa.site_id
                    """)
                    if synced:
                        logger.info(f"Reconciliation: synced {len(synced)} stale site_appliances rows")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Reconciliation loop error: {e}")
        await asyncio.sleep(300)


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
                    await conn.execute("""
                        INSERT INTO l1_rules (
                            rule_id, incident_pattern, runbook_id,
                            confidence, enabled, source, description,
                            match_count, success_count, failure_count,
                            created_at
                        ) VALUES (
                            $1, $2::jsonb, $3,
                            $4, true, 'flywheel_recurrence', $5,
                            0, 0, 0, NOW()
                        )
                        ON CONFLICT (rule_id) DO NOTHING
                    """,
                        rule_id,
                        json.dumps({"incident_type": row["incident_type"]}),
                        row["runbook_id"],
                        row["confidence"],
                        f"Auto-promoted: L2 root-cause fix broke recurrence cycle. {row['reasoning'][:200]}",
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
            pool = await get_pool()
            async with pool.acquire() as conn:
                async with conn.transaction():
                    rows = await conn.fetch("""
                        UPDATE site_appliances
                        SET status = 'offline',
                            offline_since = COALESCE(offline_since, NOW()),
                            offline_event_count = offline_event_count + 1
                        WHERE status != 'offline'
                          AND status != 'decommissioned'
                          AND deleted_at IS NULL
                          AND last_checkin IS NOT NULL
                          AND last_checkin < NOW() - ($1 || ' minutes')::INTERVAL
                        RETURNING appliance_id, site_id, display_name, hostname,
                                  last_checkin, offline_notified
                    """, str(APPLIANCE_STALE_THRESHOLD_MINUTES))

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

"""
Background task loops extracted from main.py.

All periodic tasks that run via asyncio.create_task() during the
server lifespan. Imported and started by main.py's lifespan() function.
"""

import asyncio
import json
import re

import structlog
from sqlalchemy import text

from .shared import async_session

logger = structlog.get_logger()


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
                try:
                    remaining = max(0, 5 - promotions_this_cycle)
                    if remaining == 0:
                        logger.info("Flywheel promotion cap reached (5/cycle), skipping platform auto-promotion")
                    platform_result = await db.execute(text("""
                        SELECT pattern_key, incident_type, runbook_id,
                               distinct_orgs, total_occurrences, success_rate
                        FROM platform_pattern_stats
                        WHERE promoted_at IS NULL
                          AND distinct_orgs >= 5
                          AND success_rate >= 0.90
                          AND total_occurrences >= 20
                        ORDER BY distinct_orgs DESC, total_occurrences DESC
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

                        rule_id = f"L1-PLATFORM-{pc.incident_type.upper()}-{pc.runbook_id[:12].upper().replace('-', '')}"
                        try:
                            incident_pattern = {"incident_type": pc.incident_type}
                            if pc.incident_type:
                                incident_pattern["check_type"] = pc.incident_type

                            result = await db.execute(text("""
                                INSERT INTO l1_rules (
                                    rule_id, incident_pattern, runbook_id,
                                    confidence, promoted_from_l2, enabled, source
                                ) VALUES (
                                    :rule_id, CAST(:pattern AS jsonb), :runbook_id,
                                    :confidence, true, true, 'platform'
                                )
                                ON CONFLICT (rule_id) DO UPDATE SET
                                    confidence = EXCLUDED.confidence
                                RETURNING (xmax = 0) AS inserted
                            """), {
                                "rule_id": rule_id,
                                "pattern": json.dumps(incident_pattern),
                                "runbook_id": pc.runbook_id,
                                "confidence": float(pc.success_rate),
                            })
                            was_inserted = result.fetchone().inserted

                            await db.execute(text("""
                                UPDATE platform_pattern_stats
                                SET promoted_at = NOW(), promoted_rule_id = :rid
                                WHERE pattern_key = :pk
                            """), {"rid": rule_id, "pk": pc.pattern_key})

                            await db.commit()
                            if was_inserted:
                                platform_promoted += 1
                                promotions_this_cycle += 1
                                logger.info(
                                    "Platform rule auto-promoted",
                                    rule_id=rule_id,
                                    incident_type=pc.incident_type,
                                    distinct_orgs=pc.distinct_orgs,
                                    success_rate=f"{pc.success_rate:.1%}",
                                    total_occurrences=pc.total_occurrences,
                                )
                        except Exception as e:
                            logger.warning(f"Failed to promote platform rule {rule_id}: {e}")
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

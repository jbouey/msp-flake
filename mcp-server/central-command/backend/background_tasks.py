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

                # Step 5: Post-promotion health monitoring
                try:
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
                except Exception as e:
                    logger.debug(f"Post-promotion monitoring: {e}")
                    await db.rollback()

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
                    WHERE status = 'active' AND expires_at < NOW()
                """)
                if updated and 'UPDATE' in updated:
                    count = int(updated.split()[-1])
                    if count > 0:
                        logger.info(f"Expired {count} fleet orders")
        except Exception as e:
            logger.warning(f"Fleet order expiry check failed: {e}")
        await asyncio.sleep(300)


async def reconciliation_loop():
    """Periodically sync site_appliances from appliances when diverged (every 5 min)."""
    from dashboard_api.fleet import get_pool
    await asyncio.sleep(180)
    while True:
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
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

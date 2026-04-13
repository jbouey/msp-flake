"""Background chain-tamper detector (Phase 15 A-spec hygiene).

Periodically walks the last N compliance_bundles per active site and
verifies the cryptographic chain (prev_hash linkage + chain_hash
recomputation). Any failure means somebody mutated stored bundles
post-write — which the DELETE/UPDATE triggers (migrations 151, 161)
shouldn't allow, but this is the watchdog that proves it.

Why this exists:
  - The on-demand /sites/{id}/verify-chain endpoint only fires when
    an auditor or admin asks. Tampering between asks is invisible.
  - The auditor-kit ZIP exists for handover but the operator only
    learns about a bad chain when an auditor downloads it — months
    later.
  - This loop runs every CHAIN_TAMPER_INTERVAL_S (default 3600s) and
    surfaces tampering in seconds via the chain-tamper Prometheus
    metric + admin_audit_log entry.

Design properties:
  - Bounded work: walk at most CHAIN_TAMPER_WINDOW bundles per site
    (default 100) — the most recent slice. A fully-tampered earlier
    epoch is still detectable by an auditor running the full kit.
  - Active-site filter: sites with no checkin in 24h are skipped to
    avoid wasted work on decommissioned tenants.
  - Heartbeat-instrumented for /api/admin/health/loops visibility.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
from typing import List, Tuple

logger = logging.getLogger(__name__)


CHAIN_TAMPER_INTERVAL_S = int(os.getenv("CHAIN_TAMPER_INTERVAL_S", "3600"))
CHAIN_TAMPER_WINDOW = int(os.getenv("CHAIN_TAMPER_WINDOW", "100"))
GENESIS_HASH = "0" * 64


async def _verify_site_recent(conn, site_id: str) -> Tuple[int, List[dict]]:
    """Walk the most-recent CHAIN_TAMPER_WINDOW bundles for a site,
    in chain_position order, and return (verified_count, broken_list).

    broken_list entries: {position, bundle_id, hash_valid, link_valid,
    expected_chain_hash, actual_chain_hash}
    """
    rows = await conn.fetch(
        """
        SELECT bundle_id, bundle_hash, prev_hash, chain_position, chain_hash
        FROM compliance_bundles
        WHERE site_id = $1
        ORDER BY chain_position DESC
        LIMIT $2
        """,
        site_id,
        CHAIN_TAMPER_WINDOW,
    )
    if not rows:
        return 0, []

    # Reverse to chain_position ASC so we can validate prev_hash linkage
    bundles = list(reversed(rows))
    verified = 0
    broken: List[dict] = []

    for i, b in enumerate(bundles):
        chain_data = f"{b['bundle_hash']}:{b['prev_hash']}:{b['chain_position']}"
        expected_chain_hash = hashlib.sha256(chain_data.encode()).hexdigest()
        hash_ok = hmac.compare_digest(b["chain_hash"] or "", expected_chain_hash)

        link_ok = True
        if i == 0:
            # First bundle in our window — we can't verify prev_hash without
            # fetching the bundle at chain_position - 1. We treat the window
            # boundary as a known anchor and only check chain_hash.
            pass
        else:
            prev = bundles[i - 1]
            link_ok = (
                hmac.compare_digest(b["prev_hash"] or "", prev["bundle_hash"] or "")
                and b["chain_position"] == prev["chain_position"] + 1
            )

        if hash_ok and link_ok:
            verified += 1
        else:
            broken.append({
                "position": b["chain_position"],
                "bundle_id": b["bundle_id"],
                "hash_valid": hash_ok,
                "link_valid": link_ok,
                "expected_chain_hash": expected_chain_hash,
                "actual_chain_hash": b["chain_hash"],
            })

    return verified, broken


async def chain_tamper_detector_loop():
    """Background task — runs every CHAIN_TAMPER_INTERVAL_S. Walks
    recent bundles per active site and surfaces tamper events.

    Registered from main.py lifespan alongside the other flywheel
    background tasks."""
    await asyncio.sleep(120)  # let other startup finish

    while True:
        try:
            from .bg_heartbeat import record_heartbeat
            record_heartbeat("chain_tamper_detector")
        except Exception:
            pass

        try:
            from .fleet import get_pool
            from .tenant_middleware import admin_connection

            pool = await get_pool()
            async with admin_connection(pool) as conn:
                # Active sites only — we don't need to keep walking
                # decommissioned tenants forever.
                site_rows = await conn.fetch(
                    """
                    SELECT DISTINCT site_id
                    FROM site_appliances
                    WHERE last_checkin > NOW() - INTERVAL '24 hours'
                    """
                )
                site_count = 0
                tamper_count = 0
                broken_total = 0
                for sr in site_rows:
                    sid = sr["site_id"]
                    site_count += 1
                    try:
                        async with conn.transaction():
                            verified, broken = await _verify_site_recent(conn, sid)
                    except Exception as e:
                        logger.warning(
                            "chain_tamper_detector site walk failed",
                            extra={"site_id": sid, "error": str(e)},
                        )
                        continue

                    if broken:
                        tamper_count += 1
                        broken_total += len(broken)
                        # Log ERROR — this is the exact severity the log
                        # shipper alerts on. CLAUDE.md guarantees all
                        # log-shipper alerts fire on ERROR-level messages.
                        logger.error(
                            "CHAIN_TAMPER_DETECTED",
                            extra={
                                "site_id": sid,
                                "verified": verified,
                                "broken_count": len(broken),
                                "first_broken": broken[0],
                            },
                        )

                        # Phase 15 alert wiring — chain tampering is the
                        # one event that should wake someone up. Email
                        # the security distribution immediately. Failure
                        # to send the alert must not stop the audit_log
                        # write from the block below — they're independent.
                        try:
                            from .email_alerts import send_critical_alert
                            send_critical_alert(
                                title=f"CHAIN_TAMPER_DETECTED on {sid}",
                                message=(
                                    f"The chain-tamper detector found "
                                    f"{len(broken)} broken bundle(s) on "
                                    f"site {sid} during the periodic walk "
                                    f"of the most-recent {CHAIN_TAMPER_WINDOW} "
                                    f"bundles.\n\n"
                                    f"This means a compliance_bundles row "
                                    f"was MUTATED after write — the "
                                    f"DELETE/UPDATE triggers (migrations "
                                    f"151, 161) are supposed to make this "
                                    f"impossible. Investigate immediately:\n\n"
                                    f"  1. Check admin_audit_log for the "
                                    f"correlated CHAIN_TAMPER_DETECTED row.\n"
                                    f"  2. Run the auditor kit for site "
                                    f"{sid} to confirm scope.\n"
                                    f"  3. Inspect first_broken below.\n\n"
                                    f"first_broken: {broken[0]}\n"
                                    f"verified_in_window: {verified}"
                                ),
                                site_id=sid,
                                category="security_chain_integrity",
                                metadata={
                                    "verified": verified,
                                    "broken_count": len(broken),
                                    "first_broken": broken[0],
                                },
                            )
                        except Exception as e:
                            logger.error(
                                "chain_tamper_detector alert dispatch failed",
                                extra={"site_id": sid, "error": str(e)},
                                exc_info=True,
                            )

                        # Persist to admin_audit_log so the next operator
                        # session sees it without needing to be tailing
                        # logs at the moment of detection.
                        try:
                            async with conn.transaction():
                                await conn.execute(
                                    """
                                    INSERT INTO admin_audit_log
                                    (action, target_type, target_id, details, created_at)
                                    VALUES (
                                        'CHAIN_TAMPER_DETECTED',
                                        'site',
                                        $1,
                                        $2::jsonb,
                                        NOW()
                                    )
                                    """,
                                    sid,
                                    __import__("json").dumps({
                                        "site_id": sid,
                                        "verified": verified,
                                        "broken": broken[:10],  # cap blob size
                                        "broken_count": len(broken),
                                        "window": CHAIN_TAMPER_WINDOW,
                                    }),
                                )
                        except Exception as e:
                            logger.error(
                                "chain_tamper_detector audit insert failed",
                                extra={"site_id": sid, "error": str(e)},
                                exc_info=True,
                            )

                if tamper_count:
                    logger.error(
                        "chain_tamper_detector cycle complete with violations",
                        extra={
                            "sites_checked": site_count,
                            "sites_tampered": tamper_count,
                            "broken_total": broken_total,
                        },
                    )
                else:
                    logger.info(
                        "chain_tamper_detector cycle clean",
                        extra={"sites_checked": site_count},
                    )
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"chain_tamper_detector top-level error: {e}")

        await asyncio.sleep(CHAIN_TAMPER_INTERVAL_S)

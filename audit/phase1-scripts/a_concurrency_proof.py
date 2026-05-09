"""
§A — pg_advisory_xact_lock concurrency proof.

Races N=10 concurrent writers on the SAME synthetic site_id, all simulating
the privileged_access_attestation chain-mutator path:

  BEGIN
  pg_advisory_xact_lock(hashtext(site_id), hashtext('attest'))
  SELECT prev_bundle (chain_position, bundle_hash)
  INSERT compliance_bundles (chain_position = prev+1, prev_hash = prev.bundle_hash)
  COMMIT

Verifies:
  1. No duplicate (site_id, chain_position) bundles
  2. chain_position increments contiguously (0..N-1)
  3. prev_hash chain unbroken on chain-walk
  4. Lock-wait p99 bounded (< 30s under 10-way concurrency)

CLEANUP: deletes ALL synthetic rows on its way out (synthetic site_id + uuid).

Audit-time only — DO NOT keep as a permanent test.
"""

import asyncio
import hashlib
import json
import os
import secrets
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone

import asyncpg

DSN = os.environ["AUDIT_DSN"]
N = 10
SITE_ID = f"phase1-A-concur-{uuid.uuid4().hex[:8]}"


async def writer(conn_idx: int, site_id: str, results: list, dsn: str):
    """One writer: BEGIN, advisory lock, read prev, write next bundle, COMMIT."""
    conn = await asyncpg.connect(dsn)
    try:
        t0 = time.monotonic()
        async with conn.transaction():
            t_lock_start = time.monotonic()
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1), hashtext('attest'))",
                site_id,
            )
            t_lock_done = time.monotonic()

            row = await conn.fetchrow(
                "SELECT bundle_id, bundle_hash, chain_position "
                "FROM compliance_bundles "
                "WHERE site_id = $1 "
                "ORDER BY checked_at DESC LIMIT 1",
                site_id,
            )
            if row:
                prev_bundle_id = row["bundle_id"]
                prev_hash = row["bundle_hash"]
                chain_position = row["chain_position"] + 1
            else:
                prev_bundle_id = None
                prev_hash = "0" * 64
                chain_position = 0

            now = datetime.now(timezone.utc)
            payload = {
                "site_id": site_id,
                "writer": conn_idx,
                "checked_at": now.isoformat(),
                "prev_hash": prev_hash,
                "chain_position": chain_position,
            }
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            bundle_hash = hashlib.sha256(canonical.encode()).hexdigest()
            chain_hash = hashlib.sha256((prev_hash + bundle_hash).encode()).hexdigest()
            bundle_id = f"PA-A-{now.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4)}-{conn_idx}"

            await conn.execute("""
                INSERT INTO compliance_bundles (
                    site_id, bundle_id, bundle_hash, check_type, check_result,
                    checked_at, checks, summary,
                    signed_data, signature_valid,
                    prev_bundle_id, prev_hash, chain_position, chain_hash,
                    signature, signed_by, ots_status
                ) VALUES (
                    $1, $2, $3, 'privileged_access', 'recorded',
                    $4, $5::jsonb, $6::jsonb,
                    $7, true,
                    $8, $9, $10, $11,
                    $12, 'phase1-audit', 'none'
                )
            """,
                site_id, bundle_id, bundle_hash,
                now,
                json.dumps([payload]), json.dumps({"writer": conn_idx}),
                canonical,
                prev_bundle_id, prev_hash, chain_position, chain_hash,
                "0" * 128,
            )
            t_done = time.monotonic()
        results.append({
            "writer": conn_idx,
            "bundle_id": bundle_id,
            "chain_position": chain_position,
            "prev_hash": prev_hash,
            "bundle_hash": bundle_hash,
            "lock_wait_s": t_lock_done - t_lock_start,
            "total_s": t_done - t0,
        })
    finally:
        await conn.close()


async def cleanup(site_id: str, dsn: str):
    conn = await asyncpg.connect(dsn)
    try:
        deleted = await conn.fetchval(
            "WITH d AS (DELETE FROM compliance_bundles WHERE site_id = $1 RETURNING 1) "
            "SELECT count(*) FROM d",
            site_id,
        )
        return deleted
    finally:
        await conn.close()


async def main():
    print(f"[A] site_id={SITE_ID} N={N}")

    # All 10 writers fire simultaneously (asyncio.gather).
    results: list = []
    t0 = time.monotonic()
    await asyncio.gather(*(writer(i, SITE_ID, results, DSN) for i in range(N)))
    elapsed = time.monotonic() - t0
    print(f"[A] elapsed={elapsed:.3f}s")

    # Sort by chain_position to verify contiguity + chain integrity.
    results.sort(key=lambda r: r["chain_position"])

    # Check 1: no duplicate chain_positions
    positions = [r["chain_position"] for r in results]
    dup_check = len(set(positions)) == len(positions)

    # Check 2: contiguous 0..N-1
    contiguous = positions == list(range(N))

    # Check 3: chain integrity (each row's prev_hash matches prior row's bundle_hash)
    chain_ok = True
    for i in range(1, N):
        if results[i]["prev_hash"] != results[i - 1]["bundle_hash"]:
            chain_ok = False
            break

    # Check 4: lock-wait p99
    waits = sorted(r["lock_wait_s"] for r in results)
    p99 = waits[-1]  # only 10 samples, max ≈ p99
    p50 = statistics.median(waits)

    print(f"[A] positions={positions}")
    print(f"[A] no-dup={dup_check} contiguous={contiguous} chain-ok={chain_ok}")
    print(f"[A] lock-wait: p50={p50*1000:.1f}ms p99(max)={p99*1000:.1f}ms")
    print(f"[A] total elapsed: {elapsed*1000:.1f}ms (10-way)")

    # Cleanup
    deleted = await cleanup(SITE_ID, DSN)
    print(f"[A] cleanup: deleted {deleted} synthetic rows")

    verdict = "PASS" if (dup_check and contiguous and chain_ok and p99 < 30.0) else "FAIL"
    print(f"[A] VERDICT: {verdict}")
    sys.exit(0 if verdict == "PASS" else 1)


if __name__ == "__main__":
    asyncio.run(main())

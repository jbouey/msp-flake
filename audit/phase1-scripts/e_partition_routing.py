"""
§E — Partition routing under multi-tenant concurrent inserts.

10 concurrent writers, each on a DIFFERENT synthetic site_id, all insert
1 row. Verify all rows land in compliance_bundles_2026_05 (current
month). compliance_bundles_default must remain at n_tup_ins=0.

Cleanup: trigger-toggle dance to delete synthetic rows.
"""

import asyncio
import hashlib
import json
import os
import secrets
import sys
import uuid
from datetime import datetime, timezone

import asyncpg

DSN = os.environ["AUDIT_DSN"]
N = 10
PREFIX = f"phase1-E-part-{uuid.uuid4().hex[:6]}"


async def writer(idx: int, site_id: str, dsn: str, results: list):
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            now = datetime.now(timezone.utc)
            payload = {"site_id": site_id, "writer": idx}
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            bh = hashlib.sha256(canonical.encode()).hexdigest()
            ch = hashlib.sha256(("0" * 64 + bh).encode()).hexdigest()
            bid = f"PA-E-{now.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4)}-{idx}"

            await conn.execute("""
                INSERT INTO compliance_bundles (
                    site_id, bundle_id, bundle_hash, check_type, check_result,
                    checked_at, checks, summary,
                    signed_data, signature_valid,
                    prev_bundle_id, prev_hash, chain_position, chain_hash,
                    signature, signed_by, ots_status
                ) VALUES (
                    $1,$2,$3,'privileged_access','recorded',
                    $4,$5::jsonb,$6::jsonb,
                    $7,true,
                    NULL,$8,0,$9,
                    $10,'phase1-audit','none'
                )
            """,
                site_id, bid, bh, now,
                json.dumps([payload]), json.dumps({}),
                canonical,
                "0" * 64, ch,
                "0" * 128,
            )
            tableoid = await conn.fetchval(
                "SELECT tableoid::regclass FROM compliance_bundles WHERE bundle_id=$1",
                bid,
            )
            results.append({"writer": idx, "bundle_id": bid, "partition": str(tableoid)})
    finally:
        await conn.close()


async def main():
    results: list = []
    sites = [f"{PREFIX}-{i:02d}" for i in range(N)]
    await asyncio.gather(*(writer(i, sites[i], DSN, results) for i in range(N)))

    print(f"[E] N={N} prefix={PREFIX}")
    parts = {}
    for r in results:
        parts[r["partition"]] = parts.get(r["partition"], 0) + 1
    print(f"[E] partition routing: {parts}")

    # Check default partition n_tup_ins (should not have grown)
    conn = await asyncpg.connect(DSN)
    default_inserts = await conn.fetchval(
        "SELECT n_tup_ins FROM pg_stat_user_tables WHERE relname='compliance_bundles_default'"
    )
    await conn.close()
    print(f"[E] compliance_bundles_default n_tup_ins (cumulative): {default_inserts}")

    only_current_month = (
        len(parts) == 1 and "2026_05" in next(iter(parts.keys()))
    )
    print(f"[E] all rows in current-month partition: {only_current_month}")
    print(f"[E] VERDICT: {'PASS' if only_current_month and default_inserts == 0 else 'FAIL'}")
    sys.exit(0 if only_current_month and default_inserts == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())

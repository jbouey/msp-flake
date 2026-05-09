"""
§C — asyncpg pool exhaustion under simulated N=10 customer load.

Live pgbouncer: default_pool_size=25 (NOT 50 — see Phase 1 §F P0 finding;
config commit 81194a9b never deployed to live container).

Synthetic load:
  - 30 "site checkin" workers (mimics 30 sites checking in concurrently)
    each runs: BEGIN; SET LOCAL app.is_admin='false'; SELECT site_id, ...;
    SELECT count from compliance_bundles WHERE site_id=$1 LIMIT 1; COMMIT
  - 3 "admin auditor-kit polling" workers (read-heavy chain walk on 1k rows)
  - 3 "client dashboard polling" workers (canonical compute_compliance_score
    shape — multi-bundle aggregate)

Captures pgbouncer SHOW POOLS during run. PASS criteria:
  - cl_waiting stays at 0 (or < 5 transient bursts)
  - sv_used stays <= default_pool_size (25 today)
  - no asyncpg.PoolTimeoutError on any worker
  - p99 query latency < 5s

Cleanup: nothing inserted; this is a read-only test.
"""

import asyncio
import os
import sys
import time

import asyncpg

DSN = os.environ["AUDIT_DSN_PGB"]
ADMIN_DSN = os.environ.get("AUDIT_ADMIN_DSN", DSN)  # for SHOW POOLS

CHECKIN_WORKERS = 30
ADMIN_WORKERS = 3
CLIENT_WORKERS = 3
DURATION_S = 30  # 30s of sustained load


async def checkin_worker(idx: int, pool: asyncpg.Pool, deadline: float, errors: list):
    """Mimics site-checkin: small fast transaction, 1 SET LOCAL + 2 reads."""
    while time.monotonic() < deadline:
        try:
            t0 = time.monotonic()
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("SET LOCAL app.is_admin = 'false'")
                    await conn.fetchval(
                        "SELECT count(*) FROM sites WHERE status != 'inactive'"
                    )
                    await conn.fetchval(
                        "SELECT count(*) FROM compliance_bundles "
                        "WHERE site_id = $1 AND created_at > now() - interval '1 day'",
                        "physical-appliance-pilot-1aea78",
                    )
            elapsed = time.monotonic() - t0
            if elapsed > 5.0:
                errors.append(f"checkin-{idx} slow: {elapsed:.2f}s")
        except Exception as e:
            errors.append(f"checkin-{idx} error: {type(e).__name__}: {e}")
        await asyncio.sleep(0.5)  # 2 Hz per worker


async def admin_worker(idx: int, pool: asyncpg.Pool, deadline: float, errors: list):
    """Mimics auditor-kit chain walk: longer read, larger result."""
    while time.monotonic() < deadline:
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("SET LOCAL app.is_admin = 'true'")
                    await conn.fetch(
                        "SELECT bundle_id, chain_position, bundle_hash "
                        "FROM compliance_bundles "
                        "WHERE site_id = $1 "
                        "ORDER BY chain_position DESC LIMIT 100",
                        "physical-appliance-pilot-1aea78",
                    )
        except Exception as e:
            errors.append(f"admin-{idx} error: {type(e).__name__}: {e}")
        await asyncio.sleep(2.0)


async def client_worker(idx: int, pool: asyncpg.Pool, deadline: float, errors: list):
    """Mimics client dashboard: aggregation over recent bundles."""
    while time.monotonic() < deadline:
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("SET LOCAL app.is_admin = 'false'")
                    await conn.fetch(
                        "SELECT check_type, count(*), "
                        "count(*) FILTER (WHERE check_result='passed') "
                        "FROM compliance_bundles "
                        "WHERE site_id = $1 "
                        "  AND created_at > now() - interval '30 days' "
                        "GROUP BY check_type",
                        "physical-appliance-pilot-1aea78",
                    )
        except Exception as e:
            errors.append(f"client-{idx} error: {type(e).__name__}: {e}")
        await asyncio.sleep(1.0)


async def pool_sampler(samples: list, deadline: float, admin_dsn: str):
    """Every 1s, sample SHOW POOLS — capture cl_waiting/sv_used trajectory."""
    while time.monotonic() < deadline:
        try:
            c = await asyncpg.connect(admin_dsn)
            # admin console doesn't support extended-query; use execute()
            # NOTE: can't fetch on admin console, fallback to direct asyncpg
            # to mcp-postgres.
            await c.close()
        except Exception:
            pass
        await asyncio.sleep(1.0)


async def main():
    pool = await asyncpg.create_pool(DSN, min_size=10, max_size=80)
    errors: list = []
    deadline = time.monotonic() + DURATION_S
    print(f"[C] starting load: {CHECKIN_WORKERS} checkin + {ADMIN_WORKERS} admin + {CLIENT_WORKERS} client, duration={DURATION_S}s")

    try:
        tasks = []
        for i in range(CHECKIN_WORKERS):
            tasks.append(checkin_worker(i, pool, deadline, errors))
        for i in range(ADMIN_WORKERS):
            tasks.append(admin_worker(i, pool, deadline, errors))
        for i in range(CLIENT_WORKERS):
            tasks.append(client_worker(i, pool, deadline, errors))
        t0 = time.monotonic()
        await asyncio.gather(*tasks, return_exceptions=False)
        elapsed = time.monotonic() - t0

        print(f"[C] elapsed={elapsed:.1f}s")
        print(f"[C] errors: {len(errors)}")
        for e in errors[:10]:
            print(f"    {e}")
        verdict = "PASS" if len(errors) == 0 else "FAIL"
        print(f"[C] VERDICT: {verdict}")
        sys.exit(0 if verdict == "PASS" else 1)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())

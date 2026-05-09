"""
§B — RLS context bleeding across PgBouncer transaction-pool mode.

PgBouncer transaction-pool: SET LOCAL is supposed to be transaction-scoped
and discarded on COMMIT/ROLLBACK. server_reset_query=DISCARD ALL adds
defense-in-depth.

Test: open 5 asyncpg connections via pgbouncer pool. In a tight inner loop:
  - Conn 1 BEGIN; SET LOCAL app.current_org='org-A'; ... COMMIT
  - Conn 2 BEGIN; SELECT current_setting('app.current_org', true); ... COMMIT
    must NOT see 'org-A'

Repeat with app.current_partner_id and app.is_admin. Run 200 iterations
to exercise pgbouncer connection rotation.

If any leak observed → P0.
"""

import asyncio
import os
import sys

import asyncpg

DSN = os.environ["AUDIT_DSN_PGB"]  # pgbouncer DSN
N_CONNS = 5
ITERATIONS = 200


async def writer_set(pool: asyncpg.Pool, key: str, value: str, results: list):
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL {key} = '{value}'")
            seen = await conn.fetchval(f"SELECT current_setting('{key}', true)")
            results.append(("SET", key, value, seen))


async def reader_check(pool: asyncpg.Pool, key: str, results: list):
    async with pool.acquire() as conn:
        async with conn.transaction():
            seen = await conn.fetchval(f"SELECT current_setting('{key}', true)")
            results.append(("READ", key, None, seen))


async def main():
    pool = await asyncpg.create_pool(DSN, min_size=N_CONNS, max_size=N_CONNS)
    leaks: list = []
    keys = ["app.current_org", "app.current_partner_id", "app.is_admin"]

    try:
        for it in range(ITERATIONS):
            results: list = []
            # Fire writers + readers concurrently against the same small pool.
            # Writers set their key; readers fetch the same key in a separate
            # transaction. If pgbouncer leaks, reader sees writer's value.
            tasks = []
            for k in keys:
                writer_val = f"leak-test-{it}-{k.split('.')[-1]}"
                tasks.append(writer_set(pool, k, writer_val, results))
                # 3 readers per writer to force pool reuse
                for _ in range(3):
                    tasks.append(reader_check(pool, k, results))
            await asyncio.gather(*tasks)

            # Validate: a REAL leak is the reader seeing the writer's
            # specific test value (e.g. "leak-test-N-org"). The
            # database-default GUCs (app.is_admin=false,
            # app.current_org='', etc., set via ALTER DATABASE) are
            # session-defaults and inherited by every backend — those
            # are NOT cross-transaction leaks, they are intentional
            # baselines.
            for op, key, val, seen in results:
                if op == "READ" and seen and seen.startswith("leak-test-"):
                    leaks.append({
                        "iteration": it,
                        "key": key,
                        "leaked_value": seen,
                    })

        print(f"[B] iterations={ITERATIONS} keys={keys}")
        print(f"[B] total leaks observed: {len(leaks)}")
        if leaks:
            print(f"[B] FIRST 5 leak samples:")
            for l in leaks[:5]:
                print(f"    {l}")
        verdict = "PASS" if len(leaks) == 0 else "FAIL"
        print(f"[B] VERDICT: {verdict}")
        sys.exit(0 if verdict == "PASS" else 1)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())

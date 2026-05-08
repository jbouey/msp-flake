# merkle_batch_stalled

**Severity:** sev1
**Display name:** Merkle batch worker stalled — evidence not anchoring

## What this means (plain English)

One or more `compliance_bundles` rows have been pinned at
`ots_status='batching'` for more than 6 hours. The hourly
`_merkle_batch_loop` is supposed to walk every site with rows in
that state, build a Merkle tree, submit the root to OpenTimestamps,
and transition the rows to `ots_status='pending'` (then ultimately
`'anchored'` once Bitcoin confirms). When this invariant fires, that
pipeline is not running on the affected site.

The customer-facing tamper-evidence promise depends on Bitcoin OTS
anchoring. §164.312(c)(1) integrity controls assume timestamped
evidence. Bundles stuck in `batching` for hours are evidence WITHOUT
the tamper-evidence layer — auditors will catch that gap.

This is sev1 because every additional hour of stall is another hour
of audit-vulnerable evidence.

## Root cause categories

- **RLS-blind background loop (the 2026-05-08 class).** The
  `_merkle_batch_loop` was using bare `pool.acquire()` — PgBouncer-
  routed asyncpg pool inherits `app.is_admin='false'`, RLS hides
  every row. Fixed structurally in commit `7db2faab` (admin_transaction)
  + CI gate `test_bg_loop_admin_context.py`. If this fires AGAIN with
  the structural fix in place, look for a NEW loop with the same
  shape, or a regression in `tenant_middleware.admin_transaction`.
- **OTS calendar outage.** `submit_hash_to_ots` returns None when
  the calendar is unreachable; `process_merkle_batch` then logs
  `Merkle batch OTS submission failed for {batch_id}` and exits
  early. Every retry cycle would print the same line.
- **asyncpg pool exhaustion.** Backgrounded `pool.acquire()` blocks
  on a free connection; if every connection is held by long-running
  reads, the loop never gets to run its body.
- **Code bug in `process_merkle_batch`.** Recent change broke the
  batch_id generator, the Merkle-tree builder, or the
  `compliance_bundles UPDATE` step.

## Immediate action

1. `docker logs mcp-server | grep -E 'Merkle batch|merkle_batch'`
   — confirm `bg_task_started task=merkle_batch` is present AND
   look for `Merkle batch created` lines following each. A missing
   `Merkle batch created` line points at the loop entering its body
   but `process_merkle_batch` returning `batched=0` (look at why).
2. `curl -sS https://alice.btc.calendar.opentimestamps.org/` —
   confirm the OTS calendar is responsive. If 5xx or timeout,
   the batcher will re-try next cycle automatically; recovery is
   passive once the calendar is back.
3. Verify admin context reaches the loop:
   ```bash
   docker exec mcp-server python -c "
   import asyncio
   from dashboard_api.fleet import get_pool
   from dashboard_api.tenant_middleware import admin_transaction
   async def main():
       pool = await get_pool()
       async with admin_transaction(pool) as conn:
           n = await conn.fetchval(
               \"SELECT COUNT(*) FROM compliance_bundles WHERE ots_status='batching'\"
           )
           print(f'visible: {n}')
   asyncio.run(main())
   "
   ```
   Compare against direct psql with `BEGIN; SET LOCAL app.is_admin='true'; SELECT COUNT(*) ...; ROLLBACK;`. If they match, RLS is OK. If app shows 0 and psql shows N, RLS regression.
4. **Manual unstall** (the 2026-05-08 procedure):
   ```bash
   docker exec mcp-server python -c "
   import asyncio
   from dashboard_api.fleet import get_pool
   from dashboard_api.tenant_middleware import admin_transaction
   from dashboard_api.evidence_chain import process_merkle_batch
   async def main():
       pool = await get_pool()
       async with admin_transaction(pool) as conn:
           sites = await conn.fetch(
               \"SELECT DISTINCT site_id FROM compliance_bundles WHERE ots_status='batching'\"
           )
           for r in sites:
               stats = await process_merkle_batch(conn, r['site_id'])
               print(stats)
   asyncio.run(main())
   "
   ```

## Verification

After action: this invariant clears in <60s once
`SELECT COUNT(*) FROM compliance_bundles WHERE ots_status='batching' AND created_at < NOW() - INTERVAL '6 hours'`
returns zero. The substrate engine re-checks every 60s.

## Escalation

If manual unstall succeeds but the loop fails to fire on its own
within an hour, escalate to engineering on-call. The stall mechanism
is then deeper than RLS routing OR OTS calendar — likely a deadlock
in `process_merkle_batch` itself or in `submit_hash_to_ots`. Pull
`docker logs mcp-server | grep -A40 merkle_batch_loop` and a
`pg_stat_activity` snapshot for diagnosis.

## Related runbooks

- `bg_loop_silent.md` — class detector for any background loop that
  has stopped emitting heartbeats. Fires alongside this if the
  loop's stuck-await is the cause (vs. silent zero-row return).
- `compliance_packets_stalled.md` — sibling §164.316(b)(2)(i)
  invariant for monthly packets.

## Related

- Audit: `audit/coach-e2e-attestation-audit-2026-05-08.md` F-P0-1
- Round-table verdict: `audit/round-table-verdict-2026-05-08.md` RT-1.1
- Round-table close-out: `audit/round-table-closeout-2026-05-08.md`

## Change log

- **2026-05-08:** Created. Prompted by 18-day production rupture on
  the only paying customer site (north-valley-branch-2), 2,669
  bundles unanchored. Manual unstall + structural admin_transaction
  fix in commit `7db2faab`. CI gate
  `test_bg_loop_admin_context.py` prevents the RLS regression class.
  This invariant is the runtime defense for any OTHER stall cause.

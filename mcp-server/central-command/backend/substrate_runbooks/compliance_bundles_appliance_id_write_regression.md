# compliance_bundles_appliance_id_write_regression

**Severity:** sev2
**Display name:** Deprecated column written: compliance_bundles.appliance_id

## What this means (plain English)

A `compliance_bundles` row was written within the last hour with
`appliance_id IS NOT NULL`. This column was deprecated by migration
268 (2026-05-01) when the round-table chose Path B over a 245K-row
backfill. All 4 production writers (`evidence_chain.py:1443`,
`runbook_consent.py:460`, `appliance_relocation.py:222/383`,
`privileged_access_attestation.py:497`) omit it.

A non-zero count here means a regression has re-introduced a writer.
The column is being removed in Phase 3 of #122; every regression
extends the quiet-soak window and pushes the DROP date.

## Why sev2 (not sev1)

The column is NOT in the signed payload (verified
`evidence_chain.py:1443` — the INSERT omits `appliance_id` and the
`signed_data` is built independently upstream). A regression here
does NOT corrupt chain integrity or auditor-kit determinism — it
just keeps a dead column alive longer.

## Why sev2 (not sev3)

Operator-attention threshold: Phase 2/3 of the deprecation depends
on this column being write-quiet. sev3 falls below dashboard
prominence and would let regressions accumulate silently.

## Root cause categories

1. **New code branch added an INSERT/UPDATE writer.** Most common.
   Find via `git log -S 'compliance_bundles' --since=24h` filtered
   to writers that mention `appliance_id`.

2. **A reverted migration re-introduced the column on partition.**
   Rare. Verify schema state: `\d+ compliance_bundles` in psql.

3. **External DB write (manual psql, debugging session) leaked.**
   Verify `admin_audit_log` for recent manual SQL events.

4. **A test fixture used in prod accidentally.** Check
   `test_*_pg.py` files for INSERTs that include `appliance_id`
   in the column list — those should never run against prod.

## Immediate action

1. **Identify the offending writer:**
   ```bash
   git log -S 'compliance_bundles' --since=24h --oneline
   git log -p -S 'appliance_id' --since=24h \
       -- mcp-server/central-command/backend/
   ```

2. **Verify in the database:**
   ```sql
   SELECT bundle_id, site_id, appliance_id, check_type, created_at
     FROM compliance_bundles
    WHERE appliance_id IS NOT NULL
      AND created_at > NOW() - INTERVAL '1 hour'
    ORDER BY created_at DESC LIMIT 20;
   ```

3. **Fix forward**: revert the writer or drop `appliance_id` from
   the INSERT/UPDATE column list. Re-deploy.

4. **DELETE the orphan rows** (allowed — column is being removed):
   ```sql
   -- NOTE: trg_prevent_audit_deletion on compliance_bundles MAY
   -- block this DELETE depending on the row's age. If blocked,
   -- the rows will be cleaned up at Phase 3 DROP COLUMN time.
   UPDATE compliance_bundles SET appliance_id = NULL
    WHERE bundle_id IN (...);
   ```

   Setting to NULL is safer than DELETE — it preserves the bundle
   for chain integrity while removing the deprecated value.

## Verification

- Invariant clears on next 60s tick once all hot rows have
  `appliance_id IS NULL` again.
- CI gate `tests/test_no_compliance_bundles_appliance_id_writes.py`
  catches future regressions at build time (AST scan over backend
  Python source for `INSERT INTO compliance_bundles` containing
  `appliance_id` in the column list).

## Escalation

- **>10 violations in 24h:** sev1 escalation. A persistent
  regression means the deprecation Phase 2/3 schedule slips.
  Pause downstream tasks #122 Phase 2 + #122 Phase 3 until the
  writer is closed.

## Related runbooks

- mig 268 (`calculate_compliance_score_fix`) — original
  deprecation moment
- mig 326 (`rewrite_v_control_status`) — view rewrite removing
  the last reader
- mig 327 (`drop_dead_appliance_id_index`) — dead-index removal
- `tests/test_no_compliance_bundles_appliance_id_writes.py` —
  build-time AST gate

## Change log

- 2026-05-16 — initial — #122 Phase 1 closure. Companion to
  mig 326 + mig 327 + AST CI gate.

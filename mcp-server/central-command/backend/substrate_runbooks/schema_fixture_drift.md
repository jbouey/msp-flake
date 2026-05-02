# schema_fixture_drift

**Severity:** sev3
**Display name:** Prod schema differs from deployed code's fixture

## What this means (plain English)

The substrate compares prod's actual `information_schema` against the
JSON fixture (`tests/fixtures/schema/prod_columns.json`) shipped with
the currently-running backend code. They should match exactly. They
don't.

This is a **future-CI signal-accuracy** problem, not a runtime
problem. The deployed code is functioning normally; some queries
might fail later if a new code path tries to use a column that
doesn't exist (or a removed column that the fixture still claims
exists). The CI gate `test_sql_columns_match_schema` prevents new
deploys with drift — so this firing means drift slipped past CI
historically OR a manual SQL change was made on prod.

## Root cause categories

- **Manual SQL ALTER on prod outside the migration system**. Bypasses
  CI. Look at admin_audit_log for unexplained schema operations.
- **Migration applied but fixture forward-merge missed**. PR shipped
  the migration without updating prod_columns.json in the same diff.
  The audit-closure cycle on 2026-05-02 hit this twice (mig 271).
- **Fixture commit reverted but migration stayed**. Rare; usually
  caught at CI gate but possible if someone --force-pushed.
- **Drift between production replicas if you have multiple prod DBs**.
  Not applicable today (single-DB), but flag for the future.

## Immediate action

1. Look at the violation `details` field — it names one (table, column)
   per row with `direction = fixture_only` (in fixture, not prod) or
   `prod_only` (in prod, not fixture).

2. **If `direction = prod_only`:** prod has a column the fixture
   doesn't. Likely cause: migration applied without fixture update.
   Run the fixture-regen pipeline (see `tests/test_sql_columns_match_schema.py`
   docstring) and ship as a fixture-only commit. Example:

   ```bash
   ssh root@178.156.162.116 "docker exec mcp-postgres psql -U mcp -d mcp -tAc \"
     SELECT json_object_agg(table_name, columns) FROM (
       SELECT table_name, json_agg(column_name ORDER BY column_name) AS columns
       FROM information_schema.columns
       WHERE table_schema = 'public' AND table_name NOT LIKE 'pg_%'
       GROUP BY table_name) s\"" | python3 -m json.tool > \
     mcp-server/central-command/backend/tests/fixtures/schema/prod_columns.json
   ```

   Then `git diff` to confirm only the expected adds, commit,
   push.

3. **If `direction = fixture_only`:** fixture has a column prod
   doesn't. Likely cause: column was dropped in a migration but
   nobody removed it from the fixture. Same regen pipeline; the
   removed column will drop out of the fixture.

4. **If both directions are present** for the same table: investigate
   first. Could indicate a partial migration or a manual ALTER.
   Check `admin_audit_log` for the time window when drift began.

## Verification

- After committing the fixture update + push: deploy lands; substrate
  re-checks; violation row gets `resolved_at` populated on next 60s
  tick.
- CLI: `ssh root@178.156.162.116 "docker exec mcp-postgres psql -U mcp -d mcp -c \"SELECT * FROM substrate_violations WHERE invariant_name='schema_fixture_drift' AND resolved_at IS NULL\""`

## Escalation

This is sev3 — operator-priority "today's queue, not now." If the
drift is large (>10 columns) or spans multiple tables, treat as a
dba escalation: investigate whether a manual SQL session ran without
proper change control.

## Related runbooks

- `bg_loop_silent.md` — if the substrate engine itself is wedged it
  can stop reporting drift; cross-check.
- `partition_maintainer_dry.md` — partition operations are a common
  place for fixture drift to appear (new partitions add columns to
  the parent's child set, but partition children aren't in
  prod_columns.json so generally fine).
- The `test_sql_columns_match_schema.py` CI gate is the upstream
  prevention mechanism. If this invariant fires repeatedly, the gate
  is being bypassed somewhere — investigate.

## Change log

- **2026-05-02:** Created. Followup #49 from the AI-independence audit
  (Diana adversarial-audit recommendation). Closes the class that bit
  Session 214 audit cycle TWICE (mig 271 forward-merge required
  manual fixture edits).

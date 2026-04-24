# synthetic_l2_pps_rows

**Severity:** sev3
**Display name:** Synthetic L2-* runbook_id rows in platform_pattern_stats

## What this means (plain English)

`platform_pattern_stats` is the aggregation table that the flywheel's
Step-4 promotion loop reads to decide which cross-client patterns deserve
to become platform-wide L1 rules. Rows here should only carry
**canonical** runbook_ids (L1-*, LIN-*, WIN-*, MAC-*, NET-*, RB-*, ESC-*).

L2-prefixed runbook_ids are **synthetic planner-internal markers** that
leak in from legacy January 2026 `execution_telemetry` rows. The Step-3
aggregation INSERT in `background_tasks.py:1189` filters them out via
`AND et.runbook_id NOT LIKE 'L2-%'`, so they should never appear.

## Root cause categories

- **The filter regressed** — someone refactored the aggregation SQL and
  dropped the `NOT LIKE` clause. Verify the filter is still in the
  deployed code.
- **A new INSERT path was added** — someone wrote a second code path
  that INSERTs into `platform_pattern_stats` without applying the same
  filter. Grep for `INSERT INTO platform_pattern_stats` across the
  backend.
- **External resurrection** — `pg_restore` from an old backup,
  manual psql INSERT, or a database migration that replayed old data.
  Not a code bug but worth knowing.

Historical context: this invariant was added after Session 210
(2026-04-24) found 2 L2- rows resurrected from an earlier migration-237
cleanup despite the filter being in code. The resurrection mechanism
was never identified, so this invariant exists to make the NEXT
reappearance loud.

## Immediate action

1. **Cleanup:**
   ```sql
   DELETE FROM platform_pattern_stats WHERE runbook_id LIKE 'L2-%';
   ```
   Safe and idempotent. Rows have `distinct_orgs=1` and fail the
   `>= 5` promotion threshold anyway — they are never candidates.

2. **Verify the filter is deployed:**
   ```
   docker exec mcp-server grep -n "NOT LIKE 'L2-%'" /app/dashboard_api/background_tasks.py
   ```
   Expected: `1189: AND et.runbook_id NOT LIKE 'L2-%'`. If missing,
   the filter regressed — track down the commit that removed it.

3. **Audit recent INSERT paths:**
   ```
   grep -rn "INSERT INTO platform_pattern_stats" mcp-server/central-command/backend/
   ```
   Expected: exactly ONE hit at `background_tasks.py:1158`. If more,
   the new path needs the same filter.

4. **Check for a restore event:**
   ```
   # On VPS
   journalctl -u postgresql --since "7 days ago" | grep -E "pg_restore|COPY"
   ```

## Verification

- The invariant auto-resolves within 60s of deleting the rows.
- Sanity query:
  ```sql
  SELECT COUNT(*) FROM platform_pattern_stats WHERE runbook_id LIKE 'L2-%';
  ```
  Expected: 0.

## Escalation

If the rows return within 48h after cleanup, this is a regression of
the Session 210 fix. Page the owner of `background_tasks.py` and
inspect the Step-3 aggregation code path for a subtle regression
(e.g., parametrized SQL that drops the LIKE clause under certain flags).

## Related runbooks

- `flywheel_ledger_stalled.md` — same subsystem (flywheel promotion
  path); different failure mode.
- `l2_decisions_stalled.md` — also in the L2 planner family; these
  synthetic rows were a downstream symptom of the same issue that
  drove the 2026-04-12 L2 kill switch.

## Change log

- **2026-04-24** — initial. Session 210 investigation found 2 L2- rows
  resurrected despite migration 237 + the filter. Rows re-deleted.
  Invariant shipped to make next resurrection loud.

# partition_maintainer_dry

**Severity:** sev1
**Display name:** Next-month partition missing on critical table

## What this means (plain English)

Four tables in this schema are **monthly partitioned**:

| Table                  | Partition class    | Migration |
|------------------------|--------------------|-----------|
| `compliance_bundles`   | Evidence chain     | 138       |
| `portal_access_log`    | HIPAA audit log    | 138       |
| `appliance_heartbeats` | Liveness ledger    | 121       |
| `promoted_rule_events` | Flywheel ledger    | 181       |

`partition_maintainer_loop` (and `heartbeat_partition_maintainer_loop`
for `appliance_heartbeats`) run daily and create the next 3 months of
child partitions idempotently via `CREATE TABLE IF NOT EXISTS …
PARTITION OF`.

This invariant fires when **next month's partition is missing** on
ANY of those tables. Without next-month partitions, INSERTs targeting
next month land in the `<table>_default` partition. That partition
exists as a safety net, but:

- Default-partition rows are NOT range-pruned — every query scans them.
- Auditor-kit queries against `compliance_bundles` slow proportionally
  to the bloat in the default partition.
- For tables WITHOUT a default partition (rare but possible), the
  INSERT fails entirely with `no partition of relation found for row`.

## Why this matters (architectural)

`_supervised` would auto-restart these loops on **exceptions**, but a
**stuck await** (asyncpg pool exhaustion, deadlock) is not an exception.
The loop hangs, the supervisor sees nothing, and the dashboard pins to
"all-clear." The new `bg_loop_silent` sev2 invariant from Block 2
catches the loop-side; THIS invariant is the outcome-layer counterpart.

Sev1 because: the evidence chain (`compliance_bundles`) depends on
partition health for query performance, which auditors observe.

## Root cause categories

- **`partition_maintainer_loop` is silently stuck.** Cross-check
  `bg_loop_silent` for `partition_maintainer` or
  `heartbeat_partition_maintainer`.
- **Schema drift in the partition naming convention.** This invariant
  pattern-matches against four conventions (see SQL in `assertions.py
  ::_check_partition_maintainer_dry`); a migration that changed the
  convention without updating this invariant would false-fire here
  AND silently mask real partition gaps. **Desired failure mode.**
- **DBA manually dropped a future partition.** Migration 151's
  DELETE-blocking trigger on `compliance_bundles` would prevent
  blanket DELETE; partition DROP is a different code path and could
  succeed. Audit-log will show.
- **Disk-pressure path** that prevented `CREATE TABLE` from
  committing. Check VPS disk + pg_stat_database.

## Naming conventions

The invariant matches these substring patterns in child names:

| Parent table         | Pattern (next month = 2026-05) |
|----------------------|-------------------------------|
| `compliance_bundles` | `2026_05`                     |
| `portal_access_log`  | `2026_05`                     |
| `appliance_heartbeats` | `y202605`                   |
| `promoted_rule_events` | `202605`                    |

A child whose name contains the substring is considered "covers
that month." Loose match is intentional — partial-month or
backfill-style partitions still satisfy.

## Immediate action

1. **Confirm the gap on prod:**
   ```sql
   SELECT parent.relname AS parent_table,
          array_agg(child.relname ORDER BY child.relname DESC) AS children
     FROM pg_inherits i
     JOIN pg_class parent ON parent.oid = i.inhparent
     JOIN pg_class child ON child.oid = i.inhrelid
    WHERE parent.relname IN ('compliance_bundles','portal_access_log',
                              'appliance_heartbeats','promoted_rule_events')
      AND child.relname NOT LIKE '%_default'
    GROUP BY parent.relname;
   ```

2. **Identify which table is missing next month.** The
   `details.parent_table` of the violation row names it directly.

3. **Manually create the missing partition** to unblock writes
   immediately:
   ```sql
   -- Example for compliance_bundles 2026-05:
   CREATE TABLE IF NOT EXISTS compliance_bundles_2026_05
     PARTITION OF compliance_bundles
     FOR VALUES FROM ('2026-05-01 00:00:00+00') TO ('2026-06-01 00:00:00+00');
   ```
   Use the same naming convention the existing children use.

4. **Restart mcp-server** to rearm the maintainer loop:
   ```
   ssh root@<vps> "docker compose -f /opt/mcp-server/docker-compose.yml \
     restart mcp-server"
   ```

5. **Per non-operator partner posture:** substrate exposes; operator
   decides BAA disclosure. Partition lag does not corrupt evidence;
   it degrades query performance — typically NOT a customer-facing
   incident.

## Verification

- Panel: invariant resolves on the next 60s tick after the partition
  is created.
- CLI: re-run query (1) — should now show a child name containing the
  next-month substring.

## Escalation

Sev1 — operator action within the workday. NOT paging-class because
the `_default` partition catches writes; no data is lost. Sustained
firing > 7 days warrants engineering follow-up on why the maintainer
loop won't restart.

## Related runbooks

- `bg_loop_silent.md` — sibling sev2 for the loop-side; will fire on
  `partition_maintainer` or `heartbeat_partition_maintainer` if the
  loop is stuck (cross-correlate)
- `evidence_chain_stalled.md` — fires if compliance_bundles INSERT
  fails entirely (more severe — no default partition, or default is
  full)
- `compliance_packets_stalled.md` — sibling sev1 for monthly
  attestation aggregation (vs this per-bundle partition health)

## Change log

- 2026-05-01 — created — Session 214 Block 4 P1 closure round-table.
  Closes the post-c270bb76 audit Fork 1 P1: "Partition-maintainer
  drought is uninstrumented — only compliance_bundles outcome-layer
  covered; 3 other partitioned tables are blind."

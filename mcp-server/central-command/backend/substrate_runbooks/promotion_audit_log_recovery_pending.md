# promotion_audit_log_recovery_pending

**Severity:** sev1
**Display name:** Promotion audit log dead-letter queue has unrecovered rows

## What this means (plain English)

An L1 rule promotion happened (operator clicked Approve, fleet_order
was issued, the rule is now live) but its row in
`promotion_audit_log` — the WORM-style append-only audit table that
HIPAA §164.312(b) requires for chain-of-custody — did NOT land. The
flywheel_promote Step 7 savepoint caught the INSERT failure
(partition missing, CHECK violation, etc.), continued the
promotion, and routed the audit payload to the
`promotion_audit_log_recovery` dead-letter queue (Migration 253).
This invariant fires the moment any row in that queue is unrecovered.

This is a HIPAA-relevant durability event. Until recovered, the
chain of custody for "who promoted what when" has a gap. Every
minute the queue stays non-empty, the gap widens.

## Root cause categories

- **Partition missing.** `promotion_audit_log` is partitioned by
  month. `partition_maintainer_loop` runs daily creating the next
  3 months ahead — but a long mcp-server outage or a lifespan
  failure can leave the current month's partition uncreated. INSERT
  fails with `no partition of relation "promotion_audit_log" found
  for row`.
- **CHECK violation drift.** Three-list lockstep
  (`promotion_audit_log` event_type CHECK + Python EVENT_TYPES +
  transition matrix) — adding a new event_type in code without the
  CHECK update raises `check_violation`.
- **Disk pressure / replication lag.** Less common; surfaces as
  `out_of_disk` or `serialization_failure`.

## Immediate action

- **Inspect the queue.**
  ```
  ssh root@178.156.162.116 'docker exec mcp-postgres psql -U mcp -d mcp -c \"
    SELECT id, queued_at, rule_id, site_id, failure_class, failure_reason
      FROM promotion_audit_log_recovery
     WHERE recovered = FALSE
     ORDER BY queued_at\"'
  ```
  Group by `failure_class`. If they're all the same class
  (e.g. `PartitionedTableNoPartition`) the root cause is one fix
  + a single recovery run. Different classes = investigate
  separately.

- **Fix the root cause first.** Recovering before the underlying
  issue is fixed will just re-queue the same rows.
  - PartitionedTableNoPartition →
    `CREATE TABLE promotion_audit_log_YYYYMM PARTITION OF
     promotion_audit_log FOR VALUES FROM (...) TO (...);`
    Then trigger `partition_maintainer_loop` manually.
  - CheckViolationError → audit the Python EVENT_TYPES vs DB
    CHECK; align via migration. Run `test_three_list_lockstep_pg`.

- **Run the recovery script.**
  ```
  python3 scripts/recover_promotion_audit_log.py --dry-run
  python3 scripts/recover_promotion_audit_log.py --apply
  ```
  Idempotent. Each row's `recovered=true` flip happens AFTER the
  successful INSERT into `promotion_audit_log`. The
  `recovery_audit_log_id` column points at the eventual real audit
  row.

- **DO NOT manually flip `recovered=true`.** The trigger blocks
  UPDATEs on the audit-payload columns but allows the recovery
  flag — bypassing the script means you flip the flag without
  the audit INSERT actually succeeding. That's a phantom recovery
  and breaks the chain anyway. Use the script.

## Verification

- Substrate clears on the next 60s tick once
  `COUNT(*) FILTER (WHERE recovered = FALSE) = 0`.
- Spot-check: pick a recovered row and verify the corresponding
  `promotion_audit_log` row exists with the same rule_id +
  approximate queued_at.

## Escalation

- If the queue grows AT ALL between two consecutive ticks (not
  just non-empty — actually growing), there's an active failure
  source. Open the dashboard's Flywheel Intelligence panel; the
  approve flow is dropping audit rows in real time. Roll back
  the most recent flywheel_promote / promotion_audit_log
  change before more rows pile up.
- If a customer auditor specifically requests an attestation
  range that overlaps with unrecovered queued_at timestamps,
  recovery is not optional — defer the attestation until the
  queue is clean.

## Related runbooks

- `flywheel_ledger_stalled` — covers the FLEET-ORDER side of the
  same promote path. When BOTH this AND
  `flywheel_ledger_stalled` fire = the entire promote path is
  silently broken.
- `evidence_chain_stalled` — analogous chain-of-custody gate for
  appliance evidence bundles.

## Change log

- 2026-04-28 — created — Session 212 round-table P0 finding.
  Migration 253 added the queue table; this invariant pages on
  any row left in it. Pre-existing audit-loss class (savepoint
  swallow on `promotion_audit_log` INSERT failure) is now visible
  instead of silent.

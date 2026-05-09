# compliance_bundles_trigger_disabled

**Severity:** sev1
**Display name:** Chain-of-custody trigger DISABLED — integrity guard degraded

## What this means (plain English)

The `compliance_bundles_no_delete` trigger on the partitioned
evidence-bundle table is not in the `ENABLE ALWAYS` state. The
trigger's job is to REJECT every DELETE/UPDATE attempt on the
table — it's the last-line database defense against an operator
accidentally bulk-truncating the customer's evidence chain.

When this fires, every minute the trigger is non-ALWAYS is a
minute of customer-visible tamper-evidence integrity risk.

## Root cause categories

- An adversarial test or one-shot cleanup script ran
  `ALTER TABLE compliance_bundles DISABLE TRIGGER ALL` and the
  cleanup aborted before re-enabling.
- A migration changed the trigger to `ENABLE` (without ALWAYS) —
  meaning it would not fire under `session_replication_role='replica'`.
- An operator psql session manually disabled it for debugging
  and forgot to re-enable.

## Immediate action

```sql
-- Identify all parent + partition tables where the trigger isn't ALWAYS
SELECT n.nspname || '.' || c.relname AS qualified_table,
       t.tgname,
       t.tgenabled
  FROM pg_trigger t
  JOIN pg_class c ON c.oid = t.tgrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE t.tgname = 'compliance_bundles_no_delete'
   AND t.tgenabled <> 'A';

-- For each row returned:
ALTER TABLE <qualified_table>
ENABLE ALWAYS TRIGGER compliance_bundles_no_delete;
```

If the invariant fired during a known-active adversarial test and
the operator is mid-cleanup, the cleanup script must include
`ENABLE ALWAYS TRIGGER` BEFORE its final commit/exit. This is the
discipline encoded in `audit/multi-tenant-phase1-concurrent-write-stress-2026-05-09.md`.

## Verification

Substrate engine re-checks every 60s. Once all parent + partitions
report `tgenabled='A'`, this invariant clears.

## Escalation

If the trigger keeps flipping back to non-ALWAYS, find the
migration or script that's running `DISABLE TRIGGER` and fix it.
Persistent non-ALWAYS state is a P0 — escalate to engineering
on-call and consider a substrate kill-switch on customer-visible
write paths until the trigger is reinstated.

## Related runbooks

- `substrate_sla_breach.md` — meta-invariant; will fire if this
  invariant stays open >4h (sev1 SLA).

## Related

- Phase 1 audit: `audit/multi-tenant-phase1-concurrent-write-stress-2026-05-09.md` F-P1-3
- mig <which one ships compliance_bundles_no_delete trigger>
- `~/.claude/projects/-Users-dad-Documents-Msp-Flakes/memory/feedback_critical_architectural_principles.md` (chain-of-custody invariant)

## Change log

- **2026-05-09:** Created. Phase 1 multi-tenant audit F-P1-3 closure.

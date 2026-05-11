# l1_resolution_without_remediation_step

**Severity:** sev2
**Display name:** L1 resolution without remediation step

## What this means (plain English)

An incident has `resolution_tier = 'L1'` (the L1 deterministic-rule
path was the resolver) AND `status = 'resolved'`, but no row in
`incident_remediation_steps` references the incident. Integrity gap
between the resolution tier and the relational audit-step record —
`resolution_tier='L1'` is the customer-facing "auto-healed" label,
so a missing relational step is a false claim on the audit chain.

Migration 137 moved remediation tracking from
`incidents.remediation_history` JSONB to the relational
`incident_remediation_steps` table. The JSONB column was not dropped
but is no longer written to; ground truth is the relational table.

Customer-facing surfaces (`compliance_packet.py:1132`,
`partners.py:1392-1394`, `routes.py:2476-2525`) count
`resolution_tier='L1'` as "auto-healed." When this invariant fires,
the auto-healed metric overstates the platform's remediation rate.

## Root cause categories

- **Auto-resolve race (hypothesized, Phase 2 pending):**
  `sites.py` checkin handler's auto-resolve path runs when a drift
  scan returns clean. It sets `resolution_tier='L1'` without writing
  a relational step. Meanwhile, the daemon's healing_executor.go
  `ReportHealed` callback writes the relational step via
  `agent_api.py:1248-1262`. If the auto-resolve fires first, the
  relational step never gets written.
- A code path in `agent_api.py` set `resolution_tier='L1'` but did
  not dispatch an order or write `incident_remediation_steps`.
- An incident was tier-set manually (DB surgery) for ops reasons
  without inserting a paired `incident_remediation_steps` row.
- L1 rule fired but daemon failed to call `ReportHealed` (network
  gap, daemon crash).

## Immediate action

1. Identify the affected incidents:

   ```sql
   SELECT i.id,
          i.site_id,
          i.incident_type,
          i.resolved_at,
          i.dedup_key
     FROM incidents i
     LEFT JOIN incident_remediation_steps irs
       ON irs.incident_id = i.id
    WHERE i.resolution_tier = 'L1'
      AND i.status = 'resolved'
      AND i.resolved_at > NOW() - INTERVAL '24 hours'
      AND irs.id IS NULL
    ORDER BY i.resolved_at DESC;
   ```

2. Check which site is bleeding orphans. The chaos-lab
   (`north-valley-branch-2`) is the canonical test site and may
   contain 1000+ orphans by design until Phase 3 ships. The paying
   customer site (`north-valley-branch-1`) MUST be 0 — a non-zero
   count there is a customer-notification event.

3. For each non-chaos-lab affected incident, check daemon logs for
   the corresponding host:

   ```bash
   ssh root@<vps> 'docker logs mcp-server 2>&1 | grep "<incident_pk>"' | head -20
   ```

   Look for `ReportHealed called` or `dispatch_order_for_incident`
   log lines. Their absence indicates the daemon never reported
   completion.

4. **If the auto-resolve race is the culprit** (Phase 2 finding):
   the long-term fix is Phase 3 — reclassify auto-resolve writes as
   `resolution_tier='monitoring'` with `details->>'monitoring_reason'='auto_clean_no_runbook'`.
   Until Phase 3 ships, the orphans are loud-visible via this
   invariant.

## Verification

After remediation:

```sql
SELECT COUNT(*) AS gap_count
  FROM incidents i
  LEFT JOIN incident_remediation_steps irs
    ON irs.incident_id = i.id
 WHERE i.resolution_tier = 'L1'
   AND i.status = 'resolved'
   AND i.resolved_at > NOW() - INTERVAL '24 hours'
   AND irs.id IS NULL;
```

Should trend toward `0` after Phase 3 lands. Chaos-lab orphans will
be re-labeled `monitoring`; customer sites should remain at `0`.

## Escalation

If a paying-customer site shows orphans:
- P1 customer-notification event under Maya's "active false claim on
  the audit chain" framing
- Open counsel-question TaskCreate for §164.528 retroactive
  disclosure obligation
- Page on-call substrate engineer

If chaos-lab orphans exceed 2000 in a 24h window:
- Phase 2 root cause investigation is overdue; escalate to a
  sprint priority

## Related runbooks

- `l2_resolution_without_decision_record.md` — sibling sev2 (L2 path)
- `l3_resolution_without_human_escalation_record.md` — (future)

## Related

- Round-table: Session 219 L1-orphan investigation 2026-05-11
- Canonical write path: `agent_api.py:1248-1262`
  (relational step INSERT on daemon ReportHealed callback)
- Migration: 137 (table introduction), 151 (immutability triggers)
- CI gate: `tests/test_l1_resolution_requires_remediation_step.py`
- Module: `mcp-server/central-command/backend/sites.py` checkin
  handler (suspected auto-resolve race origin)
- Daemon: `appliance/internal/daemon/healing_executor.go:644`

## Change log

- 2026-05-11: invariant introduced. Prod sample at introduction:
  1131 of 2327 L1 resolutions (49%) on chaos-lab lacked
  `incident_remediation_steps`. Paying customer site had zero
  exposure in the 30-day window. Sev2 chosen because the L1 label
  is customer-visible but no customer was actively affected.

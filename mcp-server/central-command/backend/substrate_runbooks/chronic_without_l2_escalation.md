# chronic_without_l2_escalation

**Severity:** sev2
**Display name:** Chronic incident pattern without L2 escalation

## What this means (plain English)

A site has a recurring incident pattern (`incident_recurrence_velocity.is_chronic = TRUE`,
freshly computed in the last 24h) for some `(site_id, incident_type)`, but the
flywheel has NOT escalated it to the L2 LLM planner in the same window — there
is no row in `l2_decisions` with `escalation_reason IN ('recurrence',
'recurrence_backfill')` for that site + incident-type in the last 24h.

Dashboard.tsx:764 ("Incident types recurring 3+ times in 4 hours bypass L1 and
go to L2 for root-cause analysis") is the customer-facing contract. When this
invariant fires, the platform is silently breaching that contract — chronic
patterns are detected on the rollup but the in-flight detector never routes
them to L2. The flywheel cannot learn for the affected class.

## Root cause categories

- **Detector partitioning regression (the original bug, fixed 2026-05-12).**
  The recurrence-count `SELECT COUNT(*) FROM incidents WHERE appliance_id = ...`
  at `agent_api.py` partitioned by `appliance_id` instead of `site_id`. On
  multi-daemon sites (north-valley-branch-2: 3 daemons scanning one target host)
  the per-daemon count never crossed the `>= 3` threshold even when the
  cross-daemon count was 5+. See SECURITY_ADVISORY_2026-05-12_RECURRENCE_DETECTOR_PARTITIONING.
- **Velocity-loop stall.** The `background_tasks.py::recurrence_velocity_loop`
  populates `incident_recurrence_velocity` on a ~5min cadence. If the loop is
  wedged (exception caught at `:1193-1194`), `computed_at` ages past the
  detector's 10-minute freshness gate; the detector falls back to "no
  recurrence" and the L1 path runs. The sibling sev3 invariant
  `recurrence_velocity_stale` is the canonical surface for that class — if it
  is firing too, fix that first.
- **`l2_decisions` write failure.** `record_l2_decision()` raises mid-flight
  and the new `l2_decision_recorded` gate (Session 219 mig 300) correctly
  refuses to set `resolution_tier='L2'`. The incident escalates to L3 instead.
  In that case this invariant + `l2_resolution_without_decision_record`
  (different shape) may both surface — investigate the L2 path's exception.
- **`record_l2_decision()` succeeded with a non-recurrence `escalation_reason`.**
  The L2 path ran but the reason value used was `'normal'` or `'l1_failed_fallback'`,
  not `'recurrence'`. Code path bug in the routing decision.

## Immediate action

1. Identify the affected `(site_id, incident_type)` pairs:

   ```sql
   SELECT v.site_id,
          v.incident_type,
          v.resolved_4h,
          v.resolved_7d,
          v.is_chronic,
          v.computed_at
     FROM incident_recurrence_velocity v
    WHERE v.is_chronic = TRUE
      AND v.computed_at > NOW() - INTERVAL '24 hours'
      AND NOT EXISTS (
          SELECT 1
            FROM l2_decisions ld
            JOIN incidents i ON i.id::text = ld.incident_id
           WHERE i.site_id = v.site_id
             AND i.incident_type = v.incident_type
             AND ld.escalation_reason IN ('recurrence', 'recurrence_backfill')
             AND ld.created_at > NOW() - INTERVAL '24 hours'
      )
    ORDER BY v.computed_at DESC;
   ```

   Note: `l2_decisions` has NO `incident_type` column — the join goes through
   `incidents`. The substrate-engine assertion uses the same shape.

2. Confirm `recurrence_velocity_stale` is NOT also firing on the substrate
   panel. If it is, that runbook is the upstream fix.

3. Pick one affected pair and inspect the most-recent incident's logs:

   ```bash
   docker logs mcp-server 2>&1 | grep "<incident_id>" | head -40
   ```

   Look for: `recurrence_context` populated, `escalation_reason='recurrence'`
   set, and `record_l2_decision` called. Absence of any of these is the
   regression class.

4. **If the regression is a new code path** that bypasses the velocity-table
   read: open a P1 engineering ticket. The CI gate
   `tests/test_no_appliance_id_partitioned_recurrence_count.py` should also
   start failing — verify and tighten if needed.

5. **If `record_l2_decision` is raising:** check `l2_planner.py` connectivity
   to OpenClaw (`178.156.243.221`) and the LLM budget. The L2 path being
   structurally fail-closed is correct (Session 219 design); the fix is to
   restore the planner, not to bypass the gate.

## Verification

After remediation, the assertion-query should return zero rows. Wait for one
substrate tick (60s) — the invariant auto-clears.

Optional confirmation against the customer-visible dashboard: pick a
previously-affected `(site_id, incident_type)` and verify that the next
incident open routes to L2 (`resolution_tier='L2'` + `l2_decisions` row with
`escalation_reason='recurrence'` within the same minute).

## Escalation

If the assertion is open for >24h despite the velocity loop being healthy
(`recurrence_velocity_stale` clean) AND the L2 planner is healthy
(`l2_decisions_stalled` clean), there is a routing regression that needs
engineering attention same-day. Page the substrate on-call.

Customer-visible impact: the flywheel SLA dashboard shows "chronic" for
patterns that are silently not getting L2 root-cause analysis. Material
breach of the auditor-visible technical-control claim.

## Related runbooks

- `recurrence_velocity_stale.md` — upstream sev3; check first if both fire.
- `l2_resolution_without_decision_record.md` — sibling Session 219 class
  (catches L2-tier-without-decision-row; different shape).
- `l2_decisions_stalled.md` — fires when L2 has not run AT ALL in 48h.
- `l2_recurrence_partitioning_disclosed.md` — sev3 disclosure surface
  associated with the 2026-05-12 historical gap.

## Change log

- 2026-05-12 — invariant introduced (Session 220 P1 persistence-drift L2
  routing fix). Closes the forward-looking class for the
  `agent_api.py` detector partitioning bug. Sev2 because the customer-facing
  flywheel SLA claim depends on this routing working — silent under-delivery.
  Companion migration: `308_l2_escalations_missed.sql`. Disclosure:
  `SECURITY_ADVISORY_2026-05-12_RECURRENCE_DETECTOR_PARTITIONING.md`.
  References mig 300 (Session 219) for the symmetric decision-record gap.

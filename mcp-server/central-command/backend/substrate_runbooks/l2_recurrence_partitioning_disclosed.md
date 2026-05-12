# l2_recurrence_partitioning_disclosed

**Severity:** sev3
**Display name:** Recurrence-detector partitioning gap (historical disclosure)

## What this means (plain English)

This is an **INFORMATIONAL** invariant. It fires whenever the
`l2_escalations_missed` table holds one or more rows — those rows enumerate
historical incidents where the L2 LLM **should have run** under the
customer-facing flywheel SLA but did not, because of the recurrence-detector
partitioning bug fixed on 2026-05-12 (the detector counted per-`appliance_id`
on multi-daemon sites, slicing the count below the `>= 3` threshold).

The invariant **NEVER auto-resolves.** Disclosure IS the resolution.
Mirrors the `pre_mig175_privileged_unattested` sev3 pattern from Session 218
round-table 2026-05-08: an honest, persistent operator-visible reminder
that the customer-facing artifact `disclosures/missed_l2_escalations.json`
ships in the auditor kit and the associated security advisory is live.

When this invariant fires, no engineering action is required and no remediation
is possible. The right action is to confirm the disclosure surfaces are intact
(advisory + kit JSON section + customer notification per the Maya P0-C verdict).

## Root cause categories

- **Historical bug, fix already shipped (2026-05-12).** Forward-looking
  invariant `chronic_without_l2_escalation` (sev2) catches new occurrences.
  This sev3 surface exists solely to flag the historical-disclosure window.
- There is no "other" root cause. If the row count in `l2_escalations_missed`
  GROWS unexpectedly, that itself is a separate bug (the table is INSERT-ONLY
  and only written by migration 308's one-shot backfill — see CI gate
  `tests/test_l2_escalations_missed_immutable.py`).

## Immediate action

No action required. This invariant signals "disclosure surface is open."

If you are auditing during an enterprise security review:

- Confirm `docs/security/SECURITY_ADVISORY_2026-05-12_RECURRENCE_DETECTOR_PARTITIONING.md`
  is present and readable.
- Confirm the auditor kit `kit_version` is `2.2` or later (the version bump
  that ships the new disclosures section).
- Confirm the kit ZIP contains `disclosures/missed_l2_escalations.json` and
  the markdown advisory file.
- Confirm `tests/test_auditor_kit_deterministic.py` passes — the kit must
  remain byte-identical across consecutive downloads with no new rows in
  `l2_escalations_missed`.

If any of those four fail, that is a separate sev1/sev2 disclosure-surface
break — escalate to the substrate-on-call engineer.

## Verification

Row-count sanity check:

```sql
SELECT COUNT(*) AS disclosed_rows,
       MIN(recorded_at) AS first_recorded,
       MAX(recorded_at) AS last_recorded
  FROM l2_escalations_missed;
```

Expected at deploy time: ~320 rows aggregated to ~9 unique
`(site_id, incident_type)` pairs (per the 2026-05-12 prod-evidence sample).
The number does not change over time except via a one-shot operator-authored
migration (re-run of the historical detector for a different window).

## Escalation

There is no escalation path for this invariant. It is by design a sev3
informational surface. If you find yourself wanting to "fix" it by deleting
rows from `l2_escalations_missed`, STOP — the table is INSERT-ONLY via DB
trigger and any DELETE attempt raises an exception. The disclosure is the
contract; mutating it retroactively would be the exact forgery pattern
Maya P0-C verdict rejected (see `audit/maya-p0c-backfill-decision-2026-05-12.md`).

If the operations team decides the disclosure window should be closed (e.g.,
all affected customers have acknowledged the advisory in writing), the
correct path is a NEW migration that retires the invariant from
`ALL_ASSERTIONS` — not a row delete. The audit trail of the disclosure
persists in `admin_audit_log` regardless.

## Related runbooks

- `chronic_without_l2_escalation.md` — sibling sev2 invariant; the forward-
  looking gate that blocks regressions of this class.
- `recurrence_velocity_stale.md` — sibling sev3; catches velocity-loop
  outages that would silently kill L2 escalation again.
- `pre_mig175_privileged_unattested.md` — same disclosure pattern, prior
  precedent (Session 218 round-table 2026-05-08 RT-1.2).

## Change log

- 2026-05-12 — invariant introduced (Session 220 P1 persistence-drift L2
  routing fix). Mirrors `pre_mig175_privileged_unattested` shape. Companion
  migration `308_l2_escalations_missed.sql` creates the underlying table.
  Companion advisory `SECURITY_ADVISORY_2026-05-12_RECURRENCE_DETECTOR_PARTITIONING.md`.
  Sev3 because no operator action is possible; the invariant exists for
  honest persistent visibility per the round-table disclosure-first norm.

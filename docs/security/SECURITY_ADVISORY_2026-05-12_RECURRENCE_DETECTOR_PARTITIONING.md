# Security Advisory — OSIRIS-2026-05-12-RECURRENCE-DETECTOR-PARTITIONING

**Title:** Chronic-pattern recurrence detector mispartitioned by appliance on multi-daemon sites; L2 escalation gap
**Date discovered:** 2026-05-12 (caught by weekly P1 persistence-drift round-table audit)
**Date remediated:** 2026-05-12 (same day; detector switched to per-site partitioning, parallel disclosure table populated)
**Affected versions:** mcp-server commits prior to the 2026-05-12 detector-switch fix
**Severity:** MEDIUM (technical-control SLA gap; PHI handling intact; compliance_bundles signing chain intact)
**Status:** REMEDIATED + DISCLOSED + PARALLEL DISCLOSURE TABLE POPULATED (Maya P0-C verdict: Option B)

---

## Summary

The OsirisCare flywheel routes incidents that recur 3+ times in 4 hours away
from the deterministic L1 path and into the L2 LLM-planner path for
root-cause analysis. This routing rule is the customer-facing technical-
control claim on the Dashboard ("Incident types recurring 3+ times in 4
hours bypass L1 and go to L2 for root-cause analysis") and is part of the
flywheel SLA narrative auditors review when evaluating the platform's
self-healing posture.

On 2026-05-12 a weekly persistence-drift audit discovered that the in-flight
recurrence detector in `agent_api.py::report_incident` was counting recurring
incidents per **appliance** rather than per **site**. On multi-daemon sites
(an architecture pattern that has been live since the multi-appliance work
in Sessions 196-202), this caused the per-appliance count to never cross the
`>= 3` threshold even when the **cross-daemon** count for the same site
and incident-type was higher. The dashboard rollup
(`incident_recurrence_velocity`, populated by a parallel background loop
that DID group by site + incident_type) correctly flagged
`is_chronic = TRUE` for the affected patterns, but that signal never made
it into the in-flight routing decision.

The net effect: on `north-valley-branch-2` (a 3-daemon chaos-lab site that
exercises the multi-daemon partitioning case), ~320 L1 resolutions over 7
days for three check-types (`windows_update`, `defender_exclusions`,
`rogue_scheduled_tasks`) were **never escalated to L2** despite the
customer-facing SLA promising they should have been. The L1 path's symptom
fix was applied correctly each time; the gap is in the missing L2
root-cause analysis layer.

We are aware of **zero** customer reports affected by this gap and **zero**
evidence the gap was relied upon by an external auditor during the gap
window. The underlying compliance_bundles for the affected period were
**never lost, never altered, never re-signed.** PHI scrubbing at appliance
egress was unaffected; this is a routing bug in the central-command flywheel,
not in the appliance-side telemetry pipeline.

---

## Affected scope (verified on production database 2026-05-12)

- **Sites affected:** 1 directly observed (`north-valley-branch-2`) with the
  multi-daemon partitioning pattern at audit time. The class is latent on
  any future site with ≥2 daemons reporting the same incident type — the
  fix is forward-looking for the entire fleet.
- **Incident types affected:** `windows_update`, `defender_exclusions`,
  `rogue_scheduled_tasks` (the three with observed chronic patterns).
- **Affected period (observed):** Last 7 days at audit time; the parallel
  disclosure table aggregates last-30-days from the velocity rollup.
- **L2 escalation gap (observed):** ~320 individual L1 resolutions over 7
  days that should have triggered the chronic-pattern bypass. Aggregated by
  `(site_id, incident_type)` the gap is approximately 9 unique tuples.
- **Auto-promotion pipeline impact:** Zero `l2_decisions` rows with
  `escalation_reason='recurrence'` for the affected period meant the
  recurrence-auto-promotion loop (`background_tasks.py:1199`) had no input
  for these check-types, so the L2→L1 promotion path also could not run for
  this class.

What was NOT affected:

- **PHI scrubbing** at the appliance egress (`phiscrub` package): intact.
  This bug is downstream of the PHI boundary.
- **`compliance_bundles` signing chain**: intact. Ed25519 signatures, prev-
  hash links, OpenTimestamps proofs for all bundles emitted during the
  affected window verify byte-for-byte against the auditor-kit
  `verify.sh` script today exactly as they would have on the date of
  emission.
- **Dashboard chronic-pattern flag**: was CORRECT throughout the gap. The
  customer-facing "chronic" surface was reading the same
  `incident_recurrence_velocity` table that the fix now uses; the only
  inconsistency was between the dashboard signal and the routing decision.
- **L2-tier-without-decision-record class** (Session 219 mig 300): unrelated
  and unaffected.
- **Customer billing / contractual deliverables**: this gap does not touch
  HIPAA monthly compliance packets, BAA evidence, or any other customer-
  contracted artifact other than the technical-control claim about L2
  routing.

---

## Root cause

`mcp-server/central-command/backend/agent_api.py` had two callsites — the
new-incident branch and the dedup-reopen branch — both running a query of
the shape:

```sql
SELECT COUNT(*) FROM incidents
 WHERE appliance_id = :appliance_id      -- ← partitioning bug
   AND incident_type = :incident_type
   AND status = 'resolved'
   AND resolved_at > NOW() - INTERVAL '4 hours'
```

On a multi-daemon site reporting the same incident type from each daemon
(each tagged with its own `appliance_id`), the per-`appliance_id` count
slices the recurrence below the `>= 3` threshold. Meanwhile
`background_tasks.py::recurrence_velocity_loop` was correctly grouping by
`(site_id, incident_type)` and lighting `is_chronic = TRUE` on the
dashboard. The two code paths had inconsistent granularity. The fix
replaces both `COUNT(*)` callsites with a SELECT against
`incident_recurrence_velocity` keyed on `(site_id, incident_type)`,
unifying the granularity.

---

## What we did

1. **Detection (round-table audit, 2026-05-12):** Weekly persistence-drift
   audit cross-checked `incidents.resolution_tier='L1'` for chronic check-
   types against `l2_decisions.escalation_reason='recurrence'` rows. Found
   320 L1 resolutions in 7 days, zero recurrence escalations.
2. **Gate A adversarial review (fork, 2026-05-12):** Steve / Maya / Carol /
   Coach reviewed the 3-part proposed fix. Verdict: APPROVE-WITH-FIXES, 5
   P0s. Maya P0-C is the load-bearing one: backfilling synthetic
   `l2_decisions` rows would fabricate evidence of LLM root-cause analysis
   that never happened — the exact forgery pattern Session 218 rejected.
3. **Maya P0-C verdict (Option B):** Materialize the missed-escalations as
   a PARALLEL TABLE (`l2_escalations_missed`) outside `v_l2_outcomes`, NOT
   as synthetic rows in `l2_decisions`. Disclose via the auditor kit
   (`disclosures/missed_l2_escalations.json` + this advisory) + bump kit
   version 2.1 → 2.2. Audit chain stays immutable; auditors get a
   queryable artifact of the gap.
4. **Detector fix (2026-05-12):** Replaced both `COUNT(*)` callsites with
   a velocity-table SELECT keyed on `(site_id, incident_type)`. Added a
   10-minute freshness gate (`computed_at > NOW() - INTERVAL '10 minutes'`)
   to refuse routing on stale rollup data. Preserved the existing
   `recurrence_context` JSON shape (`recurrence_count_4h`,
   `recurrence_count_7d`, `message`) consumed by `l2_planner.py`.
5. **Parallel disclosure table populated (migration 308):**
   `l2_escalations_missed` created with INSERT-ONLY triggers; backfilled
   from `incident_recurrence_velocity WHERE is_chronic=TRUE AND
   computed_at > NOW() - INTERVAL '30 days'` aggregated by
   `(site_id, incident_type)`. Audit-log row recorded under
   `username='system:mig-308'`.
6. **Substrate invariants (2026-05-12):**
   - `chronic_without_l2_escalation` (sev2) — forward-looking gate; catches
     any future regression of the same class.
   - `l2_recurrence_partitioning_disclosed` (sev3) — informational
     disclosure surface; never auto-resolves (mirrors Session 218
     `pre_mig175_privileged_unattested` shape).
   - `recurrence_velocity_stale` (sev3) — single-point-of-failure surface;
     catches velocity-loop outages that would silently kill L2 escalation
     again (Steve P0-B Gate A close).
7. **CI gates (2026-05-12):**
   - `tests/test_no_appliance_id_partitioned_recurrence_count.py` — bans
     regression of the per-`appliance_id` partitioning shape.
   - `tests/test_l2_escalations_missed_immutable.py` — pins the INSERT-only
     contract on the disclosure table.
8. **Customer-notification (OPAQUE-mode, per Session 218 rule):** Sites
   with non-zero rows in `l2_escalations_missed` receive an opaque-mode
   email ("Service advisory — auditor kit update available"; body redirects
   to authenticated portal). No clinic names / counts / incident types in
   the SMTP channel.
9. **Kit version bump:** 2.1 → 2.2 across all four surfaces (X-Kit-Version
   header, chain_metadata, pubkeys_payload, identity_chain_payload,
   iso_ca_payload). Determinism contract preserved.

---

## Auditor verification

Any auditor with access to an affected site can independently confirm:

```bash
# 1. Download the auditor kit (kit_version should be 2.2 or later)
curl -H "Authorization: Bearer <token>" \
  https://api.osiriscare.net/api/evidence/sites/<site_id>/auditor-kit \
  -o site-kit.zip

# 2. Verify the chain (verify.sh ships in the ZIP)
unzip site-kit.zip && cd site-kit && bash verify.sh

# 3. Inspect the disclosure JSON section (new in kit_version 2.2)
cat disclosures/missed_l2_escalations.json | python3 -m json.tool

# 4. Inspect this advisory file shipped in every kit
cat disclosures/SECURITY_ADVISORY_2026-05-12_RECURRENCE_DETECTOR_PARTITIONING.md
```

To obtain the canonical list of missed escalations for your site, see
`disclosures/missed_l2_escalations.json` in this kit. The file is sorted
deterministically by `(last_observed_at, site_id, incident_type)` and is
byte-identical across consecutive kit downloads with no new rows
(`tests/test_auditor_kit_deterministic.py` enforces). Empty array when
your site has no rows.

Auditors verifying the post-fix behavior can confirm forward-looking
correctness by querying `l2_decisions` directly:

```sql
SELECT COUNT(*) FROM l2_decisions
 WHERE escalation_reason = 'recurrence'
   AND created_at > '2026-05-12'::date;
```

Non-zero (and growing as multi-daemon sites encounter chronic patterns)
confirms the routing fix is operational.

---

## Why we are disclosing this

Two reasons:

1. **The flywheel SLA is a contractual technical-control claim.** Dashboard
   copy stating that 3+ recurrences in 4h bypass L1 to L2 is part of the
   auditor-visible flywheel narrative. A silent breach of that claim — even
   one whose underlying evidence remains intact — is the exact kind of gap
   our standing disclosure-first commitment exists to publish.
2. **Standing commitment from Session 203.** When we discovered the Merkle
   batch-id collision (OSIRIS-2026-04-09), we publicly committed to
   disclose every future evidence-integrity or attestation event the same
   way. This is the fourth such disclosure (after Merkle, packet auto-gen,
   and the pre-mig-175 privileged orders). Each carries the same posture:
   publish the gap, the cause, the fix, and the verification script.

We will continue to disclose. If you operate a site under our substrate and
have questions about this advisory, please contact support@osiriscare.net
through the authenticated portal (per our opaque-mode email rule, we do not
identify clinic/org names in SMTP — the portal is the canonical channel).

---

**Discovered:** 2026-05-12 by weekly P1 persistence-drift audit
**Backfilled (parallel disclosure table):** 2026-05-12 via migration 308
**Disclosed:** 2026-05-12
**Advisory ID:** OSIRIS-2026-05-12-RECURRENCE-DETECTOR-PARTITIONING

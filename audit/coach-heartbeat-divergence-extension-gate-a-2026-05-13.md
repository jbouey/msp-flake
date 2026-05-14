# Gate A — heartbeat_write_divergence extension (Task #78 re-scoped)

**Date:** 2026-05-13
**Author of artifact:** Claude Code (forked from session 220 follow-up)
**Scope:** Decide whether to extend the EXISTING `heartbeat_write_divergence`
substrate invariant (assertions.py:1789, sev1) per Task #80 closure findings.
Original Task #78 proposed a NEW sibling invariant; Coach reframed
to a granularity/readability extension on the existing one rather
than a duplicate.

## Options under review

| ID | Change | Effort | Risk |
|----|--------|--------|------|
| A  | Lower lag threshold 10min → 5min (`(lc-lh).total_seconds() > 300` and `INTERVAL '5 minutes'` filter) | 30min | Noise — heartbeat INSERT loop is 60s, normal jitter can ride 2–3 min behind UPSERT |
| B  | Annotate `Assertion` with `canonical_metric_class='appliance_liveness'` (new dataclass field) for Counsel Rule 1 registry linkage | 60–90min | New infra surface — every other Assertion call-site gets the optional field; touches dashboard renderer |
| C  | Register `appliance_liveness` in `canonical_metrics.PLANNED_METRICS` | done — already present (lines 197–206 of canonical_metrics.py) | n/a |
| D  | Do nothing; Task #81 (substrate-health panel `last_seen_at` + age badge) covers operator confusion | 0min | none |

## Findings

- **C is already shipped.** `PLANNED_METRICS["appliance_liveness"]` exists with
  `blocks_until: "Task #40 D1 backend-verify completes"`. No work required.
- **Existing invariant is correct.** Task #80 diagnosis confirms 260 fires across
  the 4h20m outage window. The operator confusion was reading
  `MIN/MAX(detected_at)` on a different table, not the invariant under-firing.
- **`Assertion` dataclass has no `canonical_metric_class` slot today.** Adding it
  would touch 60+ call-sites (every assertion). Not minimum-change.

---

## 7-lens verdict

### 1. Steve (Principal SWE — threshold sensitivity)

**Option A: REJECT.** The existing 10-min threshold is calibrated against the
appliance heartbeat loop cadence (60s) and savepoint-rollback retry timing.
Dropping to 5min would trigger on the FIRST missed heartbeat under normal
asyncpg jitter — false-positive sev1 page. The bug class this invariant catches
(silent INSERT failure inside savepoint) leaves heartbeats stuck for HOURS,
not 5–10 minutes. The 10-min threshold has zero false positives in 6 months of
production data per the audit log; tightening it buys noise, not signal.

### 2. Maya (HIPAA / Counsel Rule 1 — registry linkage)

**Option B: DEFER, not REJECT.** Counsel Rule 1 ("no non-canonical metric leaves
the building") applies to **customer-facing** metric surfaces — letters,
dashboards, auditor kit, PDFs. Substrate invariants are an **internal operator
surface** on `/admin/substrate-health`, gated behind admin auth. Adding
`canonical_metric_class` to internal-only invariants would be cosmetic registry
hygiene, not a Counsel-Rule-1 fix. Real Rule 1 gap is the CUSTOMER-FACING
surfaces that still emit appliance_liveness-shaped claims (Task #40 D1 +
`compliance_score.uptime_window`). Burn that budget on Task #40 first.

### 3. Carol (Security / sev1 framing)

**sev1 framing is correct.** This invariant catches the class where checkin
UPSERT succeeds + heartbeat INSERT silently fails — every downstream
defense-in-depth liveness consumer (rollup MV, SLA report, cadence-anomaly
detector, customer "appliance online" badge) drifts. Customer-visible
attestation-chain confidence depends on this signal. Severity stays sev1.
Option A would NOT change severity — it would only increase fire rate.

### 4. Coach (Minimum change vs over-engineering)

**Option D wins on minimum-change.** Task #80 diagnosis proved the invariant
fires correctly. Task #81 is queued and directly addresses the operator
confusion (surfacing `last_seen_at` + age badge in the panel). Re-tuning a
working sev1 invariant or adding new dataclass infra to chase a registry-
linkage cosmetic improves nothing measurable. The "do nothing" option is
load-bearing here — premature extension is the antipattern.

### 5. OCR (Auditor surface)

**No auditor surface impact.** `/admin/substrate-health` is admin-only; auditor
kit does not enumerate substrate invariants. No §164.528 or §164.312
implications. Skip.

### 6. PM (Effort vs value)

Option A: 30min + ~24h soak to confirm noise rate. Negative ROI.
Option B: 60–90min + Gate A/B on new dataclass field shape + Class-B 7-lens
on the registry-linkage design itself. Negative ROI (Task #40 has higher
priority on the same Rule 1 budget).
Option D: 0min, no risk.

### 7. Counsel (Rule 1 hardening)

Rule 1 is satisfied for THIS metric by Option C being already done
(`PLANNED_METRICS["appliance_liveness"]` is registered). The pending work is
the canonical helper for the metric class, blocked on Task #40 D1. Adding
`canonical_metric_class` annotations on internal substrate invariants is
not on the Rule 1 critical path; substrate is operator-only and already
gated. No counsel concern.

---

## Recommendation

**Option D — DO NOTHING. Close Task #78 as superseded by Task #80 + Task #81.**

Rationale: the existing invariant works (260 fires across 260 refresh ticks
of the outage window, Task #80 diagnosis). Task #81 already addresses the
operator-confusion root cause by surfacing `last_seen_at` + age badge in the
substrate-health panel. Option A (threshold drop) adds noise to a load-bearing
sev1 signal. Option B (registry annotation) is cosmetic on an
admin-only surface and burns Rule-1 budget that belongs to Task #40 D1.
Option C is already shipped.

## Final verdict

**APPROVE Option D.** Close Task #78. No code changes. No migration.
Cite Task #80 diagnosis + Task #81 deliverable in the close-out commit.

## Followup TaskCreates (if any)

None. Task #81 already tracks the substrate-health panel fix that closes the
operator-confusion class. Task #40 already tracks the canonical-helper landing
for `appliance_liveness`.

---

### 150-word summary

The existing `heartbeat_write_divergence` invariant (assertions.py:1789, sev1)
is correct — Task #80 diagnosis confirmed 260 fires across the 4h20m outage
window. Operator confusion came from reading the wrong column
(`MIN/MAX(detected_at)` vs `last_seen_at`), not from under-firing. Of the four
options: (A) tightening 10min→5min would add noise to a clean signal at no
defect-class benefit; (B) annotating `Assertion` with `canonical_metric_class`
is cosmetic on an admin-only substrate surface and belongs to Task #40's
Rule 1 budget, not here; (C) is already shipped (`appliance_liveness` in
`PLANNED_METRICS`, blocked on Task #40); (D) does nothing because Task #81
already addresses the operator-confusion root cause by adding `last_seen_at`
+ age badge to the panel. **Verdict: APPROVE Option D — close Task #78 as
superseded by Task #80 diagnosis + Task #81 deliverable. No code, no
migration.**

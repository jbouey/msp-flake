# F6 phase 2 enforcement — DEFERRED

**Status:** DEFERRED to a dedicated, rested session with HIPAA specialist
**Filed:** 2026-04-30 (Session 214 round-table verdict — unanimous SHIP_FOUNDATION_SLICE)
**Reopen criterion:** see "Pre-conditions" below — completion of any one item triggers re-evaluation
**Reviewer-suggested follow-up cadence:** ~14 days after foundation slice ships, check `flywheel_federation_candidate_daily` for clean observation data

---

## What's deferred

The eligibility-update + rollout enforcement path for Tier 1 (org-aggregated)
and Tier 2 (platform-aggregated) federation. Specifically:

- Modifying `_flywheel_promotion_loop` Step 2 to evaluate tiers 1 + 2
  beyond Tier 0
- WRITE path that flips `aggregated_pattern_stats.promotion_eligible=TRUE`
  for org-aggregated or platform-aggregated patterns
- `safe_rollout_promoted_rule` integration at scope='org' or 'platform'
- Cross-org WRITE-path testing (the READ path already has property test)

The READ path (Option A) shipped Session 214 commit `a91794ce` and is
verified in prod with the cross-org isolation property test passing in CI.

---

## Why deferred — four open questions

**Q1 — Does Tier 1 violate non-operator posture?**
- Reviewer consensus: **NO** at the policy level (partner has BAA authority within org)
- BUT the cross-org JOIN at the WRITE layer must be airtight; the engineering risk is sev0 privacy class

**Q2 — Does Tier 2 violate non-operator posture?**
- Reviewer consensus: **YES** — Osiris making cross-org generalization decisions
  and auto-deploying via fleet_orders to a different BAA-bound entity is
  operator-class behavior. Even with PHI scrubbed, the *decision* crosses
  the BAA boundary.
- The Privileged-Access Chain of Custody section in CLAUDE.md codifies the
  pattern Tier 2 would need to mirror: per-org consent-config + named human
  actor + attestation bundle + chain-linked evidence. **None exists yet for
  federation rollout.**

**Q3 — Does Tier 2 require §164.528 (HIPAA disclosure accounting) work?**
- Reviewer consensus: **YES**, with one nuance — the L1 rule artifact itself
  is probably not PHI under §164.514(b) safe harbor, but the act of
  training-on-A-and-deploying-to-B is a disclosure of derived information
  under recent HHS guidance.
- This call requires HIPAA counsel review. Specifically: (a) is BAA
  language sufficient or does it need amendment to authorize inter-customer
  pattern federation? (b) does §164.528 accounting need to be per-patient
  or can it be reduced to a logging requirement?
- **NOT a sleep-deprived engineer's call.**

**Q4 — Does the engineering-discipline framework alone justify deferral?**
- Reviewer consensus: **YES** — even if all three policy questions came
  back green, the prior round-table verdict was explicit: "F6 phase 2
  enforcement requires dedicated cross-org HIPAA round-table — NOT a
  sleep-deprived session."

---

## Pre-conditions before any enforcement commit lands

1. **2-3 weeks of calibration data** from the foundation slice's
   `flywheel_federation_candidate_daily` snapshot table (yet to be shipped)
2. **Cross-org WRITE-path property test** mirroring the existing READ-path
   test in `tests/test_flywheel_eligibility_queries.py::test_cross_org_isolation_property`
3. **`federation_disclosure` event_type** added to the `promoted_rule_events`
   three-list lockstep (Assertion + _DISPLAY_METADATA + runbook + CHECK)
4. **Auditor-kit surface** for federation events — extend
   `chain.json["site_canonical_aliases"]` pattern with a federation-event
   section
5. **Substrate invariant `cross_org_federation_leak` (sev1)** wired and
   firing on any cross-org WRITE that lacks proper consent attestation —
   must be live BEFORE the feature flag flips

---

## Required round-table participants (when this is reopened)

- Principal SWE (engineering risk + cross-org JOIN correctness)
- DBA (schema + audit chain)
- **Security/HIPAA specialist** (NEW — was not in any of the 12 prior round-tables; non-negotiable)
- PM (non-operator posture + product policy)
- **Outside HIPAA counsel review** of the disclosure-accounting question — required before Tier 2 specifically; the engineer/AI cannot make this call internally

---

## What to ship in the meantime — Foundation Slice (4-6h, well-rested)

The reviewer's recommended forward path that does NOT cross any policy
boundary:

### 1. Minimal schema add

```sql
ALTER TABLE promoted_rule_events
    ADD COLUMN tier_at_promotion TEXT;  -- nullable, no CHECK yet
```

Do NOT add `promoted_rules.rollout_scope` enum yet — that's a federation
rollout decision and belongs in the dedicated session.

### 2. Read-only operator endpoint

`GET /api/admin/flywheel/federation-candidates?tier=1|2&dry_run=true`

Returns per-org-or-platform:
- Count of patterns clearing current seed thresholds
- Small sample (pattern_signature, total_occurrences, success_rate,
  distinct_sites/orgs)

**Strictly count + summary; no rule_id-level disclosure across orgs.**

This is the calibration data the dedicated session will need to set
real thresholds.

### 3. Daily snapshot table

```sql
CREATE TABLE flywheel_federation_candidate_daily (
    snapshot_date DATE NOT NULL,
    tier_name TEXT NOT NULL,
    client_org_id TEXT,  -- nullable for tier 2 (platform)
    candidate_count INTEGER NOT NULL,
    p50_success_rate FLOAT,
    p95_success_rate FLOAT,
    PRIMARY KEY (snapshot_date, tier_name, client_org_id)
);
```

Background loop snapshots once/day. Gives the dedicated round-table 2-3
weeks of observation data before they have to commit to thresholds.

### Estimated effort

4-6h end-to-end. Round-table on completion. Zero policy exposure (no
cross-org WRITE, no rollout, no fleet_order issuance).

---

## Reviewer affirmations of today's work

> *"Migration 261's CHECK constraints (`flywheel_tier_org_isolation_required`,
> `flywheel_tier_distinct_orgs_required_when_calibrated`) already encode the
> answer to today's question at the schema level. The author of that migration
> was protecting the future-self who would be tempted to flip enforcement at
> 4 AM. That self-discipline is exactly the substrate-credibility posture."*

> *"Option A (read-only) shipped with a property test for cross-org isolation.
> That's the right gate-pattern to mirror for the WRITE path."*

> *"The fact that this question is being asked at all — instead of just
> shipping — is the answer."*

---

## Next-session checklist (when this card is picked up)

- [ ] Schedule round-table with HIPAA specialist (NEW participant)
- [ ] Confirm 2-3 weeks of calibration data exists in
      `flywheel_federation_candidate_daily`
- [ ] HIPAA counsel review of Q3 (disclosure-accounting)
- [ ] Draft cross-org WRITE-path property test
- [ ] Draft `federation_disclosure` event_type lockstep entries
- [ ] Draft `cross_org_federation_leak` substrate invariant
- [ ] Draft auditor-kit federation-event surface
- [ ] Then and only then: write the enforcement commit

# Class-B 7-lens Gate A — `canonical_metric_drift` substrate invariant

**Date:** 2026-05-13
**Reviewer:** fork (general-purpose) — author cannot self-grade per Session 219 two-gate lock-in
**Design under review:** `audit/canonical-metric-drift-invariant-design-2026-05-13.md`
**Phase:** Task #50 Phase 2 (Counsel Rule 1 runtime half)

---

## Per-lens verdict

| # | Lens | Verdict | Headline P0/P1 |
|---|---|---|---|
| 1 | Engineering (Steve) | **BLOCK** | P0-E1: helper signature mismatch; P0-E2: bundle ≠ score; P0-E3: no `period_start/end`/`signed_at` columns exist |
| 2 | HIPAA auditor (OCR surrogate) | APPROVE-WITH-FIXES | P1-A1: needs framing as "helper-semantic-drift detector", NOT chain-integrity proof |
| 3 | Coach (no double-build) | **BLOCK** | P0-C1: `signed_data` column already stores attested payload — mig 314 column is double-build |
| 4 | Attorney | APPROVE-WITH-FIXES | P1-L1: Article 3.2 claim is hash-chain + Ed25519 — this invariant is supplementary, not load-bearing |
| 5 | Product manager | APPROVE-WITH-FIXES | P1-P1: 10% sample rate + 7d window = ~10min p99 detection; alert routing must be ops-only |
| 6 | Medical-technical | APPROVE | operator-internal only; never reaches clinic admins (correct posture) |
| 7 | Legal-internal (Maya + Carol) | APPROVE-WITH-FIXES | P1-LM1: substrate-runbook copy must avoid "ensures"/"guarantees"; runbook not yet drafted |

**Overall: BLOCK** — 3 P0s in Lenses 1 + 3. Redesign required before Phase 2a implementation begins.

---

## Lens 1 — Engineering (Steve)

### P0-E1: Helper signature does not accept `period_start` / `period_end`

Design §3 calls:
```python
await compute_compliance_score(conn, site_ids=[r["site_id"]],
                               window_start=r["period_start"],
                               window_end=r["period_end"])
```

Verified at `compliance_score.py:157`. The actual signature is:
```python
async def compute_compliance_score(conn, site_ids, *, include_incidents=False,
                                   window_days: Optional[int] = DEFAULT_WINDOW_DAYS)
```

There is NO `window_start`/`window_end` — only a relative `window_days: int`. The helper's SQL filters `cb.checked_at > NOW() - ($2::int * INTERVAL '1 day')`. **You cannot re-run the helper against a fixed past `[period_start, period_end]` window without extending the helper's API.** Mechanism C as drafted does not compile.

**Fix:** Either (a) extend `compute_compliance_score` with absolute `window_start`/`window_end` kwargs (additive — preserves existing callers) AND a separate code path emitting absolute-bounded SQL; OR (b) accept Mechanism C is impossible without API surgery and redesign.

### P0-E2: One compliance_bundles row ≠ a "compliance score"

Verified at `evidence_chain.py:1430` (INSERT) — a `compliance_bundles` row represents ONE scan from ONE appliance at ONE `checked_at`. Columns: `site_id, bundle_id, bundle_hash, check_type, check_result, checked_at, checks (jsonb), summary (jsonb), agent_signature, signed_data, chain_position, chain_hash, ots_status`. There is no `period_start`/`period_end`/`signed_at` and NO attested aggregate score.

`compute_compliance_score()` aggregates across MANY bundles (latest-per-(site, check_type, hostname) over 30 days). A single bundle does not carry the helper's output — it carries raw `checks[]`. The invariant's framing "the bundle's attested metric value" is a category error: bundles attest raw check results, not the aggregated score.

**Implication:** `attested_compliance_score` (mig 314) would not be populated by the existing daemon→backend flow. The score is computed AT DISPLAY TIME by aggregating bundles; nothing currently "attests" the score in the chain. To make Mechanism C work, the backend would need to (a) compute the score at bundle-INSERT time over the historical 30d window for that site, (b) persist it on the bundle, (c) ensure the value is included in the signed payload (else it's not chain-attested — it's just a column). That is meaningful net-new infrastructure, NOT a small additive column.

### P0-E3: Schema columns referenced do not exist

Design §3 SQL uses `cb.signed_at`, `cb.period_start`, `cb.period_end`, `cb.deleted_at`. None of these exist on `compliance_bundles`. Closest:
- `signed_at` → `created_at` (set at INSERT) OR `checked_at` (scan time).
- `period_start`/`period_end` → no equivalent.
- `deleted_at` → no soft-delete; compliance bundles are immutable by design.

### Tick cost math (Steve check)

Design §6 claims 1500 bundles × ~50ms helper call = 75s/tick → 10% sample (~7.5s).

Actual cost is worse than that estimate:
- `compute_compliance_score(site_ids=[single_site], window_days=30)` profiled at 2634ms for the 155K-bundle org and 632ms for 7-day window in `compliance_score.py:99-104` docstring.
- 50 customers × 30 bundles × ~600ms (7-day window equivalent) = **900 seconds/tick**.
- At 10% sample: ~90s/tick. Still dominates the 60s tick budget — substrate engine will spiral.
- Per-assertion `admin_transaction` (Session 220 commit `57960d4b`) means each call holds its own conn — 150 concurrent acquisitions on a pool sized for 25.

**Sample rate must be ≤2%** OR the helper needs a fast-path that skips the JSONB unnest when called with a single site_id + small window. Design's 10% rate is 5× too aggressive.

### Verdict: BLOCK — P0-E1, P0-E2, P0-E3 each independently sufficient.

---

## Lens 2 — HIPAA auditor surrogate (OCR posture)

The invariant as framed claims to materialize Master BAA Article 3.2 "cryptographic-attestation-chain commitment." OCR would not accept this framing because:

- Article 3.2's chain integrity is ALREADY proven by Ed25519 + `prev_hash`/`chain_hash` (verified at `evidence_chain.py:1422-1426`) + OTS Merkle anchoring. The chain itself is the artifact.
- This invariant detects "did our helper's semantics change without re-attestation?" That is a **product-quality** signal, not a chain-integrity signal. The chain is still cryptographically intact even when this invariant fires.

**P1-A1:** Re-frame the invariant as "helper-semantic-drift detector" — an internal QA signal showing customer-facing display values haven't silently diverged from the values a recent chain attested. NOT "chain-integrity proof." Update Master BAA mapping language accordingly.

OCR auditor would find value in the signal (defense-in-depth) but would NOT accept it as the load-bearing evidence of Article 3.2.

### Verdict: APPROVE-WITH-FIXES (re-framing required).

---

## Lens 3 — Coach (no over-engineering / no double-build)

### P0-C1: `signed_data` column already exists — mig 314 is double-build

Verified at `migrations/012_store_signed_data.sql:10`:
```sql
ALTER TABLE compliance_bundles ADD COLUMN IF NOT EXISTS signed_data TEXT;
```

This column already stores the canonical signed payload (TEXT, the bytes the Ed25519 signature covers). `evidence_chain.py:1432, 1451` confirms: every bundle INSERT writes `stored_signed_data` into this column.

The user's question Q3 was: "does mig 314's `attested_compliance_score` column duplicate any existing chain-payload field?" **YES.** If the design wants a chain-attested score, the correct location is INSIDE `signed_data` (where it would actually be Ed25519-signed). A separate plain `NUMERIC(5,1)` column is:
- Not cryptographically attested (the signature doesn't cover it).
- Mutable post-INSERT (no trigger lockdown).
- Therefore proves nothing the invariant claims to prove.

**Fix paths:**

- **Option A (cleaner):** include `compliance_score` in the JSON payload that gets signed. Then the invariant parses `signed_data` JSON and compares. No new column. No double-build. Requires daemon-side change (it computes per-scan, not per-30d-window — see P0-E2).
- **Option B (admit the limitation):** the score is computed at DISPLAY time by the backend, not at bundle-creation time by the daemon. There is no "chain-attested score" to compare to today. Either build that pipeline as a separate workstream OR drop Mechanism C and accept that Counsel Rule 1's runtime half is a sampling-table design (Mechanism B) and is heavier — not Mechanism C lite.

### P1-C2: Sample-rate 10% chosen by feel, not measurement

Design §6 sets `SAMPLE_RATE = 0.1` without a tick-budget calculation. Per Lens 1 P0-E1, ≤2% is the actually-feasible ceiling for 50 customers. Need an explicit budget: "≤500ms per tick at p95" and reverse-derive sample rate.

### P0-C3: Two helpers, one invariant — false ergonomic

User-question (f): per-class scope. The design covers `compliance_score` only. The other 3 metrics (`baa_on_file`, `runbook_id_canonical`, `l2_resolution_tier`) are NOT scores — they are booleans/enums/IDs. Mechanism C's "re-run helper against chain input + compare numeric tolerance 0.1" does NOT generalize. Each needs its own invariant shape. The design pretends the same machinery extends; it doesn't. Either rename to `canonical_compliance_score_drift` (honest narrow scope) OR design the cross-class abstraction now.

### Verdict: BLOCK — P0-C1 is mechanical double-build; P0-C3 is dishonest scope-naming.

---

## Lens 4 — Attorney

Article 3.2 of `MASTER_BAA_v1.0_INTERIM.md` commits the platform to a "cryptographic-attestation chain" — Ed25519 + hash-chain + OTS anchoring. This invariant is supplementary; it does NOT carry the legal load of Article 3.2.

**P1-L1:** The substrate runbook copy MUST NOT say "this invariant proves Article 3.2 compliance." The invariant proves the helper hasn't drifted, which is a customer-trust property, not a BAA-commitment property. Conflating the two opens legal exposure if the invariant ever fires and someone misreads it as a BAA breach.

**P1-L2:** "Helper-output ≠ chain-attested-value" violation severity must NOT be auto-disclosed to customers without legal review — could be misconstrued as a § 164.402 incident even when it's a code-drift bug.

### Verdict: APPROVE-WITH-FIXES (runbook copy must clearly scope the legal claim).

---

## Lens 5 — Product manager

**Operational impact:**
- 50 customers × monthly helper deploys = ~5-10 expected Class-A fires per quarter (helper changed, chain not yet rolled forward). All are false-positives from a "is something wrong" perspective.
- Alert routing MUST be ops-channel ONLY (not customer-facing notifications). Substrate invariants today fire to ops-substrate-health panel — this is correct.

**P1-P1:** Sample rate 10% + 7-day recency = ~10 minute p99 detection latency. Acceptable for QA-grade signal; would NOT be acceptable for a security-grade signal. The runbook must state "operator-investigation-only — no customer impact assumed."

**P1-P2:** Auto-resolution wording in §1 ("Class B → Class A transition self-heals") is wrong direction — should be "Class A → resolves when chain re-attests." Class B is the no-drift case.

### Verdict: APPROVE-WITH-FIXES.

---

## Lens 6 — Medical-technical

Operator-internal substrate-engine output; never surfaces to clinic admins per `/admin/substrate-health` scoping. Correct posture. No medical-side concerns.

### Verdict: APPROVE.

---

## Lens 7 — Legal-internal (Maya + Carol)

**P1-LM1:** Substrate runbook (`substrate_runbooks/canonical_metric_drift.md`) does not yet exist. When written, it MUST be scanned against the banned-word list:
- No "ensures", "prevents", "protects", "guarantees" (Session 199 legal-language rule).
- "PHI never leaves" — N/A here (operator-internal signal).
- Use "detects", "surfaces", "indicates potential" — never "proves."

**P1-LM2:** Per Session 218 round-table 2026-05-06 `.format()` template gate — if the violation `details["interpretation"]` string ever flows into a customer-visible artifact (it shouldn't — operator-internal), the f-string templates in §3 lines 113-125 need `{{`/`}}` escaping audit. Today's operator-internal scope is OK.

### Verdict: APPROVE-WITH-FIXES (runbook copy gate at Gate A on the runbook itself).

---

## Cross-cutting verifications

### Mechanism C vs Mechanism B — auditor preference

Given Lens 1 P0-E2 + Lens 3 P0-C1, **Mechanism C as drafted does not exist today**. To make it real requires:
- Daemon computes 30d historical score at scan-time (new pipeline)
- That score lands inside `signed_data` (chain-attested) — NOT a separate column
- Invariant parses signed_data JSON

That is a non-trivial daemon-side change that touches the cryptographic chain. It is auditor-grade IF built that way, but the design as written is shape-only.

Mechanism B (response-sampling table) is heavier infrastructure but does not touch the cryptographic chain. **Auditor would prefer B for this Phase 2 because:**
- No chain-touch reduces risk of breaking Article 3.2 commitments.
- Sample table is explicit operator-internal scope (no BAA implication).
- Faster to ship correctly than Mechanism C's daemon surgery.

**Recommendation:** Mechanism B for Phase 2; revisit Mechanism C in a later phase IF the chain-attested-score pipeline is built for other reasons (e.g. quarterly practice compliance summaries — mig 292).

### The `signed_payload` JSONB question

There is NO `signed_payload` JSONB column today. There is `signed_data TEXT` (mig 012) that holds the bytes the Ed25519 signature covers. The `summary` JSONB column might be a target for including a score, but is also NOT currently chain-attested (column-level — depends on whether `signed_data` includes a serialization of `summary`; verify before any design assumes it).

**Action:** before Phase 2a redesign, READ `evidence_chain.py` lines ~1300-1420 to determine what bytes are actually included in `stored_signed_data`. If `summary` JSONB is serialized into `signed_data` at sign-time, a score could be added to `summary` and become chain-attested for free. If not, score-attestation requires daemon-side change.

### Tolerance 0.1

Verified: `compute_compliance_score` returns `round(total_passed / total * 100, 1)` (line 380). 1-decimal precision. Tolerance 0.1 = exactly one decimal-place delta = LARGEST acceptable single rounding step. For asymmetric edge cases (`total` differs between chain and now because new bundles arrived even within same window), the helper output could drift by more than 0.1 legitimately — counted-bundles is a function of NOW(), not period.

**Recommendation:** if Mechanism C is pursued, helper must accept an absolute window AND tolerance should be 0.5 to account for the "new bundles arrived" class. Or freeze the input set by bundle-ID list, not by time.

### Per-class scope — recommendation

Rename Phase 2 invariant to `canonical_compliance_score_drift` (narrow honest name). Design separate invariants for the other 3 classes:

| Metric | Invariant shape |
|---|---|
| `compliance_score` | Mechanism B sample-table comparison |
| `baa_on_file` | bool — compare `is_baa_on_file_verified()` vs `baa_signatures.acknowledged_at IS NOT NULL` (read-only — table is source of truth — invariant is trivial) |
| `runbook_id_canonical` | string-equality on telemetry-runbook-id vs catalog-runbook-id — substrate already has `_check_unbridged_telemetry_runbook_ids` (assertions.py:1051), extend it |
| `l2_resolution_tier` | substrate already has `_check_l2_resolution_without_decision_record` (assertions.py:1101) — extend with display-time comparison |

Three of the four already have invariant precedents; this design's single-metric scope is correct. Just rename it to match.

---

## Recommended Phase 2a + 2b implementation order

**REVISED — pending user-gate decision on Mechanism B vs C:**

### If Mechanism B (recommended):
- **Phase 2a (~3-5 days):** sampling-table schema (mig 314 → `customer_metric_response_samples`), endpoint decorator on the 3 customer-facing surfaces (dashboard, reports, per-site), retention sweep job, Gate A on the schema + decorator.
- **Phase 2b (~2-3 days, after 7d of sample population):** `_check_canonical_metric_drift` reads sample table vs current helper output, runbook + DISPLAY_METADATA + pg-test fixtures, Gate A + Gate B.

### If Mechanism C (NOT recommended without redesign):
- **Phase 2a-prelim (~5-7 days):** redesign chain-attested-score pipeline; daemon-side change to compute 30d historical score per scan; serialize into `signed_data`; Gate A on the chain-touching change is its own multi-lens review (Article 3.2 implications).
- **Phase 2a (~2-3 days):** mig 314 column OR signed_data JSON parsing helper; sign-path verification.
- **Phase 2b:** invariant ships after 7d population.

### Either path:
- Rename to `canonical_compliance_score_drift`.
- Separate invariants for baa/runbook/l2 classes; do NOT pretend Mechanism C generalizes.

---

## Open questions for user-gate

1. **Mechanism B vs C** — coach + Steve recommend B; C requires chain-touching daemon work that is out of scope for Phase 2 timeline.
2. **Helper API extension** — if C is chosen, extend `compute_compliance_score` with `window_start`/`window_end` absolute kwargs? Approve before any code lands.
3. **Sample rate budget** — explicit p95 tick-cost budget (recommend ≤500ms per assertion)?
4. **Per-class scope** — APPROVE renaming Phase 2 invariant to `canonical_compliance_score_drift` + designing the other 3 class invariants in a parallel sprint?
5. **Tolerance** — 0.5 (operational, accounts for input-set drift) vs 0.1 (strict, only valid with frozen input)?

---

## Final recommendation

**BLOCK** — design as written does not compile (P0-E1), references nonexistent schema columns (P0-E3), proposes a column that is cryptographically meaningless next to existing `signed_data` (P0-C1), and mischaracterizes the legal load of Article 3.2 (P1-L1).

### Top 3 P0s

1. **P0-C1 (Coach):** Drop mig 314's `attested_compliance_score` column. `signed_data` (mig 012) already exists. If a score must be chain-attested, it goes INSIDE signed_data via the daemon's sign-path — not a sibling column. Mechanism B (sampling table) is the lower-risk Phase 2 path; Mechanism C requires daemon surgery to be real.
2. **P0-E1 (Steve):** `compute_compliance_score` does not accept `window_start`/`window_end`. Mechanism C's "re-run helper against chain's input" is not implementable without API extension. Extend the helper OR drop Mechanism C.
3. **P0-E2 (Steve):** A `compliance_bundles` row attests raw `checks[]` for ONE scan — not an aggregated 30-day score. The design's premise that "the chain attested a score" is false. Either build the score-attestation pipeline (daemon-side, scoped change to Article 3.2-touching code) OR redesign with a sampling-table approach that does NOT pretend the chain already carries the value.

Resubmit redesign for Gate A round 2 once these three are addressed and user-gate questions are answered.

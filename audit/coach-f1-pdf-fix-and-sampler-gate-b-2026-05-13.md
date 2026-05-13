# Gate B — F1 PDF score-extraction fix + canonical-metrics sampler integration

**Commit:** `9c64cd28` `fix(F1): dataclass score extraction + sampler integration (Task #67 Phase 2b)`
**Date:** 2026-05-13
**Reviewer:** Class-B 7-lens fork (Steve / Maya / Carol / Coach / OCR / PM / Counsel)
**Files changed:** 3 (+124 lines)
**Tests added:** 3 (test_f1_pdf_score_extraction.py)
**Pre-push allowlist:** updated (`.githooks/pre-push:127`)
**Full sweep result:** 247 passed, 0 skipped — clean

## 250-word summary

Two related shipped changes at `client_attestation_letter.py::_compute_facts`: (1) a latent bug fix where the F1 PDF score extraction used a legacy `isinstance(score_result, dict)` guard against a `ComplianceScore` dataclass return — the guard was always False, `sc` was always None, every F1 row persisted `overall_score=None`. The fix switches to dataclass attribute access `score_result.overall_score`. (2) canonical_metrics_sampler integration captures `(window_days=DEFAULT_PERIOD_DAYS, include_incidents=False, classification="customer-facing", endpoint_path="f1:attestation_letter")` matching the actual call kwargs for Phase 2c drift detection.

**Critical bug-impact finding:** the F1 PDF template (`templates/attestation_letter/letter.html.j2`) does NOT reference `overall_score` — the customer-facing PDF body is qualitative (period dates, site/appliance/workstation counts, BAA practice name, presenter brand). The `overall_score` field is persisted to `compliance_attestation_letters.overall_score` and stored in the Ed25519-signed canonical attestation payload (`_canonical_attestation_payload`), but does NOT render to clinic-visible text. Customer-visible impact: **none in PDF body**; downstream impact: every historical attestation row has `overall_score=NULL` in DB + signed payload. Auditors pulling the verify endpoint or DB queries see NULL where score should be.

**Sampler shape verification:** signature matches `sample_metric_response(conn, metric_class, tenant_id, captured_value, endpoint_path, helper_input, classification)` exactly. `tenant_id=str(client_org_id)` is correct (UUID string). `captured_value=float(overall_score) if overall_score is not None else None` correctly preserves None semantics. Soft-fail outer try/except is defensive in addition to sampler's internal soft-fail — belt-and-suspenders is appropriate at the highest-stakes emit-path.

**Verdict: APPROVE.**

---

## Per-lens verdicts

### 1. Engineering (Steve) — APPROVE

Verified at `client_attestation_letter.py:196-251`:

- `sc = score_result.overall_score` (line 219) — dataclass attribute access, correct shape against `ComplianceScore` (compliance_score.py:56-71, `overall_score: Optional[float]`)
- `isinstance(score_result, dict)` REMOVED (regression test pins this with `not in src`)
- Sampler integration uses **double-fallback lazy import** (relative then absolute) at lines 228-231 — matches the pattern at lines 198-201 for `compute_compliance_score`
- `tenant_id=str(client_org_id)` — line 235, UUID string coercion correct
- `captured_value=float(overall_score) if overall_score is not None else None` — line 237, None-preserving as sampler design specifies
- `helper_input` captures `site_ids` + `window_days=DEFAULT_PERIOD_DAYS` + `include_incidents=False` — matches the actual `compute_compliance_score(conn, site_ids, window_days=DEFAULT_PERIOD_DAYS)` call (include_incidents=False is the default the call relies on, correctly captured for Phase 2c recompute parity)
- `endpoint_path="f1:attestation_letter"` — distinct marker, separates from `/api/client/dashboard` samples
- `classification="customer-facing"` — fires substrate drift assertion in Phase 2c
- Outer `try/except: pass  # sampler is best-effort` — defensive even though sampler is internally soft-fail; appropriate at the highest-stakes emit-path
- Sampler call is INSIDE the `try:` that wraps `compute_compliance_score` — if compute fails before sampler runs, sampler is skipped (correct: no data to sample)

No engineering concerns.

### 2. Database (Maya) — APPROVE

- Sampler INSERT against `canonical_metric_samples` (parent table; partition routing handles canonical_metric_samples_2026_05 per mig 314)
- `tenant_id` column accepts UUID-string per sampler design
- `helper_input::jsonb` cast applied in INSERT (sampler line 93)
- No new DB schema in this commit; sampler infra shipped in Phase 2a (mig 313/314)

No DB concerns.

### 3. Security (Carol) — APPROVE

- F1 PDF is Ed25519-signed via `_sign_attestation(canonical)` — historical rows that stored `overall_score=NULL` in the signed payload are **immutable**. They remain NULL forever, signed cryptographically.
- Going-forward rows ship the real score. This is a behavior change; the attestation chain integrity is preserved (each row is signed at-issue; this commit doesn't mutate historical rows).
- Rule 5 (no stale doc as authority): historical PDFs are issued artifacts, not stale docs claiming authority. They are signed snapshots of state-at-time-of-issue. Not a Rule 5 concern.
- Sampler does NOT cross PHI boundary — `tenant_id=client_org_id`, no PHI captured.

### 4. Coach — APPROVE

Combined commit (bug fix + integration) is appropriate because:
- The bug fix DIRECTLY affects what the sampler captures. Without the fix, the sampler would record `captured_value=None` for every F1 emission forever, defeating Phase 2c drift detection entirely.
- The two changes touch adjacent lines (219 + 232-246).
- Commit message clearly separates the two changes with numbered sections.
- Regression tests pin BOTH changes: test 1 pins dataclass access, tests 2-3 pin sampler shape.
- Same composition as recent `2118f04c` precedent.

Not scope-creep.

### 5. Auditor (OCR) — APPROVE-WITH-OBSERVATION

**Customer-impact assessment:** I read the F1 PDF Jinja2 template at `templates/attestation_letter/letter.html.j2`. **`overall_score` is NOT rendered in the customer-visible PDF body.** Customers received PDFs with practice name, sites/appliances/workstations counts, period dates, BAA practice name, and presenter brand. They did NOT see a numeric score — neither as "—" nor "0%" nor "100%". The score lives in:
- `compliance_attestation_letters.overall_score` column (NULL for all historical rows)
- The Ed25519-signed canonical attestation payload (`overall_score: null` for historical rows)
- The `/verify` endpoint payload returns

**Auditor question — §164.524 / §164.528 implications:** None. The F1 PDF text explicitly says "This letter is audit-supportive technical evidence. It contains no patient-identifying information and is not part of any designated record set under §164.524. It is not a substitute for the practice's §164.528 disclosure accounting...". Customer-facing PDF body is unchanged.

**Observation (non-blocking):** Auditors querying `/verify` endpoint payload OR the DB row would see `overall_score=null` for historical letters. This is a defensible "no data" state given the canonical-score-was-not-extracted reality. Going-forward, real values populate. No backfill is appropriate (Ed25519-signed payload is immutable).

### 6. PM — APPROVE

- 1 bug fix + 1 integration + 3 regression tests + 1 allowlist entry = 4 cohesive units in 1 commit
- Same shape as recent shipped Phase 2b commits
- Commit message is well-structured with clear sections
- Aligns with Task #67 Phase 2b rollout sequence (12 emit-paths remaining tracked separately)

### 7. Counsel (in-house) — APPROVE

- **Rule 1 (no non-canonical metric):** F1 was nominally delegating to `compute_compliance_score` (the canonical helper, task #50 registry) but silently dropping the result. Post-fix, F1 actually USES the canonical value. The bug-fix-IS-the-Rule-1-fix — explicitly called out in comment block at lines 215-218. This is a Rule 1 closure, not just a bug fix.
- **Rule 2 (no raw PHI):** No PHI in sampler call. `tenant_id=client_org_id` (org-level identifier), `captured_value=float`, `helper_input={site_ids, window_days, include_incidents}`. Clean.
- **Banned words check on new comment block:** Searched for `ensures|prevents|protects|guarantees|audit-ready` — zero hits. Clean.
- **Rule 5 (no stale doc):** Historical PDFs are signed at-issue snapshots; not Rule 5 concern as discussed under Carol.

---

## Adversarial probes — results

| Probe | Result |
|---|---|
| `grep -B2 -A40 "score_result.overall_score"` shape verification | PASS — post-fix shape matches design |
| `pytest test_f1_pdf_score_extraction.py -v` | 3/3 PASS in 0.13s |
| F1 Jinja2 template `overall_score` rendering | NOT RENDERED in customer PDF body — bounded customer impact |
| `bash .githooks/full-test-sweep.sh` | rc=0, 247 passed, 0 skipped |
| `.githooks/pre-push` allowlist | `tests/test_f1_pdf_score_extraction.py` at line 127 |
| Sampler signature parity | All 7 kwargs match `sample_metric_response` definition |
| `DEFAULT_PERIOD_DAYS` referenced consistently | line 74 (def=30), 210 (compute call), 242 (helper_input), 344 (default param) |
| Banned-word scan on new comment block | Zero hits |

---

## Full pre-push sweep summary

```
✓ 247 passed, 0 skipped (need backend deps)
```

Sweep includes the new `test_f1_pdf_score_extraction.py` (verified at pre-push:127). No regressions in the 247-test corpus.

---

## Final verdict — APPROVE

**Rationale:**
1. Bug fix correctly switches to dataclass attribute access. Regression test pins the shape.
2. Sampler integration correctly captures `(window_days=DEFAULT_PERIOD_DAYS, include_incidents=False)` matching the actual call kwargs — Phase 2c drift detection will recompute with parity.
3. `tenant_id=str(client_org_id)`, `classification="customer-facing"`, `endpoint_path="f1:attestation_letter"` all correct.
4. Customer-visible PDF body does NOT render `overall_score` — bounded impact, no §164.524 / §164.528 concerns, no auditor-visible "wrong number" issue.
5. Counsel Rule 1 closure: F1 now actually uses the canonical helper's result instead of silently discarding it.
6. Pre-push allowlist updated. Full sweep clean (247 passed).
7. Same composition as recent shipped Phase 2b commits — combined-commit shape is appropriate given the dependency between the two changes.

**Followups (NOT blockers, all already tracked):**
- Task #64 — Phase 2c assertion implementation (`_check_canonical_compliance_score_drift`)
- Task #67 — 12 remaining emit-paths to integrate with sampler
- No new task needed for this commit; both halves are pinned by regression tests.

# Gate A — #41 Non-HIPAA Question Banks (SOC2/GLBA)

**Date:** 2026-05-17
**Reviewer:** fresh-context fork (general-purpose subagent, opus-4.7[1m])
**Verdict: APPROVE-WITH-FIXES**

## 2-line summary

Question-bank assets exist (soc2: 30q, glba: 25q) but are dead code — no endpoint, no allowlist entry for GLBA, partial/zero YAML mappings, silent HIPAA fallback footgun. **APPROVE-WITH-FIXES**: 4 P0s (kill silent fallback, add GLBA to allowlist, refresh stale docstring per Counsel Rule 5, extract single-source `SUPPORTED_FRAMEWORKS` constant + CI lockstep gate) + 4 P1s (YAML coverage GLBA 0→34, SOC2 18→34, framework_sync backfill, GLBA industry preset). No mig needed.

## Discovery — what actually exists

| Surface | State |
|---|---|
| `soc2_templates.py` | 30 questions across CC/A/C/PI/P. UNREFERENCED by any endpoint. |
| `glba_templates.py` | 25 questions. UNREFERENCED. |
| `framework_templates.py` (93 LOC) | Dispatcher with `get_assessment_questions(framework)` + `get_policy_templates(framework)`. ZERO call sites in backend. Dead code. |
| `frameworks.py:389/483` `valid_frameworks` | `{hipaa, soc2, pci_dss, nist_csf, cis}` — **GLBA absent from allowlist.** |
| `control_mappings.yaml` (34 checks) | HIPAA all 34, SOC2 18/34, **GLBA 0/34.** |
| `evidence_framework_mappings` (mig 013) | Table exists, scoring view (`v_control_status` mig 326) JOINs it. Backfilled HIPAA-only. |
| `compliance_frameworks.py:1-17` | Docstring claims 10 frameworks supported. Reality: only HIPAA has end-to-end binding. **Counsel Rule 5 violation (stale doc).** |

## Recommended scope (ships now)

1. **Wire `framework_templates.py` into a real endpoint** — `GET /api/frameworks/{framework}/assessment-questions` + `GET /api/frameworks/{framework}/policy-templates`, gated by `require_auth`, validated against the `valid_frameworks` allowlist.
2. **Add `glba` to `valid_frameworks` allowlist** (2 spots, frameworks.py:389 + 483).
3. **Backfill GLBA mappings** in `control_mappings.yaml` for all 34 checks; finish SOC2 16-check gap. Reuse existing yaml shape; no schema change needed.
4. **Loader → `evidence_framework_mappings`** — extend `framework_sync.py` (or add one-shot script) to backfill mappings for last-30d bundles for `soc2` + `glba` so `v_control_status` returns non-empty rows for those frameworks.
5. **Industry preset wiring** — financial-services preset → primary `glba`; SaaS → primary `soc2` (already in `frameworks.py:657-678` for SOC2, ADD GLBA row).

## Defers (anti-scope)

- Auditor kit per-framework PDF/ZIP (separate F-series ticket; auditor-kit contract is HIPAA-bound today, changing it is a Counsel Rule 9 determinism risk).
- POSTURE_OVERLAY.md non-HIPAA section (task #51 owns the overlay; reference it once shipped).
- Customer-facing "compliance score" surfaces — `compute_compliance_score()` is the canonical helper (Counsel Rule 1); MUST refactor BEFORE non-HIPAA framework % shows on dashboard. Out of scope for #41.
- BAA enforcement triad changes — GLBA/SOC2 are not CE-mutating; no new BAA gate needed.

## Per-lens verdict

- **Steve (architecture):** APPROVE. Don't fork a parallel scoring path; reuse v_control_status + evidence_framework_mappings. BLOCK if anyone proposes a new `glba_*` view.
- **Maya (legal/HIPAA):** APPROVE-WITH-FIXES. Counsel Rule 5: `compliance_frameworks.py` docstring listing 10 frameworks is stale-doc authority. Fix it in this commit. Counsel Rule 10: GLBA Safeguards Rule question wording must NOT imply clinical/legal authority — review the 25 questions.
- **Carol (security):** APPROVE-WITH-FIXES. Question text + policy templates are publish-once authored content — no PHI risk. BUT: `framework` path-param must be allowlist-validated server-side (frameworks.py:389 pattern); the `framework_templates.py` `else: fallback to HIPAA` silent fallback (lines 30-36, 59-64) is a footgun — an unknown framework returns HIPAA questions misleadingly. **Convert silent fallback → HTTPException(400).**
- **Coach (consistency):** APPROVE-WITH-FIXES. `valid_frameworks` is duplicated at frameworks.py:389 + 483 + `framework_templates.py` get_reference_field_name + `compliance_frameworks.py` INDUSTRY_PRESETS. Extract to ONE module-level constant; CI gate (similar to `test_privileged_order_four_list_lockstep.py`) for the framework allowlist.
- **DBA:** APPROVE. No new tables. mig 326 view + mig 013 table already handle per-framework binding. No mig number needed.
- **Frontend:** APPROVE. Existing `compliance-frameworks` router patterns can render the new endpoints; no UI rewrite needed for question-bank read surface.
- **PM:** APPROVE-WITH-FIXES. Counsel Rule 1: do NOT surface SOC2/GLBA compliance % to customer dashboard until `compute_compliance_score()` is parameterized by framework. Question bank read-only ships standalone; score surfacing defers.

## P0 bindings (BLOCKERS until closed)

- **P0-1** Replace silent HIPAA fallback in `framework_templates.py` (lines 30-36, 59-64) with `HTTPException(400, f"Unsupported framework: {framework}")`.
- **P0-2** Add `glba` to `frameworks.py:389` + `frameworks.py:483` allowlist sets.
- **P0-3** Refresh `compliance_frameworks.py:1-17` docstring — strip frameworks that don't have end-to-end binding; cite this commit + the YAML coverage matrix. (Counsel Rule 5.)
- **P0-4** Extract `SUPPORTED_FRAMEWORKS` constant to ONE module (e.g. `compliance_frameworks.SUPPORTED_FRAMEWORKS`); add CI gate `tests/test_framework_allowlist_lockstep.py` enforcing parity across all 4 callsites. Pattern: `test_privileged_order_four_list_lockstep.py`.

## P1 bindings

- **P1-1** GLBA YAML mappings 0/34 → ship at minimum 12 checks covering Safeguards Rule §314.4(c) (access control, encryption, MFA, logging, incident response, vendor mgmt, training, backup, patching, antivirus, firewall, integrity monitoring).
- **P1-2** Finish SOC2 YAML 18/34 → 34/34.
- **P1-3** Extend `framework_sync.py` to backfill `evidence_framework_mappings` for last-30d bundles per new framework.
- **P1-4** Add `glba` industry-preset row in `frameworks.py:657-678` (financial-services).

## P2 bindings

- **P2-1** Move `*_templates.py` content to versioned content store (YAML/JSON under `backend/content/frameworks/{name}/v1/`) so edits don't require code deploy.
- **P2-2** `data_completeness_pct` (frameworks.py:307) currently HIPAA-tuned; verify it returns sensible values for SOC2/GLBA post-backfill.

## File layout

- `framework_templates.py` — MODIFY (remove silent fallback) [P0-1]
- `frameworks.py` — MODIFY (add glba to 2 allowlist sets) [P0-2] + add /assessment-questions + /policy-templates endpoints
- `compliance_frameworks.py` — MODIFY (refresh stale docstring [P0-3], extract SUPPORTED_FRAMEWORKS [P0-4])
- `control_mappings.yaml` — MODIFY (add glba: blocks [P1-1], complete soc2: [P1-2])
- `framework_sync.py` — MODIFY (backfill per-framework [P1-3])
- `tests/test_framework_allowlist_lockstep.py` — NEW [P0-4]
- `tests/test_framework_question_bank_endpoint.py` — NEW (covers P0-1 + endpoint contract)
- `tests/test_control_mappings_yaml_coverage.py` — NEW (covers P1-1/P1-2 — gates SOC2/GLBA per-check coverage thresholds)

## Migration claim

**NONE.** mig 013 + mig 326 already support multi-framework binding.

## Gate B preconditions

- All 4 P0s closed (cite line numbers).
- P1s closed OR explicit TaskCreate followups by ID.
- Full pre-push sweep green; cite pass count.
- Runtime curl evidence: SOC2 returns 30 q's, GLBA returns 25 q's, unknown returns 400 (not silent HIPAA).

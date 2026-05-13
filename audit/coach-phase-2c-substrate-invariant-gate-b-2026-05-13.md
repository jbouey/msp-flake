# Gate B — Phase 2c substrate invariant + `_skip_cache` kwarg

**Date:** 2026-05-13
**Reviewer:** fork (Steve / Maya / Carol / Coach / OCR / PM / Counsel)
**Subject:** AS-SHIPPED Task #64 Phase 2c — `_check_canonical_compliance_score_drift` invariant + `_skip_cache=False` kwarg on `compute_compliance_score`
**Gate A reference:** `audit/coach-canonical-compliance-score-drift-v3-patched-gate-a-2026-05-13.md` (APPROVED v4)
**Verdict:** **APPROVE-WITH-FIXES** — single P0 (missing `## Escalation` section in runbook) MUST close before claiming shipped.

---

## 250-word summary

Phase 2c closes the loop on the Counsel Rule 1 runtime drift detector: Phase 2a established the `canonical_metric_samples` table + 3-layer defense (CHECK + partial index + WHERE-filter), Phase 2b ships the 10% sampler decorator. Phase 2c is the consumer — every 60s the substrate engine pulls samples classified `customer-facing` from the last 15 minutes, recomputes via `compute_compliance_score(..., _skip_cache=True)` against the captured `helper_input`, and opens a sev2 violation when |captured − canonical| > 0.5.

The AS-IMPLEMENTED matches DESIGN v4 across all five deviation-matrix items: helper kwarg is correctly `KEYWORD_ONLY` + default `False`; cache-enabled gate ANDs in `not _skip_cache`; invariant queries the partial-index-aligned WHERE clause (`metric_class='compliance_score' AND classification='customer-facing'`); JSONB `helper_input` decode handles asyncpg's auto-decode + string fallback; soft-fail wraps `compute_compliance_score` and the import; `_DISPLAY_METADATA` + ALL_ASSERTIONS entries land in lockstep; 2-test pin gate landed in pre-push allowlist.

Banned-word scan returned 0 hits across runbook + Violation strings + assertion description + recommended_action.

**Single P0 surfaces from the mandatory Gate B full-sweep run (Session 220 lock-in):** `test_substrate_docs_present.py::test_doc_exists_and_has_sections[canonical_compliance_score_drift]` FAILS because the runbook is missing the `## Escalation` section required by `_TEMPLATE.md`. Author shipped 6 of 7 canonical sections plus an extra `## False-positive guard`. Fix is one-paragraph insert before `## Related runbooks`. This is the canonical Session-220 class — diff-only review would have approved this; sweep catches it.

Pre-push sweep: **246 passed, 1 failed (test_substrate_docs_present)** of 247 files.

---

## AS-IMPLEMENTED vs DESIGN v4 deviation matrix

| # | Artifact | Designed | Shipped | Verdict |
|---|----------|----------|---------|---------|
| 1 | `compliance_score._skip_cache` kwarg | `bool = False`, KEYWORD_ONLY, gates `_cache_enabled = _should_cache_score(window_days) and not _skip_cache`; cache-write gated on `_cache_enabled` (line 427) | EXACT match (line 163 + line 224 + line 427); probe `inspect.signature(...).parameters['_skip_cache'].kind` returns `KEYWORD_ONLY` | ✅ PASS |
| 2 | `_check_canonical_compliance_score_drift` substrate function | Queries `canonical_metric_samples` filtered by `metric_class='compliance_score' AND classification='customer-facing' AND captured_at > NOW()-15min AND captured_value IS NOT NULL`, ORDER BY captured_at DESC LIMIT 50; decodes JSONB helper_input with string-fallback; calls helper with `_skip_cache=True` + captured kwargs; tolerance `> 0.5`; soft-fail per-sample | EXACT match (assertions.py:6083–6195); 3-layer defense WHERE clause; soft-fail on `compute_compliance_score` exception (line 6152 `continue`) + soft-fail on JSON decode (line 6135 `continue`) + soft-fail on float coerce (line 6160 `continue`) + soft-skip on empty site_ids (line 6138) + soft-skip on None comparison values (line 6155) | ✅ PASS |
| 3 | ALL_ASSERTIONS entry | severity=sev2, description cites Counsel Rule 1 runtime half + pairs with static AST gate + samples 10% via decorator + runbook link | EXACT match (assertions.py:2297–2302) | ✅ PASS |
| 4 | `_DISPLAY_METADATA` entry | display_name + recommended_action with concrete next-step (inspect endpoint_path + delta, identify path, drive-down PR) | EXACT match (assertions.py:3123–3135) | ✅ PASS |
| 5 | Runbook | 7 sections per `_TEMPLATE.md`: What this means / Root cause categories / Immediate action / Verification / Escalation / Related runbooks / Change log | **6 of 7 sections shipped + 1 extra `## False-positive guard`. MISSING `## Escalation`.** test_substrate_docs_present FAILS. | ❌ **P0 FAIL** |

---

## Per-lens verdict

### 1. Engineering (Steve) — APPROVE

Helper change (compliance_score.py):
- Line 163: `_skip_cache: bool = False` — keyword-only (after `*,` on line 160), default False, type-annotated. Runtime-verified via `inspect.signature(...).parameters['_skip_cache'].kind` → `KEYWORD_ONLY`. ✅
- Line 224: `_cache_enabled = _should_cache_score(window_days) and not _skip_cache` — gates BOTH read (line 226–231) AND write (line 427 `if _cache_enabled and _cache_key is not None`). Single gate variable, single source of truth. ✅
- 60s TTL cache from `perf_cache.cache_get/cache_set` — both read+write blocked when `_skip_cache=True`. ✅

Invariant (assertions.py:6083–6195):
- Query filters: `metric_class='compliance_score' AND classification='customer-facing' AND captured_at > NOW()-15min AND captured_value IS NOT NULL`. Matches Carol Gate A v4 Layer 3 verbatim. ✅
- JSONB decode: asyncpg auto-decodes JSONB → dict in most paths, but old driver versions return string; fallback `if isinstance(helper_input, str): json.loads(...)` (line 6130–6135) — defensive correct. ✅
- Calls `compute_compliance_score(conn, site_ids=..., window_days=..., include_incidents=..., _skip_cache=True)` (line 6146–6151) — kwarg name + value match design. ✅
- Tolerance `abs(helper_score_f - captured_value) > 0.5` (line 6162). Exactly `> 0.5`. ✅
- Violation details: sample_id, tenant_id, endpoint_path, captured_value, canonical_value, delta (signed, rounded to 2), captured_at (ISO), interpretation, remediation — all auditor-readable, all string-encodable. ✅
- Soft-fail wrappers: helper exception → `continue`; JSON decode failure → `continue`; coerce failure → `continue`; empty site_ids → `continue`; None comparison → `continue`. One sample's failure costs that one sample, never the whole substrate tick. ✅

**Minor nit (informational, not blocking):** the `from compliance_score import ... except ImportError: from .compliance_score import ...` shim (line 6142–6144) is correct for the deploy layout (backend runs sys.path-rooted, tests sometimes from package-rooted). Same pattern other substrate functions use.

### 2. Database (Maya) — APPROVE

- SQL is read-only against `canonical_metric_samples`. No JOINs that could re-introduce operator-internal rows.
- Index used: `idx_canonical_metric_samples_drift ON canonical_metric_samples (metric_class, classification, captured_at DESC) WHERE classification='customer-facing'` (mig 314). The query's WHERE `metric_class = 'compliance_score' AND classification = 'customer-facing' AND captured_at > NOW() - INTERVAL '15 minutes' AND captured_value IS NOT NULL` is a direct prefix match — index-only scan, bounded by the 15min `captured_at` predicate + LIMIT 50.
- `compute_compliance_score` inside the invariant runs against `admin_transaction(pool)` from `run_assertions_once` per-assertion isolation — same RLS context as other substrate invariants. compliance_bundles has the asyncpg admin-context policy that exposes all sites (the substrate's job is cross-tenant invariant detection). ✅
- No NOW()-in-partial-index pitfall (Session 219 lesson) — the partial-index predicate is the IMMUTABLE `classification = 'customer-facing'`, not a NOW-anchored expression. ✅

### 3. Security (Carol) — APPROVE

3-layer defense verified end-to-end:
- **Layer 1 (CHECK constraint, mig 314)**: `classification IN ('customer-facing', 'operator-internal')` — write-time prevention of invalid values.
- **Layer 2 (partial index `idx_canonical_metric_samples_drift`)**: physically excludes operator-internal rows from the drift index. Any query that uses the index physically cannot return operator-internal rows.
- **Layer 3 (WHERE clause in `_check_canonical_compliance_score_drift`)**: explicit `classification = 'customer-facing'` filter on line 6120. Defense even if the planner picks a different index (it won't, but Carol's principle holds).

No JOIN. No subquery. No `IN (...)` over a value the operator can influence. Operator-internal samples cannot reach the alert path. ✅

### 4. Coach — **APPROVE-WITH-FIXES**

Template compliance check vs `_TEMPLATE.md`:
- `## What this means` ✅ (line 6 — labeled `## What this means (plain English)`, matches template + sibling)
- `## Root cause categories` ✅ (line 12)
- `## Immediate action` ✅ (line 19)
- `## Verification` ✅ (line 32)
- `## Escalation` ❌ **MISSING**
- `## False-positive guard` — extra section (line 45). Not in template. Sibling `daemon_heartbeat_unsigned.md` uses the same extra section, so this is a recognized pattern, but it does NOT substitute for `## Escalation`.
- `## Related runbooks` ✅ (line 52)
- `## Change log` ✅ (line 57)

Sibling `daemon_heartbeat_unsigned.md` has BOTH `## Escalation` (line ~22) AND `## False-positive guard` (line ~26). The author shipped the False-positive guard but dropped Escalation. Test `test_substrate_docs_present.py::test_doc_exists_and_has_sections[canonical_compliance_score_drift]` FAILS at the missing-Escalation assertion.

Related runbooks point to real sibling files:
- `unbridged_telemetry_runbook_ids.md` ✅ exists
- `l2_resolution_without_decision_record.md` ✅ exists

**Required fix (P0):** insert a `## Escalation` section between current `## Verification` (line 32–43) and `## False-positive guard` (line 45). Suggested copy:

```markdown
## Escalation

If the same `endpoint_path` continues to fire after a drive-down PR claims the migration is complete, the canonical helper itself may have forked locally OR the sampler's `helper_input` capture may mismatch the endpoint's actual helper call. Escalate to whoever owns `canonical_metrics.py` allowlist. **Do not** suppress the invariant — drift signaled here is a Rule 1 runtime violation and counsel-grade evidence that the canonical-source registry is incomplete.
```

### 5. Auditor (OCR) — APPROVE

Violation details are auditor-grade per Counsel Rule 1 runtime-evidence requirement:
- `interpretation` (string): "Endpoint X returned Y for tenant Z but canonical helper produces W for the same inputs. Non-canonical computation path is in use OR a bug exists between the helper and the endpoint's response shape." — auditor reads this and knows what drift was detected without re-reading the code.
- `remediation` (string): "Inspect <endpoint_path> source: it should delegate to compliance_score.compute_compliance_score. Likely uses one of the allowlist `migrate`-class entries (db_queries, frameworks, etc.) — drive-down PR migrates that path to canonical helper." — auditor sees the concrete next step.
- All values float/string-coerced → JSON-safe → ZIP-safe for auditor-kit emission.
- `captured_at.isoformat()` ✅ (deterministic, not wall-clock).
- `sample_id` + `tenant_id` are stringified ✅ — auditor can re-query `canonical_metric_samples WHERE sample_id = '<id>'` to verify the chain.

This satisfies the Counsel Rule 1 runtime half: when a drift is caught, the violation row is a self-contained audit record. ✅

### 6. PM — APPROVE-WITH-FIXES

- Gate A v4 verdict cited (`audit/coach-canonical-compliance-score-drift-v3-patched-gate-a-2026-05-13.md`).
- Author ran 36 narrow gates + 20 assertion-completeness gates + pre-push sweep — claimed rc=0 on the pre-push sweep, but the FULL test sweep (`bash .githooks/full-test-sweep.sh`) returns rc≠0 because of the missing-Escalation gate. **The author's claim "pre-push sweep: rc=0 (clean)" appears to refer to a narrower subset; the full sweep is the Session-220 mandate.**
- 2-test pin gate (`test_compliance_score_skip_cache_arg.py`) added to pre-push allowlist line 128.
- CI deploy not yet verified — but per Gate B BLOCK on the runbook, no CI run should be triggered until P0 closes.

### 7. Counsel (in-house) — APPROVE

Banned-word scan:
- `substrate_runbooks/canonical_compliance_score_drift.md` — `grep -iE "ensures|prevents|protects|guarantees|100%|PHI never leaves"` → 0 hits ✅
- `assertions.py` lines 2297–2302 (ALL_ASSERTIONS entry description) — 0 hits ✅
- `assertions.py` lines 3123–3135 (_DISPLAY_METADATA recommended_action) — 0 hits ✅
- `assertions.py` lines 6083–6195 (`_check_canonical_compliance_score_drift` docstring + Violation interpretation + Violation remediation) — 0 hits ✅

Tone is investigatory, not coercive. "Likely uses…", "Inspect…", "drive-down PR migrates…" — substrate-internal operator language. No customer-facing exposure (alert is operator-internal per Session 218 opaque-mode parity, explicitly stated at runbook line 21).

---

## Banned-word scan result

`grep -iE "ensures|prevents|protects|guarantees|100%|PHI never leaves"` across all 4 surfaces (runbook + ALL_ASSERTIONS description + _DISPLAY_METADATA recommended_action + invariant docstring/Violation strings): **0 hits**. ✅

## Pre-push full sweep count

`bash .githooks/full-test-sweep.sh`: **247 files run / 246 passed / 1 FAILED / 0 skipped**.

Failure: `tests/test_substrate_docs_present.py::test_doc_exists_and_has_sections[canonical_compliance_score_drift]` — missing `## Escalation` section.

Per Session 220 lock-in (`Gate B MUST run the full pre-push test sweep, not just review the diff`): this is exactly the failure class that diff-only Gate B reviews miss. The diff added the runbook with 6+1 sections; the missing-Escalation gate only fires when the test executes against the new ALL_ASSERTIONS entry.

---

## Adversarial probes — results

| Probe | Expected | Got | Verdict |
|-------|----------|-----|---------|
| `inspect.signature(compute_compliance_score).parameters['_skip_cache']` | `_skip_cache: bool = False`, KEYWORD_ONLY | `_skip_cache: 'bool' = False`, KEYWORD_ONLY | ✅ |
| `grep -n "_check_canonical_compliance_score_drift\|canonical_compliance_score_drift" assertions.py` | ≥3 refs | 6 refs (function def, ALL_ASSERTIONS, _DISPLAY_METADATA + runbook citations) | ✅ |
| `bash .githooks/full-test-sweep.sh` exit | clean | rc≠0, 1 failure (missing Escalation) | ❌ **P0** |
| 7 sections in runbook | all present | 6 of 7 (missing Escalation) + 1 extra (False-positive guard) | ❌ **P0** |
| Banned-word grep on runbook | 0 hits | 0 hits | ✅ |
| Banned-word grep on assertions.py sections | 0 hits | 0 hits | ✅ |
| 2-pin test runs clean | 2/2 pass | 2/2 pass in 0.12s | ✅ |
| Sibling runbooks referenced exist | exist | both exist (`unbridged_telemetry_runbook_ids.md`, `l2_resolution_without_decision_record.md`) | ✅ |
| Drift index supports query | partial index on `(metric_class, classification, captured_at DESC) WHERE classification='customer-facing'` | mig 314 confirmed | ✅ |

---

## Final verdict

**APPROVE-WITH-FIXES.**

Single P0 must close before claiming shipped:

**P0-1.** Add `## Escalation` section to `mcp-server/central-command/backend/substrate_runbooks/canonical_compliance_score_drift.md` between current `## Verification` and `## False-positive guard`. Suggested copy in Coach lens above. After insert: re-run `bash .githooks/full-test-sweep.sh` and confirm `247 passed`. THEN commit with body citing this Gate B verdict + the closing fix.

No P1, no P2. Architecture is sound, all 5 design items shipped correctly except for the one missing runbook section. The Session 220 lock-in worked as designed — diff-only Gate B would have approved this commit; the full-sweep mandate caught the missing section before "shipped" was claimed.

---

## Recommendations (not advisory — Session 219 lock-in extension)

- **R1 (P0, blocks shipped-claim):** add `## Escalation` to the runbook; re-run full sweep; cite 247/247 in commit body.
- **R2 (P1, named TaskCreate followup acceptable):** Phase 2d pruner (Task #65) — `canonical_metric_samples` will grow without bound at 10% sampling × all customer-facing endpoints; the 14d pruner from Gate A v4 plan is the closure. Tracked.
- **R3 (informational):** the author's claim of "Pre-push sweep: rc=0 (clean)" in the Gate B prompt does not match the full sweep result. Recommend the author standardize: pre-push allowlist sweep ≠ full sweep. Reference Session 220 lock-in language verbatim in future Gate B requests.

— Coach (fork) 2026-05-13

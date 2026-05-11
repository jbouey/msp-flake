# Gate B verdict — escalate-rule check_type drift CI gate (2026-05-11)

**Verdict:** APPROVE

## Gate A directive compliance
- P0 incident_type: PASS — `_CONDITION_FIELD_RE` at line 92-95 matches `(check_type|incident_type)` alternation; positive control `test_synthetic_incident_type_rule_is_caught` (line 214) pins the behavior. Verified against `L1-SERVICE-001` at builtin_rules.go:212 (`incident_type`).
- P1 decision tree: PASS — 4 explicit branches (a/b/c/d) at test file lines 167-179, each actionable + named.
- P1 pre-seed: PASS — all 9 currently-known escalate rules in `_KNOWN_ESCALATE_CHECK_TYPES` (lines 58-68) with auditor-readable justifications. Source-side count (grep `Action: "escalate"` → 9 hits at lines 161/215/712/732/823/988/1008/1028/1048) matches.
- P2 SCOPE: PASS — docstring lines 19-22 explicitly scope to compile-time Go builtin rules; calls out runtime JSON rule path as out-of-scope.
- P2 controls: PASS — 3 control tests: `test_synthetic_undocumented_rule_is_caught` (check_type positive), `test_synthetic_incident_type_rule_is_caught` (incident_type positive), `test_known_escalate_check_types_match_source` (negative — stale-allowlist detector).

## Full sweep result (MANDATORY)
131 passed, 0 failed (1.90s)

## Adversarial findings (NEW)

**Steve — 20-line window safety:** L1-SERVICE-001 has the largest Conditions block of the 9 escalate rules (2 conditions, action 8 lines below ID). All 9 fit comfortably in the 20-line window. Docstring at line 26 says "15 lines" but code at line 110 uses 20 — minor doc/code drift, non-blocking (code is the more lenient/correct value). Recommend doc-fix in followup.

**Steve — `ID:` regex collisions:** `_RULE_ID_RE` is anchored `^\s*ID:\s*"..."` — won't match `RunbookID:` or other suffixed fields. Safe.

**Steve — 10th-rule synthetic:** A new escalate rule added without allowlist entry produces the 4-branch error naming `builtin_rules.go:<line>  <rule_id>  (<field>=<value>)`. Auditor-actionable.

**Maya — branch (b) actionability:** Branch (b) references "next mig-306-style backfill" — actionable today since mig 306 is the established pattern; auditor reading the message knows the cookbook.

**Carol — allowlist bypass:** `test_known_escalate_check_types_match_source` (line 242) closes the typo'd-rule_id bypass class by requiring every allowlist key to correspond to a real source rule. Verified working.

**Coach — sweep + sibling parity:** 131/131 pass. Test shape mirrors `test_privileged_chain_allowed_events_lockstep.py` (line-window scan + allowlist + 4 control tests). No missing-companion-file class (no new substrate assertion, no new migration).

## Recommendation

APPROVE — ship task #114. Followup nit: align docstring "15 lines" to match code's "20 lines" (line 26 vs 110). Non-blocking.

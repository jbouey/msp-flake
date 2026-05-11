# Gate A — escalate-rule check_type drift CI gate (2026-05-11)

**Verdict:** APPROVE-WITH-FIXES

## Source verification

Grepped `appliance/internal/healing/*.go`:
- All 9 escalate sites use literal `Action: "escalate",` (one space). NO `actionEscalate` constant exists. Only other hit is `l1_engine_test.go:94` (test assertion — exclude by path).
- **Critical shape finding:** rule at line 215 (`L1-SVC-CRASH-001`) keys off `incident_type`, not `check_type` — its condition is `{Field: "incident_type", Operator: OpEquals, Value: "service_crash"}`. The proposed regex that only extracts `check_type` will SILENTLY MISS this rule and report 8 of 9 instead of 9. Same class of escalate-orphan drift applies regardless of which field gates the rule.

**Suggested parser** (not regex — use a tiny line-window scan):
1. Find every line matching `^\s*Action:\s*"escalate"\s*,\s*$`.
2. Walk backwards up to 15 lines to the nearest `Conditions: []RuleCondition{`.
3. Inside that window, extract the FIRST `{Field: "(check_type|incident_type)", Operator: OpEquals, Value: "(\w+)"}`.
4. Record `(rule_id_from_ID:_line, field, value, line_no)`.

Regex-only approach is fragile because `Conditions` blocks contain 1-3 conditions in arbitrary order; balancing braces in regex is brittle. Line-window scan with `^\s*` anchors is sibling-equivalent to `test_appliance_endpoints_auth_pinned.py`.

## P0 / P1 / P2 findings

- **P0 — extractor must cover `incident_type` too.** Otherwise `L1-SVC-CRASH-001` (line 215) is invisible to the gate. Add `incident_type` to the field allowlist and treat (field, value) as the dedup key.
- **P1 — output must NAME the decision (Maya).** When gate fires, print a 4-line decision tree: `(a) add to MONITORING_ONLY_CHECKS if not auto-healable`, `(b) add to mig 306 IN-list when migration ships`, `(c) add to _KNOWN_ESCALATE_CHECK_TYPES with justification comment`, `(d) refactor to a healable Action if a runbook exists`. Sibling pattern: `test_privileged_chain_allowed_events_lockstep.py` error message.
- **P1 — pre-seed allowlist with all 9 current escalate rules (Coach).** Firing on the 6 non-chaos-lab rules now adds zero safety (they're already correct by design — escalate IS the right action) and gates Phase 3 merge on busy-work documentation. Seed allowlist with all 9 entries, each tagged with `# justified: <reason>` + check_type. Future ADDS trigger the gate; existing entries don't.
- **P2 — document scope (Carol).** Add a `SCOPE:` header comment: gate covers compile-time builtin Go rules ONLY. Runtime-loaded JSON rules from `/var/lib/msp/rules/l1_rules.json` (synced from Central Command) are out of scope — those flow through server-side `l1_rules` table review.
- **P2 — positive/negative control tests.** Sibling pattern requires both: (a) inject a fake `Action: "escalate"` line with unknown check_type into a tmpdir copy → assert gate fails; (b) run against real builtin_rules.go → assert gate passes with seeded allowlist.

## Per-lens

- **Steve:** regex approach is the wrong tool; line-window scan handles nested struct literals cleanly. `incident_type` is the silent gap.
- **Maya:** decision-tree output language is the deliverable, not the failure.
- **Carol:** scope-document the JSON-rule blind spot; out-of-scope is fine if named.
- **Coach:** mirror `test_privileged_chain_allowed_events_lockstep.py` shape exactly — same `SOURCE_FILE = Path(...)`, same allowlist-with-comments mechanism, same control-test pair. Seed allowlist with 9 known entries.

## Recommendation

APPROVE-WITH-FIXES. Address P0 (`incident_type` field) + both P1s (decision-tree output + pre-seed 9 entries) before commit. P2s are documentation polish — land in same commit. Gate B will verify the as-implemented test catches a planted negative control AND passes against current builtin_rules.go with seeded allowlist.

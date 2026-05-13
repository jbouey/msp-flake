# Task #54 v2 — Class-B Gate B (post-P0 #1 + #3 fix; P0 #2 user-gated)

**Per-lens verdict**

| # | Lens | Verdict | Top finding |
|---|---|---|---|
| 1 | Engineering (Steve) | APPROVE | Inline + module-const pattern is implementable; both phiscrub-import options (A subprocess / B port+parity-gate) are tractable; enclosing-FunctionDef rule materialized in §2 |
| 2 | Coach | APPROVE | Single canonical surface (inline + module-const) mirrors opacity gate precedent; Phase 2 phiscrub-import closure removes double-build |
| 3 | Attorney (Master BAA Art. 1.2) | APPROVE | Rule 2 materialized as compiler-rule; banned-word scan clean; Go source as single SoT preserved |
| 4 | Inside-counsel-surrogate | APPROVE | `operator-internal-only` rename is legal-defensible (no "exempt" word that auditors flag); semantics preserved |

**Overall: APPROVE — ready to ship Phase 0 modulo P0 #2 user-gate.**

---

## P0 closure matrix

| # | Issue | Status | Evidence in v2 doc |
|---|---|---|---|
| P0 #1 (Coach) — declaration-mechanism over-build (4 surfaces → 1 pattern) | **CLOSED** | v2 header §3 + §2 explicitly collapse to inline marker + module-const ONLY. YAML + commit-body parse REMOVED. §3 detection algorithm still references YAML on line 106 — see Lens 1 NEW finding L1-N1 below (residual cleanup, P1 not P0). |
| P0 #2 (Coach) — Task #50 lockstep (option a/b/c) | **CARRY-FORWARD (honest)** | v2 header line 9 explicitly names option (b) separate-per-rule as engineering default, names Q3 reconcile, flags as USER-GATED. Honesty check: PASS — doc does not silently pick option (b) and bury the choice. |
| P0 #3 (Coach) — Phase 2 substrate invariant double-builds `phiscrub` | **CLOSED** | v2 §4 Phase 2 explicitly mandates Go scrubber as single SoT; two implementation options (A subprocess / B Python port + `test_phiscrub_pattern_python_parity.py` CI gate). Master BAA Art. 1.2 cited as authority. |
| Bonus — `exempt-internal` → `operator-internal-only` rename | **CLOSED** | v2 §2 classification list line 72 uses `operator-internal-only` with explicit Gate A Lens 7 rationale; clarification that it's NOT a Rule 2 exemption added. |
| Bonus — Phase 1+ scope exclusions explicit | **CLOSED** | v2 header line 13 explicitly excludes YAML config, commit-body parse, daemon Go gate, gRPC, Prometheus labels from Phase 0. §4 Phase 3 names daemon + gRPC. Header line 15 names ratchet-at-today's-count + no dedicated drive-down. |

---

## Lens 1-4 NEW findings (post-v2 regression scan)

### Lens 1 (Engineering) — APPROVE

- **L1-N1 (P1, NOT a Gate B blocker):** §3 `PHI_BOUNDARY_DECLARATION_SOURCES` list on line 106 STILL references `phi_boundary.yaml` as a third surface, contradicting the v2 header's "YAML REMOVED" claim. This is residual stale code-block content from v1, not a design regression — the algorithm description (§3 step 2) says "inline OR module-level constant OR YAML" but the v2 header collapsed to two surfaces. **Recommendation:** strip the YAML row from line 106 before Phase 0 ships. Mechanical fix, doesn't gate APPROVE.
- **L1-N2 (P2):** §3 step 2 still uses `±10 lines` proximity language. Gate A Lens 1 P0-1 recommended `_enclosing_function()` from `test_email_opacity_harmonized.py:194`. v2 didn't apply this in §3 detection algorithm even though §2 declaration mechanism does attach to FunctionDef. Reconcile in Phase 0 implementation; not a Gate B blocker.

### Lens 2 (Coach) — APPROVE

- No new over-build introduced in v2. Two-surface shape (inline + module-const) is exactly the opacity-gate precedent. Phase 2 phiscrub-import is single-source-of-truth. Phase 1+ scope exclusions are clean.
- **Watch-item (not a finding):** §6 "Open questions for Class-B Gate A" section is now stale (Gate A has run). v2 should either delete it or rename to "Open questions for user-gate (Phase 0 launch decisions)." Cosmetic, not a regression.

### Lens 3 (Attorney) — APPROVE

- Banned-word scan: v2 uses `monitors / scrubbed / reduces exposure / cannot reach by construction` correctly. No `ensures / prevents / protects / guarantees / 100%`.
- Master BAA Art. 1.2 cited in Phase 2 closure (§4) as legal basis for Go-as-single-SoT — "a parallel pattern set is itself a Rule 2 violation per Master BAA Article 1.2" is the right framing.
- Compiler-rule conversion materialized: "Rule 2 is a compiler rule, not a posture preference" appears in counsel quote and §2 closure.

### Lens 4 (Inside-counsel-surrogate) — APPROVE

- `operator-internal-only` rename is the legally-defensible label. Word `exempt` removed everywhere in v2. §2 line 72 explicitly clarifies "this is NOT a Rule 2 exemption — operator paths are still subject to Rule 2 if they later become customer-visible" which is the right legal hedge against future scope drift.
- Classification semantics still cite §164.514(b) safe harbor for `hard-no` (line 69) — auditor-traceable.

---

## P0 #2 user-gate documentation honesty check

**Verdict: HONEST.**

Evidence:
- v2 header line 9 says "STILL USER-GATED" in caps — not buried in a footnote.
- Names all three options (a / b / c) explicitly with their tradeoffs.
- States "Design v2 below assumes option (b) as engineering default" — doesn't silently pick option (b) and present it as the closure.
- Names "sketched migration path to shared infra if user chooses (a) later" — acknowledges the re-work cost is real.
- Q3 reconcile is named as the deferral mechanism, not "we'll figure it out later."

What would have failed the honesty check (and didn't): silently picking option (b), removing P0 #2 from the closure list, or claiming "option (b) is the right answer" without naming it as user-gated.

---

## Final recommendation

**APPROVE — ready to ship Phase 0 modulo P0 #2 user-gate.**

Ship-readiness decomposition:
- **P0 #1 + P0 #3:** closed in v2; verified inline.
- **P0 #2:** carry-forward, honestly user-gated; engineering default option (b) is shippable standalone. If user picks option (a) later, declaration shape stays compatible (inline marker + module-const generalize to a `compiler_rule_declarations` shared walker without re-writing the declarations themselves — only the gate's AST-walker plumbing).
- **Bonus items:** both closed.
- **L1-N1 + L1-N2:** mechanical cleanups for Phase 0 implementation; not Gate B blockers.

**Block on P0 #2 alone vs ready-modulo-user-gate:** READY-MODULO-USER-GATE. The doc is honest about P0 #2, engineering default is implementable standalone, and option (a) is a future plumbing refactor that does not invalidate Phase 0 declarations. User answers P0 #2 → Phase 0 ships in 2-3 engineer-days per Gate A Lens 5 estimate.

---

## 150-word summary

Task #54 v2 closes Gate A's two engineering-actionable P0s cleanly: P0 #1 collapses four declaration surfaces (YAML + inline + module-const + commit-body) to a single opacity-gate-precedent pattern (inline marker + module-const), and P0 #3 mandates Go scrubber as the single source of truth for Phase 2 substrate-invariant patterns with two tractable implementation options (subprocess export vs Python port + parity-gate). The `operator-internal-only` rename and Phase 1+ scope exclusions are both materialized in v2 §2 and the v2 header. P0 #2 (Task #50 shared `compiler_rule_declarations` infra) is honestly carried forward as user-gated with option (b) separate-per-rule as engineering default and Q3 reconcile named — not silently picked and buried. Four-lens rerun returns APPROVE with two minor residual cleanups (L1-N1 stale YAML row in §3 line 106; L1-N2 `±10 lines` language reconcile). Phase 0 is ship-ready modulo the user-gate decision.

## Closure matrix (compact)

| Item | Status |
|---|---|
| P0 #1 declaration-mechanism over-build | CLOSED |
| P0 #2 Task #50 lockstep | CARRY-FORWARD (honest, user-gated) |
| P0 #3 Phase 2 phiscrub double-build | CLOSED |
| Bonus rename `exempt-internal` → `operator-internal-only` | CLOSED |
| Bonus Phase 1+ scope exclusions explicit | CLOSED |
| New L1-N1 stale YAML in §3 line 106 | P1 (mechanical, not Gate B blocker) |
| New L1-N2 `±10 lines` vs `_enclosing_function()` | P2 (Phase 0 implementation detail) |

## Ship-readiness

**READY-MODULO-USER-GATE.** Not blocked on P0 #2 alone — engineering default option (b) is shippable standalone, declarations are forward-compatible with option (a) shared infra. User answers P0 #2 → Phase 0 ships.

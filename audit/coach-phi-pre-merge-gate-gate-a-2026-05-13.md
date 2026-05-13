# Class-B 7-lens Gate A — PHI-pre-merge gate design (Task #54)

**Design under review:** `audit/phi-pre-merge-gate-design-2026-05-13.md`
**Counsel Rule under enforcement:** Rule 2 (compiler-rule conversion of PHI-free posture)
**Counsel priority order placement:** Rule 2 is NOT in counsel's top-5 legal-exposure-closure list (6→8→5→1→7). Counsel framed Rule 2 as a *procedural strengthening* of an already-strong runtime defense (`phiscrub/scrubber.go`). Gate A inherits that framing: the design must materialize the pre-merge question without re-building runtime guarantees.

**Per-lens verdict**

| # | Lens | Verdict | Top finding |
|---|---|---|---|
| 1 | Engineering (Steve) | APPROVE-WITH-FIXES | AST patterns tractable, but `±10 lines` proximity rule is brittle — module-constant + inline-comment dual canonical surface needed; YAML file should be deferred to Phase 1 |
| 2 | HIPAA auditor surrogate | APPROVE-WITH-FIXES | Declaration registry must emit a per-PR audit artifact (commit-log greppable) that an OCR auditor can reconstruct |
| 3 | Coach (no double-build) | **BLOCK-PENDING-FIXES** | THREE separate declaration mechanisms (YAML + inline + module-const) is over-build; pick ONE canonical surface + ratchet the rest; cross-task lockstep with Task #50 unresolved |
| 4 | Attorney (Master BAA Art. 1.2) | APPROVE | Design materializes the BAA commitment — Art. 1.2 names `phiscrub/scrubber.go` as the authoritative implementation; this gate makes the pre-merge question structural |
| 5 | Product manager | APPROVE-WITH-FIXES | Backfill cost ~5×N min; with measured N≈660 AST-pattern hits backend-only (404 logger.f-string + 631 router + 25 LLM + ~2 outbound HTTP, deduplicated/scoped ≈ 200-400 distinct paths), realistic at 2-3 sprints; declaration fatigue is real risk if scope not tightened in Phase 0 |
| 6 | Medical-technical | APPROVE-WITH-FIXES | Realistic PHI-vector coverage is good but MISSES two classes: (a) gRPC payloads at `appliance/internal/grpcserver/` (daemon→backend stream), (b) Prometheus label cardinality (label-value can carry PHI if templated from incident text) |
| 7 | Legal-internal (Maya + Carol) | APPROVE | Banned-word scan clean; classification language ("hard-no / scrubbed-at-edge / n-a-no-egress / exempt-internal") legally precise and matches BAA Art. 1.2 framing |

**Overall: APPROVE-WITH-FIXES** — design is sound, scope is right, materializes counsel Rule 2; but three coach-lens P0s must close before Phase 0 ships.

---

## Lens 1 — Engineering (Steve)

**P0-1: `±10 lines` proximity rule is the wrong default.** Inline-comment markers next to AST nodes (the F4 `noqa: rename-site-gate` pattern) work for narrow single-line patterns. For data-emitting features the AST node is often a multi-line block (multi-line subject, parenthesized concatenation, async-context-manager body). Proximity radius needed for honest coverage is closer to enclosing-function — not line-count. **Fix:** declaration MUST live inside the enclosing FunctionDef body OR as a `phi_boundary` decorator on the enclosing function. `_enclosing_function()` helper already lives in `test_email_opacity_harmonized.py:194` — reuse.

**P0-2: AST pattern false-positive rate.** Raw measurement: `logger.{info,warning,error}(f"...")` matches 404 backend call sites. Many are operator-internal (NOT customer-facing — operator alerts, substrate logs, admin audit). The opacity gate solved this with `_OPAQUE_MODULES` allowlist + `OPERATOR_ALLOWLIST`. **Fix:** mirror that two-list structure for PHI scope. Operator-internal modules (`email_alerts.py`, `assertions.py`, `prometheus_metrics.py`, `escalation_engine.py`) declared once at module top → all in-module data-emitters inherit `exempt-internal` classification.

**P1-3: Declaration mechanism via commit-body parse is fragile.** Commit bodies don't survive rebases/squashes cleanly. **Fix:** drop commit-body option. In-source markers OR module-level constant are the canonical surfaces.

**P1-4: Scope detector for `route_path` startswith `/api/admin/` or `/api/internal/`.** These are operator-only by URL prefix — auto-classify as `exempt-internal`, don't require per-handler declaration.

**False-positive estimate at proposed AST-pattern scope (raw):**
- 404 logger.f-string hits
- 631 router decorators
- ~25 LLM call sites
- 2 outbound HTTP
- Estimated DEDUPLICATED customer-facing-only after operator-allowlist: 200-400 paths

---

## Lens 2 — HIPAA auditor surrogate

**P1-1: OCR-traceability.** When OCR asks "show me how PHI cannot reach Central Command for feature X shipped on date Y," the gate must produce:
- (a) The PR + commit that introduced feature X
- (b) The `phi_boundary` declaration attached to it
- (c) The Phase 2 substrate-invariant runtime evidence proving the declaration matches reality

(c) is the load-bearing piece. (a) + (b) without (c) is a posture artifact, not evidence.

**Fix:** Phase 2 substrate invariant `phi_boundary_drift` MUST be sev1 not sev2 — Rule 2 is a compiler rule per counsel; runtime drift of a compiler-rule-claimed property is a sev1 event. Lower the bar later if false-positive rate proves it.

**P2-2: Declaration registry must persist as a queryable artifact.** A pile of inline comments cannot be queried at audit time. Either: (a) a code-generator extracts all declarations into `audit/phi_boundary_registry.json` at CI time (committed alongside code), OR (b) the substrate invariant snapshot writes the registry into the auditor kit. Prefer (a) — auditor kit must be deterministic per Session 218 contract; injecting CI-derived state breaks byte-identity.

---

## Lens 3 — Coach (no over-engineering / no double-build) — **BLOCK-PENDING-FIXES**

**P0-1: THREE declaration mechanisms is over-build.** The design lists commit-body parse, inline comment, module-level constant, AND `phi_boundary.yaml`. Pick ONE canonical surface for Phase 0. Coach's preference: **inline `# phi_boundary: <class> — <reason>` markers attached to the enclosing FunctionDef**, plus a module-level `PHI_BOUNDARY_MODULE_DEFAULT = "exempt-internal"` constant for whole-module operator allowlisting. Mirror the opacity gate's `_OPAQUE_MODULES` / `OPERATOR_ALLOWLIST` two-list shape. YAML and commit-body parse are over-build until Phase 1 proves a need.

**P0-2: Cross-task lockstep with Task #50 (canonical-source registry) is unresolved.** Task #50 has no plan file under `.agent/plans/` — designed but not specced. If both gates ship with DIFFERENT declaration shapes, every data-emitting feature has TWO declaration languages to learn. **Recommendation:** delay Task #54 Phase 0 by 1-2 days, design Task #50 declaration shape concurrently, ship them with a SHARED `compiler_rule_declarations` infrastructure (single AST walker, single ratchet pattern, two classification namespaces). NOT shipping this coordination = guaranteed re-work in Q3.

**P0-3: Phase 2 substrate-invariant runtime check duplicates `phiscrub/scrubber.go`.** Both surfaces are pattern-matching against the same 14 patterns. **Fix:** Phase 2 invariant should IMPORT `phiscrub` (via cgo or a Python port of the regex catalog) — DO NOT re-implement. If Python port is unavailable, run the invariant as a Go binary against sampled backend responses (cheap subprocess invocation). Single source of truth for patterns is non-negotiable per Master BAA Art. 1.2.

**P1-4: Inline marker vs module-const choice.** Module-const is canonical for operator-allowlisted modules (no per-handler decl). Inline is canonical for customer-facing modules with a mix of `hard-no` + `scrubbed-at-edge` handlers. YAML config = redundant once these two shapes exist.

---

## Lens 4 — Attorney (Master BAA Article 1.2)

**APPROVE.** Article 1.2 names `appliance/internal/phiscrub/scrubber.go` as the authoritative implementation of the PHI-scrubbing safeguard. The pre-merge gate materializes the BAA commitment by:
- Making "scrubbed-at-edge" a declared, auditable property of each new feature
- Forcing engineers to articulate WHICH safeguard category applies before merge
- Producing per-feature evidence of the §164.504(e)(2) safeguards obligation

**One nit:** the `exempt-internal` classification needs a BAA-paragraph cross-reference. Internal substrate dashboards are NOT in scope under Art. 1.2 (they don't cross the appliance boundary), but the classification language should cite "§3 — operator-internal paths under the Substrate's internal-only surfaces" so an auditor traces the legal basis directly to the BAA.

---

## Lens 5 — Product manager

**Backfill cost realism:** N ≈ 200-400 deduplicated paths after operator-allowlisting (per Lens 1 measurement). At 5 min/declaration (read the code → classify → write inline marker → commit), that's 17-33 engineer-hours = **1-2 sprint-weeks** for full drive-down with one engineer. Realistic if scoped tight in Phase 0.

**Declaration-fatigue risk:** real but mitigable.
- IF scope is "every data-emitting AST hit" (~1000+ raw matches): engineers will hate it within 2 days, gate gets bypassed, ratchet never moves.
- IF scope is "every NEW customer-facing handler after gate ships" (Phase 0 ratchets EXISTING count + only NEW work needs declarations): friction stays low.

**Fix:** Phase 0 MUST ratchet at today's count without forcing backfill. Backfill is Phase 1 (separate sprint), and engineering does ~10 declarations per sprint as opportunistic work alongside other changes in those modules. NOT a blocking drive-down.

**Sprint estimate (revised):**
- Phase 0 (ratchet + new-violation gate): 2-3 days
- Phase 1 (opportunistic backfill, 6-12 sprints elapsed): no dedicated sprint, ambient cost
- Phase 2 (substrate invariant): 1 sprint
- Phase 3 (extend to daemon): 1 sprint

Total dedicated effort: ~3 sprints. Reasonable.

---

## Lens 6 — Medical-technical

**APPROVE-WITH-FIXES — two missing classes:**

**P1-1: gRPC payloads at `appliance/internal/grpcserver/` are not covered.** The daemon streams `DriftEvent` / `IncidentReport` / `EvidenceBundle` over gRPC to Central Command. If a new field on a proto message is templated from clinic-side log content, PHI crosses the boundary. The current scrubber operates on the appliance side BEFORE gRPC encoding — but new proto fields can bypass scrubbing if the field name isn't in the scrubbed set. **Fix:** Phase 3 (daemon extension) must include `.proto` field additions as a data-emitting AST class.

**P1-2: Prometheus label cardinality.** `prometheus_metrics.py` exports gauges/counters with labels. If a new label is templated from incident text (e.g., `incident_summary` field), PHI leaks into `/metrics`. The `/metrics` endpoint is operator-internal but is sometimes mirrored to external observability platforms. **Fix:** add `prometheus_client.Counter/Gauge/Histogram(...labels=[...])` as an AST pattern. Label-list literals are pre-merge inspectable.

**P2-3: Realistic clinic-side flow coverage IS good for the 6 named classes** (network egress, log sink, API response, LLM prompt, email/SMS, file export, DB INSERT to customer-visible table). These are the high-leverage PHI paths.

---

## Lens 7 — Legal-internal (Maya + Carol)

**Banned-word scan:** clean. Design uses `monitors / detect / scrubbed / reduces` correctly; no `ensures / prevents / protects / guarantees`. Classification labels are legally precise.

**Classification-language audit:**
- `hard-no` — accurate; matches Art. 1.2's "by construction PHI-free" framing
- `scrubbed-at-edge` — accurate; matches the Art. 1.2 named safeguard
- `n-a-no-egress` — accurate; "doesn't cross customer-facing surface" is the right legal frame
- `exempt-internal` — accurate but RISKY label. "Exempt" is a word that auditors flag. **Fix:** rename to `operator-internal-only` — same semantic, more defensible language.

**Carol-lens (Layer-2 leak posture):** the gate's scope correctly includes the 7 named boundary classes. NOT included = HTTP response headers (`Set-Cookie`, custom headers). Adding header-templating as a Phase 1 AST class is recommended (Session 218 RT33 P2 showed `X-` headers can leak appliance MAC).

---

## Specific cross-cutting verifications

### (a) Declaration mechanism choice — Coach lens
**Verdict:** inline `# phi_boundary: <class> — <reason>` markers attached to FunctionDef + module-level `PHI_BOUNDARY_MODULE_DEFAULT` constant for whole-module allowlisting. YAML file and commit-body parse REJECTED for Phase 0.

### (b) Scope — TEST FILES
**Verdict:** test files EXEMPT-BY-DEFAULT. Add `test_` filename prefix + `tests/` directory exclusion to the AST walker. Synthetic test fixtures don't cross the appliance boundary. Counter-argument: test fixtures that synthetic-mock real PHI are sometimes templated from real PHI — but that's a fixture-hygiene issue, NOT a Rule 2 compiler-gate issue. Keep scope tight.

### (c) Ratchet baseline — order of magnitude
**Measured (backend-only, raw):**
- logger.f-string: 404
- router decorators: 631
- LLM call sites: ~25
- outbound HTTP: 2
- Estimated DEDUPLICATED after operator-allowlist: **200-400 customer-facing paths**

Order of magnitude: **low-hundreds, NOT thousands**. Manageable.

### (d) Phase 2 substrate invariant `phi_boundary_drift`
**Verdict:** SAMPLE-BASED in Phase 2, REPLAY-BASED in Phase 3 (if drift events justify). Sample 1% of customer-facing endpoint responses every 5min, scan with `phiscrub.ContainsPHI`. Cheap. False-positives are operator-actionable (each one is a real Rule 2 question). Replay-based is expensive (re-run last-N-min of traffic) — defer until evidence justifies.

**Severity:** sev1 not sev2 (per Lens 2 finding). Rule 2 is a compiler rule; runtime drift = compiler-rule violation = sev1.

### (e) Cross-task lockstep with Task #50 — **CRITICAL UNRESOLVED**
Task #50 has no plan file. Shipping Task #54 with a Task-#54-only declaration shape will force re-work when Task #50 lands with a different shape. **Recommendation:** add a 1-day Gate A pre-step: design `compiler_rule_declarations` shared infrastructure (single AST walker, single ratchet pattern, classification namespaces per-rule). Both Task #54 (Rule 2) and Task #50 (Rule 1) consume that infrastructure. Ship Task #54 Phase 0 after the shared infra exists.

---

## Recommended Phase 0 minimum-viable scope

1. `tests/test_phi_pre_merge_boundary.py` — AST gate, backend-only (`mcp-server/central-command/backend/*.py`), test-files-exempt
2. Inline marker grammar: `# phi_boundary: <hard-no|scrubbed-at-edge|n-a-no-egress|operator-internal-only> — <reason>` on enclosing FunctionDef
3. Module-level `PHI_BOUNDARY_MODULE_DEFAULT = "operator-internal-only"` constant for whole-module operator allowlisting (mirror `_OPAQUE_MODULES` pattern)
4. AST patterns covered Phase 0: `@router.{get,post,put,patch,delete}` decorators + `logger.{info,warning,error}` calls with f-string args + `send_email(...)` calls + Anthropic/OpenAI client `.messages.create(...)` calls
5. Ratchet `BASELINE_MAX = <measured today>` — gate fails on NEW violations only; backfill is Phase 1
6. Renamed classification `operator-internal-only` (not `exempt-internal`) per Lens 7
7. Operator-allowlist seeded with: `email_alerts.py`, `assertions.py`, `escalation_engine.py`, `prometheus_metrics.py`, `privileged_access_notifier.py`, `substrate_*.py`, `admin_*.py`

**Explicitly OUT of Phase 0:**
- YAML config file (not until Phase 1 proves need)
- Commit-body parse (rejected)
- Daemon Go-side coverage (Phase 3)
- gRPC proto coverage (Phase 3)
- Prometheus label coverage (Phase 1)
- HTTP response header coverage (Phase 1)
- Backfill drive-down (Phase 1, opportunistic)
- Substrate invariant (Phase 2)

---

## Open questions for user-gate

1. **Task #50 lockstep:** ship Task #54 Phase 0 in 2-3 days as standalone, OR delay 1 day to design `compiler_rule_declarations` shared infra with Task #50? Coach strongly prefers the coordinated approach.
2. **Phase 2 substrate invariant severity:** sev1 (compiler-rule violation framing) or sev2 (operator-noise framing)? Counsel Rule 2 reads as sev1.
3. **Operator-allowlist seed list:** who reviews? Maya for legal classification of each entry, or coach-only?
4. **Daemon-side Go gate (Phase 3):** Go AST tooling is rougher than Python's. Worth a Phase 3 spec now, or defer until Phase 2 ships?

---

## Final recommendation

**APPROVE-WITH-FIXES**

Top 3 P0 findings (must close before Phase 0 ships):

1. **Coach P0-1 (declaration mechanism over-build):** drop YAML file + commit-body parse from Phase 0. Ship ONE canonical surface (inline marker on FunctionDef + module-const) mirroring the opacity gate's two-list shape. Re-introduce additional surfaces only if Phase 1 evidence justifies.

2. **Coach P0-2 (Task #50 lockstep unresolved):** design `compiler_rule_declarations` shared infrastructure before Task #54 Phase 0 ships, OR explicitly accept the re-work cost when Task #50 lands. User-gate question.

3. **Coach P0-3 (Phase 2 invariant double-build):** Phase 2 substrate invariant MUST import `phiscrub` pattern catalog (subprocess call or Python port). DO NOT re-implement the 14-pattern regex set — Master BAA Art. 1.2 names `scrubber.go` as the authoritative implementation; a parallel Python pattern set is per-se a Rule 2 violation.

Top secondary P1 findings (close before Phase 1 ships):
- Engineering P0-1 (proximity rule → enclosing-function rule)
- Auditor P1-1 (per-PR audit artifact materialization)
- Medical-tech P1-1 + P1-2 (gRPC proto + Prometheus labels)
- Legal P0-3 (rename `exempt-internal` → `operator-internal-only`)

Phase 0 is shippable in 2-3 engineer-days after these P0s close. Backfill is opportunistic Phase 1. Substrate invariant is Phase 2 (1 sprint). Daemon extension is Phase 3 (1 sprint).

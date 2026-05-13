# PHI-Pre-Merge Gate Design v2 (Task #54, Counsel Rule 2)

> **v2 changes (Gate A APPROVE-WITH-FIXES, P0 #1 + P0 #3 applied 2026-05-13):**
>
> **P0 #1 (Coach: declaration-mechanism over-build):** Collapsed 4 surfaces (YAML + inline + module-const + commit-body) into ONE pattern mirroring the opacity gate: **inline marker** (`# phi_boundary: <classification> — <reason>`) on the enclosing FunctionDef + **module-level constant** (`PHI_BOUNDARY_MODULE_DEFAULT = "<classification>"`) for whole-module defaults. The YAML config and commit-body parse are REMOVED — over-build per Coach.
>
> **P0 #3 (Coach: Phase 2 substrate invariant double-builds `phiscrub`):** Phase 2 substrate invariant `phi_boundary_drift` MUST consume the daemon Go scrubber's pattern catalog (`appliance/internal/phiscrub/scrubber.go`) — NOT parallel-build patterns in Python. Implementation options: (a) shell-subprocess invocation of a Go-side `scrubber_export_patterns_json` helper at substrate-engine startup; (b) one-shot Python port of the pattern strings with a CI gate (`test_phiscrub_pattern_python_parity.py`) that asserts the Python copy matches the Go source. Either way, the Go source is the single source of truth — parallel pattern set is itself a Rule 2 violation per Master BAA Article 1.2.
>
> **P0 #2 (Task #50 shared `compiler_rule_declarations` infra):** STILL USER-GATED. Pending user decision on option a (build shared infra now) vs option b (separate-per-rule + Q3 reconcile) vs option c (pause Task #54 until Task #50 ships first). Design v2 below assumes option (b) as engineering default — separate declaration mechanism for Rule 2, with sketched migration path to shared infra if user chooses (a) later.
>
> **Other v2 changes per Gate A:**
> - Renamed `exempt-internal` → `operator-internal-only` per Lens 7 legal-defensibility (clearer that it's a substrate-internal use, not a "we ignore Rule 2 here" exemption).
> - Phase 1+ scope items (YAML config, commit-body parse, daemon Go gate, gRPC, Prometheus labels) explicitly excluded from Phase 0 per PM lens.
> - Phase 0 AST coverage: router decorators + logger f-strings + send_email + LLM `.messages.create(...)`. Test-files exempt by default.
> - Ratchet at today's count (~200-400 paths backfill, 1-2 sprint-weeks — Phase 1 opportunistic, NOT dedicated drive-down).

> **Counsel Rule 2 (gold authority, 2026-05-13):** *"No raw PHI crosses the appliance boundary. PHI-free Central Command is a compiler rule, not a posture preference. Every new data-emitting feature (endpoint, log sink, LLM prompt, export, email template) MUST answer at merge time: 'Could raw PHI cross this boundary?' If the answer is not a hard no, it does not ship."*

> **Multi-device-enterprise lens:** at N customers × M data-emitting features, every uncategorized feature is N false-positive PHI risks simultaneously. The current runtime defense is `appliance/internal/phiscrub/scrubber.go` (14 patterns at appliance edge). This task converts the posture into a **pre-merge compiler rule**: every new data-emitting feature must declare its PHI boundary state before merge.

---

## §1 — Scope: data-emitting feature classes

A "data-emitting feature" is any code path that emits content the platform did not previously emit, AT ANY OF THESE BOUNDARIES:

| Boundary class | Examples | Why it matters for Rule 2 |
|---|---|---|
| **Network egress (outbound)** | HTTP POST to subprocessor (Anthropic / SendGrid / PagerDuty / OpenAI), webhook delivery, SMTP send | Subprocessor sees the payload; if PHI leaks, Rule 2 violated at the appliance boundary |
| **Persisted log sink** | `logger.info(...)` with payload-templated content, structured slog, sentry-style error reports | Logs are PHI-bearing if the templated content is | 
| **External API response (customer-facing)** | `@router.get/post/put` returning a Pydantic response | Customer's browser sees the payload; if PHI templated through, leaked |
| **LLM prompt** | `l2_planner.py` Anthropic/OpenAI/Azure-OpenAI call payload | LLM subprocessor sees the prompt; PHI must be scrubbed before construction |
| **Email/SMS template** | Jinja2 template render, in-line f-string subject/body | Email-subject Rule 7 + PHI Rule 2 both implicated |
| **File export** | Auditor kit ZIP, F-series PDF, CSV export | Customer/auditor receives the file; if PHI leaked, Rule 2 violated |
| **Database INSERT into customer-visible table** | tables read by `/api/client/*` endpoints | If template field is PHI-bearing, downstream queries leak |

Internal-only paths (admin substrate dashboards, `/admin/substrate-health`, operator-only metrics) are out of scope — these are operator-internal and don't cross the appliance boundary by definition.

---

## §2 — Pre-merge gate shape

**Declaration mechanism (v2 — single pattern per Gate A P0 #1):** every PR introducing a new data-emitting feature MUST include EITHER:

1. **Inline marker** on the enclosing FunctionDef:

   ```python
   @router.post("/...")
   async def new_endpoint(...):
       # phi_boundary: hard-no — endpoint operates on site_id/org_id only,
       # never on patient-bearing payloads; tenant-metadata only per
       # §164.514(b) safe harbor.
       ...
   ```

2. **Module-level constant** for whole-module defaults:

   ```python
   # Top of module:
   PHI_BOUNDARY_MODULE_DEFAULT = "operator-internal-only"
   ```

   Module-level default applies unless an inline marker overrides per function.

YAML config and commit-body parse are NOT used (over-build per Coach). The opacity-gate-precedent two-surface shape (inline + module-const) is the canonical pattern mirror.

**Classification semantics (v2):**

- **`hard-no`** — by construction, PHI cannot reach this code path. Example: tenant metadata (org_id, site_id, count of bundles) which is non-PHI per §164.514(b) safe harbor.
- **`scrubbed-at-edge`** — PHI is potentially present at the source but is filtered by the appliance-edge `phiscrub` (14 patterns) before reaching Central Command. The feature only sees scrubbed content.
- **`n-a-no-egress`** — the feature does not emit content outside Central Command. Internal-only paths.
- **`operator-internal-only`** (renamed from `exempt-internal` per Gate A Lens 7 legal-defensibility) — substrate-internal use only (admin dashboards, Prometheus); doesn't cross customer-facing surfaces. This is NOT a Rule 2 exemption — operator paths are still subject to Rule 2 if they later become customer-visible; the marker just declares "today this is operator-only, gate is satisfied."

If the answer is NOT one of these four, the PR fails the gate. **No "TBD" / "maybe" — Rule 2 is a compiler rule.**

---

## §3 — CI gate detection strategy

`tests/test_phi_pre_merge_boundary.py` — AST-based detector with declaration lookup:

```python
DATA_EMITTING_AST_PATTERNS = [
    # FastAPI route definitions
    "@router.get|post|put|delete|patch decorators",
    # Logger calls with templated content
    "logger.{info,warning,error}(f\"...\")",  # f-string in arg
    # SMTP send / SendGrid / outbound HTTP
    "httpx.{post,get,put}", "requests.{post,get,put}", "aiohttp.session.{post,get,put}",
    # SMTP via smtplib
    "smtplib.SMTP",
    # LLM call sites
    "ANTHROPIC_API_KEY|OPENAI_API_KEY|AZURE_OPENAI_*",
    # Jinja2 render
    "Environment().get_template().render(...)",
    # PagerDuty
    "events.pagerduty.com",
]

PHI_BOUNDARY_DECLARATION_SOURCES = [
    # Inline marker on enclosing FunctionDef (per v2 P0 #1 fix):
    "# phi_boundary: <classification> — <reason>",
    # Module-level constant for whole-module defaults:
    "PHI_BOUNDARY_MODULE_DEFAULT = '<classification>'",
    # YAML file + commit-body parse: REMOVED in v2 per Gate A P0 #1
    # (over-build per Coach lens). Single-pattern declaration mirrors
    # the opacity gate precedent.
]
```

**Detection algorithm (v2):**
1. AST-walk every backend `.py` file.
2. For each match of `DATA_EMITTING_AST_PATTERNS`, attach the marker check to the **enclosing FunctionDef** (NOT a ±N-line proximity heuristic — Lens 1 N2 flagged proximity as brittle). The inline marker comment must appear as a leading comment inside the function body OR the module-level `PHI_BOUNDARY_MODULE_DEFAULT` constant must provide a default.
3. If no declaration found → gate FAILS with the AST node line number + suggested declarations.
4. Ratchet baseline (`BASELINE_MAX = <today's count>`) — drive-down to zero is incremental.

**False-positive guard:** the gate is intentionally over-broad on the data-emitting pattern; the declaration mechanism is intentionally lightweight. Better to over-declare and have engineers articulate the boundary explicitly than to miss a leak.

---

## §4 — Implementation plan

**Phase 0:** ship `tests/test_phi_pre_merge_boundary.py` with `BASELINE_MAX = <today's count>`. Ratchet at current state. New violations fail CI; baseline drives to zero via declaration backfill.

**Phase 1:** drive-down — backfill `phi_boundary` declarations on the ~N existing data-emitting paths. Each declaration is a small commit with the classification + reason inline. Coach pass per declaration.

**Phase 2:** substrate invariant `phi_boundary_drift` (sev2). Per Gate A P0 #3: this invariant **MUST CONSUME the daemon Go scrubber's pattern catalog** (`appliance/internal/phiscrub/scrubber.go`) — NOT parallel-build patterns in Python. Master BAA Article 1.2 names the Go scrubber as the authoritative implementation; a parallel pattern set is itself a Rule 2 violation. Two implementation options:

  - **Option A (preferred):** shell-subprocess invocation of a new Go-side `scrubber_export_patterns_json` helper at substrate-engine startup; pattern catalog cached in-process per-tick.
  - **Option B (fallback if A is too complex):** one-shot Python port of the pattern strings + a CI gate (`test_phiscrub_pattern_python_parity.py`) that asserts the Python copy matches the Go source byte-for-byte. Periodic gate runs catch divergence.

Either way, the Go source is the single source of truth. Layer-2 safety net per Session 220 Backend-First pattern.

**Phase 3:** scope expansion — extend the gate from `mcp-server/central-command/backend/` to also cover the daemon (`appliance/internal/`) for the on-appliance scrub side. Today's `phiscrub/scrubber.go` is the gate's target; extending to NEW go files at the appliance edge would catch a daemon-side new-feature that bypasses scrubbing.

---

## §5 — Multi-device-enterprise lens

At N customers × M data-emitting features:
- Without gate: every new feature is N false-positive PHI risks
- With gate at merge time: every new feature has explicit boundary classification
- With substrate invariant runtime-detection (Phase 2): drift alerts catch declaration-vs-runtime mismatch

The compiler-rule conversion is what enterprise-scale credibility requires. "We have a posture" is weak; "the platform structurally cannot ship a new feature without answering the PHI question" is strong.

---

## §6 — Open questions for Class-B Gate A

- (a) Declaration mechanism — RESOLVED in v2 per Gate A P0 #1: inline + module-const, mirroring opacity-gate precedent.
- (b) Should the gate run on EVERY PR (pre-merge) or only on PRs touching specific module-globs (data-emitting modules)?
- (c) Phase 2 substrate invariant — sample-based or replay-based? Sampling is cheap but may miss; replay is expensive but comprehensive.
- (d) Does the gate apply to TEST FILES? Test fixtures often template synthetic data — should they be exempt-by-default or declared?
- (e) Backfill effort estimate — depends on `BASELINE_MAX` today; ~N existing data-emitting paths, ~5 min per declaration, so ~5×N minutes total. Realistic timeline: 1-2 sprints for full drive-down.
- (f) Should this gate's declaration format share schema with the canonical-source registry (Task #50) for consistency? E.g. both use AST-node-match + per-line markers + ratchet baseline.

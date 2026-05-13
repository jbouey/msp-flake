# PHI-Pre-Merge Gate Design (Task #54, Counsel Rule 2)

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

**Declaration mechanism:** every PR introducing a new data-emitting feature MUST include in the commit body OR a designated `phi_boundary.yaml` config:

```yaml
phi_boundary:
  - feature: <function-name or endpoint path>
    classification: hard-no | scrubbed-at-edge | n-a-no-egress | exempt-internal
    reason: <one-line justification>
```

**Classification semantics:**

- **`hard-no`** — by construction, PHI cannot reach this code path. Example: the feature operates only on tenant metadata (org_id, site_id, count of bundles) which is non-PHI per §164.514(b) safe harbor.
- **`scrubbed-at-edge`** — PHI is potentially present at the source but is filtered by the appliance-edge `phiscrub` (14 patterns) before reaching Central Command. The feature only sees scrubbed content.
- **`n-a-no-egress`** — the feature does not emit content outside Central Command. Internal-only paths.
- **`exempt-internal`** — substrate-internal use only (admin dashboards, Prometheus); doesn't cross customer-facing surfaces.

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
    # In-line comment marker (recommended for narrow paths)
    "# phi_boundary: <classification> — <reason>",
    # Module-level constant for multiple paths
    "PHI_BOUNDARY = {<func_name>: (<classification>, <reason>), ...}",
    # YAML file in repo
    "mcp-server/central-command/backend/phi_boundary.yaml",
]
```

**Detection algorithm:**
1. AST-walk every backend `.py` file.
2. For each match of `DATA_EMITTING_AST_PATTERNS`, look for a paired declaration within ±10 lines OR in the module-level constant OR in the YAML file.
3. If no declaration found → gate FAILS with the AST node line number + suggested declarations.
4. Ratchet baseline (`BASELINE_MAX = <today's count>`) — drive-down to zero is incremental.

**False-positive guard:** the gate is intentionally over-broad on the data-emitting pattern; the declaration mechanism is intentionally lightweight. Better to over-declare and have engineers articulate the boundary explicitly than to miss a leak.

---

## §4 — Implementation plan

**Phase 0:** ship `tests/test_phi_pre_merge_boundary.py` with `BASELINE_MAX = <today's count>`. Ratchet at current state. New violations fail CI; baseline drives to zero via declaration backfill.

**Phase 1:** drive-down — backfill `phi_boundary` declarations on the ~N existing data-emitting paths. Each declaration is a small commit with the classification + reason inline. Coach pass per declaration.

**Phase 2:** substrate invariant `phi_boundary_drift` (sev2) — sample customer-facing endpoint responses against a per-feature PHI-pattern detector; alerts if any data-emitting feature unexpectedly produces content matching any of the 14 scrubber patterns. Layer-2 safety net per Session 220 Backend-First pattern.

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

- (a) Declaration mechanism — commit-body parse vs YAML file vs inline comment vs module-level constant? Coach lens preference.
- (b) Should the gate run on EVERY PR (pre-merge) or only on PRs touching specific module-globs (data-emitting modules)?
- (c) Phase 2 substrate invariant — sample-based or replay-based? Sampling is cheap but may miss; replay is expensive but comprehensive.
- (d) Does the gate apply to TEST FILES? Test fixtures often template synthetic data — should they be exempt-by-default or declared?
- (e) Backfill effort estimate — depends on `BASELINE_MAX` today; ~N existing data-emitting paths, ~5 min per declaration, so ~5×N minutes total. Realistic timeline: 1-2 sprints for full drive-down.
- (f) Should this gate's declaration format share schema with the canonical-source registry (Task #50) for consistency? E.g. both use AST-node-match + per-line markers + ratchet baseline.

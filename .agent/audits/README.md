# Repeatable consistency audits

Spawnable with the Agent tool. Each `.prompt.md` here is a self-contained
prompt for an Explore- or general-purpose subagent that audits one
"double-flywheel-class" consistency dimension.

## Origin

Session 210-B 2026-04-25 — after a day of running into shadowed code
(`flywheel_promotion_loop` × 2, `learning_api_main.py` × ~700 lines,
column-name typos against the live schema, broken Go test counts), we
needed a way to *catch this class proactively* without needing a
specific bug to chase.

5 parallel workers found 4 P0 bugs that would have failed at runtime,
plus several P1 quality bumps. The cost was ~5 min wall clock; the
value was ~6 hours of forensics avoided. Worth running on a cadence.

## Cadence

Run all 5 in parallel:
- After any major refactor (anything that touches background_tasks,
  routes, or migrations).
- Before a release-tag commit.
- Weekly during stable periods (Mondays — Friday's incidents are
  freshest in memory).

Don't gate every PR on these — they're broad-spectrum scans, not
per-change tests. The strict CI tests (`test_lifespan_imports_resolve`,
`test_sql_columns_match_schema`, `test_three_list_lockstep_pg`,
`test_no_new_duplicate_pydantic_model_names`) are the per-PR gate.

## Polarity rule (lessons from 210-B)

When an audit finds a duplicate, **do NOT auto-delete either side**.
First determine polarity:
1. Which one is *imported*? (Use grep / AST.)
2. Which one's path appears in mounted routers / task_defs?
3. Which one's last commit is more recent?
4. If still ambiguous, ASK before deleting.

The 210-B audit had the polarity reversed for the
`learning_api_main.py` finding — fortunately the operator caught it
before any deletion happened. Lock the polarity-determination step in
to any future audit-fix automation.

## Worker dispatch

```python
# Pseudo-code — actual dispatch is via the Agent tool with these
# prompts as the `prompt` field.
parallel([
    Agent(subagent_type="Explore",
          prompt=open(".agent/audits/duplicate_functions.prompt.md").read()),
    Agent(subagent_type="general-purpose",
          prompt=open(".agent/audits/sql_schema_columns.prompt.md").read()),
    Agent(subagent_type="general-purpose",
          prompt=open(".agent/audits/three_list_lockstep.prompt.md").read()),
    Agent(subagent_type="Explore",
          prompt=open(".agent/audits/bg_tasks_registry.prompt.md").read()),
    Agent(subagent_type="general-purpose",
          prompt=open(".agent/audits/endpoint_test_coverage.prompt.md").read()),
])
```

Each prompt is < 500 words. Combined output is < 5000 words. Triage
follows the polarity rule above; commits batch the verified P0+P1
fixes with a separate documentation note for false positives.

# Endpoint-vs-test coverage gap audit

Use with: `Agent(subagent_type="general-purpose", prompt=<this file's contents>)`.

---

Audit the Msp_Flakes Python backend (cwd: /Users/dad/Documents/Msp_Flakes) for endpoints that ship without ANY guard test.

Background: on 2026-04-25 the relocate endpoint had multiple schema bugs (fleet_orders columns, admin_audit_log column) that source-level tests didn't catch because they weren't asserting against actual schema. We want to identify the riskiest endpoints — newly-shipped + state-changing + WITHOUT any test, source-level or otherwise.

Task:

1. Enumerate every FastAPI route decorator in `mcp-server/central-command/backend/*.py` and `mcp-server/main.py`. Patterns: `@router.post(`, `@router.get(`, `@router.patch(`, `@router.delete(`, `@app.post(`, `@app.get(`, `@router.put(`, `@app.put(`. Capture the method + path.

2. For each, search `mcp-server/central-command/backend/tests/*.py` for any reference to the path string OR the handler function name.

3. Bucket endpoints into:
   - **TESTED** — at least one test file mentions the path OR handler function name
   - **UNTESTED state-changing** — POST/PUT/PATCH/DELETE with NO matching test
   - **UNTESTED read-only** — GET only, no test (lower priority)

4. For each UNTESTED state-changing endpoint, note:
   - The decorator file:line
   - Whether the handler signature has Depends(require_admin/require_operator/require_partner) — gating depth
   - Whether it does INSERT/UPDATE/DELETE on protected tables (api_keys, site_appliances, fleet_orders, compliance_bundles, admin_audit_log, sites)

Skip:
- `venv/`, `archived/`, `.claude/worktrees/`, `vendor/`, `node_modules/`
- Routes that are only HEAD/OPTIONS variants of GETs
- Routes whose path includes `/test/` or the handler name starts with `_dev_`

Report under 500 words:

- Total endpoint count + tested vs untested breakdown
- TOP 10 riskiest UNTESTED state-changing endpoints (those touching protected tables, gated only by Depends but no behavior tests)
- For each: file:line + path + handler + a 1-line "what test would cover this" suggestion
- If <5 untested state-changing endpoints exist: list them all

**Trend awareness:** prior audits documented these candidates. Re-confirm or note progress:
- `provisioning.py:701 /rekey` (no auth, mutates api_keys + site_appliances)
- `provisioning.py:818 /admin/restore` (require_admin, mutates audit_log + api_keys)
- `iso_ca.py:301 /provision/claim-v2` (no auth, mutates api_keys + site_appliances)
- `routes.py:6550 /sites/{site_id}/decommission`
- `sites.py:2371 /{site_id}/appliances/{id}/move` (legacy /move, sister to the new /relocate)

Read-only — don't write or edit anything.

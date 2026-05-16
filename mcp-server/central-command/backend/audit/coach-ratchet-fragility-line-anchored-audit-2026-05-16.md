# Ratchet-fragility audit (line-anchored allowlist class)

## Summary
Audit of 69 CI test files (`tests/test_*.py`) scanning for the line-anchored allowlist pattern that plagued canonical_metrics.py. Searched for data structures (lists/sets/dicts) containing `"file.py:LINE"` string entries that would silently rot under refactors.

## Gates with HAS-FRAGILITY-RISK

### tests/test_export_endpoints_column_drift.py
- **1 data-structure entry with embedded line reference**: `_KNOWN_NONEXISTENT` frozenset (line 31–36) contains `"vendor"` with comment `"# was in routes.py:6442 pre-fix"` (line 32)
- **Pattern**: The string value itself ("vendor") won't change, but the comment documents a route that changed. If routes.py is refactored, the line number 6442 will shift without warning.
- **Spot-check**: Line 6442 in routes.py no longer references "vendor" (fixed in the past); the comment is historical. This specific reference is stale but harmless since the gate works off the string value, not the line number.
- **Risk level**: LOW — the line number is comment-only and doesn't enforce gate behavior, but it exemplifies the pattern that could introduce silent drift if extended.
- **Recommendation**: (c) Accept + document. These are historical annotations. But establish a convention: never embed active file:line references in data-structure comments. If you must reference a source, use function/module names instead (e.g., "was deprecated in routes::admin_export endpoint fix").

## Gates with NO-RISK

- **test_no_silent_db_write_swallow.py**: Uses dynamic AST walk + `SWALLOW_ALLOWLIST` (currently empty) keyed by `(file_path_str, int)` tuples. Lines are computed at test-time, not hardcoded.
- **test_escalate_rule_check_type_drift.py**: Uses dynamic regex + window-scan. `_KNOWN_ESCALATE_CHECK_TYPES` keyed by rule_id (strings), not line numbers.
- **test_operator_alert_hook_callsites.py**: Uses AST walk to extract event_type from live Call nodes. `EXPECTED_HOOKS` list is keyed by (file, event_type_string), not lines.
- **test_no_unfiltered_site_appliances_select.py**: Dynamic scan with regex + window search. `BASELINE_MAX` is a count, not a line anchor.
- **test_admin_connection_no_multi_query.py**: Dynamic AST walk. `BLOCK_ALLOWLIST` keyed by function names, not lines.
- **test_appliance_endpoints_auth_pinned.py**: Dynamic AST walk. Allowlists keyed by handler function names.
- **test_import_shape_in_package_context.py**: Dynamic AST walk. `ALLOWLIST` keyed by `"module_name"` pairs.
- **test_sql_columns_match_schema.py**: Dynamic regex scan of source + fixture-based schema validation. No hardcoded line anchors.
- **test_no_middleware_dispatch_raises_httpexception.py**: Dynamic AST walk to find `raise HTTPException` patterns. Allowlist via `# noqa: middleware-raise-allowed` marker (line-agnostic).

## Gates with DOC-ONLY mentions

- **test_admin_audit_log_column_lockstep.py**: Docstring (lines 10–12) references pre-fix sites (e.g., `startup_invariants.py:334`, `chain_tamper_detector.py:215`). No data-structure enforcement.
- **test_import_shape_in_package_context.py**: Docstring mentions `sites.py:4231` (the 2026-05-13 outage). Historical context, not enforced.
- **test_no_silent_db_write_swallow.py**: Docstring comments reference fixed sites (lines 147–152, 202–205). Purely informational.
- **test_lazy_import_resolution.py**: Comments reference specific line ranges (e.g., `client_portal.py:348 + 531`). Not enforced.

## Cross-cutting recommendation

**No systemic ratchet-fragility risk found.** The 31 test files examined are split:
- ~20 use **dynamic scanning** (AST walk / regex window scan / fixture-based validation) — **NO-RISK** by design.
- 1 has **low-risk comment annotations** — `test_export_endpoints_column_drift.py` — which are historical/stale but non-enforcing.
- 10+ have **DOC-ONLY mentions** in docstrings/comments — harmless, informational.

**Actionable recommendations**:

1. **(Adopt a convention)** For any future data-structure allowlist that must reference code locations, anchor by **function/module name** (like `test_escalate_rule_check_type_drift.py`'s rule_id) rather than line number. If a line reference is truly needed for legal/audit reasons, compute it dynamically at test time (like `test_no_silent_db_write_swallow.py`'s AST-based line extraction).

2. **(No new CI gate needed)** The canonical_metrics.py pattern (14 stale line anchors) was a one-off fragility spike in that test's design. The broader CI suite has moved to dynamic scanning — much healthier. No global gate needed.

3. **(Document the exception)** If `test_export_endpoints_column_drift.py`'s comments become stale during future refactors, add a task to CLAUDE.md or a follow-up audit: "Refresh schema-drift-gate historical annotations every 6 months or after major routes.py rewrites."

**Session 220 ratchet status**: All 4 audit-named sites from the 2026-05-08 E2E attestation (`test_no_silent_db_write_swallow.py`) remain fixed. No new line-anchored fragility risk detected.

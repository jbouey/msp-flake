"""CI gate: every partner-portal /me/* GET handler that reads from
site-scoped tables MUST filter by partner_id at the application layer.

Round-table 2026-05-09 partner-portal runtime adversarial audit
(audit/coach-partner-portal-runtime-audit-2026-05-09.md, P1-2).

ROOT CAUSE the gate prevents from regressing: partners.py defends
cross-partner data leakage via 65 explicit `WHERE s.partner_id = $1`
filters. Migration 297 (P1-1) added DB-level last-line defense via
`tenant_partner_isolation` RLS policies, but only on 6 of 9 audit
tables — `sites`, `discovered_assets`, `discovery_scans` are deferred
because they have no RLS today. Until those land, the application-
layer WHERE filter is the ONLY barrier. One forgotten filter = silent
cross-partner leak.

This gate AST-walks partners.py and asserts every @router.get on a
`/me/...` path that reads from a site-scoped table contains a literal
`partner_id` reference in its function body, OR is in an explicit
allowlist with rationale.

Sibling gate to `test_partner_mutations_role_gated.py` — that one
covers WRITES (role-gating). This one covers READS (partner_id filter).

Limitations:
  - Uses string-presence detection on the function source, not full
    SQL parsing. False negatives are possible if a query references
    partner_id only via a CTE/JOIN buried in concatenated SQL.
    Belt-and-suspenders: the partner-RLS migration 297 covers the
    same class at the DB layer for 6 of 9 tables.
"""
from __future__ import annotations

import ast
import pathlib

import pytest


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_PARTNERS_PY = _BACKEND / "partners.py"

# Site-scoped tables that the audit identified as cross-partner-leak
# risk. Any /me/* GET handler reading from one of these tables MUST
# scope by partner_id (either inline `WHERE s.partner_id = $...` or
# via a CTE that does so).
SITE_SCOPED_TABLES = {
    "sites",
    "site_appliances",
    "compliance_bundles",
    "incidents",
    "execution_telemetry",
    "discovered_assets",
    "site_credentials",
    "discovery_scans",
    "admin_orders",
}

# Endpoints that legitimately do NOT need a partner_id filter because
# they read from a per-user / per-partner table that's already keyed
# on the caller's partner identity (e.g. partner_users notifications).
# Add with explicit justification.
PARTNER_ID_FILTER_ALLOWLIST: dict[str, str] = {
    # GET /me — reads from `partners` table directly using the
    # caller's own partner_id; no cross-partner risk.
    "get_my_partner": "reads partners table by caller's own id",
    # GET /me/onboarding — reads partner-level onboarding state.
    "get_partner_onboarding": "partner-level state, no site reads",
    # GET /me/digest-prefs — reads partner-level prefs.
    "get_partner_digest_prefs": "partner-level prefs, no site reads",
    # GET /me/digest/preview — assembles digest from partner-scoped
    # background data; uses the partner_id internally already.
    "preview_partner_digest": "delegates to digest builder w/ partner_id",
    # GET /me/branding — partner-level branding.
    "get_my_branding": "partner-level state",
    # GET /me/commission — partner-level commission ledger.
    "get_my_commission": "partner-level ledger, joined by partner_id",
    # GET /me/notifications — reads partner_notifications scoped by
    # partner_user_id (more restrictive than partner_id).
    "get_partner_notifications": "scoped by partner_user_id",
    # GET /me/users — list_my_partner_users reads partner_users
    # directly keyed on caller's partner_id.
    "list_my_partner_users": "partner_users keyed on caller's partner_id",
    # GET /me/provisions — provision codes scoped by caller's partner_id.
    "list_provision_codes": "partner_id-keyed table",
    # GET /me/provisions/{provision_id}/qr — provision QR scoped by
    # caller's partner_id; ownership check inside.
    "get_provision_qr_code": "ownership-checked by partner_id internally",
}


def _is_router_get(decorator: ast.Call) -> tuple[bool, str]:
    """Returns (is_get, path) for @router.get(...)."""
    func = decorator.func
    if not isinstance(func, ast.Attribute):
        return False, ""
    if not (isinstance(func.value, ast.Name) and func.value.id == "router"):
        return False, ""
    if func.attr != "get":
        return False, ""
    path = ""
    if decorator.args and isinstance(decorator.args[0], ast.Constant):
        path = str(decorator.args[0].value)
    return True, path


def _function_source(src: str, node: ast.AsyncFunctionDef) -> str:
    """Extract the function body source for grep checks."""
    lines = src.splitlines()
    # ast lineno is 1-indexed; end_lineno may be None on older Python
    end = node.end_lineno or len(lines)
    return "\n".join(lines[node.lineno - 1 : end])


def _reads_site_scoped_table(body_src: str) -> set[str]:
    """Return the set of site-scoped tables this function reads from
    (via FROM/JOIN). Lowercase match, simple substring detection."""
    src_lower = body_src.lower()
    found: set[str] = set()
    for tbl in SITE_SCOPED_TABLES:
        # Match `from <tbl>` or `join <tbl>` with word-boundary-like
        # check to avoid e.g. `incidents_history` matching `incidents`.
        for kw in ("from ", "join "):
            idx = 0
            while True:
                idx = src_lower.find(kw + tbl, idx)
                if idx == -1:
                    break
                # Confirm next char is whitespace, comma, newline, or alias
                tail_pos = idx + len(kw) + len(tbl)
                if tail_pos >= len(src_lower):
                    found.add(tbl)
                    break
                tail = src_lower[tail_pos]
                if tail in (" ", "\n", "\t", ",", "\r"):
                    found.add(tbl)
                    break
                idx = tail_pos
    return found


def _filters_by_partner_id(body_src: str) -> bool:
    """True iff the function body contains a `partner_id` reference
    in a query context. Heuristic: any literal `partner_id` token
    suffices — `s.partner_id = $1` is the canonical shape but a CTE
    or subquery may use different aliasing. False positives are
    acceptable (the gate is permissive); false negatives are not."""
    return "partner_id" in body_src


def test_partner_get_endpoints_filter_by_partner_id():
    """Every @router.get on a /me/... endpoint that reads from a
    site-scoped table MUST contain a literal `partner_id` reference
    in its function body, OR be in PARTNER_ID_FILTER_ALLOWLIST.

    Pre-fix (audit P1-2, 2026-05-09): NO CI gate asserted partner_id
    presence on partner-portal reads. RT31 covered role-gating only
    (writes). Adding a new /me/* GET that forgets the partner_id
    WHERE clause = silent cross-partner data leak until the deferred
    RLS migration covers `sites`/`discovered_assets`/`discovery_scans`.
    """
    src = _PARTNERS_PY.read_text()
    tree = ast.parse(src)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            is_get, path = _is_router_get(dec)
            if not is_get:
                continue
            if not path.startswith("/me/") and path != "/me":
                continue
            if node.name in PARTNER_ID_FILTER_ALLOWLIST:
                continue
            body_src = _function_source(src, node)
            tables = _reads_site_scoped_table(body_src)
            if not tables:
                continue
            if _filters_by_partner_id(body_src):
                continue
            violations.append(
                f"partners.py:{node.lineno} GET {path} → {node.name}: "
                f"reads from {sorted(tables)} but contains no "
                f"`partner_id` reference. Cross-partner leak risk — "
                f"partner-portal queries MUST scope by partner_id."
            )

    assert not violations, (
        "Partner-side GET endpoints reading site-scoped tables must "
        "filter by partner_id at the application layer. Migration 297 "
        "added DB-level RLS for 6 of 9 audit tables; sites, "
        "discovered_assets, discovery_scans remain code-defense only. "
        "Add `WHERE s.partner_id = $1` (or equivalent CTE filter), or "
        "add the function name to PARTNER_ID_FILTER_ALLOWLIST with "
        "justification if the read is partner-keyed via a different "
        "column.\n\n" + "\n".join(f"  - {v}" for v in violations)
    )


def test_migration_297_helper_function_is_stable():
    """The partner-RLS helper function MUST be STABLE (or IMMUTABLE)
    so the planner can hoist its calls across rows of the same query.
    Mirrors the same gate in test_org_scoped_rls_policies.py for the
    org-side helper."""
    import re

    mig = _BACKEND / "migrations" / "297_partner_scoped_rls_for_site_tables.sql"
    assert mig.exists(), "mig 297 missing"
    src = mig.read_text()
    helper_block = re.search(
        r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+rls_site_belongs_to_current_partner.*?\$\$",
        src, re.IGNORECASE | re.DOTALL,
    )
    assert helper_block, "rls_site_belongs_to_current_partner not found"
    assert (
        "STABLE" in helper_block.group(0).upper()
        or "IMMUTABLE" in helper_block.group(0).upper()
    ), "Partner RLS helper must be STABLE or IMMUTABLE for planner hoisting"


def test_migration_297_keys_on_partner_id():
    """Spot-check: the policy MUST key on sites.partner_id (not
    client_org_id, not partner_user_id). Cross-check with the column
    that partners.py WHERE filters use (s.partner_id)."""
    mig = _BACKEND / "migrations" / "297_partner_scoped_rls_for_site_tables.sql"
    src = mig.read_text()
    assert "tenant_partner_isolation" in src
    assert "rls_site_belongs_to_current_partner" in src
    assert "s.partner_id::text = current_setting('app.current_partner_id'" in src
    # Sanity: 6 covered tables present in the apply-list.
    for tbl in ("site_appliances", "compliance_bundles", "incidents",
                "execution_telemetry", "site_credentials", "admin_orders"):
        assert f"'{tbl}'" in src, f"mig 297 missing apply-list table {tbl}"


def test_partner_connection_helper_exists_in_tenant_middleware():
    """The `partner_connection` helper in tenant_middleware.py is the
    canonical migration target for endpoints to switch from
    admin_connection to partner-RLS-enforced reads. Pin its presence
    so a refactor can't silently drop it."""
    src = (_BACKEND / "tenant_middleware.py").read_text()
    assert "async def partner_connection(" in src, (
        "tenant_middleware.partner_connection helper missing — required "
        "by mig 297 partner-scoped RLS. Endpoints converting from "
        "admin_connection to RLS-enforced partner reads need this "
        "context manager to set app.current_partner_id via SET LOCAL."
    )
    assert "app.current_partner_id" in src, (
        "tenant_middleware must SET LOCAL app.current_partner_id for "
        "the mig 297 RLS policy to admit rows."
    )

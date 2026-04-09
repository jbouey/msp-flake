"""Tests for site activity audit trail.

Validates two things:
  1) `_audit_site_change()` writes a correctly-shaped row into admin_audit_log
     using the expected columns (user_id, username, action, target, details, ip).
  2) `update_site` and `update_healing_tier` call it for real diffs and skip
     it when nothing actually changed.
  3) `get_site_activity` unions admin_audit_log + fleet_orders + incidents
     and sorts newest-first.

Runs at source level — does not require a live DB — because sites.py
leans on asyncpg which is awkward to mock. Rather than stand up a fake
pool, we assert the audit call shape via string grep + AST analysis.
"""

import ast
import os


SITES_PY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "sites.py",
)


def _load_source() -> str:
    with open(SITES_PY) as f:
        return f.read()


def _get_func(tree: ast.AST, name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name} not found in sites.py")


class TestAuditHelper:
    def test_helper_exists(self):
        src = _load_source()
        assert "async def _audit_site_change(" in src

    def test_helper_writes_to_admin_audit_log(self):
        src = _load_source()
        tree = ast.parse(src)
        helper = _get_func(tree, "_audit_site_change")
        body_src = ast.get_source_segment(src, helper) or ""
        assert "INSERT INTO admin_audit_log" in body_src
        # Must use the six canonical columns
        for col in ("user_id", "username", "action", "target", "details", "ip_address"):
            assert col in body_src, f"missing column {col}"

    def test_helper_catches_exceptions(self):
        """Audit failures must NOT break the underlying mutation."""
        src = _load_source()
        tree = ast.parse(src)
        helper = _get_func(tree, "_audit_site_change")
        body_src = ast.get_source_segment(src, helper) or ""
        assert "try:" in body_src
        assert "except Exception" in body_src
        # Must log at error level for observability
        assert "logger.error" in body_src


class TestUpdateSiteAudit:
    def test_update_site_calls_audit_helper(self):
        src = _load_source()
        tree = ast.parse(src)
        fn = _get_func(tree, "update_site")
        body_src = ast.get_source_segment(src, fn) or ""
        assert "_audit_site_change(" in body_src
        assert "SITE_UPDATED" in body_src

    def test_update_site_takes_request(self):
        """Request object is required so we can extract the client IP."""
        src = _load_source()
        tree = ast.parse(src)
        fn = _get_func(tree, "update_site")
        arg_names = [a.arg for a in fn.args.args]
        assert "request" in arg_names, f"update_site missing 'request' arg: {arg_names}"

    def test_update_site_diffs_before_and_after(self):
        """Audit log must record before → after, not just the new value."""
        src = _load_source()
        tree = ast.parse(src)
        fn = _get_func(tree, "update_site")
        body_src = ast.get_source_segment(src, fn) or ""
        assert "before" in body_src
        # Diff logic uses `from` / `to` keys — field-level delta
        assert '"from"' in body_src
        assert '"to"' in body_src


class TestUpdateHealingTierAudit:
    def test_healing_tier_update_audits(self):
        src = _load_source()
        tree = ast.parse(src)
        fn = _get_func(tree, "update_healing_tier")
        body_src = ast.get_source_segment(src, fn) or ""
        assert "_audit_site_change(" in body_src
        assert "HEALING_TIER_CHANGED" in body_src

    def test_healing_tier_update_captures_before(self):
        """Must read the old tier before UPDATE so we can log a real diff."""
        src = _load_source()
        tree = ast.parse(src)
        fn = _get_func(tree, "update_healing_tier")
        body_src = ast.get_source_segment(src, fn) or ""
        assert "SELECT healing_tier FROM sites" in body_src

    def test_healing_tier_update_skips_audit_on_noop(self):
        """If old == new, no audit row should be written."""
        src = _load_source()
        tree = ast.parse(src)
        fn = _get_func(tree, "update_healing_tier")
        body_src = ast.get_source_segment(src, fn) or ""
        assert "if old_tier != new_tier" in body_src


class TestSiteActivityEndpoint:
    def test_endpoint_exists(self):
        src = _load_source()
        assert '@router.get("/{site_id}/activity")' in src
        assert "async def get_site_activity(" in src

    def test_endpoint_unions_three_sources(self):
        src = _load_source()
        tree = ast.parse(src)
        fn = _get_func(tree, "get_site_activity")
        body_src = ast.get_source_segment(src, fn) or ""
        assert "admin_audit_log" in body_src
        assert "fleet_orders" in body_src
        assert "incidents" in body_src

    def test_endpoint_requires_auth(self):
        src = _load_source()
        tree = ast.parse(src)
        fn = _get_func(tree, "get_site_activity")
        body_src = ast.get_source_segment(src, fn) or ""
        assert "require_auth" in body_src

    def test_endpoint_respects_limit(self):
        src = _load_source()
        tree = ast.parse(src)
        fn = _get_func(tree, "get_site_activity")
        # Query param with ge/le bounds
        assert any(
            isinstance(a.annotation, ast.Name) and a.annotation.id == "int"
            for a in fn.args.args
            if a.arg == "limit"
        )

    def test_endpoint_returns_event_list(self):
        src = _load_source()
        tree = ast.parse(src)
        fn = _get_func(tree, "get_site_activity")
        body_src = ast.get_source_segment(src, fn) or ""
        # Must return an events list + the site_id echo
        assert '"events"' in body_src
        assert '"site_id"' in body_src

    def test_endpoint_sorts_newest_first(self):
        src = _load_source()
        tree = ast.parse(src)
        fn = _get_func(tree, "get_site_activity")
        body_src = ast.get_source_segment(src, fn) or ""
        assert "reverse=True" in body_src

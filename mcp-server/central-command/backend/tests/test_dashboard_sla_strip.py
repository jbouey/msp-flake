"""Tests for the /sla-strip dashboard endpoint.

Source-level checks — the endpoint is a read-only aggregation of three
DB queries, so the project's idiomatic "verify the handler has the right
shape and guards" style suffices.
"""

import ast
import os


ROUTES_PY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "routes.py",
)


def _load() -> str:
    with open(ROUTES_PY) as f:
        return f.read()


def _get_func(tree: ast.AST, name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


class TestSLAStripEndpoint:
    def test_endpoint_registered(self):
        src = _load()
        assert '@router.get("/sla-strip")' in src
        assert "async def get_dashboard_sla_strip(" in src

    def test_requires_auth(self):
        src = _load()
        tree = ast.parse(src)
        fn = _get_func(tree, "get_dashboard_sla_strip")
        body = ast.get_source_segment(src, fn) or ""
        assert "require_auth" in body

    def test_uses_admin_connection(self):
        src = _load()
        tree = ast.parse(src)
        fn = _get_func(tree, "get_dashboard_sla_strip")
        body = ast.get_source_segment(src, fn) or ""
        # Wave-4 ratchet (2026-05-08): migrated from admin_connection
        # to admin_transaction (Session 212 routing-pathology rule —
        # 4 admin reads in sequence MUST pin to one PgBouncer backend).
        assert "admin_transaction" in body or "admin_connection" in body

    def test_healing_rate_query_uses_execution_telemetry(self):
        src = _load()
        tree = ast.parse(src)
        fn = _get_func(tree, "get_dashboard_sla_strip")
        body = ast.get_source_segment(src, fn) or ""
        assert "execution_telemetry" in body
        assert "24 hours" in body

    def test_ots_query_reads_pending_proofs(self):
        src = _load()
        tree = ast.parse(src)
        fn = _get_func(tree, "get_dashboard_sla_strip")
        body = ast.get_source_segment(src, fn) or ""
        assert "ots_proofs" in body
        assert "'pending'" in body

    def test_fleet_query_uses_site_appliances(self):
        src = _load()
        tree = ast.parse(src)
        fn = _get_func(tree, "get_dashboard_sla_strip")
        body = ast.get_source_segment(src, fn) or ""
        assert "site_appliances" in body
        assert "last_checkin" in body
        assert "5 minutes" in body

    def test_response_has_all_targets(self):
        src = _load()
        tree = ast.parse(src)
        fn = _get_func(tree, "get_dashboard_sla_strip")
        body = ast.get_source_segment(src, fn) or ""
        # Targets come from the backend — frontend must not hard-code.
        assert "healing_target" in body
        assert "ots_target_minutes" in body
        assert "fleet_target" in body

    def test_each_query_wrapped_in_try_except(self):
        """Any sub-query failure must degrade gracefully to None, not 500."""
        src = _load()
        tree = ast.parse(src)
        fn = _get_func(tree, "get_dashboard_sla_strip")
        body = ast.get_source_segment(src, fn) or ""
        # Count `try:` blocks — should be at least 3 (one per sub-query)
        assert body.count("try:") >= 3
        assert body.count("except Exception") >= 3

    def test_empty_pending_ots_returns_zero_not_null(self):
        """When no proofs are pending, the endpoint must report 0 minutes
        (SLA trivially met) rather than None (SLA unknown). Different
        semantic: 0 = 'meeting', None = 'no data, show dash'."""
        src = _load()
        tree = ast.parse(src)
        fn = _get_func(tree, "get_dashboard_sla_strip")
        body = ast.get_source_segment(src, fn) or ""
        assert "ots_anchor_age_minutes = 0.0" in body

    def test_response_echoes_computed_at(self):
        src = _load()
        tree = ast.parse(src)
        fn = _get_func(tree, "get_dashboard_sla_strip")
        body = ast.get_source_segment(src, fn) or ""
        assert "computed_at" in body

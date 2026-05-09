"""Tests for the /kpi-trends dashboard sparkline endpoint.

Source-level checks — verifies endpoint shape, auth, bounds on the `days`
parameter, the 3 series keys, and per-query try/except safety.
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


class TestKPITrendsEndpoint:
    def test_endpoint_registered(self):
        src = _load()
        assert '@router.get("/kpi-trends")' in src
        assert "async def get_kpi_trends(" in src

    def test_requires_auth(self):
        src = _load()
        tree = ast.parse(src)
        fn = _get_func(tree, "get_kpi_trends")
        body = ast.get_source_segment(src, fn) or ""
        assert "require_auth" in body

    def test_default_days_is_14(self):
        src = _load()
        tree = ast.parse(src)
        fn = _get_func(tree, "get_kpi_trends")
        # Look for `days: int = 14` in the signature
        body = ast.get_source_segment(src, fn) or ""
        assert "days: int = 14" in body

    def test_clamps_days_to_reasonable_range(self):
        """Both a min (2) and max (90) bound so the sparkline query never
        runs away on a bad input."""
        src = _load()
        tree = ast.parse(src)
        fn = _get_func(tree, "get_kpi_trends")
        body = ast.get_source_segment(src, fn) or ""
        assert "days < 2" in body
        assert "days > 90" in body

    def test_returns_three_series(self):
        src = _load()
        tree = ast.parse(src)
        fn = _get_func(tree, "get_kpi_trends")
        body = ast.get_source_segment(src, fn) or ""
        assert '"incidents_24h"' in body
        assert '"l1_rate"' in body
        assert '"clients"' in body

    def test_each_query_wrapped_in_try_except(self):
        """Any sub-query failure degrades to a zero-filled series, not 500."""
        src = _load()
        tree = ast.parse(src)
        fn = _get_func(tree, "get_kpi_trends")
        body = ast.get_source_segment(src, fn) or ""
        assert body.count("try:") >= 3
        assert body.count("except Exception") >= 3

    def test_uses_admin_connection(self):
        src = _load()
        tree = ast.parse(src)
        fn = _get_func(tree, "get_kpi_trends")
        body = ast.get_source_segment(src, fn) or ""
        # Wave-4 ratchet (2026-05-08): migrated to admin_transaction
        # (Session 212 routing-pathology rule).
        assert "admin_transaction" in body or "admin_connection" in body

    def test_response_has_computed_at(self):
        src = _load()
        tree = ast.parse(src)
        fn = _get_func(tree, "get_kpi_trends")
        body = ast.get_source_segment(src, fn) or ""
        assert "computed_at" in body

    def test_l1_rate_from_execution_telemetry(self):
        src = _load()
        tree = ast.parse(src)
        fn = _get_func(tree, "get_kpi_trends")
        body = ast.get_source_segment(src, fn) or ""
        assert "execution_telemetry" in body

    def test_incidents_from_incidents_table(self):
        src = _load()
        tree = ast.parse(src)
        fn = _get_func(tree, "get_kpi_trends")
        body = ast.get_source_segment(src, fn) or ""
        assert "FROM incidents" in body

    def test_clients_from_client_orgs(self):
        src = _load()
        tree = ast.parse(src)
        fn = _get_func(tree, "get_kpi_trends")
        body = ast.get_source_segment(src, fn) or ""
        assert "client_orgs" in body

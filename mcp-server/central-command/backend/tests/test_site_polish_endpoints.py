"""Tests for Site Detail polish endpoints (Session 203 round-table).

Validates three new additions to the backend:

  1) ``GET /api/sites/{site_id}/sla`` — healing SLA indicator, with
     fallback from ``site_healing_sla`` to ``execution_telemetry`` when
     the rollup table is empty.
  2) ``GET /api/sites/{site_id}/search`` — cross-category in-site search
     over incidents / devices / credentials / workstations, bounded by
     ``limit`` and guarded against empty/too-short queries.
  3) ``POST /api/portal/sites/{site_id}/generate-token`` — response shape
     now surfaces the real token expiry (``expires_at`` / ``created_at``
     / ``expires_in_seconds``) instead of just the legacy
     ``expires: "never"`` string.

Runs at source level — same pattern as ``test_site_activity_audit.py``
and ``test_site_id_enforcement.py``. We assert call shape via AST +
string grep because sites.py leans on asyncpg + RLS, which is awkward to
mock without a live DB.
"""

import ast
import os


BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITES_PY = os.path.join(BACKEND_DIR, "sites.py")
PORTAL_PY = os.path.join(BACKEND_DIR, "portal.py")


def _load(path: str) -> str:
    with open(path) as f:
        return f.read()


def _get_func(tree: ast.AST, name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name} not found")


# =============================================================================
# Task 1: SLA endpoint
# =============================================================================


class TestSlaEndpoint:
    def test_route_registered(self):
        """SLA endpoint is wired into the sites router."""
        src = _load(SITES_PY)
        assert '@router.get("/{site_id}/sla")' in src
        assert "async def get_site_sla(" in src

    def test_requires_auth(self):
        """SLA endpoint uses require_auth for admin/operator access."""
        tree = ast.parse(_load(SITES_PY))
        fn = _get_func(tree, "get_site_sla")
        body_src = ast.get_source_segment(_load(SITES_PY), fn) or ""
        assert "require_auth" in body_src

    def test_uses_tenant_connection(self):
        """Queries scope to the site via tenant_connection for RLS."""
        tree = ast.parse(_load(SITES_PY))
        fn = _get_func(tree, "get_site_sla")
        body_src = ast.get_source_segment(_load(SITES_PY), fn) or ""
        assert "tenant_connection(pool, site_id=site_id)" in body_src

    def test_falls_back_to_admin_connection(self):
        """Admin connection is a fallback if tenant context fails."""
        tree = ast.parse(_load(SITES_PY))
        fn = _get_func(tree, "get_site_sla")
        body_src = ast.get_source_segment(_load(SITES_PY), fn) or ""
        assert "admin_connection(pool)" in body_src

    def test_reads_site_healing_sla_table(self):
        """Primary data source is the site_healing_sla rollup table."""
        tree = ast.parse(_load(SITES_PY))
        fn = _get_func(tree, "get_site_sla")
        body_src = ast.get_source_segment(_load(SITES_PY), fn) or ""
        assert "site_healing_sla" in body_src
        # Must select key columns
        for col in ("healing_rate", "sla_target", "sla_met", "period_start"):
            assert col in body_src, f"missing column {col}"

    def test_falls_back_to_execution_telemetry(self):
        """When site_healing_sla has no rows, compute from execution_telemetry."""
        tree = ast.parse(_load(SITES_PY))
        fn = _get_func(tree, "get_site_sla")
        body_src = ast.get_source_segment(_load(SITES_PY), fn) or ""
        assert "execution_telemetry" in body_src
        assert "status='success'" in body_src or 'status = \'success\'' in body_src
        assert "INTERVAL '24 hours'" in body_src

    def test_response_shape_keys(self):
        """Response dict includes all documented keys."""
        tree = ast.parse(_load(SITES_PY))
        fn = _get_func(tree, "get_site_sla")
        body_src = ast.get_source_segment(_load(SITES_PY), fn) or ""
        for key in (
            '"site_id"',
            '"sla_target"',
            '"current_rate"',
            '"sla_met"',
            '"periods_last_7d"',
            '"periods_met_last_7d"',
            '"met_pct_7d"',
            '"trend"',
            '"source"',
        ):
            assert key in body_src, f"missing response key {key}"

    def test_source_field_indicates_origin(self):
        """`source` must reflect which data source was used."""
        tree = ast.parse(_load(SITES_PY))
        fn = _get_func(tree, "get_site_sla")
        body_src = ast.get_source_segment(_load(SITES_PY), fn) or ""
        assert '"site_healing_sla"' in body_src
        assert '"execution_telemetry"' in body_src
        assert '"none"' in body_src  # nothing-found path

    def test_null_fallback_when_empty(self):
        """`current_rate: None` when no data found in either source."""
        tree = ast.parse(_load(SITES_PY))
        fn = _get_func(tree, "get_site_sla")
        body_src = ast.get_source_segment(_load(SITES_PY), fn) or ""
        # The nothing-found return uses explicit None for rate/sla_met
        assert '"current_rate": None' in body_src
        assert '"sla_met": None' in body_src


# =============================================================================
# Task 2: Search endpoint
# =============================================================================


class TestSearchEndpoint:
    def test_route_registered(self):
        src = _load(SITES_PY)
        assert '@router.get("/{site_id}/search")' in src
        assert "async def search_site(" in src

    def test_requires_auth(self):
        tree = ast.parse(_load(SITES_PY))
        fn = _get_func(tree, "search_site")
        body_src = ast.get_source_segment(_load(SITES_PY), fn) or ""
        assert "require_auth" in body_src

    def test_rejects_short_query(self):
        """Query shorter than 2 chars must 400 — prevents data dump."""
        tree = ast.parse(_load(SITES_PY))
        fn = _get_func(tree, "search_site")
        body_src = ast.get_source_segment(_load(SITES_PY), fn) or ""
        assert "len(term) < 2" in body_src
        assert "status_code=400" in body_src
        assert "at least 2 characters" in body_src

    def test_limit_bounded(self):
        """Limit is capped — default 25, max 100."""
        tree = ast.parse(_load(SITES_PY))
        fn = _get_func(tree, "search_site")
        # limit Query has ge=1, le=100 bounds
        body_src = ast.get_source_segment(_load(SITES_PY), fn) or ""
        assert "ge=1, le=100" in body_src or "ge=1,le=100" in body_src

    def test_searches_all_four_categories(self):
        """Incidents, devices, credentials, workstations must all be queried."""
        tree = ast.parse(_load(SITES_PY))
        fn = _get_func(tree, "search_site")
        body_src = ast.get_source_segment(_load(SITES_PY), fn) or ""
        assert "FROM incidents" in body_src
        assert "FROM discovered_devices" in body_src
        assert "FROM site_credentials" in body_src
        assert "FROM workstations" in body_src

    def test_uses_ilike_parameterized(self):
        """ILIKE patterns must be parameterized, not f-stringed into SQL."""
        tree = ast.parse(_load(SITES_PY))
        fn = _get_func(tree, "search_site")
        body_src = ast.get_source_segment(_load(SITES_PY), fn) or ""
        assert "ILIKE $2" in body_src
        # The pattern variable must exist — term-based f-string goes into the
        # BINDING, never into the query text itself.
        assert 'pattern = f"%{term}%"' in body_src
        # And we must never see ILIKE with a curly-braced interpolation
        assert "ILIKE '%{" not in body_src
        assert 'ILIKE "%{' not in body_src

    def test_echoes_site_id_and_query(self):
        """Response echoes site_id + query so the frontend can assert scope."""
        tree = ast.parse(_load(SITES_PY))
        fn = _get_func(tree, "search_site")
        body_src = ast.get_source_segment(_load(SITES_PY), fn) or ""
        assert '"site_id": site_id' in body_src
        assert '"query": term' in body_src
        assert '"total"' in body_src

    def test_uses_tenant_connection(self):
        tree = ast.parse(_load(SITES_PY))
        fn = _get_func(tree, "search_site")
        body_src = ast.get_source_segment(_load(SITES_PY), fn) or ""
        assert "tenant_connection(pool, site_id=site_id)" in body_src

    def test_skips_missing_tables_gracefully(self):
        """If workstations table is missing, search must not 500."""
        tree = ast.parse(_load(SITES_PY))
        fn = _get_func(tree, "search_site")
        body_src = ast.get_source_segment(_load(SITES_PY), fn) or ""
        # Per-category try/except so one missing table doesn't kill the whole request
        assert "except Exception" in body_src
        assert "workstations query" in body_src

    def test_credentials_exclude_encrypted_data(self):
        """Search response must never expose encrypted credential blobs.

        The credentials SELECT list must contain only id / credential_type /
        credential_name. We allow the string ``encrypted_data`` to appear in
        a comment (it's a security intent doc), but NOT in the SQL SELECT.
        """
        tree = ast.parse(_load(SITES_PY))
        fn = _get_func(tree, "search_site")
        body_src = ast.get_source_segment(_load(SITES_PY), fn) or ""
        # Find the site_credentials SELECT and slice just that query
        marker = "FROM site_credentials"
        assert marker in body_src
        select_start = body_src.rfind("SELECT", 0, body_src.index(marker))
        select_stmt = body_src[select_start:body_src.index(marker)]
        assert "encrypted_data" not in select_stmt, (
            "credentials SELECT must not include encrypted_data column"
        )
        # Sanity: the safe columns we DO select
        assert "credential_type" in select_stmt
        assert "credential_name" in select_stmt


# =============================================================================
# Task 3: Portal token expiry
# =============================================================================


class TestPortalTokenExpiry:
    def test_token_response_has_expires_at_field(self):
        """TokenResponse model exposes expires_at, expires_in_seconds, created_at."""
        src = _load(PORTAL_PY)
        assert "class TokenResponse(" in src
        # Canonical fields (new)
        assert "expires_at:" in src
        assert "expires_in_seconds:" in src
        assert "created_at:" in src
        assert "url:" in src
        # Legacy fields preserved for back-compat
        assert "portal_url:" in src
        assert "token:" in src

    def test_endpoint_returns_expires_at(self):
        """generate_portal_token populates expires_at in the response."""
        tree = ast.parse(_load(PORTAL_PY))
        fn = _get_func(tree, "generate_portal_token")
        body_src = ast.get_source_segment(_load(PORTAL_PY), fn) or ""
        assert "expires_at=" in body_src
        assert "expires_in_seconds=" in body_src
        assert "created_at=" in body_src

    def test_endpoint_honors_redis_ttl(self):
        """When session manager is Redis-backed, surface the real TTL."""
        tree = ast.parse(_load(PORTAL_PY))
        fn = _get_func(tree, "generate_portal_token")
        body_src = ast.get_source_segment(_load(PORTAL_PY), fn) or ""
        assert "PORTAL_TOKEN_TTL" in body_src
        # Must read `redis` attribute to detect backend type
        assert 'getattr(session_mgr, "redis"' in body_src or "session_mgr.redis" in body_src

    def test_endpoint_preserves_permanent_semantics(self):
        """In-memory fallback must return expires_at: None, not fabricate a TTL."""
        tree = ast.parse(_load(PORTAL_PY))
        fn = _get_func(tree, "generate_portal_token")
        body_src = ast.get_source_segment(_load(PORTAL_PY), fn) or ""
        # The None-expiry branch lives in generate_portal_token
        assert "expires_at_iso = None" in body_src
        assert "expires_in_seconds = None" in body_src

    def test_endpoint_requires_admin(self):
        """Admin-only endpoint — must use require_admin."""
        tree = ast.parse(_load(PORTAL_PY))
        fn = _get_func(tree, "generate_portal_token")
        body_src = ast.get_source_segment(_load(PORTAL_PY), fn) or ""
        assert "require_admin" in body_src


# =============================================================================
# Cross-cutting: sites.py parses cleanly
# =============================================================================


class TestSmokeCompile:
    def test_sites_py_parses(self):
        """Source file must be syntactically valid Python."""
        ast.parse(_load(SITES_PY))

    def test_portal_py_parses(self):
        ast.parse(_load(PORTAL_PY))

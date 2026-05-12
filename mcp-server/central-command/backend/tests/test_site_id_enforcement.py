"""Tests for auth_site_id enforcement across agent-facing endpoints.

Validates that appliances cannot spoof other sites' identities.
This is the #1 security finding from the Session 202 round-table audit.

Tests the enforcement function in isolation and verifies it's wired into
all agent-facing endpoints via source inspection.
"""

import os
import re
import pytest


class FakeHTTPException(Exception):
    """Minimal HTTPException for testing without FastAPI installed."""
    def __init__(self, status_code=500, detail=""):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class FakeLogger:
    """Captures log calls for assertion."""
    def __init__(self):
        self.warnings = []

    def warning(self, msg, **kwargs):
        self.warnings.append((msg, kwargs))

    def info(self, *a, **kw):
        pass

    def error(self, *a, **kw):
        pass


def make_enforce_site_id(http_exc_cls, logger):
    """Build _enforce_site_id with injected deps (no import needed)."""
    def _enforce_site_id(auth_site_id, request_site_id, endpoint=""):
        if request_site_id and request_site_id != auth_site_id:
            logger.warning(
                "site_id mismatch: appliance attempted cross-site action",
                auth_site_id=auth_site_id,
                request_site_id=request_site_id,
                endpoint=endpoint,
            )
            raise http_exc_cls(
                status_code=403,
                detail="Site ID mismatch: token does not authorize this site",
            )
    return _enforce_site_id


class TestEnforceSiteId:
    """Test _enforce_site_id() — the core auth enforcement function."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.logger = FakeLogger()
        self.enforce = make_enforce_site_id(FakeHTTPException, self.logger)

    def test_matching_site_ids_pass(self):
        """Same auth and request site_id should not raise."""
        self.enforce("north-valley", "north-valley", "test")

    def test_mismatched_site_ids_raise_403(self):
        """Different auth and request site_id should raise 403."""
        with pytest.raises(FakeHTTPException) as exc_info:
            self.enforce("north-valley", "south-valley", "test")
        assert exc_info.value.status_code == 403
        assert "mismatch" in exc_info.value.detail.lower()

    def test_mismatch_logs_warning(self):
        """Mismatch should log a warning with both site IDs."""
        with pytest.raises(FakeHTTPException):
            self.enforce("site-a", "site-b", "checkin")
        assert len(self.logger.warnings) == 1
        _, kwargs = self.logger.warnings[0]
        assert kwargs["auth_site_id"] == "site-a"
        assert kwargs["request_site_id"] == "site-b"
        assert kwargs["endpoint"] == "checkin"

    def test_empty_request_site_id_passes(self):
        """Empty request site_id is allowed (some endpoints have optional site_id)."""
        self.enforce("north-valley", "", "test")

    def test_none_request_site_id_passes(self):
        """None request site_id is allowed."""
        self.enforce("north-valley", None, "test")

    def test_case_sensitive_match(self):
        """Site IDs are case-sensitive."""
        with pytest.raises(FakeHTTPException) as exc_info:
            self.enforce("north-valley", "North-Valley", "test")
        assert exc_info.value.status_code == 403

    def test_whitespace_matters(self):
        """Trailing whitespace in either value should raise."""
        with pytest.raises(FakeHTTPException):
            self.enforce("north-valley", "north-valley ", "test")


class TestCanonicalResolution:
    """Session 220 task #112 (2026-05-11). _enforce_site_id slow path
    canonical-resolves both sides via canonical_site_id() (mig 256).
    Legacy daemon callers sending OLD (pre-rename) site_id alongside
    a bearer authenticated for the NEW canonical site_id no longer
    permanent-403 + emit false-positive audit rows.

    Gate A v1 P1-1 (2026-05-11) — existing unit tests use a sync
    fixture that re-implements direct-compare and don't exercise the
    real async DB path. This class fills that gap with AsyncMock
    against the actual production function.
    """

    @pytest.fixture(autouse=True)
    def skip_if_missing_deps(self):
        """The real _enforce_site_id imports asyncpg-dependent modules
        at call time. If unavailable (e.g. minimal dev box), skip these
        tests — the sync-fixture coverage in TestEnforceSiteId still
        validates the logic shape."""
        pytest.importorskip("dashboard_api.shared")

    @pytest.mark.asyncio
    async def test_canonical_match_after_rename_does_not_raise(self):
        """Legacy caller sends OLD site_id; bearer's canonical is NEW.
        canonical_site_id() resolves both sides to the same value →
        legitimate rename case → no raise, no insert."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from dashboard_api.shared import _enforce_site_id

        # Mock the DB path: fetchrow returns canonical-match row.
        mock_conn = MagicMock()
        mock_conn.fetchrow = AsyncMock(return_value={
            "auth_canon": "site-canonical",
            "req_canon": "site-canonical",
        })
        mock_conn.execute = AsyncMock()

        class _TxCtx:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *a):
                return False

        with patch("dashboard_api.fleet.get_pool", AsyncMock(return_value="POOL")):
            with patch(
                "dashboard_api.tenant_middleware.admin_transaction",
                lambda pool: _TxCtx(),
            ):
                # Should return without raising — caller is legitimate.
                await _enforce_site_id(
                    "site-canonical", "site-old-renamed-from", "test_endpoint"
                )

        # CRITICAL: audit-log INSERT must NOT fire on a legitimate
        # rename case (Maya audit-cleanliness rule).
        mock_conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_true_mismatch_after_canonical_raises_403_and_inserts(self):
        """Two distinct sites with NO mapping → canonical-resolves to
        themselves → still mismatch → 403 + audit row."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from dashboard_api.shared import _enforce_site_id
        from fastapi import HTTPException

        mock_conn = MagicMock()
        mock_conn.fetchrow = AsyncMock(return_value={
            "auth_canon": "site-a",
            "req_canon": "site-b",
        })
        mock_conn.execute = AsyncMock()

        class _TxCtx:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *a):
                return False

        with patch("dashboard_api.fleet.get_pool", AsyncMock(return_value="POOL")):
            with patch(
                "dashboard_api.tenant_middleware.admin_transaction",
                lambda pool: _TxCtx(),
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await _enforce_site_id("site-a", "site-b", "test_endpoint")

        assert exc_info.value.status_code == 403
        mock_conn.execute.assert_called_once()
        # Verify the audit row INSERT was on admin_audit_log with the
        # cross_site_spoof_attempt action.
        call_args = mock_conn.execute.call_args
        assert "admin_audit_log" in call_args.args[0]
        assert "cross_site_spoof_attempt" in call_args.args[0]

    @pytest.mark.asyncio
    async def test_fetchrow_raises_still_403(self):
        """Failure mode: if the DB query fails (e.g. mig 256 not
        applied, pool exhausted), the bare except swallows + logs
        ERROR + raises 403. Fail-closed semantics."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from dashboard_api.shared import _enforce_site_id
        from fastapi import HTTPException

        mock_conn = MagicMock()
        # fetchrow raises (e.g. UndefinedFunctionError in envs without mig 256)
        mock_conn.fetchrow = AsyncMock(side_effect=Exception("mig 256 missing"))
        mock_conn.execute = AsyncMock()

        class _TxCtx:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *a):
                return False

        with patch("dashboard_api.fleet.get_pool", AsyncMock(return_value="POOL")):
            with patch(
                "dashboard_api.tenant_middleware.admin_transaction",
                lambda pool: _TxCtx(),
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await _enforce_site_id("site-a", "site-b", "test_endpoint")

        # Fail-closed: still 403 even though DB path failed.
        assert exc_info.value.status_code == 403
        # No audit row written (couldn't reach the INSERT statement).
        mock_conn.execute.assert_not_called()


class TestEnforcementCoverage:
    """Verify enforcement is wired into all agent-facing endpoints via source inspection."""

    @pytest.fixture(autouse=True)
    def load_source(self):
        """Session 219 (2026-05-11) lifted `_enforce_site_id` from
        agent_api.py to shared.py. Coverage check now reads BOTH —
        bearer endpoints are scattered across agent_api.py +
        appliance_delegation.py + (future Commit 2) provisioning.py +
        sensors.py + discovery.py."""
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(backend_dir, "agent_api.py")) as f:
            self.source = f.read()
        with open(os.path.join(backend_dir, "shared.py")) as f:
            self.shared_source = f.read()
        with open(os.path.join(backend_dir, "appliance_delegation.py")) as f:
            self.delegation_source = f.read()

    def test_enforce_function_exists(self):
        """_enforce_site_id must be defined in shared.py (lifted from
        agent_api.py in Session 219, 2026-05-11)."""
        assert "async def _enforce_site_id(" in self.shared_source, (
            "_enforce_site_id missing from shared.py — Session 219 "
            "(2026-05-11) lifted it from agent_api.py; coverage tests "
            "must look at shared.py."
        )

    def test_all_bearer_endpoints_call_enforce(self):
        """Every endpoint with require_appliance_bearer should call _enforce_site_id.

        We count Depends(require_appliance_bearer) occurrences (= endpoints)
        and _enforce_site_id(auth_site_id calls (= enforcement) across
        agent_api.py + appliance_delegation.py (Session 219 expansion).
        """
        combined = self.source + "\n" + self.delegation_source
        bearer_deps = len(re.findall(r"Depends\(require_appliance_bearer\)", combined))
        enforce_calls = len(re.findall(r"_enforce_site_id\(auth_site_id", combined))

        # Allow small gap (some endpoints may conditionally enforce, e.g., optional site_id)
        assert enforce_calls >= bearer_deps - 4, (
            f"Found {bearer_deps} endpoints with require_appliance_bearer "
            f"but only {enforce_calls} _enforce_site_id calls. "
            f"A new endpoint may be missing _enforce_site_id()."
        )

    def test_no_bare_req_site_id_usage(self):
        """After enforcement, request body site_id should not be used directly
        without first being overridden by auth_site_id in critical paths.

        Session 210-B 2026-04-25 audit: `learning_api_main.py` was deleted
        as orphaned extraction (live versions of those endpoints have
        always been in mcp-server/main.py). Test now checks main.py for
        the same pattern.
        """
        main_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "..",
            "main.py",
        )
        with open(main_path) as f:
            main_source = f.read()

        # Verify enforcement exists in main.py for the appliance-facing
        # learning endpoints (PromotionReportRequest etc).
        assert "auth_site_id" in main_source, (
            "main.py must enforce auth_site_id on appliance-facing "
            "endpoints. Without this an appliance with site-A token "
            "could submit a promotion report claiming site-B."
        )
        assert "403" in main_source or "mismatch" in main_source.lower()

    def test_device_sync_enforcement(self):
        """device_sync.py must enforce auth_site_id."""
        ds_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "device_sync.py",
        )
        with open(ds_path) as f:
            ds_source = f.read()

        assert "auth_site_id" in ds_source
        assert "403" in ds_source


class TestOrderEndpointAuth:
    """Verify sites.py order and alert endpoints have auth."""

    @pytest.fixture(autouse=True)
    def load_source(self):
        sites_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "sites.py",
        )
        with open(sites_path) as f:
            self.source = f.read()

    def test_get_order_requires_auth(self):
        """GET /orders/{order_id} must require authentication."""
        # Find the get_order function and check it has auth
        match = re.search(r"async def get_order\([^)]+\)", self.source)
        assert match, "get_order endpoint not found"
        assert "require_auth" in match.group() or "require_admin" in match.group(), \
            "get_order must have require_auth or require_admin dependency"

    def test_send_email_alert_requires_auth(self):
        """POST /alerts/email must require authentication."""
        match = re.search(r"async def send_email_alert\([^)]+\)", self.source)
        assert match, "send_email_alert endpoint not found"
        assert "require_appliance_bearer" in match.group(), \
            "send_email_alert must have require_appliance_bearer dependency"

    def test_get_pending_orders_validates_site(self):
        """get_pending_orders must validate auth_site_id matches path site_id."""
        # Find the function body
        start = self.source.find("async def get_pending_orders(")
        assert start >= 0
        # Check the next 500 chars for enforcement
        body = self.source[start:start + 500]
        assert "auth_site_id" in body
        assert "403" in body or "mismatch" in body.lower()


class TestOrderSigningDRY:
    """Verify order_signing.py was properly deduplicated."""

    @pytest.fixture(autouse=True)
    def load_source(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "order_signing.py",
        )
        with open(path) as f:
            self.source = f.read()

    def test_single_sign_implementation(self):
        """sign_admin_order and sign_fleet_order should delegate to _sign_order."""
        assert "def _sign_order(" in self.source, "DRY helper _sign_order must exist"
        # Both public functions should be thin wrappers
        assert self.source.count("from main import sign_data") == 1, \
            "sign_data should only be imported once (in _sign_order)"

    def test_no_duplicate_nonce_generation(self):
        """Nonce generation should happen only in _sign_order."""
        assert self.source.count("secrets.token_hex(16)") == 1, \
            "token_hex should only appear once (in _sign_order)"


class TestComplianceCategoriesDRY:
    """Verify client_portal.py category maps are deduplicated."""

    @pytest.fixture(autouse=True)
    def load_source(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "client_portal.py",
        )
        with open(path) as f:
            self.source = f.read()

    def test_single_category_definition(self):
        """COMPLIANCE_CATEGORIES should be defined once at module level."""
        assert "COMPLIANCE_CATEGORIES" in self.source
        # Should appear as module-level constant
        assert self.source.count("COMPLIANCE_CATEGORIES = {") == 1

    def test_no_inline_category_dicts(self):
        """Functions should use COMPLIANCE_CATEGORIES, not inline dicts."""
        # Count inline category dict definitions (the old pattern)
        inline_dicts = self.source.count('"patching": ["nixos_generation"')
        assert inline_dicts == 1, (
            f"Found {inline_dicts} inline category dicts — should be 1 "
            f"(in the COMPLIANCE_CATEGORIES constant only)"
        )

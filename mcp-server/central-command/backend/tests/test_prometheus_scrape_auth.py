"""Session 209 /metrics bearer-auth regression.

Pre-fix, /metrics required `require_auth` — a user cookie or user
Bearer. Prometheus scrapers have neither. Shipping the Prom-on-Vault
standup required a static env-token path. These tests lock:

  1. `require_scrape_or_admin` exists and is wired on the route.
  2. A matching `PROMETHEUS_SCRAPE_TOKEN` + Bearer passes without
     touching the DB (short-circuit before require_auth).
  3. A mismatching Bearer falls through to `require_auth`.
  4. When the env var is unset, the dependency degrades to admin-
     only auth (preserves pre-Session-209 behavior).
  5. Compare is constant-time (uses hmac.compare_digest).
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from unittest.mock import patch, AsyncMock

import pytest

os.environ.setdefault("SESSION_TOKEN_SECRET", "test-secret")
os.environ.setdefault("ENVIRONMENT", "development")

_backend = pathlib.Path(__file__).resolve().parent.parent
_mcp_server = _backend.parent.parent
for _p in (str(_backend), str(_mcp_server)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load_prom():
    try:
        from dashboard_api import prometheus_metrics as _pm
    except Exception:
        import prometheus_metrics as _pm  # type: ignore
    return _pm


BACKEND = _backend
PROM = BACKEND / "prometheus_metrics.py"


def _source() -> str:
    return PROM.read_text(encoding="utf-8")


# -----------------------------------------------------------------------------
# Source-level guardrails
# -----------------------------------------------------------------------------


def test_dependency_function_exists():
    assert "async def require_scrape_or_admin" in _source()


def test_route_uses_new_dependency():
    src = _source()
    idx = src.find('@router.get("/metrics"')
    assert idx != -1, "Cannot find /metrics route decorator"
    block = src[idx : idx + 400]
    assert "Depends(require_scrape_or_admin)" in block, (
        "/metrics route must depend on require_scrape_or_admin. "
        "Reverting to Depends(require_auth) breaks Prometheus scrapers."
    )


def test_constant_time_compare_used():
    assert "hmac.compare_digest" in _source(), (
        "Bearer compare must use hmac.compare_digest — plain `==` "
        "leaks a timing oracle on the scrape token."
    )


def test_env_var_name_is_prometheus_scrape_token():
    assert 'os.getenv("PROMETHEUS_SCRAPE_TOKEN"' in _source(), (
        "Env var name is part of the operator contract — renaming "
        "breaks the Vault-host prometheus.yml without warning."
    )


# -----------------------------------------------------------------------------
# Runtime behavior
# -----------------------------------------------------------------------------


def _mock_request(auth_header: str | None = None):
    class _R:
        headers = {"authorization": auth_header} if auth_header else {}
    return _R()


def test_matching_bearer_short_circuits_db():
    pm = _load_prom()
    original = pm.require_auth
    try:
        pm.require_auth = AsyncMock(
            side_effect=AssertionError("must not be called")
        )
        with patch.dict(os.environ, {"PROMETHEUS_SCRAPE_TOKEN": "s3cret-token"}):
            result = asyncio.run(
                pm.require_scrape_or_admin(_mock_request("Bearer s3cret-token"))
            )
    finally:
        pm.require_auth = original
    assert result == {"username": "prometheus_scraper", "role": "scraper"}


def test_mismatching_bearer_falls_through_to_admin_auth():
    pm = _load_prom()
    fake_admin = {"username": "jeff", "role": "admin"}
    stub = AsyncMock(return_value=fake_admin)
    original = pm.require_auth
    try:
        pm.require_auth = stub
        with patch.dict(os.environ, {"PROMETHEUS_SCRAPE_TOKEN": "s3cret-token"}):
            result = asyncio.run(
                pm.require_scrape_or_admin(_mock_request("Bearer wrong"))
            )
    finally:
        pm.require_auth = original
    stub.assert_called_once()
    assert result == fake_admin


def test_unset_env_var_falls_through_to_admin_auth():
    """When PROMETHEUS_SCRAPE_TOKEN is unset, /metrics degrades to
    admin-only auth — pre-Session-209 behavior preserved.
    """
    pm = _load_prom()
    fake_admin = {"username": "jeff", "role": "admin"}
    stub = AsyncMock(return_value=fake_admin)
    original = pm.require_auth
    env = {k: v for k, v in os.environ.items() if k != "PROMETHEUS_SCRAPE_TOKEN"}
    try:
        pm.require_auth = stub
        with patch.dict(os.environ, env, clear=True):
            result = asyncio.run(
                pm.require_scrape_or_admin(_mock_request("Bearer anything"))
            )
    finally:
        pm.require_auth = original
    stub.assert_called_once()
    assert result == fake_admin


def test_empty_env_var_falls_through():
    """Empty-string env var is equivalent to unset — guard against
    the 'token field left blank in .env' foot-gun.
    """
    pm = _load_prom()
    fake_admin = {"username": "jeff", "role": "admin"}
    stub = AsyncMock(return_value=fake_admin)
    original = pm.require_auth
    try:
        pm.require_auth = stub
        with patch.dict(os.environ, {"PROMETHEUS_SCRAPE_TOKEN": "   "}):
            result = asyncio.run(
                pm.require_scrape_or_admin(_mock_request("Bearer   "))
            )
    finally:
        pm.require_auth = original
    assert result == fake_admin


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

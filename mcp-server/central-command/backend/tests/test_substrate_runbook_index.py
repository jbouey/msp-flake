"""Tests for GET /api/admin/substrate/runbooks (Task 16).

Asserts:
  (a) Every invariant in ALL_ASSERTIONS appears in the index.
  (b) has_action mirrors INVARIANT_ACTION_WHITELIST — the 4 operator-whitelisted
      invariants (install_loop, install_session_ttl, auth_failure_lockout,
      agent_version_lag) report has_action=true; everything else false.
  (c) Severity is the bare 'sev1'/'sev2'/'sev3' string pulled from
      Assertion.severity — NEVER double-prefixed 'sevsev1'.

Non-DB: no TEST_DATABASE_URL required.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("SESSION_TOKEN_SECRET", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

_ADMIN_USER = {
    "id": "00000000-0000-0000-0000-aaaaaaaaaaaa",
    "email": "admin@osiriscare.net",
    "username": "admin",
    "role": "admin",
}


def _build_app() -> FastAPI:
    from auth import require_auth
    from substrate_action_api import router as substrate_router

    app = FastAPI()
    app.include_router(substrate_router)

    async def _mock_auth():
        return _ADMIN_USER

    app.dependency_overrides[require_auth] = _mock_auth
    return app


async def _get(path: str):
    app = _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.get(path)


@pytest.mark.asyncio
async def test_index_returns_all_invariants():
    from assertions import ALL_ASSERTIONS

    r = await _get("/api/admin/substrate/runbooks")
    assert r.status_code == 200, r.text
    body = r.json()
    items = body["items"]
    names = {i["invariant"] for i in items}
    assert names == {a.name for a in ALL_ASSERTIONS}


@pytest.mark.asyncio
async def test_index_severity_is_bare_sev_string():
    r = await _get("/api/admin/substrate/runbooks")
    body = r.json()
    for item in body["items"]:
        assert item["severity"] in {"sev1", "sev2", "sev3"}, (
            f"{item['invariant']} severity={item['severity']!r} — expected bare sev string"
        )


@pytest.mark.asyncio
async def test_index_has_action_flag_matches_whitelist():
    r = await _get("/api/admin/substrate/runbooks")
    items = r.json()["items"]
    with_action = {i["invariant"] for i in items if i["has_action"]}
    assert with_action == {
        "install_loop",
        "install_session_ttl",
        "auth_failure_lockout",
        "agent_version_lag",
    }
    for item in items:
        if item["has_action"]:
            assert item["action_key"] in {
                "cleanup_install_session",
                "unlock_platform_account",
                "reconcile_fleet_order",
            }
        else:
            assert item["action_key"] is None

"""Feature-flag integration tests for the substrate API surface (Task 18).

Asserts:
  (a) POST /api/admin/substrate/action returns 503 when
      SUBSTRATE_ACTIONS_ENABLED != 'true', and the 503 body names the
      flag so operators know what to flip.
  (b) GET /api/admin/substrate/runbook/{invariant} is ALWAYS on,
      regardless of the action-endpoint flag — read-only runbook prose
      is a help-yourself surface that must not go dark during rollout.
  (c) GET /api/admin/substrate/runbooks (index) is ALSO always on.
  (d) Flipping the flag to 'true' mid-process allows a subsequent POST
      to reach the handler dispatch path (400 for unknown action_key,
      NOT 503 — proves the flag read is request-time, not module-load).

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


async def _post(path: str, payload: dict):
    app = _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.post(path, json=payload)


async def _get(path: str):
    app = _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.get(path)


@pytest.mark.asyncio
async def test_action_endpoint_returns_503_when_flag_off(monkeypatch):
    monkeypatch.setenv("SUBSTRATE_ACTIONS_ENABLED", "false")
    r = await _post(
        "/api/admin/substrate/action",
        {
            "action_key": "cleanup_install_session",
            "target_ref": {"mac": "aa:bb:cc:dd:ee:ff"},
            "reason": "",
        },
    )
    assert r.status_code == 503, r.text
    body = r.json()
    assert "SUBSTRATE_ACTIONS_ENABLED" in str(body.get("detail", ""))


@pytest.mark.asyncio
async def test_runbook_viewer_always_on_regardless_of_flag(monkeypatch):
    monkeypatch.setenv("SUBSTRATE_ACTIONS_ENABLED", "false")
    r = await _get("/api/admin/substrate/runbook/install_loop")
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_runbook_index_always_on_regardless_of_flag(monkeypatch):
    monkeypatch.setenv("SUBSTRATE_ACTIONS_ENABLED", "false")
    r = await _get("/api/admin/substrate/runbooks")
    assert r.status_code == 200, r.text
    assert "items" in r.json()


@pytest.mark.asyncio
async def test_flag_read_is_request_time_not_module_load(monkeypatch):
    """Flipping the flag to true must let a new request pass the gate
    without reloading the module. Proves the env read is lazy.
    """
    monkeypatch.setenv("SUBSTRATE_ACTIONS_ENABLED", "true")
    r = await _post(
        "/api/admin/substrate/action",
        {
            "action_key": "definitely_not_a_real_action",
            "target_ref": {},
            "reason": "",
        },
    )
    # Past the flag gate → unknown action → 400 (NOT 503).
    assert r.status_code == 400, r.text

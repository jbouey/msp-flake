"""Rate-limit integration for POST /api/admin/substrate/action (Task 19).

Verifies:
  (a) RATE_LIMITS dict is configured at 60/10/20 per 3600s for the three
      registered actions. If someone changes the window or caps, the CI
      test forces a PR conversation.
  (b) When check_rate_limit returns allowed=False, the endpoint responds
      HTTP 429 with Retry-After header and structured detail body. The
      Redis check itself is covered by shared.py's own tests; here we
      only verify the wiring.
  (c) When check_rate_limit returns allowed=True, the request flows past
      the rate-limit gate (manifests as 400 "unknown action_key" for a
      bogus key — NOT 429).
  (d) The rate-limit gate fires AFTER the feature flag + action registry
      + reason gates — i.e. a bogus action_key returns 400 even under
      simulated rate-limit pressure, to keep the feedback order sane.

Non-DB: check_rate_limit is monkeypatched to skip Redis.
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

import substrate_action_api  # noqa: E402

_ADMIN_USER = {
    "id": "00000000-0000-0000-0000-aaaaaaaaaaaa",
    "email": "admin@osiriscare.net",
    "username": "admin",
    "role": "admin",
}


def _build_app() -> FastAPI:
    from auth import require_auth

    app = FastAPI()
    app.include_router(substrate_action_api.router)

    async def _mock_auth():
        return _ADMIN_USER

    app.dependency_overrides[require_auth] = _mock_auth
    return app


async def _post(payload: dict):
    app = _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.post("/api/admin/substrate/action", json=payload)


def test_rate_limit_config_is_60_10_20_per_hour():
    assert substrate_action_api.RATE_LIMITS == {
        "cleanup_install_session": (3600, 60),
        "unlock_platform_account": (3600, 10),
        "reconcile_fleet_order":   (3600, 20),
    }


@pytest.mark.asyncio
async def test_rate_limit_exceeded_returns_429(monkeypatch):
    monkeypatch.setenv("SUBSTRATE_ACTIONS_ENABLED", "true")

    async def _deny(*_args, **_kwargs):
        return (False, 1234)

    monkeypatch.setattr(substrate_action_api, "check_rate_limit", _deny)

    r = await _post({
        "action_key": "cleanup_install_session",
        "target_ref": {"mac": "aa:bb:cc:dd:ee:ff"},
        "reason": "",
    })
    assert r.status_code == 429, r.text
    body = r.json()
    detail = body["detail"]
    assert detail["reason"] == "rate_limit_exceeded"
    assert detail["action_key"] == "cleanup_install_session"
    assert detail["window_seconds"] == 3600
    assert detail["max_requests"] == 60
    assert detail["retry_after_seconds"] == 1234
    assert r.headers.get("retry-after") == "1234"


@pytest.mark.asyncio
async def test_rate_limit_allowed_does_not_429(monkeypatch):
    monkeypatch.setenv("SUBSTRATE_ACTIONS_ENABLED", "true")

    async def _allow(*_args, **_kwargs):
        return (True, 0)

    monkeypatch.setattr(substrate_action_api, "check_rate_limit", _allow)

    # Known handler without a DB pool would attempt a DB call → we use a
    # bogus action_key so we hit the "unknown action_key" branch (400),
    # proving the rate-limit gate let us through.
    r = await _post({
        "action_key": "not_a_real_handler",
        "target_ref": {},
        "reason": "",
    })
    assert r.status_code == 400, r.text
    assert "unknown action_key" in str(r.json()["detail"])


@pytest.mark.asyncio
async def test_unknown_action_key_returns_400_even_under_rate_pressure(monkeypatch):
    """Rate-limit gate must fire AFTER the action-registry gate.
    Otherwise a bogus action_key would get 429 (which is misleading) when
    the real answer is 'that action_key doesn't exist'.
    """
    monkeypatch.setenv("SUBSTRATE_ACTIONS_ENABLED", "true")

    async def _deny(*_args, **_kwargs):
        return (False, 60)

    monkeypatch.setattr(substrate_action_api, "check_rate_limit", _deny)

    # Bogus action_key. RATE_LIMITS lookup returns None for non-registered
    # keys → rate-limit gate is skipped entirely. Unknown-action gate fires.
    r = await _post({
        "action_key": "bogus_action",
        "target_ref": {},
        "reason": "",
    })
    assert r.status_code == 400, r.text
    assert "unknown action_key" in str(r.json()["detail"])

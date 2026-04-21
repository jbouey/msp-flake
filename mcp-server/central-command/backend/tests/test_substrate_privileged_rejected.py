"""Non-operator posture guardrail: privileged order types MUST NOT be
reachable via POST /api/admin/substrate/action.

Privileged types (signing_key_rotation, bulk_remediation, emergency_access,
watchdog_*, break_glass_*, enable_recovery_shell_24h, etc.) carry the
chain-of-custody attestation bundle defined in the CLAUDE.md "Privileged-
Access Chain of Custody" rule, and ONLY fleet_cli knows how to mint that
bundle. If any privileged type becomes an action_key in SUBSTRATE_ACTIONS,
the substrate panel becomes a chain-bypass vector.

This test asserts the endpoint returns 400 (unknown action_key) for every
member of fleet_cli.PRIVILEGED_ORDER_TYPES — which is the exact shape that
the registry dispatch emits when a key isn't registered. A future
regression that aliases a privileged type into the registry would switch
this to a 2xx and break the test.

Non-DB: the endpoint rejects the unknown key before any handler runs, so
this suite is safe to run without TEST_DATABASE_URL.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Add backend directory to sys.path so backend modules are importable.
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Set required env vars BEFORE any backend import walks through os.environ.
os.environ.setdefault("SESSION_TOKEN_SECRET", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from fleet_cli import PRIVILEGED_ORDER_TYPES  # noqa: E402
from substrate_actions import SUBSTRATE_ACTIONS  # noqa: E402

_ADMIN_USER = {
    "id": "00000000-0000-0000-0000-aaaaaaaaaaaa",
    "email": "admin@osiriscare.net",
    "username": "admin",
    "role": "admin",
}


def _build_app() -> FastAPI:
    """Build a minimal FastAPI app with substrate router + mocked auth."""
    from auth import require_auth
    from substrate_action_api import router as substrate_router

    app = FastAPI()
    app.include_router(substrate_router)

    async def _mock_auth():
        return _ADMIN_USER

    app.dependency_overrides[require_auth] = _mock_auth
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("privileged_type", sorted(PRIVILEGED_ORDER_TYPES))
async def test_privileged_action_key_rejected(privileged_type, monkeypatch):
    """A privileged order_type used as action_key MUST be rejected with 400.

    The endpoint receives a body whose action_key is a privileged fleet
    order type. Because SUBSTRATE_ACTIONS is the only allowlist the
    endpoint consults, an unregistered privileged type returns 400 with
    "unknown action_key". Any status other than 400 indicates the
    registry has been extended to cover a privileged type — a
    chain-of-custody bypass.
    """
    # Pre-flight sanity: the registry must not already contain a privileged key.
    assert privileged_type not in SUBSTRATE_ACTIONS, (
        f"regression: privileged type {privileged_type!r} is registered in "
        "SUBSTRATE_ACTIONS — this is a chain-of-custody bypass. Privileged "
        "orders carry attestation bundles and MUST be dispatched via fleet_cli."
    )

    monkeypatch.setenv("SUBSTRATE_ACTIONS_ENABLED", "true")

    app = _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(
            "/api/admin/substrate/action",
            json={
                "action_key": privileged_type,
                "target_ref": {},
                "reason": "x" * 25,
            },
        )

    assert r.status_code == 400, (
        f"privileged type {privileged_type!r} was accepted by substrate "
        f"endpoint (got HTTP {r.status_code}) — MUST stay in fleet_cli per "
        "chain-of-custody. Response body: {r.text}"
    )
    body = r.json()
    assert "valid_keys" in body["detail"], (
        f"expected 'valid_keys' in 400 response detail, got: {body}"
    )

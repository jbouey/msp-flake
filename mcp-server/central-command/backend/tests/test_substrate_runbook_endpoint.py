"""Tests for GET /api/admin/substrate/runbook/{invariant} (Task 10).

Verifies:
  (a) known invariant returns 200 with JSON shape {invariant, display_name,
      severity, markdown} — markdown contains required section headings.
  (b) unknown invariant returns 404.
  (c) path-traversal style invariant names return 400 or 404 (never leak
      files outside backend/substrate_runbooks/).
  (d) missing auth dependency override → 401/403.

Non-DB: the endpoint reads backend/substrate_runbooks/<name>.md from disk
and returns the contents. Safe to run without TEST_DATABASE_URL.
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

_ADMIN_USER = {
    "id": "00000000-0000-0000-0000-aaaaaaaaaaaa",
    "email": "admin@osiriscare.net",
    "username": "admin",
    "role": "admin",
}


def _build_app(with_auth_override: bool = True) -> FastAPI:
    from auth import require_auth
    from substrate_action_api import router as substrate_router

    app = FastAPI()
    app.include_router(substrate_router)

    if with_auth_override:
        async def _mock_auth():
            return _ADMIN_USER

        app.dependency_overrides[require_auth] = _mock_auth

    return app


async def _get(path: str, app: FastAPI | None = None):
    app = app or _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.get(path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runbook_returns_markdown_for_known_invariant():
    """Known invariant returns 200 with markdown body + metadata."""
    r = await _get("/api/admin/substrate/runbook/install_loop")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["invariant"] == "install_loop"
    assert body["severity"] == "sev1"  # install_loop is sev1 in ALL_ASSERTIONS
    assert "display_name" in body
    assert "## What this means" in body["markdown"]
    assert "## Change log" in body["markdown"]


@pytest.mark.asyncio
async def test_runbook_404_for_unknown_invariant():
    r = await _get("/api/admin/substrate/runbook/does_not_exist")
    assert r.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_name",
    [
        "..%2F..%2Fetc%2Fpasswd",
        "..%2F_TEMPLATE",
        "install_loop.md",       # extension leaked
        "Install_Loop",          # uppercase
        "install loop",          # space
        "install/loop",          # path sep
    ],
)
async def test_runbook_rejects_invalid_names(bad_name):
    """Unsafe names MUST NOT resolve to files outside backend/substrate_runbooks/.

    The regex ^[a-z0-9_]+$ is the only character-level guard; anything
    that reaches the filesystem must already be a pure snake_case name.
    """
    r = await _get(f"/api/admin/substrate/runbook/{bad_name}")
    assert r.status_code in (400, 404), (
        f"bad name {bad_name!r} returned HTTP {r.status_code}: {r.text}"
    )


@pytest.mark.asyncio
async def test_runbook_requires_auth():
    """Without auth override, require_auth raises 401/403."""
    app = _build_app(with_auth_override=False)
    r = await _get("/api/admin/substrate/runbook/install_loop", app=app)
    assert r.status_code in (401, 403)

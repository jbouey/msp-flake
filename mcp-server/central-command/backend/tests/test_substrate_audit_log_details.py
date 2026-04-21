"""Audit-log content assertion test (Task 20).

Complements `test_endpoint_writes_one_audit_row` in
test_substrate_action_endpoint.py (which only asserts COUNT += 1) by pinning
the CONTENT shape of the row a successful substrate action writes.

Contract:
  - `action` column == "substrate.<action_key>"
  - `target` column == "substrate_action:<action_key>"
  - `username` column == actor email derived from the admin user
  - `details` JSONB contains "reason", "target_ref" (with the submitted
    fields), and "result" (the handler's return payload). These are what
    an auditor will pull 3 years from now to reconstruct what happened.

If the shape drifts, every prior audit row becomes unparseable — so this
test is the contract, not the implementation.

DB-gated: requires TEST_DATABASE_URL + the seed_locked_partner fixture
from tests/conftest.py.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("SESSION_TOKEN_SECRET", "test-secret")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test",
)

from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

_TEST_DB_URL = os.getenv("TEST_DATABASE_URL")

_requires_db = pytest.mark.skipif(
    not _TEST_DB_URL,
    reason="audit-log content test requires TEST_DATABASE_URL",
)

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


async def _post(body):
    app = _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.post("/api/admin/substrate/action", json=body)


@_requires_db
@pytest.mark.asyncio
async def test_audit_row_captures_reason_target_and_result(
    pool, seed_locked_partner, monkeypatch,
):
    """Successful unlock_platform_account writes an audit row with the full
    operator-supplied reason, the original target_ref, and the handler's
    result payload — all three are required to reconstruct the action
    3 years from now during an audit."""
    from tenant_middleware import admin_connection

    monkeypatch.setenv("SUBSTRATE_ACTIONS_ENABLED", "true")
    email = seed_locked_partner["email"]
    reason = "[JR] Confirmed via phone callback with partner — locked out"

    r = await _post({
        "action_key": "unlock_platform_account",
        "target_ref": {"table": "partners", "email": email},
        "reason": reason,
    })
    assert r.status_code == 200, r.text

    async with admin_connection(pool) as conn:
        row = await conn.fetchrow(
            "SELECT action, target, username, details "
            "FROM admin_audit_log "
            "WHERE action = 'substrate.unlock_platform_account' "
            "  AND username = $1 "
            "ORDER BY id DESC LIMIT 1",
            _ADMIN_USER["email"],
        )

    assert row is not None, "endpoint did not write an audit row"
    assert row["action"] == "substrate.unlock_platform_account"
    assert row["target"] == "substrate_action:unlock_platform_account"
    assert row["username"] == _ADMIN_USER["email"]

    details = row["details"]
    if isinstance(details, str):
        details = json.loads(details)

    assert "reason" in details, "audit row missing operator reason"
    assert details["reason"] == reason

    assert "target_ref" in details, "audit row missing target_ref"
    assert details["target_ref"]["email"] == email
    assert details["target_ref"]["table"] == "partners"

    assert "result" in details, "audit row missing handler result"
    # Handler echoes email in result; see substrate_actions.unlock_platform_account
    assert details["result"].get("email") == email


@_requires_db
@pytest.mark.asyncio
async def test_audit_row_redacts_nothing_the_operator_typed(
    pool, seed_locked_partner, monkeypatch,
):
    """Belt-and-suspenders: the audit row stores the reason byte-for-byte.
    Silently stripping or truncating an operator's reason text would
    defeat the point of the audit trail."""
    from tenant_middleware import admin_connection

    monkeypatch.setenv("SUBSTRATE_ACTIONS_ENABLED", "true")
    email = seed_locked_partner["email"]
    reason = (
        "[JR 2026-04-21 14:37 ET] Locked out after MFA reset. "
        "Phone callback to +1-555-0100 x42, voice match confirmed. "
        "Ticket OC-8814."
    )

    r = await _post({
        "action_key": "unlock_platform_account",
        "target_ref": {"table": "partners", "email": email},
        "reason": reason,
    })
    assert r.status_code == 200, r.text

    async with admin_connection(pool) as conn:
        details = await conn.fetchval(
            "SELECT details FROM admin_audit_log "
            "WHERE action = 'substrate.unlock_platform_account' "
            "  AND username = $1 "
            "ORDER BY id DESC LIMIT 1",
            _ADMIN_USER["email"],
        )

    if isinstance(details, str):
        details = json.loads(details)

    assert details["reason"] == reason, (
        "operator reason was modified between POST and audit write — "
        "this breaks the HIPAA §164.312(b) audit-trail contract"
    )

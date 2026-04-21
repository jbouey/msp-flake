"""Tests for POST /api/admin/substrate/action endpoint (Task 6).

Verifies:
  (a) unknown action_key returns 400 with valid_keys in detail
  (b) reason-length check enforces action.required_reason_chars
  (c) missing auth returns 401/403
  (d) feature flag off returns 503
  (e) TargetNotFound maps to 404 (DB-gated)
  (f) idempotency replay returns same action_id with already_completed (DB-gated)
  (g) writes exactly one admin_audit_log row per successful invocation (DB-gated)

DB-touching tests are gated by TEST_DATABASE_URL. Non-DB tests run always.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

# Add backend directory to sys.path so backend modules are importable.
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Set required env vars BEFORE any backend import walks through os.environ.
os.environ.setdefault("SESSION_TOKEN_SECRET", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

ADMIN_USER = {
    "id": "00000000-0000-0000-0000-aaaaaaaaaaaa",
    "email": "admin@osiriscare.net",
    "username": "admin",
    "role": "admin",
}


def _build_app(with_auth_override: bool = True) -> FastAPI:
    """Build a minimal FastAPI app with substrate router and (optional) mocked auth."""
    from substrate_action_api import router as substrate_router
    from auth import require_auth

    app = FastAPI()
    app.include_router(substrate_router)

    if with_auth_override:
        async def _mock_auth():
            return ADMIN_USER

        app.dependency_overrides[require_auth] = _mock_auth

    return app


async def _post(body, headers=None, app=None):
    app = app or _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.post(
            "/api/admin/substrate/action",
            json=body,
            headers=headers or {},
        )


# ---------------------------------------------------------------------------
# Non-DB tests (always run)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_endpoint_rejects_unknown_action_key(monkeypatch):
    """Unknown action_key returns 400 with valid_keys listing all 3 actions."""
    monkeypatch.setenv("SUBSTRATE_ACTIONS_ENABLED", "true")
    r = await _post(
        {
            "action_key": "delete_everything",
            "target_ref": {},
            "reason": "",
        }
    )
    assert r.status_code == 400
    body = r.json()
    assert "valid_keys" in body["detail"]
    assert set(body["detail"]["valid_keys"]) == {
        "cleanup_install_session",
        "unlock_platform_account",
        "reconcile_fleet_order",
    }


@pytest.mark.asyncio
async def test_endpoint_enforces_reason_length(monkeypatch):
    """unlock_platform_account requires reason >= 20 chars — short reason → 400."""
    monkeypatch.setenv("SUBSTRATE_ACTIONS_ENABLED", "true")
    r = await _post(
        {
            "action_key": "unlock_platform_account",
            "target_ref": {"table": "partners", "email": "a@b.c"},
            "reason": "too short",  # 9 chars
        }
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_endpoint_requires_auth(monkeypatch):
    """Without auth dependency override, require_auth raises 401 (no session cookie)."""
    monkeypatch.setenv("SUBSTRATE_ACTIONS_ENABLED", "true")
    app = _build_app(with_auth_override=False)
    r = await _post(
        {
            "action_key": "cleanup_install_session",
            "target_ref": {"mac": "aa:bb:cc:dd:ee:ff"},
            "reason": "",
        },
        app=app,
    )
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_endpoint_feature_flag_off_returns_503(monkeypatch):
    """When SUBSTRATE_ACTIONS_ENABLED is unset/false, endpoint returns 503."""
    monkeypatch.delenv("SUBSTRATE_ACTIONS_ENABLED", raising=False)
    r = await _post(
        {
            "action_key": "cleanup_install_session",
            "target_ref": {"mac": "aa:bb:cc:dd:ee:ff"},
            "reason": "",
        }
    )
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# DB-gated tests
# ---------------------------------------------------------------------------

_TEST_DB_URL = os.getenv("TEST_DATABASE_URL")

_requires_db = pytest.mark.skipif(
    not _TEST_DB_URL,
    reason="substrate endpoint integration tests require TEST_DATABASE_URL",
)

# Re-use the same TEST_MAC as test_substrate_actions_cleanup.py — same
# xdist warning applies here. Parallel workers collide on this MAC.
TEST_MAC = "11:22:33:44:55:66"
TEST_SITE_ID = "test-substrate-endpoint"


@pytest_asyncio.fixture
async def pool():
    """Create a short-lived asyncpg pool for the test session."""
    if not _TEST_DB_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    p = await asyncpg.create_pool(_TEST_DB_URL, min_size=1, max_size=2)
    try:
        yield p
    finally:
        await p.close()


# Not xdist-safe — uses a fixed TEST_MAC, so parallel workers would collide.
# Run this test file with `-p no:xdist` or `-n 0` if running distributed.
@pytest_asyncio.fixture
async def seed_stale_install_session(pool):
    """Insert one stale install_sessions row, yield target_ref dict, then clean up.

    Mirrors the fixture in test_substrate_actions_cleanup.py; kept local here
    because pytest fixtures don't share automatically across test files
    without a shared conftest.
    """
    from tenant_middleware import admin_connection

    async with admin_connection(pool) as conn:
        # Idempotent pre-clean so the INSERT below never hits a PK collision.
        await conn.execute(
            "DELETE FROM install_sessions WHERE mac_address = $1",
            TEST_MAC,
        )
        await conn.execute(
            "INSERT INTO install_sessions "
            "(session_id, site_id, mac_address, install_stage, checkin_count, "
            " first_seen, last_seen) "
            "VALUES ($1, $2, $3, $4, $5, "
            " NOW() - INTERVAL '2 hours', NOW() - INTERVAL '1 hour')",
            f"{TEST_SITE_ID}:{TEST_MAC}",
            TEST_SITE_ID,
            TEST_MAC,
            "live_usb",
            5,
        )

    yield {"mac": TEST_MAC, "site_id": TEST_SITE_ID}

    # Teardown — DELETE even if the test itself deleted the row (idempotent).
    async with admin_connection(pool) as conn:
        await conn.execute(
            "DELETE FROM install_sessions WHERE mac_address = $1",
            TEST_MAC,
        )
        # Best-effort cleanup of invocation rows from this test. Scope to
        # test-tagged idempotency_keys only so we don't scrub legit rows
        # under the same admin email (derived-key hashes won't match, but
        # explicitly supplied keys used in tests always start with "test-").
        await conn.execute(
            "DELETE FROM substrate_action_invocations "
            "WHERE actor_email = $1 AND idempotency_key LIKE 'test-%'",
            ADMIN_USER["email"],
        )


@_requires_db
@pytest.mark.asyncio
async def test_endpoint_target_not_found_maps_to_404(pool, monkeypatch):
    """Non-existent install_sessions row → handler raises TargetNotFound → 404."""
    monkeypatch.setenv("SUBSTRATE_ACTIONS_ENABLED", "true")
    r = await _post(
        {
            "action_key": "cleanup_install_session",
            "target_ref": {"mac": "00:00:00:00:00:00", "stage": "live_usb"},
            "reason": "",
        }
    )
    assert r.status_code == 404


@_requires_db
@pytest.mark.asyncio
async def test_endpoint_idempotency_replay(
    pool, seed_stale_install_session, monkeypatch
):
    """Same (actor, idempotency-key) within 24h → r2 returns the prior row
    with status=already_completed and the same action_id as r1."""
    monkeypatch.setenv("SUBSTRATE_ACTIONS_ENABLED", "true")
    mac = seed_stale_install_session["mac"]
    body = {
        "action_key": "cleanup_install_session",
        "target_ref": {"mac": mac, "stage": "live_usb"},
        "reason": "",
    }
    headers = {"Idempotency-Key": "test-key-abc-endpoint"}
    app = _build_app()

    r1 = await _post(body, headers=headers, app=app)
    r2 = await _post(body, headers=headers, app=app)

    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "completed"
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "already_completed"
    assert r2.json()["action_id"] == r1.json()["action_id"]


# Not xdist-safe — uses an unfiltered COUNT(*) on admin_audit_log.
# Parallel workers running this test concurrently would break the
# before/after invariant. Gate via -p no:xdist or -n 0 for this file.
@_requires_db
@pytest.mark.asyncio
async def test_endpoint_writes_one_audit_row(
    pool, seed_stale_install_session, monkeypatch
):
    """Each successful invocation writes exactly one admin_audit_log row."""
    from tenant_middleware import admin_connection

    monkeypatch.setenv("SUBSTRATE_ACTIONS_ENABLED", "true")
    mac = seed_stale_install_session["mac"]

    async with admin_connection(pool) as conn:
        before = await conn.fetchval(
            "SELECT COUNT(*) FROM admin_audit_log "
            "WHERE action = 'substrate.cleanup_install_session'"
        )

    r = await _post(
        {
            "action_key": "cleanup_install_session",
            "target_ref": {"mac": mac},
            "reason": "",
        }
    )
    assert r.status_code == 200, r.text

    async with admin_connection(pool) as conn:
        after = await conn.fetchval(
            "SELECT COUNT(*) FROM admin_audit_log "
            "WHERE action = 'substrate.cleanup_install_session'"
        )
    assert after == before + 1


@_requires_db
@pytest.mark.asyncio
async def test_endpoint_success_response_shape(
    pool, seed_stale_install_session, monkeypatch
):
    """Fresh (non-replay) invocation returns action_id + status=completed + details."""
    monkeypatch.setenv("SUBSTRATE_ACTIONS_ENABLED", "true")
    mac = seed_stale_install_session["mac"]
    r = await _post(
        {
            "action_key": "cleanup_install_session",
            "target_ref": {"mac": mac, "stage": "live_usb"},
            "reason": "",
        }
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"action_id", "status", "details"}
    assert body["status"] == "completed"
    assert body["action_id"]  # non-empty
    assert body["details"]["deleted"] == 1
    assert body["details"]["mac"] == mac
    assert body["details"]["stage"] == "live_usb"


@_requires_db
@pytest.mark.asyncio
async def test_endpoint_idempotency_race_beyond_24h_window(
    pool, seed_stale_install_session, monkeypatch
):
    """Pre-existing row outside 24h window triggers UniqueViolationError on
    INSERT; endpoint re-reads and returns replay shape, not 409."""
    import json as _json

    from tenant_middleware import admin_connection

    monkeypatch.setenv("SUBSTRATE_ACTIONS_ENABLED", "true")
    mac = seed_stale_install_session["mac"]
    race_key = f"test-race-{uuid.uuid4()}"

    # Seed a prior invocation row outside the 24h replay window so the
    # pre-flight SELECT misses it but the unique index still fires on INSERT.
    async with admin_connection(pool) as conn:
        audit_id = await conn.fetchval(
            "INSERT INTO admin_audit_log "
            "(user_id, username, action, target, details, ip_address) "
            "VALUES (NULL, $1, $2, $3, $4::jsonb, $5) "
            "RETURNING id",
            ADMIN_USER["email"],
            "substrate.cleanup_install_session",
            "substrate_action:cleanup_install_session",
            _json.dumps({"reason": "", "target_ref": {}, "result": {}}),
            None,
        )
        prior_inv_id = await conn.fetchval(
            "INSERT INTO substrate_action_invocations "
            "(idempotency_key, actor_email, action_key, target_ref, "
            " reason, result_status, result_body, admin_audit_id, created_at) "
            "VALUES ($1, $2, $3, $4::jsonb, $5, 'completed', $6::jsonb, $7, "
            "        now() - INTERVAL '25 hours') "
            "RETURNING id",
            race_key,
            ADMIN_USER["email"],
            "cleanup_install_session",
            _json.dumps({"mac": mac, "stage": "live_usb"}),
            "",
            _json.dumps(
                {
                    "status": "completed",
                    "details": {
                        "deleted": 1,
                        "mac": mac,
                        "stage": "live_usb",
                        "checkin_count": 5,
                        "first_seen": "2026-04-17T00:00:00+00:00",
                    },
                }
            ),
            audit_id,
        )

    # Endpoint call with same idempotency_key triggers the race path:
    # pre-flight SELECT misses (25h > 24h window), INSERT hits unique index.
    r = await _post(
        {
            "action_key": "cleanup_install_session",
            "target_ref": {"mac": mac, "stage": "live_usb"},
            "reason": "",
        },
        headers={"Idempotency-Key": race_key},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "already_completed"
    assert body["action_id"] == str(prior_inv_id)

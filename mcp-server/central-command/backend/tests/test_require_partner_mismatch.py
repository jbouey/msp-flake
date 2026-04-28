"""Tests for `require_partner` token-mismatch detection (P0 hardening
2026-04-28 round-table angle 3).

Pre-fix: when both X-API-Key and session cookie were sent (the new
default after the CSRF additive rewrite in commits 0c81fef6 +
efe413cf), require_partner checked X-API-Key first and returned
immediately. A leaked X-API-Key silently overrode the session
cookie — including a session for a DIFFERENT partner on a shared
workstation.

Post-fix: when both credentials resolve to a partner.id, they MUST
match. Mismatch → log `auth_token_partner_mismatch` ERROR + 401.

This test pins the security control. A regression that re-introduces
the silent-precedence behavior fails CI loudly.
"""
from __future__ import annotations

import os
import sys
import uuid
import logging
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Set required env BEFORE importing partners
os.environ.setdefault("SESSION_TOKEN_SECRET", "test-secret-for-mismatch-tests")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("AGENT_BEARER_TOKEN", "test-token")

_BACKEND = Path(__file__).resolve().parent.parent
_MCP_SERVER = _BACKEND.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
if str(_MCP_SERVER) not in sys.path:
    sys.path.insert(0, str(_MCP_SERVER))

from fastapi import HTTPException


PARTNER_A_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
PARTNER_B_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


class _FakeAcquireCtx:
    def __init__(self, conn): self._conn = conn
    async def __aenter__(self): return self._conn
    async def __aexit__(self, *a): pass


class _FakeTxn:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class _FakePool:
    def __init__(self, conn): self._conn = conn
    def acquire(self): return _FakeAcquireCtx(self._conn)


class _FakeConn:
    """Returns canned rows for a list of (query-substring, row) pairs.
    First match wins. None if nothing matches."""

    def __init__(self, mappings):
        self._mappings = list(mappings)

    async def fetchrow(self, query, *args):
        for needle, row in self._mappings:
            if needle in query:
                return row
        return None

    async def fetchval(self, query, *args):
        for needle, row in self._mappings:
            if needle in query and row:
                # row is dict-like; return first value
                return next(iter(row.values()))
        return None

    async def execute(self, query, *args):
        return "OK"

    def transaction(self): return _FakeTxn()


def _api_key_partner_row(pid):
    """Shape returned by get_partner_from_api_key — a Record-like dict."""
    return {
        "id": pid,
        "name": f"Partner {str(pid)[-4:]}",
        "slug": f"partner-{str(pid)[-4:]}",
        "status": "active",
        "api_key_expires_at": None,
    }


def _session_row(pid):
    """Shape returned by the partner_sessions JOIN query in require_partner Step 2."""
    return {
        "partner_id": pid,
        "id": pid,
        "name": f"Partner {str(pid)[-4:]}",
        "slug": f"partner-{str(pid)[-4:]}",
        "status": "active",
        "user_role": "admin",
        "partner_user_id": None,
    }


@pytest.fixture
def patch_pool_and_api_key():
    """Yield a callable that wires fake pool + fake api-key resolver."""
    from contextlib import contextmanager

    @contextmanager
    def _setup(api_key_returns, session_returns):
        conn_mappings = []
        if session_returns is not None:
            conn_mappings.append(("FROM partner_sessions", session_returns))
        conn = _FakeConn(conn_mappings)
        pool = _FakePool(conn)

        async def _get_pool():
            return pool

        async def _get_partner_from_api_key(api_key):
            return api_key_returns

        with patch("dashboard_api.partners.get_pool", _get_pool), \
             patch("dashboard_api.partners.get_partner_from_api_key", _get_partner_from_api_key):
            yield

    return _setup


# ---------------------------------------------------------------------------
# Mismatch detection — the P0 control
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_both_creds_same_partner_succeeds(patch_pool_and_api_key, caplog):
    """Both X-API-Key and cookie resolve to PARTNER A → success, no
    mismatch log."""
    from dashboard_api.partners import require_partner

    with patch_pool_and_api_key(
        api_key_returns=_api_key_partner_row(PARTNER_A_ID),
        session_returns=_session_row(PARTNER_A_ID),
    ):
        with caplog.at_level(logging.ERROR, logger="partners"):
            result = await require_partner(
                x_api_key="key-for-A",
                osiris_partner_session="cookie-for-A",
            )
    assert str(result["id"]) == str(PARTNER_A_ID)
    mismatches = [r for r in caplog.records if "auth_token_partner_mismatch" in r.getMessage()]
    assert not mismatches, "matching credentials must NOT log mismatch"


@pytest.mark.asyncio
async def test_both_creds_different_partners_401(patch_pool_and_api_key, caplog):
    """X-API-Key resolves to PARTNER A, cookie to PARTNER B → 401 with
    auth_token_partner_mismatch ERROR log. The P0 security control."""
    from dashboard_api.partners import require_partner

    with patch_pool_and_api_key(
        api_key_returns=_api_key_partner_row(PARTNER_A_ID),
        session_returns=_session_row(PARTNER_B_ID),
    ):
        with caplog.at_level(logging.ERROR, logger="partners"):
            with pytest.raises(HTTPException) as exc_info:
                await require_partner(
                    x_api_key="key-for-A",
                    osiris_partner_session="cookie-for-B",
                )

    assert exc_info.value.status_code == 401
    assert "mismatch" in exc_info.value.detail.lower()

    mismatches = [r for r in caplog.records if "auth_token_partner_mismatch" in r.getMessage()]
    assert mismatches, (
        "P0 control: divergent credentials MUST log auth_token_partner_mismatch "
        "ERROR. A regression that drops the log is a security regression."
    )
    record = mismatches[-1]
    assert record.levelno == logging.ERROR
    assert hasattr(record, "api_key_partner_id")
    assert hasattr(record, "session_partner_id")
    assert record.api_key_partner_id == str(PARTNER_A_ID)
    assert record.session_partner_id == str(PARTNER_B_ID)


@pytest.mark.asyncio
async def test_only_api_key_succeeds(patch_pool_and_api_key):
    """API key alone resolves cleanly to its partner — no mismatch path
    fires when cookie is absent."""
    from dashboard_api.partners import require_partner

    with patch_pool_and_api_key(
        api_key_returns=_api_key_partner_row(PARTNER_A_ID),
        session_returns=None,
    ):
        result = await require_partner(
            x_api_key="key-for-A",
            osiris_partner_session=None,
        )
    assert str(result["id"]) == str(PARTNER_A_ID)


@pytest.mark.asyncio
async def test_only_cookie_succeeds(patch_pool_and_api_key):
    """Session cookie alone resolves cleanly — no mismatch path fires
    when api key is absent."""
    from dashboard_api.partners import require_partner

    with patch_pool_and_api_key(
        api_key_returns=None,
        session_returns=_session_row(PARTNER_A_ID),
    ):
        result = await require_partner(
            x_api_key=None,
            osiris_partner_session="cookie-for-A",
        )
    assert str(result["id"]) == str(PARTNER_A_ID)


@pytest.mark.asyncio
async def test_invalid_api_key_401(patch_pool_and_api_key):
    """Invalid API key (resolver returns None) → 401 immediately, even
    with a valid cookie present (api-key-invalid is a separate failure
    mode from token mismatch)."""
    from dashboard_api.partners import require_partner

    with patch_pool_and_api_key(
        api_key_returns=None,
        session_returns=_session_row(PARTNER_A_ID),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await require_partner(
                x_api_key="bogus-key",
                osiris_partner_session="cookie-for-A",
            )
    assert exc_info.value.status_code == 401
    assert "Invalid or inactive API key" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_no_credentials_401(patch_pool_and_api_key):
    """Neither credential present → 401 'Authentication required'."""
    from dashboard_api.partners import require_partner

    with patch_pool_and_api_key(
        api_key_returns=None,
        session_returns=None,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await require_partner(
                x_api_key=None,
                osiris_partner_session=None,
            )
    assert exc_info.value.status_code == 401
    assert "Authentication required" in str(exc_info.value.detail)

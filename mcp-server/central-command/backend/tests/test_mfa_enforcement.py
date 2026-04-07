"""Tests for MFA enforcement in admin authentication.

Verifies that:
1. mfa_required=True + mfa_enabled=False → login blocked with mfa_setup_required
2. mfa_required=True + mfa_enabled=True  → TOTP pending flow (mfa_required status)
3. mfa_required=False + mfa_enabled=False → login proceeds, session created
4. mfa_required=False + mfa_enabled=True  → user opted in, TOTP pending flow
"""

import os
import sys
import types
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Environment + stub setup (must precede auth import)
# ---------------------------------------------------------------------------

os.environ.setdefault("SESSION_TOKEN_SECRET", "test-secret-for-mfa-enforcement-tests")

for mod_name in (
    "fastapi",
    "sqlalchemy",
    "sqlalchemy.ext",
    "sqlalchemy.ext.asyncio",
):
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

sys.modules["fastapi"].Request = object
sys.modules["fastapi"].HTTPException = Exception
sys.modules["fastapi"].Depends = lambda x: x
_sa_mod = sys.modules.setdefault("sqlalchemy", types.ModuleType("sqlalchemy"))
_sa_mod.text = lambda x: x
_sa_async = sys.modules.setdefault("sqlalchemy.ext.asyncio", types.ModuleType("sqlalchemy.ext.asyncio"))
_sa_async.AsyncSession = object

import auth
from auth import hash_password


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(*, mfa_enabled: bool, mfa_required: bool):
    """Build a minimal AsyncSession mock for authenticate_user.

    The function calls db.execute() multiple times in sequence:
      1. SELECT user row       → needs 8-element tuple
      2. UPDATE failed_attempts (password ok path - no fetchone needed)
      3. INSERT admin_audit_log for reset (no fetchone needed)
      ... but actually on GOOD password path:
      2. reset failed_attempts  (no fetchone)
      3. SELECT mfa row         → needs 2-element tuple
      4. possibly INSERT session or INSERT audit_log

    We key on whether the SQL statement contains 'mfa_enabled' to return the
    MFA row; everything else returns a result whose fetchone() returns None.
    """
    password_hash = hash_password("GoodPass@1234!")

    # 8-element user row (matches SELECT id,username,password_hash,display_name,role,status,...)
    user_tuple = (1, "admin", password_hash, "Admin User", "admin", "active", 0, None)
    user_result = MagicMock()
    user_result.fetchone = MagicMock(return_value=user_tuple)

    # 2-element MFA row
    mfa_tuple = (mfa_enabled, mfa_required)
    mfa_result = MagicMock()
    mfa_result.fetchone = MagicMock(return_value=mfa_tuple)

    # Generic result for all other queries (INSERT, UPDATE, etc.)
    generic_result = MagicMock()
    generic_result.fetchone = MagicMock(return_value=None)

    # Track which execute call this is
    call_count = {"n": 0}

    async def _execute(stmt, params=None):
        call_count["n"] += 1
        # stmt is the raw string (our stub sets text = lambda x: x)
        stmt_str = str(stmt) if stmt else ""
        if "SELECT id, username, password_hash" in stmt_str:
            return user_result
        if "mfa_enabled" in stmt_str and "mfa_required" in stmt_str:
            return mfa_result
        # Everything else (UPDATE, INSERT, etc.)
        return generic_result

    db = MagicMock()
    db.execute = _execute
    db.commit = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAdminMfaEnforcement:
    """Admin authenticate_user MFA enforcement tests."""

    def test_mfa_required_but_not_enabled_blocks_login(self):
        """When mfa_required=True and mfa_enabled=False, login must be blocked."""
        db = _make_db(mfa_enabled=False, mfa_required=True)

        success, token, data = asyncio.run(
            auth.authenticate_user(
                db=db,
                username="admin",
                password="GoodPass@1234!",
                ip_address="127.0.0.1",
                user_agent="pytest",
            )
        )

        assert success is False
        assert token is None
        assert data is not None
        assert data.get("status") == "mfa_setup_required", (
            f"Expected status='mfa_setup_required', got: {data}"
        )
        assert "error" in data

    def test_mfa_required_and_enabled_proceeds_to_totp(self):
        """When both mfa_required and mfa_enabled are True, enter TOTP pending flow."""
        db = _make_db(mfa_enabled=True, mfa_required=True)

        success, token, data = asyncio.run(
            auth.authenticate_user(
                db=db,
                username="admin",
                password="GoodPass@1234!",
                ip_address="127.0.0.1",
                user_agent="pytest",
            )
        )

        assert success is False
        assert token is None
        assert data is not None
        assert data.get("status") == "mfa_required", (
            f"Expected status='mfa_required' (TOTP pending), got: {data}"
        )
        assert "mfa_token" in data

    def test_mfa_not_required_login_succeeds(self):
        """When mfa_required=False and mfa_enabled=False, session is created."""
        db = _make_db(mfa_enabled=False, mfa_required=False)

        success, token, data = asyncio.run(
            auth.authenticate_user(
                db=db,
                username="admin",
                password="GoodPass@1234!",
                ip_address="127.0.0.1",
                user_agent="pytest",
            )
        )

        assert success is True
        assert token is not None
        assert isinstance(token, str)
        assert len(token) > 10

    def test_mfa_not_required_but_user_enrolled_proceeds_to_totp(self):
        """User opted into MFA (enabled=True) regardless of org requirement."""
        db = _make_db(mfa_enabled=True, mfa_required=False)

        success, token, data = asyncio.run(
            auth.authenticate_user(
                db=db,
                username="admin",
                password="GoodPass@1234!",
                ip_address="127.0.0.1",
                user_agent="pytest",
            )
        )

        assert data.get("status") == "mfa_required", (
            f"Expected TOTP pending flow, got: {data}"
        )
        assert "mfa_token" in data

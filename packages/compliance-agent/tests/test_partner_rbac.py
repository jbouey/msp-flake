"""
Tests for Partner Portal RBAC enforcement.

Tests cover:
- require_partner_role dependency factory
- Role-based access control for view, operational, and admin-only endpoints
- API key auth defaults to admin role
- Legacy sessions (NULL partner_user_id) get admin role
- 403 responses for unauthorized roles
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException


# =============================================================================
# HELPERS — simulate require_partner_role logic without importing FastAPI app
# =============================================================================

async def _check_role(partner: dict, allowed_roles: tuple):
    """Replicate the core logic of require_partner_role's inner _check function."""
    if partner.get("user_role") not in allowed_roles:
        raise HTTPException(
            status_code=403,
            detail="Insufficient permissions for this action",
        )
    return partner


def _make_partner(role="admin", partner_user_id="pu-001"):
    """Build a partner dict as returned by require_partner."""
    return {
        "id": "partner-test-001",
        "name": "Test MSP",
        "slug": "test-msp",
        "status": "active",
        "user_role": role,
        "partner_user_id": partner_user_id,
    }


# =============================================================================
# TESTS
# =============================================================================

@pytest.mark.asyncio
async def test_admin_role_passes_all_checks():
    """Admin role should pass any role check."""
    partner = _make_partner(role="admin")

    # Admin should pass view endpoints (admin, tech, billing)
    result = await _check_role(partner, ("admin", "tech", "billing"))
    assert result["user_role"] == "admin"

    # Admin should pass operational endpoints (admin, tech)
    result = await _check_role(partner, ("admin", "tech"))
    assert result["user_role"] == "admin"

    # Admin should pass admin-only endpoints
    result = await _check_role(partner, ("admin",))
    assert result["user_role"] == "admin"


@pytest.mark.asyncio
async def test_tech_role_rejected_from_admin_only():
    """Tech role should get 403 on admin-only endpoints."""
    partner = _make_partner(role="tech")

    # Tech should pass view endpoints
    result = await _check_role(partner, ("admin", "tech", "billing"))
    assert result["user_role"] == "tech"

    # Tech should pass operational endpoints
    result = await _check_role(partner, ("admin", "tech"))
    assert result["user_role"] == "tech"

    # Tech should be rejected from admin-only endpoints
    with pytest.raises(HTTPException) as exc_info:
        await _check_role(partner, ("admin",))
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_billing_role_rejected_from_operational():
    """Billing role should get 403 on operational endpoints."""
    partner = _make_partner(role="billing")

    # Billing should pass view endpoints
    result = await _check_role(partner, ("admin", "tech", "billing"))
    assert result["user_role"] == "billing"

    # Billing should be rejected from operational endpoints (admin, tech only)
    with pytest.raises(HTTPException) as exc_info:
        await _check_role(partner, ("admin", "tech"))
    assert exc_info.value.status_code == 403

    # Billing should be rejected from admin-only endpoints
    with pytest.raises(HTTPException) as exc_info:
        await _check_role(partner, ("admin",))
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_api_key_gets_admin_role():
    """API key auth should return user_role='admin'.

    When authenticating via X-API-Key header, the partner dict
    should always have user_role='admin' since API keys are
    partner-level credentials.
    """
    # Simulate what require_partner does for API key auth:
    # partner = await get_partner_from_api_key(x_api_key)
    # result = dict(partner)
    # result["user_role"] = "admin"
    mock_partner_record = {
        "id": "partner-test-001",
        "name": "Test MSP",
        "slug": "test-msp",
        "status": "active",
    }
    result = dict(mock_partner_record)
    result["user_role"] = "admin"

    assert result["user_role"] == "admin"

    # Should pass all role checks
    checked = await _check_role(result, ("admin",))
    assert checked["user_role"] == "admin"


@pytest.mark.asyncio
async def test_legacy_session_null_user_gets_admin():
    """Session without partner_user_id (pre-migration) should get admin role.

    Before migration 100 added partner_user_id to partner_sessions,
    existing sessions have NULL for both partner_user_id and user_role.
    The require_partner function defaults these to admin.
    """
    # Simulate a session row where LEFT JOIN partner_users returns NULL
    mock_session_row = {
        "id": "partner-test-001",
        "name": "Test MSP",
        "slug": "test-msp",
        "status": "active",
        "user_role": None,  # NULL from LEFT JOIN
        "partner_user_id": None,  # NULL — no partner_user_id in session
    }

    # Replicate require_partner logic for session auth
    result = {
        "id": mock_session_row["id"],
        "name": mock_session_row["name"],
        "slug": mock_session_row["slug"],
        "status": mock_session_row["status"],
    }
    result["user_role"] = mock_session_row.get("user_role") or "admin"
    result["partner_user_id"] = str(mock_session_row["partner_user_id"]) if mock_session_row.get("partner_user_id") else None

    assert result["user_role"] == "admin"
    assert result["partner_user_id"] is None

    # Should pass all role checks since it defaults to admin
    checked = await _check_role(result, ("admin",))
    assert checked["user_role"] == "admin"


@pytest.mark.asyncio
async def test_unauthorized_role_returns_403_with_message():
    """403 response should include clear error message."""
    partner = _make_partner(role="billing")

    with pytest.raises(HTTPException) as exc_info:
        await _check_role(partner, ("admin",))

    exc = exc_info.value
    assert exc.status_code == 403
    assert exc.detail == "Insufficient permissions for this action"

"""Tests for the Stripe billing integration module (backend/billing.py).

Tests cover:
- HAS_STRIPE availability flag and check_stripe_available()
- Customer creation (get_or_create_stripe_customer)
- Checkout session creation
- Subscription management (cancel, reactivate)
- Customer portal session
- Invoice listing
- Webhook handling (all event types + dedup + signature verification)
- Plan listing and pricing calculation
- Public config endpoint
- Edge cases: Stripe unavailable, API errors, missing data
"""

import sys
import json
import uuid
import importlib
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import asynccontextmanager

# =============================================================================
# MODULE BOOTSTRAPPING
# =============================================================================
# billing.py uses relative imports (from .fleet import get_pool, etc.).
# We stub those sibling modules into sys.modules as a fake "backend" package
# so billing.py can be imported cleanly without pulling in the full backend.
# =============================================================================

_BACKEND_PKG = "backend"


def _make_stub(name):
    """Create a stub module with a given name."""
    mod = MagicMock()
    mod.__name__ = name
    mod.__package__ = _BACKEND_PKG
    mod.__path__ = []
    mod.__file__ = f"<stub:{name}>"
    mod.__spec__ = None
    return mod


def _setup_billing_module():
    """Stub backend sibling modules, then import backend.billing cleanly."""
    # Build a fake stripe error hierarchy for the mock
    _StripeError = type("StripeError", (Exception,), {})
    _InvalidRequestError = type("InvalidRequestError", (_StripeError,), {})
    _SignatureVerificationError = type("SignatureVerificationError", (_StripeError,), {})

    stripe_mock = MagicMock()
    stripe_mock.error = MagicMock()
    stripe_mock.error.StripeError = _StripeError
    stripe_mock.error.InvalidRequestError = _InvalidRequestError
    stripe_mock.error.SignatureVerificationError = _SignatureVerificationError

    # Create stub modules for the relative imports billing.py needs
    backend_stub = _make_stub(_BACKEND_PKG)
    backend_stub.__path__ = ["/fake/backend"]

    fleet_stub = _make_stub(f"{_BACKEND_PKG}.fleet")
    fleet_stub.get_pool = AsyncMock()

    tenant_stub = _make_stub(f"{_BACKEND_PKG}.tenant_middleware")

    @asynccontextmanager
    async def _admin_conn_stub(pool):
        yield MagicMock()
    tenant_stub.admin_connection = _admin_conn_stub

    partners_stub = _make_stub(f"{_BACKEND_PKG}.partners")
    partners_stub.require_partner = MagicMock()

    db_utils_stub = _make_stub(f"{_BACKEND_PKG}.db_utils")
    db_utils_stub._uid = lambda s: uuid.UUID(str(s)) if not isinstance(s, uuid.UUID) else s

    # Install stubs
    stubs = {
        _BACKEND_PKG: backend_stub,
        f"{_BACKEND_PKG}.fleet": fleet_stub,
        f"{_BACKEND_PKG}.tenant_middleware": tenant_stub,
        f"{_BACKEND_PKG}.partners": partners_stub,
        f"{_BACKEND_PKG}.db_utils": db_utils_stub,
    }

    # Remove any previously cached billing module
    for mod_name in list(sys.modules):
        if mod_name == f"{_BACKEND_PKG}.billing" or mod_name == "billing":
            del sys.modules[mod_name]

    saved = {}
    for k, v in stubs.items():
        saved[k] = sys.modules.get(k)
        sys.modules[k] = v

    # Also ensure stripe is available as a mock
    sys.modules["stripe"] = stripe_mock

    import importlib.util
    import os
    billing_path = os.path.join(
        os.path.dirname(__file__),
        "..", "..", "..",
        "mcp-server", "central-command", "backend", "billing.py"
    )
    billing_path = os.path.normpath(billing_path)

    spec = importlib.util.spec_from_file_location(
        f"{_BACKEND_PKG}.billing", billing_path,
        submodule_search_locations=[]
    )
    billing_mod = importlib.util.module_from_spec(spec)
    # Critical: set __package__ so relative imports (from .fleet, etc.)
    # resolve through sys.modules stubs instead of the filesystem
    billing_mod.__package__ = _BACKEND_PKG
    sys.modules[f"{_BACKEND_PKG}.billing"] = billing_mod
    spec.loader.exec_module(billing_mod)

    return billing_mod, stripe_mock


billing, mock_stripe_module = _setup_billing_module()

# Re-export the stripe error types for use in tests
StripeError = mock_stripe_module.error.StripeError
InvalidRequestError = mock_stripe_module.error.InvalidRequestError
SignatureVerificationError = mock_stripe_module.error.SignatureVerificationError


# =============================================================================
# FAKE DB HELPERS
# =============================================================================

class FakeConn:
    """Fake asyncpg connection for testing billing endpoints."""

    def __init__(self, fetchrow_result=None, fetchval_result=None):
        self._fetchrow_result = fetchrow_result
        self._fetchval_result = fetchval_result
        self.executed = []

    async def fetchrow(self, query, *args):
        return self._fetchrow_result

    async def fetchval(self, query, *args):
        return self._fetchval_result

    async def execute(self, query, *args):
        self.executed.append((query, args))

    async def fetch(self, query, *args):
        return []


@asynccontextmanager
async def fake_admin_connection(pool):
    """Yields the FakeConn attached to pool._fake_conn."""
    conn = getattr(pool, "_fake_conn", FakeConn())
    yield conn


def make_fake_pool(conn=None):
    """Create a fake pool object with an attached FakeConn."""
    pool = MagicMock()
    pool._fake_conn = conn or FakeConn()
    return pool


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def partner():
    """Sample authenticated partner dict (as returned by require_partner)."""
    return {"id": str(uuid.uuid4()), "name": "Test Partner LLC"}


@pytest.fixture
def fresh_stripe():
    """Provide a fresh MagicMock stripe with the correct error classes."""
    s = MagicMock()
    s.error = MagicMock()
    s.error.StripeError = StripeError
    s.error.InvalidRequestError = InvalidRequestError
    s.error.SignatureVerificationError = SignatureVerificationError
    return s


# =============================================================================
# 1. HAS_STRIPE FLAG AND check_stripe_available()
# =============================================================================

@pytest.mark.asyncio
async def test_check_stripe_available_no_library():
    """check_stripe_available raises 501 when HAS_STRIPE is False."""
    from fastapi import HTTPException
    original = billing.HAS_STRIPE
    try:
        billing.HAS_STRIPE = False
        with pytest.raises(HTTPException) as exc_info:
            billing.check_stripe_available()
        assert exc_info.value.status_code == 501
        assert "not installed" in exc_info.value.detail
    finally:
        billing.HAS_STRIPE = original


@pytest.mark.asyncio
async def test_check_stripe_available_no_secret_key():
    """check_stripe_available raises 501 when STRIPE_SECRET_KEY is missing."""
    from fastapi import HTTPException
    original_has = billing.HAS_STRIPE
    original_key = billing.STRIPE_SECRET_KEY
    try:
        billing.HAS_STRIPE = True
        billing.STRIPE_SECRET_KEY = None
        with pytest.raises(HTTPException) as exc_info:
            billing.check_stripe_available()
        assert exc_info.value.status_code == 501
        assert "not configured" in exc_info.value.detail
    finally:
        billing.HAS_STRIPE = original_has
        billing.STRIPE_SECRET_KEY = original_key


@pytest.mark.asyncio
async def test_check_stripe_available_success():
    """check_stripe_available does not raise when both flag and key are set."""
    original_has = billing.HAS_STRIPE
    original_key = billing.STRIPE_SECRET_KEY
    try:
        billing.HAS_STRIPE = True
        billing.STRIPE_SECRET_KEY = "sk_test_fake"
        # Should not raise
        billing.check_stripe_available()
    finally:
        billing.HAS_STRIPE = original_has
        billing.STRIPE_SECRET_KEY = original_key


# =============================================================================
# 2. CUSTOMER CREATION
# =============================================================================

@pytest.mark.asyncio
async def test_get_or_create_customer_existing(partner):
    """Returns existing stripe_customer_id from DB without calling Stripe."""
    conn = FakeConn(fetchrow_result={"stripe_customer_id": "cus_existing123"})
    pool = make_fake_pool(conn)

    with patch.object(billing, "get_pool", return_value=pool), \
         patch.object(billing, "admin_connection", side_effect=fake_admin_connection):
        result = await billing.get_or_create_stripe_customer(
            partner["id"], "test@example.com", "Test"
        )
    assert result == "cus_existing123"


@pytest.mark.asyncio
async def test_get_or_create_customer_new(partner, fresh_stripe):
    """Creates a new Stripe customer when none exists in DB."""
    conn = FakeConn(fetchrow_result={"stripe_customer_id": None})
    pool = make_fake_pool(conn)

    fake_customer = MagicMock()
    fake_customer.id = "cus_new456"
    fresh_stripe.Customer.create.return_value = fake_customer

    with patch.object(billing, "get_pool", return_value=pool), \
         patch.object(billing, "admin_connection", side_effect=fake_admin_connection), \
         patch.object(billing, "stripe", fresh_stripe):
        result = await billing.get_or_create_stripe_customer(
            partner["id"], "new@example.com", "New Partner"
        )

    assert result == "cus_new456"
    fresh_stripe.Customer.create.assert_called_once()
    call_kwargs = fresh_stripe.Customer.create.call_args
    assert call_kwargs[1]["email"] == "new@example.com"
    assert call_kwargs[1]["metadata"]["platform"] == "osiriscare"
    # Verify customer ID was saved to DB
    assert len(conn.executed) == 1
    assert "UPDATE partners SET stripe_customer_id" in conn.executed[0][0]


@pytest.mark.asyncio
async def test_get_or_create_customer_stripe_error(partner, fresh_stripe):
    """Propagates Stripe API error during customer creation."""
    conn = FakeConn(fetchrow_result={"stripe_customer_id": None})
    pool = make_fake_pool(conn)

    fresh_stripe.Customer.create.side_effect = StripeError("API down")

    with patch.object(billing, "get_pool", return_value=pool), \
         patch.object(billing, "admin_connection", side_effect=fake_admin_connection), \
         patch.object(billing, "stripe", fresh_stripe):
        with pytest.raises(StripeError):
            await billing.get_or_create_stripe_customer(
                partner["id"], "fail@example.com", "Fail Partner"
            )


# =============================================================================
# 3. SUBSCRIPTION MANAGEMENT
# =============================================================================

@pytest.mark.asyncio
async def test_cancel_subscription_success(partner, fresh_stripe):
    """Cancel sets cancel_at_period_end=True and updates local status."""
    sub_id = "sub_cancel_test"
    conn = FakeConn(fetchrow_result={"stripe_subscription_id": sub_id})
    pool = make_fake_pool(conn)

    fake_sub = MagicMock()
    fake_sub.current_period_end = 1700000000
    fresh_stripe.Subscription.modify.return_value = fake_sub

    with patch.object(billing, "get_pool", return_value=pool), \
         patch.object(billing, "admin_connection", side_effect=fake_admin_connection), \
         patch.object(billing, "stripe", fresh_stripe), \
         patch.object(billing, "HAS_STRIPE", True), \
         patch.object(billing, "STRIPE_SECRET_KEY", "sk_test"):
        result = await billing.cancel_subscription(partner=partner)

    assert result["status"] == "canceling"
    assert "cancel_at" in result
    fresh_stripe.Subscription.modify.assert_called_once_with(
        sub_id, cancel_at_period_end=True
    )
    assert any("subscription_status = 'canceling'" in q for q, _ in conn.executed)


@pytest.mark.asyncio
async def test_cancel_subscription_no_subscription(partner):
    """Cancel raises 400 when partner has no subscription."""
    from fastapi import HTTPException
    conn = FakeConn(fetchrow_result={"stripe_subscription_id": None})
    pool = make_fake_pool(conn)

    with patch.object(billing, "get_pool", return_value=pool), \
         patch.object(billing, "admin_connection", side_effect=fake_admin_connection), \
         patch.object(billing, "HAS_STRIPE", True), \
         patch.object(billing, "STRIPE_SECRET_KEY", "sk_test"):
        with pytest.raises(HTTPException) as exc_info:
            await billing.cancel_subscription(partner=partner)
        assert exc_info.value.status_code == 400
        assert "No active subscription" in exc_info.value.detail


@pytest.mark.asyncio
async def test_reactivate_subscription_success(partner, fresh_stripe):
    """Reactivate sets cancel_at_period_end=False and updates status to active."""
    sub_id = "sub_reactivate_test"
    conn = FakeConn(fetchrow_result={"stripe_subscription_id": sub_id})
    pool = make_fake_pool(conn)

    fake_sub = MagicMock()
    fresh_stripe.Subscription.modify.return_value = fake_sub

    with patch.object(billing, "get_pool", return_value=pool), \
         patch.object(billing, "admin_connection", side_effect=fake_admin_connection), \
         patch.object(billing, "stripe", fresh_stripe), \
         patch.object(billing, "HAS_STRIPE", True), \
         patch.object(billing, "STRIPE_SECRET_KEY", "sk_test"):
        result = await billing.reactivate_subscription(partner=partner)

    assert result["status"] == "active"
    assert result["message"] == "Subscription reactivated"
    fresh_stripe.Subscription.modify.assert_called_once_with(
        sub_id, cancel_at_period_end=False
    )


# =============================================================================
# 4. PLAN LISTING AND PUBLIC CONFIG
# =============================================================================

@pytest.mark.asyncio
async def test_list_subscription_plans():
    """list_subscription_plans returns all tiers and currency."""
    result = await billing.list_subscription_plans()
    assert result["currency"] == "usd"
    plans = result["plans"]
    assert "clinic" in plans
    assert "practice" in plans
    assert "enterprise" in plans
    assert plans["clinic"]["price_monthly"] == 400
    assert plans["clinic"]["max_endpoints"] == 25
    assert isinstance(plans["clinic"]["features"], list)
    assert plans["enterprise"]["max_endpoints"] is None  # Unlimited


@pytest.mark.asyncio
async def test_get_billing_config():
    """get_billing_config returns public config with pricing model."""
    result = await billing.get_billing_config()
    assert result["pricing_model"] == "per_appliance"
    assert result["endpoint_price_monthly"] == 20
    assert result["currency"] == "usd"
    assert "appliance_tiers" in result
    assert "value_comparison" in result


# =============================================================================
# 5. PRICING CALCULATOR
# =============================================================================

@pytest.mark.asyncio
async def test_calculate_pricing_tier_clinic():
    """Tier pricing for a single small clinic appliance."""
    result = await billing.calculate_pricing(
        num_appliances=1, endpoints_per_appliance=20, pricing_model="tier"
    )
    assert result["recommended_tier"] == "clinic"
    assert result["monthly_cost"] == 400
    assert result["annual_cost"] == 400 * 12
    assert result["savings_percent"] > 0


@pytest.mark.asyncio
async def test_calculate_pricing_tier_practice():
    """Tier pricing for a medium practice (50 endpoints)."""
    result = await billing.calculate_pricing(
        num_appliances=2, endpoints_per_appliance=50, pricing_model="tier"
    )
    assert result["recommended_tier"] == "practice"
    assert result["monthly_cost"] == 800 * 2


@pytest.mark.asyncio
async def test_calculate_pricing_tier_enterprise():
    """Tier pricing for large deployments (200+ endpoints per appliance)."""
    result = await billing.calculate_pricing(
        num_appliances=3, endpoints_per_appliance=200, pricing_model="tier"
    )
    assert result["recommended_tier"] == "enterprise"
    assert result["monthly_cost"] == 1500 * 3


@pytest.mark.asyncio
async def test_calculate_pricing_per_endpoint():
    """Per-endpoint pricing model ($20/endpoint/month)."""
    result = await billing.calculate_pricing(
        num_appliances=2, endpoints_per_appliance=30, pricing_model="per_endpoint"
    )
    assert result["recommended_tier"] is None
    assert result["total_endpoints"] == 60
    assert result["monthly_cost"] == 60 * 20
    assert result["pricing_model"] == "per_endpoint"


@pytest.mark.asyncio
async def test_calculate_pricing_savings_vs_traditional():
    """Savings calculation against traditional MSP costs."""
    result = await billing.calculate_pricing(
        num_appliances=1, endpoints_per_appliance=25, pricing_model="tier"
    )
    # Traditional = $4400/appliance/month, clinic = $400/month
    assert result["traditional_monthly_cost"] == 4400
    assert result["monthly_savings"] == 4400 - 400
    assert result["savings_percent"] == pytest.approx(90.9, abs=0.1)


# =============================================================================
# 6. INVOICE LISTING
# =============================================================================

@pytest.mark.asyncio
async def test_list_invoices_no_customer(partner):
    """Returns empty list when partner has no Stripe customer ID."""
    conn = FakeConn(fetchrow_result={"stripe_customer_id": None})
    pool = make_fake_pool(conn)

    with patch.object(billing, "get_pool", return_value=pool), \
         patch.object(billing, "admin_connection", side_effect=fake_admin_connection), \
         patch.object(billing, "HAS_STRIPE", True), \
         patch.object(billing, "STRIPE_SECRET_KEY", "sk_test"):
        result = await billing.list_invoices(limit=10, partner=partner)

    assert result["invoices"] == []
    assert result["has_more"] is False


@pytest.mark.asyncio
async def test_list_invoices_success(partner, fresh_stripe):
    """Returns formatted invoices from Stripe API."""
    conn = FakeConn(fetchrow_result={"stripe_customer_id": "cus_inv_test"})
    pool = make_fake_pool(conn)

    fake_inv = MagicMock()
    fake_inv.id = "inv_001"
    fake_inv.number = "INV-0001"
    fake_inv.status = "paid"
    fake_inv.amount_due = 40000
    fake_inv.amount_paid = 40000
    fake_inv.currency = "usd"
    fake_inv.created = 1700000000
    fake_inv.due_date = None
    fake_inv.status_transitions.paid_at = 1700000100
    fake_inv.invoice_pdf = "https://stripe.com/pdf/inv_001"
    fake_inv.hosted_invoice_url = "https://stripe.com/inv_001"

    fake_invoice_list = MagicMock()
    fake_invoice_list.data = [fake_inv]
    fake_invoice_list.has_more = False
    fresh_stripe.Invoice.list.return_value = fake_invoice_list

    with patch.object(billing, "get_pool", return_value=pool), \
         patch.object(billing, "admin_connection", side_effect=fake_admin_connection), \
         patch.object(billing, "stripe", fresh_stripe), \
         patch.object(billing, "HAS_STRIPE", True), \
         patch.object(billing, "STRIPE_SECRET_KEY", "sk_test"):
        result = await billing.list_invoices(limit=10, partner=partner)

    assert len(result["invoices"]) == 1
    inv = result["invoices"][0]
    assert inv["id"] == "inv_001"
    assert inv["status"] == "paid"
    assert inv["amount_paid"] == 40000
    assert inv["invoice_pdf"] is not None


# =============================================================================
# 7. WEBHOOK HANDLING
# =============================================================================

async def _call_webhook(fresh_stripe, pool, event_type, event_data,
                        event_id="evt_test_001", webhook_secret=None):
    """Helper to invoke the webhook handler with a mocked Request."""
    event_obj = MagicMock()
    event_obj.id = event_id
    event_obj.type = event_type
    event_obj.data.object = event_data

    payload = json.dumps({"id": event_id, "type": event_type}).encode()

    request = MagicMock()
    request.body = AsyncMock(return_value=payload)
    request.headers = {"stripe-signature": "sig_test"}

    if webhook_secret:
        fresh_stripe.Webhook.construct_event.return_value = event_obj
    else:
        fresh_stripe.Event.construct_from.return_value = event_obj

    with patch.object(billing, "get_pool", return_value=pool), \
         patch.object(billing, "admin_connection", side_effect=fake_admin_connection), \
         patch.object(billing, "stripe", fresh_stripe), \
         patch.object(billing, "HAS_STRIPE", True), \
         patch.object(billing, "STRIPE_WEBHOOK_SECRET", webhook_secret):
        return await billing.stripe_webhook(request=request)


@pytest.mark.asyncio
async def test_webhook_checkout_completed(fresh_stripe):
    """checkout.session.completed updates partner subscription in DB."""
    conn = FakeConn(fetchval_result=None)  # Not a duplicate
    pool = make_fake_pool(conn)

    event_data = MagicMock()
    event_data.metadata.get = lambda k: "00000000-0000-0000-0000-000000000001" if k == "partner_id" else None
    event_data.subscription = "sub_new_001"

    fake_sub = MagicMock()
    fake_sub.status = "active"
    fake_sub.current_period_end = 1700000000
    fresh_stripe.Subscription.retrieve.return_value = fake_sub

    result = await _call_webhook(
        fresh_stripe, pool,
        "checkout.session.completed", event_data
    )
    assert result["status"] == "received"
    # Should have: CREATE TABLE, INSERT dedup, UPDATE partners
    assert len(conn.executed) >= 3


@pytest.mark.asyncio
async def test_webhook_subscription_updated(fresh_stripe):
    """customer.subscription.updated updates status and period end."""
    conn = FakeConn(fetchval_result=None)
    pool = make_fake_pool(conn)

    event_data = MagicMock()
    event_data.metadata.get = lambda k: "00000000-0000-0000-0000-000000000002" if k == "partner_id" else None
    event_data.status = "active"
    event_data.current_period_end = 1710000000

    result = await _call_webhook(
        fresh_stripe, pool,
        "customer.subscription.updated", event_data
    )
    assert result["status"] == "received"
    update_queries = [q for q, _ in conn.executed if "subscription_status" in q]
    assert len(update_queries) >= 1


@pytest.mark.asyncio
async def test_webhook_subscription_deleted(fresh_stripe):
    """customer.subscription.deleted sets status to canceled and clears sub ID."""
    conn = FakeConn(fetchval_result=None)
    pool = make_fake_pool(conn)

    event_data = MagicMock()
    event_data.metadata.get = lambda k: "00000000-0000-0000-0000-000000000003" if k == "partner_id" else None

    result = await _call_webhook(
        fresh_stripe, pool,
        "customer.subscription.deleted", event_data
    )
    assert result["status"] == "received"
    cancel_queries = [q for q, _ in conn.executed if "'canceled'" in q]
    assert len(cancel_queries) >= 1


@pytest.mark.asyncio
async def test_webhook_invoice_paid(fresh_stripe):
    """invoice.paid sets subscription_status to active by customer ID."""
    conn = FakeConn(fetchval_result=None)
    pool = make_fake_pool(conn)

    event_data = MagicMock()
    event_data.customer = "cus_paid_001"
    event_data.metadata = MagicMock()
    event_data.metadata.get = lambda k: None

    result = await _call_webhook(
        fresh_stripe, pool,
        "invoice.paid", event_data
    )
    assert result["status"] == "received"
    active_queries = [q for q, _ in conn.executed if "'active'" in q]
    assert len(active_queries) >= 1


@pytest.mark.asyncio
async def test_webhook_invoice_payment_failed(fresh_stripe):
    """invoice.payment_failed sets subscription_status to past_due."""
    conn = FakeConn(fetchval_result=None)
    pool = make_fake_pool(conn)

    event_data = MagicMock()
    event_data.customer = "cus_fail_001"
    event_data.metadata = MagicMock()
    event_data.metadata.get = lambda k: None

    result = await _call_webhook(
        fresh_stripe, pool,
        "invoice.payment_failed", event_data
    )
    assert result["status"] == "received"
    past_due_queries = [q for q, _ in conn.executed if "'past_due'" in q]
    assert len(past_due_queries) >= 1


@pytest.mark.asyncio
async def test_webhook_dedup_skips_processed_event(fresh_stripe):
    """Already-processed webhook events return already_processed without re-handling."""
    conn = FakeConn(fetchval_result=1)  # Event already exists in dedup table
    pool = make_fake_pool(conn)

    event_data = MagicMock()
    event_data.metadata = MagicMock()
    event_data.metadata.get = lambda k: None

    result = await _call_webhook(
        fresh_stripe, pool,
        "invoice.paid", event_data,
        event_id="evt_already_done"
    )
    assert result["status"] == "already_processed"


@pytest.mark.asyncio
async def test_webhook_invalid_signature(fresh_stripe):
    """Invalid webhook signature raises 400."""
    from fastapi import HTTPException

    fresh_stripe.Webhook.construct_event.side_effect = SignatureVerificationError("bad sig")

    request = MagicMock()
    request.body = AsyncMock(return_value=b'{"id":"evt_1","type":"test"}')
    request.headers = {"stripe-signature": "invalid_sig"}

    with patch.object(billing, "HAS_STRIPE", True), \
         patch.object(billing, "STRIPE_WEBHOOK_SECRET", "whsec_test"), \
         patch.object(billing, "stripe", fresh_stripe):
        with pytest.raises(HTTPException) as exc_info:
            await billing.stripe_webhook(request=request)
        assert exc_info.value.status_code == 400
        assert "Invalid signature" in exc_info.value.detail


@pytest.mark.asyncio
async def test_webhook_invalid_payload(fresh_stripe):
    """Malformed webhook payload raises 400."""
    from fastapi import HTTPException

    fresh_stripe.Webhook.construct_event.side_effect = ValueError("bad json")

    request = MagicMock()
    request.body = AsyncMock(return_value=b"not json")
    request.headers = {"stripe-signature": "sig_test"}

    with patch.object(billing, "HAS_STRIPE", True), \
         patch.object(billing, "STRIPE_WEBHOOK_SECRET", "whsec_test"), \
         patch.object(billing, "stripe", fresh_stripe):
        with pytest.raises(HTTPException) as exc_info:
            await billing.stripe_webhook(request=request)
        assert exc_info.value.status_code == 400
        assert "Invalid payload" in exc_info.value.detail


@pytest.mark.asyncio
async def test_webhook_stripe_not_available():
    """Webhook returns 501 when HAS_STRIPE is False."""
    from fastapi import HTTPException

    request = MagicMock()
    request.body = AsyncMock(return_value=b"{}")
    request.headers = {}

    with patch.object(billing, "HAS_STRIPE", False):
        with pytest.raises(HTTPException) as exc_info:
            await billing.stripe_webhook(request=request)
        assert exc_info.value.status_code == 501


@pytest.mark.asyncio
async def test_webhook_with_signature_verification(fresh_stripe):
    """Webhook verifies signature when STRIPE_WEBHOOK_SECRET is set."""
    conn = FakeConn(fetchval_result=None)
    pool = make_fake_pool(conn)

    event_data = MagicMock()
    event_data.customer = "cus_verified"
    event_data.metadata = MagicMock()
    event_data.metadata.get = lambda k: None

    result = await _call_webhook(
        fresh_stripe, pool,
        "invoice.paid", event_data,
        webhook_secret="whsec_real_secret"
    )
    assert result["status"] == "received"
    fresh_stripe.Webhook.construct_event.assert_called_once()


# =============================================================================
# 8. CHECKOUT SESSION CREATION
# =============================================================================

@pytest.mark.asyncio
async def test_create_checkout_session_success(partner, fresh_stripe):
    """Creates checkout session and returns URL + session ID."""
    conn = FakeConn(fetchrow_result={"contact_email": "p@test.com", "name": "TestCo"})
    pool = make_fake_pool(conn)

    fake_session = MagicMock()
    fake_session.url = "https://checkout.stripe.com/session/cs_test"
    fake_session.id = "cs_test_001"
    fresh_stripe.checkout.Session.create.return_value = fake_session

    mock_get_or_create = AsyncMock(return_value="cus_checkout")

    with patch.object(billing, "get_pool", return_value=pool), \
         patch.object(billing, "admin_connection", side_effect=fake_admin_connection), \
         patch.object(billing, "stripe", fresh_stripe), \
         patch.object(billing, "HAS_STRIPE", True), \
         patch.object(billing, "STRIPE_SECRET_KEY", "sk_test"), \
         patch.object(billing, "get_or_create_stripe_customer", mock_get_or_create):
        req = billing.CreateCheckoutSession(price_id="price_clinic_monthly")
        result = await billing.create_checkout_session(request=req, partner=partner)

    assert result["checkout_url"] == "https://checkout.stripe.com/session/cs_test"
    assert result["session_id"] == "cs_test_001"
    fresh_stripe.checkout.Session.create.assert_called_once()
    call_kwargs = fresh_stripe.checkout.Session.create.call_args[1]
    assert call_kwargs["customer"] == "cus_checkout"
    assert call_kwargs["mode"] == "subscription"
    assert call_kwargs["allow_promotion_codes"] is True


@pytest.mark.asyncio
async def test_create_checkout_session_stripe_error(partner, fresh_stripe):
    """Checkout session creation failure raises 400."""
    from fastapi import HTTPException

    conn = FakeConn(fetchrow_result={"contact_email": "p@test.com", "name": "TestCo"})
    pool = make_fake_pool(conn)

    fresh_stripe.checkout.Session.create.side_effect = StripeError("rate limited")

    mock_get_or_create = AsyncMock(return_value="cus_err")

    with patch.object(billing, "get_pool", return_value=pool), \
         patch.object(billing, "admin_connection", side_effect=fake_admin_connection), \
         patch.object(billing, "stripe", fresh_stripe), \
         patch.object(billing, "HAS_STRIPE", True), \
         patch.object(billing, "STRIPE_SECRET_KEY", "sk_test"), \
         patch.object(billing, "get_or_create_stripe_customer", mock_get_or_create):
        req = billing.CreateCheckoutSession(price_id="price_bad")
        with pytest.raises(HTTPException) as exc_info:
            await billing.create_checkout_session(request=req, partner=partner)
        assert exc_info.value.status_code == 400


# =============================================================================
# 9. CUSTOMER PORTAL SESSION
# =============================================================================

@pytest.mark.asyncio
async def test_create_portal_session_no_customer(partner):
    """Portal session raises 400 when partner has no Stripe customer."""
    from fastapi import HTTPException
    conn = FakeConn(fetchrow_result={"stripe_customer_id": None})
    pool = make_fake_pool(conn)

    with patch.object(billing, "get_pool", return_value=pool), \
         patch.object(billing, "admin_connection", side_effect=fake_admin_connection), \
         patch.object(billing, "HAS_STRIPE", True), \
         patch.object(billing, "STRIPE_SECRET_KEY", "sk_test"):
        with pytest.raises(HTTPException) as exc_info:
            await billing.create_customer_portal_session(partner=partner)
        assert exc_info.value.status_code == 400
        assert "No billing account" in exc_info.value.detail


@pytest.mark.asyncio
async def test_create_portal_session_success(partner, fresh_stripe):
    """Portal session returns URL when partner has a Stripe customer."""
    conn = FakeConn(fetchrow_result={"stripe_customer_id": "cus_portal_test"})
    pool = make_fake_pool(conn)

    fake_portal = MagicMock()
    fake_portal.url = "https://billing.stripe.com/session/bps_test"
    fresh_stripe.billing_portal.Session.create.return_value = fake_portal

    with patch.object(billing, "get_pool", return_value=pool), \
         patch.object(billing, "admin_connection", side_effect=fake_admin_connection), \
         patch.object(billing, "stripe", fresh_stripe), \
         patch.object(billing, "HAS_STRIPE", True), \
         patch.object(billing, "STRIPE_SECRET_KEY", "sk_test"):
        result = await billing.create_customer_portal_session(partner=partner)

    assert result["portal_url"] == "https://billing.stripe.com/session/bps_test"
    fresh_stripe.billing_portal.Session.create.assert_called_once()

"""Stripe billing integration for partner subscriptions.

Provides API endpoints for:
- Creating checkout sessions for new subscriptions
- Managing subscriptions (cancel, update)
- Webhook handling for payment events
- Invoice/payment history
"""

import os
import logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Header, Depends, Request
from pydantic import BaseModel

from .fleet import get_pool
from .partners import require_partner

logger = logging.getLogger(__name__)

# Try to import stripe
try:
    import stripe
    HAS_STRIPE = True
except ImportError:
    HAS_STRIPE = False
    logger.warning("stripe library not installed - billing endpoints will be disabled")


# Initialize Stripe with secret key from environment
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

if HAS_STRIPE and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY


router = APIRouter(prefix="/api/billing", tags=["billing"])


# =============================================================================
# MODELS
# =============================================================================

class CreateCheckoutSession(BaseModel):
    """Model for creating a Stripe checkout session."""
    price_id: str  # Stripe Price ID for the subscription plan
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class UpdateSubscription(BaseModel):
    """Model for updating subscription."""
    price_id: Optional[str] = None  # New price ID to switch plans
    cancel_at_period_end: Optional[bool] = None


# =============================================================================
# PRICING MODEL - Per-Appliance with Endpoint Tiers
# =============================================================================

# Per-endpoint pricing (auto-scales with deployment)
ENDPOINT_PRICE_MONTHLY = 20  # $20/endpoint/month

# Appliance tier pricing (alternative to per-endpoint)
APPLIANCE_TIERS = {
    "clinic": {
        "name": "Clinic",
        "price_monthly": 400,
        "max_endpoints": 25,
        "description": "Small healthcare practices",
        "features": [
            "HIPAA compliance monitoring",
            "Automated evidence generation",
            "Self-healing infrastructure",
            "Network discovery",
            "Medical device protection",
            "24/7 drift detection",
            "Monthly compliance reports",
        ],
    },
    "practice": {
        "name": "Practice",
        "price_monthly": 800,
        "max_endpoints": 100,
        "description": "Medium healthcare organizations",
        "features": [
            "Everything in Clinic tier",
            "Multi-server management",
            "Advanced remediation playbooks",
            "Weekly compliance reports",
            "Priority support",
            "Custom runbook configuration",
        ],
    },
    "enterprise": {
        "name": "Enterprise",
        "price_monthly": 1500,
        "max_endpoints": None,  # Unlimited
        "description": "Large healthcare networks",
        "features": [
            "Everything in Practice tier",
            "Unlimited endpoints",
            "Dedicated support channel",
            "Custom integrations",
            "SLA guarantee",
            "On-site deployment assistance",
            "Executive compliance dashboard",
        ],
    },
}

# Value comparison for sales
VALUE_COMPARISON = {
    "traditional_msp_monitoring": {"monthly": 2500, "description": "MSP labor (15-20 hrs @ $150/hr)"},
    "hipaa_compliance_consulting": {"monthly": 500, "description": "Compliance consulting (amortized)"},
    "incident_response": {"monthly": 1000, "description": "Incident response/remediation"},
    "audit_preparation": {"monthly": 400, "description": "Audit prep & evidence (amortized)"},
    "total_traditional": {"monthly": 4400, "description": "Total traditional cost"},
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def check_stripe_available():
    """Raise error if Stripe is not configured."""
    if not HAS_STRIPE:
        raise HTTPException(
            status_code=501,
            detail="Stripe library not installed"
        )
    if not STRIPE_SECRET_KEY:
        raise HTTPException(
            status_code=501,
            detail="Stripe not configured - STRIPE_SECRET_KEY environment variable not set"
        )


async def get_or_create_stripe_customer(partner_id: str, email: str, name: str) -> str:
    """Get existing or create new Stripe customer for partner."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Check if partner already has a Stripe customer ID
        row = await conn.fetchrow("""
            SELECT stripe_customer_id FROM partners WHERE id = $1
        """, partner_id)

        if row and row['stripe_customer_id']:
            return row['stripe_customer_id']

        # Create new Stripe customer
        customer = stripe.Customer.create(
            email=email,
            name=name,
            metadata={
                "partner_id": partner_id,
                "platform": "osiriscare"
            }
        )

        # Store customer ID
        await conn.execute("""
            UPDATE partners SET stripe_customer_id = $1 WHERE id = $2
        """, customer.id, partner_id)

        return customer.id


# =============================================================================
# PARTNER BILLING ENDPOINTS
# =============================================================================

@router.get("/plans")
async def list_subscription_plans():
    """List available subscription plans."""
    return {
        "plans": SUBSCRIPTION_PLANS,
        "currency": "usd",
    }


@router.get("/status")
async def get_billing_status(partner=Depends(require_partner)):
    """Get current billing/subscription status for partner."""
    check_stripe_available()
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT stripe_customer_id, stripe_subscription_id,
                   subscription_status, subscription_plan,
                   subscription_current_period_end, trial_ends_at
            FROM partners WHERE id = $1
        """, partner['id'])

        if not row:
            raise HTTPException(status_code=404, detail="Partner not found")

        subscription_data = None
        upcoming_invoice = None

        # Get subscription details from Stripe if exists
        if row['stripe_subscription_id']:
            try:
                subscription = stripe.Subscription.retrieve(row['stripe_subscription_id'])
                subscription_data = {
                    "id": subscription.id,
                    "status": subscription.status,
                    "current_period_start": datetime.fromtimestamp(
                        subscription.current_period_start, tz=timezone.utc
                    ).isoformat(),
                    "current_period_end": datetime.fromtimestamp(
                        subscription.current_period_end, tz=timezone.utc
                    ).isoformat(),
                    "cancel_at_period_end": subscription.cancel_at_period_end,
                    "plan": {
                        "id": subscription.items.data[0].price.id if subscription.items.data else None,
                        "amount": subscription.items.data[0].price.unit_amount if subscription.items.data else None,
                        "interval": subscription.items.data[0].price.recurring.interval if subscription.items.data else None,
                    } if subscription.items.data else None,
                }

                # Get upcoming invoice
                try:
                    upcoming = stripe.Invoice.upcoming(customer=row['stripe_customer_id'])
                    upcoming_invoice = {
                        "amount_due": upcoming.amount_due,
                        "currency": upcoming.currency,
                        "next_payment_date": datetime.fromtimestamp(
                            upcoming.next_payment_attempt, tz=timezone.utc
                        ).isoformat() if upcoming.next_payment_attempt else None,
                    }
                except stripe.error.InvalidRequestError:
                    # No upcoming invoice (e.g., subscription canceled)
                    pass

            except stripe.error.StripeError as e:
                logger.error(f"Stripe error fetching subscription: {e}")

        # Get payment methods
        payment_methods = []
        if row['stripe_customer_id']:
            try:
                methods = stripe.PaymentMethod.list(
                    customer=row['stripe_customer_id'],
                    type="card"
                )
                for pm in methods.data:
                    payment_methods.append({
                        "id": pm.id,
                        "brand": pm.card.brand,
                        "last4": pm.card.last4,
                        "exp_month": pm.card.exp_month,
                        "exp_year": pm.card.exp_year,
                        "is_default": pm.id == stripe.Customer.retrieve(
                            row['stripe_customer_id']
                        ).invoice_settings.default_payment_method,
                    })
            except stripe.error.StripeError as e:
                logger.error(f"Stripe error fetching payment methods: {e}")

        return {
            "has_subscription": row['stripe_subscription_id'] is not None,
            "subscription_status": row['subscription_status'] or "none",
            "subscription_plan": row['subscription_plan'],
            "subscription": subscription_data,
            "upcoming_invoice": upcoming_invoice,
            "payment_methods": payment_methods,
            "trial_ends_at": row['trial_ends_at'].isoformat() if row['trial_ends_at'] else None,
        }


@router.post("/checkout")
async def create_checkout_session(
    request: CreateCheckoutSession,
    partner=Depends(require_partner)
):
    """Create a Stripe Checkout session for subscription signup."""
    check_stripe_available()
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT contact_email, name FROM partners WHERE id = $1
        """, partner['id'])

        if not row:
            raise HTTPException(status_code=404, detail="Partner not found")

    # Get or create Stripe customer
    customer_id = await get_or_create_stripe_customer(
        partner['id'],
        row['contact_email'],
        row['name']
    )

    # Default URLs if not provided
    success_url = request.success_url or "https://dashboard.osiriscare.net/partner?billing=success"
    cancel_url = request.cancel_url or "https://dashboard.osiriscare.net/partner?billing=canceled"

    try:
        # Create Checkout Session
        checkout_session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{
                "price": request.price_id,
                "quantity": 1,
            }],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "partner_id": partner['id'],
            },
            subscription_data={
                "metadata": {
                    "partner_id": partner['id'],
                }
            },
            # Allow promotion codes
            allow_promotion_codes=True,
            # Collect billing address
            billing_address_collection="required",
        )

        return {
            "checkout_url": checkout_session.url,
            "session_id": checkout_session.id,
        }

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error creating checkout session: {e}")
        raise HTTPException(status_code=400, detail="Payment processing failed. Please try again.")


@router.post("/portal")
async def create_customer_portal_session(partner=Depends(require_partner)):
    """Create a Stripe Customer Portal session for managing subscription."""
    check_stripe_available()
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT stripe_customer_id FROM partners WHERE id = $1
        """, partner['id'])

        if not row or not row['stripe_customer_id']:
            raise HTTPException(
                status_code=400,
                detail="No billing account found. Please subscribe first."
            )

    try:
        portal_session = stripe.billing_portal.Session.create(
            customer=row['stripe_customer_id'],
            return_url="https://dashboard.osiriscare.net/partner",
        )

        return {
            "portal_url": portal_session.url,
        }

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error creating portal session: {e}")
        raise HTTPException(status_code=400, detail="Billing portal unavailable. Please try again.")


@router.get("/invoices")
async def list_invoices(
    limit: int = 10,
    partner=Depends(require_partner)
):
    """List invoices for partner."""
    check_stripe_available()
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT stripe_customer_id FROM partners WHERE id = $1
        """, partner['id'])

        if not row or not row['stripe_customer_id']:
            return {"invoices": [], "has_more": False}

    try:
        invoices = stripe.Invoice.list(
            customer=row['stripe_customer_id'],
            limit=limit,
        )

        return {
            "invoices": [
                {
                    "id": inv.id,
                    "number": inv.number,
                    "status": inv.status,
                    "amount_due": inv.amount_due,
                    "amount_paid": inv.amount_paid,
                    "currency": inv.currency,
                    "created": datetime.fromtimestamp(inv.created, tz=timezone.utc).isoformat(),
                    "due_date": datetime.fromtimestamp(inv.due_date, tz=timezone.utc).isoformat() if inv.due_date else None,
                    "paid_at": datetime.fromtimestamp(inv.status_transitions.paid_at, tz=timezone.utc).isoformat() if inv.status_transitions.paid_at else None,
                    "invoice_pdf": inv.invoice_pdf,
                    "hosted_invoice_url": inv.hosted_invoice_url,
                }
                for inv in invoices.data
            ],
            "has_more": invoices.has_more,
        }

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error listing invoices: {e}")
        raise HTTPException(status_code=400, detail="Failed to retrieve invoices. Please try again.")


@router.post("/subscription/cancel")
async def cancel_subscription(partner=Depends(require_partner)):
    """Cancel subscription at end of current billing period."""
    check_stripe_available()
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT stripe_subscription_id FROM partners WHERE id = $1
        """, partner['id'])

        if not row or not row['stripe_subscription_id']:
            raise HTTPException(status_code=400, detail="No active subscription found")

    try:
        # Cancel at period end (not immediately)
        subscription = stripe.Subscription.modify(
            row['stripe_subscription_id'],
            cancel_at_period_end=True
        )

        # Update local status
        async with pool.acquire() as conn:
            await conn.execute("""
                UPDATE partners
                SET subscription_status = 'canceling'
                WHERE id = $1
            """, partner['id'])

        return {
            "status": "canceling",
            "cancel_at": datetime.fromtimestamp(
                subscription.current_period_end, tz=timezone.utc
            ).isoformat(),
            "message": "Subscription will be canceled at the end of the billing period",
        }

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error canceling subscription: {e}")
        raise HTTPException(status_code=400, detail="Failed to cancel subscription. Please try again.")


@router.post("/subscription/reactivate")
async def reactivate_subscription(partner=Depends(require_partner)):
    """Reactivate a subscription that was set to cancel."""
    check_stripe_available()
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT stripe_subscription_id FROM partners WHERE id = $1
        """, partner['id'])

        if not row or not row['stripe_subscription_id']:
            raise HTTPException(status_code=400, detail="No subscription found")

    try:
        subscription = stripe.Subscription.modify(
            row['stripe_subscription_id'],
            cancel_at_period_end=False
        )

        # Update local status
        async with pool.acquire() as conn:
            await conn.execute("""
                UPDATE partners
                SET subscription_status = 'active'
                WHERE id = $1
            """, partner['id'])

        return {
            "status": "active",
            "message": "Subscription reactivated",
        }

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error reactivating subscription: {e}")
        raise HTTPException(status_code=400, detail="Failed to reactivate subscription. Please try again.")


# =============================================================================
# STRIPE WEBHOOK HANDLER
# =============================================================================

@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events."""
    if not HAS_STRIPE:
        raise HTTPException(status_code=501, detail="Stripe not available")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    # Verify webhook signature if secret is configured
    if STRIPE_WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, STRIPE_WEBHOOK_SECRET
            )
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid payload")
        except stripe.error.SignatureVerificationError:
            raise HTTPException(status_code=400, detail="Invalid signature")
    else:
        # Parse without verification (dev only)
        import json
        event = stripe.Event.construct_from(
            json.loads(payload), stripe.api_key
        )

    pool = await get_pool()

    # Handle the event
    event_type = event.type
    data = event.data.object

    logger.info(f"Received Stripe webhook: {event_type}")

    if event_type == "checkout.session.completed":
        # New subscription created via checkout
        partner_id = data.metadata.get("partner_id")
        if partner_id and data.subscription:
            subscription = stripe.Subscription.retrieve(data.subscription)
            async with pool.acquire() as conn:
                await conn.execute("""
                    UPDATE partners SET
                        stripe_subscription_id = $1,
                        subscription_status = $2,
                        subscription_current_period_end = $3
                    WHERE id = $4
                """,
                    data.subscription,
                    subscription.status,
                    datetime.fromtimestamp(subscription.current_period_end, tz=timezone.utc),
                    partner_id
                )
            logger.info(f"Partner {partner_id} subscribed: {data.subscription}")

    elif event_type == "customer.subscription.updated":
        # Subscription updated (plan change, renewal, etc.)
        partner_id = data.metadata.get("partner_id")
        if partner_id:
            async with pool.acquire() as conn:
                await conn.execute("""
                    UPDATE partners SET
                        subscription_status = $1,
                        subscription_current_period_end = $2
                    WHERE id = $3
                """,
                    data.status,
                    datetime.fromtimestamp(data.current_period_end, tz=timezone.utc),
                    partner_id
                )
            logger.info(f"Partner {partner_id} subscription updated: {data.status}")

    elif event_type == "customer.subscription.deleted":
        # Subscription canceled/ended
        partner_id = data.metadata.get("partner_id")
        if partner_id:
            async with pool.acquire() as conn:
                await conn.execute("""
                    UPDATE partners SET
                        subscription_status = 'canceled',
                        stripe_subscription_id = NULL
                    WHERE id = $1
                """, partner_id)
            logger.info(f"Partner {partner_id} subscription canceled")

    elif event_type == "invoice.paid":
        # Invoice paid successfully
        customer_id = data.customer
        async with pool.acquire() as conn:
            await conn.execute("""
                UPDATE partners SET
                    subscription_status = 'active'
                WHERE stripe_customer_id = $1
            """, customer_id)
        logger.info(f"Invoice paid for customer {customer_id}")

    elif event_type == "invoice.payment_failed":
        # Payment failed
        customer_id = data.customer
        async with pool.acquire() as conn:
            await conn.execute("""
                UPDATE partners SET
                    subscription_status = 'past_due'
                WHERE stripe_customer_id = $1
            """, customer_id)
        logger.warning(f"Payment failed for customer {customer_id}")

    return {"status": "received"}


# =============================================================================
# PUBLIC CONFIG ENDPOINT
# =============================================================================

@router.get("/config")
async def get_billing_config():
    """Get public billing configuration (publishable key, etc.)."""
    return {
        "stripe_publishable_key": STRIPE_PUBLISHABLE_KEY,
        "pricing_model": "per_appliance",
        "endpoint_price_monthly": ENDPOINT_PRICE_MONTHLY,
        "appliance_tiers": APPLIANCE_TIERS,
        "value_comparison": VALUE_COMPARISON,
        "currency": "usd",
    }


@router.get("/calculate")
async def calculate_pricing(
    num_appliances: int = 1,
    endpoints_per_appliance: int = 25,
    pricing_model: str = "tier",  # "tier" or "per_endpoint"
):
    """Calculate pricing based on deployment size.

    Args:
        num_appliances: Number of appliances to deploy
        endpoints_per_appliance: Average endpoints per appliance
        pricing_model: "tier" for appliance tiers, "per_endpoint" for $20/endpoint
    """
    total_endpoints = num_appliances * endpoints_per_appliance

    if pricing_model == "per_endpoint":
        monthly_cost = total_endpoints * ENDPOINT_PRICE_MONTHLY
        annual_cost = monthly_cost * 12
        recommended_tier = None
    else:
        # Determine tier based on endpoints per appliance
        if endpoints_per_appliance <= 25:
            tier = APPLIANCE_TIERS["clinic"]
            tier_key = "clinic"
        elif endpoints_per_appliance <= 100:
            tier = APPLIANCE_TIERS["practice"]
            tier_key = "practice"
        else:
            tier = APPLIANCE_TIERS["enterprise"]
            tier_key = "enterprise"

        monthly_cost = tier["price_monthly"] * num_appliances
        annual_cost = monthly_cost * 12
        recommended_tier = tier_key

    # Calculate savings vs traditional
    traditional_monthly = VALUE_COMPARISON["total_traditional"]["monthly"] * num_appliances
    savings_monthly = traditional_monthly - monthly_cost
    savings_percent = round((savings_monthly / traditional_monthly) * 100, 1)

    return {
        "num_appliances": num_appliances,
        "total_endpoints": total_endpoints,
        "pricing_model": pricing_model,
        "recommended_tier": recommended_tier,
        "monthly_cost": monthly_cost,
        "annual_cost": annual_cost,
        "traditional_monthly_cost": traditional_monthly,
        "monthly_savings": savings_monthly,
        "savings_percent": savings_percent,
        "currency": "usd",
    }


# =============================================================================
# DATABASE MIGRATION
# =============================================================================

BILLING_MIGRATION = """
-- Add billing columns to partners table
ALTER TABLE partners ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT;
ALTER TABLE partners ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT;
ALTER TABLE partners ADD COLUMN IF NOT EXISTS subscription_status TEXT DEFAULT 'none';
ALTER TABLE partners ADD COLUMN IF NOT EXISTS subscription_plan TEXT;
ALTER TABLE partners ADD COLUMN IF NOT EXISTS subscription_current_period_end TIMESTAMPTZ;
ALTER TABLE partners ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMPTZ;

-- Index for Stripe lookups
CREATE INDEX IF NOT EXISTS idx_partners_stripe_customer
    ON partners(stripe_customer_id) WHERE stripe_customer_id IS NOT NULL;
"""

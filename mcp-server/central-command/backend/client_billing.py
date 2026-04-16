"""Client billing — post-purchase self-serve.

Companion to client_signup.py. client_signup handles the PRE-purchase
funnel (/signup → BAA → Checkout). This module handles what a logged-in
client sees AFTER they've paid:
  - GET  /api/billing/client/status   — current subscription summary
  - POST /api/billing/client/portal   — spawn Stripe Customer Portal
                                        session, return hosted URL

Lookup strategy: client_users.email → Stripe customer via
stripe.Customer.list(email=…, limit=1). Avoids a schema migration to
link client_users → stripe_customers. Query is one API call per
invocation; acceptable for a human-viewed billing page.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .client_portal import require_client_user

logger = logging.getLogger("client_billing")

router = APIRouter(prefix="/api/billing/client", tags=["billing-client"])

try:
    import stripe
    HAS_STRIPE = True
except ImportError:
    HAS_STRIPE = False
    stripe = None  # type: ignore

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
if HAS_STRIPE and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY


def _check_stripe() -> None:
    if not HAS_STRIPE:
        raise HTTPException(status_code=501, detail="stripe library unavailable")
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="STRIPE_SECRET_KEY not configured")


def _find_customer(email: str):
    try:
        customers = stripe.Customer.list(email=email, limit=1)
    except Exception as e:
        logger.error("stripe customer lookup failed email=%s err=%s", email, e, exc_info=True)
        raise HTTPException(status_code=502, detail=f"stripe lookup failed: {e}")
    return customers.data[0] if customers.data else None


class PortalRequest(BaseModel):
    return_url: str = Field(..., min_length=1, max_length=500)


@router.get("/status")
async def client_billing_status(user: dict = Depends(require_client_user)) -> Dict[str, Any]:
    """Return the logged-in client's subscription summary.

    Response shape (null when no subscription on file):
      customer_id: str | None
      subscription: { id, status, plan, current_period_end, cancel_at_period_end, trial_end } | None
    """
    _check_stripe()

    email = user.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="client user has no email on file")

    customer = _find_customer(email)
    if not customer:
        return {"customer_id": None, "subscription": None}

    try:
        subs = stripe.Subscription.list(customer=customer.id, status="all", limit=10)
    except Exception as e:
        logger.error("stripe sub list failed customer=%s err=%s", customer.id, e, exc_info=True)
        raise HTTPException(status_code=502, detail=f"stripe sub list failed: {e}")

    # Prefer active/trialing/past_due/unpaid; fall back to latest by created
    order = {"trialing": 0, "active": 1, "past_due": 2, "unpaid": 3, "incomplete": 4, "canceled": 5}
    if not subs.data:
        return {"customer_id": customer.id, "subscription": None}
    sub = sorted(subs.data, key=lambda s: (order.get(s.status, 9), -(s.created or 0)))[0]
    item = sub["items"]["data"][0] if sub["items"]["data"] else None
    plan_name = None
    if item and item.get("price"):
        lookup = item["price"].get("lookup_key") or ""
        plan_name = lookup.replace("osiris-", "").replace("-monthly", "").replace("-onetime", "")

    return {
        "customer_id": customer.id,
        "subscription": {
            "id": sub.id,
            "status": sub.status,
            "plan": plan_name,
            "current_period_end": sub.current_period_end,
            "cancel_at_period_end": sub.cancel_at_period_end,
            "trial_end": sub.trial_end,
        },
    }


@router.post("/portal")
async def client_billing_portal(
    req: PortalRequest,
    user: dict = Depends(require_client_user),
) -> Dict[str, Any]:
    """Create a Stripe-hosted Customer Portal session.

    The Portal lets the customer self-serve:
      - Update card on file
      - Download invoices
      - Cancel / reactivate subscription
      - See payment history

    Return URL must be whitelisted in the Stripe Dashboard → Settings →
    Customer Portal. Default OsirisCare UI whitelist: app.osiriscare.net/*.
    """
    _check_stripe()

    email = user.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="client user has no email on file")

    customer = _find_customer(email)
    if not customer:
        raise HTTPException(
            status_code=404,
            detail="No Stripe customer on file. If you recently signed up, "
                   "wait a minute for the payment webhook to complete.",
        )

    try:
        session = stripe.billing_portal.Session.create(
            customer=customer.id,
            return_url=req.return_url,
        )
    except Exception as e:
        logger.error("stripe portal create failed customer=%s err=%s", customer.id, e, exc_info=True)
        raise HTTPException(status_code=502, detail=f"stripe portal create failed: {e}")

    logger.info("client_portal_session email=%s customer=%s session=%s",
                email, customer.id, session.id)
    return {"portal_url": session.url, "portal_session_id": session.id}

"""Client self-serve signup — Session 207 PM-consensus client billing path.

The direct-customer (healthcare SMB) conversion funnel. Three-step flow:

    /signup             → POST /api/billing/signup/start
    /signup/baa         → POST /api/billing/signup/sign-baa
    /signup/checkout    → POST /api/billing/signup/checkout
    (Stripe-hosted Checkout)
    Stripe webhook      → checkout.session.completed → subscription provisioned
    /signup/complete    → GET  /api/billing/signup/session/{signup_id}

Why NOT use billing.py's partner flow:
  - That module is scoped to require_partner and writes to partners.
  - This flow is public (no auth until Stripe returns), writes to
    subscriptions via webhook, and carries a BAA e-sign gate.

PHI boundary — enforced here:
  - No patient_*, provider_npi, diagnosis_*, treatment_* data touches
    Stripe customer.metadata (CHECK constraint on subscriptions table
    + strict whitelist in this module).
  - BAA is NOT signed with Stripe — billing is scoped to be PHI-free
    so Stripe never becomes a Business Associate.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field, field_validator

from .fleet import get_pool
from .tenant_middleware import admin_connection

try:
    from .shared import check_rate_limit
except ImportError:  # pytest-safe (mirrors auth.py / evidence_chain.py pattern)
    from shared import check_rate_limit  # type: ignore[no-redef]

logger = logging.getLogger("client_signup")


def _client_ip(request: Request) -> str:
    """Best-effort client IP. Behind Caddy (trusted proxy), use X-Forwarded-For
    first entry. Fall back to request.client.host. Used as the rate-limit key
    on the pre-session signup endpoints — not for anything security-critical."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

router = APIRouter(prefix="/api/billing/signup", tags=["billing-signup"])


# ─── Stripe guards ────────────────────────────────────────────────

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


# ─── Plan catalog (source of truth for /signup) ──────────────────
# Keyed by `plan` enum; lookup_key matches the Stripe Price lookup_key
# set when products were created. That way a price-id rotation in
# Stripe doesn't require a code change.

PLAN_CATALOG: Dict[str, Dict[str, Any]] = {
    "pilot": {
        "lookup_key": "osiris-pilot-onetime",
        "mode": "payment",            # Stripe Checkout mode — one-time
        "display_name": "90-Day Pilot",
        "description": "Full Essentials access for 90 days. No auto-conversion.",
        "amount_cents": 29900,
    },
    "essentials": {
        "lookup_key": "osiris-essentials-monthly",
        "mode": "subscription",
        "display_name": "Essentials",
        "description": "59 compliance checks, L1 auto-healing.",
        "amount_cents": 49900,
    },
    "professional": {
        "lookup_key": "osiris-professional-monthly",
        "mode": "subscription",
        "display_name": "Professional",
        "description": "+ L2 LLM healing, full runbooks, peer-witnessed evidence.",
        "amount_cents": 79900,
    },
    "enterprise": {
        "lookup_key": "osiris-enterprise-monthly",
        "mode": "subscription",
        "display_name": "Enterprise",
        "description": "+ dedicated L3, 4hr SLA, custom runbooks.",
        "amount_cents": 129900,
    },
}

# BAA version pinned in code so each signature records which text
# was agreed to. Bump whenever the BAA is materially updated; old
# signatures remain bound to their original hash.
BAA_VERSION = "v1.0-2026-04-15"


# ─── Models ───────────────────────────────────────────────────────

class SignupStart(BaseModel):
    email: EmailStr
    practice_name: str = Field(..., min_length=1, max_length=255)
    billing_contact_name: str = Field(..., min_length=1, max_length=255)
    state: Optional[str] = Field(None, pattern=r"^[A-Z]{2}$")  # US state code
    plan: str

    @field_validator("plan")
    @classmethod
    def _plan_in_catalog(cls, v: str) -> str:
        if v not in PLAN_CATALOG:
            raise ValueError(f"plan must be one of {sorted(PLAN_CATALOG.keys())}")
        return v


class SignupBaaSign(BaseModel):
    signup_id: str = Field(..., min_length=1, max_length=64)
    signer_name: str = Field(..., min_length=1, max_length=255)
    baa_text_sha256: str = Field(..., pattern=r"^[a-f0-9]{64}$")


class SignupCheckout(BaseModel):
    signup_id: str = Field(..., min_length=1, max_length=64)
    success_url: str
    cancel_url: str


# ─── Routes ────────────────────────────────────────────────────────

@router.post("/start")
async def start_signup(req: SignupStart, request: Request) -> Dict[str, Any]:
    """Step 1: create signup session + Stripe customer.

    Customer metadata is deliberately narrow — email + name +
    practice_name + signup_id. No PHI, no patient-linked identifiers,
    no provider NPIs. This is the technical enforcement of the
    "design so we never need a BAA with Stripe" posture.
    """
    _check_stripe()

    # CSRF is intentionally not enforced on signup/* (pre-session). Abuse
    # prevention for /start is IP-based — Stripe customer creation + a row
    # in signup_sessions is cheap but not free. 5 req/hour/IP is well past
    # "fat-finger the form twice" and well below "script a thousand new
    # Stripe customers per hour."
    ip = _client_ip(request)
    allowed, retry_after = await check_rate_limit(
        f"ip:{ip}", "signup_start", window_seconds=3600, max_requests=5,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many signup attempts. Try again later.",
            headers={"Retry-After": str(retry_after or 3600)},
        )

    signup_id = str(uuid.uuid4())

    # Create Stripe customer FIRST so we can attach the id to the session.
    # If this fails, no session row is written — clean rollback.
    try:
        customer = stripe.Customer.create(
            email=req.email,
            name=req.billing_contact_name,
            metadata={
                "practice_name": req.practice_name,
                "signup_id": signup_id,
                "plan": req.plan,
                "state": req.state or "",
            },
        )
    except Exception as e:
        logger.error("stripe customer create failed email=%s err=%s", req.email, e, exc_info=True)
        raise HTTPException(status_code=502, detail=f"stripe customer create failed: {e}")

    pool = await get_pool()
    async with admin_connection(pool) as conn, conn.transaction():
        await conn.execute(
            """
            INSERT INTO signup_sessions
                (signup_id, email, practice_name, billing_contact_name,
                 state, plan, stripe_customer_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            signup_id, req.email, req.practice_name, req.billing_contact_name,
            req.state, req.plan, customer.id,
        )

    logger.info(
        "signup_started signup_id=%s email=%s plan=%s customer=%s",
        signup_id, req.email, req.plan, customer.id,
    )
    return {
        "signup_id": signup_id,
        "email": req.email,
        "plan": req.plan,
        "plan_details": PLAN_CATALOG[req.plan],
        "baa_version": BAA_VERSION,
        "next": "/signup/baa",
    }


@router.post("/sign-baa")
async def sign_baa(req: SignupBaaSign, request: Request) -> Dict[str, Any]:
    """Step 2: record BAA e-signature.

    Gates the later checkout step — no checkout without a signed BAA.
    Signature record includes: typed name, IP, UA, BAA version, SHA256
    of the text shown to them. If the BAA text ever changes, old
    signatures remain bound to their original hash.
    """
    # Key by signup_id — prevents re-sign churn from a single session.
    allowed, retry_after = await check_rate_limit(
        f"signup:{req.signup_id}", "signup_baa", window_seconds=3600, max_requests=10,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many BAA attempts for this session.",
            headers={"Retry-After": str(retry_after or 3600)},
        )

    pool = await get_pool()
    async with admin_connection(pool) as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT email, stripe_customer_id, plan, expires_at, completed_at "
            "FROM signup_sessions WHERE signup_id = $1 FOR UPDATE",
            req.signup_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="signup_id not found")
        if row["completed_at"]:
            raise HTTPException(status_code=409, detail="signup already completed")
        if row["expires_at"] < datetime.now(timezone.utc):
            raise HTTPException(status_code=410, detail="signup session expired")

        signature_id = str(uuid.uuid4())
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent", "")[:500]

        await conn.execute(
            """
            INSERT INTO baa_signatures
                (signature_id, email, stripe_customer_id, signer_name,
                 signer_ip, signer_user_agent, baa_version, baa_text_sha256,
                 metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
            """,
            signature_id, row["email"], row["stripe_customer_id"],
            req.signer_name, client_ip, user_agent,
            BAA_VERSION, req.baa_text_sha256,
            json.dumps({"signup_id": req.signup_id, "plan": row["plan"]}),
        )
        await conn.execute(
            "UPDATE signup_sessions "
            "   SET baa_signature_id = $1, baa_signed_at = NOW() "
            " WHERE signup_id = $2",
            signature_id, req.signup_id,
        )

    logger.info(
        "baa_signed signup_id=%s signature_id=%s signer=%s ip=%s",
        req.signup_id, signature_id, req.signer_name, client_ip,
    )
    return {
        "signature_id": signature_id,
        "signed_at": datetime.now(timezone.utc).isoformat(),
        "baa_version": BAA_VERSION,
        "next": "/signup/checkout",
    }


@router.post("/checkout")
async def create_checkout(req: SignupCheckout, request: Request) -> Dict[str, Any]:
    """Step 3: create Stripe Checkout session.

    Gates:
      - signup session must exist, not expired, not completed
      - BAA must be signed
    Returns the Stripe-hosted Checkout URL.
    """
    _check_stripe()

    allowed, retry_after = await check_rate_limit(
        f"signup:{req.signup_id}", "signup_checkout", window_seconds=3600, max_requests=10,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many checkout attempts for this session.",
            headers={"Retry-After": str(retry_after or 3600)},
        )

    pool = await get_pool()
    async with admin_connection(pool) as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT email, stripe_customer_id, plan, baa_signed_at, "
            "       completed_at, expires_at "
            "  FROM signup_sessions WHERE signup_id = $1 FOR UPDATE",
            req.signup_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="signup_id not found")
        if row["completed_at"]:
            raise HTTPException(status_code=409, detail="signup already completed")
        if row["expires_at"] < datetime.now(timezone.utc):
            raise HTTPException(status_code=410, detail="signup session expired")
        if not row["baa_signed_at"]:
            raise HTTPException(
                status_code=403,
                detail="BAA must be signed before checkout (chain-of-custody)",
            )

        plan = row["plan"]
        plan_cfg = PLAN_CATALOG[plan]

        # Look up the Price by its lookup_key so we're resilient to
        # Stripe price-id rotations (e.g., if someone archives a price
        # and creates a new one with the same lookup_key).
        try:
            prices = stripe.Price.list(lookup_keys=[plan_cfg["lookup_key"]], limit=1)
            if not prices.data:
                raise HTTPException(
                    status_code=500,
                    detail=f"Stripe price not found for lookup_key={plan_cfg['lookup_key']}",
                )
            price = prices.data[0]
        except HTTPException:
            raise
        except Exception as e:
            logger.error("stripe price lookup failed plan=%s err=%s", plan, e, exc_info=True)
            raise HTTPException(status_code=502, detail=f"stripe price lookup failed: {e}")

        # Create Checkout session. metadata.signup_id is what the webhook
        # handler uses to correlate checkout.session.completed back to
        # the signup_sessions row. mode differs: one-time (pilot) vs
        # subscription (essentials/professional/enterprise).
        try:
            session = stripe.checkout.Session.create(
                mode=plan_cfg["mode"],
                customer=row["stripe_customer_id"],
                line_items=[{"price": price.id, "quantity": 1}],
                success_url=req.success_url + "?session_id={CHECKOUT_SESSION_ID}",
                cancel_url=req.cancel_url,
                metadata={"signup_id": req.signup_id, "plan": plan},
                # Include ACH via Link/us_bank_account where available.
                # Stripe auto-populates the methods allowed for the
                # destination account; this hint nudges the UI toward ACH.
                payment_method_types=["card", "us_bank_account"]
                    if plan_cfg["mode"] == "subscription" else ["card"],
            )
        except Exception as e:
            logger.error(
                "stripe checkout create failed signup_id=%s plan=%s err=%s",
                req.signup_id, plan, e, exc_info=True,
            )
            raise HTTPException(status_code=502, detail=f"stripe checkout create failed: {e}")

        await conn.execute(
            "UPDATE signup_sessions SET checkout_session_id = $1 "
            "WHERE signup_id = $2",
            session.id, req.signup_id,
        )

    logger.info(
        "checkout_created signup_id=%s checkout_session=%s plan=%s",
        req.signup_id, session.id, plan,
    )
    return {
        "checkout_url": session.url,
        "checkout_session_id": session.id,
    }


@router.get("/session/{signup_id}")
async def get_session(signup_id: str) -> Dict[str, Any]:
    """Page-rehydration endpoint. Returns the current state so the
    frontend can re-enter at the right step on refresh."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        row = await conn.fetchrow(
            "SELECT signup_id, email, practice_name, plan, baa_signed_at, "
            "       checkout_session_id, completed_at, expires_at "
            "  FROM signup_sessions WHERE signup_id = $1",
            signup_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="signup_id not found")

    return {
        "signup_id": row["signup_id"],
        "email": row["email"],
        "practice_name": row["practice_name"],
        "plan": row["plan"],
        "plan_details": PLAN_CATALOG.get(row["plan"]),
        "baa_signed_at": row["baa_signed_at"].isoformat() if row["baa_signed_at"] else None,
        "checkout_session_id": row["checkout_session_id"],
        "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
        "expired": row["expires_at"] < datetime.now(timezone.utc),
        "baa_version": BAA_VERSION,
    }


# ─── Webhook handler hook ─────────────────────────────────────────
# Called by the shared /api/billing/webhook dispatcher when a
# checkout.session.completed event's metadata.signup_id matches a
# signup_sessions row. Separated out so the dispatch lives in one
# place but the signup-specific side-effects live here.

async def handle_checkout_completed_for_signup(event_data: Dict[str, Any]) -> None:
    """On checkout.session.completed for a signup flow:
      - mark signup_sessions.completed_at
      - upsert subscriptions row
      - trigger site provisioning (TODO — wiring depends on existing
        site-creation flow; for now we log and let ops take over)
    """
    session = event_data.get("object", {})
    metadata = session.get("metadata") or {}
    signup_id = metadata.get("signup_id")
    if not signup_id:
        return  # Not a signup — must be partner flow

    subscription_id = session.get("subscription")   # null for one-time (pilot)
    customer_id = session.get("customer")
    mode = session.get("mode")
    plan = metadata.get("plan", "")

    pool = await get_pool()
    async with admin_connection(pool) as conn, conn.transaction():
        # Mark signup completed (idempotent).
        await conn.execute(
            "UPDATE signup_sessions SET completed_at = COALESCE(completed_at, NOW()) "
            "WHERE signup_id = $1",
            signup_id,
        )

        if mode == "subscription" and subscription_id:
            # Pull the freshest state from Stripe so we don't drift.
            try:
                sub = stripe.Subscription.retrieve(subscription_id)
            except Exception as e:
                logger.error(
                    "stripe sub retrieve failed signup_id=%s sub=%s err=%s",
                    signup_id, subscription_id, e, exc_info=True,
                )
                return

            await conn.execute(
                """
                INSERT INTO subscriptions
                    (stripe_subscription_id, stripe_customer_id, plan, status,
                     trial_end, current_period_start, current_period_end,
                     cancel_at_period_end, billing_mode)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'card')
                ON CONFLICT (stripe_subscription_id) DO UPDATE SET
                    status                = EXCLUDED.status,
                    trial_end             = EXCLUDED.trial_end,
                    current_period_start  = EXCLUDED.current_period_start,
                    current_period_end    = EXCLUDED.current_period_end,
                    cancel_at_period_end  = EXCLUDED.cancel_at_period_end,
                    updated_at            = NOW()
                """,
                sub.id, customer_id, plan, sub.status,
                datetime.fromtimestamp(sub.trial_end, tz=timezone.utc) if sub.trial_end else None,
                datetime.fromtimestamp(sub.current_period_start, tz=timezone.utc),
                datetime.fromtimestamp(sub.current_period_end, tz=timezone.utc),
                sub.cancel_at_period_end,
            )
        elif mode == "payment":
            # One-time (pilot) — write a pseudo-subscription row with a
            # synthetic 90-day trial_end so the rest of the system can
            # treat it like a subscription.
            now = datetime.now(timezone.utc)
            from datetime import timedelta
            pilot_end = now + timedelta(days=90)
            await conn.execute(
                """
                INSERT INTO subscriptions
                    (stripe_subscription_id, stripe_customer_id, plan, status,
                     trial_end, current_period_start, current_period_end,
                     cancel_at_period_end, billing_mode)
                VALUES ($1, $2, $3, 'trialing', $4, $5, $4, false, 'card')
                ON CONFLICT (stripe_subscription_id) DO NOTHING
                """,
                f"pilot_{session.get('id', signup_id)}", customer_id, plan,
                pilot_end, now,
            )

    logger.info(
        "signup_completed signup_id=%s customer=%s plan=%s mode=%s",
        signup_id, customer_id, plan, mode,
    )

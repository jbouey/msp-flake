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
from .tenant_middleware import admin_connection, admin_transaction

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
    # Optional partner-invite token (from /signup?invite=…). Captured here
    # so it survives /start → /checkout without re-posting from the client.
    # Consumed in the Stripe webhook via consume_invite_for_signup().
    partner_invite_token: Optional[str] = Field(None, min_length=1, max_length=256)

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

    # If a partner invite token came in, validate + look up partner_id
    # NOW so we fail fast on a bogus or consumed token — cheaper than
    # letting the user sign the BAA + hit checkout only to error at
    # webhook consumption. The token itself is stored on the row so we
    # don't need to re-post it at /checkout.
    partner_id: Optional[str] = None
    if req.partner_invite_token:
        from .partner_invites_api import _sha256_hex as _invite_sha256
        async with (await get_pool()).acquire() as _vconn:  # read-only
            invrow = await _vconn.fetchrow(
                """
                SELECT partner_id, expires_at, consumed_at, revoked_at
                  FROM partner_invites
                 WHERE token_sha256 = $1
                """,
                _invite_sha256(req.partner_invite_token),
            )
        if not invrow:
            raise HTTPException(status_code=400, detail="partner invite not found")
        if invrow["consumed_at"]:
            raise HTTPException(status_code=409, detail="partner invite already used")
        if invrow["revoked_at"]:
            raise HTTPException(status_code=410, detail="partner invite revoked")
        if invrow["expires_at"] and invrow["expires_at"] < datetime.now(timezone.utc):
            raise HTTPException(status_code=410, detail="partner invite expired")
        partner_id = str(invrow["partner_id"])

    pool = await get_pool()
    async with admin_connection(pool) as conn, conn.transaction():
        await conn.execute(
            """
            INSERT INTO signup_sessions
                (signup_id, email, practice_name, billing_contact_name,
                 state, plan, stripe_customer_id,
                 partner_invite_token, partner_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
            signup_id, req.email, req.practice_name, req.billing_contact_name,
            req.state, req.plan, customer.id,
            req.partner_invite_token, partner_id,
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
            "       completed_at, expires_at, partner_invite_token, partner_id "
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
        # Checkout session metadata — the webhook reads this back. We pass
        # the partner invite token through if present so that the webhook
        # handler can atomically consume it via consume_invite_for_signup()
        # and set subscriptions.partner_id. Stripe caps metadata values at
        # 500 chars and keys at 40, both well over what we need.
        checkout_metadata: Dict[str, str] = {"signup_id": req.signup_id, "plan": plan}
        if row["partner_invite_token"]:
            checkout_metadata["partner_invite_token"] = row["partner_invite_token"]
        if row["partner_id"]:
            checkout_metadata["partner_id"] = str(row["partner_id"])

        try:
            session = stripe.checkout.Session.create(
                mode=plan_cfg["mode"],
                customer=row["stripe_customer_id"],
                line_items=[{"price": price.id, "quantity": 1}],
                success_url=req.success_url + "?session_id={CHECKOUT_SESSION_ID}",
                cancel_url=req.cancel_url,
                metadata=checkout_metadata,
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
      - (self-serve cold path, partner-less) materialize a
        `client_orgs` row (status=pending) + owner `client_users` row
        + magic-link, send opaque onboarding email, issue an
        `appliance_provisions` claim code, write a
        `client_org_created` event into the per-org Ed25519
        attestation chain, and (on subsequent BAA confirmation
        upstream) flip status pending → active.

    Cold-onboarding adversarial-walkthrough P0 #1+#3+#4 closure
    (2026-05-09). The previous TODO-marked stub left the customer at
    a dead-end: they paid and got nothing. Now the webhook returns
    only after a fully-shaped tenant exists.

    Partner-invited signups skip the cold-path side-effects — the
    partner provisions the customer through the partner workflow.
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
    partner_invite_token = metadata.get("partner_invite_token")

    # Consume partner invite BEFORE marking signup complete — if consume
    # fails (expired, revoked, already-consumed), the signup still completes
    # but as a direct-to-clinic subscription (no partner_id set). Logged at
    # WARNING so it surfaces in the monitoring stream.
    consumed_invite: Optional[Dict[str, Any]] = None
    if partner_invite_token:
        try:
            from .partner_invites_api import consume_invite_for_signup
        except ImportError:
            from partner_invites_api import consume_invite_for_signup  # type: ignore
        consumed_invite = await consume_invite_for_signup(
            partner_invite_token, signup_id,
        )
        if consumed_invite is None:
            logger.warning(
                "signup with partner_invite_token failed to consume signup_id=%s "
                "— falling back to direct subscription (no partner_id)",
                signup_id,
            )
    resolved_partner_id = (
        consumed_invite["partner_id"] if consumed_invite else metadata.get("partner_id")
    )

    pool = await get_pool()
    # Cold-onboarding (2026-05-09): admin_transaction() pins SET LOCAL
    # app.is_admin + the multi-statement webhook work to a single
    # PgBouncer backend (CLAUDE.md inviolable rule for multi-statement
    # admin paths).
    async with admin_transaction(pool) as conn:
        # Pull signup_session row up front — we need email +
        # practice_name + billing_contact_name later for the
        # client_orgs materialization.
        signup_row = await conn.fetchrow(
            "SELECT email, practice_name, billing_contact_name, state, "
            "       baa_signature_id "
            "  FROM signup_sessions WHERE signup_id = $1",
            signup_id,
        )
        if signup_row is None:
            logger.error(
                "checkout_completed for unknown signup_id=%s — webhook noop",
                signup_id,
            )
            return

        # Mark signup completed (idempotent) + clear the plaintext token
        # so it isn't sitting in the DB after consumption.
        await conn.execute(
            "UPDATE signup_sessions "
            "   SET completed_at = COALESCE(completed_at, NOW()), "
            "       partner_invite_token = NULL, "
            "       partner_id = COALESCE(partner_id, $2::uuid) "
            " WHERE signup_id = $1",
            signup_id, resolved_partner_id,
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
                     cancel_at_period_end, billing_mode, partner_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'card', $9::uuid)
                ON CONFLICT (stripe_subscription_id) DO UPDATE SET
                    status                = EXCLUDED.status,
                    trial_end             = EXCLUDED.trial_end,
                    current_period_start  = EXCLUDED.current_period_start,
                    current_period_end    = EXCLUDED.current_period_end,
                    cancel_at_period_end  = EXCLUDED.cancel_at_period_end,
                    partner_id            = COALESCE(subscriptions.partner_id, EXCLUDED.partner_id),
                    updated_at            = NOW()
                """,
                sub.id, customer_id, plan, sub.status,
                datetime.fromtimestamp(sub.trial_end, tz=timezone.utc) if sub.trial_end else None,
                datetime.fromtimestamp(sub.current_period_start, tz=timezone.utc),
                datetime.fromtimestamp(sub.current_period_end, tz=timezone.utc),
                sub.cancel_at_period_end,
                resolved_partner_id,
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
                     cancel_at_period_end, billing_mode, partner_id)
                VALUES ($1, $2, $3, 'trialing', $4, $5, $4, false, 'card', $6::uuid)
                ON CONFLICT (stripe_subscription_id) DO NOTHING
                """,
                f"pilot_{session.get('id', signup_id)}", customer_id, plan,
                pilot_end, now, resolved_partner_id,
            )

        # ─── Cold-path side-effects (self-serve only) ─────────────
        # Partner-invited signups are provisioned by the partner via
        # the partner workflow (existing path), so we skip the
        # client_orgs materialization + provision-code issuance for
        # them. Self-serve only (resolved_partner_id is None) gets
        # the wire-through that closes audit P0 #1 + #3 + #4.
        provision_code: Optional[str] = None
        client_org_id: Optional[str] = None
        attestation_failed = False
        if resolved_partner_id is None:
            (
                client_org_id,
                provision_code,
                attestation_failed,
            ) = await _materialize_self_serve_tenant(
                conn=conn,
                signup_id=signup_id,
                signup_row=dict(signup_row),
                customer_id=customer_id,
                plan=plan,
            )

    # ─── Side-effects outside the DB transaction ─────────────────
    # Email + operator alert run AFTER the transaction commits.
    # Failure of either MUST NOT block the customer's account
    # materialization — the chain row is already on disk.
    if resolved_partner_id is None and client_org_id is not None:
        await _send_self_serve_onboarding_email(
            recipient=signup_row["email"],
            signup_id=signup_id,
            provision_code=provision_code,
        )
        # Operator-visibility alert (Session 216 chain-gap escalation
        # pattern). attestation_failed=True escalates severity and
        # appends [ATTESTATION-MISSING] to subject.
        from .chain_attestation import send_chain_aware_operator_alert
        send_chain_aware_operator_alert(
            event_type="client_org_created",
            severity="P2",
            summary="self-serve cold-onboarding tenant materialized",
            details={
                "signup_id": signup_id,
                "client_org_id": client_org_id,
                "plan": plan,
            },
            actor_email=None,  # Stripe webhook — no human actor
            site_id=f"client_org:{client_org_id}",
            attestation_failed=attestation_failed,
        )

    logger.info(
        "signup_completed signup_id=%s customer=%s plan=%s mode=%s "
        "client_org_id=%s provision_code=%s",
        signup_id, customer_id, plan, mode,
        client_org_id, "[issued]" if provision_code else None,
    )


# ─── Cold-onboarding helpers (2026-05-09 audit P0 #1+#3+#4) ───────

async def _materialize_self_serve_tenant(
    *,
    conn,
    signup_id: str,
    signup_row: Dict[str, Any],
    customer_id: Optional[str],
    plan: str,
) -> tuple[Optional[str], Optional[str], bool]:
    """Wire-through the self-serve cold-onboarding spine.

    Creates (idempotently):
      1. `client_orgs` row, `status='pending'` until BAA confirmed.
         When `signup_sessions.baa_signature_id IS NOT NULL`, the
         BAA is already on file → status flips straight to 'active'.
      2. Owner `client_users` row, role='owner' + magic-link token.
      3. `appliance_provisions` claim code, scoped to client_org_id
         (no partner_id; mig 296 makes that legal).
      4. `client_org_created` event into the per-org Ed25519
         attestation chain. Anchors at synthetic
         `client_org:<id>` namespace (Session 216 convention) since
         no site exists yet.
      5. If BAA already signed at signup-time, also writes a
         `baa_signed` chain event (P1-5 from the audit).

    Returns ``(client_org_id, provision_code, attestation_failed)``.
    All work runs inside the caller's admin_transaction so the
    webhook is atomic. Returns ``(None, None, False)`` on
    unrecoverable error.
    """
    email = signup_row.get("email")
    practice_name = signup_row.get("practice_name") or (email or "Practice")
    billing_state = signup_row.get("state")
    baa_signature_id = signup_row.get("baa_signature_id")

    # 1. client_orgs — idempotent on (primary_email).
    org_status = "active" if baa_signature_id else "pending"
    org_row = await conn.fetchrow(
        """
        INSERT INTO client_orgs (
            name, primary_email, billing_email, state,
            stripe_customer_id, status, onboarded_at
        )
        VALUES ($1, $2, $2, $3, $4, $5,
                CASE WHEN $5 = 'active' THEN NOW() ELSE NULL END)
        ON CONFLICT (primary_email) DO UPDATE SET
            stripe_customer_id = COALESCE(client_orgs.stripe_customer_id, EXCLUDED.stripe_customer_id),
            -- Promote pending → active when BAA confirmed; never demote.
            status = CASE
                WHEN client_orgs.status = 'pending' AND EXCLUDED.status = 'active'
                    THEN 'active'
                ELSE client_orgs.status
            END,
            onboarded_at = COALESCE(client_orgs.onboarded_at,
                                    CASE WHEN EXCLUDED.status = 'active'
                                         THEN NOW() ELSE NULL END),
            updated_at = NOW()
        RETURNING id, status
        """,
        practice_name, email, billing_state, customer_id, org_status,
    )
    if org_row is None:
        logger.error(
            "client_orgs upsert returned no row for signup_id=%s — webhook abort",
            signup_id,
        )
        return None, None, False
    client_org_id = str(org_row["id"])

    # 2. Owner client_users row + magic-link.
    magic_token = secrets.token_urlsafe(32)
    magic_token_hash = hashlib.sha256(magic_token.encode()).hexdigest()
    from datetime import timedelta
    magic_expires = datetime.now(timezone.utc) + timedelta(days=7)
    await conn.execute(
        """
        INSERT INTO client_users (
            client_org_id, email, name,
            magic_token, magic_token_expires_at,
            role, is_active, email_verified
        )
        VALUES ($1, $2, $3, $4, $5, 'owner', true, false)
        ON CONFLICT (email) DO UPDATE SET
            magic_token = EXCLUDED.magic_token,
            magic_token_expires_at = EXCLUDED.magic_token_expires_at,
            updated_at = NOW()
        """,
        client_org_id, email,
        signup_row.get("billing_contact_name"),
        magic_token_hash, magic_expires,
    )

    # 3. appliance_provisions — issue a claim code for the customer
    # to flash + boot a USB. Self-serve provision: client_org_id set,
    # partner_id NULL. Mig 296 makes this legal via the
    # appliance_provisions_partner_or_org_ck CHECK.
    from datetime import timedelta as _td
    provision_code = secrets.token_urlsafe(12).replace("_", "").replace("-", "")[:16].upper()
    target_site_id_hint = (
        practice_name.lower().replace(" ", "-")[:32] + "-" + secrets.token_hex(3)
    )
    await conn.execute(
        """
        INSERT INTO appliance_provisions (
            partner_id, client_org_id, provision_code,
            target_site_id, client_name,
            status, expires_at
        )
        VALUES (NULL, $1::uuid, $2, $3, $4, 'pending', NOW() + INTERVAL '30 days')
        ON CONFLICT (provision_code) DO NOTHING
        """,
        client_org_id, provision_code, target_site_id_hint, practice_name,
    )

    # 4. client_org_created chain event. Anchor synthetic per Session
    # 216 convention (no site exists yet for the brand-new org).
    from .chain_attestation import (
        emit_privileged_attestation,
        resolve_client_anchor_site_id,
    )
    anchor = await resolve_client_anchor_site_id(conn, client_org_id)
    failed_create, _ = await emit_privileged_attestation(
        conn,
        anchor_site_id=anchor,
        event_type="client_org_created",
        actor_email=email or "unknown@stripe-webhook",
        reason=(
            f"Stripe webhook materialized self-serve client_org from "
            f"signup_id={signup_id} plan={plan}"
        ),
    )

    # 5. baa_signed chain event (P1-5). Only if BAA was actually
    # signed at signup-time (the BAA gate runs BEFORE checkout in
    # client_signup.sign_baa).
    failed_baa = False
    if baa_signature_id:
        failed_baa, _ = await emit_privileged_attestation(
            conn,
            anchor_site_id=anchor,
            event_type="baa_signed",
            actor_email=email or "unknown@stripe-webhook",
            reason=(
                f"BAA signature on file at checkout — signature_id="
                f"{baa_signature_id} signup_id={signup_id}"
            ),
        )

    return client_org_id, provision_code, (failed_create or failed_baa)


# Subject literal MUST be a plain string (Session 218 task #42 +
# test_email_opacity_harmonized.py). Do NOT introduce f-string
# subjects or interpolate org/clinic/actor names.
_ONBOARDING_SUBJECT = "Your OsirisCare account is ready"


async def _send_self_serve_onboarding_email(
    *,
    recipient: Optional[str],
    signup_id: str,
    provision_code: Optional[str],
) -> None:
    """Customer-facing onboarding email. Opaque mode (Session 218
    task #42 harmonization): no clinic/org/actor names in subject or
    body. Identity context is served by authenticated portal session.

    The provision_code IS in the body — it's not identifying context
    (it's a 30-day, single-use bootstrap secret tied to the
    customer's account, scoped to their client_org_id). The opacity
    gate's FORBIDDEN_BODY_TOKENS list intentionally excludes
    provision codes; they are operational onboarding material, not
    PHI/clinic context.

    Best-effort. SMTP failure is logged at ERROR but MUST NOT block
    the response path — the customer's tenant is already
    materialized; ops can re-send the email manually.
    """
    if not recipient:
        logger.error(
            "onboarding email skipped — no recipient for signup_id=%s",
            signup_id,
        )
        return
    if not provision_code:
        logger.error(
            "onboarding email skipped — no provision_code for signup_id=%s",
            signup_id,
        )
        return
    try:
        # email_service.send_email delegates to
        # email_alerts._send_smtp_with_retry — no inline SMTP code
        # (CLAUDE.md inviolable rule).
        from .email_service import send_email
        portal_url = os.getenv(
            "CLIENT_PORTAL_URL", "https://portal.osiriscare.net"
        )
        signup_ref = signup_id[:8]
        # Inline f-string body so test_email_opacity_harmonized.py
        # can resolve it to a literal at AST time. None of the
        # interpolated tokens ({portal_url}, {provision_code},
        # {signup_ref}) are in FORBIDDEN_BODY_TOKENS.
        body = (
            f"Hello,\n"
            f"\n"
            f"Your OsirisCare account is provisioned. To complete setup:\n"
            f"\n"
            f"1. Sign in to the customer portal:\n"
            f"     {portal_url}\n"
            f"2. Download the appliance installer ISO from the portal.\n"
            f"3. Flash the ISO to a USB stick (16GB+) and boot the target\n"
            f"   hardware.\n"
            f"4. When prompted, enter the provision code below:\n"
            f"\n"
            f"     PROVISION CODE: {provision_code}\n"
            f"\n"
            f"Reference: signup-{signup_ref}\n"
            f"Provision code expires in 30 days.\n"
            f"\n"
            f"Why this email omits identifying information:\n"
            f"We minimize identifying information in unauthenticated channels\n"
            f"(email transit, third-party SMTP relays). Full account context\n"
            f"is visible only inside the authenticated portal session.\n"
            f"\n"
            f"If you did not just sign up for OsirisCare, do not follow these\n"
            f"instructions. Reply to this email so we can investigate.\n"
            f"\n"
            f"---\n"
            f"OsirisCare — substrate-level account onboarding notice"
        )
        await send_email(recipient, _ONBOARDING_SUBJECT, body)
    except Exception:
        logger.error(
            "onboarding_email_failed signup_id=%s", signup_id, exc_info=True,
        )

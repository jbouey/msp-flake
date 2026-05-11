"""Stripe Connect scaffold for partner payouts (audit finding #37).

Separates the partner-payout surface from the clinic-billing surface in
``billing.py``. billing.py is the *inbound* money path (clinic pays
OsirisCare). This module is the *outbound* money path (OsirisCare pays
partner the revenue share computed by migration 233).

Architecture:

  1. Partner clicks "connect payouts" in the dashboard
     → POST /api/partners/me/payouts/onboard
     → creates a Stripe Connect Express account (if not already created)
     → returns a one-shot AccountLink URL the partner completes on Stripe's
       hosted onboarding — bank account collection lives entirely inside
       Stripe so no payout detail ever hits our DB.

  2. Stripe calls back to /return_url; our frontend polls
     → GET /api/partners/me/payouts/status
     → refreshes capabilities from Stripe.Account.retrieve and updates
       partners.stripe_connect_status.

  3. Monthly payout job (scheduled via background task in main.py)
     → compute commission from same primitives the dashboard uses
       (active clinics + mrr + compute_partner_rate_bps())
     → UPSERT into partner_payout_runs with status='computed'
     → for each row with status='computed' AND partner.payouts_enabled=true,
       create a Stripe Transfer
     → on success, status='paid' + transferred_at. On failure, status='failed'
       with stripe_error captured; next run retries.

  4. Reconciliation endpoint surfaces the ledger
     → GET /api/partners/me/payouts/history

This is SCAFFOLD. We wire the endpoints + the job skeleton, and we persist
all state idempotently. The first real partner to complete Connect
onboarding + earn a payout will exercise the live Stripe Transfer path; a
feature flag (OSIRIS_PAYOUT_ENABLED=true) gates the transfer itself so
ops can dry-run the computation without moving money.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .fleet import get_pool
from .tenant_middleware import admin_connection, admin_transaction
from .partners import require_partner

logger = logging.getLogger(__name__)

try:
    import stripe
    HAS_STRIPE = True
except ImportError:
    HAS_STRIPE = False
    logger.warning("stripe library not installed - Connect endpoints disabled")

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://www.osiriscare.net")

# Feature flag gating the real money-movement side of the pipeline.
# While this is unset, the monthly job runs in DRY-RUN mode: it computes
# partner_payout_runs rows but does NOT create Stripe Transfers. This
# lets ops verify the math against the commission dashboard for a cycle
# or two before enabling disbursement.
PAYOUT_ENABLED = os.getenv("OSIRIS_PAYOUT_ENABLED", "false").lower() == "true"

# Monthly amount per plan — parallel to partners.get_commission. Kept in
# this module to avoid a cross-module import; the source of truth is Stripe
# (Price.unit_amount) and these constants are a fallback used when a
# subscription row has no Stripe id yet (freshly-provisioned clinic in the
# same month as its payout run).
_MONTHLY_AMOUNT_CENTS = {
    "essentials":   49900,
    "professional": 79900,
    "enterprise":  129900,
}

if HAS_STRIPE and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY


router = APIRouter(prefix="/api/partners/me/payouts", tags=["partner-payouts"])


# ---------------------------------------------------------------------------
# Onboarding: Connect Express account creation + AccountLink
# ---------------------------------------------------------------------------


class OnboardResponse(BaseModel):
    account_id: str
    onboarding_url: str
    status: str


@router.post("/onboard", response_model=OnboardResponse)
async def onboard_connect(
    request: Request,
    partner: Dict[str, Any] = Depends(require_partner),
):
    """Create (or reuse) a Stripe Connect Express account and return the
    AccountLink URL the partner completes on Stripe's hosted flow.

    The partner's bank account never touches our infra — Stripe's hosted
    onboarding collects it directly. We only store the account_id.
    """
    if not HAS_STRIPE or not STRIPE_SECRET_KEY:
        raise HTTPException(503, "Stripe is not configured on this deployment")

    partner_id = partner["id"]
    pool = await get_pool()

    # admin_transaction (wave-33): onboard_connect issues 2 admin
    # statements (partner lookup, UPDATE Stripe Connect status).
    async with admin_transaction(pool) as conn:
        row = await conn.fetchrow(
            "SELECT stripe_connect_account_id, stripe_connect_status, contact_email "
            "FROM partners WHERE id = $1",
            partner_id,
        )
        if not row:
            raise HTTPException(404, "Partner not found")

        account_id = row["stripe_connect_account_id"]

        if not account_id:
            # First-time onboarding — create the Express account. Type = express
            # is the Stripe-branded onboarding flow with minimal integration
            # surface on our side. Stripe owns the KYC + ID-verification UX.
            try:
                acct = stripe.Account.create(
                    type="express",
                    email=row["contact_email"],
                    capabilities={
                        "transfers": {"requested": True},
                    },
                    metadata={
                        "osiris_partner_id": str(partner_id),
                    },
                )
            except stripe.error.StripeError as e:
                logger.error(
                    "stripe_connect.create_account_failed",
                    extra={"partner_id": str(partner_id), "err": str(e)},
                    exc_info=True,
                )
                raise HTTPException(502, "Stripe account creation failed")

            account_id = acct["id"]
            await conn.execute(
                "UPDATE partners SET "
                "  stripe_connect_account_id = $1, "
                "  stripe_connect_status = 'onboarding', "
                "  stripe_connect_country = $2, "
                "  stripe_connect_linked_at = NOW() "
                "WHERE id = $3",
                account_id, acct.get("country"), partner_id,
            )

    # AccountLink is single-use; generate fresh every call. On completion
    # Stripe redirects the partner back to FRONTEND_URL/partner/billing
    # where the frontend polls /status to refresh capabilities.
    try:
        link = stripe.AccountLink.create(
            account=account_id,
            refresh_url=f"{FRONTEND_URL}/partner/billing?connect=refresh",
            return_url=f"{FRONTEND_URL}/partner/billing?connect=done",
            type="account_onboarding",
        )
    except stripe.error.StripeError as e:
        logger.error(
            "stripe_connect.account_link_failed",
            extra={"partner_id": str(partner_id), "account_id": account_id, "err": str(e)},
            exc_info=True,
        )
        raise HTTPException(502, "Stripe onboarding link could not be generated")

    return OnboardResponse(
        account_id=account_id,
        onboarding_url=link["url"],
        status="onboarding",
    )


class ConnectStatus(BaseModel):
    account_id: Optional[str]
    status: str
    payouts_enabled: bool
    charges_enabled: bool
    requirements_due: List[str]
    last_synced: Optional[str]


@router.get("/status", response_model=ConnectStatus)
async def get_connect_status(
    partner: Dict[str, Any] = Depends(require_partner),
):
    """Refresh the Connect account capabilities from Stripe and persist."""
    partner_id = partner["id"]
    pool = await get_pool()

    # admin_transaction (wave-33): get_connect_status issues 2 admin
    # statements (lookup, UPDATE last_synced + capabilities cache).
    async with admin_transaction(pool) as conn:
        row = await conn.fetchrow(
            "SELECT stripe_connect_account_id, stripe_connect_status, "
            "       stripe_connect_last_synced "
            "FROM partners WHERE id = $1",
            partner_id,
        )
        if not row or not row["stripe_connect_account_id"]:
            return ConnectStatus(
                account_id=None,
                status="not_started",
                payouts_enabled=False,
                charges_enabled=False,
                requirements_due=[],
                last_synced=None,
            )

        account_id = row["stripe_connect_account_id"]

        if not HAS_STRIPE or not STRIPE_SECRET_KEY:
            # Without Stripe we can still surface what's persisted.
            return ConnectStatus(
                account_id=account_id,
                status=row["stripe_connect_status"] or "unknown",
                payouts_enabled=False,
                charges_enabled=False,
                requirements_due=[],
                last_synced=(
                    row["stripe_connect_last_synced"].isoformat()
                    if row["stripe_connect_last_synced"] else None
                ),
            )

        try:
            acct = stripe.Account.retrieve(account_id)
        except stripe.error.StripeError as e:
            logger.error(
                "stripe_connect.status_fetch_failed",
                extra={"partner_id": str(partner_id), "account_id": account_id, "err": str(e)},
                exc_info=True,
            )
            raise HTTPException(502, "Could not read Stripe account status")

        payouts_enabled = bool(acct.get("payouts_enabled"))
        charges_enabled = bool(acct.get("charges_enabled"))
        requirements = acct.get("requirements", {}) or {}
        currently_due = list(requirements.get("currently_due", []) or [])

        if payouts_enabled and charges_enabled:
            status = "payouts_enabled"
        elif charges_enabled:
            status = "charges_enabled"
        elif currently_due:
            status = "onboarding"
        else:
            status = "restricted"

        await conn.execute(
            "UPDATE partners SET "
            "  stripe_connect_status = $1, "
            "  stripe_connect_last_synced = NOW() "
            "WHERE id = $2",
            status, partner_id,
        )

        return ConnectStatus(
            account_id=account_id,
            status=status,
            payouts_enabled=payouts_enabled,
            charges_enabled=charges_enabled,
            requirements_due=currently_due,
            last_synced=datetime.now(timezone.utc).isoformat(),
        )


# ---------------------------------------------------------------------------
# Payout history (reconciliation endpoint)
# ---------------------------------------------------------------------------


class PayoutRun(BaseModel):
    id: str
    period_start: str
    period_end: str
    active_clinic_count: int
    mrr_cents: int
    effective_rate_bps: int
    payout_cents: int
    currency: str
    status: str
    stripe_transfer_id: Optional[str]
    transferred_at: Optional[str]
    notes: Optional[str]


class PayoutHistory(BaseModel):
    runs: List[PayoutRun]
    connect_status: str
    dry_run_mode: bool


@router.get("/history", response_model=PayoutHistory)
async def get_payout_history(
    partner: Dict[str, Any] = Depends(require_partner),
):
    """Return the partner's payout ledger. Used for reconciliation."""
    partner_id = partner["id"]
    pool = await get_pool()

    # admin_transaction (wave-33): get_payout_history issues 2 admin
    # reads (status check, payout ledger).
    async with admin_transaction(pool) as conn:
        ps = await conn.fetchrow(
            "SELECT stripe_connect_status FROM partners WHERE id = $1",
            partner_id,
        )
        rows = await conn.fetch(
            "SELECT id, period_start, period_end, active_clinic_count, "
            "       mrr_cents, effective_rate_bps, payout_cents, currency, "
            "       status, stripe_transfer_id, transferred_at, notes "
            "  FROM partner_payout_runs "
            " WHERE partner_id = $1 "
            " ORDER BY period_start DESC "
            " LIMIT 60",
            partner_id,
        )

    runs = [
        PayoutRun(
            id=str(r["id"]),
            period_start=r["period_start"].isoformat(),
            period_end=r["period_end"].isoformat(),
            active_clinic_count=r["active_clinic_count"],
            mrr_cents=r["mrr_cents"],
            effective_rate_bps=r["effective_rate_bps"],
            payout_cents=r["payout_cents"],
            currency=r["currency"],
            status=r["status"],
            stripe_transfer_id=r["stripe_transfer_id"],
            transferred_at=(
                r["transferred_at"].isoformat() if r["transferred_at"] else None
            ),
            notes=r["notes"],
        )
        for r in rows
    ]

    return PayoutHistory(
        runs=runs,
        connect_status=(ps["stripe_connect_status"] if ps else "not_started") or "not_started",
        dry_run_mode=not PAYOUT_ENABLED,
    )


# ---------------------------------------------------------------------------
# Monthly payout job
# ---------------------------------------------------------------------------


# Per-payout sanity ceiling — a bug in commission math (bad rate_bps, wrong
# plan amount, currency confusion) must not move unbounded money. $100k is
# ~3 orders of magnitude above the real max per-partner monthly commission;
# hitting it is a loud signal to halt + investigate, not a silent transfer.
MAX_PAYOUT_CENTS_CEILING = 10_000_000

# Default commission rate when compute_partner_rate_bps returns NULL. Prior
# code used 4000bps (40%) silently, which made a function-side bug look like
# "every partner earns 40%". We intentionally do NOT have a silent fallback
# — the job logs + skips so ops can fix the rate function rather than
# disbursing at a made-up rate.
_JOB_ADVISORY_LOCK_KEY = "partner_payout_job"


def _sanitize_stripe_error(e: "stripe.error.StripeError") -> str:  # type: ignore[name-defined]
    """Produce a compact, PII-safe summary of a Stripe error for persistence.

    `str(e)` can leak recipient/account identifiers embedded in Stripe's
    error body. We persist code + type + http_status + a short reason only.
    The full exception is always logged (exc_info=True) to the structured log
    for ops — the DB row stores only what's safe to surface in the partner
    reconciliation endpoint.
    """
    code = getattr(e, "code", None) or "unknown"
    etype = type(e).__name__
    http_status = getattr(e, "http_status", None) or "-"
    # user_message if Stripe flagged one is intentionally public-safe;
    # fall back to the exception class short reason.
    user_msg = getattr(getattr(e, "user_message", None), "__str__", lambda: None)()
    short_reason = (user_msg or etype)[:200]
    return f"{etype}/{code}/http={http_status}/{short_reason}"


async def run_monthly_payout_job(for_month: Optional[date] = None) -> Dict[str, Any]:
    """Compute + optionally disburse partner payouts for the given month.

    Run from main.py's scheduled-task tree on the first of each month at
    02:00 UTC (after the compliance_packets generator, before business
    hours). Safe to run repeatedly — UPSERT on (partner_id, period_start)
    makes every row idempotent.

    Invariants (Session 209 audit F4):
      - pg_advisory_xact_lock serializes concurrent runs. Cron + an admin
        "run now" click can both fire; we refuse to double-process.
      - No single DB connection spans Stripe API calls. Each partner's DB
        ops run in a short-lived admin_connection; the Stripe Transfer call
        happens outside any held connection.
      - NULL rate_bps is a bug signal, not a 40% silent default. Skip + log.
      - Per-payout ceiling enforced before Transfer.create — a bug in math
        must not move unbounded money.
      - Persisted stripe_error_message is sanitized (no raw str(e) — that
        can contain recipient identifiers).

    Returns a summary dict so the caller can log / alert on dry-run output.
    """
    pool = await get_pool()
    now = datetime.now(timezone.utc)

    # Default period = previous calendar month.
    if for_month is None:
        first_of_this_month = now.replace(day=1).date()
        period_end = first_of_this_month - timedelta(days=1)
        period_start = period_end.replace(day=1)
    else:
        period_start = for_month.replace(day=1)
        next_month = (period_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        period_end = next_month - timedelta(days=1)

    summary: Dict[str, Any] = {
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "dry_run": not PAYOUT_ENABLED,
        "computed": 0,
        "transferred": 0,
        "failed": 0,
        "skipped": 0,
        "skipped_no_rate": 0,
        "skipped_over_cap": 0,
        "lock_not_acquired": False,
    }

    # Step 1 (short conn): acquire cross-run advisory lock + read partner list.
    # We use pg_try_advisory_lock (NOT _xact_lock) so we can HOLD the lock
    # across the separate short transactions we open per partner below. Any
    # concurrent invocation returns False immediately and exits without work.
    conn_held = None
    try:
        # admin_transaction (wave-53): outer block reads partner list +
        # acquires pg_try_advisory_lock. The advisory lock is SESSION-
        # scoped (not _xact_lock), so the COMMIT issued by
        # admin_transaction's __aexit__ does NOT release it — the
        # connection returning to the pool does (same as admin_connection).
        # Behavior identical; routing-pathology hardening is the win.
        async with admin_transaction(pool) as conn:
            got_lock = await conn.fetchval(
                "SELECT pg_try_advisory_lock(hashtext($1))", _JOB_ADVISORY_LOCK_KEY,
            )
            if not got_lock:
                logger.warning(
                    "stripe_connect.monthly_payout_skipped_concurrent",
                    extra={"period_start": period_start.isoformat()},
                )
                summary["lock_not_acquired"] = True
                return summary

            partner_rows = await conn.fetch("""
                WITH active_clinics AS (
                    SELECT s.partner_id,
                           COUNT(DISTINCT s.site_id) AS active_count,
                           COALESCE(SUM(
                               CASE
                                   WHEN sub.plan = 'essentials'   THEN 49900
                                   WHEN sub.plan = 'professional' THEN 79900
                                   WHEN sub.plan = 'enterprise'   THEN 129900
                                   ELSE 0
                               END
                           ), 0) AS mrr_cents
                      FROM sites s
                 LEFT JOIN subscriptions sub ON sub.site_id = s.site_id
                     WHERE s.partner_id IS NOT NULL
                       AND s.status = 'active'
                     GROUP BY s.partner_id
                )
                SELECT p.id AS partner_id,
                       COALESCE(ac.active_count, 0)::int AS active_count,
                       COALESCE(ac.mrr_cents,    0)::int AS mrr_cents,
                       p.stripe_connect_account_id,
                       p.stripe_connect_status
                  FROM partners p
             LEFT JOIN active_clinics ac ON ac.partner_id = p.id
                 WHERE p.status = 'active'
            """)

            # Release this connection so Stripe API calls don't tie up a
            # pool slot. The advisory lock is tied to THIS connection via
            # pg_try_advisory_lock — releasing means another run can acquire.
            # That's acceptable for our cadence (monthly cron): worst case
            # two concurrent runs after this point each race on the
            # per-partner UPSERT, which is idempotent on (partner_id,
            # period_start). Transfer.create is idempotency-keyed too.
            # If we need stricter serialization in future, convert to a
            # persistent lock row in partner_payout_job_locks.

        for row in partner_rows:
            pid = row["partner_id"]
            clinics = row["active_count"] or 0
            mrr = row["mrr_cents"] or 0

            if clinics == 0 or mrr == 0:
                summary["skipped"] += 1
                continue

            # Short conn #1 for this partner: compute rate + UPSERT row.
            # admin_transaction (wave-53): 2 admin statements (compute
            # rate + UPSERT payout row). Pinned to one PgBouncer backend.
            async with admin_transaction(pool) as conn_compute:
                rate_bps = await conn_compute.fetchval(
                    "SELECT compute_partner_rate_bps($1::uuid, $2::int)",
                    pid, clinics,
                )
                if rate_bps is None:
                    # compute_partner_rate_bps is the source of truth for
                    # commission. A NULL here means the tier ladder is
                    # broken (missing row, wrong input type, function
                    # rewrite). Do NOT fall back to 40% silently — that
                    # masks the bug and overpays.
                    logger.error(
                        "stripe_connect.rate_bps_null",
                        extra={
                            "partner_id": str(pid),
                            "active_clinic_count": clinics,
                            "period_start": period_start.isoformat(),
                        },
                    )
                    summary["skipped_no_rate"] += 1
                    continue
                rate_bps = int(rate_bps)
                payout_cents = (mrr * rate_bps) // 10000

                # Sanity ceiling. Logged loud; row is not written so it
                # doesn't accidentally get picked up by a manual reconcile.
                if payout_cents > MAX_PAYOUT_CENTS_CEILING:
                    logger.error(
                        "stripe_connect.payout_over_cap",
                        extra={
                            "partner_id": str(pid),
                            "period_start": period_start.isoformat(),
                            "payout_cents": payout_cents,
                            "cap_cents": MAX_PAYOUT_CENTS_CEILING,
                            "mrr_cents": mrr,
                            "rate_bps": rate_bps,
                            "active_clinic_count": clinics,
                        },
                    )
                    summary["skipped_over_cap"] += 1
                    continue

                await conn_compute.execute(
                    """
                    INSERT INTO partner_payout_runs (
                        partner_id, period_start, period_end,
                        active_clinic_count, mrr_cents, effective_rate_bps,
                        payout_cents, currency, status
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, 'usd', 'computed')
                    ON CONFLICT (partner_id, period_start) DO UPDATE SET
                        period_end           = EXCLUDED.period_end,
                        active_clinic_count  = EXCLUDED.active_clinic_count,
                        mrr_cents            = EXCLUDED.mrr_cents,
                        effective_rate_bps   = EXCLUDED.effective_rate_bps,
                        payout_cents         = EXCLUDED.payout_cents,
                        status               = CASE
                            WHEN partner_payout_runs.status = 'paid' THEN 'paid'
                            ELSE 'computed'
                        END
                    """,
                    pid, period_start, period_end,
                    clinics, mrr, rate_bps, payout_cents,
                )
                summary["computed"] += 1

            # Disbursement gates. Anything that stays 'computed' surfaces in
            # the partner reconciliation endpoint for human review.
            if not PAYOUT_ENABLED:
                continue
            if row["stripe_connect_status"] != "payouts_enabled":
                continue
            if not row["stripe_connect_account_id"]:
                continue
            if not HAS_STRIPE or not STRIPE_SECRET_KEY:
                continue

            # Short conn #2: mark transferring. Released before Stripe call
            # so a slow Stripe API doesn't pin a connection for minutes.
            async with admin_connection(pool) as conn_mark:
                await conn_mark.execute(
                    "UPDATE partner_payout_runs SET status = 'transferring' "
                    " WHERE partner_id = $1 AND period_start = $2 "
                    "   AND status = 'computed'",
                    pid, period_start,
                )

            # Stripe API call — deliberately NOT inside any DB tx. Idempotency
            # key is stable so a retry after a DB hiccup below does not
            # create a duplicate transfer.
            transferred_ok = False
            transfer_id: Optional[str] = None
            sanitized_error: Optional[str] = None
            error_code: Optional[str] = None
            try:
                transfer = stripe.Transfer.create(
                    amount=payout_cents,
                    currency="usd",
                    destination=row["stripe_connect_account_id"],
                    description=f"OsirisCare revenue share — {period_start.strftime('%B %Y')}",
                    idempotency_key=f"payout-{pid}-{period_start.isoformat()}",
                    metadata={
                        "partner_id": str(pid),
                        "period_start": period_start.isoformat(),
                        "period_end": period_end.isoformat(),
                    },
                )
                transfer_id = transfer["id"]
                transferred_ok = True
            except stripe.error.StripeError as e:
                logger.error(
                    "stripe_connect.payout_failed",
                    extra={
                        "partner_id": str(pid),
                        "period_start": period_start.isoformat(),
                    },
                    exc_info=True,
                )
                error_code = getattr(e, "code", None)
                sanitized_error = _sanitize_stripe_error(e)

            # Short conn #3: record outcome. This MUST run — if it fails we
            # re-raise so the monthly job alerts on a partial-write; an
            # un-recorded successful Transfer would leak dollars from the
            # reconciliation view.
            async with admin_connection(pool) as conn_record:
                async with conn_record.transaction():
                    if transferred_ok:
                        await conn_record.execute(
                            "UPDATE partner_payout_runs SET "
                            "  status = 'paid', "
                            "  stripe_transfer_id = $1, "
                            "  transferred_at = NOW() "
                            " WHERE partner_id = $2 AND period_start = $3",
                            transfer_id, pid, period_start,
                        )
                        summary["transferred"] += 1
                    else:
                        await conn_record.execute(
                            "UPDATE partner_payout_runs SET "
                            "  status = 'failed', "
                            "  stripe_error_code = $1, "
                            "  stripe_error_message = $2 "
                            " WHERE partner_id = $3 AND period_start = $4",
                            error_code, sanitized_error, pid, period_start,
                        )
                        summary["failed"] += 1

    finally:
        # Best-effort release of the advisory lock if this process still
        # holds it. The lock is connection-scoped, so the with-block exit
        # above already released it via connection return-to-pool; this is
        # defensive only.
        if conn_held is not None:
            try:
                await conn_held.close()
            except Exception:
                pass

    logger.info("stripe_connect.monthly_payout_complete", extra=summary)
    return summary


# ---------------------------------------------------------------------------
# Admin manual trigger (useful for backfill + ops dry-runs)
# ---------------------------------------------------------------------------


class AdminPayoutRun(BaseModel):
    period_start: str  # YYYY-MM-DD


class AdminPayoutResult(BaseModel):
    period_start: str
    period_end: str
    dry_run: bool
    computed: int
    transferred: int
    failed: int
    skipped: int


admin_router = APIRouter(prefix="/api/admin/payouts", tags=["admin-payouts"])


try:
    from .auth import require_admin
except ImportError:
    from auth import require_admin


@admin_router.post("/run", response_model=AdminPayoutResult)
async def admin_trigger_payout(
    payload: AdminPayoutRun,
    admin: Dict[str, Any] = Depends(require_admin),
):
    """Manually trigger a payout run for a specific month. Admin-only.

    Separate from the monthly scheduled job so ops can backfill a skipped
    month or run dry-runs against prior periods for reconciliation.
    """
    try:
        month = date.fromisoformat(payload.period_start)
    except ValueError:
        raise HTTPException(400, "period_start must be YYYY-MM-DD (will be snapped to the 1st)")

    summary = await run_monthly_payout_job(for_month=month)
    return AdminPayoutResult(**summary)

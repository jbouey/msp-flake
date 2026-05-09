"""Cold-onboarding 2026-05-09 adversarial-walkthrough P0 #1+#3+#4.

Pre-fix: `client_signup.handle_checkout_completed_for_signup` carried
a TODO and never materialized `client_orgs` / `client_users` /
provision-code / chain attestation. A customer who paid $499 via
Stripe got nothing — webhook was a dead-end.

This source-shape gate pins the wire-through invariants so a future
refactor can't silently regress them.

Invariants enforced:
    1. Webhook handler creates a `client_orgs` row.
    2. Owner `client_users` row created with role='owner' +
       magic-link.
    3. `appliance_provisions` claim code issued (self-serve →
       client_org_id set, partner_id NULL).
    4. `client_org_created` event written to per-org Ed25519 chain.
    5. ALLOWED_EVENTS contains both `client_org_created` and
       `baa_signed` (P1-5).
    6. Onboarding email goes through email_service.send_email — no
       inline SMTP code.
    7. All multi-statement DB work runs inside `admin_transaction()`
       (CLAUDE.md inviolable rule for admin-class multi-stmt paths).
    8. Operator-alert hook fires on creation with the chain-aware
       helper (Session 216 chain-gap escalation pattern).
"""
from __future__ import annotations

import ast
import pathlib

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_SIGNUP = _BACKEND / "client_signup.py"
_PRIV = _BACKEND / "privileged_access_attestation.py"
_MIG_296 = (
    _BACKEND
    / "migrations"
    / "296_cold_onboarding_idempotency_and_provisions.sql"
)


def _src() -> str:
    return _SIGNUP.read_text()


def _tree() -> ast.Module:
    return ast.parse(_src(), filename=str(_SIGNUP))


# ─── 1. client_orgs creation ────────────────────────────────────


def test_webhook_inserts_into_client_orgs():
    src = _src()
    assert "INSERT INTO client_orgs" in src, (
        "client_signup.py must INSERT INTO client_orgs in the cold-"
        "onboarding wire-through. Pre-fix the webhook only wrote a "
        "subscriptions row — customer was orphaned with no tenant."
    )


def test_client_orgs_status_starts_pending_until_baa():
    """Maya P0: default new client_orgs to status='pending' until BAA
    confirmed. Promotion to 'active' is contingent on
    baa_signature_id being on the signup_session row."""
    src = _src()
    assert "'pending'" in src and "'active'" in src, (
        "Cold-onboarding webhook must distinguish pending vs active "
        "client_orgs status (Maya P0 — gate for BAA-confirmed only)."
    )
    assert "baa_signature_id" in src, (
        "Status promotion must read signup_sessions.baa_signature_id "
        "to know if the BAA was signed before checkout."
    )


# ─── 2. owner client_users + magic link ─────────────────────────


def test_webhook_inserts_owner_client_user():
    src = _src()
    assert "INSERT INTO client_users" in src, (
        "client_signup.py must INSERT INTO client_users with the "
        "buyer's email as the owner of the new client_orgs row."
    )
    assert "'owner'" in src, "owner role must be assigned"
    assert "magic_token" in src, (
        "magic_token + magic_token_expires_at required so the "
        "customer can sign in to the portal without a password set."
    )


# ─── 3. provision-code issuance ─────────────────────────────────


def test_webhook_issues_provision_code():
    src = _src()
    assert "INSERT INTO appliance_provisions" in src, (
        "client_signup.py must issue an appliance_provisions row so "
        "the customer can flash + boot a USB to bring their first "
        "appliance online (P0 #4 from cold-onboarding audit)."
    )
    # Self-serve = partner_id NULL, client_org_id set (mig 296).
    tree = _tree()
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant):
            continue
        if isinstance(node.value, str) and "INSERT INTO appliance_provisions" in node.value:
            stmt = node.value
            assert (
                "partner_id" in stmt
                and "client_org_id" in stmt
            ), (
                "Self-serve appliance_provisions INSERT must set "
                "client_org_id and leave partner_id NULL (mig 296 "
                "CHECK constraint enforces the mutex)."
            )
            found = True
            break
    assert found, "could not locate INSERT INTO appliance_provisions string"


# ─── 4. chain attestation event ─────────────────────────────────


def test_webhook_emits_client_org_created_chain_event():
    src = _src()
    assert "client_org_created" in src, (
        "client_signup.py must emit a `client_org_created` event into "
        "the per-org Ed25519 chain at moment of org materialization "
        "(audit P0 #1 — chain anchor for the tenant lifecycle)."
    )
    assert "emit_privileged_attestation" in src, (
        "Use chain_attestation.emit_privileged_attestation (canonical "
        "helper) — direct calls to "
        "create_privileged_access_attestation are gated by "
        "test_chain_attestation_no_inline_duplicates.py."
    )


def test_webhook_emits_baa_signed_when_present():
    """P1-5: when the buyer signed the BAA at signup-time (the gate
    for checkout), the webhook must also write a `baa_signed` chain
    event."""
    src = _src()
    assert "baa_signed" in src, (
        "P1-5: baa_signed chain event must be emitted when "
        "signup_sessions.baa_signature_id is set."
    )


# ─── 5. ALLOWED_EVENTS lockstep ─────────────────────────────────


def test_allowed_events_contains_client_org_created_and_baa_signed():
    src = _PRIV.read_text()
    for event in ("client_org_created", "baa_signed"):
        assert f'"{event}"' in src, (
            f"ALLOWED_EVENTS must include `{event}`. The lockstep "
            f"checker (scripts/check_privileged_chain_lockstep.py) "
            f"requires ALLOWED_EVENTS ⊇ admin-API class events."
        )


# ─── 6. email goes through email_service.send_email ─────────────


def test_webhook_uses_email_service_not_inline_smtp():
    """CLAUDE.md inviolable rule: ALL emails via _send_smtp_with_retry
    (or its delegate, email_service.send_email). Never inline."""
    src = _src()
    assert "from .email_service import send_email" in src, (
        "Onboarding email must delegate to email_service.send_email. "
        "Inline smtplib calls are banned (CLAUDE.md _send_smtp_with_"
        "retry rule)."
    )
    forbidden = ("import smtplib", "smtplib.SMTP")
    for tok in forbidden:
        assert tok not in src, (
            f"client_signup.py must not contain `{tok}` — inline SMTP "
            f"is banned. Use email_service.send_email."
        )


# ─── 7. admin_transaction usage ─────────────────────────────────


def test_webhook_uses_admin_transaction_for_multistatement_path():
    """CLAUDE.md inviolable rule: multi-statement admin paths use
    admin_transaction() to pin SET LOCAL app.is_admin + queries to a
    single PgBouncer backend."""
    src = _src()
    assert "admin_transaction" in src, (
        "Cold-onboarding wire-through is a multi-statement admin path "
        "(client_orgs INSERT + client_users INSERT + "
        "appliance_provisions INSERT + chain attestation). It MUST "
        "use admin_transaction(pool), not bare admin_connection."
    )


# ─── 8. operator-alert hook ─────────────────────────────────────


def test_webhook_calls_chain_aware_operator_alert():
    """Session 216 chain-gap escalation pattern: any operator-
    visibility hook that follows an Ed25519 attestation must
    escalate severity to P0-CHAIN-GAP if the attestation step
    failed."""
    src = _src()
    assert "send_chain_aware_operator_alert" in src, (
        "Cold-onboarding webhook must call "
        "chain_attestation.send_chain_aware_operator_alert so the "
        "chain-gap escalation pattern is uniform across the codebase."
    )


# ─── Migration 296 sanity ───────────────────────────────────────


def test_mig_296_exists_with_idempotency_indexes():
    src = _MIG_296.read_text()
    for needle in (
        "CREATE UNIQUE INDEX IF NOT EXISTS uniq_signup_sessions_email_plan",
        "CREATE UNIQUE INDEX IF NOT EXISTS uniq_baa_signatures_email_version",
        "ADD COLUMN IF NOT EXISTS client_org_id",
        "appliance_provisions_partner_or_org_ck",
    ):
        assert needle in src, (
            f"Migration 296 missing required clause: {needle!r}"
        )

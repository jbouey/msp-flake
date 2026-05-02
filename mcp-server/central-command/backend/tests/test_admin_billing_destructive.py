"""CI gate: admin destructive billing actions follow the privileged
action chain.

#72 closure 2026-05-02 (followup of #67 admin billing read-only view).

Adversarial round-table contract:
  - confirm_phrase typed-literal (different per action — anti-typo)
  - reason ≥20 chars (privileged-access chain rule)
  - actor MUST be email (Ed25519 attestation requires '@' format)
  - Stripe call uses idempotency_key (anti-double-click)
  - admin_audit_log row written
  - Ed25519 privileged_access_attestation written to customer's site_id

This source-level gate enforces the contract. Runtime testing
(against a Stripe test account) is a separate concern not in scope.
"""
from __future__ import annotations

import pathlib

import pytest


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND.parent.parent.parent
_MAIN_PY = _REPO_ROOT / "mcp-server" / "main.py"
_PAA = _BACKEND / "privileged_access_attestation.py"


def _read(p: pathlib.Path) -> str:
    return p.read_text()


# ─── Endpoint existence ─────────────────────────────────────────────


def test_cancel_subscription_endpoint_exists():
    src = _read(_MAIN_PY)
    assert "/api/admin/billing/customers/{stripe_customer_id}/cancel-subscription" in src, (
        "POST /api/admin/billing/customers/{cust}/cancel-subscription endpoint missing — "
        "this is the admin destructive cancel primitive."
    )


def test_refund_charge_endpoint_exists():
    src = _read(_MAIN_PY)
    assert "/api/admin/billing/customers/{stripe_customer_id}/refund-charge" in src, (
        "POST /api/admin/billing/customers/{cust}/refund-charge endpoint missing — "
        "this is the admin destructive refund primitive."
    )


# ─── confirm_phrase contract ────────────────────────────────────────


def test_cancel_uses_distinct_confirm_phrase():
    """Anti-typo: cancel + refund must use DIFFERENT typed phrases so
    a half-typed confirmation can't accidentally trigger the wrong
    destructive action."""
    src = _read(_MAIN_PY)
    assert "CANCEL-CUSTOMER-SUBSCRIPTION" in src, (
        "Cancel endpoint missing confirm_phrase 'CANCEL-CUSTOMER-SUBSCRIPTION'."
    )
    assert "REFUND-CUSTOMER-CHARGE" in src, (
        "Refund endpoint missing confirm_phrase 'REFUND-CUSTOMER-CHARGE'."
    )


# ─── audit chain contract ───────────────────────────────────────────


def test_cancel_writes_admin_audit_log():
    src = _read(_MAIN_PY)
    assert "billing.subscription_cancel" in src, (
        "Cancel endpoint missing admin_audit_log INSERT with action=billing.subscription_cancel."
    )


def test_refund_writes_admin_audit_log():
    src = _read(_MAIN_PY)
    assert "billing.charge_refund" in src, (
        "Refund endpoint missing admin_audit_log INSERT."
    )


def test_actions_in_allowed_events():
    """ALLOWED_EVENTS must contain both new event types or attestation
    will raise PrivilegedAccessAttestationError."""
    src = _read(_PAA)
    for event in ("customer_subscription_cancel", "customer_subscription_refund"):
        assert f'"{event}"' in src, (
            f"ALLOWED_EVENTS missing {event!r}. Attestation will raise; "
            f"the audit chain has a gap for this action."
        )


def test_actions_require_email_actor():
    """create_privileged_access_attestation rejects actors without '@'.
    Both endpoints MUST validate email format BEFORE Stripe call."""
    src = _read(_MAIN_PY)
    # Both endpoints should have the email-format check
    assert src.count('admin billing requires authenticated email actor') >= 2, (
        "Admin billing endpoints don't validate email-format actor. "
        "Without this check, attestation raises with confusing 'no @' "
        "message and Stripe call may have already executed."
    )


def test_actions_require_reason_min_length():
    src = _read(_MAIN_PY)
    assert "AdminBillingActionRequest" in src, (
        "Pydantic request model missing"
    )
    # Find the model's reason field min_length
    import re
    m = re.search(
        r"class AdminBillingActionRequest.*?min_length\s*=\s*(\d+)",
        src,
        re.DOTALL,
    )
    assert m, "AdminBillingActionRequest model missing min_length on reason"
    assert int(m.group(1)) >= 20, (
        f"reason min_length is {m.group(1)} but privileged-action chain "
        f"requires ≥20 chars."
    )


# ─── idempotency contract ───────────────────────────────────────────


def test_actions_use_stripe_idempotency_key():
    """Operator panic-clicks twice MUST not double-cancel/refund. Stripe
    idempotency_key on the API call is the safety net."""
    src = _read(_MAIN_PY)
    assert "idempotency_key=idem_key" in src, (
        "Admin billing actions don't pass idempotency_key to Stripe API. "
        "Double-click during operator panic = duplicate refund/cancel."
    )


def test_idempotency_key_is_deterministic():
    """The key MUST be deterministic over (actor, customer, reason, day)
    so within-day double-clicks dedupe. Hashing prevents key from
    leaking PII to Stripe metadata."""
    src = _read(_MAIN_PY)
    assert "hashlib.sha256" in src, "idempotency_key must use SHA-256 hash"
    assert "datetime.now(timezone.utc).date()" in src, (
        "idempotency_key must include the current date so a same-action "
        "request the next day creates a new (legitimate) refund."
    )


# ─── attestation chain contract ─────────────────────────────────────


def test_actions_call_create_privileged_access_attestation():
    """Each action MUST call create_privileged_access_attestation so
    the customer's HIPAA chain captures the admin event."""
    src = _read(_MAIN_PY)
    # Count occurrences in main.py — should appear in both endpoints
    assert src.count("create_privileged_access_attestation") >= 4, (
        "Admin billing endpoints don't call create_privileged_access_"
        "attestation. The customer's chain has a gap for the admin event."
    )


# ─── adversarial round-table fixes (B-1, D-1) ───────────────────────


def test_refund_verifies_charge_belongs_to_customer():
    """B-1 (Brian, P1): refund endpoint must verify charge.customer
    matches stripe_customer_id from URL BEFORE calling Refund.create.
    Without this check, an operator typo refunds the wrong customer's
    charge while writing audit_log + Ed25519 attestation against the
    URL's site_id — silently misattributing the financial event."""
    src = _read(_MAIN_PY)
    assert "stripe.Charge.retrieve(request.charge_id)" in src, (
        "Refund endpoint missing stripe.Charge.retrieve() ownership check. "
        "URL stripe_customer_id MUST be verified to own the charge_id "
        "before Stripe.Refund.create() executes."
    )
    assert "charge_customer != stripe_customer_id" in src, (
        "Refund endpoint missing charge.customer != URL.customer guard."
    )


def test_actions_use_execute_with_retry():
    """D-1 (Diana, P1): all SQLAlchemy queries must use execute_with_retry
    per CLAUDE.md PgBouncer rule. Raw db.execute() raises
    DuplicatePreparedStatementError on PgBouncer rotation."""
    src = _read(_MAIN_PY)
    # Find the cancel and refund endpoint blocks; ensure they import
    # execute_with_retry at least twice (once per endpoint).
    assert src.count(
        "from dashboard_api.shared import async_session, execute_with_retry"
    ) >= 2, (
        "Admin billing endpoints don't import execute_with_retry. "
        "Raw db.execute() through PgBouncer fails intermittently."
    )

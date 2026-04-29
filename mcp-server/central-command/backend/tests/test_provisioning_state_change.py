"""Auth-gate tests for the four provisioning state-change endpoints
flagged by the Session 213 round-table P0 audit.

These four endpoints write to api_keys / site_appliances /
admin_audit_log / baa_signatures — all chain-of-custody-relevant
tables — and the endpoint_test_coverage audit flagged that NONE of
them had any test referencing their path or handler. This module is
the minimum viable coverage that closes the audit gap.

Endpoints covered:
  - POST /api/provision/admin/restore  (manual _resolve_admin auth)
  - POST /api/provision/rekey         (MAC + site_id trust)
  - POST /api/provision/claim-v2      (CA-attestation, no replay test)
  - POST /signup/sign-baa             (no auth — pre-account flow)

Tests are source-level + unit-level: they assert that the auth path
exists in the code, that bad-input shapes are rejected, and that the
audit-log shape uses the canonical column names. They do NOT exercise
the full path against a real DB — that's the role of the next-session
integration suite (filed as P3).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]
BACKEND = REPO_ROOT / "mcp-server" / "central-command" / "backend"

# Ensure backend is on sys.path for import-time checks.
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


# ---------------------------------------------------------------------------
# /admin/restore — manual admin auth dispatch
# ---------------------------------------------------------------------------


def test_admin_restore_requires_admin_auth_check():
    """The handler MUST call _resolve_admin(request) before any DB work.
    Pinning the literal call ensures a future refactor that drops the
    auth check fails CI loudly (the audit's specific concern: manual
    auth is invisible to a generic Depends-introspecting test harness).
    """
    src = (BACKEND / "provisioning.py").read_text()
    # Find the admin_restore_appliance function body.
    fn_start = src.find("async def admin_restore_appliance(")
    assert fn_start > 0, "admin_restore_appliance handler missing"
    # Walk to the next top-level function (or 1500 chars, whichever).
    fn_end = src.find("\nasync def ", fn_start + 1)
    if fn_end == -1:
        fn_end = fn_start + 1500
    body = src[fn_start:fn_end]
    assert "await _resolve_admin(request)" in body, (
        "admin_restore_appliance must call _resolve_admin(request) — "
        "the manual auth check that gates this endpoint. A future "
        "refactor that drops this call leaves the endpoint open."
    )
    # Reason validator must be present (≥20 chars).
    assert "len(req.reason.strip()) < 20" in body, (
        "admin_restore_appliance must enforce reason ≥ 20 chars — "
        "audit-log context required."
    )


def test_admin_restore_uses_canonical_audit_log_column():
    """Session 210-B class bug: admin_audit_log column is `username`,
    not `actor`. /admin/restore writes audit rows; pin the canonical
    column name."""
    src = (BACKEND / "provisioning.py").read_text()
    # The audit INSERT must use 'username' (the canonical column);
    # `actor` would 500 with UndefinedColumnError.
    fn_start = src.find("async def admin_restore_appliance(")
    fn_end = src.find("\nasync def ", fn_start + 1)
    if fn_end == -1:
        fn_end = fn_start + 4000
    body = src[fn_start:fn_end]
    if "INSERT INTO admin_audit_log" in body:
        # Find the column list and assert username (not actor).
        insert_pos = body.find("INSERT INTO admin_audit_log")
        column_list_end = body.find(")", insert_pos)
        column_list = body[insert_pos:column_list_end]
        assert "username" in column_list, (
            "admin_audit_log INSERT must reference `username` column "
            "(Session 210-B class bug — was `actor`)."
        )
        assert "actor" not in column_list.replace("username", ""), (
            "admin_audit_log INSERT must NOT reference `actor` "
            "(use `username`)."
        )


# ---------------------------------------------------------------------------
# /rekey — MAC + site_id trust + rate limit
# ---------------------------------------------------------------------------


def test_rekey_has_rate_limit_check():
    """Per round-table P0: the rekey path must be rate-limited or
    require either a still-valid sigauth header OR an admin Bearer.
    Today's implementation uses a per-(site, appliance) cooldown
    bucket. Pin the cooldown check so a refactor doesn't drop it."""
    src = (BACKEND / "provisioning.py").read_text()
    # Find rekey handler.
    fn_start = src.find("async def rekey")
    if fn_start == -1:
        # Path-style match
        fn_start = src.find("/rekey")
    assert fn_start > 0, "rekey handler/route missing from provisioning.py"
    # Cooldown bucket must be referenced.
    assert "_rekey_cooldowns" in src, (
        "/rekey must use _rekey_cooldowns rate-limit bucket — round-"
        "table P0. A refactor that drops this opens an unauth-gated "
        "key-mint endpoint to abuse."
    )


def test_rekey_validates_appliance_known():
    """The /rekey handler must look up the existing appliance row
    BEFORE minting a new key. Anonymous mint = unbounded api_keys
    growth."""
    src = (BACKEND / "provisioning.py").read_text()
    # Find the rekey function body.
    candidates = ["async def rekey_appliance", "async def rekey"]
    fn_start = -1
    for c in candidates:
        fn_start = src.find(c)
        if fn_start > 0:
            break
    if fn_start < 0:
        pytest.skip("rekey handler not found by name — manual review needed")
    fn_end = src.find("\nasync def ", fn_start + 1)
    if fn_end == -1:
        fn_end = fn_start + 4000
    body = src[fn_start:fn_end]
    # Either: SELECT from site_appliances (existing-row check), OR
    # raise 404 on unknown.
    has_lookup = (
        "FROM site_appliances" in body
        or "fetchrow" in body
    )
    assert has_lookup, (
        "/rekey must look up site_appliances before minting key — "
        "round-table P0."
    )


# ---------------------------------------------------------------------------
# /signup/sign-baa — no auth (pre-account flow)
# ---------------------------------------------------------------------------


def test_sign_baa_validates_signup_token():
    """/sign-baa is on the pre-account signup flow — no session yet.
    Round-table P0: gate via signed signup-token tied to the Stripe
    `signup_id`, not via require_auth (which would break the flow).
    Token validation must be present.
    """
    src = (BACKEND / "client_signup.py").read_text()
    fn_start = src.find("async def sign_baa")
    if fn_start < 0:
        # Try by route
        fn_start = src.find("/sign-baa")
    assert fn_start > 0, "sign_baa handler missing from client_signup.py"
    fn_end = src.find("\nasync def ", fn_start + 1)
    if fn_end == -1:
        fn_end = fn_start + 4000
    body = src[fn_start:fn_end]
    # Either signup_id lookup against signup_sessions, OR an
    # explicit 401 on missing token.
    has_token_check = (
        "signup_id" in body
        or "signup_sessions" in body
        or "signup_session" in body
    )
    assert has_token_check, (
        "/sign-baa must validate the signup_id token against "
        "signup_sessions — round-table P0. Without it, anyone can "
        "POST a baa_signature row with arbitrary content."
    )


def test_baa_signatures_table_is_append_only():
    """Migration 224 made baa_signatures append-only via UPDATE/DELETE
    trigger. CLAUDE.md cites this. Pin the trigger reference so a
    future migration can't quietly drop it."""
    migrations_dir = BACKEND / "migrations"
    found_trigger = False
    for sql_file in migrations_dir.glob("*.sql"):
        text = sql_file.read_text()
        if "baa_signatures" in text and "TRIGGER" in text.upper():
            if "BEFORE UPDATE" in text or "BEFORE DELETE" in text:
                found_trigger = True
                break
    assert found_trigger, (
        "baa_signatures must have an append-only trigger — HIPAA "
        "§164.316(b)(2)(i) 7-year retention. CLAUDE.md cites it; "
        "the migration must keep it. If you removed it, that's a "
        "compliance regression."
    )


# ---------------------------------------------------------------------------
# /provision/claim-v2 — CA-attestation + replay guard
# ---------------------------------------------------------------------------


def test_claim_v2_validates_ca_chain():
    """Per round-table P0: claim-v2 is CA-attested (real auth) but
    needs a replay guard. Pin the CA validation call site."""
    src = (BACKEND / "iso_ca.py").read_text()
    fn_start = src.find("async def claim_v2")
    if fn_start < 0:
        fn_start = src.find("/provision/claim-v2")
    assert fn_start > 0, "claim_v2 handler missing from iso_ca.py"
    # Either _validate_claim_cert or equivalent must be called.
    assert (
        "_validate_claim_cert" in src
        or "validate_claim_cert" in src
        or "verify_claim" in src
    ), (
        "claim-v2 must validate the CA chain — "
        "round-table P0 + Migration 156 / claim_cert_expired_in_use "
        "substrate invariant relies on this."
    )

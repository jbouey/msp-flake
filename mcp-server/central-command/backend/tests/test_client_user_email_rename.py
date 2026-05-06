"""Tests for client_user email rename (task #23, mig 277).

Round-table 2026-05-05 (.agent/plans/22-client-user-email-rename-roundtable-2026-05-05.md).
Source-level + lockstep tests pin the contract; behavior tests run in
CI against prod-mirror DB.
"""
from __future__ import annotations

import ast
import pathlib

import pytest


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND.parent.parent.parent
_MAIN_PY = _REPO_ROOT / "mcp-server" / "main.py"
_MIG_DIR = _BACKEND / "migrations"


def _read(p: pathlib.Path) -> str:
    return p.read_text()


def _find_function(src: str, name: str) -> str:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                return ast.get_source_segment(src, node, padded=False) or ""
    return ""


# ─── Migration 277 contract ───────────────────────────────────────


def test_migration_277_present():
    mig = _MIG_DIR / "277_client_user_email_rename.sql"
    assert mig.exists()


def test_migration_277_partner_toggle_default_true():
    """Maya enterprise-grade-default posture (round-table 2026-05-05):
    auto_provision must default true so a partner who forgets to
    configure doesn't strand every customer they onboard."""
    src = _read(_MIG_DIR / "277_client_user_email_rename.sql")
    assert "auto_provision_owner_on_signup" in src
    assert "BOOLEAN NOT NULL DEFAULT true" in src


def test_migration_277_creates_email_change_log():
    src = _read(_MIG_DIR / "277_client_user_email_rename.sql")
    assert "CREATE TABLE IF NOT EXISTS client_user_email_change_log" in src
    for col in [
        "client_user_id", "client_org_id", "old_email", "new_email",
        "changed_at", "changed_by_kind", "changed_by_email",
        "reason", "attestation_bundle_id",
    ]:
        assert col in src, f"mig 277 missing column `{col}`"


def test_migration_277_actor_kind_check():
    src = _read(_MIG_DIR / "277_client_user_email_rename.sql")
    assert "changed_by_kind IN ('self', 'partner', 'substrate')" in src


def test_migration_277_friction_asymmetry_in_check():
    """Round-table friction ladder: self ≥0, partner ≥20, substrate ≥40."""
    src = _read(_MIG_DIR / "277_client_user_email_rename.sql")
    # CHECK encodes the asymmetry.
    assert "changed_by_kind = 'self'" in src
    assert "LENGTH(reason) >= 20" in src
    assert "LENGTH(reason) >= 40" in src


def test_migration_277_append_only():
    """No DELETE, no UPDATE on the audit ledger."""
    src = _read(_MIG_DIR / "277_client_user_email_rename.sql")
    assert "prevent_email_change_log_deletion" in src
    assert "prevent_email_change_log_mutation" in src
    assert "BEFORE DELETE ON client_user_email_change_log" in src
    assert "BEFORE UPDATE ON client_user_email_change_log" in src


# ─── ALLOWED_EVENTS lockstep ──────────────────────────────────────


def test_four_new_events_in_allowed_events():
    src = _read(_BACKEND / "privileged_access_attestation.py")
    for ev in [
        "client_user_email_changed_by_self",
        "client_user_email_changed_by_partner",
        "client_user_email_changed_by_substrate",
        "client_user_email_change_reversed",
    ]:
        assert f'"{ev}"' in src, (
            f"ALLOWED_EVENTS missing `{ev}` — attestation create "
            f"will reject at runtime."
        )


def test_four_new_events_in_lockstep_test():
    src = _read(_BACKEND / "tests/test_privileged_chain_allowed_events_lockstep.py")
    for ev in [
        "client_user_email_changed_by_self",
        "client_user_email_changed_by_partner",
        "client_user_email_changed_by_substrate",
        "client_user_email_change_reversed",
    ]:
        assert f'"{ev}"' in src


def test_email_rename_events_NOT_in_privileged_order_types():
    fleet_cli = _BACKEND / "fleet_cli.py"
    if not fleet_cli.exists():
        pytest.skip("fleet_cli.py not in this checkout slice")
    src = fleet_cli.read_text()
    for ev in [
        "client_user_email_changed_by_self",
        "client_user_email_changed_by_partner",
        "client_user_email_changed_by_substrate",
    ]:
        assert f'"{ev}"' not in src, (
            f"fleet_cli.PRIVILEGED_ORDER_TYPES must NOT contain `{ev}` — "
            f"these are admin-API events, not fleet_orders."
        )


# ─── Module surface ───────────────────────────────────────────────


def test_email_rename_module_present():
    assert (_BACKEND / "client_user_email_rename.py").exists()


def test_three_routers_registered_in_main():
    src = _read(_MAIN_PY)
    assert "email_rename_self_router" in src
    assert "email_rename_partner_router" in src
    assert "email_rename_substrate_router" in src
    assert "app.include_router(email_rename_self_router)" in src
    assert "app.include_router(email_rename_partner_router)" in src
    assert "app.include_router(email_rename_substrate_router)" in src


# ─── Endpoint friction ────────────────────────────────────────────


def test_partner_reason_length_min_20():
    src = _read(_BACKEND / "client_user_email_rename.py")
    assert "MIN_REASON_PARTNER = 20" in src


def test_substrate_reason_length_min_40():
    src = _read(_BACKEND / "client_user_email_rename.py")
    assert "MIN_REASON_SUBSTRATE = 40" in src


def test_partner_confirm_phrase():
    src = _read(_BACKEND / "client_user_email_rename.py")
    assert 'confirm_phrase != "CHANGE-CLIENT-EMAIL"' in src


def test_substrate_confirm_phrase():
    src = _read(_BACKEND / "client_user_email_rename.py")
    assert 'confirm_phrase != "SUBSTRATE-CLIENT-EMAIL-CHANGE"' in src


# ─── Steve M1: same-txn session invalidation ─────────────────────


def test_session_invalidation_in_rename_txn():
    """Steve M1 (round-table): privilege-retention defense — every
    rename MUST DELETE FROM client_sessions in the SAME txn as the
    UPDATE client_users. Otherwise an attacker who triggered the
    rename keeps their pre-rename session."""
    src = _read(_BACKEND / "client_user_email_rename.py")
    fn = _find_function(src, "_do_rename_in_txn")
    assert fn
    assert "DELETE FROM client_sessions WHERE user_id" in fn, (
        "Steve M1 violation: session invalidation missing or out-of-txn"
    )
    # And the UPDATE is also in the same function:
    assert "UPDATE client_users" in fn
    assert "SET email = $1" in fn


# ─── Steve M2: dual-notification ─────────────────────────────────


def test_dual_notification_old_and_new():
    src = _read(_BACKEND / "client_user_email_rename.py")
    fn = _find_function(src, "_send_dual_notification")
    assert fn
    # Both addresses receive a send_email call.
    assert fn.count("send_email(") == 2
    assert "old_email" in fn and "new_email" in fn
    # Maya P1-2 anti-phishing-quotation rule: actor email NOT inline.
    # The function MUST mention this rule (so future edits don't
    # silently regress).
    assert "phishing" in fn.lower() or "actor's identity" in fn.lower()


def test_dual_notification_called_from_all_three_endpoints():
    src = _read(_BACKEND / "client_user_email_rename.py")
    for handler in [
        "self_confirm_email_change",
        "partner_change_client_email",
        "substrate_change_client_email",
    ]:
        fn = _find_function(src, handler)
        assert fn, f"handler `{handler}` not found"
        assert "_send_dual_notification(" in fn, (
            f"{handler} skips dual-notification — Steve M2 violation"
        )


# ─── Steve M3: rate limit ────────────────────────────────────────


def test_rate_limit_constants():
    src = _read(_BACKEND / "client_user_email_rename.py")
    assert "RATE_LIMIT_WINDOW_DAYS = 30" in src
    assert "RATE_LIMIT_MAX_CHANGES = 3" in src


def test_rate_limit_called_from_all_three_endpoints():
    src = _read(_BACKEND / "client_user_email_rename.py")
    for handler in [
        "self_confirm_email_change",
        "partner_change_client_email",
        "substrate_change_client_email",
    ]:
        fn = _find_function(src, handler)
        assert "_check_rate_limit(" in fn, (
            f"{handler} skips Steve M3 rate-limit — would let an "
            f"attacker rotate emails past detection windows"
        )


# ─── Steve M4 + M5: interlocks ───────────────────────────────────


def test_interlock_helper_checks_both_owner_transfer_and_mfa_revocation():
    src = _read(_BACKEND / "client_user_email_rename.py")
    fn = _find_function(src, "_check_interlocks")
    assert fn
    assert "client_org_owner_transfer_requests" in fn
    assert "mfa_revocation_pending" in fn


def test_interlocks_called_from_all_three_endpoints():
    src = _read(_BACKEND / "client_user_email_rename.py")
    for handler in [
        "self_confirm_email_change",
        "partner_change_client_email",
        "substrate_change_client_email",
    ]:
        fn = _find_function(src, handler)
        assert "_check_interlocks(" in fn, (
            f"{handler} skips Steve M4/M5 interlocks — magic-link "
            f"redirect attack possible during in-flight owner-transfer "
            f"or MFA-revocation"
        )


# ─── Steve M6 / Maya P1: owner-rename severity P0 ───────────────


def test_owner_rename_severity_p0():
    """Maya P1 (round-table 2026-05-05): owner-role rename is highest
    blast radius — partner MUST receive P0-OWNER-RENAME tier alert."""
    src = _read(_BACKEND / "client_user_email_rename.py")
    fn = _find_function(src, "_severity_for_role")
    assert fn
    assert 'role == "owner"' in fn
    assert "P0-OWNER-RENAME" in fn


def test_owner_rename_severity_called_from_all_paths():
    """Each of the three handlers must distinguish owner from non-owner
    in the operator-alert severity. Two routes: either via _severity_for_role
    helper or via inline `role == "owner"` check."""
    src = _read(_BACKEND / "client_user_email_rename.py")
    for handler in [
        "self_confirm_email_change",
        "partner_change_client_email",
        "substrate_change_client_email",
    ]:
        fn = _find_function(src, handler)
        assert "P0-OWNER-RENAME" in fn, (
            f"{handler} doesn't escalate owner-role to P0 — Maya P1 "
            f"violation"
        )


# ─── Email collision check (Camila #1) ───────────────────────────


def test_email_collision_check_called_from_all_endpoints():
    """idx_client_users_email is GLOBALLY unique — must pre-flight."""
    src = _read(_BACKEND / "client_user_email_rename.py")
    for handler in [
        "self_confirm_email_change",
        "partner_change_client_email",
        "substrate_change_client_email",
    ]:
        fn = _find_function(src, handler)
        assert "_check_email_collision(" in fn, (
            f"{handler} skips collision check — would 500 with "
            f"UniqueViolationError instead of 409"
        )


# ─── Self-service magic-link confirm ─────────────────────────────


def test_self_service_uses_magic_link_to_NEW_address():
    """Self-service is the only path with a magic-link gate (user
    controls both mailboxes). Partner + substrate paths MUST NOT have
    this gate (would deadlock the recovery cases)."""
    src = _read(_BACKEND / "client_user_email_rename.py")
    self_fn = _find_function(src, "self_initiate_email_change")
    confirm_fn = _find_function(src, "self_confirm_email_change")
    assert self_fn and confirm_fn
    # The token is bound to new_email at hash-time so it can't be
    # swapped at confirm time.
    assert "email-confirm:" in self_fn
    # Token-only auth on the confirm endpoint (Maya P0-2 lesson from #19)
    assert "Depends(require_client_user)" not in confirm_fn, (
        "self-confirm endpoint must be token-only — same lesson as MFA "
        "restore (#19): the user might not be currently logged in."
    )


def test_partner_and_substrate_paths_have_no_magic_link_gate():
    """Brian's veto: any 'target-confirms-via-magic-link-to-NEW' gate
    on partner/substrate paths would deadlock recovery (the user does
    not yet control the new mailbox in the worst case)."""
    src = _read(_BACKEND / "client_user_email_rename.py")
    for handler in [
        "partner_change_client_email",
        "substrate_change_client_email",
    ]:
        fn = _find_function(src, handler)
        # No magic-token / confirm-link issuance in the partner/substrate
        # handlers themselves.
        assert "send_email(" not in fn, (
            f"{handler} sends email inline — should delegate to "
            f"_send_dual_notification post-txn"
        )


# ─── Self-service re-auth (Steve self-service M2) ────────────────


def test_self_initiate_requires_password_re_auth():
    """Self-service M2 (Steve): fresh password verification proves the
    session isn't compromised at the moment of action. Without it, a
    stolen session cookie could rotate the email out of the legitimate
    user's reach."""
    src = _read(_BACKEND / "client_user_email_rename.py")
    fn = _find_function(src, "self_initiate_email_change")
    assert "current_password" in fn
    assert "bcrypt.verify" in fn


# ─── Substrate actor-email is named human ────────────────────────


def test_substrate_actor_must_be_named_human():
    """CLAUDE.md privileged-chain rule: actor MUST be a named human
    email — never log actor as `system`/`fleet-cli`/`admin`."""
    src = _read(_BACKEND / "client_user_email_rename.py")
    fn = _find_function(src, "substrate_change_client_email")
    assert "actor_email must be a named human admin" in fn or \
           "named human" in fn


# ─── Anchor-namespace convention ─────────────────────────────────


def test_anchor_uses_org_primary_site_id_with_synthetic_fallback():
    """Round-table 32 closure: anchor resolver moved to chain_attestation.py.
    client_user_email_rename imports it under the legacy name."""
    src = _read(_BACKEND / "client_user_email_rename.py")
    assert "resolve_client_anchor_site_id" in src, (
        "client_user_email_rename must import the canonical anchor "
        "resolver (round-table 32 DRY closure)."
    )
    canonical = _read(_BACKEND / "chain_attestation.py")
    fn = _find_function(canonical, "resolve_client_anchor_site_id")
    assert fn
    assert "ORDER BY created_at ASC LIMIT 1" in fn
    assert 'f"client_org:{org_id}"' in fn


# ─── Operator-alert chain-gap escalation pattern ─────────────────


def test_chain_gap_escalation_uniform():
    """Round-table 32: chain-gap literals live in chain_attestation.py
    post-DRY closure. client_user_email_rename's
    _send_operator_visibility shim delegates."""
    canonical = _read(_BACKEND / "chain_attestation.py")
    assert "P0-CHAIN-GAP" in canonical
    assert "ATTESTATION-MISSING" in canonical
    src = _read(_BACKEND / "client_user_email_rename.py")
    assert "_send_chain_aware_operator_alert" in src, (
        "client_user_email_rename must delegate operator alerts to "
        "chain_attestation post-round-table-32 DRY closure."
    )


# ─── Auto-provision-on-signup hook ───────────────────────────────


def test_auto_provision_helper_present():
    src = _read(_BACKEND / "client_user_email_rename.py")
    assert "async def auto_provision_owner_on_signup" in src


def test_auto_provision_honors_partner_toggle():
    src = _read(_BACKEND / "client_user_email_rename.py")
    fn = _find_function(src, "auto_provision_owner_on_signup")
    assert fn
    assert "auto_provision_owner_on_signup" in fn  # column read
    assert "FROM partners WHERE id" in fn
    # Skip path
    assert "auto_provision_skipped" in fn


def test_auto_provision_idempotent():
    src = _read(_BACKEND / "client_user_email_rename.py")
    fn = _find_function(src, "auto_provision_owner_on_signup")
    # If a row already exists for (org, email), don't re-create.
    assert "SELECT id FROM client_users" in fn
    assert "WHERE client_org_id = $1::uuid" in fn


def test_auto_provision_wired_into_org_creation():
    """Auto-provision must fire from the org-creation path (org_management.py)
    so newly-onboarded customers get a login automatically. Best-effort:
    org-create still succeeds if auto-provision blows up — operator
    falls back to substrate email-rename."""
    src = _read(_BACKEND / "org_management.py")
    assert "from .client_user_email_rename import" in src
    assert "auto_provision_owner_on_signup" in src
    assert "auto_provision_owner_failed" in src  # log on failure


# ─── Append-only ledger writes ───────────────────────────────────


def test_rename_writes_to_email_change_log():
    src = _read(_BACKEND / "client_user_email_rename.py")
    fn = _find_function(src, "_do_rename_in_txn")
    assert "INSERT INTO client_user_email_change_log" in fn
    # Same txn as the UPDATE (caller is responsible for txn boundary).


def test_attestation_bundle_id_persisted_to_log_row():
    src = _read(_BACKEND / "client_user_email_rename.py")
    fn = _find_function(src, "_do_rename_in_txn")
    assert "UPDATE client_user_email_change_log" in fn
    assert "SET attestation_bundle_id = $2" in fn

"""Tests for MFA admin overrides (task #19, mig 276).

Source-level + lockstep tests pin the contract. Behavior tests run
against prod-mirror DB in CI.

Round-table 2026-05-05 verdict: 5/5 APPROVE_DESIGN with Maya 2nd-eye
verifying:
  - PARITY (toggle, reset, revoke, restore on both portals)
  - DIFFERENT_SHAPE (revoke higher friction than reset; client owner-
    only vs partner admin-only is the role asymmetry both portals
    converge to: highest-privilege role on each side)
  - ANCHOR_NAMESPACE (client_org/partner_org synthetic same as the
    rest of session 216)
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


# ─── Migration 276 contract ───────────────────────────────────────


def test_migration_276_present():
    mig = _MIG_DIR / "276_mfa_admin_overrides.sql"
    assert mig.exists()


def test_migration_276_creates_mfa_revocation_pending():
    src = _read(_MIG_DIR / "276_mfa_admin_overrides.sql")
    assert "CREATE TABLE IF NOT EXISTS mfa_revocation_pending" in src
    for col in [
        "target_user_id", "user_kind", "scope_id", "target_email",
        "revoked_by_email", "revoked_at", "expires_at",
        "reversal_token_hash", "reason", "restored_at",
        "restored_by_email", "attestation_bundle_ids",
    ]:
        assert col in src, f"mig 276 missing column `{col}`"


def test_migration_276_user_kind_check():
    """user_kind discriminator MUST be checked at schema level so a
    bad INSERT doesn't write garbage to the audit table."""
    src = _read(_MIG_DIR / "276_mfa_admin_overrides.sql")
    assert "CHECK (user_kind IN ('client_user', 'partner_user'))" in src


def test_migration_276_reason_min_40():
    """Steve P3 mit B: revoke reason ≥40ch enforced at schema level."""
    src = _read(_MIG_DIR / "276_mfa_admin_overrides.sql")
    assert "LENGTH(reason) >= 40" in src
    assert "chk_mfa_revocation_reason_length" in src


def test_migration_276_one_pending_per_user_unique_index():
    """Partial unique index gates pending revocations.

    NOTE: Postgres index predicates require IMMUTABLE functions.
    NOW() is STABLE → cannot be in WHERE on CREATE INDEX. Index
    covers restored_at IS NULL only; application + sweep loop
    filter on expires_at > NOW() at query time. CI-discovered
    on prod deploy 2026-05-05 (commit 069a8da3); fix amended
    here forward-only.
    """
    src = _read(_MIG_DIR / "276_mfa_admin_overrides.sql")
    assert "idx_mfa_revocation_one_pending_per_user" in src
    # Must be a partial index on restored_at IS NULL (without NOW()).
    assert "WHERE restored_at IS NULL" in src
    # Negative: NOW() must NOT appear inside any CREATE INDEX block
    # (PG would reject the migration with InvalidObjectDefinitionError).
    # Strip `-- comment` lines before the scan so documentary explanations
    # of why-NOW-is-banned don't false-positive.
    stripped = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("--")
    )
    create_index_blocks = stripped.split("CREATE")
    for block in create_index_blocks:
        if block.lstrip().startswith(("INDEX", "UNIQUE INDEX")):
            assert "NOW()" not in block, (
                "CREATE INDEX block contains NOW() in predicate — "
                "PG rejects (functions in index predicate must be "
                "marked IMMUTABLE). Use restored_at IS NULL only."
            )


def test_migration_276_append_only():
    src = _read(_MIG_DIR / "276_mfa_admin_overrides.sql")
    assert "prevent_mfa_revocation_deletion" in src
    assert "BEFORE DELETE ON mfa_revocation_pending" in src


# ─── ALLOWED_EVENTS lockstep ──────────────────────────────────────


def test_eight_new_events_in_allowed_events():
    src = _read(_BACKEND / "privileged_access_attestation.py")
    for ev in [
        "client_org_mfa_policy_changed",
        "partner_mfa_policy_changed",
        "client_user_mfa_reset",
        "partner_user_mfa_reset",
        "client_user_mfa_revoked",
        "partner_user_mfa_revoked",
        "client_user_mfa_revocation_reversed",
        "partner_user_mfa_revocation_reversed",
    ]:
        assert f'"{ev}"' in src, (
            f"ALLOWED_EVENTS missing `{ev}` — attestation create "
            f"will reject at runtime."
        )


def test_eight_new_events_in_lockstep_test():
    src = _read(_BACKEND / "tests/test_privileged_chain_allowed_events_lockstep.py")
    for ev in [
        "client_org_mfa_policy_changed",
        "partner_mfa_policy_changed",
        "client_user_mfa_reset",
        "partner_user_mfa_reset",
        "client_user_mfa_revoked",
        "partner_user_mfa_revoked",
        "client_user_mfa_revocation_reversed",
        "partner_user_mfa_revocation_reversed",
    ]:
        assert f'"{ev}"' in src


def test_mfa_events_NOT_in_privileged_order_types():
    fleet_cli = _BACKEND / "fleet_cli.py"
    if not fleet_cli.exists():
        pytest.skip("fleet_cli.py not in this checkout slice")
    src = fleet_cli.read_text()
    for ev in [
        "client_org_mfa_policy_changed",
        "partner_mfa_policy_changed",
        "client_user_mfa_reset",
        "partner_user_mfa_reset",
        "client_user_mfa_revoked",
        "partner_user_mfa_revoked",
        "client_user_mfa_revocation_reversed",
        "partner_user_mfa_revocation_reversed",
    ]:
        assert f'"{ev}"' not in src


# ─── Module surface ──────────────────────────────────────────────


def test_mfa_admin_module_present():
    assert (_BACKEND / "mfa_admin.py").exists()


def test_routers_registered_in_main():
    src = _read(_MAIN_PY)
    assert "mfa_admin_client_router" in src
    assert "mfa_admin_partner_router" in src
    assert "app.include_router(mfa_admin_client_router)" in src
    assert "app.include_router(mfa_admin_partner_router)" in src


def test_sweep_loop_registered():
    src = _read(_MAIN_PY)
    assert '("mfa_revocation_expiry_sweep", _mfa_revocation_expiry_sweep_loop)' in src

    bg = _read(_BACKEND / "bg_heartbeat.py")
    assert '"mfa_revocation_expiry_sweep": 60' in bg

    loop_src = _read(_BACKEND / "mfa_admin.py")
    assert "await asyncio.sleep(60)" in loop_src
    assert 'record_heartbeat("mfa_revocation_expiry_sweep")' in loop_src


# ─── Endpoint friction ────────────────────────────────────────────


def test_revoke_endpoints_require_confirm_phrase():
    """Both portals' revoke endpoints require typed CONFIRM-MFA-REVOKE
    (anti-misclick on the highest-friction action)."""
    src = _read(_BACKEND / "mfa_admin.py")
    assert 'confirm_phrase != "CONFIRM-MFA-REVOKE"' in src


def test_revoke_reason_min_40_chars():
    """Steve P3 mit B: reason ≥40ch on revoke (vs ≥20 elsewhere)."""
    src = _read(_BACKEND / "mfa_admin.py")
    assert "MIN_REVOKE_REASON_CHARS = 40" in src
    assert "min_length=MIN_REVOKE_REASON_CHARS" in src


def test_client_revoke_owner_only_partner_revoke_admin_only():
    """Maya's role-elevation parity: each portal's revoke gates at
    the highest-privilege role available."""
    src = _read(_BACKEND / "mfa_admin.py")
    client_revoke = _find_function(src, "client_user_mfa_revoke")
    partner_revoke = _find_function(src, "partner_user_mfa_revoke")
    assert client_revoke and partner_revoke
    assert "require_client_owner" in client_revoke, (
        "client revoke must be owner-only (highest client privilege)"
    )
    assert 'require_partner_role("admin")' in partner_revoke, (
        "partner revoke must be admin-only (highest partner privilege)"
    )


def test_reset_endpoints_admin_or_owner():
    """Reset is lower-friction than revoke. Client: owner OR admin
    (require_client_admin includes both). Partner: admin (only role
    that can reset other users)."""
    src = _read(_BACKEND / "mfa_admin.py")
    client_reset = _find_function(src, "client_user_mfa_reset")
    partner_reset = _find_function(src, "partner_user_mfa_reset")
    assert "require_client_admin" in client_reset
    assert 'require_partner_role("admin")' in partner_reset


# ─── Restore mechanism ───────────────────────────────────────────


def test_restore_token_hashed_not_stored_plaintext():
    src = _read(_BACKEND / "mfa_admin.py")
    assert "_hash_token(" in src
    assert "secrets.token_urlsafe(32)" in src, (
        "restore token not generated with cryptographic randomness"
    )
    # Token cleared after restoration (SQL UPDATE sets it to '' or NULL)
    assert "reversal_token_hash = ''" in src or "reversal_token_hash = NULL" in src


def test_restore_is_token_only_auth():
    """Maya P0-2 (round-table 2026-05-05): restore endpoints MUST be
    token-only — no Depends(require_client_user) / Depends(require_partner).
    A Depends-gated restore is unreachable on `mfa_required=true` orgs
    because the target's MFA was just cleared. The 256-bit token (delivered
    to target_email at revoke-time) IS the authentication primitive.
    """
    src = _read(_BACKEND / "mfa_admin.py")
    client_restore = _find_function(src, "client_user_mfa_restore")
    partner_restore = _find_function(src, "partner_user_mfa_restore")
    assert client_restore and partner_restore
    for fn_src, name in [(client_restore, "client"), (partner_restore, "partner")]:
        assert "Depends(require_client_user)" not in fn_src, (
            f"{name} restore is Depends-gated — see Maya P0-2"
        )
        assert "Depends(require_partner)" not in fn_src, (
            f"{name} restore is Depends-gated — see Maya P0-2"
        )
        assert "TOKEN-ONLY" in fn_src, (
            f"{name} restore must document its token-only contract"
        )


def test_restore_validates_window():
    src = _read(_BACKEND / "mfa_admin.py")
    assert "Restoration window expired" in src


# ─── Owner-transfer interlock (Steve mit D) ──────────────────────


def test_has_active_mfa_revocation_predicate_present():
    src = _read(_BACKEND / "mfa_admin.py")
    assert "async def has_active_mfa_revocation" in src
    assert "user_kind = $1" in src
    assert "scope_id = $2::uuid" in src


def test_client_owner_transfer_calls_interlock():
    src = _read(_BACKEND / "client_owner_transfer.py")
    fn_src = _find_function(src, "initiate_owner_transfer")
    assert fn_src
    assert "has_active_mfa_revocation" in fn_src, (
        "client owner-transfer initiate must check the MFA revocation "
        "interlock — Steve mit D anti-race posture."
    )
    assert '"client_user"' in fn_src


def test_partner_admin_transfer_calls_interlock():
    src = _read(_BACKEND / "partner_admin_transfer.py")
    fn_src = _find_function(src, "initiate_partner_admin_transfer")
    assert fn_src
    assert "has_active_mfa_revocation" in fn_src
    assert '"partner_user"' in fn_src


# ─── Anchor namespace ────────────────────────────────────────────


def test_partner_events_use_partner_org_namespace():
    src = _read(_BACKEND / "mfa_admin.py")
    assert 'f"partner_org:{partner_id}"' in src


def test_client_events_use_org_primary_site_id():
    src = _read(_BACKEND / "mfa_admin.py")
    assert "_resolve_client_anchor_site_id" in src
    helper = _find_function(src, "_resolve_client_anchor_site_id")
    assert "ORDER BY created_at ASC LIMIT 1" in helper


# ─── Operator-alert chain-gap escalation ─────────────────────────


def test_chain_gap_escalation_uniform():
    src = _read(_BACKEND / "mfa_admin.py")
    assert "P0-CHAIN-GAP" in src
    assert "ATTESTATION-MISSING" in src


def test_revoke_severity_p0_class():
    """Revoke is incident-response-class even when legitimate
    (Steve mit B). Operator should always see P0-MFA-REVOKE on revoke,
    not the lower-tier severity used elsewhere."""
    src = _read(_BACKEND / "mfa_admin.py")
    assert "P0-MFA-REVOKE" in src


# ─── Banned legal language ───────────────────────────────────────


def test_banned_words_absent_from_user_facing_email():
    """Per CLAUDE.md Session 199, the user-facing revoke email must
    not contain banned words. Body lives inside _send_revoke_email_to_target."""
    src = _read(_BACKEND / "mfa_admin.py")
    fn_src = _find_function(src, "_send_revoke_email_to_target")
    assert fn_src
    banned = ["ensures", "prevents", "protects", "guarantees",
              "audit-ready", "PHI never leaves", "100%"]
    for word in banned:
        assert word not in fn_src, (
            f"revoke email body contains banned word `{word}`"
        )

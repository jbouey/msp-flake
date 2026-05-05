"""Tests for partner_admin_transfer state machine (Maya round-table
2026-05-04, item B).

Mirror of test_owner_transfer.py but for the simpler partner-side
shape: 2-state (pending → completed/canceled/expired), no cooling-off,
no magic-link, no target-creation. Operator-class friction.

Source-level + lockstep tests pin the contract; full DB-live tests
run in CI.
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


# ─── Migration 274 contract ───────────────────────────────────────


def test_migration_274_present():
    mig = _MIG_DIR / "274_partner_admin_transfer.sql"
    assert mig.exists(), (
        "migrations/274_partner_admin_transfer.sql missing — partner-"
        "admin transfer data layer unshipped."
    )


def test_migration_274_creates_table_with_required_columns():
    src = _read(_MIG_DIR / "274_partner_admin_transfer.sql")
    assert "CREATE TABLE IF NOT EXISTS partner_admin_transfer_requests" in src
    for col in [
        "partner_id", "initiated_by_user_id", "target_email",
        "target_user_id", "status", "reason", "completed_at",
        "canceled_at", "canceled_by", "cancel_reason",
        "expires_at", "attestation_bundle_ids",
    ]:
        assert col in src, f"mig 274 missing column `{col}`"


def test_migration_274_no_cooling_off_column():
    """Maya design: NO cooling-off. The mig must NOT carry a
    cooling_off_until column — that would imply the field exists in
    the schema and the application could later reintroduce the
    delay. Operator class = no friction we don't need."""
    src = _read(_MIG_DIR / "274_partner_admin_transfer.sql")
    assert "cooling_off_until" not in src, (
        "Migration 274 reintroduced cooling_off_until — Maya's design "
        "specifically EXCLUDED this for the operator-class flow."
    )


def test_migration_274_one_pending_per_partner_unique_index():
    src = _read(_MIG_DIR / "274_partner_admin_transfer.sql")
    assert "idx_partner_admin_transfer_one_pending" in src
    assert "WHERE status = 'pending_target_accept'" in src, (
        "partial unique index must gate exactly the pending status."
    )


def test_migration_274_min_one_admin_trigger():
    """Brian non-negotiable, partner side: schema-level last-line
    defense against zero-admin partner state."""
    src = _read(_MIG_DIR / "274_partner_admin_transfer.sql")
    assert "CREATE OR REPLACE FUNCTION enforce_min_one_admin_per_partner" in src
    assert "trg_enforce_min_one_admin_per_partner" in src
    assert "BEFORE UPDATE OR DELETE ON partner_users" in src, (
        "trigger must fire on UPDATE + DELETE — both demote paths."
    )


def test_migration_274_table_is_append_only():
    src = _read(_MIG_DIR / "274_partner_admin_transfer.sql")
    assert "prevent_partner_admin_transfer_deletion" in src
    assert "BEFORE DELETE ON partner_admin_transfer_requests" in src


# ─── Module surface contract ─────────────────────────────────────


def test_partner_admin_transfer_module_present():
    assert (_BACKEND / "partner_admin_transfer.py").exists(), (
        "backend module missing — endpoint surface unshipped."
    )


def test_partner_admin_transfer_router_registered_in_main():
    src = _read(_MAIN_PY)
    assert "from dashboard_api.partner_admin_transfer import partner_admin_transfer_router" in src
    assert "app.include_router(partner_admin_transfer_router)" in src


def test_partner_admin_transfer_sweep_loop_registered():
    src = _read(_MAIN_PY)
    assert '("partner_admin_transfer_sweep", _partner_admin_transfer_sweep_loop)' in src

    bg_src = _read(_BACKEND / "bg_heartbeat.py")
    assert '"partner_admin_transfer_sweep": 60' in bg_src

    loop_src = _read(_BACKEND / "partner_admin_transfer.py")
    assert "await asyncio.sleep(60)" in loop_src
    assert 'record_heartbeat("partner_admin_transfer_sweep")' in loop_src


def test_endpoints_have_correct_friction():
    src = _read(_BACKEND / "partner_admin_transfer.py")
    assert "MIN_REASON_CHARS = 20" in src
    assert "min_length=MIN_REASON_CHARS" in src
    # Two distinct confirm_phrases so a click-jacking attempt at one
    # endpoint can't replay-satisfy the other.
    assert 'confirm_phrase != "CONFIRM-PARTNER-ADMIN-TRANSFER"' in src
    assert 'confirm_phrase != "ACCEPT-PARTNER-ADMIN"' in src
    assert "Cannot transfer admin role to yourself" in src


def test_target_must_already_be_partner_user():
    """Maya design: NO target-creation flow. Target must be an
    existing partner_user with role!=admin in same partner_org."""
    src = _read(_BACKEND / "partner_admin_transfer.py")
    # Reject-not-a-partner_user check (substring tolerates Python's
    # adjacent-string-literal concatenation across line breaks)
    assert "is not a partner_user" in src, (
        "Endpoint must reject targets that are not already partner_users."
    )
    assert 'target_row["role"] == "admin"' in src, (
        "Endpoint must reject already-admin targets — would create "
        "a redundant transfer with no role change."
    )
    assert "Target is already an admin" in src, (
        "Operator-facing error message for already-admin case must "
        "be specific — generic 409 would be hard to triage."
    )


def test_role_swap_promotes_before_demoting():
    """Brian + Linda: 1-admin-min trigger means we MUST promote the
    new admin BEFORE demoting the old one. The trigger fires on the
    intermediate zero-admin state otherwise."""
    src = _read(_BACKEND / "partner_admin_transfer.py")
    fn_start = src.find("async def accept_partner_admin_transfer(")
    assert fn_start >= 0
    fn_body = src[fn_start:fn_start + 5000]
    # Find the two UPDATE statements
    promote_idx = fn_body.find("role = 'admin', updated_at")
    demote_idx = fn_body.find("role = 'tech', updated_at")
    assert promote_idx >= 0 and demote_idx >= 0
    assert promote_idx < demote_idx, (
        "Role swap must promote target to admin BEFORE demoting "
        "initiator. Otherwise zero-admin intermediate state fires the "
        "1-admin-min trigger from mig 274."
    )


def test_role_swap_immediate_no_cooling_off():
    """Maya design — NO cooling-off means accept = complete in the
    same transaction. The endpoint must NOT defer completion to the
    sweep loop; the sweep handles expiry only."""
    src = _read(_BACKEND / "partner_admin_transfer.py")
    fn_start = src.find("async def accept_partner_admin_transfer(")
    assert fn_start >= 0
    fn_body = src[fn_start:fn_start + 5000]
    assert "status = 'completed'" in fn_body, (
        "accept handler must mark status=completed immediately — no "
        "cooling-off-delayed completion."
    )


def test_attestation_chain_writes_per_state_transition():
    """All 4 event_types must appear at runtime call sites."""
    src = _read(_BACKEND / "partner_admin_transfer.py")
    tree = ast.parse(src)
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "event_type" and isinstance(kw.value, ast.Constant):
                if isinstance(kw.value.value, str):
                    found.add(kw.value.value)
    expected = {
        "partner_admin_transfer_initiated",
        "partner_admin_transfer_completed",
        "partner_admin_transfer_canceled",
        "partner_admin_transfer_expired",
    }
    missing = expected - found
    assert not missing, (
        f"event_types not wired at runtime call sites: {sorted(missing)}"
    )


# ─── Three-list lockstep ──────────────────────────────────────────


def test_four_event_types_in_allowed_events():
    src = _read(_BACKEND / "privileged_access_attestation.py")
    for ev in [
        "partner_admin_transfer_initiated",
        "partner_admin_transfer_completed",
        "partner_admin_transfer_canceled",
        "partner_admin_transfer_expired",
    ]:
        assert f'"{ev}"' in src, (
            f"ALLOWED_EVENTS missing `{ev}` — attestation create will "
            f"reject the event_type at runtime."
        )


def test_partner_admin_events_NOT_in_privileged_order_types():
    fleet_cli = _BACKEND / "fleet_cli.py"
    if not fleet_cli.exists():
        pytest.skip("fleet_cli.py not in this checkout slice")
    src = fleet_cli.read_text()
    for ev in [
        "partner_admin_transfer_initiated",
        "partner_admin_transfer_completed",
        "partner_admin_transfer_canceled",
        "partner_admin_transfer_expired",
    ]:
        assert f'"{ev}"' not in src, (
            f"fleet_cli.PRIVILEGED_ORDER_TYPES must NOT contain `{ev}` — "
            f"these are admin-API events, not fleet_orders."
        )


# ─── Anchor-site_id namespace ────────────────────────────────────


def test_attestation_uses_partner_org_namespace():
    src = _read(_BACKEND / "partner_admin_transfer.py")
    assert 'f"partner_org:{partner_id}"' in src, (
        "Partner attestation must anchor to partner_org:<id> "
        "namespace so auditor kit can cross-walk partner-event chains."
    )


# ─── Operator alert + chain-gap escalation pattern ───────────────


def test_operator_alert_on_each_state_transition():
    """All 4 state transitions fire send_operator_alert via
    _send_operator_visibility wrapper. Wrapper handles chain-gap
    escalation pattern (severity → P0-CHAIN-GAP if attestation
    failed)."""
    src = _read(_BACKEND / "partner_admin_transfer.py")
    assert "_send_operator_visibility(" in src
    assert "P0-CHAIN-GAP" in src, (
        "chain-gap escalation pattern not applied — operator wouldn't "
        "see when partner-admin transfer chain breaks."
    )


# ─── Audit trail ──────────────────────────────────────────────────


def test_log_partner_activity_on_each_endpoint():
    """Each endpoint must call log_partner_activity per CLAUDE.md
    audit-coverage rule. This is the partner-side _audit_client_action
    equivalent."""
    src = _read(_BACKEND / "partner_admin_transfer.py")
    # initiate / accept / cancel each must call log_partner_activity
    for fn_name in [
        "initiate_partner_admin_transfer",
        "accept_partner_admin_transfer",
        "cancel_partner_admin_transfer",
    ]:
        fn_start = src.find(f"async def {fn_name}(")
        assert fn_start >= 0, f"{fn_name} not found"
        fn_body = src[fn_start:fn_start + 5000]
        assert "log_partner_activity(" in fn_body, (
            f"{fn_name} missing log_partner_activity call — partner "
            f"audit trail will be incomplete for this state transition."
        )

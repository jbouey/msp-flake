"""Tests for the owner-transfer state machine (punch-list #8, 2026-05-04).

Round-table-approved 5/5 with deltas:
  - Brian: 1-owner-min DB trigger MUST be in the migration (mig 273).
  - Linda: events go in ALLOWED_EVENTS only (admin-API class).
  - Steve: 24h cooling-off, any-admin-cancel, deprovision-blocks-pending.
  - Camila: backend this session, frontend follow-up.
  - Adam: subject "Account access change request: ..."; no banned words.

Source-level + lockstep tests pin the contract. No DB-live tests here
— full integration tests run in CI against the prod-mirror DB once
the deploy lands.
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


# ─── Migration 273 contract ──────────────────────────────────────


def test_migration_273_present():
    mig = _MIG_DIR / "273_owner_transfer_requests.sql"
    assert mig.exists(), (
        "migrations/273_owner_transfer_requests.sql missing — "
        "the data layer for owner-transfer is unshipped."
    )


def test_migration_273_creates_table_with_required_columns():
    src = _read(_MIG_DIR / "273_owner_transfer_requests.sql")
    assert "CREATE TABLE IF NOT EXISTS client_org_owner_transfer_requests" in src
    for col in [
        "client_org_id", "initiated_by_user_id", "target_email",
        "target_user_id", "status", "reason", "accept_token_hash",
        "current_ack_at", "target_accept_at", "completed_at",
        "canceled_at", "canceled_by", "cancel_reason",
        "expires_at", "cooling_off_until", "attestation_bundle_ids",
    ]:
        assert col in src, f"mig 273 missing column `{col}`"


def test_migration_273_one_pending_per_org_unique_index():
    src = _read(_MIG_DIR / "273_owner_transfer_requests.sql")
    assert "idx_owner_transfer_one_pending_per_org" in src
    assert "WHERE status IN ('pending_current_ack', 'pending_target_accept')" in src, (
        "Steve P3 ratchet — the partial-unique index MUST gate exactly "
        "the two non-terminal pending statuses, otherwise concurrent "
        "re-initiation could race past the application-layer check."
    )


def test_migration_273_min_one_owner_trigger():
    """Brian non-negotiable: the 1-owner-min invariant is at the DB
    level, not application-only. If a future code path ever attempts
    to demote/delete the last owner, the trigger fires."""
    src = _read(_MIG_DIR / "273_owner_transfer_requests.sql")
    assert "CREATE OR REPLACE FUNCTION enforce_min_one_owner_per_org" in src
    assert "trg_enforce_min_one_owner_per_org" in src
    # Trigger must fire on UPDATE OR DELETE — both are demotion paths
    assert "BEFORE UPDATE OR DELETE ON client_users" in src, (
        "trigger must fire on both UPDATE (role change) AND DELETE."
    )
    # Must check that AT LEAST ONE OTHER owner exists post-change
    assert "v_remaining_owners = 0" in src or "remaining_owners = 0" in src, (
        "trigger must explicitly check for zero-other-owners state."
    )


def test_migration_273_table_is_append_only():
    """Owner-transfer state machine is audit-class — DELETE blocked."""
    src = _read(_MIG_DIR / "273_owner_transfer_requests.sql")
    assert "prevent_owner_transfer_deletion" in src
    assert "BEFORE DELETE ON client_org_owner_transfer_requests" in src


# ─── Module surface contract ─────────────────────────────────────


def test_owner_transfer_module_present():
    assert (_BACKEND / "client_owner_transfer.py").exists(), (
        "backend module missing — the endpoint surface is unshipped."
    )


def test_owner_transfer_router_registered_in_main():
    src = _read(_MAIN_PY)
    assert "from dashboard_api.client_owner_transfer import owner_transfer_router" in src
    assert "app.include_router(owner_transfer_router)" in src


def test_owner_transfer_sweep_loop_registered():
    """Steve P3 ratchet — without the sweep loop, accepted transfers
    sit in pending_target_accept forever and never complete the role
    swap. EXPECTED_INTERVAL_S calibration also pinned (Session 214
    drift class) — sweep cadence (60s) must match the
    asyncio.sleep(60) inside the loop body."""
    src = _read(_MAIN_PY)
    assert '("owner_transfer_sweep", _owner_transfer_sweep_loop)' in src

    bg_src = _read(_BACKEND / "bg_heartbeat.py")
    assert '"owner_transfer_sweep": 60' in bg_src, (
        "EXPECTED_INTERVAL_S entry missing or miscalibrated."
    )

    loop_src = _read(_BACKEND / "client_owner_transfer.py")
    assert "await asyncio.sleep(60)" in loop_src, (
        "sweep loop sleep cadence drifted from EXPECTED_INTERVAL_S=60."
    )


def test_endpoints_have_correct_friction():
    """Every state-mutation endpoint enforces the privileged-chain
    friction levels: actor-must-be-email, reason ≥20ch, and where
    applicable a typed-literal confirm_phrase."""
    src = _read(_BACKEND / "client_owner_transfer.py")
    # Reason min length on initiate + cancel
    assert "MIN_REASON_CHARS = 20" in src
    assert 'min_length=MIN_REASON_CHARS' in src
    # confirm_phrase on ack
    assert 'confirm_phrase != "CONFIRM-OWNER-TRANSFER"' in src
    # No self-transfer
    assert "Cannot transfer ownership to yourself" in src


def test_accept_token_hashed_not_plaintext():
    """The accept token sent in email is the secret; what's stored
    in the DB is its SHA256 hash. Post-acceptance, the hash is NULLed
    so the token can't be re-used by a leaked-DB attacker."""
    src = _read(_BACKEND / "client_owner_transfer.py")
    assert "_hash_token(" in src
    assert "hashlib.sha256" in src
    assert "accept_token_hash = NULL" in src, (
        "accept_token_hash must be NULLed after acceptance."
    )


def test_role_swap_promotes_before_demoting():
    """Brian + Linda: the 1-owner-min trigger means we MUST promote
    the new owner BEFORE demoting the old one. Otherwise an
    intermediate state has zero owners and the trigger fires."""
    src = _read(_BACKEND / "client_owner_transfer.py")
    # Find _complete_transfer body
    fn_start = src.find("async def _complete_transfer(")
    assert fn_start >= 0
    fn_body = src[fn_start:fn_start + 3000]
    promote_idx = fn_body.find("role = 'owner', updated_at")
    demote_idx = fn_body.find("role = 'admin', updated_at")
    assert promote_idx >= 0 and demote_idx >= 0
    assert promote_idx < demote_idx, (
        "Role swap MUST promote target to owner BEFORE demoting "
        "initiator — otherwise zero-owner intermediate state fires "
        "the 1-owner-min trigger."
    )


def test_attestation_chain_writes_per_state_transition():
    """Every state transition writes an attestation bundle. Six
    event_types — verify all six appear at runtime call sites in
    the module (AST-strength)."""
    src = _read(_BACKEND / "client_owner_transfer.py")
    tree = ast.parse(src)
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Looking for _emit_attestation(... event_type="...") OR
        # _send_operator_visibility(... event_type="...")
        for kw in node.keywords:
            if kw.arg == "event_type" and isinstance(kw.value, ast.Constant):
                if isinstance(kw.value.value, str):
                    found.add(kw.value.value)
    expected = {
        "client_org_owner_transfer_initiated",
        "client_org_owner_transfer_acked",
        "client_org_owner_transfer_accepted",
        "client_org_owner_transfer_canceled",
        "client_org_owner_transfer_completed",
        "client_org_owner_transfer_expired",
    }
    missing = expected - found
    assert not missing, (
        f"event_types not wired at runtime call sites: {sorted(missing)}. "
        f"Each state transition must write an attestation."
    )


# ─── Three-list lockstep ─────────────────────────────────────────


def test_six_event_types_in_allowed_events():
    """ALLOWED_EVENTS must contain all six owner-transfer event_types.
    They are NOT in PRIVILEGED_ORDER_TYPES + v_privileged_types
    (admin-API class, asymmetry permitted by lockstep checker)."""
    src = _read(_BACKEND / "privileged_access_attestation.py")
    for ev in [
        "client_org_owner_transfer_initiated",
        "client_org_owner_transfer_acked",
        "client_org_owner_transfer_accepted",
        "client_org_owner_transfer_completed",
        "client_org_owner_transfer_canceled",
        "client_org_owner_transfer_expired",
    ]:
        assert f'"{ev}"' in src, (
            f"ALLOWED_EVENTS missing `{ev}` — attestation create "
            f"will reject the event_type at runtime."
        )


def test_owner_transfer_events_NOT_in_privileged_order_types():
    """Steve + Linda lockstep asymmetry: owner-transfer events are
    admin-API class (state-machine endpoints in client_portal.py),
    NOT fleet_orders. They must NOT appear in fleet_cli.PRIVILEGED_
    ORDER_TYPES — adding them there would break the sym-difference
    invariant the lockstep checker enforces."""
    fleet_cli = _BACKEND / "fleet_cli.py"
    if not fleet_cli.exists():
        pytest.skip("fleet_cli.py not in this checkout slice")
    src = fleet_cli.read_text()
    for ev in [
        "client_org_owner_transfer_initiated",
        "client_org_owner_transfer_acked",
        "client_org_owner_transfer_accepted",
        "client_org_owner_transfer_completed",
        "client_org_owner_transfer_canceled",
        "client_org_owner_transfer_expired",
    ]:
        assert f'"{ev}"' not in src, (
            f"fleet_cli.PRIVILEGED_ORDER_TYPES must NOT contain `{ev}` — "
            f"these are admin-API events, not fleet_orders. The lockstep "
            f"checker permits ALLOWED_EVENTS ⊇ PRIVILEGED_ORDER_TYPES "
            f"only; reverse-asymmetry would fail."
        )


# ─── Email language compliance ───────────────────────────────────


def test_deprovision_blocks_while_pending_transfer_exists():
    """Steve P3 round-table: deprovision must refuse while a pending
    owner-transfer is in flight. Otherwise an adversarial owner who
    initiated a transfer could race the practice's own deprovision
    intent. The 409 response surfaces the transfer_id so the operator
    can cancel-or-wait."""
    src = _read(_BACKEND / "org_management.py")
    assert "client_org_owner_transfer_requests" in src, (
        "deprovision endpoint not querying owner-transfer table — "
        "the race-condition gate is missing."
    )
    assert "pending_current_ack" in src and "pending_target_accept" in src, (
        "deprovision gate not filtering on the two pending statuses."
    )
    # Error message must point operators at the cancel endpoint
    assert "/cancel" in src or "cancel the transfer" in src.lower(), (
        "deprovision 409 must surface the cancel route so the operator "
        "knows how to resolve the conflict."
    )


def test_emails_use_neutral_legal_language():
    """Adam: emails must NOT use the banned words from CLAUDE.md
    Session 199. Banned: ensures, prevents, protects, guarantees,
    audit-ready, PHI never leaves, 100%."""
    src = _read(_BACKEND / "client_owner_transfer.py")
    banned = ["ensures", "prevents", "protects", "guarantees",
              "audit-ready", "PHI never leaves", "100%"]
    for word in banned:
        assert word not in src, (
            f"owner-transfer email body contains banned word `{word}` "
            f"per CLAUDE.md Session 199 legal-language rules."
        )


def test_email_subject_uses_anti_spam_phrasing():
    """Adam: subject line must avoid "ownership transfer" verbatim
    in the subject (some MSP-managed domains spam-filter on it).
    2026-05-06 task #42 update: subjects are now opaque (no
    {org_name} interpolation) per RT21 v2.3 counsel posture and
    opaque-mode email parity. Anti-spam constraint preserved —
    the new subjects say "account access" instead of "ownership
    transfer", which still bypasses the spam-filter trigger word."""
    src = _read(_BACKEND / "client_owner_transfer.py")
    # Both _send_target_accept_email and _send_initiator_confirmation_email
    # subjects must contain "account access" and must NOT say
    # "ownership transfer" (the original anti-spam ban) and must NOT
    # interpolate {org_name} (the opaque-mode harmonization).
    assert '"OsirisCare: account access change request initiated"' in src, (
        "Initiator confirmation subject not found — opaque-mode "
        "anti-spam phrasing required."
    )
    assert '"OsirisCare: action required — account access proposal"' in src, (
        "Target accept subject not found — opaque-mode anti-spam "
        "phrasing required."
    )
    # Banned phrasing in subject (body is fine)
    fn_starts = [
        src.find("def _send_target_accept_email("),
        src.find("def _send_initiator_confirmation_email("),
    ]
    import re
    for fn_start in fn_starts:
        assert fn_start >= 0
        # Bound the window to this helper's body only — stop at the
        # next `def `/`async def ` so docstrings of unrelated
        # functions don't leak into the search.
        end_match = re.search(r"\n(async )?def ", src[fn_start + 10:])
        fn_end = fn_start + 10 + end_match.start() if end_match else len(src)
        fn_body = src[fn_start:fn_end]
        assert "account access" in fn_body, (
            "Anti-spam subject pattern (account access) missing."
        )
        assert "ownership transfer" not in fn_body.lower(), (
            "Banned spam-trigger phrase 'ownership transfer' found "
            "in email helper body — Adam P3 anti-spam ratchet."
        )

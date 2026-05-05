"""Tests for per-org configurable cooling-off / expiry (mig 275, task #20).

Schema-additive change: client_orgs and partners now carry
transfer_cooling_off_hours + transfer_expiry_days columns. Two new
endpoints (client owner + partner admin) write these with full
privileged-action chain. Read at transfer-initiate time.

Source-level + lockstep tests pin the contract; behavior tests run
against prod-mirror DB in CI.
"""
from __future__ import annotations

import ast
import pathlib

import pytest


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND.parent.parent.parent
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


# ─── Migration 275 contract ───────────────────────────────────────


def test_migration_275_present():
    mig = _MIG_DIR / "275_per_org_transfer_prefs.sql"
    assert mig.exists()


def test_migration_275_adds_columns_to_both_tables():
    src = _read(_MIG_DIR / "275_per_org_transfer_prefs.sql")
    assert "ALTER TABLE client_orgs" in src
    assert "ALTER TABLE partners" in src
    for col in ["transfer_cooling_off_hours", "transfer_expiry_days"]:
        assert src.count(col) >= 2, (
            f"column `{col}` not added to BOTH client_orgs + partners"
        )


def test_migration_275_default_values_match_session_design():
    src = _read(_MIG_DIR / "275_per_org_transfer_prefs.sql")
    client_block_start = src.find("ALTER TABLE client_orgs")
    client_block = src[client_block_start:client_block_start + 600]
    assert "transfer_cooling_off_hours INT" in client_block
    assert "DEFAULT 24" in client_block, (
        "client_orgs.transfer_cooling_off_hours default drifted from 24h"
    )
    partner_block_start = src.find("ALTER TABLE partners")
    partner_block = src[partner_block_start:partner_block_start + 600]
    assert "DEFAULT 0" in partner_block, (
        "partners.transfer_cooling_off_hours default drifted from 0h "
        "(Maya operator-class design)"
    )


def test_migration_275_check_constraints_bound_ranges():
    src = _read(_MIG_DIR / "275_per_org_transfer_prefs.sql")
    assert "BETWEEN 0 AND 168" in src
    assert "BETWEEN 1 AND 30" in src
    for name in [
        "chk_client_orgs_transfer_cooling_off",
        "chk_client_orgs_transfer_expiry",
        "chk_partners_transfer_cooling_off",
        "chk_partners_transfer_expiry",
    ]:
        assert name in src, f"CHECK constraint `{name}` missing"


# ─── ALLOWED_EVENTS lockstep ──────────────────────────────────────


def test_two_new_events_in_allowed_events():
    src = _read(_BACKEND / "privileged_access_attestation.py")
    for ev in [
        "client_org_transfer_prefs_changed",
        "partner_transfer_prefs_changed",
    ]:
        assert f'"{ev}"' in src


def test_two_new_events_in_lockstep_test():
    src = _read(_BACKEND / "tests/test_privileged_chain_allowed_events_lockstep.py")
    for ev in [
        "client_org_transfer_prefs_changed",
        "partner_transfer_prefs_changed",
    ]:
        assert f'"{ev}"' in src


def test_two_new_events_NOT_in_privileged_order_types():
    fleet_cli = _BACKEND / "fleet_cli.py"
    if not fleet_cli.exists():
        pytest.skip("fleet_cli.py not in this checkout slice")
    src = fleet_cli.read_text()
    for ev in [
        "client_org_transfer_prefs_changed",
        "partner_transfer_prefs_changed",
    ]:
        assert f'"{ev}"' not in src


# ─── Endpoint contracts ──────────────────────────────────────────


def test_client_transfer_prefs_endpoint_present():
    src = _read(_BACKEND / "client_owner_transfer.py")
    fn_src = _find_function(src, "update_transfer_prefs")
    assert fn_src, "update_transfer_prefs handler not found"
    assert "require_client_owner" in fn_src
    assert "TransferPrefsUpdate" in src
    assert "create_privileged_access_attestation" in fn_src
    assert 'event_type="client_org_transfer_prefs_changed"' in fn_src
    assert "_audit_client_action" in fn_src
    assert "send_operator_alert" in fn_src


def test_partner_transfer_prefs_endpoint_present():
    src = _read(_BACKEND / "partner_admin_transfer.py")
    fn_src = _find_function(src, "update_partner_transfer_prefs")
    assert fn_src, "update_partner_transfer_prefs handler not found"
    assert 'require_partner_role("admin")' in fn_src
    assert "create_privileged_access_attestation" in fn_src
    assert 'event_type="partner_transfer_prefs_changed"' in fn_src
    assert "log_partner_activity" in fn_src
    assert "send_operator_alert" in fn_src


def test_client_endpoint_pydantic_model_validation():
    src = _read(_BACKEND / "client_owner_transfer.py")
    assert "ge=0, le=168" in src
    assert "ge=1, le=30" in src
    assert "min_length=MIN_REASON_CHARS" in src


def test_friction_weakening_escalates_severity():
    """Operator-alert severity escalates when cooling-off is being
    weakened — operator should see the friction-reduction in real time."""
    src = _read(_BACKEND / "client_owner_transfer.py")
    fn_src = _find_function(src, "update_transfer_prefs")
    assert "weakening" in fn_src
    assert "FRICTION-WEAKENED" in fn_src
    assert "P0-CHAIN-GAP" in fn_src


def test_partner_endpoint_flags_cooling_off_as_informational():
    src = _read(_BACKEND / "partner_admin_transfer.py")
    fn_src = _find_function(src, "update_partner_transfer_prefs")
    assert "cooling_set_but_ignored" in fn_src or \
           "COOLING-OFF-INFORMATIONAL-ONLY" in fn_src
    assert "cooling_off_honored_at_runtime" in fn_src


# ─── Read-at-initiate-time ───────────────────────────────────────


def test_client_initiate_reads_per_org_prefs():
    src = _read(_BACKEND / "client_owner_transfer.py")
    fn_src = _find_function(src, "initiate_owner_transfer")
    assert fn_src
    assert "_resolve_org_transfer_prefs" in fn_src


def test_client_accept_reads_per_org_cooling_off():
    src = _read(_BACKEND / "client_owner_transfer.py")
    fn_src = _find_function(src, "accept_owner_transfer")
    assert fn_src
    assert "_resolve_org_transfer_prefs" in fn_src


def test_partner_initiate_reads_per_partner_expiry():
    src = _read(_BACKEND / "partner_admin_transfer.py")
    fn_src = _find_function(src, "initiate_partner_admin_transfer")
    assert fn_src
    assert "_resolve_partner_expiry_days" in fn_src


# ─── Anchor namespace consistency ────────────────────────────────


def test_client_prefs_anchor_at_primary_site_id():
    src = _read(_BACKEND / "client_owner_transfer.py")
    fn_src = _find_function(src, "update_transfer_prefs")
    assert "ORDER BY created_at ASC LIMIT 1" in fn_src
    assert 'f"client_org:{org_id}"' in fn_src


def test_partner_prefs_anchor_at_partner_org_namespace():
    src = _read(_BACKEND / "partner_admin_transfer.py")
    fn_src = _find_function(src, "update_partner_transfer_prefs")
    assert 'f"partner_org:{partner_id}"' in fn_src

"""CI gate: RT-DM data-model audit (2026-05-06) — three fix contracts.

Outside-audit found three foundational telemetry-truth bugs
distorting dashboards:

  Issue #1 — runbook_id namespace mismatch (agent emits L1-*; backend
             stores LIN-*/RB-*). Joins fail; per-runbook execution
             counts go to 0.
  Issue #2 — L2 truth split between l2_decisions + incidents; no
             canonical view JOINing them.
  Issue #3 — orders.status had no code path transitioning past
             'acknowledged'. Order-completion dashboards show 0%.

This gate pins each fix:

  Migration 284 — runbooks.agent_runbook_id bridge column + backfill
  Migration 285 — v_l2_outcomes view + compute_l2_success_rate function
  Migration 286 — auto_complete_order_on_telemetry trigger + sweeper

Plus three substrate invariants catch future drift:

  unbridged_telemetry_runbook_ids (sev2)
  l2_resolution_without_decision_record (sev2)
  orders_stuck_acknowledged (sev2)
"""
from __future__ import annotations

import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_MIG_284 = _BACKEND / "migrations" / "284_runbook_agent_id_bridge.sql"
_MIG_285 = _BACKEND / "migrations" / "285_l2_outcomes_canonical_view.sql"
_MIG_286 = _BACKEND / "migrations" / "286_orders_completion_path.sql"
_AGENT_API = _BACKEND / "agent_api.py"
_ASSERTIONS = _BACKEND / "assertions.py"


# ─────────────────────────────────────────────────────────────────
# Issue #1 — runbook ID bridge
# ─────────────────────────────────────────────────────────────────


def test_mig_284_adds_agent_runbook_id_column():
    src = _MIG_284.read_text()
    assert re.search(
        r"ALTER\s+TABLE\s+runbooks\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+agent_runbook_id\s+TEXT",
        src,
        re.IGNORECASE,
    ), "mig 284 missing the ALTER TABLE runbooks ADD COLUMN agent_runbook_id"


def test_mig_284_creates_unique_index_on_agent_runbook_id():
    src = _MIG_284.read_text()
    assert re.search(
        r"CREATE\s+UNIQUE\s+INDEX[^;]+agent_runbook_id",
        src,
        re.IGNORECASE | re.DOTALL,
    ), "mig 284 missing UNIQUE INDEX on runbooks.agent_runbook_id"


def test_mig_284_backfills_known_l1_mappings():
    """At minimum the LIN-* set should have explicit UPDATE rows
    linking each LIN-* runbook to its agent L1-LIN-* counterpart."""
    src = _MIG_284.read_text()
    # Spot-check 3 well-known mappings
    for canonical, agent in (
        ("LIN-AUDIT-001", "L1-LIN-AUDIT-001"),
        ("LIN-FW-001", "L1-LIN-FW-001"),
        ("LIN-SVC-001", "L1-LIN-SVC-001"),
    ):
        pattern = rf"UPDATE\s+runbooks\s+SET\s+agent_runbook_id\s*=\s*'{re.escape(agent)}'\s+WHERE\s+runbook_id\s*=\s*'{re.escape(canonical)}'"
        assert re.search(pattern, src, re.IGNORECASE), (
            f"mig 284 missing backfill mapping {canonical} → {agent}"
        )


def test_mig_284_inserts_placeholder_rows_for_orphan_l1_ids():
    """L1-* IDs that don't have a backend counterpart MUST get a
    placeholder row inserted (otherwise the JOIN still fails on
    those IDs). Spot-check a few key Windows-class L1-* IDs that
    have no LIN-*/WIN-* counterpart."""
    src = _MIG_284.read_text()
    for agent_id in (
        "L1-DNS-001",
        "L1-FIREWALL-001",
        "L1-PATCH-001",
        "L1-AUDIT-001",
        "L1-PASSWORD-001",
    ):
        assert agent_id in src, (
            f"mig 284 missing placeholder INSERT for orphan L1 ID {agent_id}"
        )


# ─────────────────────────────────────────────────────────────────
# Issue #2 — L2 outcomes canonical view
# ─────────────────────────────────────────────────────────────────


def test_mig_285_creates_v_l2_outcomes_view():
    src = _MIG_285.read_text()
    assert re.search(
        r"CREATE\s+OR\s+REPLACE\s+VIEW\s+v_l2_outcomes",
        src,
        re.IGNORECASE,
    ), "mig 285 missing CREATE VIEW v_l2_outcomes"
    # The view must JOIN l2_decisions to incidents
    assert re.search(
        r"FROM\s+l2_decisions\s+ld[\s\S]+JOIN\s+incidents",
        src,
        re.IGNORECASE,
    ), "v_l2_outcomes must JOIN l2_decisions to incidents"
    # And expose a derived is_l2_success boolean
    assert "is_l2_success" in src, (
        "v_l2_outcomes must derive an is_l2_success boolean column"
    )


def test_mig_285_creates_compute_l2_success_rate_function():
    src = _MIG_285.read_text()
    assert re.search(
        r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+compute_l2_success_rate",
        src,
        re.IGNORECASE,
    ), "mig 285 missing compute_l2_success_rate function"
    # Function should accept window_days parameter
    assert re.search(
        r"window_days\s+INT",
        src,
        re.IGNORECASE,
    ), "compute_l2_success_rate must take window_days INT parameter"


# ─────────────────────────────────────────────────────────────────
# Issue #3 — orders completion path (post-Maya 2nd-eye redesign)
#
# Initial round-table consensus was a DB trigger reading from
# `execution_telemetry.metadata->>'order_id'`. Maya's 2nd-eye on the
# fix found execution_telemetry has NO metadata column — trigger
# would silently no-op. Redesigned: explicit /orders/complete
# endpoint as primary completion path; sweep_stuck_orders() backstop.
# ─────────────────────────────────────────────────────────────────


def test_mig_286_adds_order_id_column_to_execution_telemetry():
    """Forward-compat column for agent-side correlation when the
    agent's binary is updated to emit order_id with telemetry."""
    src = _MIG_286.read_text()
    assert re.search(
        r"ALTER\s+TABLE\s+execution_telemetry\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+order_id\s+TEXT",
        src,
        re.IGNORECASE,
    ), "mig 286 missing ALTER TABLE execution_telemetry ADD COLUMN order_id"


def test_mig_286_creates_sweep_function():
    """Non-consensus hardening: sweep_stuck_orders() function clears
    orders stuck past their timeout window — primary backstop after
    the trigger-based design was rejected."""
    src = _MIG_286.read_text()
    assert re.search(
        r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+sweep_stuck_orders",
        src,
        re.IGNORECASE,
    ), "mig 286 missing sweep_stuck_orders() function"
    assert "status = 'failed'" in src, (
        "sweep_stuck_orders() must transition stuck rows to 'failed' "
        "with timeout reason (failure path coverage — non-consensus "
        "hardening)"
    )


def test_complete_order_endpoint_exists():
    """The primary completion path is the explicit /orders/complete
    endpoint (Maya 2nd-eye redesign — replaces the trigger-based
    approach that depended on a non-existent metadata column)."""
    src = _AGENT_API.read_text()
    assert re.search(
        r'@router\.post\(\s*"/orders/complete"\s*\)',
        src,
    ), "/orders/complete endpoint missing in agent_api.py"
    assert "_AgentApiOrderCompletion" in src, (
        "_AgentApiOrderCompletion request model missing"
    )
    # The endpoint must be appliance-bearer-auth-gated
    m = re.search(
        r"async def complete_order\b.+?(?=\nasync def |\nclass |\Z)",
        src,
        re.DOTALL,
    )
    assert m, "complete_order handler not found"
    body = m.group(0)
    assert "require_appliance_bearer" in body, (
        "complete_order must require appliance bearer auth"
    )
    # Idempotency: WHERE filter on status
    assert re.search(
        r"status\s+IN\s*\(\s*'acknowledged'",
        body,
    ), (
        "complete_order UPDATE must filter `status IN ('acknowledged',"
        " 'executing', 'pending')` for idempotency on replay"
    )
    # Failure path support
    assert "new_status = " in body and 'completed' in body and 'failed' in body, (
        "complete_order must handle BOTH success → completed AND "
        "failure → failed paths"
    )


def test_complete_order_no_trigger_left_behind():
    """The original trigger design was rejected (depends on non-
    existent metadata column). Make sure no migration still
    references that pattern — silent no-op risk."""
    src = _MIG_286.read_text()
    assert "auto_complete_order_on_telemetry" not in src, (
        "Stale trigger reference. The trigger-based design was rejected "
        "(execution_telemetry has no metadata column to correlate on). "
        "Use the explicit /orders/complete endpoint."
    )
    assert "metadata ->> 'order_id'" not in src, (
        "Stale metadata-based correlation. The metadata column "
        "doesn't exist on execution_telemetry."
    )


# ─────────────────────────────────────────────────────────────────
# Substrate invariants — drift detection
# ─────────────────────────────────────────────────────────────────


def test_unbridged_telemetry_invariant_registered():
    src = _ASSERTIONS.read_text()
    assert 'name="unbridged_telemetry_runbook_ids"' in src, (
        "unbridged_telemetry_runbook_ids invariant not registered"
    )
    assert "_check_unbridged_telemetry_runbook_ids" in src, (
        "unbridged_telemetry_runbook_ids check function missing"
    )


def test_l2_resolution_without_decision_invariant_registered():
    src = _ASSERTIONS.read_text()
    assert 'name="l2_resolution_without_decision_record"' in src, (
        "l2_resolution_without_decision_record invariant not registered"
    )
    assert "_check_l2_resolution_without_decision_record" in src, (
        "l2_resolution_without_decision_record check function missing"
    )


def test_orders_stuck_acknowledged_invariant_registered():
    src = _ASSERTIONS.read_text()
    assert 'name="orders_stuck_acknowledged"' in src, (
        "orders_stuck_acknowledged invariant not registered"
    )
    assert "_check_orders_stuck_acknowledged" in src, (
        "orders_stuck_acknowledged check function missing"
    )


# ─────────────────────────────────────────────────────────────────
# Migration ordering + idempotency
# ─────────────────────────────────────────────────────────────────


def test_all_three_migrations_idempotent():
    """Each migration must use IF NOT EXISTS / OR REPLACE / ON
    CONFLICT DO NOTHING so re-applying is safe."""
    for mig in (_MIG_284, _MIG_285, _MIG_286):
        src = mig.read_text()
        # Each migration touches schema; each should have at least
        # one of these idempotency markers.
        idempotent = (
            "IF NOT EXISTS" in src
            or "OR REPLACE" in src
            or "ON CONFLICT" in src
        )
        assert idempotent, (
            f"{mig.name} not idempotent — must use IF NOT EXISTS, "
            f"OR REPLACE, or ON CONFLICT to be safe to re-apply"
        )


def test_migrations_have_rollback_documentation():
    """Each migration must document its rollback path — even if
    the rollback is non-trivial, the path should be written down
    so a future operator knows what to do."""
    for mig in (_MIG_284, _MIG_285, _MIG_286):
        src = mig.read_text()
        # Each migration should mention rollback / revert / DROP
        # in a comment block. Loose check; not enforcing format.
        assert re.search(
            r"#?\s*[Rr]ollback|--\s*[Rr]ollback|--\s*To\s+roll\s+back|--\s*DROP\s+(TRIGGER|FUNCTION|VIEW)",
            src,
        ), (
            f"{mig.name} missing rollback documentation. Add a "
            f"comment block describing how to revert (DROP / UPDATE / "
            f"etc.) so a future operator has a documented path."
        )

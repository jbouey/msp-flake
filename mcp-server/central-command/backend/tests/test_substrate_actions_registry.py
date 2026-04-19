"""Tests for substrate_actions registry.

Verifies that the handler registry has exactly the required keys,
that each entry is a SubstrateAction with proper metadata, and that
substrate actions do NOT alias privileged order types.
"""

import pytest


def test_registry_has_exactly_three_keys():
    from substrate_actions import SUBSTRATE_ACTIONS

    assert set(SUBSTRATE_ACTIONS.keys()) == {
        "cleanup_install_session",
        "unlock_platform_account",
        "reconcile_fleet_order",
    }


def test_each_entry_is_substrate_action():
    from substrate_actions import SUBSTRATE_ACTIONS, SubstrateAction

    for key, value in SUBSTRATE_ACTIONS.items():
        assert isinstance(value, SubstrateAction)
        assert callable(value.handler)
        assert value.audit_action == f"substrate.{key}"
        assert value.required_reason_chars in (0, 20)


def test_no_privileged_order_types_in_registry():
    # Guardrail: registry must never alias a fleet_cli privileged order type.
    from substrate_actions import SUBSTRATE_ACTIONS
    from fleet_cli import PRIVILEGED_ORDER_TYPES

    assert SUBSTRATE_ACTIONS.keys().isdisjoint(PRIVILEGED_ORDER_TYPES)

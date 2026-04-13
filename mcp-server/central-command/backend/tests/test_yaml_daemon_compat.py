"""Cross-language YAML validator (Phase 15 round-table P0 #1).

The Go appliance daemon validates promoted-rule YAML in
appliance/internal/orders/processor.go::validatePromotedRule. Today's
session (2026-04-13) hit two bugs in series because Python's YAML
synthesizer didn't match the Go validator's checks: action whitelist
mismatch + missing conditions block. Each only surfaced after a real
appliance ack came back negative.

This test mirrors the Go validator IN PYTHON and asserts that
build_daemon_valid_rule_yaml() output passes ALL checks the daemon
will run — so a Python-only test breaks the build BEFORE deploy.

The Go side is the source of truth. If processor.go::validatePromotedRule
adds a check, this file MUST be updated to match. The rule of thumb is:
when you bump the Go validator, grep for `MIRROR_OF_GO_VALIDATOR` and
update.
"""
from __future__ import annotations

import re

import pytest


# MIRROR_OF_GO_VALIDATOR — keep in sync with processor.go
ALLOWED_RULE_ACTIONS = {
    "update_to_baseline_generation",
    "restart_av_service",
    "run_backup_job",
    "restart_logging_services",
    "restore_firewall_baseline",
    "run_windows_runbook",
    "run_linux_runbook",
    "escalate",
    "renew_certificate",
    "cleanup_disk_space",
}
ALLOWED_RULE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,64}$")
RULE_YAML_MAX_BYTES = 8192


def _validate_promoted_rule(rule_id: str, rule_yaml: str) -> None:
    """Mirror of processor.go::validatePromotedRule — raises on the
    same conditions the Go daemon does. Returns None on success.
    """
    import yaml as pyyaml

    if not ALLOWED_RULE_ID_PATTERN.match(rule_id):
        raise ValueError(f"rule_id {rule_id!r} contains invalid characters")
    if len(rule_yaml.encode("utf-8")) > RULE_YAML_MAX_BYTES:
        raise ValueError(
            f"rule_yaml exceeds {RULE_YAML_MAX_BYTES}B limit ({len(rule_yaml)} bytes)"
        )

    try:
        rule = pyyaml.safe_load(rule_yaml) or {}
    except pyyaml.YAMLError as e:
        raise ValueError(f"invalid YAML: {e}")

    if rule.get("id") != rule_id:
        raise ValueError(
            f"YAML id {rule.get('id')!r} does not match rule_id {rule_id!r}"
        )
    if not rule.get("name"):
        raise ValueError("rule name is required")
    action = rule.get("action") or ""
    if not action:
        raise ValueError("rule action is required")
    if action not in ALLOWED_RULE_ACTIONS:
        raise ValueError(f"action {action!r} not in allowed actions")
    conditions = rule.get("conditions") or []
    if len(conditions) == 0:
        raise ValueError("rule must have at least one condition")
    for i, cond in enumerate(conditions):
        if not cond.get("field"):
            raise ValueError(f"condition[{i}]: field is required")
        if not cond.get("operator"):
            raise ValueError(f"condition[{i}]: operator is required")


# ─── Tests ─────────────────────────────────────────────────────────


def test_synthesizer_output_passes_daemon_validator_windows():
    """Today's prod scenario: L1-AUTO-RANSOMWARE-INDICATOR was the
    backfill that finally closed the flywheel measurement loop. The
    YAML our synthesizer produced for it MUST validate."""
    from flywheel_math import build_daemon_valid_rule_yaml
    yaml = build_daemon_valid_rule_yaml(
        rule_id="L1-AUTO-RANSOMWARE-INDICATOR",
        runbook_id="RB-WIN-STG-002",
        incident_type="ransomware_indicator",
    )
    # Should not raise
    _validate_promoted_rule("L1-AUTO-RANSOMWARE-INDICATOR", yaml)


def test_synthesizer_output_passes_daemon_validator_linux():
    from flywheel_math import build_daemon_valid_rule_yaml
    yaml = build_daemon_valid_rule_yaml(
        rule_id="L1-AUTO-LINUX-FIREWALL",
        runbook_id="LIN-FW-001",
        incident_type="linux_firewall_drift",
    )
    _validate_promoted_rule("L1-AUTO-LINUX-FIREWALL", yaml)


def test_synthesizer_runs_against_every_prod_runbook_prefix():
    """For every distinct runbook_id family observed in prod
    promoted_rules (2026-04-13 snapshot), generate a YAML and
    confirm it passes daemon validation."""
    from flywheel_math import build_daemon_valid_rule_yaml
    cases = [
        ("L1-AUTO-A", "LIN-SSH-001", "ssh_drift"),
        ("L1-AUTO-B", "LIN-FW-001", "firewall_drift"),
        ("L1-AUTO-C", "LIN-SVC-001", "svc_drift"),
        ("L1-AUTO-D", "L1-LIN-USERS-001", "user_drift"),
        ("L1-AUTO-E", "L1-NET-DNS-001", "dns_drift"),
        ("L1-AUTO-F", "L1-SUID-001", "suid_drift"),
        ("L1-AUTO-G", "RB-WIN-SEC-002", "sec_drift"),
        ("L1-AUTO-H", "RB-WIN-SVC-001", "svc_drift"),
        ("L1-AUTO-I", "RB-WIN-STG-002", "stg_drift"),
        ("L1-AUTO-J", "L1-WIN-SEC-SCREENLOCK", "screen_lock"),
    ]
    for rule_id, runbook_id, incident_type in cases:
        yaml = build_daemon_valid_rule_yaml(
            rule_id=rule_id,
            runbook_id=runbook_id,
            incident_type=incident_type,
        )
        _validate_promoted_rule(rule_id, yaml)


def test_validator_catches_missing_conditions():
    """Today's bug #C exactly. Without this guardrail, a synthesizer
    regression that drops the conditions block would ship to prod and
    only surface when the appliance NACKs the order."""
    bad_yaml = (
        "id: L1-X\n"
        "name: x\n"
        "action: run_windows_runbook\n"
        "action_params:\n"
        "  runbook_id: RB-WIN-001\n"
    )
    with pytest.raises(ValueError, match="at least one condition"):
        _validate_promoted_rule("L1-X", bad_yaml)


def test_validator_catches_execute_runbook_action():
    """Today's bug #B exactly. The historical promoted_rules YAML had
    `action: execute_runbook` which the daemon rejects."""
    bad_yaml = (
        "id: L1-X\n"
        "name: x\n"
        "action: execute_runbook\n"
        "conditions:\n"
        "  - field: incident_type\n"
        "    operator: eq\n"
        "    value: y\n"
    )
    with pytest.raises(ValueError, match="not in allowed actions"):
        _validate_promoted_rule("L1-X", bad_yaml)


def test_validator_catches_id_mismatch():
    bad_yaml = (
        "id: L1-WRONG\n"
        "name: x\n"
        "action: run_windows_runbook\n"
        "conditions:\n"
        "  - field: incident_type\n"
        "    operator: eq\n"
        "    value: y\n"
    )
    with pytest.raises(ValueError, match="does not match rule_id"):
        _validate_promoted_rule("L1-RIGHT", bad_yaml)


def test_validator_catches_oversize_yaml():
    huge = "id: L1-X\nname: x\naction: run_windows_runbook\n" + ("# pad\n" * 2000)
    with pytest.raises(ValueError, match="exceeds"):
        _validate_promoted_rule("L1-X", huge)


def test_validator_catches_invalid_rule_id_chars():
    yaml = "id: L1-X\nname: x\n"
    with pytest.raises(ValueError, match="invalid characters"):
        _validate_promoted_rule("rule with spaces", yaml)
    with pytest.raises(ValueError, match="invalid characters"):
        _validate_promoted_rule("ab", yaml)  # < 3 chars


def test_validator_catches_condition_missing_field():
    bad_yaml = (
        "id: L1-X\n"
        "name: x\n"
        "action: run_windows_runbook\n"
        "conditions:\n"
        "  - operator: eq\n"
        "    value: y\n"
    )
    with pytest.raises(ValueError, match=r"condition\[0\]: field is required"):
        _validate_promoted_rule("L1-X", bad_yaml)


def test_action_whitelist_is_in_lockstep_with_normalize_rule_action():
    """normalize_rule_action returns values that MUST be in
    ALLOWED_RULE_ACTIONS or the synthesizer ships rejected YAML."""
    from flywheel_math import normalize_rule_action
    for runbook_id in ("RB-WIN-SEC-001", "L1-WIN-X", "WIN-X-001",
                       "LIN-FW-001", "L1-LIN-X", "L1-NET-X", "L1-SUID-X"):
        action = normalize_rule_action(runbook_id)
        assert action in ALLOWED_RULE_ACTIONS, (
            f"{action!r} (from {runbook_id!r}) not in daemon's whitelist — "
            f"the daemon will reject every synthesized rule with this prefix"
        )

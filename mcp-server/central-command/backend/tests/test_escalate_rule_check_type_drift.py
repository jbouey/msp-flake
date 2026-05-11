"""Pin gate — `Action: "escalate"` rules in `appliance/internal/healing/builtin_rules.go`
MUST be documented in `_KNOWN_ESCALATE_CHECK_TYPES` (or be covered by
`MONITORING_ONLY_CHECKS`).

Session 220 task #114 (2026-05-11). Phase 3 of the L1-orphan close
shipped fixes for the 9 known escalate-action rules
(`daemon.go:1706` hardcoded "L1" + `healing_executor.go:92` missing
success key + `l1_engine.go:328` Success=true default → 1,137 prod
orphans). The Phase 3 fix structurally prevents the false-heal at
daemon level (Layer 1) AND backend level (Layer 2 monitoring-only
downgrade gate). This gate detects FUTURE drift: a new builtin
escalate-action rule added to `builtin_rules.go` whose check_type
isn't reflected in:
  - `MONITORING_ONLY_CHECKS` in `mcp-server/main.py` (Layer 2
    backend gate catches it for free)
  - `_KNOWN_ESCALATE_CHECK_TYPES` below (operator acknowledgement
    that escalate-without-runbook is the right action for this class)

SCOPE: Compile-time builtin Go rules ONLY. Runtime-loaded JSON rules
from `/var/lib/msp/rules/l1_rules.json` (synced from Central Command)
are out of scope — those flow through the server-side `l1_rules`
table review path.

Algorithm (line-window scan per Gate A P0):
  1. Find every line matching `^\\s*Action:\\s*"escalate"\\s*,?\\s*$`.
  2. Walk backwards up to 20 lines to the enclosing `Conditions: []RuleCondition{`.
  3. Inside that window, extract the FIRST condition matching either
     `Field: "check_type"` OR `Field: "incident_type"` with
     `Operator: OpEquals, Value: "<name>"`.
  4. Walk backwards to the enclosing `ID: "<rule_id>"`.
  5. Each (rule_id, field, value) tuple MUST be in
     `_KNOWN_ESCALATE_CHECK_TYPES` allowlist OR `value` must be in
     `MONITORING_ONLY_CHECKS`.

If the gate fires, output a 4-branch decision tree (Gate A P1-1):
  (a) add value to MONITORING_ONLY_CHECKS if not auto-healable
  (b) add value to mig 306-style IN-list when next backfill ships
  (c) add to _KNOWN_ESCALATE_CHECK_TYPES with `# justified: <reason>`
  (d) refactor rule to a healable Action if a runbook exists

Sibling pattern:
  - `tests/test_privileged_chain_allowed_events_lockstep.py`
  - `tests/test_l1_resolution_requires_remediation_step.py`
  - `tests/test_appliance_endpoints_auth_pinned.py`
"""
from __future__ import annotations

import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_REPO = _BACKEND.parent.parent.parent
_BUILTIN_RULES = _REPO / "appliance" / "internal" / "healing" / "builtin_rules.go"

# Pre-seeded with all 9 currently-known escalate-action rules
# (Session 220 Phase 2A diagnostic — Gate A P1-2 pre-seed). Future
# additions require explicit entry. Format: rule_id → (field, value, justification).
_KNOWN_ESCALATE_CHECK_TYPES: dict[str, tuple[str, str, str]] = {
    "L1-ENCRYPT-001":          ("check_type", "encryption",            "operator review of encryption-status drift"),
    "L1-SERVICE-001":          ("incident_type", "service_crash",       "service restart-loop needs root-cause investigation"),
    "L1-LIN-PORTS-001":        ("check_type", "linux_open_ports",       "linux port-drift is policy review, not auto-remediation"),
    "L1-LIN-USERS-001":        ("check_type", "linux_user_accounts",    "user-account drift is security review, not auto-remediation"),
    "L1-WIN-ROGUE-TASKS-001":  ("check_type", "rogue_scheduled_tasks",  "scheduled-task persistence needs human malware analysis"),
    "L1-NET-PORTS-001":        ("check_type", "net_unexpected_ports",   "monitoring-only — Layer 2 backend gate downgrades to monitoring"),
    "L1-NET-SVC-001":          ("check_type", "net_expected_service",   "service detection result requires human follow-up"),
    "L1-NET-REACH-001":        ("check_type", "net_host_reachability",  "monitoring-only — Layer 2 backend gate downgrades to monitoring"),
    "L1-NET-DNS-001":          ("check_type", "net_dns_resolution",     "monitoring-only — Layer 2 backend gate downgrades to monitoring"),
}

# Imported from main.py via static read — avoids needing the runtime
# import (which pulls full backend deps). The cached set lives at
# `mcp-server/main.py:182`. We re-extract here so this test runs in
# any environment.
_MAIN_PY = _REPO / "mcp-server" / "main.py"


def _load_monitoring_only_from_main_py() -> set[str]:
    """Parse `MONITORING_ONLY_CHECKS = {...}` from main.py."""
    src = _MAIN_PY.read_text()
    m = re.search(
        r"MONITORING_ONLY_CHECKS\s*=\s*\{([^}]+)\}",
        src,
        re.DOTALL,
    )
    if not m:
        return set()
    body = m.group(1)
    return set(re.findall(r'"([^"]+)"', body))


_ACTION_ESCALATE_RE = re.compile(r'^\s*Action:\s*"escalate"\s*,?\s*$')
_CONDITION_FIELD_RE = re.compile(
    r'\{Field:\s*"(check_type|incident_type)",\s*'
    r'Operator:\s*OpEquals,\s*Value:\s*"([^"]+)"\}'
)
_RULE_ID_RE = re.compile(r'^\s*ID:\s*"([^"]+)"\s*,?\s*$')


def _extract_escalate_rules(src: str) -> list[tuple[str, str, str, int]]:
    """Return list of (rule_id, field, value, action_line_no) tuples
    for every Action: "escalate" rule in builtin_rules.go."""
    lines = src.splitlines()
    out: list[tuple[str, str, str, int]] = []
    for i, line in enumerate(lines):
        if not _ACTION_ESCALATE_RE.match(line):
            continue
        # Walk backwards up to 20 lines for the field + rule_id.
        field_value: tuple[str, str] | None = None
        rule_id: str | None = None
        for j in range(i - 1, max(0, i - 20), -1):
            if field_value is None:
                fm = _CONDITION_FIELD_RE.search(lines[j])
                if fm:
                    field_value = (fm.group(1), fm.group(2))
            if rule_id is None:
                rm = _RULE_ID_RE.match(lines[j])
                if rm:
                    rule_id = rm.group(1)
                    break
        if field_value and rule_id:
            out.append((rule_id, field_value[0], field_value[1], i + 1))
    return out


def test_all_escalate_rules_documented():
    """Every `Action: "escalate"` rule in builtin_rules.go must be
    either pre-seeded in `_KNOWN_ESCALATE_CHECK_TYPES` OR have its
    value in `MONITORING_ONLY_CHECKS` (Layer 2 backend gate catches
    it for free)."""
    if not _BUILTIN_RULES.exists():
        # CI may not have the appliance dir cloned in some configs.
        return
    monitoring_only = _load_monitoring_only_from_main_py()
    rules = _extract_escalate_rules(_BUILTIN_RULES.read_text())
    assert rules, (
        "no escalate-action rules found in builtin_rules.go — gate "
        "regex is broken OR the file moved"
    )

    undocumented: list[tuple[str, str, str, int]] = []
    for rule_id, field, value, line_no in rules:
        if rule_id in _KNOWN_ESCALATE_CHECK_TYPES:
            seeded_field, seeded_value, _just = _KNOWN_ESCALATE_CHECK_TYPES[rule_id]
            if seeded_field == field and seeded_value == value:
                continue
            # Allowlisted but the value drifted — name it.
            undocumented.append((rule_id, field, value, line_no))
            continue
        # Not allowlisted — is it caught by MONITORING_ONLY_CHECKS?
        if value in monitoring_only:
            continue
        undocumented.append((rule_id, field, value, line_no))

    if not undocumented:
        return

    lines_out = [
        "New / drifted Action: 'escalate' rules in builtin_rules.go:",
    ]
    for rule_id, field, value, line_no in undocumented:
        lines_out.append(
            f"  - builtin_rules.go:{line_no}  {rule_id}  "
            f"({field}={value!r})"
        )
    lines_out.append("")
    lines_out.append(
        "Pick ONE of the 4 branches per rule:\n"
        "  (a) Add the check_type/incident_type value to MONITORING_ONLY_CHECKS\n"
        "      in mcp-server/main.py — Layer 2 backend gate (Session 220\n"
        "      Phase 3 PR-3b) auto-downgrades L1→monitoring for monitoring-\n"
        "      only types.\n"
        "  (b) Add the value to the next mig-306-style backfill IN-list AND\n"
        "      add to _KNOWN_ESCALATE_CHECK_TYPES here with `monitoring-only\n"
        "      — Layer 2 backend gate downgrades` justification.\n"
        "  (c) Add to _KNOWN_ESCALATE_CHECK_TYPES below with explicit\n"
        "      justification (e.g. 'operator review needed; not auto-healable').\n"
        "  (d) Refactor the rule's Action to a healable shape\n"
        "      (`run_windows_runbook` / `run_linux_runbook`) if a runbook\n"
        "      exists — escalate is the LAST resort, not the default.\n"
    )
    raise AssertionError("\n".join(lines_out))


def test_synthetic_undocumented_rule_is_caught(tmp_path):
    """Positive control — inject a synthetic escalate rule with an
    unknown check_type, scan it, confirm the matcher catches it.
    Prevents the gate from silently rotting if the regex breaks."""
    synthetic = tmp_path / "builtin_rules.go"
    synthetic.write_text(
        '''
package healing

var rules = []Rule{
    {
        ID:          "L1-SYNTHETIC-001",
        Name:        "Synthetic test rule",
        Description: "ratchet positive control",
        Conditions: []RuleCondition{
            {Field: "check_type", Operator: OpEquals, Value: "synthetic_fake_check"},
            {Field: "drift_detected", Operator: OpEquals, Value: true},
        },
        Action: "escalate",
    },
}
'''
    )
    rules = _extract_escalate_rules(synthetic.read_text())
    assert any(
        r[0] == "L1-SYNTHETIC-001" and r[1] == "check_type" and r[2] == "synthetic_fake_check"
        for r in rules
    ), "extractor failed to find synthetic escalate rule — gate regex is broken"


def test_synthetic_incident_type_rule_is_caught(tmp_path):
    """Positive control for the `incident_type` field (Gate A P0
    finding). Rule `L1-SERVICE-001` keys on `incident_type` not
    `check_type` — the extractor must accept both."""
    synthetic = tmp_path / "builtin_rules.go"
    synthetic.write_text(
        '''
package healing

var rules = []Rule{
    {
        ID:          "L1-INC-TYPE-001",
        Name:        "incident_type test",
        Conditions: []RuleCondition{
            {Field: "incident_type", Operator: OpEquals, Value: "fake_incident_type"},
        },
        Action: "escalate",
    },
}
'''
    )
    rules = _extract_escalate_rules(synthetic.read_text())
    assert any(
        r[1] == "incident_type" and r[2] == "fake_incident_type"
        for r in rules
    ), "extractor missed `incident_type` field — Gate A P0 not addressed"


def test_known_escalate_check_types_match_source():
    """Negative control: every entry in `_KNOWN_ESCALATE_CHECK_TYPES`
    MUST correspond to an actual escalate-action rule in builtin_rules.go.
    Prevents the allowlist from rotting — stale entries are silently
    forever-allowed. Sibling pattern from test_l2_resolution requires
    documented allowlist entries to be real."""
    if not _BUILTIN_RULES.exists():
        return
    rules = _extract_escalate_rules(_BUILTIN_RULES.read_text())
    source_rule_ids = {r[0] for r in rules}
    stale = set(_KNOWN_ESCALATE_CHECK_TYPES.keys()) - source_rule_ids
    assert not stale, (
        f"_KNOWN_ESCALATE_CHECK_TYPES contains rules not found in "
        f"builtin_rules.go (likely renamed or deleted): {sorted(stale)}. "
        f"Remove stale entries or restore the rule definition."
    )


def test_load_monitoring_only_parses_main_py():
    """Sanity: the regex parser for MONITORING_ONLY_CHECKS in main.py
    finds at least a handful of entries. If main.py refactors the set
    into a different shape, this test catches it before the production
    gate goes silent."""
    monitoring_only = _load_monitoring_only_from_main_py()
    assert len(monitoring_only) >= 5, (
        f"MONITORING_ONLY_CHECKS parse returned only {len(monitoring_only)} "
        f"entries — main.py shape may have changed. Re-check parser regex."
    )
    # Spot-check known entries from the canonical set.
    assert "net_unexpected_ports" in monitoring_only
    assert "net_host_reachability" in monitoring_only

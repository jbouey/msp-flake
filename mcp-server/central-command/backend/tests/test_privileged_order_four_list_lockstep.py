"""Cross-language lockstep: privileged-order types must align across
Python (fleet_cli + attestation), SQL (migration v_privileged_types),
AND Go (appliance/internal/orders/processor.go::dangerousOrderTypes).

The first three are already enforced by
`scripts/check_privileged_chain_lockstep.py` (Session 205). This test
adds the missing fourth seat — the Go-side `dangerousOrderTypes` map
that gates server-pubkey verification at the agent.

Surfaced by the DBA round-table on 2026-04-25: v0.4.12 added
`reprovision` to the Go map; if a future privileged type lands only
on the Python/SQL side, the agent would happily execute it pre-checkin
without verifying the server signature — a chain-of-custody hole.

Semantic distinction (intentional asymmetry, encoded as ALLOWED_GAPS):

* `PRIVILEGED_ORDER_TYPES` (Python) = orders that need a signed
  attestation BUNDLE on the SERVER side before issue. Includes
  watchdog_* + recovery_shell — high-trust admin operations whose
  attestation is created by the API, not the daemon.

* `dangerousOrderTypes` (Go) = orders the AGENT must reject pre-
  checkin (when no server pubkey has been received yet). Includes
  update_daemon, nixos_rebuild, healing, diagnostic — operationally
  dangerous orders that the daemon can refuse to act on without a
  verified server signature. NOT every privileged order ends up here
  (e.g. break_glass_passphrase_retrieval is an admin API call, not a
  fleet order at all).

The asymmetry is allowlisted explicitly. Any drift OUTSIDE the
allowlist fails CI.
"""
from __future__ import annotations

import pathlib
import re
from typing import Set

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
FLEET_CLI = REPO_ROOT / "mcp-server" / "central-command" / "backend" / "fleet_cli.py"
PROCESSOR_GO = REPO_ROOT / "appliance" / "internal" / "orders" / "processor.go"


# Documented asymmetries — order types in one list but legitimately
# absent from the other. Adding to either side requires an explicit
# justification line here.
PYTHON_ONLY: Set[str] = {
    # Phase W0 watchdog orders — consumed by appliance-watchdog (not
    # the main daemon). Different binary, different bearer; they don't
    # flow through processor.go::verifySignature, so dangerousOrderTypes
    # doesn't gate them.
    "watchdog_restart_daemon",
    "watchdog_refetch_config",
    "watchdog_reset_pin_store",
    "watchdog_reset_api_key",
    "watchdog_redeploy_daemon",
    "watchdog_collect_diagnostics",
    # Phase S recovery-shell escape hatch — also watchdog-only.
    "enable_recovery_shell_24h",
    # Session 219 (2026-05-11) — delegate_signing_key is BACKEND-ONLY.
    # The appliance REQUESTS a delegated key from the central server
    # (POST /api/appliances/{id}/delegate-key); the daemon never
    # RECEIVES this as a fleet_order to execute. So dangerousOrderTypes
    # in processor.go doesn't gate it — there's nothing on the daemon
    # side to gate. The privileged-chain attestation lives on the
    # backend issuance path (3 lists: PRIVILEGED_ORDER_TYPES +
    # ALLOWED_EVENTS + mig 305 v_privileged_types).
    "delegate_signing_key",
    # #123 Sub-A 2026-05-17 — bulk_bearer_revoke is BACKEND-ONLY.
    # Gate B fork b029c2d1 caught the original 4-list framing as a
    # P0 — the Go-side entry broke TestDangerousHandlersRegistered
    # because no daemon handler exists. Revocation is a pure
    # server-side UPDATE on site_appliances.bearer_revoked +
    # api_keys.active inside one admin_transaction; the daemon's
    # next checkin hits 401 via shared.py:614-640 short-circuit.
    # Same shape as delegate_signing_key above. 3-list lockstep:
    # PRIVILEGED_ORDER_TYPES + ALLOWED_EVENTS + mig 329 v_privileged_
    # types. Verified by scripts/check_privileged_chain_lockstep.py.
    "bulk_bearer_revoke",
}

GO_ONLY: Set[str] = {
    # Operationally dangerous but not "privileged" in the chain-of-
    # custody sense — they don't require an attestation bundle issued
    # by fleet_cli, but they DO require the agent to verify a server
    # signature. Daemon-internal mechanics that the Python fleet_cli
    # path doesn't gate.
    #
    # `configure_workstation_agent` REMOVED 2026-04-29 (Session 213
    # P3): the entry was in dangerousOrderTypes with no matching
    # handler — dead-list entry. Removed from the Go map in commit
    # 63f4c66e + this test's allowlist in the same lockstep.
    "update_agent",
    "healing",
    "diagnostic",
    "sync_promoted_rule",
    "nixos_rebuild",
    "update_daemon",
    # reprovision is issued by the relocate API endpoint, not by
    # fleet_cli, so it doesn't appear in PRIVILEGED_ORDER_TYPES.
    # Chain-of-custody is provided via the `appliance_relocation`
    # compliance bundle written by the relocate handler. v0.4.12
    # added it to dangerousOrderTypes (Session 210-B audit P1 #164).
    "reprovision",
}


def _extract_python_set(src: str, var_name: str) -> Set[str]:
    """Extract a `VAR = {"a", "b", ...}` Python set literal."""
    m = re.search(
        rf"^{re.escape(var_name)}\s*[:=][^={{]*\{{\s*((?:[^{{}}]+?))\s*\}}",
        src,
        re.MULTILINE | re.DOTALL,
    )
    assert m, f"could not locate {var_name}"
    body = re.sub(r"#.*", "", m.group(1))
    return {
        t.strip().strip('"').strip("'")
        for t in re.split(r"[,\n]+", body)
        if t.strip().strip('"').strip("'")
    }


def _extract_go_map(src: str, var_name: str) -> Set[str]:
    """Extract a Go `var X = map[string]bool{ "a": true, ... }` map literal."""
    m = re.search(
        rf"var\s+{re.escape(var_name)}\s*=\s*map\[string\]bool\{{\s*((?:[^{{}}]|\n)+?)\s*\}}",
        src,
        re.DOTALL,
    )
    assert m, f"could not locate {var_name} in Go source"
    body = re.sub(r"//.*", "", m.group(1))
    out: Set[str] = set()
    for line in body.split("\n"):
        line = line.strip().rstrip(",")
        if not line:
            continue
        km = re.match(r'^"([^"]+)"\s*:\s*true', line)
        if km:
            out.add(km.group(1))
    return out


def test_python_only_allowlist_complete():
    """Every type in PYTHON_ONLY must actually exist in PRIVILEGED_ORDER_TYPES.
    Otherwise the allowlist is documenting non-existent state."""
    py_types = _extract_python_set(FLEET_CLI.read_text(), "PRIVILEGED_ORDER_TYPES")
    stale = PYTHON_ONLY - py_types
    assert not stale, (
        f"PYTHON_ONLY allowlist names types that no longer exist in "
        f"PRIVILEGED_ORDER_TYPES: {sorted(stale)}. Remove them from "
        f"PYTHON_ONLY in this test file."
    )


def test_go_only_allowlist_complete():
    """Every type in GO_ONLY must actually exist in dangerousOrderTypes."""
    go_types = _extract_go_map(PROCESSOR_GO.read_text(), "dangerousOrderTypes")
    stale = GO_ONLY - go_types
    assert not stale, (
        f"GO_ONLY allowlist names types that no longer exist in "
        f"dangerousOrderTypes: {sorted(stale)}. Remove them from "
        f"GO_ONLY in this test file."
    )


def test_python_go_lockstep_modulo_allowlist():
    """The headline check. After subtracting the documented asymmetries,
    Python `PRIVILEGED_ORDER_TYPES` and Go `dangerousOrderTypes` must be
    identical. Any drift = a privileged type one side blocks but the
    other side doesn't = chain-of-custody hole.
    """
    py_types = _extract_python_set(FLEET_CLI.read_text(), "PRIVILEGED_ORDER_TYPES")
    go_types = _extract_go_map(PROCESSOR_GO.read_text(), "dangerousOrderTypes")
    py_core = py_types - PYTHON_ONLY
    go_core = go_types - GO_ONLY

    only_py = py_core - go_core
    only_go = go_core - py_core

    msg_lines = []
    if only_py:
        msg_lines.append(
            f"In Python PRIVILEGED_ORDER_TYPES but missing from Go "
            f"dangerousOrderTypes: {sorted(only_py)}. The agent will "
            f"happily execute these pre-checkin without verifying a "
            f"server signature. Add to dangerousOrderTypes in "
            f"appliance/internal/orders/processor.go AND ship a new "
            f"daemon binary, OR add to PYTHON_ONLY in this test file "
            f"with explicit justification."
        )
    if only_go:
        msg_lines.append(
            f"In Go dangerousOrderTypes but missing from Python "
            f"PRIVILEGED_ORDER_TYPES: {sorted(only_go)}. Either add to "
            f"PRIVILEGED_ORDER_TYPES in fleet_cli.py (and migration "
            f"v_privileged_types — see "
            f"scripts/check_privileged_chain_lockstep.py), OR add to "
            f"GO_ONLY in this test file with explicit justification."
        )
    assert not msg_lines, "\n\n".join(msg_lines)

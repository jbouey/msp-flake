"""FIX-11 (v40, 2026-04-23): install-gate network diagnostic guardrails.

The v39 installer silently booted into a box that couldn't reach Central
Command because DNS returned a Cloudflare IP and MSP_EGRESS pinned
whatever DNS returned at boot (see FIX-9 / test_iso_firewall_no_runtime_dns).
Once the firewall is deterministic, the remaining failure modes are:

  * DNS filter blocks the hostname                → stage=dns
  * Egress ACL blocks TCP/443 to the origin IP    → stage=tcp_443
  * TLS-intercept proxy breaks cert chain         → stage=tls
  * App-side health endpoint down                 → stage=health

FIX-11 adds `run_network_gate_check()` to `msp-auto-provision.service` that
runs each stage and writes `/var/lib/msp/install_gate_status.json`, and
extends the beacon at :8443 to surface the result as `state` +
`install_gate_status`. Non-blocking: diagnostic, not halt.

These tests guard against accidental removal of that diagnostic surface.
Pure source-level checks — no nix build, no systemd, no Postgres.
"""
from __future__ import annotations

import pathlib
import re

# tests/ → backend/ → central-command/ → mcp-server/ → <repo-root>
REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
NIX_PATH = REPO_ROOT / "iso" / "appliance-disk-image.nix"

SOURCE = NIX_PATH.read_text()


def _auto_provision_script() -> str:
    """Return the `script = ''...'';` body of msp-auto-provision.service.

    Deliberately dumb-match: anchored on the service name preceding the
    script block and the unique `# MSP-DATA partition recovery` comment
    that terminates the auto-provision section. If the surrounding
    structure is refactored, update the anchors — do NOT delete the
    test.
    """
    m = re.search(
        r"systemd\.services\.msp-auto-provision\s*=\s*\{(.*?)"
        r"#\s*MSP-DATA partition recovery",
        SOURCE,
        re.DOTALL,
    )
    assert m is not None, (
        "Could not locate `systemd.services.msp-auto-provision` block "
        "terminated by the `# MSP-DATA partition recovery` comment. "
        "Update the anchor — do NOT delete this test. It guards the "
        "FIX-11 install-gate diagnostic."
    )
    return m.group(1)


def _beacon_refresh_script() -> str:
    """Return the `script = ''...'';` body of msp-beacon-refresh.service."""
    m = re.search(
        r"systemd\.services\.msp-beacon-refresh\s*=\s*\{(.*?)"
        r"systemd\.timers\.msp-beacon-refresh",
        SOURCE,
        re.DOTALL,
    )
    assert m is not None, (
        "Could not locate `systemd.services.msp-beacon-refresh` block "
        "followed by its `.timers.` unit. Update the anchor — do NOT "
        "delete this test. It guards the FIX-11 beacon surface."
    )
    return m.group(1)


def test_run_network_gate_check_function_exists():
    """The gate function itself must be defined in msp-auto-provision."""
    body = _auto_provision_script()
    assert "run_network_gate_check()" in body, (
        "FIX-11 regression: `run_network_gate_check()` function is "
        "missing from msp-auto-provision.service. The install-gate "
        "diagnostic — DNS → TCP/443 → TLS → /health — must be defined "
        "so the beacon can surface which stage fails. Re-add per "
        ".agent/plans/v40-complete-iso.md §FIX-11."
    )


def test_install_gate_status_file_path_written():
    """Gate function must write the status JSON to the canonical path."""
    body = _auto_provision_script()
    assert "/var/lib/msp/install_gate_status.json" in body, (
        "FIX-11 regression: the install-gate status file path "
        "`/var/lib/msp/install_gate_status.json` is not referenced in "
        "msp-auto-provision. The beacon reads this exact path — if the "
        "gate writes somewhere else, the beacon state classifier "
        "silently falls back to `auth_or_network_failing` and the "
        "operator loses the stage-level diagnostic."
    )


def test_gate_runs_before_phase1():
    """The gate must run at least once BEFORE the Phase 1 retry loop —
    otherwise the beacon has nothing to report during the first 60 s
    of boot, which is exactly when an on-site operator is watching."""
    body = _auto_provision_script()
    # Find the Phase 1 comment and assert a call to run_network_gate_check
    # appears within the ~30 lines preceding it.
    phase1_match = re.search(
        r"#\s*Phase 1: Initial connectivity retries", body
    )
    assert phase1_match is not None, (
        "Could not find the `Phase 1: Initial connectivity retries` "
        "comment — the auto-provision structure changed. Update this "
        "test's anchor."
    )
    preceding = body[: phase1_match.start()]
    # Last ~1500 chars should contain the gate call.
    assert "run_network_gate_check" in preceding[-2000:], (
        "FIX-11 regression: `run_network_gate_check` is not invoked "
        "before the Phase 1 retry loop. The beacon would have no "
        "install_gate_status until Phase 3 (5 min later) — too late for "
        "a site operator who's watching boot."
    )


def test_gate_runs_inside_phase3_retry_loop():
    """The gate must refresh every slow-retry tick (~5 min) so the beacon
    reflects the CURRENT failure stage, not a stale one from first boot."""
    body = _auto_provision_script()
    # The Phase 3 loop body is `while [ ! -f "$CONFIG_PATH" ]; do … done`.
    # Grab the last such loop in the script and assert the call appears.
    m = re.search(
        r"while \[ ! -f \"\$CONFIG_PATH\" \]; do(.*?)done",
        body,
        re.DOTALL,
    )
    assert m is not None, (
        "Could not find Phase 3 slow-retry loop "
        "(`while [ ! -f \"$CONFIG_PATH\" ]; do … done`). Structure "
        "changed — update the anchor."
    )
    loop_body = m.group(1)
    assert "run_network_gate_check" in loop_body, (
        "FIX-11 regression: `run_network_gate_check` is not called "
        "inside the Phase 3 slow-retry loop. install_gate_status.json "
        "will stale 5+ minutes after boot, defeating its purpose as a "
        "live diagnostic for the on-site operator."
    )


def test_gate_four_stages_present():
    """Each of the 4 stage names must be a distinguishable value so the
    beacon can classify failures sharply (DNS filter vs egress ACL vs
    TLS-intercept vs app-down)."""
    body = _auto_provision_script()
    for stage in ("dns", "tcp_443", "tls", "health"):
        assert re.search(
            rf'last_failed="{re.escape(stage)}"', body
        ), (
            f"FIX-11 regression: gate stage `{stage}` is not assigned to "
            "`last_failed` anywhere in run_network_gate_check. The "
            "beacon relies on these 4 values to distinguish failure "
            "modes — dropping one collapses two failure classes into "
            "one indistinguishable state."
        )


def test_beacon_reads_install_gate_status():
    """The beacon must read the gate status file — otherwise install_gate
    diagnostics are written but invisible."""
    body = _beacon_refresh_script()
    assert "/var/lib/msp/install_gate_status.json" in body, (
        "FIX-11 regression: msp-beacon-refresh does not read "
        "`/var/lib/msp/install_gate_status.json`. The gate writes the "
        "file but no one reads it — the on-LAN operator curling :8443 "
        "sees no stage-level diagnostic. Re-add the read + JSON "
        "surface per FIX-11."
    )


def test_beacon_state_classifier_has_network_gate_failing():
    """The state classifier must have a `network_gate_failing` branch —
    otherwise a gate failure collapses into `auth_or_network_failing`,
    which is what we're trying to differentiate away from."""
    body = _beacon_refresh_script()
    assert 'state="network_gate_failing"' in body, (
        "FIX-11 regression: beacon state classifier is missing the "
        "`network_gate_failing` branch. Without it, a gate-diagnosed "
        "failure (stage=tcp_443, tls, health) gets reported as the "
        "generic `auth_or_network_failing`, which is the exact ambiguity "
        "FIX-11 was introduced to eliminate."
    )


def test_beacon_json_surfaces_install_gate_status():
    """The JSON written to /var/lib/msp/beacon.json must include
    `install_gate_status` so the LAN operator can see the full gate
    result, not just the rolled-up state."""
    body = _beacon_refresh_script()
    # Accept either "install_gate_status": $var or bare install_gate_status
    # injection — both shapes are valid, we just need the key in the JSON.
    assert re.search(
        r'"install_gate_status"\s*:', body
    ), (
        "FIX-11 regression: beacon JSON output is missing the "
        "`install_gate_status` key. The rolled-up `state` field tells "
        "the operator which stage is broken, but the full object "
        "(with dns IP, http_code, last_error) is the actionable "
        "payload. Keep it on the JSON."
    )


def test_beacon_state_precedence_gate_before_generic_fallback():
    """Structural: the `network_gate_failing` branch must appear BEFORE
    the `auth_or_network_failing` branch in the if/elif chain.

    Otherwise the generic fallback wins first and the specific
    stage-level diagnosis is never reached. Pure ordering check — we
    look for the indices of both assignments in the script.
    """
    body = _beacon_refresh_script()
    gate_idx = body.find('state="network_gate_failing"')
    generic_idx = body.find('state="auth_or_network_failing"')
    assert gate_idx != -1, (
        "FIX-11 regression: `network_gate_failing` branch missing "
        "(covered by test_beacon_state_classifier_has_network_gate_failing, "
        "but required here for ordering check too)."
    )
    assert generic_idx != -1, (
        "`auth_or_network_failing` branch missing — unrelated regression."
    )
    assert gate_idx < generic_idx, (
        "FIX-11 regression: the `network_gate_failing` branch appears "
        f"AFTER `auth_or_network_failing` (indices {gate_idx} vs "
        f"{generic_idx}). In an if/elif chain, later branches are "
        "unreachable when an earlier branch matches. The specific "
        "stage-level diagnosis would never surface."
    )

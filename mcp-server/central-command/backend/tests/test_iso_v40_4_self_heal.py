"""v40.4 (2026-04-23): self-heal + idempotency + non-blocking gate
+ narrow rescue sudo for the appliance provisioning pipeline.

Context: v40.3 fixed the inetutils/host classpath bug and the em-dash
beacon SyntaxError, but shipped with three remaining reliability gaps
that bit us on reflash day:

  1. `run_network_gate_check` stage-1 DNS was a command substitution
     under `set -euo pipefail` with no fallback. A transient DNS race
     at boot (resolvconf still being written when the service fires)
     = fatal exit 1, no config.yaml, daemon restart-loops forever.
     The FUNCTION HEADER literally says "Non-blocking by design" but
     the implementation contradicted it.

  2. `msp-auto-provision.service` was `Type=oneshot` with no `Restart=`.
     Any exit != 0 was permanent until power-cycle — there was no
     automatic retry of a transient failure. One .242 box sat
     failed-forever while .246 (same ISO, same network) succeeded by
     DNS-ready timing luck.

  3. The script re-ran on every boot even when it had already
     succeeded. Re-provisioning a working appliance churned
     `api_keys` rows and burned server time. Needed an idempotency
     marker.

  4. The `msp` user had read-only sudo (FIX-12) — operator SSH could
     diagnose a wedged box but could NOT `systemctl restart` the
     single service that would un-wedge it. The operator had to grab
     the break-glass passphrase (5/hr admin-API rate-limited + audit-
     logged) just to restart one narrow unit. Narrow NOPASSWD for
     restart/start of msp-auto-provision closes this gap without
     weakening the rest of the sudo posture.

These tests guard against each of the four regressions landing back
in the tree. They are source-level assertions against the two
declaration files; they do not replace the QEMU-boot integration
test that's on the roadmap.
"""
from __future__ import annotations

import pathlib
import re

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent.parent
_DISK_IMAGE_NIX = _REPO_ROOT / "iso" / "appliance-disk-image.nix"
_CONFIG_NIX = _REPO_ROOT / "iso" / "configuration.nix"


def _src(path: pathlib.Path) -> str:
    assert path.exists(), f"{path} not found"
    return path.read_text()


def _msp_auto_provision_block() -> str:
    src = _src(_DISK_IMAGE_NIX)
    start = src.find("systemd.services.msp-auto-provision")
    assert start > 0, "msp-auto-provision declaration missing"
    # ~500 lines — large enough to cover all success sites and both
    # unitConfig/serviceConfig blocks.
    return src[start : start + 30000]


def test_dns_stage_is_non_blocking():
    """v40.4 regression: stage 1 DNS probe MUST NOT fatal-exit the
    script. The command substitution that captures `dns_ip` must have
    `|| true` (or equivalent fallback) so a boot-time DNS race doesn't
    brick the appliance. The function's own header docstring says
    "Non-blocking by design" — the test holds the impl to that.
    """
    block = _msp_auto_provision_block()
    # Locate the exact stage-1 DNS capture.
    m = re.search(
        r"dns_ip=\$\(\$\{pkgs\.bind\.host\}/bin/host[^\n]*\n[^\n]*awk[^\n]*\)(\s*\|\|\s*true)?",
        block,
    )
    assert m is not None, (
        "v40.4 regression: stage-1 DNS capture not found in expected "
        "form. If you refactored the gate, update this test and keep "
        "the non-blocking property."
    )
    assert m.group(1) and "true" in m.group(1), (
        "v40.4 regression: stage-1 DNS capture is NOT followed by "
        "`|| true`. Without it, `set -euo pipefail` + a failing host "
        "lookup fatal-exits the script — exactly the bug that bricked "
        ".242 on v40.3. Restore the `|| true` fallback."
    )


def test_service_restarts_on_failure():
    """v40.4 regression: msp-auto-provision MUST restart on transient
    failure. Without Restart=on-failure it's a one-shot with no retry,
    and the first DNS/provision-endpoint hiccup becomes permanent."""
    block = _msp_auto_provision_block()
    # serviceConfig block
    sc_start = block.find("serviceConfig = {")
    sc_end = block.find("};", sc_start)
    assert sc_start > 0 and sc_end > sc_start
    sc = block[sc_start:sc_end]
    assert 'Restart = "on-failure"' in sc, (
        "v40.4 regression: msp-auto-provision.serviceConfig lost "
        'Restart = "on-failure". A transient boot-time DNS race now '
        "bricks the appliance permanently until power cycle."
    )
    assert "RestartSec" in sc, (
        "v40.4 regression: RestartSec missing. Restart=on-failure "
        "without a backoff can tight-loop a wedged appliance."
    )
    # StartLimitBurst lives in unitConfig, not serviceConfig.
    uc_start = block.find("unitConfig = {")
    uc_end = block.find("};", uc_start)
    assert uc_start > 0 and uc_end > uc_start
    uc = block[uc_start:uc_end]
    assert "StartLimitBurst" in uc and "StartLimitIntervalSec" in uc, (
        "v40.4 regression: StartLimitBurst/StartLimitIntervalSec "
        "missing. Without them a truly-broken config (bad pubkey, "
        "unreachable API) restart-spins forever."
    )


def test_idempotency_marker_guard():
    """v40.4 regression: successful-provision marker + early-exit
    guard must be present. Without the guard, the script re-runs the
    whole /api/provision/{mac} flow on every boot — churns api_keys,
    wastes CC CPU, races against the daemon's own rekey path."""
    block = _msp_auto_provision_block()
    # Early exit guard near the top of the script
    assert "PROVISION_SUCCESS_MARKER" in block, (
        "v40.4 regression: PROVISION_SUCCESS_MARKER variable no "
        "longer defined in msp-auto-provision script. The idempotency "
        "guard depends on it."
    )
    # All three success sites (Phase 1, Phase 2, Phase 3) must touch
    # the marker before exit 0.
    success_log_lines = re.findall(
        r'log "SUCCESS: Provisioning complete[^"]*"', block
    )
    assert len(success_log_lines) >= 3, (
        "v40.4 regression: fewer than 3 SUCCESS log lines — the "
        "phase 1/2/3 structure of msp-auto-provision has changed. "
        "Update this test to cover the new structure, and make sure "
        "every success path touches PROVISION_SUCCESS_MARKER."
    )
    # Count touch-marker lines. Must be at least once per success
    # log line (allowing for future expansion).
    touch_count = block.count('touch "$PROVISION_SUCCESS_MARKER"')
    assert touch_count >= len(success_log_lines), (
        f"v40.4 regression: {len(success_log_lines)} SUCCESS paths "
        f"but only {touch_count} touch-marker sites. Every success "
        f"must leave the marker so the idempotency guard short-"
        f"circuits on subsequent boots."
    )
    # Guard itself — early `[ -f "$PROVISION_SUCCESS_MARKER" ] ... exit 0`
    guard = re.search(
        r'if \[ -f "\$PROVISION_SUCCESS_MARKER" \][^\n]*\n[^\n]*\n[^\n]*exit 0',
        block,
    )
    assert guard is not None, (
        "v40.4 regression: early-exit idempotency guard missing. "
        "Without it, every reboot re-runs the whole provision flow."
    )


def test_msp_sudo_nopasswd_stays_read_only():
    """v40.4 (2026-04-23): an earlier pass added NOPASSWD entries for
    `systemctl restart msp-auto-provision.service` + `start ...` to
    give operator SSH a rescue path without the break-glass passphrase.
    The FIX-12 regression test (test_iso_msp_narrow_sudo.
    test_no_write_verbs_in_whitelist) caught that broadening on CI —
    `restart` + `start` are write verbs that turn the NOPASSWD
    surface into a privilege-escalation ramp. The recovery path stays
    on the break-glass passphrase for rescue; v40.4+ has
    `Restart=on-failure` + `StartLimitBurst=10` so the self-heal path
    handles transient failures without operator intervention in the
    first place."""
    src = _src(_CONFIG_NIX)
    for banned_token in (
        "systemctl restart msp-auto-provision",
        "systemctl start msp-auto-provision",
        "systemctl stop msp-auto-provision",
    ):
        assert banned_token not in src, (
            f"v40.4 posture violation: `{banned_token}` appears in "
            f"iso/configuration.nix — the NOPASSWD surface must stay "
            f"read-only. Rescue via break-glass passphrase instead."
        )

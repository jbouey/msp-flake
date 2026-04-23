"""FIX-12 (v40, 2026-04-23): msp narrow NOPASSWD sudo guardrails.

On-site operator burned ~45 min on 2026-04-23 at a physical console because
every diagnostic invocation (`iptables -L`, `ss -tulpn`, `journalctl -u
firewall`) prompted for the 43-char Phase R break-glass passphrase.

FIX-12 grants `msp` user NOPASSWD sudo for a narrow whitelist of READ-ONLY
commands so the on-site operator can diagnose firewall / networking /
service state without typing the passphrase. No writes, no restarts,
no kills. Root stays locked.

These tests guard against two classes of regression:
  1. Accidental removal of the whitelist (would restore the 45-min
     passphrase-tax each site visit).
  2. Accidental broadening into write/restart/kill territory (would
     turn the NOPASSWD surface into a privilege-escalation ramp).

Pure source-level checks — no nix build, no system install.
"""
from __future__ import annotations

import pathlib
import re

# tests/ → backend/ → central-command/ → mcp-server/ → <repo-root>
REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
CONFIGURATION_NIX = REPO_ROOT / "iso" / "configuration.nix"
APPLIANCE_NIX = REPO_ROOT / "iso" / "appliance-disk-image.nix"

CONFIG_SRC = CONFIGURATION_NIX.read_text()
APPLIANCE_SRC = APPLIANCE_NIX.read_text()


def _msp_sudo_rules_block() -> str:
    """Return the body of the `commands = [ ... ];` list inside the
    `users = [ "msp" ]` stanza of security.sudo.extraRules.

    Uses bracket-balancing instead of a regex because the commands
    list contains nested `options = [ ... ]` attrsets that trip up
    non-greedy regex matching.
    """
    anchor = re.search(
        r'users\s*=\s*\[\s*"msp"\s*\]\s*;', CONFIG_SRC
    )
    assert anchor is not None, (
        "FIX-12 regression: could not locate the `users = [ \"msp\" ];` "
        "stanza in iso/configuration.nix. The narrow-sudo whitelist is "
        "missing or moved. Re-add per "
        ".agent/plans/v40-complete-iso.md §FIX-12."
    )
    cmds_kw = "commands = ["
    cmds_idx = CONFIG_SRC.find(cmds_kw, anchor.end())
    assert cmds_idx != -1, (
        "FIX-12 regression: the `users = [ \"msp\" ]` stanza is present "
        "but has no following `commands = [ ... ]` list. Whitelist was "
        "stripped."
    )
    start = cmds_idx + len(cmds_kw)
    depth = 1
    i = start
    while i < len(CONFIG_SRC) and depth > 0:
        c = CONFIG_SRC[i]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
        i += 1
    assert depth == 0, (
        "FIX-12 regression: bracket-balance walk never found the outer "
        "`]` closer. Source file may be malformed."
    )
    return CONFIG_SRC[start : i - 1]


def test_extra_rules_block_exists():
    """The base stanza must exist."""
    body = _msp_sudo_rules_block()
    assert len(body.strip()) > 100, (
        "FIX-12 regression: msp sudo rules stanza is present but "
        "suspiciously short (<100 chars of command list). Did someone "
        "strip the whitelist? Should contain at least iptables, ss, "
        "systemctl, journalctl, and cat rules."
    )


def test_required_diagnostic_commands_present():
    """The plan's minimum-viable whitelist must be present so the on-site
    operator has the commands they need for firewall / network / service
    diagnosis without the passphrase."""
    body = _msp_sudo_rules_block()
    required = [
        # Firewall inspection — core reason FIX-12 exists.
        "/run/current-system/sw/bin/iptables -L",
        "/run/current-system/sw/bin/ip6tables -L",
        # Socket stats — port listener visibility.
        "/run/current-system/sw/bin/ss -tulpn",
        # Service status.
        "/run/current-system/sw/bin/systemctl status *",
        # Per-unit journal.
        "/run/current-system/sw/bin/journalctl -u *",
        # Config / hosts / resolver.
        "/run/current-system/sw/bin/cat /var/lib/msp/config.yaml",
        "/run/current-system/sw/bin/cat /etc/resolv.conf",
        "/run/current-system/sw/bin/cat /etc/hosts",
    ]
    for cmd in required:
        assert cmd in body, (
            f"FIX-12 regression: required command `{cmd}` is missing "
            "from the msp NOPASSWD whitelist. See plan §FIX-12 for the "
            "minimum-viable set."
        )


def test_every_command_has_nopasswd():
    """Every entry in the msp commands list MUST carry NOPASSWD —
    otherwise the rule is dead weight (wheelNeedsPassword=true covers
    the path with-password)."""
    body = _msp_sudo_rules_block()
    # Find each `{ command = "..."; options = [ ... ]; }` entry.
    for match in re.finditer(
        r"\{\s*command\s*=\s*\"([^\"]+)\"\s*;\s*options\s*=\s*\[([^\]]*)\]\s*;\s*\}",
        body,
    ):
        cmd, options = match.group(1), match.group(2)
        assert '"NOPASSWD"' in options, (
            f"FIX-12 regression: msp sudo entry for `{cmd}` is missing "
            "NOPASSWD. Without NOPASSWD the rule is dead weight — the "
            "same command would prompt for the Phase R passphrase "
            "anyway via the baseline wheel rule."
        )


def test_no_write_verbs_in_whitelist():
    """The whitelist MUST be read-only. This test fails loudly if anyone
    adds a write/restart/start/stop/kill/reload verb."""
    body = _msp_sudo_rules_block()
    # Check every `command = "..."` value for banned substrings.
    banned = {
        " restart ": "restart is a write operation (service state change)",
        " start ": "start is a write operation",
        " stop ": "stop is a write operation",
        " reload ": "reload is a write operation",
        " kill ": "kill is a destructive operation",
        " -K": "ss -K / iptables -K kills sockets/rules",
        "--kill": "ss --kill kills sockets",
        " -F": "iptables -F flushes rules (destructive)",
        " -X": "iptables -X deletes chains (destructive)",
        " -D": "iptables -D deletes rules / systemctl -D irrelevant but ambiguous",
        "vacuum": "journalctl --vacuum wipes logs",
        "/run/current-system/sw/bin/rm": "rm is inherently destructive",
        "/run/current-system/sw/bin/nixos-rebuild": "nixos-rebuild mutates system",
        "/run/current-system/sw/bin/sh": "shell access = unbounded escalation",
        "/run/current-system/sw/bin/bash": "shell access = unbounded escalation",
        "/bin/sh": "shell path = unbounded escalation",
        "/bin/bash": "shell path = unbounded escalation",
    }
    for match in re.finditer(r'command\s*=\s*"([^"]+)"', body):
        cmd = match.group(1)
        for needle, reason in banned.items():
            assert needle not in cmd, (
                f"FIX-12 regression: msp sudo whitelist contains "
                f"`{cmd}` which includes the banned substring "
                f"`{needle.strip()}`. Reason: {reason}. The NOPASSWD "
                "surface MUST remain strictly read-only — broadening "
                "it is a privilege-escalation ramp. If you genuinely "
                "need a write op without the passphrase, add it to "
                "the watchdog fleet-order handler instead."
            )


def test_wheel_still_requires_password():
    """Mutating actions still require the passphrase via the baseline
    wheel path. `wheelNeedsPassword = true` is non-negotiable."""
    assert re.search(
        r"wheelNeedsPassword\s*=\s*true\s*;", CONFIG_SRC
    ), (
        "FIX-12 regression: `wheelNeedsPassword = true` is missing or "
        "changed. That setting is the SOLE gate that forces mutating "
        "operations (restart, edit, etc.) to cost the Phase R "
        "passphrase. Leaving it at false would collapse the narrow "
        "NOPASSWD whitelist into 'full passwordless sudo for msp' — "
        "exactly the posture this file is designed to prevent."
    )


def test_root_stays_locked():
    """Root account must remain locked — the narrow NOPASSWD whitelist
    is meaningless if root itself can log in with a password."""
    m = re.search(
        r'users\.users\.root\.hashedPassword\s*=\s*lib\.mkDefault\s*"!"\s*;',
        APPLIANCE_SRC,
    )
    assert m is not None, (
        "FIX-12 regression: `users.users.root.hashedPassword = "
        'lib.mkDefault "!";` is missing from iso/appliance-disk-image.nix. '
        "Root must stay locked. If someone unlocked root, the narrow "
        "msp sudo whitelist loses its meaning — an attacker with any "
        "msp read access can observe and then target root directly."
    )


def test_no_restart_appliance_daemon_bypass():
    """The pre-FIX-12 rule `systemctl restart appliance-daemon` is
    BANNED from reappearing. Post-watchdog, restart goes through the
    watchdog fleet-order handler. Leaving a NOPASSWD restart path is
    redundant and weakens the posture."""
    body = _msp_sudo_rules_block()
    assert "restart appliance-daemon" not in body, (
        "FIX-12 regression: the NOPASSWD `systemctl restart "
        "appliance-daemon` rule was deliberately removed in v40. It "
        "reappeared. Post-watchdog the restart path belongs to the "
        "watchdog_restart_daemon fleet order (auth'd via Central "
        "Command). Keep this rule deleted."
    )

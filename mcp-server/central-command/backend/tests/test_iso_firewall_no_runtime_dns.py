"""FIX-13 (v40, 2026-04-23): prevent regression of FIX-9.

The v39 installer shipped a `networking.firewall.extraCommands` block
that did `host -t A api.osiriscare.net` at rule-apply time and pinned
whatever IPs DNS returned. Cloudflare rotates its frontend IPs under
that name, so the pinned allowlist went stale and the appliance daemon
silently lost egress — observed on 84:3A:5B:1D:0F:E5 at 2026-04-23.

The v40 fix (FIX-9) pins `api.osiriscare.net → 178.156.162.116` via
`networking.extraHosts` and hardcodes the origin IP in the iptables
allowlist — no runtime DNS inside the firewall generator.

This test is the regression guard. It fails the CI build if anyone
adds runtime DNS back into the firewall block, or strips either the
extraHosts pin or the hardcoded origin allow rule.

Pure unit test — no nix build, no Postgres. Reads the .nix file and
asserts structural properties.
"""
from __future__ import annotations

import pathlib
import re

# Repo root is 4 levels up from this file:
# tests/ -> backend/ -> central-command/ -> mcp-server/ -> <repo-root>
REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
NIX_PATH = REPO_ROOT / "iso" / "appliance-disk-image.nix"

SOURCE = NIX_PATH.read_text()
ORIGIN_IP = "178.156.162.116"
API_NAME = "api.osiriscare.net"


def _extra_commands_block() -> str:
    """Return the literal text of the `networking.firewall.extraCommands`
    block (the multi-line shell string). Uses a deliberately dumb
    delimiter match rather than a nix parser — the extraCommands value
    is always a `''...''` string literal and the firewall block is
    uniquely named, so this is stable against unrelated edits.
    """
    # Find "extraCommands = ''" and grab everything up to the closing "''"
    # that terminates it. We anchor on "extraStopCommands" which always
    # follows — if someone ever renames that, this test fails loudly and
    # the author updates the anchor deliberately.
    m = re.search(
        r"extraCommands\s*=\s*''(.*?)''\s*;\s*\n\s*extraStopCommands",
        SOURCE,
        re.DOTALL,
    )
    assert m is not None, (
        "Could not locate `extraCommands = '' ... ''; extraStopCommands` "
        "block in iso/appliance-disk-image.nix. If the firewall structure "
        "was refactored, update this test's anchor — do NOT delete the "
        "test. It guards against the v39→v40 runtime-DNS regression."
    )
    return m.group(1)


def test_no_runtime_dns_lookup_in_firewall_block():
    """FIX-9 core assertion: no `host -t` inside the firewall generator.

    The v39 pre-fix code called `host -t A api.osiriscare.net` and
    `host -t AAAA api.osiriscare.net` at rule-apply time. Those pinned
    whatever Cloudflare's rotating DNS returned, and when CF rotated,
    the allowlist went stale. Never again.
    """
    block = _extra_commands_block()
    # Strip comment lines so a future explanatory comment mentioning
    # `host -t` historically doesn't trip the check.
    non_comment = "\n".join(
        line for line in block.splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "host -t" not in non_comment, (
        "FIX-9 regression: `host -t` appeared inside "
        "`networking.firewall.extraCommands` (excluding comments). "
        "Runtime DNS inside the firewall generator is banned — it pins "
        "rotating Cloudflare IPs and goes stale silently. Hardcode the "
        "origin IP instead, and rely on `networking.extraHosts` + the "
        "`msp-egress-selfheal` timer (FIX-10). See "
        ".agent/plans/v40-complete-iso.md §FIX-9 for the incident "
        "that drove this guardrail."
    )


def test_origin_ip_hardcoded_allow_rule():
    """FIX-9 core assertion: origin IP is an explicit allow rule.

    With DNS pinning gone, there must be a deterministic rule that
    lets the origin VPS through on TCP/443. The exact form is a hard-
    coded `iptables -A MSP_EGRESS -p tcp -d 178.156.162.116/32 --dport
    443 -j RETURN`. If someone strips it, the daemon loses egress and
    the box goes dark.
    """
    block = _extra_commands_block()
    # We require the IP to appear attached to an iptables ACCEPT/RETURN
    # rule in the extraCommands block (not merely mentioned in a comment).
    non_comment = "\n".join(
        line for line in block.splitlines()
        if not line.lstrip().startswith("#")
    )
    assert ORIGIN_IP in non_comment, (
        f"FIX-9 regression: origin IP {ORIGIN_IP} is not present as an "
        "allow rule inside `networking.firewall.extraCommands`. The "
        "origin VPS must be explicitly allowlisted for TCP/443 — "
        "without this, the daemon cannot reach Central Command and the "
        "appliance silently fails to provision. See "
        ".agent/plans/v40-complete-iso.md §FIX-9."
    )
    # Stronger structural check: the IP must appear with `--dport 443`
    # on the same logical rule. Accept either `178.156.162.116` or
    # `178.156.162.116/32` as the -d operand.
    rule_pattern = re.compile(
        r"iptables\s+-A\s+MSP_EGRESS\s+.*?-d\s+"
        + re.escape(ORIGIN_IP)
        + r"(?:/32)?\s+.*?--dport\s+443\s+.*?-j\s+RETURN",
        re.DOTALL,
    )
    assert rule_pattern.search(non_comment), (
        f"FIX-9 regression: expected an iptables rule of the shape "
        f"`iptables -A MSP_EGRESS -p tcp -d {ORIGIN_IP}/32 --dport 443 "
        "-j RETURN` inside the firewall block. Either the rule was "
        "removed, reordered into a non-MSP_EGRESS chain, or the port "
        "changed. Verify the origin is still reachable before merging."
    )


def test_extra_hosts_pins_api_to_origin():
    """FIX-9 core assertion: `networking.extraHosts` resolves the API
    name to the origin IP.

    Without this pin, a fresh userspace resolver (daemon, curl, etc.)
    would hit public DNS → Cloudflare → rotating IPs → firewall drop.
    With the pin, every local resolution returns the origin IP, which
    the hardcoded iptables rule allows.
    """
    # Look for the extraHosts attribute ANYWHERE in the networking
    # attrset — syntax is `extraHosts = '' ... '';` in a multi-line
    # string literal.
    m = re.search(
        r"extraHosts\s*=\s*''(.*?)''",
        SOURCE,
        re.DOTALL,
    )
    assert m is not None, (
        "FIX-9 regression: `networking.extraHosts` is missing from "
        "iso/appliance-disk-image.nix. Without the pin, the daemon "
        "resolves api.osiriscare.net via public DNS and hits Cloudflare "
        "IPs that the firewall does not allow. Re-add the block per "
        ".agent/plans/v40-complete-iso.md §FIX-9."
    )
    body = m.group(1)
    # Accept any whitespace between the IP and the name, case-
    # insensitive on the hostname.
    pin_pattern = re.compile(
        r"^\s*"
        + re.escape(ORIGIN_IP)
        + r"\s+"
        + re.escape(API_NAME)
        + r"\s*$",
        re.MULTILINE | re.IGNORECASE,
    )
    assert pin_pattern.search(body), (
        f"FIX-9 regression: `networking.extraHosts` exists but does not "
        f"pin `{API_NAME}` to `{ORIGIN_IP}`. If the origin IP changed, "
        "update BOTH the extraHosts pin AND the iptables allow rule in "
        "`extraCommands` — they must stay in lockstep."
    )


def test_release_hostname_not_reintroduced():
    """`release.osiriscare.net` was removed from the download allowlist
    in Session 201 (no A record exists). If it shows up in the ISO
    again, someone reintroduced a broken dependency.
    """
    assert "release.osiriscare.net" not in SOURCE, (
        "release.osiriscare.net is present in iso/appliance-disk-image.nix. "
        "This hostname has no A record (per CLAUDE.md) and was stripped "
        "in Session 201. Use api.osiriscare.net/updates/<binary> instead."
    )

"""v40.3 (2026-04-23): classpath + syntax regression guards for the
installed-system image.

Context: v40.0 through v40.2 all shipped with
    ${pkgs.inetutils}/bin/host
embedded in msp-auto-provision.service's FIX-11 network gate. In the
pinned nixpkgs revision (b134951a), `inetutils-2.5` no longer ships
`host` -- that binary moved to the `bind.host` split output. The
missing binary combined with `set -euo pipefail` exited the provision
script with status 127 on the FIRST external command after "Boot
source:" logged. Result: no config.yaml written, appliance-daemon
restart-looped forever, zero checkins, zero breakglass submissions,
LAN beacon unreachable. Three reflashed appliances bricked on the
same day (2026-04-23).

A second, independent bug in v40.x: `msp-status-beacon.py` had a
non-ASCII em-dash (U+2014) inside a `b'...'` bytes literal on line
21. Python 3 requires bytes literals to be ASCII-only. The beacon
service restart-looped with SyntaxError at import time, so port 8443
never opened.

These tests are pure source-level guards. They do not replace a
full runtime integration test (still TBD) but they block the two
concrete classes of failure we observed from re-entering the tree.
"""
from __future__ import annotations

import pathlib
import re

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent.parent
_DISK_IMAGE_NIX = _REPO_ROOT / "iso" / "appliance-disk-image.nix"


def _src() -> str:
    assert _DISK_IMAGE_NIX.exists()
    return _DISK_IMAGE_NIX.read_text()


def test_inetutils_slash_host_is_banned():
    """`${pkgs.inetutils}/bin/host` must never appear again. `host`
    lives in `pkgs.bind.host` in this nixpkgs pin, and the
    inetutils attribute path silently resolves to a derivation that
    does not contain the binary -- the classic "nix closure scan
    sees the store path, the binary doesn't exist" footgun.

    Allowed inetutils binaries (present in the derivation's bin/):
    hostname, ping, ftp, telnet, traceroute, whois, ifconfig.
    `hostname` IS still in inetutils -- the ban is specifically on
    `host`.
    """
    src = _src()
    assert "${pkgs.inetutils}/bin/host " not in src and \
           "${pkgs.inetutils}/bin/host\n" not in src and \
           "${pkgs.inetutils}/bin/host\t" not in src, (
        "v40.3 regression: ${pkgs.inetutils}/bin/host reappeared in "
        "iso/appliance-disk-image.nix. This binary does not exist "
        "in the pinned nixpkgs's inetutils -- use ${pkgs.bind.host}/bin/host "
        "instead. See test docstring for the full incident."
    )
    # Guard against the subtle pattern where someone might later use
    # inetutils for the `host` binary via a different quote form.
    # Match `${pkgs.inetutils}/bin/host` only when followed by a
    # word-boundary-like char (not when it's the start of a longer
    # binary name like `hostname`).
    bad = re.search(r"\$\{pkgs\.inetutils\}/bin/host(?!name)", src)
    assert bad is None, (
        f"v40.3 regression: found banned inetutils/host reference at "
        f"offset {bad.start() if bad else '?'}. Switch to pkgs.bind.host."
    )


def test_bind_host_is_used_for_host_binary():
    """Positive assertion: the fix path is actually present. If this
    fails, someone removed the fix without also removing the ban
    -- which would leave the DNS probe non-functional."""
    src = _src()
    assert "${pkgs.bind.host}/bin/host" in src, (
        "v40.3 regression: ${pkgs.bind.host}/bin/host is missing from "
        "iso/appliance-disk-image.nix. The FIX-11 DNS gate, the Phase 2 "
        "telemetry, and the beacon-refresh dns_test all require it. "
        "If you intentionally refactored to a different DNS tool "
        "(dig, drill, getent), update this test accordingly."
    )


def test_msp_auto_provision_has_bin_preamble():
    """The sanity preamble (`_bg_bin_check`) must run before any
    external command in msp-auto-provision. Without it, a future
    nixpkgs rotation that eats another referenced binary will brick
    the fleet silently just like this incident did."""
    src = _src()
    start = src.find("systemd.services.msp-auto-provision")
    assert start > 0, "msp-auto-provision.service declaration missing"
    block = src[start : start + 20000]
    assert "_bg_bin_check" in block, (
        "v40.3 regression: msp-auto-provision lost its _bg_bin_check "
        "preamble. That preamble is the only place we fail loud on a "
        "missing referenced binary -- without it the next nixpkgs "
        "path rotation will silently 127 the fleet again."
    )
    # The preamble should list at least the binaries we've already
    # been bitten by or are load-bearing.
    for required in (
        "${pkgs.bind.host}/bin/host",
        "${pkgs.curl}/bin/curl",
        "${pkgs.jq}/bin/jq",
        "${pkgs.yq}/bin/yq",
    ):
        assert required in block, (
            f"v40.3 regression: _bg_bin_check preamble no longer checks "
            f"{required}. Add it back -- this is the canary for that "
            f"binary existing on disk in the installed-system closure."
        )


def test_no_non_ascii_in_python_bytes_literals():
    """Python 3 requires `b'...'` bytes literals to be ASCII-only.
    v40.0 shipped msp-status-beacon.py with a U+2014 em-dash inside
    a b'...' literal, causing a SyntaxError at import time; the
    beacon service restart-looped ~11x/min until we found it. Any
    future heredoc Python with a non-ASCII rune inside `b'...'`
    re-enters the same failure mode.
    """
    src = _src()
    # Regex: find every `b'...'` or `b"..."` string and inspect contents
    # for any non-ASCII codepoint. We allow \x.. \u.. \N{..} escape
    # sequences because those are ASCII-source even when they represent
    # non-ASCII runes at runtime.
    pattern = re.compile(r"""b(['"])((?:\\.|(?!\1).)*)\1""")
    violations = []
    for match in pattern.finditer(src):
        body = match.group(2)
        for ch in body:
            if ord(ch) > 0x7F:
                line_num = src[: match.start()].count("\n") + 1
                violations.append(
                    f"line {line_num}: non-ASCII U+{ord(ch):04X} "
                    f"({ch!r}) inside bytes literal {match.group(0)[:80]!r}"
                )
                break
    assert not violations, (
        "v40.3 regression: non-ASCII characters inside Python b'...' "
        "bytes literals. Python 3 rejects these with SyntaxError at "
        "import time, and the systemd unit restart-loops silently.\n"
        + "\n".join(violations)
    )

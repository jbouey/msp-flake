"""v40.6 (2026-04-24) — on-disk config.yaml must be flat.

Context (Principal SWE round-table on code consistency, 2026-04-24):
`/api/provision/{mac}` returns a payload shaped

    { "config": {site_id, api_key, api_endpoint, ssh_authorized_keys},
      "signature": "...",
      // legacy backwards-compat duplicates at top level:
      "site_id": ..., "api_key": ..., "api_endpoint": ..., "ssh_authorized_keys": [...] }

Prior to v40.6 the msp-auto-provision shell script wrote the entire
response verbatim with `yq -y '.' response.json > config.yaml`. That
produced an on-disk file with BOTH a nested `config:` block AND the
top-level duplicates — two different readers (shell grep, Go yaml
unmarshal, operator eyeballing) could extract different api_key
values from the same file without either being wrong.

On 2026-04-24 this cost 45 minutes of rescue debugging: my `grep -m1
api_key:` picked the nested value, my rescue INSERT targeted that
hash, the daemon (reading top-level via the flat Config struct in
Go) kept sending the top-level value, and 401s continued until the
second INSERT matched the top-level.

Root-cause fix: shell writes ONLY the signed `.config` block. The
signature-verify path already reads only `.config`, so the on-disk
file stops carrying legacy duplicates the moment we filter. One
source, one reader, one location. This test enforces the property.

The secondary cleanup (backend stops emitting top-level duplicates
in the response) is tracked separately in Task #129.
"""
from __future__ import annotations

import pathlib
import re

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent.parent
_DISK_IMAGE_NIX = _REPO_ROOT / "iso" / "appliance-disk-image.nix"


def _msp_auto_provision_block() -> str:
    src = _DISK_IMAGE_NIX.read_text()
    start = src.find("systemd.services.msp-auto-provision")
    assert start > 0, "msp-auto-provision declaration missing"
    return src[start : start + 30000]


def test_config_yaml_write_filters_to_config_block_only():
    """Every `yq -y ... > CONFIG_PATH` write in msp-auto-provision must
    filter to the `.config` block. Writing the whole response (`yq -y
    '.'`) produces a file with a nested `config:` envelope PLUS the
    top-level duplicates — two values per field, two readers, drift.
    """
    block = _msp_auto_provision_block()
    # Find every yq invocation that writes to CONFIG_PATH.
    writes = re.findall(
        r"\$\{pkgs\.yq\}/bin/yq\s+-y\s+'([^']+)'\s+/tmp/provision-response\.json\s*>\s*\"\$CONFIG_PATH\"",
        block,
    )
    assert writes, (
        "v40.6 regression: no `yq -y ... > CONFIG_PATH` writes found in "
        "msp-auto-provision. If the write sites moved, update this test; "
        "do NOT leave this class undefended."
    )
    # The plan specifies three sites (phase 1 retry, phase 2 drop-ship
    # poll, phase 3 persistent retry). If the count changes, the test
    # still holds — just update the assertion message.
    assert len(writes) >= 3, (
        f"v40.6: expected at least 3 yq-write sites (phase 1/2/3 of "
        f"msp-auto-provision); found {len(writes)}. Script may have "
        f"been simplified — verify each success path writes `.config` "
        f"and update this test accordingly."
    )
    for filt in writes:
        assert filt.strip() == ".config", (
            f"v40.6 regression: yq filter is `{filt}` — must be exactly "
            f"`.config` so the on-disk config.yaml contains only the "
            f"signed payload (no nested `config:` envelope, no legacy "
            f"top-level duplicates). Writing the whole response produces "
            f"a file where Go yaml and shell grep disagree on which "
            f"api_key is canonical. See 2026-04-24 rescue debugging "
            f"cost: 45 min wasted on that exact ambiguity."
        )


def test_no_full_response_yaml_dump_remains():
    """Hard ban on `yq -y '.'` (the whole-response write) within the
    msp-auto-provision block. Pattern-level defense: even if a future
    refactor adds a fourth write site, the lint catches it.
    """
    block = _msp_auto_provision_block()
    # Look for the banned literal `-y '.'` with `.json >` on the same
    # line. We allow `yq -y '.config'`, `yq -y '.signature'`, etc.
    bad = re.search(
        r"yq\s+-y\s+'\.'\s+/tmp/provision-response\.json\s*>",
        block,
    )
    assert bad is None, (
        "v40.6 regression: a `yq -y '.'` full-response write reappeared "
        "in msp-auto-provision. That produces a split-brain config.yaml "
        "(nested `config:` + top-level duplicates). Filter to `.config` "
        "and keep this test green."
    )


def test_signature_verifier_still_reads_config_block():
    """Belt-and-braces: `verify_provision_signature` must continue to
    verify against the `.config` subtree. If someone refactors the
    signer to cover the whole response, the daemon-side write (now
    `.config`) would be narrower than the signed scope — trust model
    breaks. This test pins the contract."""
    block = _msp_auto_provision_block()
    # Find the python heredoc inside verify_provision_signature that
    # loads .config from the response.
    assert "json.load(open('$response_file'))['config']" in block, (
        "v40.6 regression: signature verifier no longer loads "
        "`response['config']` as the signed payload. The v40.6 change "
        "makes the on-disk config.yaml == response['config'] exactly, "
        "so the signature MUST be over that same subtree. If the "
        "signer scope widens (e.g. signs the whole response), the "
        "daemon would be writing a narrower payload than was signed "
        "— re-open this invariant before landing that change."
    )

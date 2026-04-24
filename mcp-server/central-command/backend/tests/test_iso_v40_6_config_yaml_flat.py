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


def test_no_writes_to_deprecated_appliance_provisioning_api_key():
    """v40.6 Split #1: `appliance_provisioning.api_key` is deprecated
    (being dropped in a follow-up migration). Every writer has been
    migrated — the MAC lookup endpoint mints fresh on every call +
    writes to api_keys, and the admin-provision handler no longer
    populates the column. This test locks that in so a regression
    doesn't re-introduce the split-brain before the column is dropped.

    Allowed: reads (none exist anymore); the SELECT line in
    provisioning.py that deliberately OMITs `ap.api_key` (comment-only
    reference); past migration files (frozen history).
    """
    backend_root = _REPO_ROOT / "mcp-server" / "central-command" / "backend"
    # Ban patterns — source code writes to the column via SQL.
    banned = [
        # INSERT that includes api_key as a column in the column list.
        # Column list is parenthesized + on the same line or nearby.
        re.compile(
            r"INSERT\s+INTO\s+appliance_provisioning\s*\([^)]{0,400}\bapi_key\b",
            re.IGNORECASE | re.DOTALL,
        ),
        # UPDATE ... SET ... api_key = — bounded distance so we don't
        # span across unrelated statements in the same string literal.
        re.compile(
            r"UPDATE\s+appliance_provisioning\b[\s\S]{0,80}?\bSET\b[\s\S]{0,200}?\bapi_key\s*=",
            re.IGNORECASE,
        ),
    ]
    # Walk every .py file in backend/, excluding tests and migrations.
    violations: list[str] = []
    for py in backend_root.rglob("*.py"):
        if "/tests/" in str(py) or "/migrations/" in str(py):
            continue
        text = py.read_text()
        for pat in banned:
            for m in pat.finditer(text):
                line_num = text[: m.start()].count("\n") + 1
                violations.append(
                    f"{py.relative_to(backend_root)}:{line_num}: {m.group(0)[:90]!r}"
                )
    assert not violations, (
        "v40.6 Split #1 regression: a writer to "
        "`appliance_provisioning.api_key` was added or re-added. That "
        "column is deprecated — the sole source of truth for auth is "
        "`api_keys WHERE active=true`. If you genuinely need to store "
        "a raw key long-term, re-open the round-table first; otherwise "
        "mint on every provision call.\n"
        + "\n".join(violations)
    )


def test_migration_241_drops_api_key_column():
    """v40.6 Split #1 stage 2: migration 241 DROPs the deprecated
    `appliance_provisioning.api_key` column. If this test fails the
    deprecation pipeline is broken — either the migration was
    renamed/moved, or the DROP got removed, or someone added it back
    as a new column."""
    mig = (
        _REPO_ROOT / "mcp-server" / "central-command" / "backend"
        / "migrations" / "241_drop_appliance_provisioning_api_key.sql"
    )
    assert mig.exists(), (
        "v40.6 regression: migration 241 (DROP appliance_provisioning.api_key) "
        "is missing. Stage-2 of the split-#1 deprecation was reverted "
        "or renamed without updating this test."
    )
    src = mig.read_text()
    assert "ALTER TABLE appliance_provisioning" in src and "DROP COLUMN" in src, (
        "v40.6 regression: migration 241 no longer contains the "
        "`ALTER TABLE appliance_provisioning DROP COLUMN` statement. "
        "Restore it; the stage-2 drop is the whole point of this file."
    )
    assert "api_key" in src, (
        "v40.6 regression: migration 241 does not reference `api_key`. "
        "That's the column this migration exists to drop."
    )
    # Idempotency — replay safety. `IF EXISTS` keeps the migration
    # harmless if it's applied to a DB that already dropped the column.
    assert "IF EXISTS" in src, (
        "v40.6 regression: migration 241 lost its `IF EXISTS` guard. "
        "Replay (re-apply) must be a no-op, not a failure."
    )


def test_adr_source_of_truth_hygiene_exists():
    """The round-table ADR documenting the three-splits principle
    must exist + reference each of the three closed splits.
    Without this doc, future engineers have no 'why' context for
    the VIEW / accessor / test discipline."""
    adr = _REPO_ROOT / "docs" / "adr" / "2026-04-24-source-of-truth-hygiene.md"
    assert adr.exists(), (
        "v40.6 regression: the source-of-truth hygiene ADR was deleted. "
        "It's the decision record that justifies the ban regexes in "
        "this test file and the future design reviews. Restore it."
    )
    src = adr.read_text()
    for marker in (
        "appliance_provisioning.api_key",    # split 1 named
        "config.yaml",                        # split 2 named
        "CredentialProvider",                 # split 3 named
        "One writer",                         # core principle
    ):
        assert marker in src, (
            f"v40.6 regression: ADR lost reference to `{marker}`. "
            f"If the ADR was restructured, keep the splits and "
            f"principle statement visible — future engineers need "
            f"the 'why'."
        )


def test_flake_exposes_appliance_boot_check():
    """The QEMU boot-integration test must exist in flake.nix under
    `checks.x86_64-linux.appliance-boot`. Source-level grep only;
    running `nix flake check` is out of scope for pytest. This
    test's job is to ensure the harness stays wired — not to run it."""
    flake = _REPO_ROOT / "flake.nix"
    src = flake.read_text()
    assert "checks.x86_64-linux" in src, (
        "v40.6 regression: flake.nix no longer exposes "
        "`checks.x86_64-linux`. The QEMU boot-integration test "
        "harness is gone. Without it, runtime regressions (missing "
        "binary inside a derivation, Python SyntaxError in heredoc, "
        "systemd deadlock) cannot be caught pre-ship."
    )
    assert "appliance-boot" in src, (
        "v40.6 regression: the `appliance-boot` check is missing from "
        "flake.nix. Restore it — the SWE round-table explicitly scoped "
        "this as non-negotiable infrastructure."
    )
    assert "pkgs.testers.runNixOSTest" in src, (
        "v40.6 regression: flake.nix no longer uses "
        "`pkgs.testers.runNixOSTest`. That's the canonical NixOS VM "
        "test framework; replacing it requires re-opening the round-"
        "table (the alternative tooling needs to prove it can catch "
        "systemd ordering + heredoc syntax + binary presence)."
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

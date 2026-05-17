"""CI sentinel gates for Vault P0 iter-4 Commit 2 (2026-05-16).

Pins the 4 P0 closure-bindings (P0-B through P0-E) so a future PR
can't silently regress the iter-1/2/3 root-cause classes again. See
`audit/coach-vault-p0-bundle-iter4-gate-a-2026-05-16.md` for the
full verdict + rationale.

P0-A (fixture/prod parity) is closed by commit 80cbd72c (clean-slate
DROP + fixture regen) and pinned by the existing
`test_substrate_invariant_sql_columns_valid.py` + the schema-fixture
parity gate. This file covers the remaining 4.
"""
from __future__ import annotations

import inspect
import pathlib
import re


_BACKEND = pathlib.Path(__file__).resolve().parent.parent


def test_p0_b_inv_uses_asyncio_to_thread_and_wait_for():
    """P0-B: the INV must wrap the sync Vault probe in
    asyncio.to_thread AND outer asyncio.wait_for. Without to_thread,
    wait_for is a no-op on the actual hang surface (sync httpx
    calls). Without wait_for, hangs aren't bounded."""
    src = (_BACKEND / "startup_invariants.py").read_text()
    # Outer wait_for around the INV invocation.
    assert "asyncio.wait_for(" in src and "_check_signing_backend_vault" in src, (
        "startup_invariants must call `await asyncio.wait_for("
        "_check_signing_backend_vault(...), timeout=...)`. "
        "Without it the iter-3 root cause re-emerges (INV blocked "
        "startup past 120s on /health)."
    )
    # The probe body itself must use asyncio.to_thread.
    probe_match = re.search(
        r"async def _check_signing_backend_vault\(.*?\n(.*?)(?=\nasync def |\Z)",
        src, re.DOTALL,
    )
    assert probe_match, "could not locate _check_signing_backend_vault"
    body = probe_match.group(1)
    assert "asyncio.to_thread(" in body, (
        "_check_signing_backend_vault must wrap sync calls in "
        "`asyncio.to_thread(...)`. Pure sync calls can't be "
        "cancelled by the outer wait_for; the thread wrap is what "
        "makes cancellation possible."
    )


def test_p0_c_inner_timeout_below_outer():
    """P0-C: the httpx per-request timeout (inner) must be STRICTLY
    LESS THAN the asyncio.wait_for (outer) so the socket times out
    BEFORE the asyncio cancellation fires — clean thread exit, no
    thread leak.

    Per the iter-4 design: outer=5.0s, inner=4.0s, buffer=1.0s.
    """
    src = (_BACKEND / "startup_invariants.py").read_text()
    outer_m = re.search(r"VAULT_PROBE_OUTER_TIMEOUT_S\s*=\s*([\d.]+)", src)
    inner_m = re.search(r"VAULT_PROBE_INNER_TIMEOUT_S\s*=\s*([\d.]+)", src)
    assert outer_m, "VAULT_PROBE_OUTER_TIMEOUT_S constant missing"
    assert inner_m, "VAULT_PROBE_INNER_TIMEOUT_S constant missing"
    outer = float(outer_m.group(1))
    inner = float(inner_m.group(1))
    assert inner < outer, (
        f"VAULT_PROBE_INNER_TIMEOUT_S={inner} must be < OUTER={outer}. "
        f"Equal timeouts race; inner > outer makes the inner pointless. "
        f"Need a positive buffer so the socket-level timeout fires "
        f"BEFORE the asyncio cancellation — clean thread exit."
    )
    # Buffer should be >= 0.5s to give the socket time to actually
    # error vs being cancelled mid-flush.
    assert (outer - inner) >= 0.5, (
        f"OUTER - INNER = {outer - inner}s; need >= 0.5s buffer for "
        f"clean socket exit."
    )


def test_p0_d_current_signing_method_logs_and_counts_fallback():
    """P0-D: the silent `except Exception: return X` swallow in
    signing_backend.current_signing_method was the false-negative
    blind spot for the substrate invariant. Fix: log ERROR with
    structured details + increment _FALLBACK_COUNT.

    Sentinel: the fallback path must reference logger.error AND
    increment the counter. Bare `except Exception: return` is now
    forbidden in this function."""
    src = (_BACKEND / "signing_backend.py").read_text()
    fn_m = re.search(
        r"def current_signing_method\(\).*?(?=\n\ndef |\Z)",
        src, re.DOTALL,
    )
    assert fn_m, "could not locate current_signing_method"
    body = fn_m.group(0)
    assert "logger.error(" in body, (
        "current_signing_method must log ERROR on the fallback path "
        "(P0-D). Bare silent swallow was the false-negative blind "
        "spot for signing_backend_drifted_from_vault."
    )
    assert "_FALLBACK_COUNT" in body, (
        "current_signing_method must increment _FALLBACK_COUNT on the "
        "fallback path so the operator has direct evidence of the "
        "silent-failure rate."
    )
    # Accessor must exist for /metrics + tests.
    assert "def get_signing_backend_fallback_count" in src, (
        "signing_backend must expose get_signing_backend_fallback_count "
        "for the substrate invariant + /metrics readers."
    )


def test_p0_e_mig_311_ledger_row_removed():
    """P0-E: when mig 311 ships on disk, the RESERVED_MIGRATIONS.md
    ledger row for 311 must be REMOVED in the same commit. Per the
    ledger lifecycle rule (CLAUDE.md §RESERVED_MIGRATIONS)."""
    mig_disk = (_BACKEND / "migrations" / "311_vault_signing_key_versions.sql")
    ledger = (_BACKEND / "migrations" / "RESERVED_MIGRATIONS.md").read_text()
    if mig_disk.exists():
        # Ledger row for 311 must be GONE — if both the on-disk mig
        # AND a ledger row coexist, the lifecycle is broken.
        # The "on-disk authority wins" rule says: remove the ledger
        # row when the mig ships.
        assert not re.search(r"^\|\s*311\s*\|", ledger, re.MULTILINE), (
            "migrations/311_*.sql exists on disk AND RESERVED_"
            "MIGRATIONS.md still has a row for 311. Per the ledger "
            "lifecycle: REMOVE the ledger row in the same commit that "
            "ships the mig file. On-disk SQL is post-ship authority."
        )


def test_inv_signing_backend_vault_registered_in_check_all():
    """The new INV must actually be returned by check_all_invariants.
    Without registration, the INV is dead code."""
    src = (_BACKEND / "startup_invariants.py").read_text()
    # check_all_invariants must append a result with name="INV-
    # SIGNING-BACKEND-VAULT" — easiest check is that the string
    # appears in the body.
    cai_m = re.search(
        r"async def check_all_invariants\(.*?return results\b",
        src, re.DOTALL,
    )
    assert cai_m, "could not locate check_all_invariants"
    body = cai_m.group(0)
    assert "INV-SIGNING-BACKEND-VAULT" in body, (
        "check_all_invariants must produce an InvariantResult with "
        "name='INV-SIGNING-BACKEND-VAULT' so the operator-visible "
        "substrate-health panel surfaces the Vault probe result."
    )


def test_vault_signing_key_versions_in_schema_fixture():
    """Lockstep with the new mig 311: the schema fixture must list
    vault_signing_key_versions with the 10-column shape. Catches the
    'mig shipped but fixture not regen'd' silent-drift class."""
    import json
    cols_path = _BACKEND / "tests" / "fixtures" / "schema" / "prod_columns.json"
    types_path = _BACKEND / "tests" / "fixtures" / "schema" / "prod_column_types.json"
    cols = json.loads(cols_path.read_text())
    types = json.loads(types_path.read_text())
    assert "vault_signing_key_versions" in cols, (
        "prod_columns.json must list vault_signing_key_versions "
        "(mig 311 iter-4). Fixture/mig drift class."
    )
    assert "vault_signing_key_versions" in types, (
        "prod_column_types.json must list vault_signing_key_versions."
    )
    # Minimum 10-column shape from mig 311 — all must remain. Newer
    # columns (e.g. attestation_bundle_id from mig 328 / #116) added
    # via additive migrations are explicitly allowed via superset
    # check (not equality). The lockstep concern is "mig 311 shape
    # still present", not "no other columns ever".
    expected_cols = {
        "id", "key_name", "key_version", "pubkey_hex", "pubkey_b64",
        "first_observed_at", "last_observed_at", "known_good",
        "approved_by", "approved_at",
    }
    fixture_cols = set(cols["vault_signing_key_versions"])
    missing = expected_cols - fixture_cols
    assert not missing, (
        f"vault_signing_key_versions fixture is missing mig 311 "
        f"columns: {sorted(missing)}. Fixture: {sorted(fixture_cols)}."
    )

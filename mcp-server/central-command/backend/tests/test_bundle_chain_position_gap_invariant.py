"""CI gates for #117 Sub-commit A: bundle_chain_position_gap
substrate invariant (per audit/coach-117-chain-contention-load-
gate-a-2026-05-16.md Part 1).

Source-shape sentinels that pin the invariant's contract:

  1. Function exists + registered in ALL_ASSERTIONS at sev1
  2. Query is per-site partitioned (not per-(site, check_type))
  3. 24h window (partition pruning on monthly-partitioned table)
  4. LAG window function with NULL exclusion (genesis carve-out)
  5. Runbook doc exists
  6. _DISPLAY_METADATA entry exists

Pinned so a future PR can't silently drop the gate (load harness
Sub-commits B/C/D depend on this gate firing to prove the
per-site advisory lock works under contention).
"""
from __future__ import annotations

import pathlib
import re


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_ASSERTIONS = _BACKEND / "assertions.py"
_RUNBOOK = _BACKEND / "substrate_runbooks" / "bundle_chain_position_gap.md"


def _read_src() -> str:
    return _ASSERTIONS.read_text(encoding="utf-8")


def test_function_exists_and_registered_at_sev1():
    """The invariant function exists + the registration in
    ALL_ASSERTIONS is sev1 (chain-corruption class)."""
    src = _read_src()
    assert "async def _check_bundle_chain_position_gap" in src, (
        "_check_bundle_chain_position_gap function missing from "
        "assertions.py — Task #117 Sub-commit A prerequisite."
    )
    # Find the Assertion(...) registration block
    m = re.search(
        r'Assertion\(\s*name="bundle_chain_position_gap"\s*,\s*'
        r'severity="(\w+)"',
        src,
    )
    assert m, (
        "bundle_chain_position_gap not registered in ALL_ASSERTIONS "
        "with name= + severity= keyword args."
    )
    assert m.group(1) == "sev1", (
        f"bundle_chain_position_gap severity={m.group(1)!r} — must "
        f"be sev1 (chain corruption class, matches sibling "
        f"cross_org_relocate_chain_orphan + load_test_marker_in_"
        f"compliance_bundles)."
    )


def test_query_is_per_site_only_not_per_check_type():
    """Per-site advisory lock locks on `hashtext(site_id)` alone.
    The invariant's PARTITION BY must match — partitioning by
    (site_id, check_type) would mis-attribute gaps across check
    types within one site."""
    src = _read_src()
    # Find the function body
    m = re.search(
        r"async def _check_bundle_chain_position_gap.*?(?=\nasync def |\Z)",
        src, re.DOTALL,
    )
    assert m, "could not locate _check_bundle_chain_position_gap body"
    body = m.group(0)
    # Find the PARTITION BY clause
    pat = re.search(r"PARTITION\s+BY\s+([^\s)]+)", body, re.IGNORECASE)
    assert pat, "no PARTITION BY in _check_bundle_chain_position_gap"
    partition_col = pat.group(1).strip()
    assert partition_col == "site_id", (
        f"PARTITION BY {partition_col!r} — must be `site_id` ALONE. "
        f"Per-site advisory lock partitions on site_id only; per-"
        f"(site, check_type) partition would mis-attribute gaps."
    )


def test_query_uses_24h_window():
    """24h window for partition pruning on monthly-partitioned
    compliance_bundles. Without it, the LAG scan is unbounded."""
    src = _read_src()
    m = re.search(
        r"async def _check_bundle_chain_position_gap.*?(?=\nasync def |\Z)",
        src, re.DOTALL,
    )
    assert m, "could not locate _check_bundle_chain_position_gap body"
    body = m.group(0)
    assert "INTERVAL '24 hours'" in body or 'INTERVAL "24 hours"' in body, (
        "_check_bundle_chain_position_gap query must use a 24h "
        "window for partition pruning on monthly-partitioned "
        "compliance_bundles. Unbounded LAG scan would not survive "
        "the production-scale table."
    )


def test_query_uses_lag_window_function():
    """LAG is the canonical shape for gap detection. NULL-from-LAG
    naturally excludes the genesis bundle (chain_position 0 has no
    predecessor, so LAG returns NULL, and the gap-size predicate
    `chain_position - prev_chain_position` is NULL-propagating)."""
    src = _read_src()
    m = re.search(
        r"async def _check_bundle_chain_position_gap.*?(?=\nasync def |\Z)",
        src, re.DOTALL,
    )
    assert m
    body = m.group(0)
    assert "LAG(chain_position)" in body or "LAG (chain_position)" in body, (
        "Use LAG(chain_position) for gap detection. Sibling "
        "patterns may use ROW_NUMBER + arithmetic, but LAG is the "
        "minimal + most-readable shape for adjacent-row comparison."
    )
    # The genesis carve-out via NULL-exclusion
    assert "prev_chain_position IS NOT NULL" in body or \
           "IS NOT NULL" in body, (
        "Must explicitly filter `prev_chain_position IS NOT NULL` "
        "(or equivalent) to exclude the genesis bundle. NULL-"
        "arithmetic happens to filter naturally but explicit is "
        "load-bearing for the auditor walking the SQL."
    )


def test_query_thresholds_gap_size_greater_than_1():
    """Gap is `chain_position - prev_chain_position > 1`. A diff
    of exactly 1 is the expected sequence; only > 1 is a gap."""
    src = _read_src()
    m = re.search(
        r"async def _check_bundle_chain_position_gap.*?(?=\nasync def |\Z)",
        src, re.DOTALL,
    )
    assert m
    body = m.group(0)
    assert "> 1" in body, (
        "Gap threshold must be `chain_position - prev_chain_"
        "position > 1`. Diff=1 is the expected sequential pattern."
    )


def test_query_has_limit_to_bound_log_spam():
    """LIMIT 100 caps the violation count per tick — widespread
    corruption would otherwise spam the engine."""
    src = _read_src()
    m = re.search(
        r"async def _check_bundle_chain_position_gap.*?(?=\nasync def |\Z)",
        src, re.DOTALL,
    )
    assert m
    body = m.group(0)
    assert "LIMIT 100" in body or "LIMIT  100" in body, (
        "Query must LIMIT 100 to bound the violation count under "
        "widespread corruption (would otherwise spam the engine "
        "+ alert channels)."
    )


def test_runbook_exists():
    """The substrate_docs_present gate will fail if missing, but
    we pin it here too so the relationship is explicit."""
    assert _RUNBOOK.exists(), (
        f"substrate_runbooks/bundle_chain_position_gap.md must "
        f"exist (sibling of cross_org_relocate_chain_orphan.md). "
        f"Looked at: {_RUNBOOK}"
    )
    content = _RUNBOOK.read_text()
    assert "Severity:** sev1" in content, "runbook must declare sev1"
    lower = content.lower()
    assert "chain" in lower and ("corruption" in lower or "integrity" in lower), (
        "runbook must explain the chain corruption/integrity class"
    )


def test_display_metadata_entry_exists():
    """_DISPLAY_METADATA must have an entry for the new invariant.
    Existing test_assertion_metadata_complete catches missing
    entries — pinning here keeps the relationship explicit."""
    src = _read_src()
    assert '"bundle_chain_position_gap": {' in src, (
        "_DISPLAY_METADATA missing entry for bundle_chain_position_gap. "
        "Without it the substrate-health panel shows the raw "
        "invariant name + empty action."
    )


def test_no_table_join_needed():
    """The query reads compliance_bundles alone (no JOIN to sites
    or other tables) — chain_position is on compliance_bundles
    directly. Verify no accidental JOIN that would change shape."""
    src = _read_src()
    m = re.search(
        r"async def _check_bundle_chain_position_gap.*?(?=\nasync def |\Z)",
        src, re.DOTALL,
    )
    assert m
    body = m.group(0)
    assert "JOIN" not in body.upper().replace("LAG", "").replace(" OR ", ""), (
        "_check_bundle_chain_position_gap should query "
        "compliance_bundles directly with no JOIN. Adding a JOIN "
        "(e.g., to sites) would slow the per-tick scan + risk "
        "false-positives from soft-deleted rows."
    )

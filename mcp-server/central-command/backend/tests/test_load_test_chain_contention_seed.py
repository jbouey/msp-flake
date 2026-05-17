"""CI gates for #117 Sub-commit B (mig 325) — load-test chain-
contention site seed + companion sev2 invariant.

Source-shape sentinels pin Gate A bindings:
  - mig 325 seeds exactly 1 site row, 20 site_appliances, 20 api_keys
  - site_id literal 'load-test-chain-contention-site' used consistently
  - sites.load_test_chain_contention column added (NOT sites.synthetic)
  - sites.synthetic stays FALSE for this site (else CHECK or invariant
    would fire incorrectly)
  - client_org_id is NULL (operationally orphaned per Gate A)
  - status='inactive' (NOT 'paused' per Sub-A Gate B P2-1 correction)
  - load_test_marker_in_compliance_bundles invariant has carve-out
    literal AND cb.site_id != 'load-test-chain-contention-site'
  - load_test_chain_contention_site_orphan invariant registered at sev2
  - api_keys seeded with deterministic key_hash = sha256(plaintext)
    where plaintext = 'load-test-bearer-NN' (for k6 reproducibility)
"""
from __future__ import annotations

import pathlib
import re


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_MIG_325 = _BACKEND / "migrations" / "325_load_test_chain_contention_site.sql"
_ASSERTIONS = _BACKEND / "assertions.py"
_RUNBOOK = _BACKEND / "substrate_runbooks" / "load_test_chain_contention_site_orphan.md"


def _read_mig() -> str:
    return _MIG_325.read_text(encoding="utf-8")


def _strip_sql_comments(text: str) -> str:
    """Strip -- single-line comments and /* */ block comments for
    SQL-shape assertions that should NOT trip on prose."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"--[^\n]*", "", text)
    return text


def _read_assertions() -> str:
    return _ASSERTIONS.read_text(encoding="utf-8")


# ── Mig 325 structural ────────────────────────────────────────────


def test_mig_325_exists():
    assert _MIG_325.exists(), (
        f"mig 325 file missing: {_MIG_325}. #117 Sub-commit B "
        f"requires this seed migration to exist."
    )


def test_mig_325_adds_load_test_chain_contention_column():
    """Gate A binding: NEW column on sites, NOT re-use of sites.synthetic
    (chain-contention test needs real bundles which CHECK constraint
    no_synthetic_bundles would reject if synthetic=TRUE)."""
    body = _read_mig()
    assert re.search(
        r"ALTER\s+TABLE\s+sites\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+"
        r"load_test_chain_contention\s+BOOLEAN",
        body, re.IGNORECASE,
    ), (
        "mig 325 must add sites.load_test_chain_contention BOOLEAN. "
        "Per Gate A Option C: NEW flag, NOT sites.synthetic, so the "
        "no_synthetic_bundles CHECK constraint passes."
    )


def test_mig_325_seeds_synthetic_false_load_test_true():
    """Defends against future regression where someone flips synthetic
    to TRUE on the seed site (would trigger no_synthetic_bundles CHECK
    + the load_test_marker_in_compliance_bundles invariant)."""
    body = _strip_sql_comments(_read_mig())
    # The INSERT INTO sites must explicitly set synthetic=FALSE and
    # load_test_chain_contention=TRUE in the VALUES. Naive `[^)]+`
    # stops at the first paren inside a string literal — split the
    # statement at ON CONFLICT instead.
    stmt_match = re.search(
        r"INSERT\s+INTO\s+sites\s*\((?P<cols>.*?)\)\s*VALUES\s*"
        r"\((?P<vals>.*?)\)\s*\n\s*ON\s+CONFLICT",
        body, re.IGNORECASE | re.DOTALL,
    )
    assert stmt_match, "mig 325 must INSERT INTO sites for the seed row"
    cols = stmt_match.group("cols")
    vals = stmt_match.group("vals")
    assert "synthetic" in cols and "load_test_chain_contention" in cols, (
        "INSERT INTO sites must include both synthetic + "
        "load_test_chain_contention columns explicitly (not rely on "
        "defaults — future schema changes could shift defaults)."
    )
    # Position-match (comments stripped): synthetic → FALSE,
    # load_test_chain_contention → TRUE. Adjacent in VALUES list.
    assert re.search(r"\bFALSE\b\s*,\s*\bTRUE\b", vals, re.IGNORECASE), (
        "VALUES must set synthetic=FALSE + load_test_chain_contention=TRUE "
        "in lockstep order with cols. Per Gate A: chain-contention site "
        "uses load_test_chain_contention as the flag, NOT synthetic."
    )


def test_mig_325_uses_inactive_status_not_paused():
    """Sub-A Gate B P2-1 fix: sites_status_check accepts pending|
    online|offline|inactive — NEVER 'paused'. The runbook for #117
    Sub-A made this error; CI gate prevents the SAME error here.
    Strips comments so prose references to 'paused' (e.g. "NEVER
    'paused'") don't false-trip."""
    body = _strip_sql_comments(_read_mig())
    assert "'paused'" not in body, (
        "mig 325 must NOT use status='paused' (would fail sites_status_"
        "check CHECK constraint). Use 'inactive'."
    )
    assert "'inactive'" in body, (
        "mig 325 must set status='inactive' on the seed site (operationally "
        "non-production)."
    )


def test_mig_325_client_org_id_is_null():
    """Counsel Rule 4: synthetic infrastructure must NOT pretend to
    be customer-owned data. client_org_id NULL on the seed row."""
    body = _read_mig()
    assert re.search(
        r"client_org_id[,\s]*\n[\s\S]{0,200}\bNULL\b",
        body,
    ) or re.search(
        r"NULL,\s*--\s*NOT a customer org",
        body,
    ), (
        "mig 325 must seed client_org_id=NULL on the load-test site "
        "(Counsel Rule 4: synthetic infra must not pretend to be "
        "customer-owned)."
    )


def test_mig_325_seeds_20_appliances_via_generate_series():
    body = _read_mig()
    assert "generate_series(0, 19)" in body, (
        "mig 325 must seed 20 site_appliances via generate_series(0, 19). "
        "Per Gate A: deterministic 0..19 indexing for k6 reproducibility."
    )


def test_mig_325_uses_correct_site_id_literal():
    body = _read_mig()
    # The literal must appear multiple times (sites + site_appliances +
    # api_keys at minimum). 3+ occurrences proves it's the consistent
    # constant, not a typo'd one-off.
    count = body.count("'load-test-chain-contention-site'")
    assert count >= 4, (
        f"mig 325 must use 'load-test-chain-contention-site' literal "
        f"≥4 times (INSERT sites, site_appliances FROM, api_keys "
        f"SELECT, admin_audit_log target); found {count}. Inconsistent "
        f"literals = silent failure mode at runtime."
    )


def test_mig_325_uses_idempotent_on_conflict():
    """Re-applying the migration must be safe."""
    body = _read_mig()
    assert body.count("ON CONFLICT") >= 3, (
        "mig 325 must use ON CONFLICT … DO NOTHING/UPDATE on all 3+ "
        "INSERTs (sites, site_appliances, api_keys) for idempotency."
    )


# ── Carve-out in existing invariant ───────────────────────────────


def test_load_test_marker_invariant_has_carve_out_literal():
    """Per Gate A P0-2d: load_test_marker_in_compliance_bundles SQL
    must include a defensive carve-out for the chain-contention site
    in case a future migration accidentally flips synthetic=TRUE here."""
    src = _read_assertions()
    # Find the function body
    m = re.search(
        r"async def _check_load_test_marker_in_compliance_bundles.*?"
        r"(?=\nasync def |\Z)",
        src, re.DOTALL,
    )
    assert m, "load_test_marker_in_compliance_bundles function not found"
    body = m.group(0)
    assert (
        "cb.site_id != 'load-test-chain-contention-site'" in body
    ), (
        "load_test_marker_in_compliance_bundles SQL must include "
        "carve-out: AND cb.site_id != 'load-test-chain-contention-site'. "
        "Per Gate A P0-2d defensive layer."
    )


# ── New companion invariant ───────────────────────────────────────


def test_new_invariant_registered_at_sev2():
    src = _read_assertions()
    m = re.search(
        r'Assertion\(\s*name="load_test_chain_contention_site_orphan"\s*,\s*'
        r'severity="(\w+)"',
        src,
    )
    assert m, (
        "load_test_chain_contention_site_orphan not registered in "
        "ALL_ASSERTIONS"
    )
    assert m.group(1) == "sev2", (
        f"severity={m.group(1)!r}; must be sev2 per Gate A (parity "
        f"with synthetic_traffic_marker_orphan sev2; sev3 falls below "
        f"operator-attention threshold for synthetic infra orphans)."
    )


def test_new_invariant_function_exists():
    src = _read_assertions()
    assert (
        "async def _check_load_test_chain_contention_site_orphan"
        in src
    )


def test_new_invariant_uses_7_day_window():
    src = _read_assertions()
    m = re.search(
        r"async def _check_load_test_chain_contention_site_orphan.*?"
        r"(?=\nasync def |\Z)",
        src, re.DOTALL,
    )
    assert m
    body = m.group(0)
    assert "INTERVAL '7 days'" in body, (
        "Invariant must bound the scan to last 7d (synthetic infra "
        "should rarely accumulate bundles; 7d is generous)."
    )


def test_new_invariant_4h_max_soak_buffer():
    """Per Gate A: 4h max soak per #117 design. COALESCE buffer."""
    src = _read_assertions()
    m = re.search(
        r"async def _check_load_test_chain_contention_site_orphan.*?"
        r"(?=\nasync def |\Z)",
        src, re.DOTALL,
    )
    assert m
    body = m.group(0)
    assert "INTERVAL '4 hours'" in body, (
        "Invariant must use INTERVAL '4 hours' COALESCE buffer (max "
        "soak duration per #117 design — clocks the load_test_runs "
        "row's effective end-time when completed_at is NULL)."
    )


def test_new_invariant_uses_not_exists_correlation():
    """NOT EXISTS on load_test_runs correlation, NOT a LEFT JOIN
    (the NOT-EXISTS form is the canonical orphan-detection pattern
    in this codebase + faster against indexed load_test_runs)."""
    src = _read_assertions()
    m = re.search(
        r"async def _check_load_test_chain_contention_site_orphan.*?"
        r"(?=\nasync def |\Z)",
        src, re.DOTALL,
    )
    assert m
    body = m.group(0)
    assert "NOT EXISTS" in body, (
        "Invariant must use NOT EXISTS correlation on load_test_runs "
        "(canonical orphan-detection shape; LEFT JOIN ... IS NULL is "
        "valid but inconsistent with sibling invariants)."
    )


# ── Runbook ───────────────────────────────────────────────────────


def test_new_invariant_runbook_exists():
    assert _RUNBOOK.exists(), (
        f"substrate_runbooks/load_test_chain_contention_site_orphan."
        f"md missing: {_RUNBOOK}"
    )
    content = _RUNBOOK.read_text()
    assert "Severity:** sev2" in content
    assert "load-test-chain-contention-site" in content
    assert "load_test_runs" in content
    assert "load_test_chain_contention" in content


def test_new_invariant_runbook_no_paused_status():
    """Sub-A Gate B P2-1 class — runbook must NOT reference invalid
    'paused' status."""
    content = _RUNBOOK.read_text()
    assert "status='paused'" not in content
    assert "status = 'paused'" not in content


def test_new_invariant_display_metadata_entry():
    src = _read_assertions()
    assert (
        '"load_test_chain_contention_site_orphan": {' in src
    ), (
        "_DISPLAY_METADATA missing entry for "
        "load_test_chain_contention_site_orphan"
    )


# ── Audit-log row for provenance ──────────────────────────────────


def test_mig_325_writes_audit_log_provenance_row():
    """Belt-and-suspenders: mig 325 should write an admin_audit_log
    row for SQL-grep discoverability."""
    body = _read_mig()
    assert "LOAD_TEST_SEED_APPLIED" in body, (
        "mig 325 should INSERT a LOAD_TEST_SEED_APPLIED audit row for "
        "provenance (operator can grep admin_audit_log to confirm seed)."
    )


def test_mig_325_audit_action_is_not_privileged():
    """The audit-log row must NOT use a PRIVILEGED_ACCESS_ prefix
    (would trigger mig 175 enforce_privileged_order_attestation +
    privileged-chain integrity guards inappropriately — seeds are
    not privileged operations). Strips comments so prose references
    don't false-trip."""
    body = _strip_sql_comments(_read_mig())
    assert "'PRIVILEGED_ACCESS_" not in body

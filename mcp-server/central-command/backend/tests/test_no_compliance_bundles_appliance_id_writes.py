"""AST CI gate — no backend code writes to compliance_bundles.appliance_id.

#122 Phase 1 P0-2 closure (audit/coach-122-compliance-bundles-
appliance-id-deprecation-gate-a-2026-05-16.md).

The column was deprecated by mig 268. All 4 production writers
(evidence_chain.py, runbook_consent.py, appliance_relocation.py,
privileged_access_attestation.py) verified to omit it as of
2026-05-16. Ratchet baseline = 0.

Scans every backend .py file for `INSERT INTO compliance_bundles`
or `UPDATE compliance_bundles SET` SQL — fails if `appliance_id`
appears in the column list.

Phase 2/3 of #122 will DROP the column. ANY new writer would
extend the quiet-soak window and push the DROP date — this gate
catches such regressions at PR-build time.

Pairs with the runtime substrate invariant
`compliance_bundles_appliance_id_write_regression` (sev2) which
catches regressions that slip past this gate (e.g., raw SQL run
from a debugging session).
"""
from __future__ import annotations

import pathlib
import re


_BACKEND = pathlib.Path(__file__).resolve().parent.parent

# Files explicitly allowed to reference compliance_bundles + appliance_id
# in the SAME query (e.g., the substrate invariant function reads the
# column to detect violations). Allowlist is INCLUSIVE — must be in
# this set to bypass the scan.
_ALLOWED_FILES = {
    # The substrate invariant function reads the column (NOT writes).
    "assertions.py",
    # The deprecation runbook itself documents the SQL.
    # (Runbooks live in substrate_runbooks/, not scanned by this gate.)
    # Tests are scanned but only for the writer pattern — see _is_write_query.
}

# Sentinel: SQL `INSERT INTO compliance_bundles (...) ...`
_INSERT_PATTERN = re.compile(
    r"INSERT\s+INTO\s+compliance_bundles\s*\(([^)]*)\)",
    re.IGNORECASE | re.DOTALL,
)

# Sentinel: SQL `UPDATE compliance_bundles SET col1=..., col2=...`
_UPDATE_PATTERN = re.compile(
    r"UPDATE\s+compliance_bundles\s+SET\s+([^;]+?)(?:WHERE|RETURNING|$)",
    re.IGNORECASE | re.DOTALL,
)

# Per Gate A: column is being removed Phase 3. Any writer that includes
# 'appliance_id' in the column list is a regression.
_BANNED_TOKEN = "appliance_id"


def _scan_py_files() -> list[pathlib.Path]:
    """All backend .py files (excluding venvs, __pycache__, migrations)."""
    files = []
    for p in _BACKEND.rglob("*.py"):
        s = str(p)
        if "/venv/" in s or "/__pycache__/" in s or "/migrations/" in s:
            continue
        files.append(p)
    return files


def _find_violations(text: str) -> list[tuple[str, str]]:
    """Return list of (operation, column_list) tuples that include
    the banned token."""
    violations: list[tuple[str, str]] = []
    for m in _INSERT_PATTERN.finditer(text):
        cols = m.group(1)
        if re.search(rf"\b{_BANNED_TOKEN}\b", cols):
            violations.append(("INSERT", cols.strip()))
    for m in _UPDATE_PATTERN.finditer(text):
        set_clause = m.group(1)
        # In SET clause, look for `appliance_id =` or `appliance_id=`.
        if re.search(rf"\b{_BANNED_TOKEN}\s*=", set_clause):
            violations.append(("UPDATE", set_clause.strip()[:200]))
    return violations


def test_no_writer_includes_appliance_id_in_compliance_bundles_inserts():
    """Ratchet baseline = 0. Per #122 Gate A Phase 1: NO writer may
    include compliance_bundles.appliance_id in INSERT/UPDATE column
    lists. The column is being removed in Phase 3."""
    offenders: list[tuple[str, str, str]] = []
    for path in _scan_py_files():
        if path.name in _ALLOWED_FILES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for op, col_list in _find_violations(text):
            rel = path.relative_to(_BACKEND)
            offenders.append((str(rel), op, col_list))
    assert not offenders, (
        f"Found {len(offenders)} writer(s) of "
        f"compliance_bundles.appliance_id — column is DEPRECATED "
        f"since mig 268 + slated for DROP in #122 Phase 3. Remove "
        f"the column from the INSERT/UPDATE column list. "
        f"Per-appliance binding lives in evidence_chain.matched_"
        f"appliance_id → site_appliances JOIN on agent_public_key "
        f"fingerprint (Session 196 rule).\n\n"
        f"Offenders:\n"
        + "\n".join(
            f"  {f}: {op} ... {cols[:160]}"
            for f, op, cols in offenders
        )
    )


def test_allowlist_is_explicit_and_minimal():
    """Allowlist drift surface — must stay small. Each entry should
    have a comment explaining why it's exempt."""
    assert len(_ALLOWED_FILES) <= 3, (
        "Allowlist for compliance_bundles.appliance_id writer scan "
        "must stay minimal (≤3 files). Each entry exempts a file "
        "from the gate — additions require fresh Gate A justification."
    )
    # assertions.py reads (NOT writes) for the substrate invariant
    assert "assertions.py" in _ALLOWED_FILES


def test_runbook_exists_for_companion_invariant():
    """Per CLAUDE.md (Session 220 #122 P2-2 binding + test_
    substrate_docs_present): every new substrate invariant ships
    with its runbook."""
    runbook = (
        _BACKEND / "substrate_runbooks"
        / "compliance_bundles_appliance_id_write_regression.md"
    )
    assert runbook.exists(), (
        f"runbook missing: {runbook}. Required for the new sev2 "
        f"substrate invariant compliance_bundles_appliance_id_"
        f"write_regression."
    )
    content = runbook.read_text(encoding="utf-8")
    assert "Severity:** sev2" in content
    # Case-insensitive — runbook uses "deprecated" + "Deprecated"
    assert "deprecated" in content.lower()
    assert "mig 268" in content
    assert "Phase 3" in content


def test_mig_326_rewrites_v_control_status():
    """Per #122 Phase 1 P0-1: mig 326 rewrites v_control_status to
    bind via site_appliances JOIN + use check_result."""
    mig = (
        _BACKEND / "migrations"
        / "326_rewrite_v_control_status.sql"
    )
    assert mig.exists(), f"mig 326 missing: {mig}"
    body = mig.read_text(encoding="utf-8")
    assert "CREATE OR REPLACE VIEW v_control_status" in body
    assert "site_appliances sa" in body, (
        "mig 326 must JOIN site_appliances for per-appliance binding"
    )
    assert "cb.check_result" in body, (
        "mig 326 must use cb.check_result (the canonical column "
        "per mig 268), NOT cb.outcome"
    )
    assert "sa.deleted_at IS NULL" in body, (
        "mig 326 must filter sa.deleted_at IS NULL on the JOIN line "
        "(Session 218 RT33 P1 rule)"
    )


def test_mig_327_drops_dead_index_single_statement():
    """Per #122 Phase 1 P1-3: dead-index DROP is a SEPARATE
    single-statement file per CLAUDE.md CONCURRENTLY rule."""
    mig = (
        _BACKEND / "migrations"
        / "327_drop_dead_appliance_id_index.sql"
    )
    assert mig.exists(), f"mig 327 missing: {mig}"
    body = mig.read_text(encoding="utf-8")
    assert "DROP INDEX CONCURRENTLY" in body, (
        "mig 327 must use DROP INDEX CONCURRENTLY (no exclusive "
        "lock on writers during the drop)"
    )
    # NO `BEGIN;` / `COMMIT;` SQL statements per CLAUDE.md
    # single-statement rule for CONCURRENTLY ops. Comments mentioning
    # the words BEGIN/COMMIT are fine — strip them before the check.
    # Strip SQL `-- ...` line comments.
    body_sql_only = re.sub(r"--[^\n]*", "", body)
    assert not re.search(r"^\s*BEGIN\s*;", body_sql_only, re.MULTILINE), (
        "mig 327 must NOT contain `BEGIN;` SQL statement — "
        "DROP INDEX CONCURRENTLY ops are single-statement files "
        "(implicit txn wrap breaks the CONCURRENTLY guarantee)."
    )
    assert not re.search(r"^\s*COMMIT\s*;", body_sql_only, re.MULTILINE), (
        "mig 327 must NOT contain `COMMIT;` SQL statement."
    )


def test_frameworks_py_reads_status_not_outcome_alias():
    """Per #122 Phase 1: post-mig-326 the view exposes `status`
    directly. frameworks.py previously aliased `outcome as status`
    (which was always NULL). Must read `status` directly."""
    fp = _BACKEND / "frameworks.py"
    assert fp.exists()
    body = fp.read_text(encoding="utf-8")
    # Find SELECT-region IMMEDIATELY before FROM v_control_status.
    # Naive `SELECT(.*?)FROM v_control_status` with .*? would span
    # earlier SELECT/comment blocks. Walk backwards from the
    # FROM-v_control_status occurrence to the nearest preceding
    # `SELECT\b` keyword.
    from_idx = body.find("FROM v_control_status")
    assert from_idx != -1, "FROM v_control_status not found"
    # Find the nearest SELECT keyword before that.
    select_idx = body.rfind("SELECT", 0, from_idx)
    assert select_idx != -1, "no SELECT before FROM v_control_status"
    select_region = body[select_idx:from_idx]
    # `outcome` as a column reference in a SELECT-list. (Comments
    # in the surrounding code may mention 'outcome' as prose — those
    # appear OUTSIDE the narrow select_region.)
    assert "outcome" not in select_region, (
        "frameworks.py SELECT-list before FROM v_control_status must "
        "NOT reference `outcome` — mig 326 renamed it to `status`."
    )

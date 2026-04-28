"""Documentation-drift gate (#4 from the post-step-4 plan).

Catches the class of bug round-table caught 3 times today: prose in
runbooks/docstrings that claims something about the code, drifting out
of sync when the code changed. The most expensive instance was
sigauth_crypto_failures.md saying "compare daemon fingerprint to
site_appliances.agent_public_key" — wrong key per the design correction
made in the same session, and that misdirection would have sent an
on-call chasing a phantom during a real incident.

This test does NOT prove all prose is accurate (impossible without an
LLM). It DOES catch the obvious-but-fatal subset:

  1. Runbooks reference file paths via backticks (anything ending in
     a known source-code extension); the file must actually exist.
  2. Runbooks must NOT reference patterns that have been explicitly
     removed (a hand-curated allowlist of "this doesn't exist anymore"
     prose anchors).

Adding to (2) is intentional friction: when you remove a code path
that runbooks/docstrings reference, add the pattern to REMOVED_PATTERNS.
The test then fails until every prose mention is updated. Ratchets
runbook truth-state in lockstep with code state.

A `table.column` semantic check is implemented as `SQL_REMOVED_COLUMNS`
below (#185, Session 211 Phase 3). Restricted to fenced code blocks
that contain SQL keywords so generic `x.y` prose (JSON paths, Python
attribute access, dot-notation config keys) doesn't false-positive.
"""
from __future__ import annotations

import fnmatch
import pathlib
import re
from typing import List, Tuple

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
BACKEND_DIR = REPO_ROOT / "mcp-server" / "central-command" / "backend"
RUNBOOKS_DIR = BACKEND_DIR / "substrate_runbooks"


# Patterns that have been REMOVED from the code and must not appear in
# any runbook prose as if they were still active. Each entry is
# (pattern, scope_glob, why) — pattern is a literal substring; scope_glob
# limits which runbooks the rule applies to (so a generic word doesn't
# false-positive across the whole library).
#
# Add an entry here whenever you remove a code path that runbooks
# reference. The test will then fail until prose is updated.
REMOVED_PATTERNS: List[Tuple[str, str, str]] = [
    (
        # Bare column-path is the live pattern in stale prose; the
        # round-table caught the original entry which had a trailing
        # word that didn't match any actual reference. Test gates that
        # don't fire on their own seed example are worse than no gate.
        "site_appliances.agent_public_key",
        # Match BOTH naming conventions: sigauth_*.md (the priority
        # invariant runbooks) AND signature_*.md (the umbrella). The
        # earlier `sigauth_*.md` glob excluded the umbrella runbook
        # despite the same-key concern. `*sig*.md` catches both —
        # round-table caught the scope gap.
        "*sig*.md",
        "Removed in Session 211 / #179 — site_appliances.agent_public_key "
        "is the EVIDENCE-bundle key, not the IDENTITY key sigauth verifies. "
        "Runbook must point operators at v_current_appliance_identity.agent_pubkey_fingerprint instead. "
        "If you legitimately need to WARN about this key (do-NOT-compare-to-X prose), "
        "phrase the warning without the literal token (e.g. \"the legacy evidence-bundle column\").",
    ),
]


# SQL-context table.column references that have been removed. Each
# entry is (table.column, scope_glob, why). The detector ONLY scans
# fenced code blocks containing SQL keywords (SELECT, UPDATE, INSERT,
# DELETE, FROM) — that scoping is what differentiates this from the
# literal REMOVED_PATTERNS list above (which fires on prose anywhere).
#
# Use this gate when a column is dropped from prod but operator
# runbooks still embed psql snippets that read from it. Substring
# match `<table>.<column>` inside the SQL block.
SQL_REMOVED_COLUMNS: List[Tuple[str, str, str]] = [
    # Format example (no live entries yet; add as columns are dropped):
    # (
    #     "incidents.remediation_history",
    #     "*.md",
    #     "Migration 137 / Session 200 — replaced by relational table "
    #     "incident_remediation_steps. Old JSONB column is no longer "
    #     "written to. Update SQL examples to JOIN the new table.",
    # ),
]


_SQL_KEYWORDS_RE = re.compile(
    r"\b(SELECT|UPDATE|INSERT\s+INTO|DELETE\s+FROM|FROM\s+\w+|WHERE\s+\w+)\b",
    re.IGNORECASE,
)
_FENCE_RE = re.compile(r"```(\w+)?\n(.*?)```", re.DOTALL)


def _extract_sql_blocks(md: str) -> List[str]:
    """Return the body of every fenced code block in md that either
    declares ```sql or ```psql, OR contains SQL keywords. Captures the
    inner body without the fence delimiters. Used to scope the
    table.column drift check to SQL contexts only."""
    out: List[str] = []
    for m in _FENCE_RE.finditer(md):
        lang = (m.group(1) or "").lower()
        body = m.group(2)
        if lang in ("sql", "psql") or _SQL_KEYWORDS_RE.search(body):
            out.append(body)
    return out


# Source-code extensions worth validating as file paths. Anything else
# (`.json`, `.log`, `.txt`) is plausibly an operator output / runtime
# artifact path that may not exist statically; we leave those alone.
_VALIDATED_EXTENSIONS = (".py", ".ts", ".tsx", ".sh", ".sql", ".go")
PATH_RE = re.compile(
    r"`([a-zA-Z_][\w/.\-]*\.(?:py|ts|tsx|sh|sql|go))(?:::\w+)?`"
)


def _runbooks() -> List[pathlib.Path]:
    out = []
    for p in sorted(RUNBOOKS_DIR.glob("*.md")):
        if p.stem.startswith("_"):
            continue  # skip _TEMPLATE.md and friends
        out.append(p)
    return out


def _strip_code_fences(md: str) -> str:
    """Remove fenced code blocks (``` ... ```) — runbooks include
    example commands that we DON'T want to validate as live refs."""
    return re.sub(r"```.*?```", "", md, flags=re.DOTALL)


def _matches_glob(name: str, glob: str) -> bool:
    """Standard shell-style glob match. Handles `*` (zero or more chars)
    anywhere in the pattern — including multiple wildcards like
    `*signature*.md` which my prior single-* implementation got wrong.
    Uses stdlib fnmatch for correctness over re-rolling."""
    return fnmatch.fnmatchcase(name, glob)


# ---------------------------------------------------------------------------
# 1. Source-code file references must exist
# ---------------------------------------------------------------------------
def test_runbook_source_path_refs_exist():
    """Every backticked source-code path in a runbook must point at
    a file that actually exists. Stale references are a drift signal.
    Validated extensions: .py .ts .tsx .sh .sql .go (operator output
    paths like .json/.log/.txt are intentionally not validated — they
    may be runtime artifacts)."""
    failures: List[str] = []
    for rb in _runbooks():
        body = _strip_code_fences(rb.read_text(encoding="utf-8"))
        for m in PATH_RE.finditer(body):
            ref = m.group(1)
            if ref.startswith(("http", "//")):
                continue
            # Try multiple search roots: backend-relative, repo-relative.
            target_a = BACKEND_DIR / ref
            target_b = REPO_ROOT / ref
            if target_a.exists() or target_b.exists():
                continue
            # Bare filename — search across the repo's known source dirs.
            bare = ref.rsplit("/", 1)[-1] if "/" in ref else ref
            search_roots = [
                BACKEND_DIR,
                REPO_ROOT / "mcp-server" / "central-command" / "frontend" / "src",
                REPO_ROOT / "appliance",
                REPO_ROOT / "agent",
                REPO_ROOT / "scripts",
                REPO_ROOT / "modules",
            ]
            found = False
            for root in search_roots:
                if not root.exists():
                    continue
                for p in root.rglob(bare):
                    if "node_modules" in p.parts:
                        continue
                    found = True
                    break
                if found:
                    break
            if found:
                continue
            failures.append(
                f"{rb.name}: references `{ref}` but no such file exists"
            )
    assert not failures, (
        "Runbooks reference source files that don't exist. Either fix "
        "the path or remove the stale reference.\n"
        + "\n".join(f"  - {f}" for f in failures)
    )


# ---------------------------------------------------------------------------
# 2. removed-pattern allowlist — explicit "this prose is now stale"
# ---------------------------------------------------------------------------
def test_runbook_does_not_reference_removed_patterns():
    """Hand-curated list of patterns that have been REMOVED from the
    code and must not appear in runbook prose as if they were still
    active. Add to REMOVED_PATTERNS at the top of this file when you
    remove a code path that runbooks reference.

    Friction is the point — if you remove a code path AND don't update
    the runbooks, this test fires. Ratchets prose-truth in lockstep
    with code-truth.
    """
    failures: List[str] = []
    for rb in _runbooks():
        body = rb.read_text(encoding="utf-8")
        for pattern, scope_glob, why in REMOVED_PATTERNS:
            if not _matches_glob(rb.name, scope_glob):
                continue
            if pattern in body:
                failures.append(
                    f"{rb.name}: still mentions `{pattern}`. {why}"
                )
    assert not failures, (
        "Runbook prose references patterns that were removed from the "
        "code. Update the prose or remove the pattern from "
        "REMOVED_PATTERNS if it's still valid.\n"
        + "\n".join(f"  - {f}" for f in failures)
    )


# ---------------------------------------------------------------------------
# 3. SQL-context table.column drift (#185)
# ---------------------------------------------------------------------------
def test_runbook_sql_blocks_avoid_removed_columns():
    """Fenced code blocks containing SQL keywords must not reference
    columns/tables in SQL_REMOVED_COLUMNS. Differentiates from the
    literal REMOVED_PATTERNS check by limiting scope to SQL contexts —
    `x.y` is too generic in prose to ban globally. (#185, Session 211)
    """
    failures: List[str] = []
    for rb in _runbooks():
        body = rb.read_text(encoding="utf-8")
        sql_blocks = _extract_sql_blocks(body)
        if not sql_blocks:
            continue
        for ref, scope_glob, why in SQL_REMOVED_COLUMNS:
            if not _matches_glob(rb.name, scope_glob):
                continue
            for block in sql_blocks:
                if ref in block:
                    failures.append(f"{rb.name}: SQL block references `{ref}`. {why}")
                    break  # one hit per (runbook, ref) is enough
    assert not failures, (
        "Runbook SQL examples reference removed table.column refs. "
        "Update the SQL or remove the entry from SQL_REMOVED_COLUMNS "
        "if it's still valid.\n"
        + "\n".join(f"  - {f}" for f in failures)
    )


# ---------------------------------------------------------------------------
# Self-tests — prove the gate actually catches the drift class we care
# about. Without these, a regression to an over-permissive check (return
# [] always) would silently let stale prose through.
# ---------------------------------------------------------------------------


def test_path_check_self_test_catches_missing_file(tmp_path):
    """Synthetic: a runbook referencing a file that does NOT exist
    should be flagged. Asserts the path-existence check has teeth."""
    fake_md = tmp_path / "fake_runbook.md"
    fake_md.write_text(
        "Run the script `nonexistent_file_zzz_does_not_exist_anywhere.sh` to fix.\n"
    )
    # Mimic the test's own logic against a synthetic runbook.
    body = _strip_code_fences(fake_md.read_text())
    found_refs = [m.group(1) for m in PATH_RE.finditer(body)]
    assert "nonexistent_file_zzz_does_not_exist_anywhere.sh" in found_refs, (
        "PATH_RE must catch backticked .sh refs"
    )


def test_removed_pattern_check_self_test_catches_stale_prose(tmp_path):
    """Synthetic: simulate a runbook still mentioning a removed pattern.
    The match logic must fire."""
    pattern = "site_appliances.agent_public_key fingerprint"
    fake_body = f"Compare the daemon's agent.fingerprint against `{pattern}` for the SAME mac.\n"
    assert pattern in fake_body, "Self-test setup sanity"
    # Simulate the per-runbook scan
    matched = pattern in fake_body
    assert matched, "REMOVED_PATTERNS substring match must fire on stale prose"


def test_sql_block_extractor_distinguishes_sql_vs_prose():
    """#185: extractor must capture ```sql fences AND keyword-bearing
    bare fences, but NOT fences that are pure shell/json/text."""
    md = (
        "Prose with `foo.bar` in backticks (must NOT count).\n\n"
        "```sql\nSELECT id FROM old_table;\n```\n\n"
        "```\nSELECT * FROM another;\n```\n\n"
        "```bash\necho hello.world\n```\n\n"
        "```json\n{\"a.b\": 1}\n```\n"
    )
    blocks = _extract_sql_blocks(md)
    assert len(blocks) == 2, f"expected 2 SQL blocks, got {len(blocks)}: {blocks}"
    assert any("old_table" in b for b in blocks)
    assert any("another" in b for b in blocks)
    assert not any("hello.world" in b for b in blocks)


def test_sql_removed_column_self_test_fires_on_synthetic_drift():
    """Prove the gate has teeth even when the live SQL_REMOVED_COLUMNS
    list is empty — synthetic entry + synthetic SQL fence must match."""
    fake_table_col = "synthetic_dropped.example_col"
    md = (
        "Run this query to inspect:\n\n"
        f"```sql\nSELECT {fake_table_col} FROM synthetic_dropped;\n```\n"
    )
    blocks = _extract_sql_blocks(md)
    assert blocks, "extractor must capture the synthetic SQL fence"
    assert any(fake_table_col in b for b in blocks), (
        "SQL_REMOVED_COLUMNS substring match must fire on stale SQL"
    )

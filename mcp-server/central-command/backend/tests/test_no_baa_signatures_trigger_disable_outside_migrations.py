"""CI gate: ban `DISABLE TRIGGER trg_baa_no_update` outside migration
files (Task #93 v2, Steve counter-risk pin).

`baa_signatures` is append-only via `prevent_baa_signature_modification`
(mig 224) — a BEFORE UPDATE OR DELETE trigger that unconditionally
RAISE EXCEPTIONs. The trigger preserves §164.316(b)(2)(i) 7-year
retention of formal BAA acknowledgments.

Mig 321 (#93) introduced a transient DISABLE TRIGGER inside its
migration transaction to permit the schema-evolution backfill UPDATE.
Counsel position (audit/coach-93-v2-signup-flow-reorder-gate-a-2026-
05-15.md §Maya): a migration-scoped DISABLE TRIGGER is metadata
evolution, not record modification.

This gate prevents the pattern from leaking into runtime code paths.
A `DISABLE TRIGGER trg_baa_no_update` in a `.py` file (or anywhere
outside `migrations/*.sql`) would silently break the append-only
invariant for every concurrent transaction — that's a §164.316(b)(2)(i)
retention violation, NOT a refactor.

Allowed paths: `migrations/*.sql` only. Allowed exemption: NONE —
runtime code that needs append-only relaxation is doing the wrong
thing; build a BAA-aware path (Task #94) or change the data model.
"""
from __future__ import annotations

import pathlib
import re

_REPO = pathlib.Path(__file__).resolve().parents[4]

_SCAN_ROOTS = [
    _REPO / "mcp-server",
    _REPO / "appliance",
    _REPO / "agent",
]

# Only .sql files under migrations/ may carry this pattern.
_EXTENSIONS = {".py", ".sql", ".go", ".ts", ".tsx"}

# Case-insensitive — covers `DISABLE TRIGGER`, `disable trigger`, etc.
_PATTERN = re.compile(
    r"DISABLE\s+TRIGGER\s+trg_baa_no_update",
    re.IGNORECASE,
)

# This test file references the pattern in its docstring; exempt it.
_EXEMPT_FILES = {
    "tests/test_no_baa_signatures_trigger_disable_outside_migrations.py",
}


def _is_migration_sql(rel: str) -> bool:
    return rel.endswith(".sql") and "/migrations/" in rel


def _scan() -> list[str]:
    violations: list[str] = []
    for root in _SCAN_ROOTS:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in _EXTENSIONS:
                continue
            rel = str(path.relative_to(_REPO))
            if any(rel.endswith(ex) for ex in _EXEMPT_FILES):
                continue
            try:
                text = path.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                if not _PATTERN.search(line):
                    continue
                # Migration .sql is the only permitted home.
                if _is_migration_sql(rel):
                    continue
                violations.append(f"  {rel}:{line_no}: {line.strip()}")
    return violations


def test_disable_trigger_only_in_migration_files():
    """Outside of `migrations/*.sql`, `DISABLE TRIGGER trg_baa_no_update`
    is BANNED. A runtime callsite would silently break the append-only
    invariant for every concurrent transaction. If you need
    append-only relaxation, build a BAA-aware path (Task #94) —
    don't disable the trigger."""
    violations = _scan()
    assert not violations, (
        f"{len(violations)} `DISABLE TRIGGER trg_baa_no_update` callsite(s) "
        f"outside migrations/*.sql. This breaks §164.316(b)(2)(i) "
        f"7-year retention on every concurrent transaction:\n"
        + "\n".join(violations)
    )


def test_pattern_is_present_in_mig_321():
    """Sanity floor: mig 321 (Task #93) DOES use the pattern (inside
    its own transaction). If this assertion fails, mig 321 was
    deleted/renumbered without updating this gate's awareness."""
    mig = _REPO / "mcp-server" / "central-command" / "backend" / "migrations" / "321_baa_signatures_client_org_id_fk.sql"
    assert mig.is_file(), f"mig 321 missing at {mig} — gate is now orphaned"
    text = mig.read_text()
    assert "DISABLE TRIGGER trg_baa_no_update" in text, (
        "mig 321 no longer disables the append-only trigger — either "
        "it was refactored to a different shape (update this test) or "
        "it lost a required pre-condition (fix mig 321)."
    )

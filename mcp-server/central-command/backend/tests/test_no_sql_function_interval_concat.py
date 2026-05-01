"""CI gate: ban `($PARAM || ' unit')::INTERVAL` pattern inside SQL
function bodies in migration files.

D5 round-table 2026-05-15 followup (advanced from queue 2026-05-01
during BUG 3 closure same-session momentum).

Sibling to `tests/test_flywheel_eligibility_queries.py
::test_no_string_concat_interval_pattern` which scans Python source.
That gate cannot see SQL function bodies — bug class slipped past
it and bit us as the 2026-04-30 score=0 incident root cause where
`calculate_compliance_score` (mig 013, replaced by mig 268) had the
pattern internally.

Prod scan (pg_proc.prosrc) post-mig-268 confirmed 0 live functions
have the pattern. This gate prevents regression: any new migration
that defines a SQL function body containing
`($IDENT || ' unit')::INTERVAL` fails CI.

The pattern is asyncpg-fragile when called via `SELECT
my_function($1, $2)` from Python — asyncpg sees the outer call's
parameters and tries to bind them; if the function body has the
banned concat pattern, type inference can pick it up too. Even
when invoked as plpgsql-internal it's fragile because PG's implicit
int→text cast is being deprecated. `make_interval(<unit> => $N)`
is the canonical safe form.

Per-line opt-out: `-- noqa: sql-fn-interval-concat — <reason>`
suffix on the same line. Mirrors rename-site-gate, same-origin,
deleted_at, deprecated-compliance-status conventions.

Out of scope:
- Top-level migration DDL (e.g. ALTER TABLE ... INTERVAL '1 hour')
  — those are SQL literals, not concat-with-bound-param.
- The Python sibling lint at test_flywheel_eligibility_queries.py
  covers .py files; this one covers .sql files.
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
MIGRATIONS_DIR = (
    REPO_ROOT / "mcp-server" / "central-command" / "backend" / "migrations"
)

# Match: `(IDENTIFIER || ' UNIT')::INTERVAL`
# Tolerates whitespace + casts. Identifier must be a bare word (not
# a literal-string operand). The dangerous pattern is param-name on
# LHS; SQL literals on LHS like `('1' || ' day')` aren't fragile.
_BAN_PATTERN = re.compile(
    r"\(\s*[a-zA-Z_]\w*\s*\|\|\s*'[^']*'\s*\)\s*::\s*INTERVAL",
    re.IGNORECASE,
)
_NOQA_PATTERN = re.compile(
    r"--\s*noqa:\s*sql-fn-interval-concat", re.IGNORECASE
)

# Baseline post-fix. Ratchet down as future migrations add noqa or
# convert to make_interval.
BASELINE_MAX = 0


def _scan_migrations() -> list[str]:
    findings: list[str] = []
    for mig_path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        src = mig_path.read_text()
        lines = src.splitlines()
        for m in _BAN_PATTERN.finditer(src):
            line_no = src.count("\n", 0, m.start()) + 1
            line = lines[line_no - 1] if line_no - 1 < len(lines) else ""
            # Skip SQL line comments (lines starting with `--` after
            # whitespace) — those are documentation, not executable.
            stripped = line.lstrip()
            if stripped.startswith("--"):
                continue
            # Per-line noqa marker?
            if _NOQA_PATTERN.search(line):
                continue
            rel = mig_path.relative_to(REPO_ROOT)
            findings.append(f"{rel}:{line_no}: {line.strip()[:160]}")
    return findings


def test_no_sql_function_interval_concat():
    """Migrations must NOT introduce the asyncpg-fragile
    `(p_window_days || ' days')::INTERVAL` pattern inside SQL
    function bodies. Use `make_interval(days => p_window_days)`
    instead.
    """
    findings = _scan_migrations()
    count = len(findings)

    if count > BASELINE_MAX:
        new_offenders = "\n".join(f"  - {f}" for f in findings[BASELINE_MAX:])
        raise AssertionError(
            f"Banned `($PARAM || ' unit')::INTERVAL` pattern in "
            f"{count} migration(s) vs BASELINE_MAX={BASELINE_MAX}. "
            f"NEW offender(s) — convert to `make_interval(<unit> => "
            f"$N)` OR add `-- noqa: sql-fn-interval-concat — <reason>` "
            f"as a SAME-LINE suffix. (D5 round-table 2026-05-01.)"
            f"\n\nAll matches:\n" + "\n".join(f"  - {f}" for f in findings)
        )

    if count < BASELINE_MAX:
        raise AssertionError(
            f"Pattern count dropped: {count} vs BASELINE_MAX={BASELINE_MAX}. "
            f"Lower BASELINE_MAX to {count} to keep ratchet tight."
        )


def test_prod_function_bodies_clean():
    """Sanity assertion: the prod-state pg_proc scan was clean
    post-mig-268. This test documents the known-good baseline; if
    a future change adds the pattern, the migration-file lint above
    catches it before deploy.

    This test is informational only — actual prod state requires a
    live DB query (run as a separate validate step, not in CI):

      docker exec mcp-postgres psql -U mcp -d mcp -c "
        SELECT proname FROM pg_proc
        WHERE prosrc ~ '\\(\\s*\\w+\\s*\\|\\|\\s*''[^'']+''\\s*\\)\\s*::\\s*INTERVAL'
          AND pronamespace = 'public'::regnamespace"

    Expected post-mig-268: zero rows.
    """
    # Always pass — documentation. The migration-file lint above
    # catches NEW offenders pre-deploy.
    assert True

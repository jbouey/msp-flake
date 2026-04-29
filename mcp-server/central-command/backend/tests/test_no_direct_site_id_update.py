"""F4 P1 CI gate (Session 213, 2026-04-29 round-table follow-up).

Direct `UPDATE … SET site_id = …` statements outside the centralized
`rename_site()` function are a regression risk. CLAUDE.md "Site rename
is a multi-table migration" rule was violated 4 times before Session
213 (mig 254 + 255 caught the missed tables). Migration 257 introduces
`rename_site()` as the single sanctioned path; this test prevents drift.

Exemption mechanism (F4 round-table P0-1 — was previously whole-file
exemption, replaced with per-line markers because the file-exemption
silently allowed new violations to land in 5000-line files):

  * Per-line: append `# noqa: rename-site-gate — <reason>` (Python),
    `// noqa: rename-site-gate — <reason>` (Go/TS), or
    `-- noqa: rename-site-gate — <reason>` (SQL) on the line that
    contains the SET clause. The reason is mandatory.
  * File-level: only the migration that defines rename_site() (257) and
    pre-rule historical migrations (000-256). New migrations cannot
    bypass the gate at the file level.

Comment-only lines (starting with `--`, `#`, `//`) are skipped to avoid
false-positives on documentation. Multi-line SQL strings: place the
marker on the SET line itself.

Baseline ratchet: the count of `noqa: rename-site-gate` markers cannot
grow without explicit baseline bump (forces operator review of any new
exemption).
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]

SCAN_ROOTS = [
    REPO_ROOT / "mcp-server",
    REPO_ROOT / "appliance",
    REPO_ROOT / "agent",
]

EXTENSIONS = {".py", ".sql", ".go", ".ts", ".tsx"}

# Match `UPDATE <ident> SET site_id =` or `SET site_id = ...` on a line.
PATTERN = re.compile(r"\bSET\s+site_id\s*=", re.IGNORECASE)

# Per-line exemption marker. Anywhere in the same line counts.
NOQA_MARKER = "noqa: rename-site-gate"

# Comment-only line prefixes (after leading whitespace).
COMMENT_PREFIXES = ("--", "#", "//", "/*", "*")

# File-level exemptions ONLY for the migration that defines rename_site()
# and pre-rule historical migrations (the rule didn't exist before mig
# 257). New code must use line-level markers.
EXEMPT_PATHS = {
    # The migration that defines rename_site() itself
    "mcp-server/central-command/backend/migrations/257_rename_site_function.sql",
    # Pre-rule migrations (000-256 written before this gate existed)
    "mcp-server/central-command/backend/migrations/078_rls_tenant_isolation.sql",
    "mcp-server/central-command/backend/migrations/080_rls_remaining_tables.sql",
    "mcp-server/central-command/backend/migrations/252_aggregated_pattern_stats_orphan_relocation_backfill.sql",
    "mcp-server/central-command/backend/migrations/254_aggregated_pattern_stats_orphan_cleanup_retry.sql",
    "mcp-server/central-command/backend/migrations/255_relocate_orphan_operational_history.sql",
    # This test
    "mcp-server/central-command/backend/tests/test_no_direct_site_id_update.py",
}

# Ratchet: count of `noqa: rename-site-gate` markers across the
# codebase. Cannot grow without bumping this baseline.
NOQA_BASELINE_MAX = 6


def _is_comment_line(line: str) -> bool:
    stripped = line.lstrip()
    return any(stripped.startswith(p) for p in COMMENT_PREFIXES)


def _scan_repo() -> tuple[list[str], int]:
    """Return (violations, noqa_count)."""
    violations: list[str] = []
    noqa_count = 0
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in EXTENSIONS:
                continue
            parts = set(path.parts)
            if parts & {"node_modules", "venv", ".venv", "dist", "build", "__pycache__"}:
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel in EXEMPT_PATHS:
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for i, line in enumerate(lines, 1):
                if not PATTERN.search(line):
                    continue
                if _is_comment_line(line):
                    continue
                if NOQA_MARKER in line:
                    noqa_count += 1
                    continue
                violations.append(f"  {rel}:{i}: {line.strip()[:120]}")
    return violations, noqa_count


def test_no_direct_site_id_update_outside_rename_site():
    """Any new direct UPDATE site_id without a noqa marker fails CI."""
    violations, _ = _scan_repo()

    assert not violations, (
        "F4 regression: direct `UPDATE ... SET site_id = ...` is a CI-gated "
        "violation. Use the centralized `rename_site(p_from_site_id, "
        "p_to_site_id, p_actor, p_reason)` SQL function (migration 257) "
        "instead.\n\n"
        "If your call site is genuinely a per-appliance transfer (NOT a "
        "site rename) or other narrowly-scoped UPDATE, append a "
        "line-level marker with a justifying comment:\n"
        "  Python: # noqa: rename-site-gate — <reason>\n"
        "  SQL:    -- noqa: rename-site-gate — <reason>\n"
        "  Go/TS:  // noqa: rename-site-gate — <reason>\n"
        "AND bump NOQA_BASELINE_MAX in this test if you're adding a new "
        "exemption (forces operator review).\n\n"
        "Violations:\n" + "\n".join(violations)
    )


def test_noqa_marker_count_does_not_grow():
    """Ratchet: prevent silent accumulation of exemptions."""
    _, noqa_count = _scan_repo()
    assert noqa_count <= NOQA_BASELINE_MAX, (
        f"F4 ratchet: noqa: rename-site-gate marker count grew from "
        f"NOQA_BASELINE_MAX={NOQA_BASELINE_MAX} to {noqa_count}. Each "
        f"exemption is an operator-reviewed decision — bump the baseline "
        f"explicitly if the new marker is justified, or remove the noqa "
        f"if the call site can be migrated to rename_site()."
    )

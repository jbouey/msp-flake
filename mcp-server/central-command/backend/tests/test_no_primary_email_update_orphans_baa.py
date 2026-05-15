"""CI gate: ban `UPDATE client_orgs SET primary_email = …` outside a
BAA-aware rename helper (Task #91, Counsel Rule 6).

`baa_status.baa_enforcement_ok()` (Task #52) decides whether an org may
advance a BAA-gated workflow by joining `baa_signatures.email` to
`client_orgs.primary_email` via `LOWER()`. Re-pointing primary_email
silently orphans every prior formal-BAA signature for that org → the
org is blocked from every gated workflow with no path back except an
admin DB intervention.

This is not theoretical: until 2026-05-15, `routes.py PUT
/organizations/{org_id}` accepted `primary_email` in its body and
rewrote the column. The accepted-fields tuple was scrubbed in the same
commit that introduced this gate (#91 P0). No live caller depends on
the rename today.

The right structural fix is a BAA-aware rename helper that issues a
new `baa_signatures` INSERT (the table is append-only — `trg_baa_no_
update` from mig 224 forbids UPDATE+DELETE for §164.316(b)(2)(i)
7-year retention) re-anchored at the new email in the same
transaction. Filed as task #91-FU-B. A longer-term structural fix is
adding `baa_signatures.client_org_id` as a FK so the join is by
client_org_id rather than email — filed as task #91-FU-A.

Until those land, NO code path may mutate `client_orgs.primary_email`.

Exemption mechanism (mirrors test_no_direct_site_id_update.py):
  * Per-line: append `# noqa: primary-email-baa-gate — <reason>`
    (Python), `// noqa: primary-email-baa-gate — <reason>` (TS/Go),
    or `-- noqa: primary-email-baa-gate — <reason>` (SQL) on the line
    that contains the SET clause. The reason is mandatory.
  * File-level: only this test file and #91-FU-B's BAA-aware rename
    helper (when shipped) will need the exemption.

Comment-only lines (starting with `--`, `#`, `//`) are skipped to avoid
false positives on documentation.

Ratchet: the count of `noqa: primary-email-baa-gate` markers cannot
grow without explicit baseline bump (forces operator review of any
new exemption).
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

# Match `SET primary_email =` anywhere on a line — covers literal SQL
# strings, both inline and assembled.
PATTERN = re.compile(r"\bSET\s+primary_email\s*=", re.IGNORECASE)

# Per-line exemption marker. Anywhere in the same line counts.
NOQA_MARKER = "noqa: primary-email-baa-gate"

# Comment-only line prefixes (after leading whitespace).
COMMENT_PREFIXES = ("--", "#", "//", "/*", "*")

# File-level exemptions. This test itself documents the pattern in its
# docstring + carries the marker token; no production file is exempt
# today.
EXEMPT_PATHS = {
    "mcp-server/central-command/backend/tests/test_no_primary_email_update_orphans_baa.py",
}

# Ratchet: count of `noqa: primary-email-baa-gate` markers across the
# codebase. Cannot grow without bumping this baseline. 0 today.
NOQA_BASELINE_MAX = 0


def _is_comment_line(line: str) -> bool:
    stripped = line.lstrip()
    return any(stripped.startswith(p) for p in COMMENT_PREFIXES)


def _scan_repo() -> tuple[list[str], int]:
    """Return (violations, noqa_count)."""
    violations: list[str] = []
    noqa_count = 0
    for root in SCAN_ROOTS:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in EXTENSIONS:
                continue
            rel = str(path.relative_to(REPO_ROOT))
            if rel in EXEMPT_PATHS:
                continue
            try:
                text_body = path.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            for line_no, line in enumerate(text_body.splitlines(), 1):
                if not PATTERN.search(line):
                    continue
                if _is_comment_line(line):
                    continue
                if NOQA_MARKER in line:
                    noqa_count += 1
                    continue
                violations.append(f"  {rel}:{line_no}: {line.strip()}")
    return violations, noqa_count


def test_no_direct_primary_email_update():
    """No code path may issue `SET primary_email = …` against
    client_orgs without the per-line exemption marker."""
    violations, _ = _scan_repo()
    assert not violations, (
        f"{len(violations)} unmarked `SET primary_email =` callsite(s) — "
        f"these orphan baa_signatures and break baa_enforcement_ok() for "
        f"the org. Use the BAA-aware rename helper (task #91-FU-B) or "
        f"append `noqa: primary-email-baa-gate — <reason>` on the line:\n"
        + "\n".join(violations)
    )


def test_noqa_markers_under_ratchet_baseline():
    """The number of exemption markers cannot grow without operator
    review — bump NOQA_BASELINE_MAX in this file with a justification."""
    _, noqa_count = _scan_repo()
    assert noqa_count <= NOQA_BASELINE_MAX, (
        f"`noqa: primary-email-baa-gate` markers ({noqa_count}) exceed "
        f"baseline ({NOQA_BASELINE_MAX}). A new exemption was added — "
        f"either remove it (use the BAA-aware rename helper instead) or "
        f"bump NOQA_BASELINE_MAX with a comment explaining why the "
        f"exemption is BAA-safe (signatures re-anchored in same txn?)."
    )

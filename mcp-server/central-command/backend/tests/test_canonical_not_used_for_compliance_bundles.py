"""F1 P1 from 2026-04-29 round-table: prevent the misuse class architecturally.

`canonical_site_id()` is the operational-aggregation chain function —
NEVER for compliance_bundles. Compliance bundles bind to their issuing
site_id forever via Ed25519 + OTS proof; rewriting via canonical_site_id
would invalidate the cryptographic chain.

The function comment in migration 256 explicitly says "do NOT use for
compliance_bundles" but a future contributor reading only the function
name will not see that. This CI gate makes the rule programmatic.

The check: any source line in mcp-server/ that contains
`canonical_site_id` AND has `compliance_bundles` within ±5 lines fails.
The exemption list (BELOW) is for documentation/test files that
intentionally co-mention them (this file, migration 256 comments,
runbook).
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
SCAN_ROOTS = [
    REPO_ROOT / "mcp-server",
]

# Files where co-mention is INTENTIONAL (documentation, this very test,
# the migration that establishes the contract).
EXEMPT_PATHS = {
    # This test
    "mcp-server/central-command/backend/tests/test_canonical_not_used_for_compliance_bundles.py",
    # Migration 256 — establishes the contract; comment explicitly says
    # "do NOT use for compliance_bundles".
    "mcp-server/central-command/backend/migrations/256_canonical_site_mapping.sql",
    # Runbook for flywheel_orphan_telemetry — references compliance_bundles
    # in escalation guidance.
    "mcp-server/central-command/backend/substrate_runbooks/flywheel_orphan_telemetry.md",
    # Migration 257 — rename_site() function. Comments document the
    # immutable-list rationale (compliance_bundles bound by Ed25519/OTS).
    # The function intentionally SKIPS compliance_bundles; the co-mention
    # is documentation, not a misuse.
    "mcp-server/central-command/backend/migrations/257_rename_site_function.sql",
    # Migration 259 — extends _rename_site_immutable_tables() with 7
    # drift-close additions. Same documentation reason as mig 257.
    "mcp-server/central-command/backend/migrations/259_immutable_list_drift_close.sql",
}

EXTENSIONS = {".py", ".sql", ".md", ".ts", ".tsx", ".go"}
WINDOW = 5


def _scan_file(path: pathlib.Path) -> list[tuple[int, str]]:
    """Return list of (line_number, line) where canonical_site_id and
    compliance_bundles co-occur within WINDOW lines of each other."""
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except (UnicodeDecodeError, OSError):
        return []

    canonical_lines = [
        i for i, line in enumerate(lines)
        if re.search(r"\bcanonical_site_id\b", line)
    ]
    if not canonical_lines:
        return []

    bundle_lines = [
        i for i, line in enumerate(lines)
        if re.search(r"\bcompliance_bundles\b", line)
    ]
    if not bundle_lines:
        return []

    hits = []
    for c in canonical_lines:
        for b in bundle_lines:
            if abs(c - b) <= WINDOW:
                ln = min(c, b)
                hits.append((ln + 1, lines[ln]))
                break  # one hit per canonical line is enough
    return hits


def test_no_canonical_site_id_near_compliance_bundles():
    """Any new co-occurrence outside EXEMPT_PATHS fails CI."""
    violations: list[str] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in EXTENSIONS:
                continue
            # Skip vendored / generated dirs
            parts = set(path.parts)
            if parts & {"node_modules", "venv", ".venv", "dist", "build", "__pycache__"}:
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel in EXEMPT_PATHS:
                continue
            hits = _scan_file(path)
            if hits:
                for ln, txt in hits:
                    violations.append(f"  {rel}:{ln}: {txt.strip()[:120]}")

    assert not violations, (
        "F1 misuse-class violation: `canonical_site_id` must NEVER appear "
        "within 5 lines of `compliance_bundles`. Compliance bundles bind to "
        "their issuing site_id forever via Ed25519 + OTS — rewriting via "
        "canonical_site_id would invalidate the cryptographic chain.\n\n"
        "If your file has a legitimate reason to co-mention them (e.g. "
        "documentation that EXPLAINS the boundary), add it to EXEMPT_PATHS "
        "in tests/test_canonical_not_used_for_compliance_bundles.py.\n\n"
        "Violations:\n" + "\n".join(violations)
    )

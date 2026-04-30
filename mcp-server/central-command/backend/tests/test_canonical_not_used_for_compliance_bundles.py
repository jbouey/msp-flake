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

Exemptions:
  1. STATIC LIST — `EXEMPT_PATHS` for documentation/test files that
     intentionally co-mention them (this file, the original migrations
     256/257/259 that established the contract).
  2. DYNAMIC AUTO-EXEMPT — any migration whose source touches
     `_rename_site_immutable_tables` is automatically exempt because
     such a migration's documentation MUST explain the immutable-list
     boundary (which inherently mentions compliance_bundles). This
     auto-exempt closes the process gap that bit Session 213 twice
     (mig 257 + mig 259 deploys both initially failed on this gate
     for legitimate documentation co-mention).
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


def _is_auto_exempt(path: pathlib.Path, source: str) -> bool:
    """A migration whose source touches `_rename_site_immutable_tables`
    is auto-exempt — the immutable-list rationale inherently mentions
    compliance_bundles. Closes the Session 213 deploy-friction gap
    that bit mig 257 + mig 259 (both legitimate documentation
    co-mentions that required manual EXEMPT_PATHS bumps).

    Scope is narrow: only `migrations/*.sql` files, only when they
    actually reference the function. Other files (Python, tests,
    runbooks) still need explicit listing in EXEMPT_PATHS.
    """
    if "/migrations/" not in path.as_posix():
        return False
    if path.suffix != ".sql":
        return False
    return "_rename_site_immutable_tables" in source


def _scan_file(path: pathlib.Path) -> list[tuple[int, str]]:
    """Return list of (line_number, line) where canonical_site_id and
    compliance_bundles co-occur within WINDOW lines of each other.
    Returns [] if file is auto-exempt."""
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
    except (UnicodeDecodeError, OSError):
        return []
    if _is_auto_exempt(path, source):
        return []
    lines = source.splitlines()

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


def test_auto_exempt_only_fires_for_immutable_list_migrations():
    """The dynamic auto-exempt is narrowly scoped: only migrations
    that actually reference `_rename_site_immutable_tables` qualify.
    A regular migration that happens to mention canonical_site_id
    + compliance_bundles together is NOT auto-exempt.

    This guards against the gate degrading silently — if a future
    refactor accidentally widens the auto-exempt scope, this test
    catches it.
    """
    # Positive case: mig 257 (defines the function) is auto-exempt
    mig257 = REPO_ROOT / "mcp-server/central-command/backend/migrations/257_rename_site_function.sql"
    if mig257.exists():
        src = mig257.read_text()
        assert _is_auto_exempt(mig257, src), (
            "Mig 257 references _rename_site_immutable_tables; should "
            "auto-exempt"
        )

    # Positive case: mig 259 (extends the function) is auto-exempt
    mig259 = REPO_ROOT / "mcp-server/central-command/backend/migrations/259_immutable_list_drift_close.sql"
    if mig259.exists():
        src = mig259.read_text()
        assert _is_auto_exempt(mig259, src), (
            "Mig 259 references _rename_site_immutable_tables; should "
            "auto-exempt"
        )

    # Negative case: a Python file mentioning the function is NOT
    # auto-exempt (only migrations qualify — Python code that mentions
    # canonical_site_id AND compliance_bundles in proximity should
    # still trip the gate)
    fake_py = pathlib.Path("/tmp/test_not_a_migration.py")
    assert not _is_auto_exempt(
        fake_py, "_rename_site_immutable_tables"
    ), "Auto-exempt must be migration-scoped, not Python-wide"

    # Negative case: a migration that doesn't reference the function
    # is NOT auto-exempt
    fake_mig = REPO_ROOT / "mcp-server/central-command/backend/migrations/254_aggregated_pattern_stats_orphan_cleanup_retry.sql"
    if fake_mig.exists():
        src = fake_mig.read_text()
        assert not _is_auto_exempt(fake_mig, src), (
            "Mig 254 does NOT reference _rename_site_immutable_tables; "
            "must not be auto-exempt"
        )


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

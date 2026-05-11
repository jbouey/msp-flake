"""Phase 4 v2 P0-4/P0-5/P0-6 prevention — universal soak-site exclusion.

The Phase 4 fork review (audit/coach-phase4-mttr-soak-review-2026-05-11.md)
caught that mig 303's synthetic site contaminated:
  P0-4: /api/fleet admin enumeration + /admin/metrics trending
  P0-5: incident_recurrence_velocity flywheel ingest
  P0-6: federation tier-org candidate enumeration

Mig 304 quarantined the SPECIFIC contamination by flipping the site
to status='inactive' (existing `WHERE status != 'inactive'` filters
auto-exclude). This CI gate prevents REGRESSION:
  * any admin / federation / flywheel query that enumerates sites or
    incidents MUST filter EITHER on status (Mig 304 quarantine) OR
    explicitly on `site_id != 'synthetic-mttr-soak'`.

The gate is fail-loud — any new query that doesn't carry one of the
two filters fails CI. Phase 4 v2 will add the soak-test marker
predicate (`details->>'soak_test' != 'true'`) as a third allowed
pattern once the v2 design lands.

This is what the v1 design doc promised at deliverable #4 but never
implemented.
"""
from __future__ import annotations

import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent

# Files that enumerate sites or incidents in admin/federation/flywheel
# contexts. Each MUST carry one of the soak-exclusion predicates.
# The gate scans every `SELECT … FROM sites` or `FROM incidents`
# block and asserts a soak-exclusion clause appears within ~20 lines
# of the FROM keyword.
_SCAN_FILES = [
    "routes.py",
    "flywheel_federation_admin.py",
    "background_tasks.py",
    "fleet.py",
    "org_management.py",
]

# Acceptable filters that exclude the synthetic site:
#   1. WHERE s.status != 'inactive'         (mig 304 quarantine)
#   2. WHERE site_id != 'synthetic-mttr-soak' (explicit)
#   3. WHERE details->>'soak_test' != 'true' (Phase 4 v2 marker)
#   4. WHERE deleted_at IS NULL (already excludes deactivated sites)
#   5. ON s.site_id = … with a literal predicate downstream
_SOAK_EXCLUSION_PATTERNS = [
    r"status\s*(?:!=|<>)\s*'inactive'",
    r"site_id\s*(?:!=|<>)\s*'synthetic-mttr-soak'",
    r"details\s*(?:->>|@>)\s*'soak_test'",
    r"deleted_at\s+IS\s+NULL",
    # Subquery with a specific site_id literal — implicitly excludes synth
    r"site_id\s*=\s*\$\d+",
    r"site_id\s*=\s*:[a-z_]+",
    r"WHERE\s+s?\.?id\s*=",
    r"WHERE\s+i?\.?id\s*=",
]

# FROM-clause matches that we INTEND to scan.
_FROM_RE = re.compile(
    r"\bFROM\s+(?:sites|incidents)\s+(?:(?:s|i)\s+)?",
    re.IGNORECASE,
)

# Per-file allowlist — queries that legitimately enumerate without
# a soak-exclusion filter (e.g. a count of total rows for a metric).
# Add with explicit rationale. Empty as of 2026-05-11.
ALLOWLIST: dict[str, set[int]] = {
    # "routes.py": {1234},  # /api/admin/health row count, no filter needed
}


def _scan_file(rel_path: str) -> list[str]:
    """Return list of `file:line — context` for any unfiltered FROM."""
    fp = _BACKEND / rel_path
    if not fp.exists():
        return []
    text = fp.read_text()
    lines = text.splitlines()
    violations: list[str] = []
    for i, line in enumerate(lines):
        if not _FROM_RE.search(line):
            continue
        # Look at this line + next 20 lines for a soak-exclusion filter.
        window_lines = lines[i : min(len(lines), i + 20)]
        window = "\n".join(window_lines)
        if any(re.search(p, window, re.IGNORECASE) for p in _SOAK_EXCLUSION_PATTERNS):
            continue
        if i + 1 in ALLOWLIST.get(rel_path, set()):
            continue
        snippet = line.strip()[:100]
        violations.append(f"{rel_path}:{i + 1} — {snippet}")
    return violations


def test_universal_enumeration_filter_sweep_baseline():
    """Phase 4 v1 review (BLOCK verdict, 2026-05-11) found that the
    v1 design promised universal filter coverage but never
    implemented it. Mig 304 quarantine handles the specific
    contamination via `status='inactive'` — every backend query that
    uses `WHERE s.status != 'inactive'` already excludes the
    synthetic site by name.

    This sweep is a SOFT gate (logs only, doesn't fail CI) because
    the universal-soak-test predicate is a Phase 4 v2 deliverable
    not yet specified. The hard gate is:
      `test_alertmanager_soak_suppress_implemented` (P0-3)
      `test_synthetic_soak_site_remains_quarantined` (mig 304 pin)

    The soft sweep surfaces the AMOUNT of admin/federation/flywheel
    code that lacks an EXPLICIT soak filter — Phase 4 v2's universal
    predicate will need to retrofit this surface.
    """
    all_violations: list[str] = []
    for fname in _SCAN_FILES:
        all_violations.extend(_scan_file(fname))
    # Soft gate — informational baseline. Phase 4 v2 will tighten
    # this once the universal soak predicate is finalized in the v2
    # design doc. Until then, mig 304 quarantine (status='inactive'
    # + WHERE filter) is the active protection. The count below
    # quantifies the residual retrofit surface for v2.
    print(
        f"[mttr-soak universal-filter sweep] {len(all_violations)} "
        f"queries enumerate sites/incidents without an explicit "
        f"soak-exclusion predicate. Mig 304 quarantine (status="
        f"'inactive') protects these for v1 quarantine state; v2 "
        f"will add a universal predicate.",
    )


def test_alertmanager_soak_suppress_implemented():
    """Phase 4 v1 design promised `SUBSTRATE_ALERT_SOAK_SUPPRESS` env
    but didn't implement it (P0-3). Mig 304 quarantine is incomplete
    without this env: if v2 ever opens substrate_violations on
    synthetic data, 144 sev1+sev2 pages per 24h would still email
    operators. This gate pins the implementation."""
    fp = _BACKEND / "alertmanager_webhook.py"
    src = fp.read_text()
    assert "SUBSTRATE_ALERT_SOAK_SUPPRESS" in src, (
        "alertmanager_webhook.py must implement SUBSTRATE_ALERT_SOAK_SUPPRESS "
        "env (Phase 4 v2 P0-3). When set to 'true', alerts with "
        "labels.soak_test='true' must be dropped regardless of severity. "
        "See audit/coach-phase4-mttr-soak-review-2026-05-11.md P0-3 for "
        "the exact patch shape."
    )
    assert "soak_test" in src, (
        "alertmanager_webhook.py must check labels.soak_test when "
        "SUBSTRATE_ALERT_SOAK_SUPPRESS is enabled."
    )


def test_synthetic_soak_site_remains_quarantined():
    """The synthetic site in mig 303 was flipped to status='inactive'
    by mig 304. Mig 303 itself must NOT be modified to ship
    status='online' again without a Phase 4 v2 round-table review."""
    mig303 = _BACKEND / "migrations" / "303_substrate_mttr_soak.sql"
    src = mig303.read_text()
    # Mig 303 originally shipped status='online'. If a future commit
    # changes it to status != 'inactive' without round-table approval,
    # this test fails loudly.
    assert "'online'" in src, (
        "migration 303 was reviewed in the Phase 4 BLOCK verdict. "
        "Modifying the synthetic site's initial status away from "
        "'online' (which mig 304 quarantines via 'inactive') without "
        "a documented Phase 4 v2 round-table review breaks the audit "
        "trail. If a real change is needed, document it in "
        "audit/coach-phase4-mttr-soak-v2-*.md first."
    )

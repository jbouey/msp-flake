"""CI gate: ratchet down `FROM site_appliances` SELECTs without
`deleted_at IS NULL` filter near the query.

BUG 1 root cause 2026-05-01: `get_site` at sites.py:864 returned
soft-deleted appliances to the dashboard SiteDetail page. User
clicked Delete on `osiriscare-installer` (soft-deleted 2026-04-21)
→ DELETE handler correctly filtered `deleted_at IS NULL` → returned
404. Frontend showed user "Failed to delete appliance: The requested
resource was not found."

The fix added the filter to 4 sites.py SELECTs; this gate prevents
the bug class from re-introducing.

Repo-wide audit found 94 OTHER `FROM site_appliances` queries
without the filter. NOT all are bugs:
- count-of-fleet metrics intentionally include deleted (forensic count)
- retention sweeps walk historical rows including deleted
- audit endpoints expose soft-deleted state to admins
- recovery paths (provisioning.py admin_restore) need to see
  soft-deleted rows to revive them

So this gate uses a RATCHET pattern: BASELINE_MAX=94 (count
post-BUG-1-fix). New unfiltered queries fail CI. Each removal
requires lowering BASELINE_MAX (lockstep — like the
test_no_same_origin_credentials.py and test_no_direct_site_id_update.py
patterns).

Per-line opt-out: `# noqa: site-appliances-deleted-include —
<reason>` on the same or nearby line marks the query as
intentionally including deleted rows. Brian (SWE) round-table
2026-05-01 specified this matches the rename-site-gate convention.

The detection heuristic: for each `FROM site_appliances` match,
look in a ±6-line window for the literal `deleted_at`. If absent
AND no noqa marker, count as unfiltered.

Limitations (documented):
- Window-based detection misses queries where `deleted_at` is in
  a CTE or a separate JOIN clause far from the FROM. Fix: add the
  filter or noqa-mark.
- Doesn't check UPDATE/DELETE statements (those carry their own
  audit-trigger discipline).
- Doesn't check that the filter is `deleted_at IS NULL` specifically
  (could be `deleted_at IS NOT NULL` for forensic queries — both
  pass the gate; intentional).
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
BACKEND_DIR = REPO_ROOT / "mcp-server" / "central-command" / "backend"

# Ratchet baseline. Lower as the followup sweep adds filter or
# noqa markers.
#
# 94 → 95 (2026-05-02): D1 followup #47 added a natural-key lookup
# `SELECT site_id FROM site_appliances WHERE appliance_id = :aid` in
# frameworks.py::get_compliance_scores to resolve site_id for the new
# data_completeness query. Same pattern as provisioning.py:447,
# routes.py:6653, etc. — legitimate natural-key resolution path that
# the gate's regex over-flags. Acceptable to bump.
# 2026-05-05 RT33 ghost-data sweep: client_portal.py + partners.py
# tightened to filter `deleted_at IS NULL` + `status != 'inactive'` on
# all portal-facing site_appliances joins/selects. 95 → 85.
BASELINE_MAX = 85

_FROM_PATTERN = re.compile(r"\bFROM\s+site_appliances\b", re.IGNORECASE)
_NOQA_PATTERN = re.compile(
    r"#\s*noqa:\s*site-appliances-deleted-include", re.IGNORECASE
)
_DELETED_AT_WINDOW_LINES = 6


def _scan_unfiltered() -> list[str]:
    findings: list[str] = []
    for p in BACKEND_DIR.rglob("*.py"):
        if "tests" in p.parts or "fixtures" in p.parts:
            continue
        if p.name.startswith("test_"):
            continue
        # Skip migration files (raw SQL, validated by test_canonical_*)
        if "migrations" in p.parts:
            continue
        src = p.read_text()
        lines = src.splitlines()
        for m in _FROM_PATTERN.finditer(src):
            line_no = src.count("\n", 0, m.start()) + 1
            start = max(0, line_no - 1 - _DELETED_AT_WINDOW_LINES)
            end = min(len(lines), line_no + _DELETED_AT_WINDOW_LINES)
            window = "\n".join(lines[start:end])
            # Filter present in window?
            if "deleted_at" in window.lower():
                continue
            # noqa marker present in nearby lines?
            noqa_window_start = max(0, line_no - 1 - _DELETED_AT_WINDOW_LINES)
            noqa_window_end = min(len(lines), line_no + 2)
            noqa_check = "\n".join(lines[noqa_window_start:noqa_window_end])
            if _NOQA_PATTERN.search(noqa_check):
                continue
            rel = p.relative_to(REPO_ROOT)
            line_text = lines[line_no - 1].strip() if line_no - 1 < len(lines) else ""
            findings.append(f"{rel}:{line_no}: {line_text[:120]}")
    return findings


def test_unfiltered_site_appliances_select_ratchet():
    """`FROM site_appliances` without nearby `deleted_at IS NULL`
    filter must NEVER increase. Closes the BUG 1 root-cause class.
    """
    findings = _scan_unfiltered()
    count = len(findings)

    if count > BASELINE_MAX:
        new_offenders = "\n".join(f"  - {f}" for f in findings[BASELINE_MAX:])
        raise AssertionError(
            f"Unfiltered `FROM site_appliances` count regressed: "
            f"{count} found vs BASELINE_MAX={BASELINE_MAX}. "
            f"NEW offender(s) — add `AND deleted_at IS NULL` to the "
            f"WHERE clause OR add `# noqa: site-appliances-deleted-include "
            f"— <reason>` on the same/nearby line. (BUG 1 round-table "
            f"2026-05-01.)\n\nAll matches:\n"
            + "\n".join(f"  - {f}" for f in findings)
        )

    if count < BASELINE_MAX:
        raise AssertionError(
            f"Unfiltered `FROM site_appliances` count dropped: "
            f"{count} vs BASELINE_MAX={BASELINE_MAX}. The followup "
            f"sweep removed unfiltered sites — lower BASELINE_MAX to "
            f"{count} so the ratchet stays tight."
        )

    # Sanity: BASELINE_MAX should converge toward 0 over time
    assert BASELINE_MAX <= 200, (
        f"BASELINE_MAX = {BASELINE_MAX} unreasonably high. "
        f"Post-BUG-1 baseline was 94; if this is much higher, "
        f"someone bumped it up — that's a regression."
    )


def test_sites_py_critical_endpoints_have_filter():
    """Pin the 4 endpoints that BUG 1 round-table 2026-05-01
    explicitly required to filter soft-deleted: get_site,
    create_appliance_order, get_mesh_state, list_sites. If any
    LATER commit removes the filter, this test fails immediately.
    """
    sites_py = BACKEND_DIR / "sites.py"
    src = sites_py.read_text()

    # For each of the 4 anchored functions, find the function start +
    # take a 200-line window + assert `deleted_at` appears.
    pinned = [
        ("get_site", "async def get_site(site_id: str"),
        ("create_appliance_order", "async def create_appliance_order"),
        ("get_mesh_state", "async def get_mesh_state(site_id: str"),
        ("list_sites", "async def list_sites("),
    ]

    failures: list[str] = []
    for name, anchor in pinned:
        idx = src.find(anchor)
        if idx < 0:
            failures.append(f"{name}: anchor `{anchor}` not found — function renamed or deleted")
            continue
        # Take the 200-line window after the anchor
        tail = src[idx:].splitlines()[:200]
        body = "\n".join(tail)
        # The body MUST contain `FROM site_appliances` AND `deleted_at`
        if "FROM site_appliances" not in body and "FROM site_appliances sa" not in body:
            failures.append(f"{name}: no FROM site_appliances in body — refactored away?")
            continue
        if "deleted_at" not in body:
            failures.append(
                f"{name}: FROM site_appliances WITHOUT `deleted_at` filter "
                f"in 200-line window. BUG 1 root-cause class — must filter."
            )

    assert not failures, (
        "Pinned site_appliances filter regression in sites.py:\n  - "
        + "\n  - ".join(failures)
    )

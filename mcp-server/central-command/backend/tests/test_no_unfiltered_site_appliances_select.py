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

So this gate uses a RATCHET pattern. BASELINE_MAX = 0 as of
2026-05-15 (Session 220 close-out): every `FROM site_appliances`
in the backend either filters `deleted_at IS NULL` near the query
or carries an explicit `# noqa: site-appliances-deleted-include`
marker with rationale. Each removal requires lowering BASELINE_MAX
in lockstep (same pattern as test_no_same_origin_credentials.py
and test_no_direct_site_id_update.py).

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
# 2026-05-06 mesh-runbook adversarial audit (Maya 2nd-eye finding):
# `mesh_consistency_check_loop()` in background_tasks.py was missing
# `deleted_at IS NULL` on both the per-site rollup query AND the
# ring-agreement query. Soft-deleted appliances were being counted
# in the mesh "total" → false ring-drift alerts. 85 → 83.
# 2026-05-11 Session 219 Commit 2 zero-auth hardening filtered 2
# provisioning.py queries (status UPDATE + heartbeat MAC lookup) +
# added noqa to cross-site forensic 403 audit lookup. 83 → 81.
# 2026-05-15 Task #74 follow-up operator/forensic marker sweep:
# db_delete_safety_check.py (5 inline `-- noqa:` SQL-string markers +
# 1 docstring reword), prometheus_metrics.py (5 operator-metric
# markers across fleet-checkin / mesh-health / mesh-drift / mesh-
# coverage gauges), retention_verifier.py (retention-window walk),
# chain_tamper_detector.py (forensic site walk), audit_package.py
# (auditor-kit pubkey map MUST include decommissioned appliances
# for historical evidence verifiability), ops_health.py (operator-
# rollup docstring reword + marker), fleet_updates.py (admin
# fleet-order target/dead-letter rollups + 2 docstring rewords).
# Also extended _NOQA_PATTERN to accept SQL-string `-- noqa:` style
# in addition to Python `# noqa:` (same convention as
# test_compliance_status_not_read.py). 81 → 56.
# 2026-05-15 (continued, same session) — pushed further to 41:
#   - evidence_chain.py 4→0 (bundle-signature verify, legacy migration
#     fallback, 2 auditor-kit chain reads — all "include all by design"
#     for historical-bundle verifiability).
#   - provisioning.py 3→0 (admin-recovery rekey + appliance-config fetch
#     + the 3rd was already marked at :1007 but outside window —
#     bumped `_DELETED_AT_WINDOW_LINES` 6 → 8 so 8-line-back search
#     catches the original marker).
#   - agent_api.py 2→0 (canonical-appliance incident fallback + display-
#     name iteration; both "include all" classes).
#   - 6 singletons: flywheel_state, mesh_targets (docstring reword),
#     frameworks, cve_watch (status='online' already excludes
#     soft-deleted), db_queries, protection_profiles. 56 → 41.
# Remaining 41 hits clustered in: sites.py:23 / routes.py:13 /
# health_monitor.py:5. These need careful per-callsite classification
# (customer-facing endpoints + operator-alert noise-suppression
# behavioral change) — deferred to a dedicated drive-down session.
# 2026-05-15 (continued) — finished health_monitor + routes.py:
#   - health_monitor.py 5→0 — added `AND sa.deleted_at IS NULL`
#     real filter to all 4 notification scans (offline-warning,
#     offline-critical, auth-failure escalation, mesh online-count
#     rollup, mesh per-site fetch). BEHAVIORAL CHANGE: stops
#     alerting on soft-deleted appliances; operator already chose
#     to delete them so alerts are noise.
#   - routes.py 13→0 — 6 real filter adds (customer-facing list
#     endpoints + admin "stop appliances" fleet-order + site-detail
#     site_id-scoped views) + 7 markers (admin SLA-strip / VPN
#     fleet status / fleet rollup / install-funnel forensic /
#     site-detail full view / bulk-update path).
# 41 → 23. Only sites.py remains.
# 2026-05-15 (continued, same session) — sites.py 23 → 0:
#   - 4 real filter adds (admin stale-cleanup count + post-cleanup
#     verify + admin appliance-list-by-site enumeration, sites.py
#     line 816 dynamic-where pattern marker since the where_clauses
#     list adds `sa.deleted_at IS NULL` downstream outside the
#     8-line window).
#   - 18 markers (checkin-handler system paths: MAC overlap, IP
#     overlap, last_checkin probe, MAC-based existing lookup,
#     merge cleanup DELETE, credential-freshness probe, boot-source
#     diff, recovery-detection, display-name lookup, IP-change
#     detection, IP-set extraction, sibling-row lookup, online-
#     enumeration, trigger-flag probe, online-appliance picker,
#     relocate-verify, MAC PK lookup, "stale cleanup" DELETE).
#   - 1 docstring reword (line 4618 mentioning site_appliances in
#     prose).
# 81 → 0. Total Session 220 ratchet drive-down: 100% closure.
# Any future bare-FROM-site_appliances without filter or marker
# fails CI hard.
BASELINE_MAX = 0

_FROM_PATTERN = re.compile(r"\bFROM\s+site_appliances\b", re.IGNORECASE)
_NOQA_PATTERN = re.compile(
    # Accept both Python-style (`# noqa:`) and SQL-string-style
    # (`-- noqa:`) markers. SQL-style is valid here because the noqa
    # often sits inside a triple-quoted SQL string where Python `#`
    # is invalid SQL syntax in strict mode. Same convention as
    # test_compliance_status_not_read.py.
    r"(?:--|#)\s*(?:#\s*)?noqa:\s*site-appliances-deleted-include",
    re.IGNORECASE,
)
_DELETED_AT_WINDOW_LINES = 8  # 2026-05-15: bumped 6 → 8 to accommodate longer SELECT column lists between marker/filter and the FROM (same window-size as test_no_raw_discovered_devices_count.py)


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

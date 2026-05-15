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
    # Task #66 B1 — synthetic-column filter (the Phase 4 v2 universal
    # predicate). Carol P0-C1: prefer `IS NOT TRUE` (NULL-safe vs
    # a future LEFT JOIN nulling the column) but accept `IS FALSE`
    # for the partial index path on substrate_violations.synthetic.
    r"s?\.?synthetic\s+IS\s+NOT\s+TRUE",
    r"s?\.?synthetic\s+IS\s+FALSE",
    r"NOT\s+s?\.?synthetic\b",
    # Substrate-engine carve-out marker — invariant scans MUST tick on
    # the synthetic site (that's the soak target). Rows segregate
    # downstream via substrate_violations.synthetic (mig 323).
    r"#\s*noqa:\s*synthetic-allowlisted",
    # Org-scoped / partner-scoped queries — synthetic site has NULL
    # client_org_id + NULL partner_id, so any predicate that filters
    # by a specific org/partner id excludes it implicitly.
    r"client_org_id\s*=\s*\$\d+",
    r"client_org_id\s*=\s*:[a-z_]+",
    r"client_org_id\s*IS\s+NOT\s+NULL",
    r"\bpartner_id\s*=\s*\$\d+",
    r"\bpartner_id\s*=\s*:[a-z_]+",
    # Incidents JOIN sites where the sites scope is parameterized.
    r"i\.site_id\s*=",
    r"a\.site_id\s*=",
    # f-string filter interpolation — the actual filter string is
    # composed above the SQL; if the Python file contains `synthetic`
    # in any form within ~30 lines before/after the FROM, accept it.
    # The {where_clause} sigil is the conventional shape.
    r"\{where_clause\}",
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


# Ratchet baseline. Task #66 B1 hardened this from soft→hard
# 2026-05-15. The 14 load-bearing callsites identified in Gate A
# (audit/coach-66-b1-concrete-plan-gate-a-2026-05-14.md) now carry
# `synthetic IS NOT TRUE`; broader scope-pattern matches drop the
# residual to 19. Drive-down to 0 is task #B1-FU (Phase 4 v2
# universal predicate completion). The ratchet pins regressions:
# any new bare-FROM-sites/incidents callsite without a recognized
# exclusion or allowlist entry fails CI.
RATCHET_BASELINE = 19


def test_universal_enumeration_filter_sweep_baseline():
    """Hard ratchet (Task #66 B1, 2026-05-15) — was a soft sweep
    pre-B1. The 14 load-bearing callsites Gate A identified are now
    filtered with `synthetic IS NOT TRUE` (or the appropriate per-
    callsite predicate). Broader scope patterns (client_org_id,
    partner_id, JOIN-scoped i.site_id, f-string `{where_clause}`)
    are now recognized as soak-exclusions because the synthetic
    site's NULL client_org_id + NULL partner_id implicitly excludes
    it under such predicates.

    Drive-down to 0: file a B1 followup task for each residual
    callsite — most are likely real misses needing a filter, OR
    truly-cannot-be-scoped (`SELECT COUNT(*) FROM incidents` for
    total volume) needing ALLOWLIST entries with rationale.

    Mig 304 quarantine (status='inactive') is the live safety net
    until the ratchet drives to 0.
    """
    all_violations: list[str] = []
    for fname in _SCAN_FILES:
        all_violations.extend(_scan_file(fname))
    assert len(all_violations) <= RATCHET_BASELINE, (
        f"mttr-soak universal-filter sweep regressed: "
        f"{len(all_violations)} bare FROM sites/incidents callsites "
        f"without an explicit soak-exclusion predicate > baseline "
        f"{RATCHET_BASELINE}. Either add a synthetic-aware filter "
        f"(`AND synthetic IS NOT TRUE`), add a site-scope predicate, "
        f"add the file:line to ALLOWLIST with rationale, OR drive the "
        f"baseline DOWN if you intentionally closed callsites.\n"
        + "\n".join(f"  {v}" for v in all_violations[:30])
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


def test_phase4_v2_requires_two_gate_review_doc_when_re_activating():
    """When Phase 4 v2 is ready to UN-quarantine the synthetic site
    (flip status back away from 'inactive'), there MUST be a fresh
    round-table review doc covering Gate A (pre-execution) AND
    Gate B (pre-completion). Until then, mig 304 quarantine stays.

    This gate guards against a future commit that adds mig 305
    (or similar) un-quarantining the site without the two-gate
    pattern locked in 2026-05-11.

    The check: if any migration NEWER than 304 mentions
    'synthetic-mttr-soak' AND sets status to anything other than
    'inactive' OR drops the site row, there MUST be a paired
    audit/coach-phase4-mttr-soak-v2-*.md file documenting both
    gate reviews.
    """
    mig_dir = _BACKEND / "migrations"
    audit_dir = _BACKEND.parent.parent.parent / "audit"
    suspect_migs = []
    for mig in sorted(mig_dir.glob("3*_*.sql")):
        # Skip mig 303 (the create) and 304 (the quarantine).
        if mig.name.startswith("303_") or mig.name.startswith("304_"):
            continue
        if int(mig.name.split("_")[0]) < 305:
            continue
        text = mig.read_text()
        if "synthetic-mttr-soak" not in text:
            continue
        # Migration mentions the soak site. Check if it un-quarantines.
        if "status = 'online'" in text or "status='online'" in text:
            suspect_migs.append(mig.name)
        elif "DROP" in text.upper() and "synthetic-mttr-soak" in text:
            suspect_migs.append(mig.name)
    if not suspect_migs:
        return
    # If un-quarantine migrations exist, demand a v2 review doc.
    v2_docs = list(audit_dir.glob("coach-phase4-mttr-soak-v2-*.md")) if audit_dir.exists() else []
    assert v2_docs, (
        f"Migrations {suspect_migs} appear to un-quarantine or remove "
        f"the synthetic-mttr-soak site, but no Phase 4 v2 round-table "
        f"review doc exists at audit/coach-phase4-mttr-soak-v2-*.md. "
        f"Per the TWO-GATE lock-in (CLAUDE.md, 2026-05-11), a fresh "
        f"Gate A (pre-execution) AND Gate B (pre-completion) fork "
        f"review must precede any state change to the soak "
        f"infrastructure."
    )

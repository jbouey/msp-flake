"""CI gate: client_portal.py queries filter soft-deleted rows.

RT33 ghost-data fix (2026-05-05). The user-visible portal showed
phantom sites + appliances because:
  - sites query missed `s.status != 'inactive'`
  - site_appliances JOINs missed `sa.deleted_at IS NULL`

Both filters are now added in client_portal.py. This gate ratchets the
fix so a future LEFT-JOIN pattern won't reintroduce the gap.

Carve-outs (intentional):
  - Line 4560 unnest(ip_addresses) for client-device exclusion list:
    soft-deleted appliances' historical IPs MUST stay in the exclusion
    set to avoid resurrecting them as "client devices."
"""
from __future__ import annotations

import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_CLIENT_PORTAL = _BACKEND / "client_portal.py"
_PARTNERS = _BACKEND / "partners.py"

# Both portal-facing modules must filter soft-deleted appliances. Maya
# 2nd-eye review of RT33 P1 (2026-05-05) found that partners.py:1079
# had the same defect — missing `sa.deleted_at IS NULL` on the LEFT
# JOIN. Gate extended to scan both files.
_PORTAL_FILES = [_CLIENT_PORTAL, _PARTNERS]

# Lines explicitly carved out — by line-anchored content, not number,
# so additions above don't shift the carve-out target.
CARVED_OUT_PATTERN_FRAGMENTS = (
    "unnest(ip_addresses::text[]) as ip FROM site_appliances",
)


def test_portal_files_site_appliance_joins_filter_soft_deletes():
    """Every JOIN/LEFT JOIN on site_appliances in either portal-facing
    file must include `deleted_at IS NULL`. The JOIN line itself MUST
    carry the filter — Maya's anchor-on-JOIN guidance from RT33 review
    so a SELECT-clause reorder can't silently move us off the gate."""
    bad = []
    for portal_file in _PORTAL_FILES:
        for i, line in enumerate(portal_file.read_text().splitlines(), 1):
            if re.search(r"\b(LEFT\s+)?JOIN\s+site_appliances\b", line, re.IGNORECASE):
                if any(frag in line for frag in CARVED_OUT_PATTERN_FRAGMENTS):
                    continue
                if "deleted_at IS NULL" in line:
                    continue
                bad.append(f"{portal_file.name}:{i}: {line.strip()}")
    assert not bad, (
        "JOIN site_appliances missing `deleted_at IS NULL` filter — "
        "RT33 ghost-data class. Either add the filter or extend "
        "CARVED_OUT_PATTERN_FRAGMENTS with explicit justification.\n\n"
        + "\n".join(f"  - {b}" for b in bad)
    )


def test_portal_files_standalone_site_appliances_select_filters_soft_deletes():
    """Bare `FROM site_appliances\\nWHERE …` blocks in either portal
    file must filter soft-deletes (modulo the carved-out unnest line
    that intentionally retains historical IPs for client-device
    exclusion)."""
    bad = []
    for portal_file in _PORTAL_FILES:
        src = portal_file.read_text()
        for match in re.finditer(
            r"FROM\s+site_appliances\b(?!\s+\w+\s+ON)\s+WHERE\b([^;]{0,300}?)(?:ORDER|LIMIT|GROUP|\)|$)",
            src,
            re.IGNORECASE | re.DOTALL,
        ):
            block = match.group(0)
            start = max(0, match.start() - 200)
            context = src[start: match.end()]
            if any(frag in context for frag in CARVED_OUT_PATTERN_FRAGMENTS):
                continue
            if "deleted_at IS NULL" in block:
                continue
            bad.append(f"{portal_file.name}: {re.sub(r'\\s+', ' ', block)[:180]}")
    assert not bad, (
        "`FROM site_appliances WHERE …` (no JOIN) query missing "
        "`deleted_at IS NULL` filter — RT33 ghost-data class.\n\n"
        + "\n".join(f"  - {b}" for b in bad)
    )


# The two ghost-prone site-list endpoints we explicitly fixed in RT33.
# Pinning by anchor strings rather than line numbers so additions above
# don't shift the target.
SITE_LIST_QUERY_ANCHORS = (
    # /api/client/sites — list_sites endpoint
    "LEFT JOIN site_go_agent_summaries gas ON gas.site_id = s.site_id",
    # /api/client/install-instructions
    "sa.appliance_id, sa.hostname as appliance_hostname",
)


def test_client_portal_site_list_endpoints_filter_inactive_status():
    """The two site-list-shaped endpoints fixed in RT33 must keep their
    `s.status != 'inactive'` filter on the WHERE clause. Anchor strings
    pin which endpoints we mean (carve-out from the universal rule —
    many other `s.client_org_id` clauses are lookups, not list views)."""
    src = _CLIENT_PORTAL.read_text()
    bad = []
    for anchor in SITE_LIST_QUERY_ANCHORS:
        idx = src.find(anchor)
        assert idx != -1, (
            f"Site-list anchor `{anchor[:60]}…` not found — test "
            f"scaffolding is stale. Re-anchor or remove from list."
        )
        # Look 1500 chars around the anchor for the WHERE clause.
        window = src[max(0, idx - 800): idx + 800]
        m = re.search(
            r"WHERE\s+s\.client_org_id\s*=\s*\$1[^\n]{0,200}",
            window,
        )
        assert m, (
            f"Anchor `{anchor[:60]}…` matched but WHERE clause not "
            f"found in surrounding window. Re-anchor or update window."
        )
        if "status != 'inactive'" not in m.group(0):
            bad.append(f"{anchor[:60]}… → {m.group(0)}")
    assert not bad, (
        "Client-portal site-list endpoint missing "
        "`s.status != 'inactive'` filter — RT33 ghost-data class. "
        "Soft-deleted sites would surface in /api/client/sites or "
        "/api/client/install-instructions.\n\n"
        + "\n".join(f"  - {b}" for b in bad)
    )

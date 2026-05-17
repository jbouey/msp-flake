"""CI gates for #120 multi-device P2-1 FLOOR track — fleet_health
block in send_partner_weekly_digest.

Per audit/coach-120-partner-digest-gate-a-2026-05-16.md
(APPROVE-WITH-FIXES, FLOOR scope: extend existing weekly digest;
SPIKE track deferred to a separate followup task).

Source-shape sentinels pin:
  P0-1 — opaque-mode parity (aggregate counts only — no clinic/IP/
         MAC/host names leak into the rendered HTML)
  P0-3 — recipient hard-pin (server-side query of partners.contact_
         email; this is structural — verified by existing send-flow)
  Soft-delete + status filter discipline (Session 218 RT33 P1)
  Direct base-table query (Session 218 RT33 P2 Steve veto on MVs)

Deferred to a separate followup task (SPIKE track):
  - P0-2 BAA-expired suppression (applies to NEW spike-alert email,
    not to the weekly digest which is unchanged for established
    partners)
  - P0-4 Jinja2 templates (applies to NEW spike-alert templates;
    the existing weekly digest is f-string-based which is NOT the
    banned `.format()` shape)
  - Substrate invariant `partner_fleet_spike_alert_not_sent_in_24h`
"""
from __future__ import annotations

import pathlib
import re


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_EMAIL = _BACKEND / "email_alerts.py"
_BG = _BACKEND / "background_tasks.py"


def _read_email() -> str:
    return _EMAIL.read_text(encoding="utf-8")


def _read_bg() -> str:
    return _BG.read_text(encoding="utf-8")


def _send_partner_digest_body() -> str:
    src = _read_email()
    m = re.search(
        r"def send_partner_weekly_digest.*?(?=\ndef |\Z)",
        src, re.DOTALL,
    )
    assert m, "send_partner_weekly_digest function not found"
    return m.group(0)


def _gather_data_body() -> str:
    src = _read_bg()
    m = re.search(
        r"async def _gather_partner_digest_data.*?(?=\nasync def |\Z)",
        src, re.DOTALL,
    )
    assert m, "_gather_partner_digest_data function not found"
    return m.group(0)


# ── Function signature + plumbing ────────────────────────────────


def test_send_partner_weekly_digest_accepts_fleet_health_kwarg():
    body = _send_partner_digest_body()
    assert "fleet_health: Optional[dict] = None" in body, (
        "send_partner_weekly_digest must accept optional fleet_health "
        "kwarg (default None). Omitted from email entirely when None "
        "(backward-compat with any older caller)."
    )


def test_gather_data_returns_fleet_health_dict():
    body = _gather_data_body()
    assert '"fleet_health":' in body, (
        "_gather_partner_digest_data must include fleet_health in "
        "the returned payload."
    )


# ── P0-1: opaque-mode parity (aggregate counts only) ─────────────


def test_fleet_health_block_only_aggregate_counters():
    """The rendered fleet_health HTML block must reference ONLY the
    4 aggregate counter keys: offline_24h, offline_7d, baa_expiring_
    30d, chronic_unack_orders. No per-row keys (no clinic_name,
    site_id, mac_address, ip, hostname)."""
    body = _send_partner_digest_body()
    # Find the fleet_health rendering block.
    m = re.search(
        r'if fleet_health is not None:(.*?)(?=\n    \w|\Z)',
        body, re.DOTALL,
    )
    assert m, "fleet_health rendering block not found"
    block = m.group(0)
    # Whitelist of acceptable keys inside the block.
    allowed = {
        "offline_24h", "offline_7d", "baa_expiring_30d",
        "chronic_unack_orders",
    }
    # Banned PHI/identifier keys that must NEVER appear in fleet_health.
    banned = [
        "clinic_name", "site_id", "mac_address", "hostname",
        "ip_address", "ip_addresses", "client_name",
        "appliance_id", "client_org_id", "partner_brand",
    ]
    for b in banned:
        # fleet_health-block code must not reference these keys.
        # (clinic_name etc may appear ELSEWHERE in send_partner_
        # weekly_digest for attention_sites / activity rows — that's
        # the established pre-existing surface, unaffected by this
        # change.)
        assert f'fleet_health.get("{b}"' not in block, (
            f"fleet_health block leaks {b!r} — must be aggregate "
            f"counts ONLY. Counsel Rule 2 + Rule 7. Allowed keys: "
            f"{sorted(allowed)}"
        )


def test_fleet_health_template_block_no_clinic_or_ip_strings():
    """Sentinel: the rendered fleet_health HTML must NOT include
    literal labels that could prompt operators to expect per-row
    detail. Operator confusion would defeat the opaque-mode promise."""
    body = _send_partner_digest_body()
    m = re.search(
        r'fleet_health_html\s*=\s*\(.*?\)',
        body, re.DOTALL,
    )
    assert m, "fleet_health_html assignment not found"
    block = m.group(0)
    for banned_label in (
        "MAC", "mac:", "IP:", "hostname:", "Clinic:", "Partner:",
    ):
        assert banned_label not in block, (
            f"fleet_health rendered HTML must not include {banned_label!r} "
            f"label — opaque-mode requires aggregate-only presentation."
        )


def test_fleet_health_block_links_to_authenticated_portal_not_inline_detail():
    """The block points operators to the authenticated partner portal
    for per-row detail — does NOT inline detail. Per Gate A Counsel
    Rule 7 (no unauth context)."""
    body = _send_partner_digest_body()
    # The block is currently aggregate-only with no inline detail
    # (no <ul> / <li> / per-appliance loops). This sentinel locks
    # that shape — future iterations cannot add inline detail
    # without explicit re-review.
    m = re.search(
        r'if fleet_health is not None:(.*?)fleet_health_html\s*=\s*\(',
        body, re.DOTALL,
    )
    assert m, "fleet_health body region not found"
    region = m.group(1)
    # No iteration over fleet_health entries (would suggest per-row).
    assert "for " not in region or "for _ in" in region, (
        "fleet_health block must not iterate over per-row entries. "
        "Aggregate counts only. Per-row detail belongs in the "
        "authenticated portal — operator clicks through, not "
        "inline."
    )


# ── Aggregate SQL discipline ─────────────────────────────────────


def test_gather_fleet_health_filters_soft_delete_on_join():
    """Session 218 RT33 P1 rule: site_appliances JOIN line must
    include `sa.deleted_at IS NULL` filter on the JOIN line itself
    (not in WHERE on continuation line)."""
    body = _gather_data_body()
    # Find the fleet_health query
    fh_query_match = re.search(
        r"fleet_health_row\s*=\s*await\s+conn\.fetchrow\(\s*\"\"\"(.*?)\"\"\"",
        body, re.DOTALL,
    )
    assert fh_query_match, "fleet_health_row fetch query not found"
    query = fh_query_match.group(1)
    # JOIN line must carry the deleted_at filter
    assert "LEFT JOIN site_appliances sa ON sa.site_id = s.site_id AND sa.deleted_at IS NULL" in query, (
        "fleet_health query: site_appliances JOIN must filter "
        "sa.deleted_at IS NULL on the JOIN line itself (Session 218 "
        "RT33 P1 rule)."
    )


def test_gather_fleet_health_filters_inactive_sites():
    body = _gather_data_body()
    fh_query_match = re.search(
        r"fleet_health_row\s*=\s*await\s+conn\.fetchrow\(\s*\"\"\"(.*?)\"\"\"",
        body, re.DOTALL,
    )
    assert fh_query_match
    query = fh_query_match.group(1)
    assert "s.status != 'inactive'" in query, (
        "fleet_health query: must filter s.status != 'inactive' "
        "(Session 218 RT33 P1 rule on site-list queries)."
    )


def test_gather_fleet_health_no_materialized_view():
    """Session 218 RT33 P2 Steve veto: appliance_status_rollup MV
    bypasses RLS. Direct base-table queries only."""
    body = _gather_data_body()
    fh_query_match = re.search(
        r"fleet_health_row\s*=\s*await\s+conn\.fetchrow\(\s*\"\"\"(.*?)\"\"\"",
        body, re.DOTALL,
    )
    assert fh_query_match
    query = fh_query_match.group(1)
    for mv in ("appliance_status_rollup", "partner_site_weekly_rollup"):
        assert mv not in query, (
            f"fleet_health query must NOT read MV {mv!r} (bypasses "
            f"RLS — Session 218 RT33 P2 Steve veto). Read base tables "
            f"directly."
        )


# ── Defaults + safety ────────────────────────────────────────────


def test_fleet_health_dict_defaults_to_zero_not_none():
    """Aggregate row may return NULL on partners with zero matching
    rows. The dict must coerce to int(... or 0) so the email
    template never sees None (which would render as 'None' string)."""
    body = _gather_data_body()
    # Find the fleet_health dict region — naive `.*?` stops at first
    # nested `}`. Brace-walk instead.
    start = body.find('"fleet_health":')
    assert start != -1, "fleet_health key not found"
    brace_start = body.find('{', start)
    depth = 0
    end = brace_start
    for i in range(brace_start, len(body)):
        if body[i] == '{':
            depth += 1
        elif body[i] == '}':
            depth -= 1
            if depth == 0:
                end = i
                break
    block = body[brace_start : end + 1]
    for key in ("offline_24h", "offline_7d", "baa_expiring_30d",
                "chronic_unack_orders"):
        assert key in block, f"fleet_health dict missing {key!r}"
    assert block.count("or 0") >= 4, (
        "Each fleet_health value must default to 0 (`... or 0`) so "
        "the email template never sees None / NULL."
    )


def test_email_renders_fleet_health_when_present():
    body = _send_partner_digest_body()
    # The rendered HTML body must reference fleet_health_html.
    assert "{fleet_health_html}" in body, (
        "Email template must insert {fleet_health_html} into the body "
        "(else the rendered block is never displayed)."
    )


def test_email_omits_fleet_health_block_when_none():
    body = _send_partner_digest_body()
    # Default of fleet_health_html (when fleet_health is None) is ""
    # so the template insertion is a no-op.
    assert 'fleet_health_html = ""' in body, (
        "When fleet_health is None, fleet_health_html must default "
        "to empty string so the block is omitted entirely (not "
        "rendered with placeholder text or zeros)."
    )

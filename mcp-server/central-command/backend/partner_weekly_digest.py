"""P-F7 — Partner Technician Weekly Digest PDF generator.

Lisa-the-technician's customer-round-table finding (2026-05-08):
"WeeklyRollup is fine on screen, useless on paper. Greg asks
'what'd you do this week' on Monday — I want a one-click
Technician Activity Digest: alerts triaged, orders run (with
attestation IDs), escalations closed, MTTR median, top 3 noisy
sites."

Internal artifact — NOT forwarded to insurance carriers,
auditors, or boards. Operational metrics only. Footer note in
the rendered PDF tells the partner to use the Portfolio
Attestation or per-site Compliance Attestation Letter for
external audiences.

Aggregate-only on top_noisy_sites: site labels are SHA-256
prefixes of site_id (8 hex chars), NOT clinic names — even
this internal artifact stays clean.
"""
from __future__ import annotations

import hashlib
import io
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import asyncpg

try:
    from .templates import render_template
except ImportError:
    from templates import render_template  # type: ignore

logger = logging.getLogger(__name__)


def _human_date(dt: datetime) -> str:
    return dt.strftime("%B %-d, %Y") if dt else ""


def _short_site_label(site_id: str) -> str:
    """Aggregate-only: return a stable opaque label for the site
    so even this internal artifact doesn't render clinic_name in
    a printable doc. Auditors who need the underlying site can
    request it through the operational system; the digest is
    a Lisa+Greg-internal review artifact."""
    h = hashlib.sha256(site_id.encode("utf-8")).hexdigest()[:8]
    return f"site-{h}"


def _format_minutes(minutes: Optional[float]) -> str:
    if minutes is None:
        return "n/a"
    if minutes < 60:
        return f"{int(round(minutes))} min"
    hours = minutes / 60
    if hours < 24:
        return f"{hours:.1f} hr"
    days = hours / 24
    return f"{days:.1f} d"


def _sanitize_partner_text(s: Optional[str], max_len: int = 200) -> str:
    if not s:
        return ""
    out = []
    for ch in s:
        if ord(ch) < 0x20 and ch != "\t":
            continue
        if ch in '<>{}[]\\`|':
            continue
        out.append(ch)
    return "".join(out)[:max_len]


async def _gather_weekly_facts(
    conn: asyncpg.Connection,
    partner_id: str,
    week_start: datetime,
    week_end: datetime,
) -> Dict[str, Any]:
    """Compute the weekly operational facts for the partner."""

    # Orders run — fleet_orders completed in window for partner's sites.
    orders_run = await conn.fetchval(
        """
        SELECT COUNT(*) FROM fleet_orders fo
          JOIN sites s ON s.site_id = fo.target_site_id
         WHERE s.partner_id = $1
           AND fo.status IN ('completed', 'delivered', 'acked')
           AND fo.created_at >= $2 AND fo.created_at < $3
        """,
        partner_id, week_start, week_end,
    ) or 0

    # Alerts triaged — incidents created in the window for the
    # partner's sites.
    alerts_triaged = await conn.fetchval(
        """
        SELECT COUNT(*) FROM incidents i
          JOIN sites s ON s.site_id = i.site_id
         WHERE s.partner_id = $1
           AND i.created_at >= $2 AND i.created_at < $3
        """,
        partner_id, week_start, week_end,
    ) or 0

    # Escalations closed — incidents resolved with resolution-tier L3
    # (human escalation) and a resolved_at within window.
    escalations_closed = await conn.fetchval(
        """
        SELECT COUNT(*) FROM incidents i
          JOIN sites s ON s.site_id = i.site_id
         WHERE s.partner_id = $1
           AND COALESCE(i.resolution_tier, '') = 'L3'
           AND i.resolved_at IS NOT NULL
           AND i.resolved_at >= $2 AND i.resolved_at < $3
        """,
        partner_id, week_start, week_end,
    ) or 0

    # Median MTTR (in minutes) for incidents resolved in the window.
    mttr_median_minutes = await conn.fetchval(
        """
        SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY
                EXTRACT(EPOCH FROM (i.resolved_at - i.created_at)) / 60.0
            )
          FROM incidents i
          JOIN sites s ON s.site_id = i.site_id
         WHERE s.partner_id = $1
           AND i.resolved_at IS NOT NULL
           AND i.resolved_at >= $2 AND i.resolved_at < $3
        """,
        partner_id, week_start, week_end,
    )

    # Top 3 noisy sites — incident counts in window, with auto-resolved
    # break-down (resolution_tier L1).
    noisy_rows = await conn.fetch(
        """
        SELECT i.site_id,
               COUNT(*) AS incident_count,
               COUNT(*) FILTER (
                   WHERE COALESCE(i.resolution_tier, '') = 'L1'
                     AND i.resolved_at IS NOT NULL
               ) AS auto_resolved_count
          FROM incidents i
          JOIN sites s ON s.site_id = i.site_id
         WHERE s.partner_id = $1
           AND i.created_at >= $2 AND i.created_at < $3
         GROUP BY i.site_id
         ORDER BY incident_count DESC
         LIMIT 3
        """,
        partner_id, week_start, week_end,
    )
    top_noisy_sites = [
        {
            "label": _short_site_label(r["site_id"]),
            "incident_count": int(r["incident_count"]),
            "auto_resolved_count": int(r["auto_resolved_count"]),
        }
        for r in noisy_rows
    ]

    return {
        "orders_run": int(orders_run),
        "alerts_triaged": int(alerts_triaged),
        "escalations_closed": int(escalations_closed),
        "mttr_median_minutes": (
            float(mttr_median_minutes)
            if mttr_median_minutes is not None
            else None
        ),
        "top_noisy_sites": top_noisy_sites,
    }


async def render_weekly_digest(
    conn: asyncpg.Connection,
    partner_id: str,
    technician_name: str = "your team",
    week_days: int = 7,
) -> Dict[str, Any]:
    """Render the digest HTML. Caller wraps html_to_pdf in
    asyncio.to_thread (Steve P1-A pattern)."""
    partner_row = await conn.fetchrow(
        "SELECT id, brand_name FROM partners WHERE id = $1",
        partner_id,
    )
    if not partner_row:
        raise RuntimeError(f"partner {partner_id} not found")

    presenter_brand = (
        _sanitize_partner_text(partner_row["brand_name"])
        or "OsirisCare Partner"
    )
    sanitized_tech = _sanitize_partner_text(technician_name, max_len=80) or "your team"

    now = datetime.now(timezone.utc)
    week_end = now
    week_start = now - timedelta(days=week_days)

    facts = await _gather_weekly_facts(conn, partner_id, week_start, week_end)

    html = render_template(
        "partner_weekly_digest/letter",
        presenter_brand=presenter_brand,
        technician_name=sanitized_tech,
        week_start_human=_human_date(week_start),
        week_end_human=_human_date(week_end),
        orders_run=facts["orders_run"],
        alerts_triaged=facts["alerts_triaged"],
        escalations_closed=facts["escalations_closed"],
        mttr_median_human=_format_minutes(facts["mttr_median_minutes"]),
        top_noisy_sites=facts["top_noisy_sites"],
    )

    logger.info(
        "partner_weekly_digest_rendered",
        extra={
            "partner_id": str(partner_id),
            "orders_run": facts["orders_run"],
            "alerts_triaged": facts["alerts_triaged"],
            "escalations_closed": facts["escalations_closed"],
            "mttr_minutes": facts["mttr_median_minutes"],
        },
    )

    return {
        "html": html,
        "presenter_brand": presenter_brand,
        "week_start": week_start,
        "week_end": week_end,
        "facts": facts,
    }


def html_to_pdf(html: str) -> bytes:
    """WeasyPrint render; caller wraps in asyncio.to_thread."""
    try:
        from weasyprint import HTML
    except ImportError as e:
        raise RuntimeError(f"WeasyPrint unavailable: {e}")
    pdf_buf = io.BytesIO()
    HTML(string=html).write_pdf(pdf_buf)
    pdf_buf.seek(0)
    return pdf_buf.read()

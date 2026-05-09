"""P-F8 — Per-incident Response Timeline PDF.

Lisa-the-MSP-MD's customer-round-table finding (2026-05-08):

  "When a clinic owner calls because something broke at 2am, I
  need to print a 1-page timeline showing exactly what the
  substrate did, when, and how it resolved. Today I dig through
  Sentry + the dashboard + the appliance logs to assemble it.
  Make me a PDF."

Read-only derived report — joins the live ``incidents`` table with
``execution_telemetry`` (drift checks + remediation outcomes) and any
L1/L2 decisions in the incident's time window. NO new state, NO
chain attestation, NO migration: every event already lives in the
authoritative substrate evidence chain; this artifact is a printable
re-projection.

Site identity is exposed only as a hash-prefix label (mirrors P-F7
posture) — the technician knows the site by partner-portal lookup
and the printed copy is a reference for the owner-conversation,
not a public attestation. PHI is scrubbed at the appliance edge
before egress so execution_telemetry rows are PHI-free; this
template inherits that boundary.
"""
from __future__ import annotations

import hashlib
import io
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg

try:
    from .templates import render_template
except ImportError:
    from templates import render_template  # type: ignore

logger = logging.getLogger(__name__)


class IncidentTimelineError(Exception):
    """Precondition violation — incident not found or not owned by
    the calling partner. Mapped to 4xx in the API layer."""


def _human_dt(dt: Optional[datetime]) -> str:
    if not dt:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _human_ttr(start: Optional[datetime], end: Optional[datetime]) -> str:
    if not (start and end):
        return "—"
    delta = (end - start).total_seconds()
    if delta < 0:
        return "—"
    if delta < 60:
        return f"{int(delta)} second{'s' if int(delta) != 1 else ''}"
    if delta < 3600:
        return f"{int(delta / 60)} minute{'s' if int(delta / 60) != 1 else ''}"
    if delta < 86400:
        return f"{delta / 3600:.1f} hours"
    return f"{delta / 86400:.1f} days"


def _short_site_label(site_id: str) -> str:
    """Hash-prefix label — mirrors P-F7 site-label posture. Never
    expose clinic_name or site_id directly in the printed artifact."""
    h = hashlib.sha256(str(site_id).encode("utf-8")).hexdigest()[:6]
    return f"site-{h}"


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


_RESOLUTION_TIER_LABELS = {
    "L1": "L1 deterministic rule",
    "L2": "L2 LLM-planned action",
    "L3": "L3 human escalation",
    None: "—",
    "": "—",
}


async def render_incident_timeline(
    conn: asyncpg.Connection,
    partner_id: str,
    incident_id: str,
) -> Dict[str, Any]:
    """Render the timeline for one incident. Verifies partner
    ownership via ``sites.partner_id``. Raises IncidentTimelineError
    on missing or non-owned incident."""

    # Fetch + ownership check in one query.
    # Schema drift fix (2026-05-09 partner-PDF runtime audit P0):
    # `incidents` has NO top-level `hostname` column — hostname lives
    # in the JSONB `details` payload (CLAUDE.md routes.py pattern:
    # `i.details->>'hostname' AS hostname`). Pre-fix every incident-
    # timeline PDF returned 500 in prod. Caught only at runtime.
    incident_row = await conn.fetchrow(
        """
        SELECT i.id, i.incident_type, i.severity, i.status,
               i.site_id, i.details->>'hostname' AS hostname,
               i.created_at, i.resolved_at, i.resolution_tier
          FROM incidents i
          JOIN sites s ON s.site_id = i.site_id
         WHERE i.id = $1
           AND s.partner_id = $2
           AND COALESCE(s.status, 'active') != 'inactive'
         LIMIT 1
        """,
        incident_id, partner_id,
    )
    if not incident_row:
        raise IncidentTimelineError(
            f"incident {incident_id} not found or not owned by partner"
        )

    site_id = incident_row["site_id"]
    # Coach-ultrathink-sweep D-4 fix-up 2026-05-08: sanitize every
    # string that interpolates into the rendered template. Even
    # though incident_type / severity / status / resolution_tier
    # come from substrate-controlled tables (incidents,
    # execution_telemetry), Maya P0 defense-in-depth: every
    # interpolation passes through `_sanitize_partner_text`. The
    # cost is bounded (60-char-cap on each); the upside is the
    # XSS-class regression class can't reappear via a future
    # column added by a migration that lets user-controlled text
    # leak into these fields.
    incident_type = _sanitize_partner_text(
        incident_row["incident_type"], max_len=60
    ) or "unknown"
    severity = _sanitize_partner_text(incident_row["severity"], max_len=20) or "—"
    status = _sanitize_partner_text(incident_row["status"], max_len=20) or "—"
    created_at = incident_row["created_at"]
    resolved_at = incident_row["resolved_at"]
    resolution_tier = _sanitize_partner_text(
        incident_row["resolution_tier"], max_len=20
    )

    # Fetch presenter brand snapshot.
    partner_row = await conn.fetchrow(
        "SELECT brand_name FROM partners WHERE id = $1", partner_id,
    )
    presenter_brand = (
        _sanitize_partner_text(partner_row["brand_name"] if partner_row else None)
        or "OsirisCare Partner"
    )

    # Build event list: detection, telemetry rows, resolution.
    events: List[Dict[str, Any]] = []
    events.append({
        "ts": created_at,
        "kind": "Detected",
        "description": (
            f"Incident opened: {incident_type} (severity {severity})"
        ),
    })

    # execution_telemetry rows in the incident window — drift checks
    # + runbook executions same site+incident_type. Window bounded by
    # incident lifetime (or NOW() if still open).
    window_end = resolved_at or datetime.now(timezone.utc)
    et_rows = await conn.fetch(
        """
        SELECT created_at, runbook_id, success, status,
               resolution_level, duration_seconds, verification_passed
          FROM execution_telemetry
         WHERE site_id = $1
           AND incident_type = $2
           AND created_at >= $3
           AND created_at <= $4
         ORDER BY created_at ASC
         LIMIT 50
        """,
        site_id, incident_type, created_at, window_end,
    )
    for r in et_rows:
        # Coach D-4 fix-up: sanitize per-row strings before
        # interpolation. runbook_id + status + resolution_level
        # are substrate-controlled today, but the template's
        # autoescape is OFF (templates/__init__.py:73 — the
        # auditor-kit/.sh outputs require it), so EVERY
        # interpolated string must be sanitized at the boundary.
        runbook_id = _sanitize_partner_text(
            r["runbook_id"], max_len=80
        ) or "(no runbook)"
        succ = r.get("success")
        status_label = _sanitize_partner_text(r.get("status"), max_len=20) or ""
        tier = _sanitize_partner_text(r.get("resolution_level"), max_len=20) or ""
        verified = r.get("verification_passed")
        duration = r.get("duration_seconds")
        duration_suffix = (
            f" ({duration:.1f}s)" if duration is not None else ""
        )
        tier_prefix = f"{tier} " if tier else ""
        if succ is True:
            kind = "Remediation"
            verify_suffix = (
                "" if verified is None else
                (" verified" if verified else " — verification did not confirm")
            )
            desc = (
                f"{tier_prefix}runbook {runbook_id} executed "
                f"successfully{verify_suffix}{duration_suffix}."
            )
        elif succ is False:
            kind = "Remediation failed"
            desc = (
                f"{tier_prefix}runbook {runbook_id} reported "
                f"failure (status: {status_label or 'failure'})"
                f"{duration_suffix}."
            )
        else:
            kind = "Telemetry"
            desc = (
                f"{tier_prefix}runbook {runbook_id} reported status: "
                f"{status_label or 'pending'}{duration_suffix}."
            )
        events.append({
            "ts": r["created_at"],
            "kind": kind,
            "description": desc,
        })

    # Resolution event (only if resolved).
    if resolved_at:
        events.append({
            "ts": resolved_at,
            "kind": "Resolved",
            "description": (
                f"Incident closed (tier: "
                f"{_RESOLUTION_TIER_LABELS.get(resolution_tier, resolution_tier or '—')})."
            ),
        })

    # Sort events by timestamp (stable on (ts, kind)).
    events.sort(key=lambda e: (e["ts"] or datetime.min.replace(tzinfo=timezone.utc), e["kind"]))

    # Format for template.
    rendered_events = [
        {
            "timestamp_human": _human_dt(e["ts"]),
            "kind": e["kind"],
            "description": e["description"],
        }
        for e in events
    ]

    incident_id_short = str(incident_id)[:8]
    now = datetime.now(timezone.utc)

    html = render_template(
        "partner_incident_timeline/letter",
        presenter_brand=presenter_brand,
        incident_id_short=incident_id_short,
        incident_type=incident_type,
        severity=severity,
        status=status,
        resolution_tier_label=_RESOLUTION_TIER_LABELS.get(
            resolution_tier, resolution_tier or "—"
        ),
        created_at_human=_human_dt(created_at),
        resolved_at_human=_human_dt(resolved_at) if resolved_at else "—",
        ttr_human=_human_ttr(created_at, resolved_at) if resolved_at else "—",
        site_label=_short_site_label(site_id),
        events=rendered_events,
        generated_at_human=_human_dt(now),
    )

    logger.info(
        "partner_incident_timeline_rendered",
        extra={
            "partner_id": str(partner_id),
            "incident_id": str(incident_id),
            "site_id": str(site_id),
            "event_count": len(rendered_events),
            "telemetry_rows": len(et_rows),
            "incident_type": incident_type,
            "status": status,
        },
    )

    return {
        "html": html,
        "presenter_brand": presenter_brand,
        "incident_id_short": incident_id_short,
        "incident_id": str(incident_id),
        "site_label": _short_site_label(site_id),
        "generated_at": now,
        "event_count": len(rendered_events),
    }


def html_to_pdf(html: str) -> bytes:
    """WeasyPrint render. Caller wraps in asyncio.to_thread."""
    try:
        from weasyprint import HTML
    except ImportError as e:
        raise IncidentTimelineError(f"WeasyPrint unavailable: {e}")
    pdf_buf = io.BytesIO()
    HTML(string=html).write_pdf(pdf_buf)
    pdf_buf.seek(0)
    return pdf_buf.read()

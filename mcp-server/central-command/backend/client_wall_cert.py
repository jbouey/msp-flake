"""F5 — Wall Certificate alternate render of an existing F1
Compliance Attestation Letter (sprint 2026-05-08).

Customer-iterated round-table finding: Maria wants a one-page
landscape certificate to hang on her clinic wall showing the
practice is monitored by an Ed25519-signed compliance substrate.

Architectural posture (NON-NEGOTIABLE):
  - Wall cert is an ALTERNATE RENDER of an existing F1 attestation
    row, NOT a new state machine. The F1 row already persists the
    canonical attestation in `compliance_attestation_letters`
    (mig 288) — Ed25519-signed, hash-chained, white-label-frozen.
  - This module READS one row by attestation_hash within the
    caller's org RLS context and re-renders it through the
    wall_cert/letter Jinja2 template. NO INSERT, NO UPDATE, NO
    DELETE, NO new chain attestation.
  - All identity facts (practice_name, Privacy Officer name + title,
    BAA dated_at, presenter_brand, valid_until, attestation_hash)
    come from the *_snapshot columns on the F1 row, NOT from live
    lookups. Pre-fix, a live lookup would risk presenting a
    re-skinned partner brand on a historical certificate — Diane
    CPA's white-label-survivability contract forbids that.
  - The §164.528 disclaimer copy is a byte-for-byte parity copy
    of the F1 letter's disclaimer paragraph. Pinned by tests.

Auth: org_admin (require_client_admin) — owners + admins. Same
posture as the F1 issuance endpoint plus admin role-gate (admins
need to physically print + frame the certificate; billing-only
users would not).

Rate limit: 10/hr per (org, user) — wall cert is a pure re-render
(no signing, no DB write, no Ed25519 work) so the bucket is
slightly more generous than F1's 5/hr issuance bucket.
"""
from __future__ import annotations

import io
import logging
from datetime import datetime
from typing import Any, Dict, Optional

import asyncpg

try:
    from .templates import render_template
except ImportError:  # pytest path
    from templates import render_template  # type: ignore

logger = logging.getLogger(__name__)


class WallCertError(Exception):
    """Wall certificate could not be rendered. Reason string is
    safe to surface to the customer via 404 / 409."""


def _human_date(dt: datetime) -> str:
    """Format like 'May 6, 2026' — Maria-readable. Mirrors
    client_attestation_letter._human_date byte-for-byte."""
    return dt.strftime("%B %-d, %Y") if dt else ""


async def _gather_ots_pct(
    conn: asyncpg.Connection, client_org_id: str, period_start, period_end
) -> str:
    """Compute the OTS-anchored percentage of compliance_bundles
    in the period. Returns a string like "98" or "—" when no
    bundles exist. Read-only; no chain mutation.

    Why a separate fact (not on the F1 row): the F1 row stores
    bundle_count but not OTS-anchored count (Ed25519 + chain
    integrity is the F1 contract; OTS anchoring is a downstream
    asynchronous fact). The wall cert displays both as a
    progress signal — Maria's contractor wants to see "Bitcoin-
    anchored" on the framed certificate.
    """
    # Coach-ultrathink-sweep D-1 fix-up 2026-05-08: real schema
    # columns are `ots_status` (mig 011) + `ots_anchored_at` (mig 011).
    # The earlier `ots_attestation` reference does not exist —
    # first customer click on the Print Wall Certificate button
    # would have raised UndefinedColumnError → 503. Sibling parity
    # with partner_portfolio_attestation.py which uses the same
    # `ots_status = 'anchored'` predicate.
    row = await conn.fetchrow(
        """
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE ots_status = 'anchored') AS anchored
          FROM compliance_bundles cb
          JOIN sites s ON s.site_id = cb.site_id
         WHERE s.client_org_id = $1
           AND cb.created_at >= $2
           AND cb.created_at <= $3
        """,
        client_org_id, period_start, period_end,
    )
    total = (row["total"] if row else 0) or 0
    anchored = (row["anchored"] if row else 0) or 0
    if total == 0:
        return "—"
    pct = int(round((anchored / total) * 100))
    return str(pct)


async def render_wall_cert(
    conn: asyncpg.Connection,
    client_org_id: str,
    attestation_hash: str,
) -> Dict[str, Any]:
    """Re-render an existing F1 attestation row as a wall
    certificate. Returns {"html": str, "practice_name": str,
    "issued_at": datetime, "attestation_hash": str}.

    Reads the F1 row from `compliance_attestation_letters` within
    the caller's RLS context (org_connection has SET LOCAL
    app.current_org). RLS guarantees the row is owned by the
    caller's org — a hash from another tenant returns no row
    and this function raises WallCertError("not_found").

    Read-only by design. NO INSERT, NO UPDATE, NO DELETE, NO
    new privileged-access chain attestation. Pinned by
    tests/test_client_wall_cert.py::test_render_does_not_persist.
    """
    h = (attestation_hash or "").strip().lower()
    if not h or not all(c in "0123456789abcdef" for c in h):
        raise WallCertError("malformed_hash")
    if len(h) != 64:
        raise WallCertError("malformed_hash_must_be_64_hex_chars")

    row = await conn.fetchrow(
        """
        SELECT
            attestation_hash,
            client_org_id,
            period_start,
            period_end,
            sites_covered_count,
            appliances_count,
            workstations_count,
            bundle_count,
            privacy_officer_name_snapshot     AS privacy_officer_name,
            privacy_officer_title_snapshot    AS privacy_officer_title,
            baa_dated_at,
            baa_practice_name_snapshot        AS baa_practice_name,
            presenter_brand_snapshot          AS presenter_brand,
            presenter_contact_line_snapshot   AS presenter_contact_line,
            issued_at,
            valid_until,
            superseded_by_id
          FROM compliance_attestation_letters
         WHERE attestation_hash = $1
        """,
        h,
    )
    if row is None:
        raise WallCertError("not_found")

    # Resolve practice_name — F1 stores baa_practice_name_snapshot
    # (Diane contract — practice name FROZEN at issue time per BAA
    # signature). client_orgs.name is the live name; for wall-cert
    # display we use the BAA snapshot since that's the legal-document
    # match — same reason F1's letter presents "Issued under BAA
    # dated [...] with [baa_practice_name]".
    practice_name = row["baa_practice_name"] or ""

    # OTS-anchored % is a separate read (see _gather_ots_pct comment).
    ots_pct = await _gather_ots_pct(
        conn, client_org_id, row["period_start"], row["period_end"]
    )

    # Verify constants (mirror F1).
    try:
        from .client_attestation_letter import (
            VERIFY_PHONE, VERIFY_URL_SHORT,
        )
    except ImportError:
        from client_attestation_letter import (  # type: ignore
            VERIFY_PHONE, VERIFY_URL_SHORT,
        )

    html = render_template(
        "wall_cert/letter",
        practice_name=practice_name,
        period_start_human=_human_date(row["period_start"]),
        period_end_human=_human_date(row["period_end"]),
        sites_covered_count=row["sites_covered_count"],
        appliances_count=row["appliances_count"],
        workstations_count=row["workstations_count"],
        bundle_count=row["bundle_count"],
        ots_anchored_pct_str=ots_pct,
        privacy_officer_name=row["privacy_officer_name"],
        privacy_officer_title=row["privacy_officer_title"],
        baa_dated_at_human=_human_date(row["baa_dated_at"]),
        baa_practice_name=row["baa_practice_name"],
        presenter_brand=row["presenter_brand"] or "OsirisCare",
        presenter_contact_line=row["presenter_contact_line"] or "",
        issued_at_human=_human_date(row["issued_at"]),
        valid_until_human=_human_date(row["valid_until"]),
        attestation_hash=row["attestation_hash"],
        verify_phone=VERIFY_PHONE,
        verify_url_short=VERIFY_URL_SHORT,
    )

    logger.info(
        "wall_cert_rendered",
        extra={
            "client_org_id": str(client_org_id),
            "attestation_hash": row["attestation_hash"],
            "issued_at": row["issued_at"].isoformat() if row["issued_at"] else None,
            "is_superseded": bool(row["superseded_by_id"] is not None),
        },
    )

    return {
        "html": html,
        "practice_name": practice_name,
        "issued_at": row["issued_at"],
        "attestation_hash": row["attestation_hash"],
    }


def html_to_pdf(html: str) -> bytes:
    """Render the HTML through WeasyPrint. Lazy import — keeps the
    module loadable on dev boxes without WeasyPrint's system deps.

    Mirrors client_attestation_letter.html_to_pdf so the wall cert
    inherits the same WeasyPrint behavior + same import-error
    handling.
    """
    try:
        from weasyprint import HTML
    except ImportError as e:
        raise WallCertError(
            f"WeasyPrint unavailable: {e}. The wall certificate "
            f"cannot be rendered without WeasyPrint installed "
            f"(production has it; tests use the HTML body directly)."
        )
    pdf_buf = io.BytesIO()
    HTML(string=html).write_pdf(pdf_buf)
    pdf_buf.seek(0)
    return pdf_buf.read()


__all__ = [
    "WallCertError",
    "render_wall_cert",
    "html_to_pdf",
]

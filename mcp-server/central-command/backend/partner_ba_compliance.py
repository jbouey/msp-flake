"""P-F6 — Partner BAA roster + BA Compliance Attestation.

Tony-the-MSP-HIPAA-lead's customer-round-table finding (2026-05-08):

> "The three-party BAA chain is invisible. I am a Business Associate
> to 14 covered entities and a downstream of OsirisCare. My auditor
> will ask: (a) show me the OsirisCare→MSP subcontractor BAA, (b)
> show me each MSP→clinic BAA executed and current, (c) show me
> evidence I'm performing my BA obligations."

This module provides:
  - add_baa_to_roster(...) — record a per-clinic BAA. Writes a chain-
    anchored Ed25519 attestation
    (``partner_baa_roster_added``).
  - revoke_baa_from_roster(...) — soft-revoke. Writes
    ``partner_baa_roster_revoked``.
  - list_active_roster(...) — read-side for the attestation render +
    UI.
  - issue_ba_compliance_attestation(...) — generates a Letter PDF
    that lists the roster, cross-references monitored sites, and
    cites the OsirisCare→MSP subcontractor BAA from
    partner_agreements.

Anchor namespace: ``partner_org:<partner_id>`` synthetic site_id
per Session 216 anchor convention.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

try:
    from .templates import render_template
    from .privileged_access_attestation import (
        create_privileged_access_attestation,
    )
except ImportError:
    from templates import render_template  # type: ignore
    from privileged_access_attestation import (  # type: ignore
        create_privileged_access_attestation,
    )

logger = logging.getLogger(__name__)


DEFAULT_VALIDITY_DAYS = 90
VERIFY_PHONE = "1-800-OSIRIS-1"
VERIFY_URL_SHORT = "osiriscare.io/verify/ba-attestation"


class BAComplianceError(Exception):
    """Precondition violation. Mapped to 4xx in API layer."""


def _human_date(dt: Optional[datetime]) -> str:
    if not dt:
        return ""
    return dt.strftime("%B %-d, %Y")


def _canonical_attestation_payload(facts: Dict[str, Any]) -> str:
    return json.dumps(facts, sort_keys=True, separators=(",", ":"))


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


def _sign_attestation(canonical: str) -> Tuple[str, str]:
    """Coach retroactive sweep 2026-05-08 — convergence with F1 + P-F5
    signing posture. Returns (sha256_hex, ed25519_signature_hex)."""
    try:
        try:
            from .signing_backend import get_signing_backend
        except ImportError:
            from signing_backend import get_signing_backend  # type: ignore
        signer = get_signing_backend()
    except Exception as e:
        raise BAComplianceError(
            f"Signing backend unavailable: {e}. BA Compliance "
            f"Attestation cannot be issued without an Ed25519 signature."
        )
    h = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    sig_bytes = signer.sign(canonical.encode("utf-8"))
    sig_hex = sig_bytes.hex() if isinstance(sig_bytes, bytes) else str(sig_bytes)
    return h, sig_hex


# ---------------------------------------------------------------- roster CRUD


async def add_baa_to_roster(
    conn: asyncpg.Connection,
    partner_id: str,
    counterparty_org_id: Optional[str],
    counterparty_practice_name: Optional[str],
    executed_at: datetime,
    expiry_at: Optional[datetime],
    scope: str,
    signer_name: str,
    signer_title: str,
    signer_email: Optional[str],
    doc_sha256: Optional[str],
    uploaded_by_user_id: Optional[str],
    uploaded_by_email: str,
) -> Dict[str, Any]:
    """Record a per-clinic BAA on the partner's roster. Writes a
    chain-anchored Ed25519 attestation (``partner_baa_roster_added``).

    Exactly one of `counterparty_org_id` / `counterparty_practice_name`
    must be provided. Replacement of an active BAA for the same
    counterparty_org_id requires explicit revoke first.
    """
    # Validate exactly-one counterparty
    if bool(counterparty_org_id) == bool(counterparty_practice_name):
        raise BAComplianceError(
            "exactly one of counterparty_org_id or "
            "counterparty_practice_name must be provided"
        )
    if not scope or len(scope.strip()) < 20:
        raise BAComplianceError(
            "scope required (≥20 chars; describe the BAA's "
            "covered services / data scope)"
        )
    if not signer_name or not signer_name.strip():
        raise BAComplianceError("signer_name required")
    if not signer_title or not signer_title.strip():
        raise BAComplianceError("signer_title required")

    scope = scope.strip()
    signer_name = _sanitize_partner_text(signer_name, max_len=120)
    signer_title = _sanitize_partner_text(signer_title, max_len=120)
    signer_email_clean = (
        signer_email.strip().lower() if signer_email else None
    )
    practice_name_clean = (
        _sanitize_partner_text(counterparty_practice_name, max_len=200)
        if counterparty_practice_name
        else None
    )

    async with conn.transaction():
        # If org-keyed: enforce no-active-BAA-already at app layer
        # (DB partial unique idx is the safety net).
        if counterparty_org_id:
            existing = await conn.fetchrow(
                """
                SELECT id FROM partner_baa_roster
                 WHERE partner_id = $1
                   AND counterparty_org_id = $2
                   AND revoked_at IS NULL
                 LIMIT 1
                """,
                partner_id, counterparty_org_id,
            )
            if existing:
                raise BAComplianceError(
                    f"An active BAA already exists for this partner + "
                    f"counterparty (id={existing['id']}). Revoke the "
                    f"existing entry first, then add the replacement."
                )

        new_row = await conn.fetchrow(
            """
            INSERT INTO partner_baa_roster (
                partner_id,
                counterparty_org_id, counterparty_practice_name,
                executed_at, expiry_at, scope, doc_sha256,
                signer_name, signer_title, signer_email,
                uploaded_by_user_id, uploaded_by_email
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12
            )
            RETURNING id, partner_id, counterparty_org_id,
                      counterparty_practice_name, executed_at,
                      expiry_at, scope, signer_name, signer_title
            """,
            partner_id,
            counterparty_org_id, practice_name_clean,
            executed_at, expiry_at, scope, doc_sha256,
            signer_name, signer_title, signer_email_clean,
            uploaded_by_user_id, uploaded_by_email,
        )
        new_id = str(new_row["id"])

        # Chain-anchored Ed25519 attestation. Anchor at synthetic
        # partner_org:<partner_id> namespace (Session 216).
        attestation_reason = (
            f"Partner BAA added to roster: counterparty_id={counterparty_org_id} "
            f"counterparty_name={practice_name_clean!r} signer={signer_name} "
            f"<{signer_email_clean}>; scope: {scope[:120]}"
        )
        attestation = await create_privileged_access_attestation(
            conn=conn,
            site_id=f"partner_org:{partner_id}",
            event_type="partner_baa_roster_added",
            actor_email=uploaded_by_email,
            reason=attestation_reason,
        )
        bundle_id = attestation.get("bundle_id")

        await conn.execute(
            "UPDATE partner_baa_roster "
            "SET attestation_bundle_id = $2::uuid WHERE id = $1",
            new_id, bundle_id,
        )

    logger.info(
        "partner_baa_roster_added",
        extra={
            "partner_id": str(partner_id),
            "roster_id": new_id,
            "counterparty_org_id": str(counterparty_org_id) if counterparty_org_id else None,
            "counterparty_practice_name": practice_name_clean,
            "uploaded_by_email": uploaded_by_email,
            "attestation_bundle_id": str(bundle_id) if bundle_id else None,
        },
    )
    return dict(new_row) | {"attestation_bundle_id": bundle_id}


async def revoke_baa_from_roster(
    conn: asyncpg.Connection,
    partner_id: str,
    roster_id: str,
    revoking_user_id: Optional[str],
    revoking_user_email: str,
    reason: str,
) -> Optional[Dict[str, Any]]:
    """Soft-revoke a roster entry. Idempotent: returns None if the
    roster entry is already revoked or doesn't exist."""
    if not reason or len(reason.strip()) < 20:
        raise BAComplianceError(
            "revocation reason required (≥20 chars)"
        )
    reason = reason.strip()

    async with conn.transaction():
        existing = await conn.fetchrow(
            """
            SELECT id, counterparty_org_id, counterparty_practice_name,
                   signer_name, signer_email, scope
              FROM partner_baa_roster
             WHERE partner_id = $1 AND id = $2 AND revoked_at IS NULL
             LIMIT 1
            """,
            partner_id, roster_id,
        )
        if existing is None:
            return None

        attestation = await create_privileged_access_attestation(
            conn=conn,
            site_id=f"partner_org:{partner_id}",
            event_type="partner_baa_roster_revoked",
            actor_email=revoking_user_email,
            reason=(
                f"Partner BAA revoked: roster_id={roster_id} "
                f"counterparty_id={existing['counterparty_org_id']} "
                f"counterparty_name={existing['counterparty_practice_name']!r}; "
                f"reason: {reason}"
            ),
        )
        bundle_id = attestation.get("bundle_id")

        await conn.execute(
            """
            UPDATE partner_baa_roster
               SET revoked_at = NOW(),
                   revoked_by_user_id = $2,
                   revoked_by_email = $3,
                   revoked_reason = $4,
                   revoked_attestation_bundle_id = $5::uuid
             WHERE id = $1
            """,
            roster_id, revoking_user_id, revoking_user_email,
            reason, bundle_id,
        )

    logger.info(
        "partner_baa_roster_revoked",
        extra={
            "partner_id": str(partner_id),
            "roster_id": str(roster_id),
            "revoking_user_email": revoking_user_email,
            "attestation_bundle_id": str(bundle_id) if bundle_id else None,
        },
    )
    return dict(existing) | {
        "revoked_attestation_bundle_id": bundle_id,
    }


async def list_active_roster(
    conn: asyncpg.Connection, partner_id: str
) -> List[Dict[str, Any]]:
    """Return active (revoked_at IS NULL) BAA roster rows for the
    partner, sorted by executed_at DESC."""
    rows = await conn.fetch(
        """
        SELECT id, counterparty_org_id, counterparty_practice_name,
               executed_at, expiry_at, scope, signer_name, signer_title,
               signer_email, attestation_bundle_id
          FROM partner_baa_roster
         WHERE partner_id = $1 AND revoked_at IS NULL
         ORDER BY executed_at DESC
        """,
        partner_id,
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- attestation render


async def issue_ba_compliance_attestation(
    conn: asyncpg.Connection,
    partner_id: str,
    issued_by_user_id: Optional[str],
    issued_by_email: Optional[str],
    validity_days: int = DEFAULT_VALIDITY_DAYS,
) -> Dict[str, Any]:
    """Render the BA Compliance Attestation Letter HTML.

    Lists the partner's active BAA roster, cross-references
    OsirisCare-monitored sites under each counterparty_org_id,
    and cites the OsirisCare→MSP subcontractor BAA from
    partner_agreements.

    Re-rendered on demand from the live roster + monitored sites
    (no separate "issued attestation" table — the BA Compliance
    Letter changes content as the roster changes; auditor wants
    the current snapshot)."""
    partner_row = await conn.fetchrow(
        "SELECT id, brand_name, support_email, support_phone, status "
        "FROM partners WHERE id = $1",
        partner_id,
    )
    if not partner_row:
        raise BAComplianceError(f"partner {partner_id} not found")
    if partner_row["status"] != "active":
        raise BAComplianceError(
            f"partner status {partner_row['status']!r} — only active "
            f"partners can issue BA Compliance Attestations"
        )

    presenter_brand = (
        _sanitize_partner_text(partner_row["brand_name"])
        or "OsirisCare Partner"
    )
    contact_bits = []
    se = _sanitize_partner_text(partner_row["support_email"], max_len=120)
    sp = _sanitize_partner_text(partner_row["support_phone"], max_len=40)
    if se:
        contact_bits.append(se)
    if sp:
        contact_bits.append(sp)
    presenter_contact_line = (
        " — " + " · ".join(contact_bits) if contact_bits else ""
    )

    # OsirisCare→MSP subcontractor BAA (existing partner_agreements row).
    osiris_baa_dated_at = None
    try:
        agreement_row = await conn.fetchrow(
            """
            SELECT signed_at FROM partner_agreements
             WHERE partner_id = $1 AND agreement_type = 'baa'
               AND status = 'active'
             ORDER BY signed_at DESC LIMIT 1
            """,
            partner_id,
        )
        if agreement_row:
            osiris_baa_dated_at = agreement_row["signed_at"]
    except Exception:
        # partner_agreements may not have these exact columns; the
        # render path tolerates a missing date.
        logger.debug(
            "ba_attestation_subcontractor_baa_lookup_failed",
            exc_info=True,
        )

    roster = await list_active_roster(conn, partner_id)

    # Cross-ref each OsirisCare-onboarded counterparty with monitored
    # sites under that partner.
    rows_for_render = []
    for r in roster:
        org_id = r.get("counterparty_org_id")
        if org_id:
            site_count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM sites
                 WHERE partner_id = $1 AND client_org_id = $2
                   AND COALESCE(status, 'active') != 'inactive'
                """,
                partner_id, org_id,
            ) or 0
            org_row = await conn.fetchrow(
                "SELECT name FROM client_orgs WHERE id = $1", org_id,
            )
            counterparty_label = (
                org_row["name"] if org_row else f"OrgID {org_id}"
            )
        else:
            site_count = 0
            counterparty_label = (
                r.get("counterparty_practice_name") or "(unnamed)"
            )

        rows_for_render.append({
            "counterparty_label": counterparty_label,
            "is_osiris_onboarded": bool(org_id),
            "monitored_site_count": int(site_count),
            "executed_at_human": _human_date(r["executed_at"]),
            "expiry_at_human": (
                _human_date(r["expiry_at"]) if r.get("expiry_at") else "no fixed expiry"
            ),
            "scope": r["scope"],
            "signer_name": r["signer_name"],
            "signer_title": r["signer_title"],
        })

    now = datetime.now(timezone.utc)
    valid_until = now + timedelta(days=validity_days)

    # Canonical hash binding (for verify-by-hash; same shape as
    # F1/P-F5).
    attestation_facts = {
        "kind": "partner_ba_compliance_attestation",
        "version": "1.0",
        "partner_id": str(partner_id),
        "issued_at": now.isoformat(),
        "valid_until": valid_until.isoformat(),
        "presenter_brand": presenter_brand,
        "subcontractor_baa_dated_at": (
            osiris_baa_dated_at.isoformat() if osiris_baa_dated_at else None
        ),
        "roster_count": len(rows_for_render),
        "roster": [
            {
                "counterparty": r["counterparty_label"],
                "is_osiris_onboarded": r["is_osiris_onboarded"],
                "monitored_site_count": r["monitored_site_count"],
                "executed_at": r["executed_at_human"],
                "expiry_at": r["expiry_at_human"],
                "scope": r["scope"],
            }
            for r in rows_for_render
        ],
    }
    canonical = _canonical_attestation_payload(attestation_facts)
    attestation_hash, ed25519_signature = _sign_attestation(canonical)

    # Persist + supersede prior. Steve P1-B partial unique idx is the
    # DB-layer enforcement; concurrent issue races trip
    # idx_pbca_one_active_per_partner rather than producing two
    # non-superseded rows. Mirrors P-F5 mig 289 / partner_portfolio
    # _attestations atomic pattern.
    async with conn.transaction():
        prior = await conn.fetchrow(
            """
            SELECT id FROM partner_ba_compliance_attestations
             WHERE partner_id = $1 AND superseded_by_id IS NULL
             LIMIT 1
            """,
            partner_id,
        )
        new_row = await conn.fetchrow(
            """
            INSERT INTO partner_ba_compliance_attestations (
                partner_id,
                subcontractor_baa_dated_at,
                roster_count,
                total_monitored_sites,
                onboarded_counterparty_count,
                presenter_brand_snapshot,
                support_email_snapshot,
                support_phone_snapshot,
                attestation_hash, ed25519_signature,
                issued_at, valid_until,
                issued_by_user_id, issued_by_email
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14
            )
            RETURNING id, attestation_hash
            """,
            partner_id,
            osiris_baa_dated_at or now,  # NOT NULL — fall back to issue time
            len(rows_for_render),
            sum(r["monitored_site_count"] for r in rows_for_render),
            sum(1 for r in rows_for_render if r["is_osiris_onboarded"]),
            presenter_brand,
            se or "",
            sp or "",
            attestation_hash, ed25519_signature,
            now, valid_until,
            issued_by_user_id, issued_by_email,
        )
        if prior:
            await conn.execute(
                "UPDATE partner_ba_compliance_attestations "
                "SET superseded_by_id = $1 WHERE id = $2",
                new_row["id"], prior["id"],
            )

    html = render_template(
        "partner_ba_compliance/letter",
        presenter_brand=presenter_brand,
        presenter_contact_line=presenter_contact_line,
        issued_at_human=_human_date(now),
        valid_until_human=_human_date(valid_until),
        subcontractor_baa_dated_at_human=(
            _human_date(osiris_baa_dated_at)
            if osiris_baa_dated_at
            else "on file with OsirisCare"
        ),
        roster_count=len(rows_for_render),
        roster=rows_for_render,
        total_monitored_sites=sum(
            r["monitored_site_count"] for r in rows_for_render
        ),
        onboarded_counterparty_count=sum(
            1 for r in rows_for_render if r["is_osiris_onboarded"]
        ),
        attestation_hash=attestation_hash,
        verify_phone=VERIFY_PHONE,
        verify_url_short=VERIFY_URL_SHORT,
    )

    logger.info(
        "partner_ba_compliance_attestation_issued",
        extra={
            "partner_id": str(partner_id),
            "attestation_id": str(new_row["id"]),
            "roster_count": len(rows_for_render),
            "total_monitored_sites": sum(
                r["monitored_site_count"] for r in rows_for_render
            ),
            "attestation_hash": attestation_hash,
            "valid_until": valid_until.isoformat(),
            "issued_by_email": issued_by_email,
        },
    )

    return {
        "attestation_id": str(new_row["id"]),
        "html": html,
        "presenter_brand": presenter_brand,
        "issued_at": now,
        "valid_until": valid_until,
        "attestation_hash": attestation_hash,
        "ed25519_signature": ed25519_signature,
        "facts": attestation_facts,
    }


async def get_ba_attestation_by_hash(
    conn: asyncpg.Connection, attestation_hash: str
) -> Optional[Dict[str, Any]]:
    """Public-verify lookup. Calls SECURITY DEFINER function so RLS
    doesn't block the public read. Mirrors P-F5
    `get_portfolio_by_hash` shape."""
    row = await conn.fetchrow(
        "SELECT * FROM public_verify_partner_ba_attestation($1)",
        attestation_hash,
    )
    return dict(row) if row else None


def html_to_pdf(html: str) -> bytes:
    """WeasyPrint render. Caller wraps in asyncio.to_thread."""
    try:
        from weasyprint import HTML
    except ImportError as e:
        raise BAComplianceError(f"WeasyPrint unavailable: {e}")
    pdf_buf = io.BytesIO()
    HTML(string=html).write_pdf(pdf_buf)
    pdf_buf.seek(0)
    return pdf_buf.read()

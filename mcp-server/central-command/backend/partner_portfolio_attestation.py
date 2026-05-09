"""P-F5 — Partner Portfolio Attestation Letter generator.

Closes Greg-the-MSP-owner's partner-round-table finding (2026-05-08):
"Maria's F1 letter has my logo via presenter_brand — but Maria
forwards that to *her* auditor; I never touch it. I need a
Portfolio Attestation: 'Greg's MSP operates the OsirisCare
substrate across 14 clinics, 23 appliances, 312 monitored
controls, hash-chained, OTS-anchored.' That's a website-trust-
badge gap and a sales-deck gap in one."

Aggregate-only artifact. NO clinic names, NO PHI, NO per-site
detail — counts + chain roots only. Anna-the-sales-lead hands
the public /verify URL to a prospect; the prospect sees the
substrate-grade evidence is real without learning which clinics
are under contract.

Mirrors F1 (client_attestation_letter.py) shape:
  * Ed25519 signature over canonical JSON
  * Atomic supersede-prior + insert-new
  * 90-day default validity
  * presenter snapshots frozen at issue time
  * SECURITY DEFINER public-verify function (no partner_id leak)
  * StrictUndefined Jinja2 render via the template registry
  * asyncio.to_thread WeasyPrint render (event-loop hygiene)

Differences from F1:
  * No Privacy Officer precondition (partners aren't CEs)
  * No BAA-on-file precondition at THIS layer (the partner→OsirisCare
    BAA is in partner_agreements, separate from the per-clinic BAA F1
    requires; precondition is "partner exists & active")
  * Aggregate-only counts; no clinic identity in the artifact
  * chain_root_hex is SHA-256 of concatenated chain heads (latest
    bundle hash per site, sorted by site_id) — auditor can recompute
    independently from per-site auditor-kits
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
except ImportError:
    from templates import render_template  # type: ignore

logger = logging.getLogger(__name__)


DEFAULT_VALIDITY_DAYS = 90
DEFAULT_PERIOD_DAYS = 30
VERIFY_PHONE = "1-800-OSIRIS-1"
VERIFY_URL_SHORT = "osiriscare.io/verify/portfolio"


class UnableToIssuePortfolio(Exception):
    """Precondition violation — portfolio attestation cannot be
    issued. Reason string is safe to surface via 409 Conflict."""


def _human_date(dt: datetime) -> str:
    return dt.strftime("%B %-d, %Y") if dt else ""


def _canonical_attestation_payload(facts: Dict[str, Any]) -> str:
    return json.dumps(facts, sort_keys=True, separators=(",", ":"))


def _sanitize_partner_text(s: Optional[str], max_len: int = 200) -> str:
    """Maya P0 (round-table 2026-05-06) — defense-in-depth against
    Markdown/HTML injection via partner-controlled brand_name."""
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


async def _gather_aggregate_facts(
    conn: asyncpg.Connection,
    partner_id: str,
    period_start: datetime,
    period_end: datetime,
) -> Dict[str, Any]:
    """Compute the partner's aggregate operational facts. Counts
    only; no clinic-identifying detail."""

    # Active sites under this partner. RT33 P1: filter status !=
    # 'inactive'.
    sites = await conn.fetch(
        """
        SELECT site_id FROM sites
         WHERE partner_id = $1 AND COALESCE(status, 'active') != 'inactive'
        """,
        partner_id,
    )
    site_ids = [r["site_id"] for r in sites]
    site_count = len(site_ids)

    if site_count == 0:
        appliance_count = 0
        workstation_count = 0
        bundle_count = 0
        anchored = 0
        chain_heads: List[Tuple[str, str]] = []
    else:
        appliance_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM site_appliances sa
              JOIN sites s ON s.site_id = sa.site_id
             WHERE s.partner_id = $1
               AND sa.deleted_at IS NULL
               AND COALESCE(s.status, 'active') != 'inactive'
            """,
            partner_id,
        ) or 0

        workstation_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM go_agents ga
              JOIN sites s ON s.site_id = ga.site_id
             WHERE s.partner_id = $1
               AND COALESCE(s.status, 'active') != 'inactive'
            """,
            partner_id,
        ) or 0

        # Bundle count + anchored count for the period.
        bundle_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM compliance_bundles cb
              JOIN sites s ON s.site_id = cb.site_id
             WHERE s.partner_id = $1
               AND cb.checked_at >= $2 AND cb.checked_at < $3
            """,
            partner_id, period_start, period_end,
        ) or 0
        anchored = await conn.fetchval(
            """
            SELECT COUNT(*) FROM compliance_bundles cb
              JOIN sites s ON s.site_id = cb.site_id
             WHERE s.partner_id = $1
               AND cb.checked_at >= $2 AND cb.checked_at < $3
               AND cb.ots_status = 'anchored'
            """,
            partner_id, period_start, period_end,
        ) or 0

        # Chain heads — latest bundle hash per site, sorted by
        # site_id for deterministic chain_root_hex.
        head_rows = await conn.fetch(
            """
            SELECT cb.site_id, cb.bundle_hash
              FROM compliance_bundles cb
              JOIN sites s ON s.site_id = cb.site_id
              JOIN (
                  SELECT site_id, MAX(checked_at) AS max_at
                    FROM compliance_bundles
                   WHERE site_id = ANY($1)
                   GROUP BY site_id
              ) latest ON latest.site_id = cb.site_id AND cb.checked_at = latest.max_at
             WHERE s.partner_id = $2
            """,
            site_ids, partner_id,
        )
        chain_heads = sorted(
            [(r["site_id"], r["bundle_hash"]) for r in head_rows],
            key=lambda t: t[0],
        )

    # Control count from check_type_registry — substrate-wide
    # constant, NOT partner-specific. Operationally what's
    # monitored across the fleet.
    # Schema drift fix (2026-05-09 partner-PDF runtime audit P0):
    # column was renamed `monitoring_only` → `is_monitoring_only` in
    # mig 157 (`check_type_registry`). Pre-fix every portfolio-
    # attestation PDF returned 500 in prod. Caught only at runtime —
    # exactly the class the runtime-evidence-required-at-closeout
    # rule was written for.
    control_count = await conn.fetchval(
        """
        SELECT COUNT(*) FROM check_type_registry
         WHERE COALESCE(is_monitoring_only, false) = false
        """,
    ) or 0

    # OTS-anchored percentage. Round to one decimal.
    if bundle_count > 0:
        ots_pct = round((anchored / bundle_count) * 100.0, 1)
    else:
        ots_pct = 0.0

    # chain_root_hex: SHA-256 of canonical concatenation of
    # (site_id|bundle_hash) pairs. Sorted; deterministic.
    if chain_heads:
        chain_root_input = "\n".join(
            f"{sid}|{bh}" for sid, bh in chain_heads
        )
        chain_root_hex = hashlib.sha256(
            chain_root_input.encode("utf-8")
        ).hexdigest()
    else:
        chain_root_hex = hashlib.sha256(b"empty-portfolio").hexdigest()

    return {
        "site_count": int(site_count),
        "appliance_count": int(appliance_count),
        "workstation_count": int(workstation_count),
        "control_count": int(control_count),
        "bundle_count": int(bundle_count),
        "ots_anchored_pct": float(ots_pct),
        "chain_root_hex": chain_root_hex,
        "period_start_iso": period_start.isoformat(),
        "period_end_iso": period_end.isoformat(),
    }


def _sign_attestation(canonical: str) -> Tuple[str, str]:
    try:
        try:
            from .signing_backend import get_signing_backend
        except ImportError:
            from signing_backend import get_signing_backend  # type: ignore
        signer = get_signing_backend()
    except Exception as e:
        raise UnableToIssuePortfolio(
            f"Signing backend unavailable: {e}. Portfolio attestation "
            f"cannot be issued without an Ed25519 signature."
        )
    h = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    sig_bytes = signer.sign(canonical.encode("utf-8"))
    sig_hex = sig_bytes.hex() if isinstance(sig_bytes, bytes) else str(sig_bytes)
    return h, sig_hex


async def issue_portfolio_attestation(
    conn: asyncpg.Connection,
    partner_id: str,
    issued_by_user_id: Optional[str],
    issued_by_email: Optional[str],
    period_days: int = DEFAULT_PERIOD_DAYS,
    validity_days: int = DEFAULT_VALIDITY_DAYS,
) -> Dict[str, Any]:
    """Issue a Partner Portfolio Attestation Letter."""

    # 1. Resolve partner row + sanitize white-label snapshot.
    partner_row = await conn.fetchrow(
        """
        SELECT id, brand_name, support_email, support_phone, status
          FROM partners WHERE id = $1
        """,
        partner_id,
    )
    if not partner_row:
        raise UnableToIssuePortfolio(
            f"partner {partner_id} not found"
        )
    if partner_row["status"] != "active":
        raise UnableToIssuePortfolio(
            f"partner status is {partner_row['status']!r}; "
            f"only active partners can issue portfolio attestations."
        )

    presenter_brand = _sanitize_partner_text(partner_row["brand_name"]) or "OsirisCare Partner"
    sanitized_email = _sanitize_partner_text(partner_row["support_email"], max_len=120)
    sanitized_phone = _sanitize_partner_text(partner_row["support_phone"], max_len=40)
    contact_bits = []
    if sanitized_email:
        contact_bits.append(sanitized_email)
    if sanitized_phone:
        contact_bits.append(sanitized_phone)
    presenter_contact_line = (
        " — " + " · ".join(contact_bits) if contact_bits else ""
    )

    # 2. Period + validity windows.
    now = datetime.now(timezone.utc)
    period_end = now
    period_start = now - timedelta(days=period_days)
    valid_until = now + timedelta(days=validity_days)

    # 3. Aggregate facts.
    facts = await _gather_aggregate_facts(
        conn, partner_id, period_start, period_end,
    )

    # 4. Canonical attestation payload — hash + signature bind to
    #    these facts only.
    attestation_facts = {
        "kind": "partner_portfolio_attestation",
        "version": "1.0",
        "partner_id": str(partner_id),
        "period_start": facts["period_start_iso"],
        "period_end": facts["period_end_iso"],
        "issued_at": now.isoformat(),
        "valid_until": valid_until.isoformat(),
        "site_count": facts["site_count"],
        "appliance_count": facts["appliance_count"],
        "workstation_count": facts["workstation_count"],
        "control_count": facts["control_count"],
        "bundle_count": facts["bundle_count"],
        "ots_anchored_pct": facts["ots_anchored_pct"],
        "chain_root_hex": facts["chain_root_hex"],
        "presenter": {
            "brand": presenter_brand,
            "contact_line": presenter_contact_line,
        },
    }
    canonical = _canonical_attestation_payload(attestation_facts)
    attestation_hash, ed25519_signature = _sign_attestation(canonical)

    # 5. Atomic supersede-prior + insert-new. The partial unique
    #    index `idx_ppa_one_active_per_partner` is the DB-layer
    #    enforcement — if two simultaneous calls race past the
    #    application-layer transaction, the second INSERT trips
    #    UniqueViolationError and the API maps to 409.
    async with conn.transaction():
        prior = await conn.fetchrow(
            """
            SELECT id FROM partner_portfolio_attestations
             WHERE partner_id = $1 AND superseded_by_id IS NULL
             LIMIT 1
            """,
            partner_id,
        )
        new_row = await conn.fetchrow(
            """
            INSERT INTO partner_portfolio_attestations (
                partner_id, period_start, period_end,
                site_count, appliance_count, workstation_count,
                control_count, bundle_count,
                ots_anchored_pct, chain_root_hex,
                presenter_brand_snapshot, support_email_snapshot,
                support_phone_snapshot,
                attestation_hash, ed25519_signature,
                issued_at, valid_until,
                issued_by_user_id, issued_by_email
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                $11, $12, $13, $14, $15, $16, $17, $18, $19
            )
            RETURNING id, attestation_hash
            """,
            partner_id, period_start, period_end,
            facts["site_count"], facts["appliance_count"],
            facts["workstation_count"], facts["control_count"],
            facts["bundle_count"], facts["ots_anchored_pct"],
            facts["chain_root_hex"],
            presenter_brand, sanitized_email, sanitized_phone,
            attestation_hash, ed25519_signature,
            now, valid_until,
            issued_by_user_id, issued_by_email,
        )
        if prior:
            await conn.execute(
                "UPDATE partner_portfolio_attestations "
                "SET superseded_by_id = $1 WHERE id = $2",
                new_row["id"], prior["id"],
            )

    logger.info(
        "partner_portfolio_attestation_issued",
        extra={
            "partner_id": str(partner_id),
            "attestation_id": str(new_row["id"]),
            "attestation_hash": attestation_hash,
            "valid_until": valid_until.isoformat(),
            "site_count": facts["site_count"],
            "ots_pct": facts["ots_anchored_pct"],
            "issued_by_email": issued_by_email,
        },
    )

    # 6. Render HTML through Jinja2 + StrictUndefined.
    html = render_template(
        "partner_portfolio_attestation/letter",
        presenter_brand=presenter_brand,
        presenter_contact_line=presenter_contact_line,
        period_start_human=_human_date(period_start),
        period_end_human=_human_date(period_end),
        site_count=facts["site_count"],
        appliance_count=facts["appliance_count"],
        workstation_count=facts["workstation_count"],
        control_count=facts["control_count"],
        bundle_count=facts["bundle_count"],
        ots_anchored_pct_str=f"{facts['ots_anchored_pct']:.1f}",
        chain_root_hex=facts["chain_root_hex"],
        chain_head_at_human=_human_date(now),
        issued_at_human=_human_date(now),
        valid_until_human=_human_date(valid_until),
        attestation_hash=attestation_hash,
        verify_phone=VERIFY_PHONE,
        verify_url_short=VERIFY_URL_SHORT,
    )

    return {
        "attestation_id": str(new_row["id"]),
        "attestation_hash": attestation_hash,
        "issued_at": now,
        "valid_until": valid_until,
        "html": html,
        "facts": facts,
        "presenter_brand": presenter_brand,
    }


def html_to_pdf(html: str) -> bytes:
    """WeasyPrint render (lazy import). Caller wraps in
    asyncio.to_thread to keep the event loop responsive (Steve P1-A
    pattern from F1)."""
    try:
        from weasyprint import HTML
    except ImportError as e:
        raise UnableToIssuePortfolio(
            f"WeasyPrint unavailable: {e}"
        )
    pdf_buf = io.BytesIO()
    HTML(string=html).write_pdf(pdf_buf)
    pdf_buf.seek(0)
    return pdf_buf.read()


async def get_portfolio_by_hash(
    conn: asyncpg.Connection, attestation_hash: str
) -> Optional[Dict[str, Any]]:
    """Used by F4-pattern public /verify endpoint. Calls the
    SECURITY DEFINER function so RLS doesn't block the public read."""
    row = await conn.fetchrow(
        "SELECT * FROM public_verify_partner_portfolio($1)",
        attestation_hash,
    )
    return dict(row) if row else None

"""F1 — Compliance Attestation Letter generator (round-table 2026-05-06).

Closes Maria's customer-round-table finding: "what do I actually
hand my insurance carrier?" Today the answer is the auditor kit ZIP,
which the carrier can't open. F1 produces a one-page branded PDF
Maria forwards to Brian, her board, etc.

Customer-iterated wording from the prime-customer round-table:
  - Maria: "make ME the accountable human" → named Privacy Officer
    sentence: "[PO Name], as Privacy Officer, reviews the monthly
    evidence summary."
  - Maria: "I can't say §164.504(e) out loud" → plain English first,
    citation second.
  - Brian (insurance agent): "I will not scan QRs from a PDF, that's
    how you get phished" → 1-800 phone number FIRST, public verify URL
    second. QR is removed entirely.
  - Brian: "valid 90 days from issuance" → expiration stamped on
    every letter. /verify endpoint returns is_expired=true past the
    window so a forwarded stale letter is detectable.
  - Diane (CPA): "BAA reference + vendor continuity clause" →
    footer references baa_signatures row + the wind-down-period
    verification commitment (Carol BLOCK-2 downgrade —
    "supported through the term of the relationship plus a
    commercially reasonable wind-down period; the practice
    retains independent copies for §164.530(j) compliance" —
    NOT the prior unbacked 7-year SLA).
  - Diane: "white-label survivability" → presenter_brand +
    presenter_contact_line are FROZEN at issue time. Switching MSPs
    does not retroactively re-skin historical letters.
  - OCR investigator: "continuously monitored is legally aggressive"
    → "monitored on a continuous automated schedule".
  - OCR investigator: kit alone satisfies no §164.308(a)(1)(ii)(D)
    control on its own. Disclaimer copy: "audit-supportive technical
    evidence", not "compliance certified".

LOAD-BEARING PRECONDITIONS:
  1. An ACTIVE Privacy Officer designation must exist
     (privacy_officer_designations.revoked_at IS NULL). Carol contract:
     never print a stale signature.
  2. A BAA-on-file row must exist (baa_signatures). Diane contract:
     "the whole letter is worthless if Maria's disclosing PHI metadata
     to a vendor with no BAA on file."

Both are checked before render; UnableToIssueLetter raised with a
specific reason the API layer maps to 409 Conflict (not 500).
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import asyncpg

try:
    from .templates import render_template
    from .client_privacy_officer import get_current as get_current_po
except ImportError:  # pytest path
    from templates import render_template  # type: ignore
    from client_privacy_officer import get_current as get_current_po  # type: ignore

logger = logging.getLogger(__name__)


# Carol-approved validity window. Brian-the-agent contract: "I need
# to know when to ask for a fresh one at renewal."
DEFAULT_VALIDITY_DAYS = 90

# Carol-approved evidence-period window. Default is the prior 30 days
# (matches the canonical compliance_score window).
DEFAULT_PERIOD_DAYS = 30

# Brian-the-agent contract: phone number FIRST. Public verify URL
# second. No QR (Brian: "that's how you get phished").
VERIFY_PHONE = "1-800-OSIRIS-1"  # placeholder — replace with real number when wired
VERIFY_URL_SHORT = "osiriscare.io/verify"


class UnableToIssueLetter(Exception):
    """Precondition violation — letter cannot be issued. The reason
    string is safe to surface to the customer via 409 Conflict."""


def _human_date(dt: datetime) -> str:
    """Format like 'May 6, 2026' — Maria-readable."""
    return dt.strftime("%B %-d, %Y") if dt else ""


def _canonical_attestation_payload(facts: Dict[str, Any]) -> str:
    """Deterministic JSON for hashing + signing. Matches the
    auditor-kit determinism contract: sort_keys=True, compact
    separators."""
    return json.dumps(facts, sort_keys=True, separators=(",", ":"))


async def _get_current_baa(
    conn: asyncpg.Connection, client_org_id: str
) -> Optional[Dict[str, Any]]:
    """Return the most recent baa_signatures row for the org.

    Task #93 v2 Commit 2 (2026-05-16): cut over from email-join to
    FK-join. baa_signatures.client_org_id (mig 321, NOT NULL FK) is
    now the structural link; the prior `LOWER(c.primary_email) =
    LOWER(s.email)` join was the orphan class — every primary_email
    rename stranded the letter's BAA lookup. SQL aliases (`AS id`,
    `AS signer_email`) preserve the historical dict-key contract this
    module's downstream consumers rely on (`baa["id"]`,
    `baa["signer_email"]`).
    """
    row = await conn.fetchrow(
        """
        SELECT s.signature_id   AS id,
               s.email           AS signer_email,
               s.signer_name,
               s.signed_at,
               c.name            AS practice_name
          FROM baa_signatures s
          JOIN client_orgs c ON c.id = s.client_org_id
         WHERE c.id = $1
         ORDER BY s.signed_at DESC
         LIMIT 1
        """,
        client_org_id,
    )
    return dict(row) if row else None


async def _gather_facts(
    conn: asyncpg.Connection,
    client_org_id: str,
    period_start: datetime,
    period_end: datetime,
) -> Dict[str, Any]:
    """Compute the operational facts that go on the letter.

    Returned dict is consumed by render() AND by the canonical-JSON
    hash. Every field must be deterministic from inputs (no
    wall-clock, no randomness)."""
    org_row = await conn.fetchrow(
        "SELECT name FROM client_orgs WHERE id = $1", client_org_id
    )
    if not org_row:
        raise UnableToIssueLetter(
            f"client_org {client_org_id} not found"
        )
    practice_name = org_row["name"]

    # Sites covered (status != 'inactive' filter per RT33 P1 rule).
    sites_count = await conn.fetchval(
        """
        SELECT COUNT(*) FROM sites
         WHERE client_org_id = $1 AND COALESCE(status, 'active') != 'inactive'
        """,
        client_org_id,
    ) or 0

    # Appliance count — RT33 P1: filter deleted_at IS NULL.
    appliances_count = await conn.fetchval(
        """
        SELECT COUNT(*) FROM site_appliances sa
          JOIN sites s ON s.site_id = sa.site_id
         WHERE s.client_org_id = $1
           AND sa.deleted_at IS NULL
           AND COALESCE(s.status, 'active') != 'inactive'
        """,
        client_org_id,
    ) or 0

    # Workstations — go_agents linked to sites under this org.
    workstations_count = await conn.fetchval(
        """
        SELECT COUNT(*) FROM go_agents ga
          JOIN sites s ON s.site_id = ga.site_id
         WHERE s.client_org_id = $1
           AND COALESCE(s.status, 'active') != 'inactive'
        """,
        client_org_id,
    ) or 0

    # Bundle count for the period — chain-evidence count.
    bundle_count = await conn.fetchval(
        """
        SELECT COUNT(*) FROM compliance_bundles cb
          JOIN sites s ON s.site_id = cb.site_id
         WHERE s.client_org_id = $1
           AND cb.checked_at >= $2 AND cb.checked_at < $3
        """,
        client_org_id, period_start, period_end,
    ) or 0

    # Overall compliance score for the period (canonical helper).
    overall_score: Optional[int] = None
    try:
        try:
            from .compliance_score import compute_compliance_score
        except ImportError:
            from compliance_score import compute_compliance_score  # type: ignore
        sites_in_org = await conn.fetch(
            "SELECT site_id FROM sites WHERE client_org_id = $1 "
            "AND COALESCE(status, 'active') != 'inactive'",
            client_org_id,
        )
        site_ids = [r["site_id"] for r in sites_in_org]
        if site_ids:
            score_result = await compute_compliance_score(
                conn, site_ids, window_days=DEFAULT_PERIOD_DAYS,
            )
            # compute_compliance_score returns ComplianceScore dataclass
            # (compliance_score.py:55-71). Prior code used a dict-shape
            # access guarded by a legacy type check that was always
            # False against the dataclass — sc was always None and every
            # F1 PDF shipped overall_score=None. Counsel Rule 1 (canonical
            # delegation) was nominally followed but the result was
            # silently discarded. Use dataclass attribute access.
            sc = score_result.overall_score
            if sc is not None:
                overall_score = int(round(float(sc)))

            # Counsel Rule 1 runtime sampling — F1 attestation letter is
            # the highest-stakes customer-facing artifact (Ed25519-signed
            # PDF shipped to clinic customers as proof of compliance).
            # 10% stochastic capture for substrate drift verification.
            try:
                try:
                    from .canonical_metrics_sampler import sample_metric_response
                except ImportError:
                    from canonical_metrics_sampler import sample_metric_response  # type: ignore
                await sample_metric_response(
                    conn,
                    metric_class="compliance_score",
                    tenant_id=str(client_org_id),
                    captured_value=(
                        float(overall_score) if overall_score is not None else None
                    ),
                    endpoint_path="f1:attestation_letter",
                    helper_input={
                        "site_ids": site_ids,
                        "window_days": DEFAULT_PERIOD_DAYS,
                        "include_incidents": False,
                    },
                    classification="customer-facing",
                )
            except Exception:
                pass  # sampler is best-effort
    except Exception:
        logger.warning("attestation_letter_score_compute_failed", exc_info=True)
        overall_score = None

    return {
        "practice_name": practice_name,
        "sites_covered_count": int(sites_count),
        "appliances_count": int(appliances_count),
        "workstations_count": int(workstations_count),
        "bundle_count": int(bundle_count),
        "overall_score": overall_score,
        "period_start_iso": period_start.isoformat(),
        "period_end_iso": period_end.isoformat(),
    }


async def _resolve_presenter(
    conn: asyncpg.Connection, client_org_id: str
) -> Tuple[str, Optional[str], str]:
    """Returns (presenter_brand, presenter_partner_id, presenter_contact_line).

    Mirrors the auditor-kit presenter resolution + sanitizes
    partner-controlled text (Maya P0 — defense against Markdown XSS
    via brand_name). The values are FROZEN at letter-issue time
    (Diane white-label-survivability contract) — they snapshot into
    compliance_attestation_letters.presenter_*_snapshot.
    """
    def _sanitize(s: Optional[str], max_len: int = 200) -> str:
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

    presenter_brand = "OsirisCare"
    presenter_partner_id: Optional[str] = None
    presenter_contact_line = ""
    row = await conn.fetchrow(
        """
        SELECT p.id, p.brand_name, p.support_email, p.support_phone
          FROM client_orgs c
          JOIN partners p ON p.id = c.current_partner_id
         WHERE c.id = $1 AND c.current_partner_id IS NOT NULL
        """,
        client_org_id,
    )
    if row and row["brand_name"]:
        sanitized = _sanitize(row["brand_name"])
        if sanitized:
            presenter_brand = sanitized
            presenter_partner_id = str(row["id"])
            bits = []
            email_san = _sanitize(row.get("support_email"), max_len=120)
            phone_san = _sanitize(row.get("support_phone"), max_len=40)
            if email_san:
                bits.append(email_san)
            if phone_san:
                bits.append(phone_san)
            if bits:
                presenter_contact_line = " — " + " · ".join(bits)
    return presenter_brand, presenter_partner_id, presenter_contact_line


def _sign_attestation(canonical: str) -> Tuple[str, str]:
    """Compute SHA-256 hash + Ed25519 signature.

    Reuses the same signing_backend abstraction the auditor-kit
    chain uses (file/Vault Transit). Returns (hash_hex, sig_hex)."""
    try:
        try:
            from .signing_backend import get_signing_backend
        except ImportError:
            from signing_backend import get_signing_backend  # type: ignore
        signer = get_signing_backend()
    except Exception as e:
        raise UnableToIssueLetter(
            f"Signing backend unavailable: {e}. The letter cannot "
            f"be issued without an Ed25519 signature."
        )
    h = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    sig_bytes = signer.sign(canonical.encode("utf-8"))
    sig_hex = sig_bytes.hex() if isinstance(sig_bytes, bytes) else str(sig_bytes)
    return h, sig_hex


async def issue_letter(
    conn: asyncpg.Connection,
    client_org_id: str,
    issued_by_user_id: Optional[str],
    issued_by_email: Optional[str],
    period_days: int = DEFAULT_PERIOD_DAYS,
    validity_days: int = DEFAULT_VALIDITY_DAYS,
) -> Dict[str, Any]:
    """Issue a Compliance Attestation Letter.

    Preconditions (raise UnableToIssueLetter on violation, mapped
    to 409 Conflict in the API):
      - Active Privacy Officer designation (Carol contract)
      - BAA-on-file (Diane contract)

    Returns the inserted row dict + rendered HTML for PDF
    conversion in the caller.
    """
    # 1. Carol contract: Privacy Officer must exist.
    po = await get_current_po(conn, client_org_id)
    if po is None:
        raise UnableToIssueLetter(
            "No active Privacy Officer designation. Owner must "
            "designate a Privacy Officer before this letter can be "
            "issued (Settings → Privacy Officer)."
        )

    # 2. Diane contract: BAA-on-file.
    baa = await _get_current_baa(conn, client_org_id)
    if baa is None:
        raise UnableToIssueLetter(
            "No BAA on file for this organization. The Compliance "
            "Attestation Letter cannot be issued without a signed "
            "Business Associate Agreement (contact OsirisCare support)."
        )

    # 3. Period + validity windows.
    now = datetime.now(timezone.utc)
    period_end = now
    period_start = now - timedelta(days=period_days)
    valid_until = now + timedelta(days=validity_days)

    # 4. Operational facts.
    facts = await _gather_facts(conn, client_org_id, period_start, period_end)

    # 5. Presenter (white-label) snapshot.
    presenter_brand, presenter_partner_id, presenter_contact_line = (
        await _resolve_presenter(conn, client_org_id)
    )

    # 6. Canonical attestation payload — what the hash + signature
    #    bind to. Frozen at issue time. Includes designation snapshot
    #    so revoking the PO later doesn't invalidate the letter's
    #    historical record.
    attestation_facts = {
        "kind": "compliance_attestation_letter",
        "version": "1.0",
        "client_org_id": str(client_org_id),
        "practice_name": facts["practice_name"],
        "period_start": facts["period_start_iso"],
        "period_end": facts["period_end_iso"],
        "issued_at": now.isoformat(),
        "valid_until": valid_until.isoformat(),
        "sites_covered_count": facts["sites_covered_count"],
        "appliances_count": facts["appliances_count"],
        "workstations_count": facts["workstations_count"],
        "bundle_count": facts["bundle_count"],
        "overall_score": facts["overall_score"],
        "privacy_officer": {
            "designation_id": str(po["id"]),
            "name": po["name"],
            "title": po["title"],
            "email": po["email"],
            "accepted_at": po["accepted_at"].isoformat(),
            "explainer_version": po["explainer_version"],
        },
        "baa": {
            "signature_id": str(baa["id"]),
            "signed_at": baa["signed_at"].isoformat(),
            "practice_name": baa["practice_name"],
        },
        "presenter": {
            "brand": presenter_brand,
            "partner_id": presenter_partner_id,
            "contact_line": presenter_contact_line,
        },
    }
    canonical = _canonical_attestation_payload(attestation_facts)
    attestation_hash, ed25519_signature = _sign_attestation(canonical)

    # 7. Insert into compliance_attestation_letters. Supersede any
    #    prior active letter for this org (denormalized chain head).
    async with conn.transaction():
        prior = await conn.fetchrow(
            """
            SELECT id FROM compliance_attestation_letters
             WHERE client_org_id = $1 AND superseded_by_id IS NULL
             LIMIT 1
            """,
            client_org_id,
        )
        new_row = await conn.fetchrow(
            """
            INSERT INTO compliance_attestation_letters (
                client_org_id, period_start, period_end,
                sites_covered_count, appliances_count, workstations_count,
                overall_score, bundle_count,
                privacy_officer_designation_id,
                privacy_officer_name_snapshot,
                privacy_officer_title_snapshot,
                privacy_officer_email_snapshot,
                privacy_officer_explainer_version_snapshot,
                baa_signature_id, baa_dated_at, baa_practice_name_snapshot,
                presenter_brand_snapshot,
                presenter_partner_id_snapshot,
                presenter_contact_line_snapshot,
                attestation_hash, ed25519_signature,
                issued_at, valid_until,
                issued_by_user_id, issued_by_email
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8,
                $9, $10, $11, $12, $13,
                $14, $15, $16,
                $17, $18, $19,
                $20, $21,
                $22, $23, $24, $25
            )
            RETURNING id, attestation_hash
            """,
            client_org_id,
            period_start, period_end,
            facts["sites_covered_count"], facts["appliances_count"],
            facts["workstations_count"], facts["overall_score"],
            facts["bundle_count"],
            po["id"], po["name"], po["title"], po["email"],
            po["explainer_version"],
            baa["id"], baa["signed_at"], baa["practice_name"],
            presenter_brand, presenter_partner_id, presenter_contact_line,
            attestation_hash, ed25519_signature,
            now, valid_until,
            issued_by_user_id, issued_by_email,
        )
        if prior:
            await conn.execute(
                "UPDATE compliance_attestation_letters "
                "SET superseded_by_id = $1 WHERE id = $2",
                new_row["id"], prior["id"],
            )

    logger.info(
        "compliance_attestation_letter_issued",
        extra={
            "client_org_id": str(client_org_id),
            "letter_id": str(new_row["id"]),
            "attestation_hash": attestation_hash,
            "valid_until": valid_until.isoformat(),
            "sites_count": facts["sites_covered_count"],
            "bundle_count": facts["bundle_count"],
            "issued_by_email": issued_by_email,
            "presenter_brand": presenter_brand,
        },
    )

    # 8. Render HTML — kwargs match the template registration.
    html = render_template(
        "attestation_letter/letter",
        practice_name=facts["practice_name"],
        period_start_human=_human_date(period_start),
        period_end_human=_human_date(period_end),
        sites_covered_count=facts["sites_covered_count"],
        appliances_count=facts["appliances_count"],
        workstations_count=facts["workstations_count"],
        bundle_count=facts["bundle_count"],
        privacy_officer_name=po["name"],
        privacy_officer_title=po["title"],
        privacy_officer_email=po["email"],
        privacy_officer_accepted_human=_human_date(po["accepted_at"]),
        privacy_officer_explainer_version=po["explainer_version"],
        baa_dated_at_human=_human_date(baa["signed_at"]),
        baa_practice_name=baa["practice_name"],
        presenter_brand=presenter_brand,
        presenter_contact_line=presenter_contact_line,
        issued_at_human=_human_date(now),
        valid_until_human=_human_date(valid_until),
        attestation_hash=attestation_hash,
        verify_phone=VERIFY_PHONE,
        verify_url_short=VERIFY_URL_SHORT,
    )

    return {
        "letter_id": str(new_row["id"]),
        "attestation_hash": attestation_hash,
        "issued_at": now,
        "valid_until": valid_until,
        "html": html,
        "facts": facts,
        "practice_name": facts["practice_name"],
    }


def html_to_pdf(html: str) -> bytes:
    """Render the HTML through WeasyPrint. Lazy import — keeps the
    module loadable on dev boxes without WeasyPrint's system deps."""
    try:
        from weasyprint import HTML
    except ImportError as e:
        raise UnableToIssueLetter(
            f"WeasyPrint unavailable: {e}. The PDF cannot be rendered "
            f"without WeasyPrint installed (production has it; tests "
            f"use the HTML body directly)."
        )
    pdf_buf = io.BytesIO()
    HTML(string=html).write_pdf(pdf_buf)
    pdf_buf.seek(0)
    return pdf_buf.read()


async def get_letter_by_hash(
    conn: asyncpg.Connection, attestation_hash: str
) -> Optional[Dict[str, Any]]:
    """Used by F4 public /verify/{hash} endpoint. Calls the
    SECURITY DEFINER function so RLS doesn't block the public read.
    Returns the OCR-grade payload (no internal IDs, no client_org_id)."""
    row = await conn.fetchrow(
        "SELECT * FROM public_verify_attestation_letter($1)",
        attestation_hash,
    )
    return dict(row) if row else None

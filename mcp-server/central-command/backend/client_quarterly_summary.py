"""F3 — Quarterly Practice Compliance Summary generator (sprint 2026-05-08).

Maria's last owner-side P1 deferred from Friday. F3 produces a one-
page printable PDF the practice's Privacy Officer signs each quarter
and the practice owner files for HIPAA §164.530(j) records-retention
compliance.

Distinction from F1 (Compliance Attestation Letter):
  * F1 freezes CURRENT-STATE facts at issue time, valid 90 days,
    looks back 30 days. Maria forwards it to her insurance carrier
    or board on demand.
  * F3 freezes a TIME-WINDOWED summary for the previous calendar
    quarter (e.g. Q1 2026 = Jan 1 — Apr 1 UTC half-open), valid
    365 days. Maria files it in the §164.530(j) retention archive.
    The artifact does NOT mutate post-issue.

Customer-iterated wording carried over from F1 round-table 2026-05-06:
  * Maria — named-Privacy-Officer accountability sentence ("Reviewed
    and attested by [PO Name], [PO Title] — Privacy Officer
    designation").
  * Maria — plain English first, §-citation second.
  * Brian (insurance agent) — 1-800 phone number FIRST, public
    verify URL second. NO QR.
  * Diane (CPA) — white-label survivability: presenter snapshots
    are FROZEN at issue time.
  * OCR-investigator — "monitored on a continuous automated
    schedule" (NOT the legally aggressive "continuously
    monitored").
  * OCR-investigator — disclaimer: "audit-supportive technical
    evidence; it is not a HIPAA §164.528 disclosure accounting
    and does not constitute a legal opinion."
  * Maria — "documents that …" not "confirms that …" (verb
    posture is descriptive, not legal-opinion).

LOAD-BEARING PRECONDITIONS:
  1. An ACTIVE Privacy Officer designation MUST exist
     (privacy_officer_designations.revoked_at IS NULL). Carol
     contract: "never print a stale signature." Without a PO, the
     issuance API returns 409 with the copy "Designate a Privacy
     Officer (F2) before issuing the quarterly summary."
  2. The requested quarter MUST be in the past — issuing a future
     or in-progress quarter is meaningless (the period hasn't ended
     yet). The API rejects period_year/period_quarter combinations
     whose period_end is > now() with QuarterlySummaryError.

§-CITATION NARROWNESS (Carol contract carried over):
  Only §164.308(a)(1)(ii)(D) (information-system-activity-review)
  and §164.530(j) (records-retention) are referenced. NO over-broad
  §164.308/.310/.312 — those are not what F3 attests.

DETERMINISM:
  Canonical JSON uses sort_keys=True + compact separators (matches
  the auditor-kit determinism contract). The contract is "the
  attestation_hash is BYTE-STABLE for a given persisted row" —
  re-fetching the same row via /api/verify/quarterly/{hash}
  reproduces the same hash and the same canonical payload.
  Re-ISSUING a quarterly summary (e.g. a corrected re-issue) by
  contrast produces a NEW row + NEW wall-clock issued_at + NEW
  hash; the old row is superseded via partial-unique-idx pattern
  (mig 292 idx_qpcs_one_active_per_org_quarter). PDF byte-
  determinism is the WeasyPrint template's responsibility — F3
  inherits the same Jinja2 strict-undefined posture as F1.

  (Coach-ultrathink-sweep D-5 fix-up 2026-05-08: clarified the
  byte-determinism contract — pre-fix the docstring read as if
  re-issuing produced the same hash, which is not the design
  contract. The chain-anchored issuance shape requires a fresh
  hash on every issuance.)
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

try:
    from .templates import render_template
    from .client_privacy_officer import get_current as get_current_po
except ImportError:  # pytest path
    from templates import render_template  # type: ignore
    from client_privacy_officer import get_current as get_current_po  # type: ignore

logger = logging.getLogger(__name__)


# Carol-approved validity window. HIPAA records-retention is 6 years
# (§164.530(j)). We re-issue annually so 365 buys margin.
DEFAULT_VALIDITY_DAYS = 365

# Brian-the-agent contract: phone first, public-verify URL second,
# no QR. Mirrors F1 verbatim.
VERIFY_PHONE = "1-800-OSIRIS-1"
VERIFY_URL_SHORT = "osiriscare.io/verify/quarterly"


class QuarterlySummaryError(Exception):
    """Precondition violation — summary cannot be issued. The reason
    string is safe to surface to the customer via 409 Conflict."""


# ---------------------------------------------------------------- helpers


def _human_date(dt: Optional[datetime]) -> str:
    """Format like 'May 6, 2026' — Maria-readable."""
    if dt is None:
        return ""
    return dt.strftime("%B %-d, %Y")


def _canonical_attestation_payload(facts: Dict[str, Any]) -> str:
    """Deterministic JSON for hashing + signing. Matches the
    auditor-kit + F1 + P-F5 determinism contract: sort_keys=True,
    compact separators."""
    return json.dumps(facts, sort_keys=True, separators=(",", ":"))


def _sanitize_partner_text(s: Optional[str], max_len: int = 200) -> str:
    """Maya P0 carried over from F1 — defense against Markdown XSS via
    partner-controlled brand_name / support_email / support_phone in
    the rendered PDF. Strips control chars and HTML/Markdown-active
    punctuation, caps length."""
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


def _quarter_to_period(year: int, quarter: int) -> Tuple[datetime, datetime]:
    """Return (period_start, period_end) in UTC for the given calendar
    quarter, half-open [start, end). Q1 = [Jan 1, Apr 1)."""
    if quarter not in (1, 2, 3, 4):
        raise QuarterlySummaryError(
            f"period_quarter must be 1/2/3/4 (got {quarter!r})."
        )
    start_month = 3 * (quarter - 1) + 1
    period_start = datetime(year, start_month, 1, 0, 0, 0, tzinfo=timezone.utc)
    end_month = start_month + 3
    end_year = year
    if end_month > 12:
        end_month -= 12
        end_year += 1
    period_end = datetime(end_year, end_month, 1, 0, 0, 0, tzinfo=timezone.utc)
    # sanity: monthrange not needed but keeps imports honest
    _ = monthrange(year, start_month)
    return period_start, period_end


def _sign_attestation(canonical: str) -> Tuple[str, str]:
    """Compute SHA-256 hash + Ed25519 signature via the same
    signing_backend abstraction F1 + P-F5 use (file or Vault Transit).
    Returns (hash_hex, sig_hex)."""
    try:
        try:
            from .signing_backend import get_signing_backend
        except ImportError:
            from signing_backend import get_signing_backend  # type: ignore
        signer = get_signing_backend()
    except Exception as e:
        raise QuarterlySummaryError(
            f"Signing backend unavailable: {e}. The quarterly summary "
            f"cannot be issued without an Ed25519 signature."
        )
    h = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    sig_bytes = signer.sign(canonical.encode("utf-8"))
    sig_hex = sig_bytes.hex() if isinstance(sig_bytes, bytes) else str(sig_bytes)
    return h, sig_hex


# ---------------------------------------------------------------- presenter


async def _resolve_presenter(
    conn: asyncpg.Connection, client_org_id: str
) -> Tuple[str, Optional[str], str]:
    """Returns (presenter_brand, presenter_partner_id, presenter_contact_line).

    Mirrors F1 _resolve_presenter exactly — partner-controlled text
    is sanitized (Maya P0). Values FREEZE into *_snapshot columns so
    later partner edits do NOT mutate historical summaries.
    """
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
        sanitized = _sanitize_partner_text(row["brand_name"])
        if sanitized:
            presenter_brand = sanitized
            presenter_partner_id = str(row["id"])
            bits = []
            email_san = _sanitize_partner_text(row.get("support_email"), max_len=120)
            phone_san = _sanitize_partner_text(row.get("support_phone"), max_len=40)
            if email_san:
                bits.append(email_san)
            if phone_san:
                bits.append(phone_san)
            if bits:
                presenter_contact_line = " — " + " · ".join(bits)
    return presenter_brand, presenter_partner_id, presenter_contact_line


# ---------------------------------------------------------------- facts


async def _compute_quarterly_facts(
    conn: asyncpg.Connection,
    client_org_id: str,
    period_start: datetime,
    period_end: datetime,
) -> Dict[str, Any]:
    """Compute the operational facts that go on the summary.

    Returned dict is consumed by both render() AND the canonical-JSON
    hash. Every field must be deterministic from inputs (no wall-
    clock, no randomness)."""
    org_row = await conn.fetchrow(
        "SELECT name FROM client_orgs WHERE id = $1", client_org_id
    )
    if not org_row:
        raise QuarterlySummaryError(
            f"client_org {client_org_id} not found"
        )
    practice_name = org_row["name"]

    # Sites covered (RT33 P1: filter status != 'inactive').
    sites_count = await conn.fetchval(
        """
        SELECT COUNT(*) FROM sites
         WHERE client_org_id = $1 AND COALESCE(status, 'active') != 'inactive'
        """,
        client_org_id,
    ) or 0

    # Appliance count (RT33 P1: filter sa.deleted_at IS NULL on the
    # JOIN line).
    appliances_count = await conn.fetchval(
        """
        SELECT COUNT(*) FROM site_appliances sa
          JOIN sites s ON s.site_id = sa.site_id AND sa.deleted_at IS NULL
         WHERE s.client_org_id = $1
           AND COALESCE(s.status, 'active') != 'inactive'
        """,
        client_org_id,
    ) or 0

    # Workstations
    workstations_count = await conn.fetchval(
        """
        SELECT COUNT(*) FROM go_agents ga
          JOIN sites s ON s.site_id = ga.site_id
         WHERE s.client_org_id = $1
           AND COALESCE(s.status, 'active') != 'inactive'
        """,
        client_org_id,
    ) or 0

    # Bundle count for the period (chain-evidence count).
    bundle_count = await conn.fetchval(
        """
        SELECT COUNT(*) FROM compliance_bundles cb
          JOIN sites s ON s.site_id = cb.site_id
         WHERE s.client_org_id = $1
           AND cb.checked_at >= $2 AND cb.checked_at < $3
        """,
        client_org_id, period_start, period_end,
    ) or 0

    # OTS anchor coverage % for the period.
    ots_row = await conn.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE cb.ots_anchored_at IS NOT NULL) AS anchored,
            COUNT(*) AS total
          FROM compliance_bundles cb
          JOIN sites s ON s.site_id = cb.site_id
         WHERE s.client_org_id = $1
           AND cb.checked_at >= $2 AND cb.checked_at < $3
        """,
        client_org_id, period_start, period_end,
    )
    if ots_row and (ots_row["total"] or 0) > 0:
        ots_anchored_pct = float(
            (ots_row["anchored"] or 0) * 100.0 / float(ots_row["total"])
        )
    else:
        ots_anchored_pct = 0.0
    # Clamp to [0, 100] for CHECK constraint safety.
    if ots_anchored_pct < 0.0:
        ots_anchored_pct = 0.0
    if ots_anchored_pct > 100.0:
        ots_anchored_pct = 100.0

    # Drift detected / resolved within the window. incidents.created_at
    # is "first observed"; resolved_at is the close. We count distinct
    # (check_type, appliance) pairs to avoid double-counting alert
    # storms.
    drift_detected_count = await conn.fetchval(
        """
        SELECT COUNT(DISTINCT (i.check_type, i.appliance_id))
          FROM incidents i
          JOIN v_appliances_current a ON a.id = i.appliance_id
          JOIN sites s ON s.site_id = a.site_id
         WHERE s.client_org_id = $1
           AND i.created_at >= $2 AND i.created_at < $3
        """,
        client_org_id, period_start, period_end,
    ) or 0

    drift_resolved_count = await conn.fetchval(
        """
        SELECT COUNT(DISTINCT (i.check_type, i.appliance_id))
          FROM incidents i
          JOIN v_appliances_current a ON a.id = i.appliance_id
          JOIN sites s ON s.site_id = a.site_id
         WHERE s.client_org_id = $1
           AND i.resolved_at IS NOT NULL
           AND i.resolved_at >= $2 AND i.resolved_at < $3
        """,
        client_org_id, period_start, period_end,
    ) or 0

    # Mean compliance score across the period — pass/fail/warn from
    # latest-result-per (site, check_type, hostname) bounded BY the
    # period window. Inline because compute_compliance_score uses
    # NOW()-window_days, which can't take a fixed past quarter.
    mean_score: Optional[int] = None
    sites_in_org = await conn.fetch(
        """
        SELECT site_id FROM sites
         WHERE client_org_id = $1
           AND COALESCE(status, 'active') != 'inactive'
        """,
        client_org_id,
    )
    site_ids: List[str] = [r["site_id"] for r in sites_in_org]
    if site_ids:
        score_rows = await conn.fetch(
            """
            WITH unnested AS (
                SELECT
                    cb.site_id,
                    cb.checked_at,
                    c->>'check'    AS check_type,
                    c->>'status'   AS check_status,
                    COALESCE(c->>'hostname', c->>'host', '') AS hostname
                  FROM compliance_bundles cb,
                       jsonb_array_elements(cb.checks) AS c
                 WHERE cb.site_id = ANY($1)
                   AND cb.checked_at >= $2 AND cb.checked_at < $3
            ),
            latest AS (
                SELECT DISTINCT ON (site_id, check_type, hostname)
                    site_id, check_type, check_status, hostname, checked_at
                  FROM unnested
                 ORDER BY site_id, check_type, hostname, checked_at DESC
            )
            SELECT check_status FROM latest
            """,
            site_ids, period_start, period_end,
        )
        passed = sum(
            1 for r in score_rows
            if (r["check_status"] or "").lower() in ("pass", "compliant", "ok")
        )
        failed = sum(
            1 for r in score_rows
            if (r["check_status"] or "").lower() in ("non_compliant", "fail")
        )
        warnings = sum(
            1 for r in score_rows
            if (r["check_status"] or "").lower() == "warning"
        )
        # Warnings count as half-pass per the canonical scoring posture.
        denom = passed + failed + warnings
        if denom > 0:
            sc = (passed + 0.5 * warnings) * 100.0 / float(denom)
            mean_score = int(round(sc))

    # Monitored check types — controls evaluated. is_scored=true rows.
    monitored_check_types_count = await conn.fetchval(
        """
        SELECT COUNT(*) FROM check_type_registry WHERE is_scored = true
        """
    ) or 0

    return {
        "practice_name": practice_name,
        "sites_count": int(sites_count),
        "appliances_count": int(appliances_count),
        "workstations_count": int(workstations_count),
        "bundle_count": int(bundle_count),
        "ots_anchored_pct": round(float(ots_anchored_pct), 2),
        "drift_detected_count": int(drift_detected_count),
        "drift_resolved_count": int(drift_resolved_count),
        "mean_score": mean_score,
        "monitored_check_types_count": int(monitored_check_types_count),
        "period_start_iso": period_start.isoformat(),
        "period_end_iso": period_end.isoformat(),
    }


# ---------------------------------------------------------------- issuance


async def issue_quarterly_summary(
    conn: asyncpg.Connection,
    client_org_id: str,
    issued_by_user_id: Optional[str],
    issued_by_email: Optional[str],
    year: int,
    quarter: int,
    validity_days: int = DEFAULT_VALIDITY_DAYS,
) -> Dict[str, Any]:
    """Issue a Quarterly Practice Compliance Summary for the given
    calendar quarter.

    Preconditions (raise QuarterlySummaryError, mapped to 409 by API):
      - Active Privacy Officer designation (Carol contract)
      - Quarter must already be in the past (period_end <= now())
      - year >= 2024 (CHECK constraint; sanity floor)

    Returns the inserted-row dict + rendered HTML for the caller's
    PDF conversion.
    """
    # 1. Derive period from (year, quarter).
    if year < 2024:
        raise QuarterlySummaryError(
            f"period_year must be >= 2024 (got {year!r})."
        )
    period_start, period_end = _quarter_to_period(year, quarter)

    now = datetime.now(timezone.utc)
    if period_end > now:
        raise QuarterlySummaryError(
            f"Q{quarter} {year} has not ended yet. The quarterly "
            f"summary covers a completed calendar quarter; this one "
            f"ends {period_end.date().isoformat()}."
        )

    # 2. Carol contract: Privacy Officer must exist.
    po = await get_current_po(conn, client_org_id)
    if po is None:
        raise QuarterlySummaryError(
            "Designate a Privacy Officer (F2) before issuing the "
            "quarterly summary."
        )

    valid_until = now + timedelta(days=validity_days)

    # 3. Operational facts (FROZEN at issue time).
    facts = await _compute_quarterly_facts(
        conn, client_org_id, period_start, period_end
    )

    # 4. Presenter snapshot (white-label survivability).
    presenter_brand, presenter_partner_id, presenter_contact_line = (
        await _resolve_presenter(conn, client_org_id)
    )

    # 5. Canonical attestation payload — what the hash + signature
    #    bind to. Frozen at issue time.
    attestation_facts = {
        "kind": "quarterly_practice_compliance_summary",
        "version": "1.0",
        "client_org_id": str(client_org_id),
        "practice_name": facts["practice_name"],
        "period_year": int(year),
        "period_quarter": int(quarter),
        "period_start": facts["period_start_iso"],
        "period_end": facts["period_end_iso"],
        "issued_at": now.isoformat(),
        "valid_until": valid_until.isoformat(),
        "bundle_count": facts["bundle_count"],
        "ots_anchored_pct": facts["ots_anchored_pct"],
        "drift_detected_count": facts["drift_detected_count"],
        "drift_resolved_count": facts["drift_resolved_count"],
        "mean_score": facts["mean_score"],
        "sites_count": facts["sites_count"],
        "appliances_count": facts["appliances_count"],
        "workstations_count": facts["workstations_count"],
        "monitored_check_types_count": facts["monitored_check_types_count"],
        "privacy_officer": {
            "name": po["name"],
            "title": po["title"],
            "email": po["email"],
        },
        "presenter": {
            "brand": presenter_brand,
            "partner_id": presenter_partner_id,
            "contact_line": presenter_contact_line,
        },
    }
    canonical = _canonical_attestation_payload(attestation_facts)
    attestation_hash, ed25519_signature = _sign_attestation(canonical)

    # 6. Insert + supersede prior active summary for this (org, qtr).
    async with conn.transaction():
        prior = await conn.fetchrow(
            """
            SELECT id FROM quarterly_practice_compliance_summaries
             WHERE client_org_id = $1
               AND period_year = $2
               AND period_quarter = $3
               AND superseded_by_id IS NULL
             LIMIT 1
            """,
            client_org_id, int(year), int(quarter),
        )
        new_row = await conn.fetchrow(
            """
            INSERT INTO quarterly_practice_compliance_summaries (
                client_org_id,
                period_year, period_quarter,
                period_start, period_end,
                bundle_count, ots_anchored_pct,
                drift_detected_count, drift_resolved_count,
                mean_score,
                sites_count, appliances_count, workstations_count,
                monitored_check_types_count,
                privacy_officer_name_snapshot,
                privacy_officer_title_snapshot,
                privacy_officer_email_snapshot,
                presenter_brand_snapshot,
                presenter_partner_id_snapshot,
                presenter_contact_line_snapshot,
                practice_name_snapshot,
                attestation_hash, ed25519_signature,
                issued_at, valid_until,
                issued_by_user_id, issued_by_email
            ) VALUES (
                $1,
                $2, $3,
                $4, $5,
                $6, $7,
                $8, $9,
                $10,
                $11, $12, $13,
                $14,
                $15, $16, $17,
                $18, $19, $20,
                $21,
                $22, $23,
                $24, $25,
                $26, $27
            )
            RETURNING id, attestation_hash
            """,
            client_org_id,
            int(year), int(quarter),
            period_start, period_end,
            facts["bundle_count"], float(facts["ots_anchored_pct"]),
            facts["drift_detected_count"], facts["drift_resolved_count"],
            facts["mean_score"],
            facts["sites_count"], facts["appliances_count"],
            facts["workstations_count"],
            facts["monitored_check_types_count"],
            po["name"], po["title"], po["email"],
            presenter_brand, presenter_partner_id, presenter_contact_line,
            facts["practice_name"],
            attestation_hash, ed25519_signature,
            now, valid_until,
            issued_by_user_id, issued_by_email,
        )
        if prior:
            await conn.execute(
                "UPDATE quarterly_practice_compliance_summaries "
                "SET superseded_by_id = $1 WHERE id = $2",
                new_row["id"], prior["id"],
            )

    logger.info(
        "quarterly_practice_compliance_summary_issued",
        extra={
            "client_org_id": str(client_org_id),
            "summary_id": str(new_row["id"]),
            "attestation_hash": attestation_hash,
            "period_year": int(year),
            "period_quarter": int(quarter),
            "valid_until": valid_until.isoformat(),
            "bundle_count": facts["bundle_count"],
            "drift_detected_count": facts["drift_detected_count"],
            "drift_resolved_count": facts["drift_resolved_count"],
            "issued_by_email": issued_by_email,
            "presenter_brand": presenter_brand,
        },
    )

    # 7. Render HTML — kwargs match the template registration.
    ots_pct_str = f"{facts['ots_anchored_pct']:.1f}"
    mean_score_val = facts["mean_score"]
    mean_score_str = f"{mean_score_val}" if mean_score_val is not None else "—"
    html = render_template(
        "quarterly_summary/letter",
        practice_name=facts["practice_name"],
        period_year=int(year),
        period_quarter=int(quarter),
        period_start_human=_human_date(period_start),
        period_end_human=_human_date(period_end - timedelta(seconds=1)),
        bundle_count=facts["bundle_count"],
        ots_anchored_pct_str=ots_pct_str,
        drift_detected_count=facts["drift_detected_count"],
        drift_resolved_count=facts["drift_resolved_count"],
        mean_score_str=mean_score_str,
        sites_count=facts["sites_count"],
        appliances_count=facts["appliances_count"],
        workstations_count=facts["workstations_count"],
        monitored_check_types_count=facts["monitored_check_types_count"],
        privacy_officer_name=po["name"],
        privacy_officer_title=po["title"],
        privacy_officer_email=po["email"],
        presenter_brand=presenter_brand,
        presenter_contact_line=presenter_contact_line,
        issued_at_human=_human_date(now),
        valid_until_human=_human_date(valid_until),
        attestation_hash=attestation_hash,
        verify_phone=VERIFY_PHONE,
        verify_url_short=VERIFY_URL_SHORT,
    )

    return {
        "summary_id": str(new_row["id"]),
        "attestation_hash": attestation_hash,
        "issued_at": now,
        "valid_until": valid_until,
        "period_year": int(year),
        "period_quarter": int(quarter),
        "html": html,
        "facts": facts,
        "practice_name": facts["practice_name"],
    }


def html_to_pdf(html: str) -> bytes:
    """Render HTML through WeasyPrint. Lazy import — keeps the module
    loadable on dev boxes without WeasyPrint's system deps."""
    try:
        from weasyprint import HTML
    except ImportError as e:
        raise QuarterlySummaryError(
            f"WeasyPrint unavailable: {e}. The PDF cannot be rendered "
            f"without WeasyPrint installed (production has it; tests "
            f"use the HTML body directly)."
        )
    pdf_buf = io.BytesIO()
    HTML(string=html).write_pdf(pdf_buf)
    pdf_buf.seek(0)
    return pdf_buf.read()


async def get_quarterly_by_hash(
    conn: asyncpg.Connection, attestation_hash: str
) -> Optional[Dict[str, Any]]:
    """Used by the public /verify/quarterly/{hash} endpoint. Calls
    the SECURITY DEFINER function so RLS doesn't block the public
    read. Returns the OCR-grade payload (no internal IDs, no
    client_org_id, no PO email)."""
    row = await conn.fetchrow(
        "SELECT * FROM public_verify_quarterly_practice_summary($1)",
        attestation_hash,
    )
    return dict(row) if row else None

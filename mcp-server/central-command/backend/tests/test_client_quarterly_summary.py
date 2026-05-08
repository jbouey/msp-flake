"""Source-shape + render contract gates for F3 Quarterly Practice
Compliance Summary (sprint 2026-05-08).

F3 mirrors F1's posture (Ed25519 + persist + supersede + SECURITY
DEFINER public verify) for a TIME-WINDOWED quarterly summary the
practice's Privacy Officer signs and the owner files for HIPAA
§164.530(j) records-retention compliance.

Customer-iterated wording is pinned at TEST time so a future PR
can't silently soften it.
"""
from __future__ import annotations

import pathlib
import sys

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


# ---------------------------------------------------------------- migration


def test_migration_creates_quarterly_summary_table():
    """292_quarterly_practice_compliance_summaries.sql must exist
    with the expected schema + RLS + the SECURITY DEFINER public-
    verify fn. F1 mig 288 parity for the canonical pattern."""
    mig = (
        _BACKEND
        / "migrations"
        / "292_quarterly_practice_compliance_summaries.sql"
    )
    assert mig.exists(), "Migration 292 file missing"
    src = mig.read_text()

    assert "CREATE TABLE IF NOT EXISTS quarterly_practice_compliance_summaries" in src
    for col in (
        "client_org_id",
        "period_year",
        "period_quarter",
        "period_start",
        "period_end",
        "bundle_count",
        "ots_anchored_pct",
        "drift_detected_count",
        "drift_resolved_count",
        "mean_score",
        "sites_count",
        "appliances_count",
        "workstations_count",
        "monitored_check_types_count",
        "privacy_officer_name_snapshot",
        "privacy_officer_title_snapshot",
        "privacy_officer_email_snapshot",
        "presenter_brand_snapshot",
        "presenter_partner_id_snapshot",
        "presenter_contact_line_snapshot",
        "practice_name_snapshot",
        "attestation_hash",
        "ed25519_signature",
        "issued_at",
        "valid_until",
        "issued_by_user_id",
        "issued_by_email",
        "superseded_by_id",
    ):
        assert col in src, f"Migration 292 missing column: {col!r}"

    # Sanity invariants
    assert "qpcs_period_year_recent" in src, (
        "year >= 2024 CHECK constraint missing"
    )
    assert "qpcs_period_quarter_range" in src, (
        "quarter IN (1,2,3,4) CHECK constraint missing"
    )
    assert "qpcs_period_order" in src
    assert "qpcs_validity_order" in src
    assert "qpcs_score_range" in src
    assert "qpcs_ots_pct_range" in src
    assert "qpcs_hash_shape" in src

    # Partial unique idx on (org, year, quarter) WHERE !superseded.
    assert "idx_qpcs_one_active_per_org_quarter" in src
    assert "WHERE superseded_by_id IS NULL" in src
    assert "(\n        client_org_id, period_year, period_quarter\n    )" in src

    # RLS — F1 mig 288 parity (org-scoped, not site-scoped).
    assert "ENABLE ROW LEVEL SECURITY" in src
    assert "CREATE POLICY tenant_org_isolation" in src
    assert "current_setting('app.current_org', true)" in src

    # SECURITY DEFINER public-verify function.
    assert (
        "FUNCTION public_verify_quarterly_practice_summary(p_hash TEXT)"
        in src
    )
    assert "SECURITY DEFINER" in src

    # Function MUST NOT leak client_org_id, PO email, or signature
    # (Maya: OCR-grade payload only).
    fn_idx = src.find("FUNCTION public_verify_quarterly_practice_summary")
    fn_block = src[fn_idx:fn_idx + 4000]
    assert "client_org_id" not in fn_block, (
        "public_verify_quarterly_practice_summary MUST NOT return "
        "client_org_id (Maya: leaks tenant identity to public endpoint)"
    )
    assert "privacy_officer_email" not in fn_block, (
        "public_verify_quarterly_practice_summary MUST NOT return "
        "privacy_officer_email (PII leak)"
    )
    assert "ed25519_signature" not in fn_block, (
        "public_verify_quarterly_practice_summary MUST NOT return "
        "ed25519_signature (verifiable independently via hash)"
    )
    assert "issued_by_email" not in fn_block
    assert "issued_by_user_id" not in fn_block


# ---------------------------------------------------------------- preconditions


def test_summary_refuses_without_privacy_officer():
    """Carol contract: never print a stale signature. issue_quarterly_
    summary() raises QuarterlySummaryError when no active F2
    designation exists. The 409-mapped reason copy is the user-
    visible "Designate a Privacy Officer (F2) before issuing the
    quarterly summary." — pinned at test time."""
    src = (_BACKEND / "client_quarterly_summary.py").read_text()
    # Source wraps across two lines — assert each fragment.
    assert "Designate a Privacy Officer (F2) before issuing the" in src, (
        "issue_quarterly_summary must raise QuarterlySummaryError "
        "with the canonical 409 copy when no PO exists"
    )
    assert "quarterly summary." in src
    assert "get_current as get_current_po" in src
    assert "po = await get_current_po(conn, client_org_id)" in src
    assert "if po is None:" in src


def test_summary_refuses_for_in_progress_quarter():
    """The quarter must be in the past — issuing for a future or
    in-progress quarter is meaningless. Pinned at test time so a
    future PR can't silently allow a half-quarter summary."""
    src = (_BACKEND / "client_quarterly_summary.py").read_text()
    assert "if period_end > now:" in src, (
        "issue_quarterly_summary must reject quarters whose period_end "
        "has not yet passed"
    )
    assert "has not ended yet" in src


def test_unable_to_issue_maps_to_409_not_500():
    """API endpoint must catch QuarterlySummaryError and raise 409
    Conflict (not let it bubble to a 500). Customer needs to know
    WHICH precondition failed."""
    src = (_BACKEND / "client_portal.py").read_text()
    idx = src.find("async def issue_quarterly_summary_pdf(")
    assert idx > 0, "F3 issuance endpoint must exist"
    body = src[idx : idx + 6000]
    assert "except QuarterlySummaryError" in body
    assert "status_code=409" in body


# ---------------------------------------------------------------- template


def test_template_registered_with_all_kwargs():
    """The template must be registered with the exact required_kwargs
    set; kwargs allow-listed in security layer (Maya P1)."""
    src = (
        _BACKEND
        / "templates"
        / "quarterly_summary"
        / "__init__.py"
    ).read_text()
    for k in (
        "practice_name",
        "period_year",
        "period_quarter",
        "period_start_human",
        "period_end_human",
        "bundle_count",
        "ots_anchored_pct_str",
        "drift_detected_count",
        "drift_resolved_count",
        "mean_score_str",
        "sites_count",
        "appliances_count",
        "workstations_count",
        "monitored_check_types_count",
        "privacy_officer_name",
        "privacy_officer_title",
        "privacy_officer_email",
        "presenter_brand",
        "presenter_contact_line",
        "issued_at_human",
        "valid_until_human",
        "attestation_hash",
        "verify_phone",
        "verify_url_short",
    ):
        assert f'"{k}"' in src, (
            f"Template registration missing kwarg {k!r}"
        )


def test_template_renders_with_sentinel_kwargs():
    """Boot smoke shape — render the registered template with the
    sentinel factory and assert non-empty output AND the customer-
    iterated wording from F1 round-table."""
    from templates import render_template, get_registration

    reg = get_registration("quarterly_summary/letter")
    sentinel = reg.sentinel_kwargs()
    rendered = render_template("quarterly_summary/letter", **sentinel)
    assert rendered, "Quarterly summary rendered empty"
    # Header parity with the F3 product name.
    assert "Quarterly Practice Compliance Summary" in rendered
    # Quarter label.
    assert "Q1 2026" in rendered
    # OCR-investigator wording (NOT "continuously monitored").
    assert "monitored on a continuous automated schedule" in rendered, (
        "OCR-investigator note: 'continuously monitored' is legally "
        "aggressive. Use 'monitored on a continuous automated schedule'."
    )
    # Maria: "documents that …", NOT "confirms that …".
    assert "documents that " in rendered, (
        "Maria's iteration: verb posture is descriptive ('documents')"
        " not legal-opinion ('confirms')."
    )
    # PO sign-off block — Maria's accountable-human ask.
    assert (
        "Reviewed and attested by"
    ) in rendered
    assert "— Privacy Officer designation" in rendered
    # Brian: 1-800 phone first.
    assert "1-800-OSIRIS-1" in rendered or "1-800-" in rendered
    # §-citation narrowness — only §164.308(a)(1)(ii)(D) +
    # §164.530(j). NO over-broad §164.310 / §164.312.
    assert "§164.308(a)(1)(ii)(D)" in rendered
    assert "§164.530(j)" in rendered
    assert "§164.310" not in rendered, (
        "F3 §-citation must be narrow — no §164.310 reference"
    )
    assert "§164.312" not in rendered, (
        "F3 §-citation must be narrow — no §164.312 reference"
    )
    # §164.528 disclaimer parity (identical copy to F1 + P-F6).
    assert "§164.528" in rendered
    assert "audit-supportive technical evidence" in rendered
    assert "is not a HIPAA §164.528 disclosure accounting" in rendered
    assert "does not constitute a legal opinion" in rendered


def test_template_has_no_qr_code():
    """Brian-the-agent: 'I will not scan QRs from a PDF, that's how
    you get phished.' Removed from F1; must NOT reappear in F3."""
    src = (
        _BACKEND
        / "templates"
        / "quarterly_summary"
        / "letter.html.j2"
    ).read_text()
    src_low = src.lower()
    assert "qrcode" not in src_low
    assert " qr " not in src_low
    assert "{{ verify_phone }}" in src or "verify_phone" in src


def test_template_has_no_banned_legal_words():
    """CLAUDE.md Session 199: never 'ensures/prevents/protects/
    guarantees/audit-ready/100%/PHI never leaves'. Sweep against
    the rendered output (catches dynamic copy)."""
    from templates import render_template, get_registration
    reg = get_registration("quarterly_summary/letter")
    rendered = render_template(
        "quarterly_summary/letter", **reg.sentinel_kwargs()
    ).lower()
    for banned in (
        " ensures ", " prevents ", " protects ", " guarantees ",
        " audit-ready ", " phi never leaves ", " 100%",
        " continuously monitored ",
    ):
        assert banned not in rendered, (
            f"Banned legal-language phrase {banned!r} in rendered F3"
        )


def test_template_verify_url_uses_32_char_hash_suffix():
    """Verify-URL hash suffix matches the 32-char floor on the public-
    verify endpoint. F1 + P-F5 parity."""
    src = (
        _BACKEND
        / "templates"
        / "quarterly_summary"
        / "letter.html.j2"
    ).read_text()
    assert "{{ attestation_hash[:32] }}" in src, (
        "Verify URL must slice attestation_hash to 32 chars (matches "
        "the public-verify endpoint's 32-char floor)"
    )
    # The 16-char prefix (F1 pre-RT) MUST NOT appear.
    assert "[:16]" not in src, (
        "16-char prefix is the deprecated insecure form"
    )


# ---------------------------------------------------------------- determinism + signing


def test_canonical_payload_is_sort_keys_compact():
    """Hash + signature bind to the same canonical bytes the
    auditor-kit + F1 + P-F5 use (sort_keys=True, compact separators)."""
    src = (_BACKEND / "client_quarterly_summary.py").read_text()
    idx = src.find("def _canonical_attestation_payload")
    body = src[idx : idx + 600]
    assert "sort_keys=True" in body
    assert 'separators=(",", ":")' in body


def test_summary_signs_with_signing_backend_abstraction():
    """Same key + same backend the auditor-kit + F1 + P-F5 use
    (file or Vault Transit). NEVER a hardcoded local key."""
    src = (_BACKEND / "client_quarterly_summary.py").read_text()
    assert (
        "from .signing_backend import get_signing_backend" in src
        or "from signing_backend import get_signing_backend" in src
    )
    assert "signer.sign(canonical.encode" in src


def test_summary_supersedes_prior_active_for_same_quarter():
    """One active summary per (org, year, quarter). Re-issue
    supersedes the prior. Pinned at test time."""
    src = (_BACKEND / "client_quarterly_summary.py").read_text()
    idx = src.find("async def issue_quarterly_summary(")
    body = src[idx : idx + 12000]
    assert "superseded_by_id IS NULL" in body
    assert "SET superseded_by_id" in body
    # Per-quarter scoping: prior lookup MUST filter on year + quarter.
    assert "AND period_year = $2" in body
    assert "AND period_quarter = $3" in body


def test_white_label_snapshot_is_frozen_at_issue():
    """Diane CPA contract — no retroactive re-skin. F3 snapshots
    presenter_brand + partner_id + contact_line into *_snapshot
    columns; later partner edits do NOT mutate historical summaries."""
    mig = (
        _BACKEND
        / "migrations"
        / "292_quarterly_practice_compliance_summaries.sql"
    ).read_text()
    for col in (
        "presenter_brand_snapshot",
        "presenter_partner_id_snapshot",
        "presenter_contact_line_snapshot",
        "privacy_officer_name_snapshot",
        "privacy_officer_title_snapshot",
        "privacy_officer_email_snapshot",
        "practice_name_snapshot",
    ):
        assert col in mig, f"Snapshot column {col} missing from mig 292"


def test_partner_text_is_sanitized_against_xss():
    """Maya P0 carry-over from F1 — partner-controlled brand_name /
    support_email / support_phone must be sanitized before render
    (HTML/Markdown-active punctuation stripped, length capped)."""
    src = (_BACKEND / "client_quarterly_summary.py").read_text()
    assert "_sanitize_partner_text" in src
    # The function strips the F1 character set verbatim.
    assert "<>{}[]\\`|" in src or "<" in src and "`|" in src


# ---------------------------------------------------------------- F3 public verify route


def test_public_verify_endpoint_registered():
    """F3's public verify endpoint must be on the existing public_
    verify_router (no /client/auth prefix). main.py wires this
    router under /api/verify."""
    src = (_BACKEND / "client_portal.py").read_text()
    assert (
        '@public_verify_router.get("/quarterly/{attestation_hash}")'
        in src
    ), "F3 public verify endpoint must exist on public_verify_router"
    # main.py already includes public_verify_router (F1).
    main_src = (_BACKEND.parent.parent / "main.py").read_text()
    assert "app.include_router(public_verify_router)" in main_src


def test_public_verify_has_no_auth_guard():
    """F3 verify is PUBLIC by design — carriers / OCR investigators
    hit it without a session. Endpoint MUST NOT have a Depends
    (require_*) auth dependency."""
    src = (_BACKEND / "client_portal.py").read_text()
    idx = src.find("async def public_verify_quarterly_summary(")
    assert idx > 0
    sig_block = src[idx : idx + 600]
    assert "Depends(require_" not in sig_block, (
        "F3 verify endpoint must NOT have a require_* dependency — "
        "it is public-by-design (insurance carriers, OCR investigators)"
    )


def test_public_verify_rate_limited_per_ip():
    """Probing defense — 60/hr per source IP (X-Forwarded-For)."""
    src = (_BACKEND / "client_portal.py").read_text()
    idx = src.find("async def public_verify_quarterly_summary(")
    body = src[idx : idx + 6000]
    assert "check_rate_limit" in body
    assert "max_requests=60" in body
    assert 'request.headers.get("x-forwarded-for"' in body, (
        "F3 verify rate-limit IP keying must use X-Forwarded-For "
        "(first hop) — request.client.host is the proxy behind nginx"
    )
    assert "site_id=client_ip" in body


def test_public_verify_accepts_only_32_or_64_hash():
    """Steve P1-D + Maya P1-A carry-over: 32-char floor (128 bits)
    closes the birthday-collision tenant-mixup vector. 16 chars MUST
    NOT be accepted."""
    src = (_BACKEND / "client_portal.py").read_text()
    idx = src.find("async def public_verify_quarterly_summary(")
    body = src[idx : idx + 6000]
    assert "len(h) not in (32, 64)" in body, (
        "F3 verify must enforce minimum 32-hex-char hash"
    )
    assert "(16, 64)" not in body, (
        "F3 verify must NOT accept the 16-char prefix"
    )


def test_public_verify_detects_ambiguous_prefix():
    """Steve P1-D defense in depth: if a pathological 32-char prefix
    collision occurs, return ambiguity error rather than silently
    picking LIMIT 1."""
    src = (_BACKEND / "client_portal.py").read_text()
    idx = src.find("async def public_verify_quarterly_summary(")
    body = src[idx : idx + 8000]
    assert "ambiguous_prefix_supply_full_64_hex_hash" in body, (
        "F3 verify must detect prefix collisions and refuse"
    )
    assert "len(full_rows) > 1" in body


def test_public_verify_payload_does_not_leak_internals():
    """OCR-grade payload only. NEVER client_org_id, NEVER PO email,
    NEVER ed25519_signature, NEVER issued_by_*."""
    src = (_BACKEND / "client_portal.py").read_text()
    idx = src.find("async def public_verify_quarterly_summary(")
    body = src[idx : idx + 8000]
    # Forbidden keys in the response payload.
    for forbidden_key in (
        '"client_org_id":',
        '"privacy_officer_email":',
        '"ed25519_signature":',
        '"issued_by_email":',
        '"issued_by_user_id":',
    ):
        assert forbidden_key not in body, (
            f"F3 verify payload leaks internal field {forbidden_key!r}"
        )
    # Required OCR-grade fields per Brian / OCR-investigator
    # contract.
    assert '"is_expired":' in body
    assert '"is_superseded":' in body
    assert '"period_year":' in body
    assert '"period_quarter":' in body
    assert '"bundle_count":' in body
    assert '"privacy_officer":' in body
    assert '"presenter_brand":' in body


# ---------------------------------------------------------------- API endpoint


def test_summary_api_endpoint_writes_audit_log():
    """Mutating endpoint must write a client_audit_log row with the
    canonical action label."""
    src = (_BACKEND / "client_portal.py").read_text()
    idx = src.find("async def issue_quarterly_summary_pdf(")
    body = src[idx : idx + 6000]
    assert "_audit_client_action" in body
    assert '"QUARTERLY_SUMMARY_ISSUED"' in body


def test_summary_api_endpoint_rate_limited():
    """5/hr per (org, user) — F1 parity."""
    src = (_BACKEND / "client_portal.py").read_text()
    idx = src.find("async def issue_quarterly_summary_pdf(")
    body = src[idx : idx + 6000]
    assert "check_rate_limit" in body
    assert "max_requests=5" in body
    assert 'caller_key=f"client:{user[\'user_id\']}"' in body


def test_summary_api_endpoint_wraps_weasyprint_in_to_thread():
    """Steve P1-A carry-over: WeasyPrint render is sync (100-500ms);
    must wrap in asyncio.to_thread so the event loop stays responsive."""
    src = (_BACKEND / "client_portal.py").read_text()
    idx = src.find("async def issue_quarterly_summary_pdf(")
    body = src[idx : idx + 6000]
    assert "asyncio.to_thread" in body or "_asyncio.to_thread" in body, (
        "PDF render must be wrapped in asyncio.to_thread"
    )


def test_summary_api_endpoint_uses_post_method():
    """F3 takes a (year, quarter) body — must be POST, not GET. F1
    is GET because it has no body. Pinned at test time."""
    src = (_BACKEND / "client_portal.py").read_text()
    assert '@auth_router.post("/quarterly-summary")' in src, (
        "F3 issuance endpoint must be POST (takes year + quarter)"
    )


def test_security_allowlist_includes_all_summary_kwargs():
    """Maya P1 — every render kwarg must be in the security allow-
    list. Adding a new kwarg requires explicit security review."""
    src = (_BACKEND / "templates" / "__init__.py").read_text()
    for k in (
        # F3-novel kwargs (the rest reuse F1's allow-list entries).
        "period_year",
        "period_quarter",
        "drift_detected_count",
        "drift_resolved_count",
        "mean_score_str",
        "monitored_check_types_count",
        "sites_count",
    ):
        assert f'"{k}"' in src, (
            f"Quarterly-summary kwarg {k!r} not in "
            f"_KWARGS_SECURITY_ALLOWLIST"
        )


def test_validity_window_is_365_days():
    """HIPAA records-retention is 6 years; F3 re-issues annually so
    365 buys margin. Pinned at test time."""
    src = (_BACKEND / "client_quarterly_summary.py").read_text()
    assert "DEFAULT_VALIDITY_DAYS = 365" in src, (
        "F3 default validity must be 365 days"
    )


def test_quarter_to_period_helper_shapes():
    """Pure helper — verify Q1/Q2/Q3/Q4 ↔ UTC half-open windows.

    Source-shape gate (matches F1's posture — runtime imports drag
    in transitive deps unavailable in stub-isolation CI). The
    helper is asserted to (a) exist as a top-level function,
    (b) compute start_month from quarter via the canonical formula,
    (c) build UTC midnights, and (d) wrap the year on Q4."""
    src = (_BACKEND / "client_quarterly_summary.py").read_text()
    assert "def _quarter_to_period(year: int, quarter: int)" in src
    assert "if quarter not in (1, 2, 3, 4):" in src
    # Canonical formula: start_month = 3 * (quarter - 1) + 1 -> 1/4/7/10.
    assert "start_month = 3 * (quarter - 1) + 1" in src
    # UTC midnights — the period is timezone-aware.
    assert "tzinfo=timezone.utc" in src
    # Q4 must wrap the year boundary (end_month > 12).
    assert "if end_month > 12:" in src
    assert "end_month -= 12" in src
    assert "end_year += 1" in src


def test_canonical_payload_is_deterministic_source_shape():
    """The canonical-JSON helper must use sort_keys=True + compact
    separators — Ed25519 signature integrity depends on byte-
    determinism. Source-shape gate."""
    src = (_BACKEND / "client_quarterly_summary.py").read_text()
    idx = src.find("def _canonical_attestation_payload")
    body = src[idx : idx + 600]
    assert "json.dumps(facts" in body
    assert "sort_keys=True" in body
    assert 'separators=(",", ":")' in body


def test_summary_uses_org_connection_for_rls():
    """F3 issuance MUST run under org_connection (RLS-aware) — NOT
    admin_connection. The tenant_org_isolation policy gates the row
    by app.current_org. Pinned at test time."""
    src = (_BACKEND / "client_portal.py").read_text()
    idx = src.find("async def issue_quarterly_summary_pdf(")
    body = src[idx : idx + 6000]
    assert "org_connection(pool, org_id=org_id)" in body, (
        "F3 issuance must use org_connection for RLS-aware writes"
    )


def test_no_overbroad_section_citations():
    """Carol contract — F3 cites only §164.308(a)(1)(ii)(D) and
    §164.530(j). NO over-broad §164.308 / §164.310 / §164.312
    references. Pinned at the SOURCE level (template + module +
    migration) so a future PR can't add an over-broad citation
    without explicit review."""
    template_src = (
        _BACKEND
        / "templates"
        / "quarterly_summary"
        / "letter.html.j2"
    ).read_text()
    # The narrow citations MUST be present.
    assert "§164.308(a)(1)(ii)(D)" in template_src
    assert "§164.530(j)" in template_src
    # Over-broad references MUST NOT appear in the rendered template.
    # (§164.524 IS allowed because it's the negative-scope assertion
    # "is not part of any designated record set under §164.524";
    # §164.528 IS allowed because of the disclaimer parity.)
    assert "§164.310" not in template_src
    assert "§164.312" not in template_src

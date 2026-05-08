"""Source-shape + render contract gates for F1 Compliance Attestation
Letter + F4 public /verify endpoint (round-table 2026-05-06).

Customer-iterated wording from Maria/Janet/Brian/Diane/OCR
investigator round-table is pinned here at TEST time so a future
PR can't silently soften it back to demo-loss copy.
"""
from __future__ import annotations

import pathlib
import sys

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


# ---------------------------------------------------------------- migration


def test_migration_creates_letters_table():
    """288_compliance_attestation_letters.sql must exist with the
    expected schema + RLS + the SECURITY DEFINER public-verify fn."""
    mig = (
        _BACKEND
        / "migrations"
        / "288_compliance_attestation_letters.sql"
    )
    assert mig.exists()
    src = mig.read_text()

    assert "CREATE TABLE IF NOT EXISTS compliance_attestation_letters" in src
    for col in (
        "client_org_id",
        "period_start",
        "period_end",
        "sites_covered_count",
        "appliances_count",
        "workstations_count",
        "bundle_count",
        "overall_score",
        "privacy_officer_designation_id",
        "privacy_officer_name_snapshot",
        "privacy_officer_title_snapshot",
        "privacy_officer_email_snapshot",
        "privacy_officer_explainer_version_snapshot",
        "baa_signature_id",
        "baa_dated_at",
        "baa_practice_name_snapshot",
        "presenter_brand_snapshot",
        "presenter_partner_id_snapshot",
        "presenter_contact_line_snapshot",
        "attestation_hash",
        "ed25519_signature",
        "issued_at",
        "valid_until",
        "issued_by_user_id",
        "issued_by_email",
        "superseded_by_id",
    ):
        assert col in src, f"Migration 288 missing column: {col!r}"

    # Sanity invariants
    assert "cal_period_order" in src
    assert "cal_validity_order" in src
    assert "cal_score_range" in src

    # RLS
    assert "ENABLE ROW LEVEL SECURITY" in src
    assert "CREATE POLICY tenant_org_isolation" in src

    # The SECURITY DEFINER public-verify function — F4 entry point.
    assert "FUNCTION public_verify_attestation_letter(p_hash TEXT)" in src
    assert "SECURITY DEFINER" in src
    # Function MUST NOT leak client_org_id (Maya: OCR-grade payload only).
    fn_idx = src.find("FUNCTION public_verify_attestation_letter")
    fn_block = src[fn_idx:fn_idx + 2000]
    assert "client_org_id" not in fn_block, (
        "public_verify_attestation_letter MUST NOT return client_org_id "
        "(Maya: leaks tenant identity to a public endpoint)"
    )


# ---------------------------------------------------------------- preconditions


def test_letter_refuses_without_privacy_officer():
    """Carol contract: never print a stale signature. issue_letter()
    raises UnableToIssueLetter when no active designation exists."""
    src = (_BACKEND / "client_attestation_letter.py").read_text()
    assert "No active Privacy Officer designation" in src, (
        "issue_letter must raise UnableToIssueLetter with a clear "
        "message when no active Privacy Officer designation exists"
    )
    # The check uses get_current_po imported from F2.
    assert "get_current as get_current_po" in src
    assert "po = await get_current_po(conn, client_org_id)" in src
    assert "if po is None:" in src


def test_letter_refuses_without_baa_on_file():
    """Diane contract: 'the whole letter is worthless if Maria's
    disclosing PHI metadata to a vendor with no BAA on file.'"""
    src = (_BACKEND / "client_attestation_letter.py").read_text()
    assert "No BAA on file" in src, (
        "issue_letter must refuse render when baa_signatures has "
        "no row for the org's primary email"
    )
    assert "_get_current_baa" in src


def test_unable_to_issue_maps_to_409_not_500():
    """API endpoint must catch UnableToIssueLetter and raise 409
    Conflict (not let it bubble to a 500). Customer needs to know
    WHICH precondition failed."""
    src = (_BACKEND / "client_portal.py").read_text()
    idx = src.find("async def issue_attestation_letter_pdf(")
    assert idx > 0
    body = src[idx : idx + 4000]
    assert "except UnableToIssueLetter" in body
    assert "status_code=409" in body


# ---------------------------------------------------------------- template


def test_template_registered_with_all_kwargs():
    """The template must be registered with the exact required_kwargs
    set; kwargs allow-listed in security layer (Maya P1)."""
    src = (_BACKEND / "templates" / "attestation_letter" / "__init__.py").read_text()
    for k in (
        "practice_name",
        "period_start_human",
        "period_end_human",
        "sites_covered_count",
        "appliances_count",
        "workstations_count",
        "bundle_count",
        "privacy_officer_name",
        "privacy_officer_title",
        "privacy_officer_email",
        "privacy_officer_accepted_human",
        "privacy_officer_explainer_version",
        "baa_dated_at_human",
        "baa_practice_name",
        "presenter_brand",
        "presenter_contact_line",
        "issued_at_human",
        "valid_until_human",
        "attestation_hash",
        "verify_phone",
        "verify_url_short",
    ):
        assert f'"{k}"' in src, f"Template registration missing kwarg {k!r}"


def test_template_renders_with_sentinel_kwargs():
    """Boot smoke shape — render the registered template with the
    sentinel factory and assert non-empty output."""
    from templates import render_template, get_registration

    reg = get_registration("attestation_letter/letter")
    sentinel = reg.sentinel_kwargs()
    rendered = render_template("attestation_letter/letter", **sentinel)
    assert rendered, "Letter template rendered empty with sentinel kwargs"
    # Boilerplate + customer-iterated wording must be present.
    assert "Compliance Attestation" in rendered
    # OCR-investigator wording (NOT the legally-aggressive "continuously").
    assert "monitored on a continuous automated schedule" in rendered, (
        "OCR-investigator note: 'continuously monitored' is legally "
        "aggressive. Use 'monitored on a continuous automated schedule'."
    )
    # Maria's "I-did-something" ask: PO is named as accountable human.
    assert ", as Privacy Officer, reviews the monthly evidence summary" in rendered
    # Brian-the-agent: 1-800 phone number FIRST (not QR).
    assert "1-800-OSIRIS-1" in rendered or "1-800-" in rendered
    # Diane-CPA contracts: BAA reference + 7-year retention.
    assert "Issued under BAA" in rendered
    assert "7 years post-termination" in rendered
    # Carol contract: §164.528 disclaimer present.
    assert "§164.528" in rendered
    # Cover paragraph for Janet's email forwarding workflow.
    assert "For the recipient" in rendered


def test_template_has_no_qr_code_per_brian():
    """Brian-the-agent: 'I will not scan QRs from a PDF, that's how
    you get phished.' Removed entirely."""
    src = (_BACKEND / "templates" / "attestation_letter" / "letter.html.j2").read_text()
    src_low = src.lower()
    assert "qrcode" not in src_low
    assert " qr " not in src_low
    # 1-800 phone is the primary verification signal.
    assert "{{ verify_phone }}" in src or "verify_phone" in src


def test_template_has_no_banned_legal_words():
    """CLAUDE.md Session 199: never 'ensures/prevents/protects/
    guarantees/audit-ready/100%/PHI never leaves'. Letter is the
    most-forwarded customer-facing artifact — sweep against the
    rendered output (catches dynamic copy)."""
    from templates import render_template, get_registration
    reg = get_registration("attestation_letter/letter")
    rendered = render_template(
        "attestation_letter/letter", **reg.sentinel_kwargs()
    ).lower()
    for banned in (" ensures ", " prevents ", " protects ", " guarantees ",
                   " audit-ready ", " phi never leaves ", " 100%"):
        assert banned not in rendered, (
            f"Banned legal-language phrase {banned!r} in rendered "
            f"attestation letter"
        )


# ---------------------------------------------------------------- determinism + signing


def test_canonical_payload_is_sort_keys_compact():
    """Hash + signature bind to the same canonical bytes the
    auditor-kit uses (sort_keys=True, separators=(',', ':'))."""
    src = (_BACKEND / "client_attestation_letter.py").read_text()
    idx = src.find("def _canonical_attestation_payload")
    body = src[idx : idx + 600]
    assert 'sort_keys=True' in body
    assert "separators=(\",\", \":\")" in body


def test_letter_signs_with_signing_backend_abstraction():
    """Same key + same backend the auditor-kit chain uses (file or
    Vault Transit). NEVER a hardcoded local key."""
    src = (_BACKEND / "client_attestation_letter.py").read_text()
    assert "from .signing_backend import get_signing_backend" in src or \
           "from signing_backend import get_signing_backend" in src
    assert "signer.sign(canonical.encode" in src


def test_letter_supersedes_prior_active_letter():
    """One active letter per org at a time (denormalized chain
    head). Issuance updates prior.superseded_by_id."""
    src = (_BACKEND / "client_attestation_letter.py").read_text()
    idx = src.find("async def issue_letter(")
    body = src[idx : idx + 8000]
    assert "superseded_by_id IS NULL" in body
    assert "SET superseded_by_id" in body


def test_white_label_snapshot_is_frozen_at_issue():
    """Diane CPA contract — no retroactive re-skin if Maria switches
    MSPs. Letter snapshots presenter_brand + partner_id + contact_line
    into *_snapshot columns; later partner edits do NOT mutate
    historical letters."""
    mig = (_BACKEND / "migrations" / "288_compliance_attestation_letters.sql").read_text()
    for col in (
        "presenter_brand_snapshot",
        "presenter_partner_id_snapshot",
        "presenter_contact_line_snapshot",
        "privacy_officer_name_snapshot",
        "privacy_officer_title_snapshot",
        "privacy_officer_email_snapshot",
        "baa_practice_name_snapshot",
    ):
        assert col in mig, f"Snapshot column {col} missing from mig 288"


# ---------------------------------------------------------------- F4 public verify


def test_public_verify_endpoint_registered():
    """F4 must be on a SEPARATE public router (no /client/auth
    prefix) that main.py mounts directly."""
    src = (_BACKEND / "client_portal.py").read_text()
    assert "public_verify_router = APIRouter(prefix=\"/api/verify\"" in src, (
        "F4 endpoint must be on a router with prefix /api/verify"
    )
    assert '@public_verify_router.get("/attestation/{attestation_hash}")' in src
    # Must be wired in main.py.
    main_src = (_BACKEND.parent.parent / "main.py").read_text()
    assert "public_verify_router" in main_src
    assert "app.include_router(public_verify_router)" in main_src


def test_public_verify_has_no_auth_guard():
    """F4 is PUBLIC by design — Brian's underwriter hits it without
    a session. The endpoint MUST NOT have a Depends(require_*) auth."""
    src = (_BACKEND / "client_portal.py").read_text()
    idx = src.find("async def public_verify_attestation_letter(")
    assert idx > 0
    sig_block = src[idx : idx + 600]
    assert "Depends(require_" not in sig_block, (
        "F4 endpoint must NOT have a require_* auth dependency — "
        "it is public-by-design (insurance carriers, OCR investigators)"
    )


def test_public_verify_rate_limited():
    """Probing defense — 60/hr per source IP."""
    src = (_BACKEND / "client_portal.py").read_text()
    idx = src.find("async def public_verify_attestation_letter(")
    body = src[idx : idx + 4000]
    assert "check_rate_limit" in body
    assert "max_requests=60" in body
    # Per-IP keying, not per-org (no org context on a public endpoint).
    assert "site_id=client_ip" in body or "client_ip" in body


def test_public_verify_payload_does_not_leak_internals():
    """OCR-grade payload only. NEVER client_org_id, NEVER user_id,
    NEVER ed25519_signature (signature is verifiable separately)."""
    src = (_BACKEND / "client_portal.py").read_text()
    idx = src.find("async def public_verify_attestation_letter(")
    body = src[idx : idx + 4000]
    # Forbidden fields in the response payload.
    assert "client_org_id" not in body or "row[\"client_org_id\"]" not in body
    assert "ed25519_signature" not in body, (
        "F4 must not leak ed25519_signature to the public endpoint"
    )
    assert "issued_by_email" not in body
    assert "issued_by_user_id" not in body
    # Required fields per OCR investigator + Brian agent contract.
    assert "is_expired" in body
    assert "is_superseded" in body
    assert "privacy_officer" in body
    assert "baa_on_file" in body
    assert "bundle_count" in body


def test_public_verify_accepts_short_or_full_hash():
    """Letter prints a 16-char prefix in some places (URL-friendly)
    and the full 64-char hash in others. Endpoint accepts both."""
    src = (_BACKEND / "client_portal.py").read_text()
    idx = src.find("async def public_verify_attestation_letter(")
    body = src[idx : idx + 4000]
    assert "len(h) not in (16, 64)" in body, (
        "F4 must accept BOTH the 16-char short form (printed on the "
        "letter for URL/QR) AND the 64-char full SHA-256 hash"
    )


# ---------------------------------------------------------------- API endpoint


def test_letter_api_endpoint_writes_audit_log():
    """Mutating endpoint must write a client_audit_log row matching
    every other mutating client-portal endpoint."""
    src = (_BACKEND / "client_portal.py").read_text()
    idx = src.find("async def issue_attestation_letter_pdf(")
    body = src[idx : idx + 4000]
    assert '_audit_client_action' in body
    assert '"ATTESTATION_LETTER_ISSUED"' in body


def test_letter_api_endpoint_rate_limited():
    """5/hr per (org, user) — letters are expensive to render."""
    src = (_BACKEND / "client_portal.py").read_text()
    idx = src.find("async def issue_attestation_letter_pdf(")
    body = src[idx : idx + 4000]
    assert "check_rate_limit" in body
    assert "max_requests=5" in body
    assert 'caller_key=f"client:{user[\'user_id\']}"' in body


def test_security_allowlist_includes_all_letter_kwargs():
    """Maya P1 — every render kwarg must be in the security allow-list.
    Adding a new kwarg requires explicit security review."""
    src = (_BACKEND / "templates" / "__init__.py").read_text()
    for k in (
        "practice_name",
        "period_start_human",
        "period_end_human",
        "sites_covered_count",
        "appliances_count",
        "workstations_count",
        "bundle_count",
        "privacy_officer_name",
        "privacy_officer_title",
        "privacy_officer_email",
        "privacy_officer_accepted_human",
        "privacy_officer_explainer_version",
        "baa_dated_at_human",
        "baa_practice_name",
        "issued_at_human",
        "valid_until_human",
        "attestation_hash",
        "verify_phone",
        "verify_url_short",
    ):
        assert f'"{k}"' in src, (
            f"Letter kwarg {k!r} not in _KWARGS_SECURITY_ALLOWLIST"
        )

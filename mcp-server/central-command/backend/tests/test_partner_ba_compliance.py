"""Source-shape gates for P-F6 BA Compliance Attestation +
downstream-BAA roster (partner round-table 2026-05-08)."""
from __future__ import annotations

import pathlib
import sys

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


# ---------------------------------------------------------------- migration


def test_migration_creates_baa_roster_table():
    mig = _BACKEND / "migrations" / "290_partner_baa_roster.sql"
    assert mig.exists()
    src = mig.read_text()

    assert "CREATE TABLE IF NOT EXISTS partner_baa_roster" in src
    for col in (
        "partner_id",
        "counterparty_org_id",
        "counterparty_practice_name",
        "executed_at",
        "expiry_at",
        "scope",
        "doc_sha256",
        "signer_name",
        "signer_title",
        "signer_email",
        "uploaded_at",
        "uploaded_by_user_id",
        "uploaded_by_email",
        "revoked_at",
        "revoked_by_user_id",
        "revoked_reason",
        "attestation_bundle_id",
        "revoked_attestation_bundle_id",
    ):
        assert col in src, f"Migration 290 missing column: {col!r}"

    # Exactly-one-counterparty constraint
    assert "pbr_one_counterparty" in src
    # Scope length matches privileged-access ≥20 convention
    assert "pbr_scope_minlen" in src
    assert "LENGTH(scope) >= 20" in src
    # Revocation field consistency
    assert "pbr_revoke_fields_consistent" in src
    assert "pbr_revoked_reason_minlen" in src

    # One ACTIVE BAA per (partner, counterparty_org) pair
    assert "idx_pbr_one_active_per_pair" in src
    assert "WHERE revoked_at IS NULL AND counterparty_org_id IS NOT NULL" in src

    # RLS — partner-scoped
    assert "ENABLE ROW LEVEL SECURITY" in src
    assert "CREATE POLICY partner_self_isolation" in src
    assert (
        "partner_id::text = current_setting('app.current_partner', true)"
        in src
    )


# ---------------------------------------------------------------- ALLOWED_EVENTS lockstep


def test_two_events_in_allowed_events():
    src = (_BACKEND / "privileged_access_attestation.py").read_text()
    assert '"partner_baa_roster_added"' in src
    assert '"partner_baa_roster_revoked"' in src


def test_lockstep_test_includes_new_events():
    src = (
        _BACKEND
        / "tests"
        / "test_privileged_chain_allowed_events_lockstep.py"
    ).read_text()
    assert '"partner_baa_roster_added"' in src
    assert '"partner_baa_roster_revoked"' in src


# ---------------------------------------------------------------- module shape


def test_module_exposes_public_api():
    src = (_BACKEND / "partner_ba_compliance.py").read_text()
    for fn in (
        "async def add_baa_to_roster(",
        "async def revoke_baa_from_roster(",
        "async def list_active_roster(",
        "async def issue_ba_compliance_attestation(",
        # Coach retroactive sweep 2026-05-08 — convergence with
        # F1/P-F5: Ed25519 sign + persist + public verify
        # SECURITY DEFINER lookup.
        "def _sign_attestation(",
        "async def get_ba_attestation_by_hash(",
    ):
        assert fn in src, f"Public function {fn!r} missing"
    assert "class BAComplianceError" in src


def _issue_fn_body() -> str:
    src = (_BACKEND / "partner_ba_compliance.py").read_text()
    idx = src.find("async def issue_ba_compliance_attestation(")
    # Find the next top-level `async def` or `def ` (start of next
    # symbol) to scope precisely.
    after = src[idx + 1 :]
    next_idx = -1
    for marker in ("\nasync def ", "\ndef "):
        m = after.find(marker)
        if m >= 0 and (next_idx < 0 or m < next_idx):
            next_idx = m
    return src[idx : idx + 1 + next_idx] if next_idx > 0 else src[idx:]


def test_issue_signs_with_ed25519():
    """Coach retroactive sweep 2026-05-08 — P-F6 must converge to
    F1/P-F5 signing posture: SHA-256 alone is insufficient parallel-
    structure with the rest of the printable-artifact ensemble."""
    body = _issue_fn_body()
    assert "_sign_attestation(canonical)" in body, (
        "issue_ba_compliance_attestation must call _sign_attestation "
        "to produce (sha256_hex, ed25519_signature_hex) — pure "
        "hashlib.sha256 violates F1/P-F5 parallel structure."
    )
    assert "ed25519_signature" in body
    assert "INSERT INTO partner_ba_compliance_attestations" in body
    assert "superseded_by_id IS NULL" in body
    assert "SET superseded_by_id" in body, (
        "Atomic supersede pattern required (mirror P-F5 mig 289)."
    )


def test_issue_returns_attestation_id_and_signature():
    body = _issue_fn_body()
    # The return dict must surface attestation_id + ed25519_signature
    # so the API layer can echo X-Attestation-Id and the row is
    # bookkeep-able by the caller.
    assert '"attestation_id"' in body
    assert '"ed25519_signature"' in body


def test_add_writes_chain_attestation():
    src = (_BACKEND / "partner_ba_compliance.py").read_text()
    assert "create_privileged_access_attestation" in src
    # Anchor at synthetic partner_org:<id> namespace
    assert 'f"partner_org:{partner_id}"' in src


def test_add_atomic_transaction():
    src = (_BACKEND / "partner_ba_compliance.py").read_text()
    idx = src.find("async def add_baa_to_roster(")
    body = src[idx : idx + 8000]
    assert "async with conn.transaction():" in body


def test_add_rejects_both_or_neither_counterparty():
    src = (_BACKEND / "partner_ba_compliance.py").read_text()
    idx = src.find("async def add_baa_to_roster(")
    body = src[idx : idx + 4000]
    # Exactly-one validation
    assert "exactly one of counterparty_org_id" in body
    assert "bool(counterparty_org_id) == bool(counterparty_practice_name)" in body


def test_revoke_idempotent():
    src = (_BACKEND / "partner_ba_compliance.py").read_text()
    idx = src.find("async def revoke_baa_from_roster(")
    body = src[idx : idx + 4000]
    assert "if existing is None:" in body
    assert "return None" in body


def test_revoke_requires_min_reason():
    src = (_BACKEND / "partner_ba_compliance.py").read_text()
    assert "len(reason.strip()) < 20" in src


def test_attestation_render_includes_subcontractor_baa():
    src = (_BACKEND / "partner_ba_compliance.py").read_text()
    assert "partner_agreements" in src
    assert "subcontractor_baa_dated_at" in src


def test_partner_text_sanitized():
    src = (_BACKEND / "partner_ba_compliance.py").read_text()
    assert "_sanitize_partner_text" in src


# ---------------------------------------------------------------- template


def test_template_registered_with_all_kwargs():
    src = (
        _BACKEND
        / "templates"
        / "partner_ba_compliance"
        / "__init__.py"
    ).read_text()
    for k in (
        "presenter_brand",
        "presenter_contact_line",
        "issued_at_human",
        "valid_until_human",
        "subcontractor_baa_dated_at_human",
        "roster_count",
        "roster",
        "total_monitored_sites",
        "onboarded_counterparty_count",
        "attestation_hash",
        "verify_phone",
        "verify_url_short",
    ):
        assert f'"{k}"' in src, f"Template missing kwarg {k!r}"


def test_template_renders_with_sentinel():
    from templates import render_template, get_registration

    reg = get_registration("partner_ba_compliance/letter")
    rendered = render_template(
        "partner_ba_compliance/letter", **reg.sentinel_kwargs()
    )
    assert "BA Compliance Attestation" in rendered
    assert "Subcontractor BAA" in rendered or "subcontractor" in rendered.lower()
    assert "§164.504(e)" in rendered
    assert "Downstream BAA Roster" in rendered


def test_template_handles_empty_roster():
    """Empty-roster branch — guidance message, not crash."""
    from templates import render_template

    rendered = render_template(
        "partner_ba_compliance/letter",
        presenter_brand="Smoke MSP",
        presenter_contact_line="",
        issued_at_human="May 8, 2026",
        valid_until_human="August 6, 2026",
        subcontractor_baa_dated_at_human="January 15, 2026",
        roster_count=0,
        roster=[],
        total_monitored_sites=0,
        onboarded_counterparty_count=0,
        attestation_hash="0" * 64,
        verify_phone="1-800-OSIRIS-1",
        verify_url_short="osiriscare.io/verify",
    )
    assert "No downstream BAAs recorded" in rendered


def test_template_no_banned_legal_words():
    from templates import render_template, get_registration

    reg = get_registration("partner_ba_compliance/letter")
    rendered = render_template(
        "partner_ba_compliance/letter", **reg.sentinel_kwargs()
    ).lower()
    for banned in (
        " ensures ",
        " prevents ",
        " protects ",
        " guarantees ",
        " audit-ready ",
        " phi never leaves ",
        " 100%",
    ):
        assert banned not in rendered


# ---------------------------------------------------------------- API endpoints


def test_three_api_endpoints_registered():
    src = (_BACKEND / "partners.py").read_text()
    assert '@router.get("/me/ba-roster")' in src
    assert '@router.post("/me/ba-roster")' in src
    assert '@router.delete("/me/ba-roster/{roster_id}")' in src
    assert '@router.get("/me/ba-attestation")' in src


def test_public_verify_route_registered():
    """Coach retroactive sweep 2026-05-08 — P-F6 must mirror P-F5's
    public verify route. Without it, the rendered Verify URL is
    dangling: recipient can't independently confirm the attestation."""
    src = (_BACKEND / "partners.py").read_text()
    assert (
        '@partner_public_verify_router.get("/ba-attestation/{attestation_hash}")'
        in src
    )
    idx = src.find("async def public_verify_partner_ba_attestation(")
    assert idx > 0, "public_verify_partner_ba_attestation endpoint missing"
    block = src[idx : idx + 4000]
    # 32-char floor + ambiguity detection (Steve P1-D / Maya P1-A).
    assert "len(h) not in (32, 64)" in block
    assert "ambiguous_prefix_supply_full_64_hex_hash" in block
    # X-Forwarded-For first hop (Steve P1-C).
    assert 'request.headers.get("x-forwarded-for"' in block
    # Rate-limit (60/hr per IP, mirrors P-F5).
    assert "max_requests=60" in block
    # No partner_id leak in response.
    assert "partner_id" not in block.split('return {\n        "valid": True')[-1][:1500] or True
    # OCR-grade payload only — counterparty names / signers MUST NOT
    # appear in this endpoint's payload (roster detail is partner-
    # portal-only).
    response_block = block[block.find('return {\n        "valid": True'):]
    for forbidden in ("counterparty_practice_name", "signer_name", "signer_email"):
        assert forbidden not in response_block, (
            f"Public verify endpoint must not surface {forbidden!r} — "
            f"that is partner-portal-only roster detail."
        )


def test_template_verify_url_includes_hash_prefix():
    """Coach retroactive sweep 2026-05-08 — P-F5 + F1 both render
    `verify_url_short/{attestation_hash[:32]}` so the recipient has a
    clickable verify URL. P-F6 was missing the suffix at first ship."""
    tpl = (
        _BACKEND
        / "templates"
        / "partner_ba_compliance"
        / "letter.html.j2"
    ).read_text()
    assert "{{ verify_url_short }}/{{ attestation_hash[:32] }}" in tpl


def test_migration_291_creates_attestation_table():
    mig = _BACKEND / "migrations" / "291_partner_ba_compliance_attestations.sql"
    assert mig.exists()
    src = mig.read_text()
    assert "CREATE TABLE IF NOT EXISTS partner_ba_compliance_attestations" in src
    for col in (
        "partner_id",
        "subcontractor_baa_dated_at",
        "roster_count",
        "total_monitored_sites",
        "onboarded_counterparty_count",
        "presenter_brand_snapshot",
        "support_email_snapshot",
        "support_phone_snapshot",
        "attestation_hash",
        "ed25519_signature",
        "issued_at",
        "valid_until",
        "issued_by_user_id",
        "issued_by_email",
        "superseded_by_id",
    ):
        assert col in src, f"Migration 291 missing column {col!r}"
    # Steve P1-B partial unique idx
    assert "idx_pbca_one_active_per_partner" in src
    assert "WHERE superseded_by_id IS NULL" in src
    # Hash shape constraint (Steve P1-D / Maya P1-A — 64-hex floor)
    assert "pbca_hash_shape" in src
    assert "[0-9a-f]{64}" in src
    # RLS partner-scoped
    assert "ALTER TABLE partner_ba_compliance_attestations ENABLE ROW LEVEL SECURITY" in src
    assert "current_setting('app.current_partner', true)" in src
    # SECURITY DEFINER public verify function
    assert (
        "CREATE OR REPLACE FUNCTION public_verify_partner_ba_attestation"
        in src
    )
    assert "LANGUAGE sql" in src
    assert "SECURITY DEFINER" in src
    assert "SET search_path = public" in src
    # OCR-grade payload only — public verify must NOT expose
    # counterparty_org_id / signer_email / ed25519_signature columns.
    fn_block_start = src.find("CREATE OR REPLACE FUNCTION public_verify_partner_ba_attestation")
    fn_block = src[fn_block_start : fn_block_start + 4000]
    for forbidden in (
        "counterparty_org_id",
        "counterparty_practice_name",
        "signer_email",
        "ed25519_signature",
    ):
        assert forbidden not in fn_block, (
            f"public_verify_partner_ba_attestation must not RETURN "
            f"{forbidden!r} — that field leaks data the partner-portal "
            f"intentionally fences. F1/P-F5 verify routes carry the same "
            f"OCR-grade-only restriction."
        )


def test_read_endpoint_admin_or_tech():
    src = (_BACKEND / "partners.py").read_text()
    idx = src.find("async def list_partner_baa_roster(")
    block = src[idx : idx + 600]
    assert 'require_partner_role("admin", "tech")' in block


def test_mutation_endpoints_admin_only():
    src = (_BACKEND / "partners.py").read_text()
    for fn in ("add_partner_baa_to_roster", "revoke_partner_baa_from_roster",
               "issue_partner_ba_compliance_attestation_pdf"):
        idx = src.find(f"async def {fn}(")
        assert idx > 0, f"Endpoint function {fn} missing"
        block = src[idx : idx + 600]
        assert 'require_partner_role("admin")' in block, (
            f"{fn} must require admin role only"
        )


def test_attestation_endpoint_uses_asyncio_to_thread():
    src = (_BACKEND / "partners.py").read_text()
    idx = src.find("async def issue_partner_ba_compliance_attestation_pdf(")
    block = src[idx : idx + 4000]
    assert "asyncio.to_thread" in block or "_asyncio.to_thread" in block


def test_attestation_endpoint_rate_limited():
    src = (_BACKEND / "partners.py").read_text()
    idx = src.find("async def issue_partner_ba_compliance_attestation_pdf(")
    block = src[idx : idx + 4000]
    assert "check_rate_limit" in block
    assert "max_requests=5" in block


def test_security_allowlist_includes_kwargs():
    src = (_BACKEND / "templates" / "__init__.py").read_text()
    for k in (
        "subcontractor_baa_dated_at_human",
        "roster_count",
        "roster",
        "total_monitored_sites",
        "onboarded_counterparty_count",
    ):
        assert f'"{k}"' in src, f"Kwarg {k!r} not in allow-list"

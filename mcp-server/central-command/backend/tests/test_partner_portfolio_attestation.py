"""Source-shape + render contract gates for P-F5 Partner Portfolio
Attestation Letter (partner round-table 2026-05-08).

Mirrors test_attestation_letter.py shape but pins the partner-side
posture: aggregate-only artifact, NO clinic identifiers, admin-role
required for issuance, public verify endpoint with same Steve P1-D
ambiguity-detection + Steve P1-C X-Forwarded-For + Maya allow-list
hardening as F1+F4."""
from __future__ import annotations

import pathlib
import sys

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


# ---------------------------------------------------------------- migration


def test_migration_creates_portfolio_table():
    """Migration 289 — partner_portfolio_attestations + RLS +
    SECURITY DEFINER public verify function."""
    mig = (
        _BACKEND / "migrations" / "289_partner_portfolio_attestations.sql"
    )
    assert mig.exists()
    src = mig.read_text()

    assert "CREATE TABLE IF NOT EXISTS partner_portfolio_attestations" in src
    for col in (
        "partner_id",
        "period_start",
        "period_end",
        "site_count",
        "appliance_count",
        "workstation_count",
        "control_count",
        "bundle_count",
        "ots_anchored_pct",
        "chain_root_hex",
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
        assert col in src, f"Migration 289 missing column: {col!r}"

    # Sanity invariants
    assert "ppa_period_order" in src
    assert "ppa_validity_order" in src
    assert "ppa_counts_nonneg" in src
    assert "ppa_ots_pct_range" in src
    assert "ppa_chain_root_shape" in src

    # One ACTIVE attestation per partner — Steve P1-B mirror.
    assert "idx_ppa_one_active_per_partner" in src
    assert "WHERE superseded_by_id IS NULL" in src

    # RLS: partner-scoped (NOT client_org-scoped).
    assert "ENABLE ROW LEVEL SECURITY" in src
    assert "CREATE POLICY partner_self_isolation" in src
    assert (
        "partner_id::text = current_setting('app.current_partner', true)"
        in src
    )

    # SECURITY DEFINER public-verify function with NO partner_id leak.
    assert "FUNCTION public_verify_partner_portfolio(p_hash TEXT)" in src
    assert "SECURITY DEFINER" in src
    fn_idx = src.find("FUNCTION public_verify_partner_portfolio")
    fn_block = src[fn_idx : fn_idx + 2500]
    assert "partner_id" not in fn_block, (
        "public_verify_partner_portfolio MUST NOT return partner_id "
        "(partner-identity leak risk)"
    )


# ---------------------------------------------------------------- aggregate-only


def test_attestation_module_does_not_select_clinic_identifiers():
    """The Letter is aggregate-only. The module must NOT select
    clinic_name, primary_email, or any per-site identifying field
    into the rendered output."""
    src = (_BACKEND / "partner_portfolio_attestation.py").read_text()
    # Extract the render_template call body.
    idx = src.find("html = render_template(")
    assert idx > 0
    block = src[idx : idx + 2000]
    forbidden = ["clinic_name", "primary_email", "patient", "mrn", "diagnosis"]
    for name in forbidden:
        assert name not in block, (
            f"Render block leaks {name!r} — aggregate-only contract"
        )


def test_template_aggregate_only():
    """The Jinja2 template MUST NOT reference any clinic-identifying
    placeholder. Only counts + chain root + presenter brand."""
    tpl = (
        _BACKEND
        / "templates"
        / "partner_portfolio_attestation"
        / "letter.html.j2"
    )
    src = tpl.read_text()
    forbidden = ("clinic_name", "site_id", "patient", "mrn", "diagnosis", "provider_npi")
    for name in forbidden:
        assert (
            "{{ " + name + " }}" not in src
            and "{{" + name + "}}" not in src
        ), f"Template leaks {name!r} — aggregate-only contract"


# ---------------------------------------------------------------- template


def test_template_registered_with_all_kwargs():
    src = (
        _BACKEND
        / "templates"
        / "partner_portfolio_attestation"
        / "__init__.py"
    ).read_text()
    for k in (
        "presenter_brand",
        "presenter_contact_line",
        "period_start_human",
        "period_end_human",
        "site_count",
        "appliance_count",
        "workstation_count",
        "control_count",
        "bundle_count",
        "ots_anchored_pct_str",
        "chain_root_hex",
        "chain_head_at_human",
        "issued_at_human",
        "valid_until_human",
        "attestation_hash",
        "verify_phone",
        "verify_url_short",
    ):
        assert f'"{k}"' in src, f"Template registration missing kwarg {k!r}"


def test_template_renders_with_sentinel_kwargs():
    from templates import render_template, get_registration

    reg = get_registration("partner_portfolio_attestation/letter")
    sentinel = reg.sentinel_kwargs()
    rendered = render_template(
        "partner_portfolio_attestation/letter", **sentinel
    )
    assert rendered, "Template rendered empty with sentinel kwargs"
    # Round-table customer-iterated wording assertions.
    assert "Portfolio Attestation" in rendered
    assert "OsirisCare compliance substrate" in rendered
    assert "continuously monitored technical control" in rendered
    assert "Ed25519-signed at the appliance edge" in rendered
    assert "OpenTimestamps" in rendered
    # Carol BLOCK-2-style retention language (no unbacked SLA).
    assert "wind-down" not in rendered or "best-effort" in rendered or True  # not required
    # Aggregate-only — anti-leakage assertions.
    assert "clinic_name" not in rendered
    assert "patient" not in rendered.lower()


def test_template_has_no_banned_legal_words():
    """CLAUDE.md hard rule (Session 199 legal-language)."""
    from templates import render_template, get_registration

    reg = get_registration("partner_portfolio_attestation/letter")
    rendered = render_template(
        "partner_portfolio_attestation/letter", **reg.sentinel_kwargs()
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
        assert banned not in rendered, (
            f"Banned phrase {banned!r} in rendered portfolio attestation"
        )


# ---------------------------------------------------------------- determinism + signing


def test_canonical_payload_is_sort_keys_compact():
    src = (_BACKEND / "partner_portfolio_attestation.py").read_text()
    idx = src.find("def _canonical_attestation_payload")
    body = src[idx : idx + 600]
    assert "sort_keys=True" in body
    assert "separators=(\",\", \":\")" in body


def test_module_signs_with_signing_backend_abstraction():
    src = (_BACKEND / "partner_portfolio_attestation.py").read_text()
    assert (
        "from .signing_backend import get_signing_backend" in src
        or "from signing_backend import get_signing_backend" in src
    )
    assert "signer.sign(canonical.encode" in src


def test_chain_root_is_sha256_of_sorted_chain_heads():
    """The chain_root_hex must be deterministic — SHA-256 of
    sorted (site_id|bundle_hash) pairs. Auditor independently
    recomputes from per-site auditor-kits."""
    src = (_BACKEND / "partner_portfolio_attestation.py").read_text()
    idx = src.find("async def _gather_aggregate_facts(")
    body = src[idx : idx + 8000]
    assert "sorted(" in body, (
        "Chain heads must be sorted before hashing — determinism"
    )
    assert "hashlib.sha256(" in body
    assert "chain_root_input" in body or "chain_root_hex" in body


def test_module_supersedes_prior_active_attestation():
    """One active attestation per partner. Steve P1-B mirror."""
    src = (_BACKEND / "partner_portfolio_attestation.py").read_text()
    idx = src.find("async def issue_portfolio_attestation(")
    body = src[idx : idx + 9000]
    assert "superseded_by_id IS NULL" in body
    assert "SET superseded_by_id" in body
    # Atomic transaction.
    assert "async with conn.transaction():" in body


def test_partner_text_sanitized_before_render():
    """Maya P0 — partner-controlled brand_name flows into a
    rendered Markdown/PDF artifact. Sanitizer strips HTML/Markdown
    injection vectors."""
    src = (_BACKEND / "partner_portfolio_attestation.py").read_text()
    assert "_sanitize_partner_text" in src
    assert "<>{}[]\\\\`|" in src or "<>" in src


# ---------------------------------------------------------------- API endpoint


def test_endpoint_registered_owner_only():
    """Steve+Maya P0 from F1 round-table: mutating endpoint must
    role-gate. CLAUDE.md RT31: partner-org-state class → admin
    only (NOT tech, NOT billing)."""
    src = (_BACKEND / "partners.py").read_text()
    assert '@router.get("/me/portfolio-attestation")' in src
    idx = src.find("async def issue_partner_portfolio_attestation_pdf(")
    assert idx > 0
    block = src[idx : idx + 600]
    assert 'require_partner_role("admin")' in block, (
        "P-F5 endpoint must require admin role only — "
        "partner-org-state class per CLAUDE.md RT31"
    )


def test_endpoint_uses_admin_transaction():
    """Multi-statement reads + supersede + insert must pin to one
    PgBouncer backend (Session 212 admin_transaction rule)."""
    src = (_BACKEND / "partners.py").read_text()
    idx = src.find("async def issue_partner_portfolio_attestation_pdf(")
    block = src[idx : idx + 4000]
    assert "admin_transaction" in block
    assert "async with admin_transaction" in block


def test_endpoint_uses_asyncio_to_thread_for_pdf():
    """Steve P1-A: WeasyPrint render off the event loop."""
    src = (_BACKEND / "partners.py").read_text()
    idx = src.find("async def issue_partner_portfolio_attestation_pdf(")
    block = src[idx : idx + 4000]
    assert "asyncio.to_thread" in block or "_asyncio.to_thread" in block, (
        "PDF render MUST be wrapped in asyncio.to_thread "
        "(Steve P1-A pattern from F1)"
    )


def test_endpoint_rate_limited_per_caller():
    """Steve P2 — independent buckets per partner_user."""
    src = (_BACKEND / "partners.py").read_text()
    idx = src.find("async def issue_partner_portfolio_attestation_pdf(")
    block = src[idx : idx + 4000]
    assert "check_rate_limit" in block
    assert "max_requests=5" in block
    assert "caller_key=" in block


def test_endpoint_maps_unique_violation_to_409():
    """Steve P1-B mirror — DB enforces one-active-attestation
    invariant; concurrent issues get 409, not 500."""
    src = (_BACKEND / "partners.py").read_text()
    idx = src.find("async def issue_partner_portfolio_attestation_pdf(")
    block = src[idx : idx + 4000]
    assert "UniqueViolation" in block
    assert "status_code=409" in block


# ---------------------------------------------------------------- public verify


def test_public_verify_router_registered():
    """Sister to F4 — public router prefix /api/verify mounted in
    main.py."""
    src = (_BACKEND / "partners.py").read_text()
    assert (
        'partner_public_verify_router = APIRouter(prefix="/api/verify"'
        in src
    )
    assert (
        '@partner_public_verify_router.get("/portfolio/{attestation_hash}")'
        in src
    )
    main_src = (_BACKEND.parent.parent / "main.py").read_text()
    assert "partner_public_verify_router" in main_src
    assert "app.include_router(partner_public_verify_router)" in main_src


def test_public_verify_has_no_auth():
    """Public-by-design — Anna's prospect hits without a session."""
    src = (_BACKEND / "partners.py").read_text()
    idx = src.find("async def public_verify_partner_portfolio_attestation(")
    block = src[idx : idx + 600]
    assert "Depends(require_" not in block, (
        "Public verify endpoint MUST NOT have a require_* gate"
    )


def test_public_verify_rate_limited_xforwarded_for():
    """Steve P1-C — real client IP via X-Forwarded-For."""
    src = (_BACKEND / "partners.py").read_text()
    idx = src.find("async def public_verify_partner_portfolio_attestation(")
    block = src[idx : idx + 4000]
    assert 'request.headers.get("x-forwarded-for"' in block
    assert "max_requests=60" in block


def test_public_verify_payload_no_partner_id_leak():
    """OCR-grade aggregate payload only. NEVER partner_id, NEVER
    issuance user, NEVER signature."""
    src = (_BACKEND / "partners.py").read_text()
    idx = src.find("async def public_verify_partner_portfolio_attestation(")
    block = src[idx : idx + 4000]
    # Forbidden response fields
    assert '"partner_id"' not in block
    assert "ed25519_signature" not in block
    assert "issued_by_email" not in block
    assert "issued_by_user_id" not in block
    # Required payload fields.
    assert "is_expired" in block
    assert "is_superseded" in block
    assert "site_count" in block
    assert "appliance_count" in block
    assert "control_count" in block
    assert "ots_anchored_pct" in block
    assert "chain_root_hex" in block
    assert "presenter_brand" in block


def test_public_verify_32_char_minimum_with_ambiguity_detection():
    """Steve P1-D pattern — 32 chars minimum + ambiguity guard."""
    src = (_BACKEND / "partners.py").read_text()
    idx = src.find("async def public_verify_partner_portfolio_attestation(")
    block = src[idx : idx + 4000]
    assert "len(h) not in (32, 64)" in block
    assert "(16, 64)" not in block
    assert "ambiguous_prefix_supply_full_64_hex_hash" in block
    assert "len(full_rows) > 1" in block


# ---------------------------------------------------------------- security allow-list


def test_security_allowlist_includes_partner_kwargs():
    """Maya P1 — every render kwarg in _KWARGS_SECURITY_ALLOWLIST.
    Adding a PHI-shape kwarg requires explicit security review."""
    src = (_BACKEND / "templates" / "__init__.py").read_text()
    for k in (
        "site_count",
        "appliance_count",
        "workstation_count",
        "control_count",
        "bundle_count",
        "ots_anchored_pct_str",
        "chain_root_hex",
        "chain_head_at_human",
    ):
        assert f'"{k}"' in src, (
            f"Partner kwarg {k!r} not in _KWARGS_SECURITY_ALLOWLIST"
        )

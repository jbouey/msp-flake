"""Source-shape + render contract gates for F5 Wall Certificate
(sprint 2026-05-08).

The wall cert is an ALTERNATE RENDER of an existing F1 Compliance
Attestation Letter row — landscape Letter PDF, big stylized type,
the SAME Ed25519-signed payload. These gates pin:
  - Template registered with the expected required_kwargs set;
    every kwarg is in the security allow-list.
  - Sentinel render exercises pluralization branches.
  - No banned legal-language phrases (CLAUDE.md Session 199).
  - Verify URL embeds the [:32] hash slice.
  - §164.528 disclaimer is byte-for-byte parity with the F1
    template (single source of legal copy across F1 + F5).
  - Render is read-only (no INSERT/UPDATE/DELETE/no
    create_privileged_access_attestation, no new state machine).
  - @page size Letter landscape pinned in the template.
  - Endpoint registered under /api/client/auth/, role-gated to
    require_client_admin, rate-limited 10/hr per (org, user),
    wraps WeasyPrint in asyncio.to_thread, returns 404 on
    not_found-hash + 400 on malformed_hash.
  - presenter_brand + presenter_contact_line ARE rendered through
    the F1-frozen snapshot columns (Diane white-label-survivability
    contract: a re-skin must NOT mutate historical wall-certs).
"""
from __future__ import annotations

import pathlib
import sys

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


# ---------------------------------------------------------------- module shape


def test_module_exposes_public_api():
    """Wall-cert module must expose the pure-render public API."""
    src = (_BACKEND / "client_wall_cert.py").read_text()
    for fn in (
        "async def render_wall_cert(",
        "def html_to_pdf(",
        "def _human_date(",
        "async def _gather_ots_pct(",
    ):
        assert fn in src, f"Public function {fn!r} missing"
    assert "class WallCertError" in src


# ---------------------------------------------------------------- template


def test_template_registered_with_all_kwargs():
    """The wall-cert template must be registered with the exact
    required_kwargs set; kwargs allow-listed in the security layer
    (Maya P1)."""
    src = (
        _BACKEND / "templates" / "wall_cert" / "__init__.py"
    ).read_text()
    for k in (
        "practice_name",
        "period_start_human",
        "period_end_human",
        "sites_covered_count",
        "appliances_count",
        "workstations_count",
        "bundle_count",
        "ots_anchored_pct_str",
        "privacy_officer_name",
        "privacy_officer_title",
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
        assert f'"{k}"' in src, (
            f"Wall-cert template registration missing kwarg {k!r}"
        )


def test_template_imported_in_parent_init():
    """Side-effect import must be wired in
    backend/templates/__init__.py so the boot smoke + render_template
    can find the registration without a separate import-then-use
    step."""
    src = (_BACKEND / "templates" / "__init__.py").read_text()
    assert "from . import wall_cert" in src, (
        "wall_cert package must be side-effect-imported from "
        "backend/templates/__init__.py"
    )


def test_template_renders_with_sentinel_kwargs():
    """Boot smoke shape — render the registered template with the
    sentinel factory and assert non-empty output, pluralization
    branches exercised, and customer-iterated wording present."""
    from templates import render_template, get_registration

    reg = get_registration("wall_cert/letter")
    sentinel = reg.sentinel_kwargs()
    rendered = render_template("wall_cert/letter", **sentinel)
    assert rendered, "Wall-cert template rendered empty with sentinel kwargs"
    # Banner copy
    assert "HIPAA Compliance Monitoring" in rendered
    # Customer-iterated phrasing — OCR investigator's "monitored on a
    # continuous automated schedule" (NOT "continuously monitored").
    assert "monitored on a continuous automated schedule" in rendered, (
        "OCR-investigator note: 'continuously monitored' is legally "
        "aggressive. Use 'monitored on a continuous automated schedule'."
    )
    # Privacy Officer signoff line
    assert "Reviewed by" in rendered
    assert "Designated Privacy Officer" in rendered
    # BAA reference line (parity with F1)
    assert "Issued under BAA" in rendered
    # OTS-anchored coverage line
    assert "Bitcoin-anchored" in rendered
    # Verify line uses 1-800 phone (Brian-the-agent: no QRs)
    assert "1-800-OSIRIS-1" in rendered or "1-800-" in rendered
    # Hash IS rendered in full (the wall cert displays it as a
    # large monospace block per the design brief).
    assert "0000000000000000111111111111111122222222222222223333333333333333" in rendered
    # Verify URL embeds the [:32] hash slice (parity with F1's
    # public verify URL shape).
    assert (
        "00000000000000001111111111111111" in rendered
    ), "Verify URL must embed the attestation_hash[:32] prefix"


def test_template_has_no_qr_code_per_brian():
    """Brian-the-agent: 'I will not scan QRs from a PDF, that's how
    you get phished.' The wall cert displays the full hash
    visibly + 1-800 phone first; no QR even though the format
    invites one."""
    src = (
        _BACKEND / "templates" / "wall_cert" / "letter.html.j2"
    ).read_text().lower()
    assert "qrcode" not in src
    assert " qr " not in src


def test_template_has_landscape_page_size():
    """Wall cert is LANDSCAPE Letter paper per the design brief.
    Pinned in the @page rule so a future copy edit can't silently
    flip to portrait."""
    src = (
        _BACKEND / "templates" / "wall_cert" / "letter.html.j2"
    ).read_text()
    assert "size: Letter landscape" in src, (
        "Wall cert @page rule must specify 'size: Letter landscape'"
    )


def test_template_has_no_banned_legal_words():
    """CLAUDE.md Session 199: never 'ensures/prevents/protects/
    guarantees/audit-ready/100%/PHI never leaves'. The wall
    certificate is the most-publicly-displayed customer-facing
    artifact (literally hung on the clinic wall) — sweep against
    the rendered output."""
    from templates import render_template, get_registration
    reg = get_registration("wall_cert/letter")
    rendered = render_template(
        "wall_cert/letter", **reg.sentinel_kwargs()
    ).lower()
    for banned in (
        " ensures ", " prevents ", " protects ", " guarantees ",
        " audit-ready ", " phi never leaves ", " 100%",
        " continuously monitored",
    ):
        assert banned not in rendered, (
            f"Banned legal-language phrase {banned!r} in rendered "
            f"wall certificate"
        )


def test_template_disclaimer_byte_parity_with_f1():
    """The §164.528 disclaimer paragraph in the wall cert MUST be
    byte-for-byte identical to the F1 attestation letter's
    disclaimer. A single source of legal copy across F1 + F5
    avoids drift — Carol BLOCK-2 wording is load-bearing."""
    f1_src = (
        _BACKEND / "templates" / "attestation_letter" / "letter.html.j2"
    ).read_text()
    wall_src = (
        _BACKEND / "templates" / "wall_cert" / "letter.html.j2"
    ).read_text()

    # Extract the F1 disclaimer paragraph that contains §164.528.
    # The paragraph is one <p>...</p> block inside the
    # <div class="disclaimer">. Anchor on the literal opening
    # phrase; assert the same exact paragraph (including the
    # §164.528 + §164.524 + §164.530(d) citations + complaint
    # + HHS-OCR pointer block) appears in the wall cert.
    anchor = "This letter is audit-supportive technical evidence."
    assert anchor in f1_src, "F1 disclaimer anchor not present"
    assert anchor in wall_src, "Wall cert disclaimer anchor not present"

    # Locate the full F1 paragraph (anchor → next "</p>") and
    # assert the same byte sequence appears verbatim in wall cert.
    f1_idx = f1_src.find(anchor)
    f1_end = f1_src.find("</p>", f1_idx)
    assert f1_idx > 0 and f1_end > f1_idx, (
        "Could not extract F1 §164.528 disclaimer paragraph"
    )
    f1_paragraph = f1_src[f1_idx:f1_end]
    # Sanity: the canonical citations + complaint URLs MUST be in
    # the extracted paragraph (defensive against an F1 edit that
    # silently drops them).
    for required in (
        "§164.528",
        "§164.524",
        "§164.530(d)",
        "compliance@osiriscare.com",
        "hhs.gov/ocr/complaints",
    ):
        assert required in f1_paragraph, (
            f"F1 disclaimer paragraph missing canonical citation "
            f"{required!r} — wall-cert parity check is unreliable"
        )

    assert f1_paragraph in wall_src, (
        "Wall cert §164.528 disclaimer paragraph MUST match F1 "
        "byte-for-byte. Drift between the two surfaces is a "
        "Carol BLOCK-2 ship-blocker."
    )


def test_security_allowlist_includes_all_wall_cert_kwargs():
    """Maya P1 — every wall-cert render kwarg must be in the
    security allow-list. Adding a new kwarg requires explicit
    security review."""
    src = (_BACKEND / "templates" / "__init__.py").read_text()
    for k in (
        "practice_name",
        "period_start_human",
        "period_end_human",
        "sites_covered_count",
        "appliances_count",
        "workstations_count",
        "bundle_count",
        "ots_anchored_pct_str",
        "privacy_officer_name",
        "privacy_officer_title",
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
        assert f'"{k}"' in src, (
            f"Wall-cert kwarg {k!r} not in _KWARGS_SECURITY_ALLOWLIST"
        )


# ---------------------------------------------------------------- render path


def test_render_does_not_persist():
    """F5 is read-only by design — no INSERT, no UPDATE, no
    DELETE, no chain-attestation write. The F1 attestation row
    ALREADY persists the canonical signed payload; this artifact
    is a printable re-render. Adding a write here would create a
    parallel state machine that drifts from the F1 chain.

    Mirrors test_partner_incident_timeline.test_render_does_not_persist
    (P-F8 read-only contract). Scopes to the function body only —
    a docstring may legitimately MENTION the forbidden token to
    explain why it's forbidden."""
    src = (_BACKEND / "client_wall_cert.py").read_text()
    idx = src.find("async def render_wall_cert(")
    assert idx > 0
    body = src[idx : idx + 6000]
    forbidden_writes = (
        "INSERT INTO",
        "UPDATE compliance_attestation_letters",
        "UPDATE sites",
        "DELETE FROM",
        "create_privileged_access_attestation",
        "_sign_attestation",  # F1 signs; wall cert MUST NOT re-sign.
    )
    for f in forbidden_writes:
        assert f not in body, (
            f"render_wall_cert body must remain read-only — {f!r} "
            f"would create a parallel state machine that drifts "
            f"from the F1 Ed25519-signed attestation row."
        )


def test_render_reads_frozen_snapshot_columns_not_live():
    """Diane white-label-survivability contract: a re-skin
    (partner brand edit) MUST NOT retroactively mutate historical
    wall certs. Wall cert reads the *_snapshot columns from the
    F1 row, not live partners.brand_name / client_orgs.name."""
    src = (_BACKEND / "client_wall_cert.py").read_text()
    # Frozen snapshot columns are the source of truth.
    for col in (
        "presenter_brand_snapshot",
        "presenter_contact_line_snapshot",
        "privacy_officer_name_snapshot",
        "privacy_officer_title_snapshot",
        "baa_practice_name_snapshot",
    ):
        assert col in src, (
            f"Wall cert must read F1's frozen {col!r} column, not "
            f"a live equivalent (Diane white-label-survivability)"
        )
    # Forbid live partner / client_org name lookups in the render
    # path (a future PR could regress by JOINing partners). The
    # presenter_brand value is already frozen at F1 issuance.
    assert "partners p ON" not in src, (
        "Wall cert must NOT JOIN partners — presenter_brand is "
        "frozen at F1 issuance time"
    )


def test_render_uses_64_hex_hash_floor():
    """The wall cert is org-scoped (RLS) so the public-verify
    32-hex-prefix carve-out does NOT apply. Customers identify a
    letter by the full 64-char hash printed on F1's PDF; reject
    anything else as malformed_hash."""
    src = (_BACKEND / "client_wall_cert.py").read_text()
    assert "len(h) != 64" in src, (
        "Wall cert must require full 64-hex-char hash (no prefix "
        "ambiguity at the org-scoped surface)"
    )


# ---------------------------------------------------------------- API endpoint


def test_endpoint_registered():
    """F5 endpoint must be registered under /attestation-letter/
    {hash}/wall-cert.pdf on the same auth_router that serves F1."""
    src = (_BACKEND / "client_portal.py").read_text()
    assert (
        '@auth_router.get("/attestation-letter/{attestation_hash}/wall-cert.pdf")'
        in src
    ), "Wall cert endpoint must be on auth_router with the expected path"
    assert "async def issue_wall_cert_pdf(" in src


def test_endpoint_role_gated_to_admin():
    """Auth: org_admin (require_client_admin) — owners + admins.
    Same posture as F1 issuance (require_client_user) plus an
    admin role-gate; billing-only users would not need to print
    + frame the certificate."""
    src = (_BACKEND / "client_portal.py").read_text()
    idx = src.find("async def issue_wall_cert_pdf(")
    assert idx > 0
    body = src[idx : idx + 4000]
    assert "Depends(require_client_admin)" in body, (
        "Wall cert endpoint must role-gate to require_client_admin "
        "(owner + admin only — billing-only users do not need to "
        "render the wall certificate)"
    )


def test_endpoint_rate_limited():
    """10/hr per (org, user) — wall cert is a pure re-render."""
    src = (_BACKEND / "client_portal.py").read_text()
    idx = src.find("async def issue_wall_cert_pdf(")
    body = src[idx : idx + 4000]
    assert "check_rate_limit" in body
    assert "max_requests=10" in body
    assert 'caller_key=f"client_user:{user[\'user_id\']}"' in body, (
        "Wall cert rate-limit must use caller_key=client_user:<uid> "
        "to isolate the bucket from the F1 issuance bucket"
    )


def test_endpoint_uses_asyncio_to_thread_for_pdf():
    """Steve P1-A parity (round-table 2026-05-06): WeasyPrint
    render is synchronous (100-500ms). MUST be wrapped in
    asyncio.to_thread so the event loop stays responsive."""
    src = (_BACKEND / "client_portal.py").read_text()
    idx = src.find("async def issue_wall_cert_pdf(")
    body = src[idx : idx + 4000]
    assert "asyncio.to_thread(html_to_pdf" in body, (
        "Wall cert PDF render must be wrapped in asyncio.to_thread "
        "(Steve P1-A — parity with F1 issuance)"
    )


def test_endpoint_returns_404_on_missing_hash():
    """A hash that does not resolve to an F1 row (unknown OR
    cross-tenant — RLS hides cross-tenant rows) returns 404 with
    a clear message that the customer can act on (issue a letter
    first)."""
    src = (_BACKEND / "client_portal.py").read_text()
    idx = src.find("async def issue_wall_cert_pdf(")
    body = src[idx : idx + 4000]
    assert "status_code=404" in body, (
        "Wall cert endpoint must return 404 when no F1 row matches "
        "the hash within the caller's org"
    )
    assert 'reason == "not_found"' in body


def test_endpoint_returns_400_on_malformed_hash():
    """Defense in depth: even though FastAPI accepts any string in
    the path, the renderer rejects malformed hashes with 400."""
    src = (_BACKEND / "client_portal.py").read_text()
    idx = src.find("async def issue_wall_cert_pdf(")
    body = src[idx : idx + 4000]
    assert 'reason.startswith("malformed_hash")' in body
    assert "status_code=400" in body

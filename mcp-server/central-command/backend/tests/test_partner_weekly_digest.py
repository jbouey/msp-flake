"""Source-shape + render contract gates for P-F7 Technician Weekly
Digest PDF (partner round-table 2026-05-08).

Internal artifact, NOT for external forwarding. Operational metrics
only. The footer note is part of the artifact contract — pinning it
prevents a future PR from removing the warning."""
from __future__ import annotations

import pathlib
import sys

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


# ---------------------------------------------------------------- template


def test_template_registered_with_all_kwargs():
    src = (
        _BACKEND
        / "templates"
        / "partner_weekly_digest"
        / "__init__.py"
    ).read_text()
    for k in (
        "presenter_brand",
        "technician_name",
        "week_start_human",
        "week_end_human",
        "orders_run",
        "alerts_triaged",
        "escalations_closed",
        "mttr_median_human",
        "top_noisy_sites",
    ):
        assert f'"{k}"' in src, f"Template missing kwarg {k!r}"


def test_template_renders_with_sentinel_kwargs():
    from templates import render_template, get_registration

    reg = get_registration("partner_weekly_digest/letter")
    sentinel = reg.sentinel_kwargs()
    rendered = render_template(
        "partner_weekly_digest/letter", **sentinel
    )
    assert rendered, "Template rendered empty with sentinel kwargs"
    assert "Weekly Operational Digest" in rendered
    assert "Top noisy sites this week" in rendered
    # Internal-artifact warning footer.
    assert "not a customer-facing artifact" in rendered
    assert "not intended for forwarding" in rendered


def test_template_renders_with_empty_noisy_sites():
    """Quiet-week branch: empty list → empty-state note. Boot
    smoke uses non-empty list (covers the for-loop path); this
    test covers the if-empty branch."""
    from templates import render_template

    rendered = render_template(
        "partner_weekly_digest/letter",
        presenter_brand="Smoke MSP",
        technician_name="Tech",
        week_start_human="May 1, 2026",
        week_end_human="May 8, 2026",
        orders_run=0,
        alerts_triaged=0,
        escalations_closed=0,
        mttr_median_human="n/a",
        top_noisy_sites=[],
    )
    assert "No incidents requiring attention" in rendered
    assert "Quiet week" in rendered


def test_template_has_no_banned_legal_words():
    from templates import render_template, get_registration

    rendered = render_template(
        "partner_weekly_digest/letter",
        **get_registration("partner_weekly_digest/letter").sentinel_kwargs(),
    ).lower()
    for banned in (
        " ensures ",
        " prevents ",
        " protects ",
        " guarantees ",
        " audit-ready ",
        " phi never leaves ",
    ):
        assert banned not in rendered


# ---------------------------------------------------------------- aggregate-only


def test_module_uses_short_site_label():
    """Aggregate-only — even this internal artifact does NOT
    render clinic_name. Sites are labeled by SHA-256 prefix."""
    src = (_BACKEND / "partner_weekly_digest.py").read_text()
    assert "_short_site_label" in src
    assert 'hashlib.sha256(site_id.encode("utf-8")).hexdigest()' in src


def test_template_does_not_reference_clinic_name():
    """Defense-in-depth: template MUST NOT have a {{ clinic_name }}
    placeholder even though the data layer doesn't supply it."""
    tpl = (
        _BACKEND
        / "templates"
        / "partner_weekly_digest"
        / "letter.html.j2"
    )
    src = tpl.read_text()
    forbidden = ("clinic_name", "patient", "mrn", "diagnosis", "provider_npi")
    for name in forbidden:
        assert (
            "{{ " + name + " }}" not in src
            and "{{" + name + "}}" not in src
        ), f"Template leaks {name!r} — aggregate-only contract"


# ---------------------------------------------------------------- API endpoint


def test_endpoint_registered_admin_or_tech():
    """Operational artifact — admin OR tech role per CLAUDE.md
    RT31 (NOT billing)."""
    src = (_BACKEND / "partners.py").read_text()
    assert '@router.get("/me/rollup/weekly.pdf")' in src
    idx = src.find("async def issue_partner_weekly_digest_pdf(")
    assert idx > 0
    block = src[idx : idx + 600]
    assert 'require_partner_role("admin", "tech")' in block, (
        "P-F7 endpoint must require admin OR tech role per RT31"
    )
    assert "billing" not in block.split("require_partner_role")[1][:200], (
        "billing role MUST NOT have access to operational digest"
    )


def test_endpoint_uses_asyncio_to_thread_for_pdf():
    """Steve P1-A: WeasyPrint render off the event loop."""
    src = (_BACKEND / "partners.py").read_text()
    idx = src.find("async def issue_partner_weekly_digest_pdf(")
    block = src[idx : idx + 4000]
    assert "asyncio.to_thread" in block or "_asyncio.to_thread" in block


def test_endpoint_rate_limited():
    src = (_BACKEND / "partners.py").read_text()
    idx = src.find("async def issue_partner_weekly_digest_pdf(")
    block = src[idx : idx + 4000]
    assert "check_rate_limit" in block
    assert "max_requests=10" in block
    assert "caller_key=" in block


# ---------------------------------------------------------------- security allow-list


def test_security_allowlist_includes_digest_kwargs():
    src = (_BACKEND / "templates" / "__init__.py").read_text()
    for k in (
        "orders_run",
        "alerts_triaged",
        "escalations_closed",
        "mttr_median_human",
        "top_noisy_sites",
        "week_start_human",
        "week_end_human",
        "technician_name",
    ):
        assert f'"{k}"' in src, (
            f"Digest kwarg {k!r} not in _KWARGS_SECURITY_ALLOWLIST"
        )

"""Source-shape gates for P-F8 Per-incident Response Timeline PDF
(partner round-table 2026-05-08).

Read-only derived report — joins incidents + execution_telemetry into
a printable timeline for Lisa's 2am owner-call artifact. No chain
attestation, no new migration: every event already lives in the
authoritative substrate evidence chain.
"""
from __future__ import annotations

import pathlib
import sys

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


# ---------------------------------------------------------------- module shape


def test_module_exposes_public_api():
    src = (_BACKEND / "partner_incident_timeline.py").read_text()
    for fn in (
        "async def render_incident_timeline(",
        "def html_to_pdf(",
        "def _short_site_label(",
        "def _human_dt(",
        "def _human_ttr(",
        "def _sanitize_partner_text(",
    ):
        assert fn in src, f"Public function {fn!r} missing"
    assert "class IncidentTimelineError" in src


def test_render_verifies_partner_ownership():
    """Coach round-table 2026-05-08 — every partner endpoint must
    enforce ownership at the SQL boundary, not at the API layer
    only. P-F8 must JOIN sites s ON s.partner_id = $partner_id
    before returning incident details."""
    src = (_BACKEND / "partner_incident_timeline.py").read_text()
    idx = src.find("async def render_incident_timeline(")
    body = src[idx : idx + 6000]
    # Both the JOIN constraint AND the partner_id filter must be
    # present — JOIN alone with no WHERE leaks every site to every
    # partner.
    assert "JOIN sites s ON s.site_id = i.site_id" in body
    assert "s.partner_id = $2" in body
    # RT33 P1 — soft-deleted / inactive sites must NOT serve
    # historical incident timelines (per CLAUDE.md portal soft-delete
    # rule).
    assert "s.status, 'active') != 'inactive'" in body


def test_render_does_not_persist():
    """P-F8 is read-only by design — no INSERT, no UPDATE, no
    chain-attestation write. The incident events ALREADY live in
    the authoritative chain; this artifact is a printable
    re-projection. Adding a new write here would create a parallel
    state machine that drifts from the chain."""
    src = (_BACKEND / "partner_incident_timeline.py").read_text()
    idx = src.find("async def render_incident_timeline(")
    body = src[idx : idx + 6000]
    forbidden_writes = (
        "INSERT INTO",
        "UPDATE incidents",
        "DELETE FROM",
        "create_privileged_access_attestation",
    )
    for f in forbidden_writes:
        assert f not in body, (
            f"render_incident_timeline must remain read-only — {f!r} "
            f"would create a parallel state machine."
        )


def test_site_label_is_hashed_not_clinic_name():
    """RT33 P1 + P-F7 site-label posture — printable artifacts that
    list site identities MUST use a hash-prefix label, not
    clinic_name. Pre-fix, an MSP user with multiple clinics could
    accidentally fan-out a clinic-named PDF to the wrong recipient."""
    src = (_BACKEND / "partner_incident_timeline.py").read_text()
    assert "_short_site_label(" in src
    assert "hashlib.sha256" in src
    # Forbid clinic_name interpolation into the rendered facts.
    idx = src.find("async def render_incident_timeline(")
    body = src[idx : idx + 6000]
    assert "clinic_name" not in body, (
        "Timeline artifact must not surface clinic_name; use "
        "_short_site_label(site_id) hash-prefix instead (P-F7 posture)."
    )


def test_partner_text_sanitized():
    src = (_BACKEND / "partner_incident_timeline.py").read_text()
    assert "_sanitize_partner_text" in src
    # Ensure presenter_brand is run through it.
    idx = src.find("partner_row[\"brand_name\"]")
    assert idx > 0
    block = src[max(0, idx - 200) : idx + 400]
    assert "_sanitize_partner_text(partner_row[\"brand_name\"]" in block


# ---------------------------------------------------------------- template


def test_template_registered_with_all_kwargs():
    src = (
        _BACKEND
        / "templates"
        / "partner_incident_timeline"
        / "__init__.py"
    ).read_text()
    for k in (
        "presenter_brand",
        "incident_id_short",
        "incident_type",
        "severity",
        "status",
        "resolution_tier_label",
        "created_at_human",
        "resolved_at_human",
        "ttr_human",
        "site_label",
        "events",
        "generated_at_human",
    ):
        assert f'"{k}"' in src, f"Template missing kwarg {k!r}"


def test_template_renders_with_sentinel():
    from templates import render_template, get_registration

    reg = get_registration("partner_incident_timeline/letter")
    rendered = render_template(
        "partner_incident_timeline/letter", **reg.sentinel_kwargs()
    )
    assert "Incident Response Timeline" in rendered
    assert "OsirisCare substrate" in rendered
    # Three sentinel events should each render.
    assert "Detected" in rendered
    assert "L1 plan" in rendered
    assert "Remediation" in rendered


def test_template_handles_empty_events():
    from templates import render_template

    rendered = render_template(
        "partner_incident_timeline/letter",
        presenter_brand="Smoke MSP",
        incident_id_short="abcdef01",
        incident_type="unknown",
        severity="—",
        status="open",
        resolution_tier_label="—",
        created_at_human="2026-05-08 09:14:02 UTC",
        resolved_at_human="—",
        ttr_human="—",
        site_label="site-abc123",
        events=[],
        generated_at_human="2026-05-08 09:30:00 UTC",
    )
    assert "No telemetry events recorded" in rendered


def test_template_no_banned_legal_words():
    from templates import render_template, get_registration

    reg = get_registration("partner_incident_timeline/letter")
    rendered = render_template(
        "partner_incident_timeline/letter", **reg.sentinel_kwargs()
    ).lower()
    for banned in (
        " ensures ",
        " prevents ",
        " protects ",
        " guarantees ",
        " audit-ready ",
        " phi never leaves ",
        " 100%",
        # Coach retroactive sweep 2026-05-08 — banned wording from
        # P-F5 slip. "continuously monitored" attaches the cadence
        # verb to the wrong word per OCR investigator review.
        "continuously monitored",
    ):
        assert banned not in rendered, (
            f"Banned wording {banned!r} appeared in rendered timeline."
        )


def test_template_phi_boundary_respected():
    """The timeline disclaimer must explain the PHI boundary so
    Lisa can hand the printed copy to a clinic owner without
    surprise. RT33-class regression class: PHI accidentally
    surfacing in a derived report."""
    from templates import render_template, get_registration

    reg = get_registration("partner_incident_timeline/letter")
    rendered = render_template(
        "partner_incident_timeline/letter", **reg.sentinel_kwargs()
    )
    assert "PHI is scrubbed at the appliance" in rendered
    assert "nothing in this timeline identifies a patient" in rendered


# ---------------------------------------------------------------- API endpoint


def test_endpoint_registered():
    src = (_BACKEND / "partners.py").read_text()
    assert (
        '@router.get("/me/incidents/{incident_id}/timeline.pdf")' in src
    )


def test_endpoint_admin_or_tech():
    """Operational artifact — NOT a state-change — so admin OR tech
    role allowed. Billing-role partner_users still rejected via
    require_partner_role contract."""
    src = (_BACKEND / "partners.py").read_text()
    idx = src.find("async def render_partner_incident_timeline_pdf(")
    block = src[idx : idx + 800]
    assert 'require_partner_role("admin", "tech")' in block


def test_endpoint_uses_asyncio_to_thread():
    src = (_BACKEND / "partners.py").read_text()
    idx = src.find("async def render_partner_incident_timeline_pdf(")
    block = src[idx : idx + 4000]
    assert "_asyncio.to_thread" in block, (
        "WeasyPrint blocks the event loop; must wrap in to_thread "
        "(Steve P1-A pattern from F1)."
    )


def test_endpoint_rate_limited_per_partner_user():
    src = (_BACKEND / "partners.py").read_text()
    idx = src.find("async def render_partner_incident_timeline_pdf(")
    block = src[idx : idx + 4000]
    assert "check_rate_limit" in block
    assert 'caller_key=f"partner_user:{caller_user_id}"' in block
    assert "max_requests=60" in block


def test_endpoint_rejects_malformed_incident_id():
    """Defense-in-depth — reject obvious garbage at the API edge so
    we don't even hit the DB layer for input that can't possibly be
    a real UUID."""
    src = (_BACKEND / "partners.py").read_text()
    idx = src.find("async def render_partner_incident_timeline_pdf(")
    block = src[idx : idx + 4000]
    assert 'detail="malformed incident_id"' in block


def test_endpoint_returns_404_on_unowned_incident():
    """If the partner-ownership JOIN returns no row, the endpoint
    must surface 404 (not 500, not 403). 403 would leak the
    incident's existence to a non-owning partner."""
    src = (_BACKEND / "partners.py").read_text()
    idx = src.find("async def render_partner_incident_timeline_pdf(")
    block = src[idx : idx + 4000]
    assert "IncidentTimelineError" in block
    assert "status_code=404" in block


# ---------------------------------------------------------------- security allow-list


def test_kwargs_in_security_allowlist():
    src = (_BACKEND / "templates" / "__init__.py").read_text()
    for k in (
        "incident_id_short",
        "incident_type",
        "severity",
        "status",
        "resolution_tier_label",
        "created_at_human",
        "resolved_at_human",
        "ttr_human",
        "site_label",
        "events",
        "generated_at_human",
    ):
        assert f'"{k}"' in src, f"Kwarg {k!r} not in allow-list"


# ---------------------------------------------------------------- ttr formatting


def test_ttr_formatter_under_minute():
    from partner_incident_timeline import _human_ttr
    from datetime import datetime, timedelta, timezone

    start = datetime(2026, 5, 8, 9, 14, 2, tzinfo=timezone.utc)
    end = start + timedelta(seconds=29)
    assert _human_ttr(start, end) == "29 seconds"
    assert _human_ttr(start, start + timedelta(seconds=1)) == "1 second"


def test_ttr_formatter_minutes_hours_days():
    from partner_incident_timeline import _human_ttr
    from datetime import datetime, timedelta, timezone

    start = datetime(2026, 5, 8, 9, 14, 2, tzinfo=timezone.utc)
    assert _human_ttr(start, start + timedelta(minutes=5)) == "5 minutes"
    assert _human_ttr(start, start + timedelta(minutes=1)) == "1 minute"
    assert _human_ttr(start, start + timedelta(hours=2, minutes=30)) == "2.5 hours"
    assert _human_ttr(start, start + timedelta(days=3)) == "3.0 days"


def test_ttr_formatter_negative_or_missing():
    from partner_incident_timeline import _human_ttr
    from datetime import datetime, timedelta, timezone

    start = datetime(2026, 5, 8, 9, 14, 2, tzinfo=timezone.utc)
    assert _human_ttr(None, None) == "—"
    assert _human_ttr(start, None) == "—"
    assert _human_ttr(None, start) == "—"
    # Negative duration (resolved_at before created_at — corrupt
    # data) renders as em-dash, not negative number.
    assert _human_ttr(start, start - timedelta(seconds=5)) == "—"


def test_short_site_label_is_deterministic_and_hashed():
    from partner_incident_timeline import _short_site_label

    label = _short_site_label("site-northvalleybranch2")
    assert label.startswith("site-")
    assert len(label) == 11  # site- + 6 hex chars
    # No part of the input site_id leaks into the label.
    assert "northvalley" not in label
    # Stable across calls.
    assert _short_site_label("site-northvalleybranch2") == label
    # Different sites produce different labels.
    assert _short_site_label("site-other") != label

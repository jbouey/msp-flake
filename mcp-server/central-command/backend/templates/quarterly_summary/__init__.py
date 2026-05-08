"""F3 Quarterly Practice Compliance Summary template registration
(sprint 2026-05-08).

Side-effect import: parent ``backend.templates.__init__`` does
``from . import quarterly_summary`` so the registration lands at
module-load time; boot-smoke + render_template can find it.

Mirror-of-F1 posture: required_kwargs are aggregate, frozen-at-issue,
non-PHI snapshot fields. Maya P1 allow-list review pinned in
``backend/templates/__init__.py``.
"""
from .. import register_customer_template

register_customer_template(
    name="quarterly_summary/letter",
    path="quarterly_summary/letter.html.j2",
    required_kwargs={
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
    },
    sentinel_factory=lambda: {
        # Sentinel kwargs that exercise pluralization branches +
        # the no-data ("—") mean_score path AND a non-zero quarter.
        "practice_name": "__SMOKE__ Family Practice",
        "period_year": 2026,
        "period_quarter": 1,
        "period_start_human": "January 1, 2026",
        "period_end_human": "March 31, 2026",
        "bundle_count": 412,
        "ots_anchored_pct_str": "98.7",
        "drift_detected_count": 5,
        "drift_resolved_count": 4,
        "mean_score_str": "94",
        "sites_count": 2,
        "appliances_count": 3,
        "workstations_count": 12,
        "monitored_check_types_count": 28,
        "privacy_officer_name": "__SMOKE__",
        "privacy_officer_title": "__SMOKE__",
        "privacy_officer_email": "smoke@example.com",
        "presenter_brand": "OsirisCare",
        "presenter_contact_line": "",
        "issued_at_human": "May 8, 2026",
        "valid_until_human": "May 8, 2027",
        # 64-hex-char fake hash so the template's [:32] slice works.
        "attestation_hash": (
            "0000000000000000111111111111111122222222222222223333333333333333"
        ),
        "verify_phone": "1-800-OSIRIS-1",
        "verify_url_short": "osiriscare.io/verify/quarterly",
    },
    owner="carol-counsel",
)

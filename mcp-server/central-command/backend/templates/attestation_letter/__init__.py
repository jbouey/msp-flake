"""Attestation Letter template registration (F1, round-table 2026-05-06).

Side-effect import: parent ``backend.templates.__init__`` does
``from . import attestation_letter`` so the registration lands at
module-load time and the boot smoke + render_template can find it.
"""
from .. import register_customer_template

register_customer_template(
    name="attestation_letter/letter",
    path="attestation_letter/letter.html.j2",
    required_kwargs={
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
    },
    sentinel_factory=lambda: {
        # Sentinel kwargs that exercise the conditional pluralization
        # branches (sites_covered_count != 1, appliances_count != 1)
        # so the boot smoke catches a Jinja conditional that would
        # raise UndefinedError on the non-default path.
        "practice_name": "__SMOKE__ Family Practice",
        "period_start_human": "April 6, 2026",
        "period_end_human": "May 6, 2026",
        "sites_covered_count": 2,
        "appliances_count": 3,
        "workstations_count": 12,
        "bundle_count": 142,
        "privacy_officer_name": "__SMOKE__",
        "privacy_officer_title": "__SMOKE__",
        "privacy_officer_email": "smoke@example.com",
        "privacy_officer_accepted_human": "May 1, 2026",
        "privacy_officer_explainer_version": "v1-2026-05-06",
        "baa_dated_at_human": "January 15, 2026",
        "baa_practice_name": "__SMOKE__",
        "presenter_brand": "OsirisCare",
        "presenter_contact_line": "",
        "issued_at_human": "May 6, 2026",
        "valid_until_human": "August 4, 2026",
        # 64-hex-char fake hash so the template's [:16] slice works.
        "attestation_hash": (
            "0000000000000000111111111111111122222222222222223333333333333333"
        ),
        "verify_phone": "1-800-OSIRIS-1",
        "verify_url_short": "osiriscare.io/verify",
    },
    owner="carol-counsel",
)

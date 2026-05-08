"""Wall Certificate template registration (F5, sprint 2026-05-08).

F5 = "Maria wants a 1-page wall certificate she can hang in the
clinic showing the practice is monitored by an Ed25519-signed
compliance substrate." Reuses the F1 attestation row + its existing
Ed25519 signature — does NOT issue a new attestation. The wall
cert is an alternate render of the SAME signed payload, NOT a new
state machine. F1 + this template share kwargs (period, practice
name, sites/appliances/workstations counts, bundle count, Privacy
Officer designation snapshot, BAA snapshot, presenter snapshot,
issuance/validity timestamps, attestation hash, verify phone +
URL). Plus a couple wall-cert-specific kwargs for the operational
summary line (bundle_count + ots_anchored_pct_str — already
allow-listed for the partner portfolio surface).

Side-effect import: parent ``backend.templates.__init__`` does
``from . import wall_cert`` so the registration lands at module-
load time and the boot smoke + render_template can find it.
"""
from .. import register_customer_template

register_customer_template(
    name="wall_cert/letter",
    path="wall_cert/letter.html.j2",
    required_kwargs={
        # Mirrors F1 (attestation_letter) — the wall cert IS a re-
        # render of F1's signed payload. Kwargs here are the
        # subset needed for the landscape display layout.
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
    },
    sentinel_factory=lambda: {
        # Sentinel kwargs that exercise pluralization branches
        # (sites_covered_count != 1, appliances_count != 1,
        # workstations_count != 1) so the boot smoke catches
        # any Jinja conditional that would raise UndefinedError
        # on the non-default path.
        "practice_name": "__SMOKE__ Family Practice",
        "period_start_human": "April 6, 2026",
        "period_end_human": "May 6, 2026",
        "sites_covered_count": 2,
        "appliances_count": 3,
        "workstations_count": 12,
        "bundle_count": 142,
        "ots_anchored_pct_str": "98",
        "privacy_officer_name": "__SMOKE__",
        "privacy_officer_title": "__SMOKE__",
        "baa_dated_at_human": "January 15, 2026",
        "baa_practice_name": "__SMOKE__",
        "presenter_brand": "OsirisCare",
        "presenter_contact_line": "",
        "issued_at_human": "May 6, 2026",
        "valid_until_human": "August 4, 2026",
        # 64-hex-char fake hash so the template's [:32] slice works.
        "attestation_hash": (
            "0000000000000000111111111111111122222222222222223333333333333333"
        ),
        "verify_phone": "1-800-OSIRIS-1",
        "verify_url_short": "osiriscare.io/verify",
    },
    owner="carol-counsel",
)

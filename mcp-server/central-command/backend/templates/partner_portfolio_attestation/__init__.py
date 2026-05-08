"""P-F5 Portfolio Attestation template registration (partner
round-table 2026-05-08). Side-effect import via parent
``backend.templates.__init__``."""
from .. import register_customer_template

register_customer_template(
    name="partner_portfolio_attestation/letter",
    path="partner_portfolio_attestation/letter.html.j2",
    required_kwargs={
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
    },
    sentinel_factory=lambda: {
        "presenter_brand": "__SMOKE__ MSP",
        "presenter_contact_line": "",
        "period_start_human": "April 8, 2026",
        "period_end_human": "May 8, 2026",
        "site_count": 14,
        "appliance_count": 23,
        "workstation_count": 312,
        "control_count": 87,
        "bundle_count": 4982,
        "ots_anchored_pct_str": "98.4",
        "chain_root_hex": "0" * 64,
        "chain_head_at_human": "May 8, 2026",
        "issued_at_human": "May 8, 2026",
        "valid_until_human": "August 6, 2026",
        "attestation_hash": (
            "0000000000000000111111111111111122222222222222223333333333333333"
        ),
        "verify_phone": "1-800-OSIRIS-1",
        "verify_url_short": "osiriscare.io/verify/portfolio",
    },
    owner="partner-product",
)

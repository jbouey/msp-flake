"""P-F8 Per-incident Response Timeline template registration."""
from .. import register_customer_template

register_customer_template(
    name="partner_incident_timeline/letter",
    path="partner_incident_timeline/letter.html.j2",
    required_kwargs={
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
    },
    sentinel_factory=lambda: {
        "presenter_brand": "__SMOKE__ MSP",
        "incident_id_short": "1234abcd",
        "incident_type": "service_down",
        "severity": "P2",
        "status": "resolved",
        "resolution_tier_label": "L1 deterministic rule",
        "created_at_human": "May 8, 2026 09:14:02 UTC",
        "resolved_at_human": "May 8, 2026 09:14:31 UTC",
        "ttr_human": "29 seconds",
        "site_label": "site-a4f7c2",
        "events": [
            {
                "timestamp_human": "2026-05-08 09:14:02 UTC",
                "kind": "Detected",
                "description": "Drift check service_running flagged DNS service stopped on monitored host.",
            },
            {
                "timestamp_human": "2026-05-08 09:14:05 UTC",
                "kind": "L1 plan",
                "description": "Deterministic rule L1-SVC-DNS-001 matched; runbook RB-AUTO-SERVICE_ selected.",
            },
            {
                "timestamp_human": "2026-05-08 09:14:31 UTC",
                "kind": "Remediation",
                "description": "Service restart issued; appliance reported service running. Incident resolved.",
            },
        ],
        "generated_at_human": "May 8, 2026 09:30:00 UTC",
    },
    owner="partner-product",
)

"""P-F7 Weekly Digest template registration (partner round-table
2026-05-08). Side-effect import via parent
``backend.templates.__init__``.

NOTE: top_noisy_sites is a LIST passed through Jinja's `{% for %}`
loop. The sentinel_factory provides a non-empty list so the boot
smoke exercises the conditional + iteration paths."""
from .. import register_customer_template

register_customer_template(
    name="partner_weekly_digest/letter",
    path="partner_weekly_digest/letter.html.j2",
    required_kwargs={
        "presenter_brand",
        "technician_name",
        "week_start_human",
        "week_end_human",
        "orders_run",
        "alerts_triaged",
        "escalations_closed",
        "mttr_median_human",
        "top_noisy_sites",
    },
    sentinel_factory=lambda: {
        "presenter_brand": "__SMOKE__ MSP",
        "technician_name": "Lisa W.",
        "week_start_human": "May 1, 2026",
        "week_end_human": "May 8, 2026",
        "orders_run": 24,
        "alerts_triaged": 87,
        "escalations_closed": 4,
        "mttr_median_human": "12 min",
        # Non-empty list exercises the `{% for %}` loop path.
        "top_noisy_sites": [
            {"label": "site-aggregate-1", "incident_count": 8, "auto_resolved_count": 6},
            {"label": "site-aggregate-2", "incident_count": 5, "auto_resolved_count": 5},
            {"label": "site-aggregate-3", "incident_count": 3, "auto_resolved_count": 2},
        ],
    },
    owner="partner-product",
)

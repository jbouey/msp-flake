"""Auditor-kit template registrations (T1.3).

Registers the three auditor-kit artifact templates with the
customer-template registry. Side-effect import: parent
``backend.templates.__init__`` does ``from . import auditor_kit``
so these registrations land at module-load time and are visible
to the boot smoke and to ``render_template`` callers.
"""
from .. import register_customer_template

# README.md.j2 — Carol-owned customer-facing legal copy. Required
# kwargs match the call site in evidence_chain.py::download_auditor_kit.
register_customer_template(
    name="auditor_kit/README",
    path="auditor_kit/README.md.j2",
    required_kwargs={
        "site_id",
        "clinic_name",
        "generated_at",
        "presenter_brand",
        "presenter_contact_line",
    },
    owner="carol-counsel",
)

# verify.sh — engineering-owned shell script. No kwargs (plain
# static file). The boot smoke renders it as-is to confirm the
# file exists and is non-empty.
register_customer_template(
    name="auditor_kit/verify_sh",
    path="auditor_kit/verify.sh",
    required_kwargs=set(),
    owner="engineering",
)

# verify_identity.sh — same.
register_customer_template(
    name="auditor_kit/verify_identity_sh",
    path="auditor_kit/verify_identity.sh",
    required_kwargs=set(),
    owner="engineering",
)

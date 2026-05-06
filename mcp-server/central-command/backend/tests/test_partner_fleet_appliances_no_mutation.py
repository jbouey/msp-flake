"""CI gate: /api/partners/me/appliances is read-only + bounded.

RT33 P3 Maya rule (2026-05-05). Operator-class actions on appliances
(toggle l2_mode, fleet-order broadcast, clear-stale, delete) live on
central command. The partner-portal fleet view is OBSERVATION ONLY.
A future commit that adds a mutation here without round-table approval
must fail CI.

Pinned constraints:
  - Hard cap on `limit` at 100 (partner is operator-class so allowed
    larger pages than client's 50, but still bounded).
  - status_filter input validation (online/stale/offline only).
  - No invocation of operator-class mutations.
  - Reads from RLS-aware site_appliances directly (mig 278).
"""
from __future__ import annotations

import pathlib
import re

_PARTNERS = (
    pathlib.Path(__file__).resolve().parent.parent / "partners.py"
)

_ENDPOINT_ANCHOR = '@router.get("/me/appliances")'

OPERATOR_CLASS_MUTATIONS = (
    "update_appliance_l2_mode",
    "clear_stale_appliances",
    "create_appliance_order",
    "delete_appliance",
    "broadcast_to_appliances",
    # The fleet_orders / order_signing path is operator-class entry.
    "sign_admin_order",
    "sign_fleet_order",
)


def _endpoint_body() -> str:
    src = _PARTNERS.read_text()
    idx = src.find(_ENDPOINT_ANCHOR)
    assert idx != -1, (
        f"Anchor `{_ENDPOINT_ANCHOR}` not found — endpoint moved or "
        f"renamed. Update the anchor in this gate."
    )
    next_router = src.find("@router.", idx + len(_ENDPOINT_ANCHOR))
    if next_router == -1:
        return src[idx:]
    return src[idx:next_router]


def test_partner_fleet_appliances_caps_limit_at_100():
    body = _endpoint_body()
    assert re.search(r"limit\s*>\s*100", body), (
        "partner /me/appliances missing `limit > 100` cap. RT33 hard "
        "bound — without it, a partner with 500 sites × 3 appliances "
        "could enumerate the entire fleet in one query."
    )


def test_partner_fleet_appliances_validates_status_filter():
    """status_filter must be allowlisted to online/stale/offline. A
    raw injection-prone string would break the inline status CASE."""
    body = _endpoint_body()
    assert (
        '"online"' in body or "'online'" in body
    ), "status_filter input validation missing 'online' allowlist"
    assert (
        '"stale"' in body or "'stale'" in body
    ), "status_filter input validation missing 'stale' allowlist"
    assert (
        '"offline"' in body or "'offline'" in body
    ), "status_filter input validation missing 'offline' allowlist"


def test_partner_fleet_appliances_invokes_no_operator_class_mutation():
    body = _endpoint_body()
    found = []
    for mut in OPERATOR_CLASS_MUTATIONS:
        if re.search(rf"\b{mut}\b\s*\(", body):
            found.append(mut)
    assert not found, (
        "partner /me/appliances invokes operator-class mutation — "
        "RT33 read-only contract violated. Move the mutation OR "
        "convene a round-table to lift the constraint.\n\n"
        + "\n".join(f"  - {f}" for f in found)
    )


def test_partner_fleet_appliances_reads_site_appliances_with_partner_scope():
    """The endpoint MUST filter by `s.partner_id` — without it, every
    partner sees every appliance in the world."""
    body = _endpoint_body()
    assert re.search(r"s\.partner_id\s*=\s*\$1", body), (
        "partner /me/appliances missing `WHERE s.partner_id = $1` "
        "scope. Without it, every partner sees every appliance — "
        "P0 cross-tenant leak."
    )
    assert re.search(r"\bFROM\s+site_appliances\b", body, re.IGNORECASE), (
        "partner /me/appliances must read from `site_appliances` "
        "(direct + scoped via JOIN sites). Same RT33 P2 logic — "
        "rollup MV bypasses RLS, direct read is the safe path."
    )


def test_partner_fleet_appliances_uses_admin_connection():
    """admin_connection is the canonical partner-side conn helper. It
    sets `app.is_admin=true` so the admin_bypass policy admits rows;
    JOIN-on-partner_id provides the tenant scope."""
    body = _endpoint_body()
    assert "admin_connection(" in body, (
        "partner /me/appliances must use admin_connection (canonical "
        "partner-side helper). org_connection would set the wrong "
        "tenant flag and tenant_connection requires a single site."
    )

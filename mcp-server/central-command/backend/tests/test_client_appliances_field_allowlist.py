"""CI gate: /api/client/appliances response field allowlist.

RT33 Carol veto (2026-05-05): the client-portal appliances endpoint
MUST NOT expose Layer 2 / mesh-topology fields. A compromised customer
session must not become a fleet recon map.

Forbidden in the response shape:
  - mac_address      (per-NIC identity, enables ARP spoof / MAC flood)
  - ip_addresses     (internal subnet topology)
  - daemon_health    (peer_macs, ring_size — full mesh map)
  - peer_macs        (mesh peer recon)
  - assigned_targets (hash-ring workload — site internal layout)
  - auth_failure_count (operator-class telemetry)

Allowed:
  - appliance_id, site_id, site_name (display labels)
  - display_name (user-friendly alias)
  - status (enum: online/offline/stale)
  - last_heartbeat_at, last_checkin (freshness)
  - agent_version (for "your appliance is on version X" UX)

Operator-class actions (l2-mode toggle, clear-stale, fleet-order
broadcast) MUST stay outside the client portal entirely — those are
the MSP's authority, not the customer's.
"""
from __future__ import annotations

import pathlib
import re

_CLIENT_PORTAL = (
    pathlib.Path(__file__).resolve().parent.parent / "client_portal.py"
)

# Locate the endpoint by its decorator string — this is the anchor.
_ENDPOINT_ANCHOR = '@auth_router.get("/appliances")'

FORBIDDEN_RESPONSE_KEYS = {
    "mac_address",
    "ip_addresses",
    "daemon_health",
    "peer_macs",
    "assigned_targets",
    "auth_failure_count",
}

REQUIRED_RESPONSE_KEYS = {
    "appliance_id",
    "site_id",
    "display_name",
    "status",
    "last_heartbeat_at",
    "last_checkin",
    "agent_version",
}

OPERATOR_CLASS_ACTIONS = (
    "update_appliance_l2_mode",
    "clear_stale_appliances",
    "create_appliance_order",
    "delete_appliance",
    "broadcast_to_appliances",
)


def _endpoint_body() -> str:
    """Return the source body of /api/client/appliances (decorator + handler)."""
    src = _CLIENT_PORTAL.read_text()
    idx = src.find(_ENDPOINT_ANCHOR)
    assert idx != -1, (
        f"Anchor `{_ENDPOINT_ANCHOR}` not found — endpoint moved or "
        f"renamed. Update the anchor in this gate."
    )
    # Capture from anchor to next `@auth_router` decorator or EOF.
    next_router = src.find("@auth_router", idx + len(_ENDPOINT_ANCHOR))
    if next_router == -1:
        return src[idx:]
    return src[idx:next_router]


def test_client_appliances_response_has_no_mesh_topology_fields():
    body = _endpoint_body()
    found = []
    for forbidden in FORBIDDEN_RESPONSE_KEYS:
        # Match `"<key>"` as a literal in the response dict — both
        # double and single quotes.
        if re.search(rf'["\']{forbidden}["\']', body):
            found.append(forbidden)
    assert not found, (
        "client-portal /appliances endpoint exposes Layer-2 topology "
        "fields — Carol veto from RT33. Either remove the field OR "
        "convene a round-table to revisit the customer-vs-operator "
        "boundary.\n\n"
        + "\n".join(f"  - {f}" for f in found)
    )


def test_client_appliances_response_includes_required_fields():
    body = _endpoint_body()
    missing = [
        key for key in REQUIRED_RESPONSE_KEYS
        if not re.search(rf'["\']{key}["\']', body)
    ]
    assert not missing, (
        "client-portal /appliances endpoint missing required fields "
        "from RT33 contract. The customer needs these to use the view.\n\n"
        + "\n".join(f"  - {m}" for m in missing)
    )


def test_client_appliances_endpoint_does_not_call_operator_class_actions():
    body = _endpoint_body()
    found = []
    for action in OPERATOR_CLASS_ACTIONS:
        if re.search(rf"\b{action}\b\s*\(", body):
            found.append(action)
    assert not found, (
        "client-portal /appliances endpoint invokes operator-class "
        "action — RT33 Maya rule. Operator-class actions (l2-mode, "
        "clear-stale, order-broadcast, delete) live on central command "
        "only. Move the action OR convene a round-table.\n\n"
        + "\n".join(f"  - {f}" for f in found)
    )


def test_client_appliances_endpoint_uses_rls_protected_path():
    """Reads from `site_appliances` (RLS-protected by mig 278
    `tenant_org_isolation`) NOT the `appliance_status_rollup` MV.
    Steve veto from RT33 P2 review: PG materialized views don't
    inherit base-table RLS, so reading the MV directly would bypass
    the tenant_org_isolation policy. Trade ~1ms perf for proper
    org isolation defense-in-depth."""
    body = _endpoint_body()
    # Must read site_appliances (the RLS-protected source).
    assert re.search(r"\bFROM\s+site_appliances\b", body, re.IGNORECASE), (
        "client-portal /appliances endpoint must read from "
        "`site_appliances` (RLS-protected via mig 278) — NOT the "
        "rollup MV which bypasses RLS. Steve veto, RT33 P2."
    )
    # Must NOT read the rollup MV (would silently bypass RLS).
    bad = re.search(
        r"\b(FROM|JOIN)\s+appliance_status_rollup\b",
        body,
        re.IGNORECASE,
    )
    assert not bad, (
        "client-portal /appliances endpoint reads "
        "`appliance_status_rollup` MV directly — bypasses RLS. "
        "Steve veto, RT33 P2. Use site_appliances + inline LATERAL "
        "heartbeat join instead.\n\n"
        f"Match: {bad.group(0) if bad else ''}"
    )
    # Must use org_connection (not admin_connection or tenant_connection).
    assert "org_connection(" in body, (
        "client-portal /appliances endpoint must use `org_connection` "
        "to set `app.current_org` — required by mig 278's "
        "tenant_org_isolation policy. Without it, RLS will return "
        "zero rows even though the JOIN looks correct."
    )


def test_client_appliances_endpoint_caps_limit_at_50():
    """Hard cap on page size — RT33 contract. Substrate-class view is
    read-only and bounded; if a customer needs >50 they should be
    operating via the MSP not the portal."""
    body = _endpoint_body()
    # Look for the validation: `if limit < 1 or limit > 50`
    assert re.search(r"limit\s*>\s*50", body), (
        "client-portal /appliances endpoint missing `limit > 50` "
        "validation — RT33 hard cap. Without it, a malformed query "
        "could enumerate the entire org fleet in one call."
    )

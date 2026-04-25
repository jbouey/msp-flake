"""Source-level guard tests for the appliance-relocate endpoint.

Session 210-B 2026-04-25 hardening #6. The endpoint at
POST /api/sites/{site_id}/appliances/{appliance_id}/relocate is the
first-class admin path the user identified as missing in today's
orphan recovery — three layers of manual ops (SQL + SSH + config.yaml
hand-edit) collapsed into one transactional call.

These tests exercise the contract surface (route registered, request
shape correct, response shape correct, audit-log integration intact)
without requiring a live Postgres. PG-backed integration coverage
lives in tests/test_appliance_relocate_pg.py (deferred — see task
#155).
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def test_relocate_endpoint_registered_in_sites_router():
    """The relocate endpoint must be mounted on the sites router."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "sites.py").read_text()
    assert (
        '@router.post("/{site_id}/appliances/{appliance_id}/relocate")' in src
    ), "appliance-relocate endpoint missing from sites.py"


def test_relocate_request_model_has_required_fields():
    """RelocateApplianceRequest must carry target_site_id and reason
    (audit context). Adding fields silently is fine; removing either
    is a contract break."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "sites.py").read_text()
    assert "class RelocateApplianceRequest(BaseModel):" in src
    # Both fields must be declared between the class line and the next
    # blank-line block.
    cls_idx = src.find("class RelocateApplianceRequest(BaseModel):")
    after_cls = src[cls_idx : cls_idx + 600]
    assert "target_site_id: str" in after_cls
    assert "reason: str" in after_cls


def test_relocate_enforces_minimum_reason_length():
    """Audit-log integrity: the reason must be ≥20 chars (matches the
    Session 210-B convention used by db_delete_safety_check.py and the
    admin/restore endpoint)."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "sites.py").read_text()
    relocate_idx = src.find("async def relocate_appliance(")
    assert relocate_idx != -1
    body = src[relocate_idx : relocate_idx + 4000]
    assert "len(req.reason.strip()) < 20" in body, (
        "relocate endpoint must reject reasons under 20 chars; without "
        "this gate audit logs become useless"
    )


def test_relocate_blocks_cross_org_moves():
    """Moving an appliance between client_orgs is a privileged-chain
    operation (touches the customer-trust boundary). This endpoint
    must refuse and direct callers to /api/admin/cross-org-relocate."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "sites.py").read_text()
    relocate_idx = src.find("async def relocate_appliance(")
    body = src[relocate_idx : relocate_idx + 6000]
    assert 'source["source_org"] != target["client_org_id"]' in body, (
        "cross-org check missing — relocate must compare source.client_org_id "
        "to target.client_org_id and 403 on mismatch"
    )
    assert "Cross-org appliance relocation" in body


def test_relocate_writes_admin_audit_log():
    """Every relocate must produce an admin_audit_log row with
    action='appliance.relocate' carrying both site_ids + reason."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "sites.py").read_text()
    relocate_idx = src.find("async def relocate_appliance(")
    body = src[relocate_idx : relocate_idx + 12000]
    assert '"appliance.relocate"' in body
    assert "INSERT INTO admin_audit_log" in body
    assert "from_site_id" in body
    assert "to_site_id" in body
    assert '"reason": req.reason' in body


def test_relocate_returns_ssh_snippet_for_daemon_completion():
    """Until daemon v0.4.11 ships the relocate_appliance fleet-order
    handler, the operator manually finishes the move via SSH. The
    response MUST include the ready-to-paste ssh_snippet."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "sites.py").read_text()
    relocate_idx = src.find("async def relocate_appliance(")
    body = src[relocate_idx : relocate_idx + 12000]
    assert '"ssh_snippet"' in body
    assert "<APPLIANCE_LAN_IP>" in body, (
        "ssh_snippet must use a placeholder for LAN IP (operator pastes it in)"
    )
    assert "yq -i" in body
    assert "systemctl restart appliance-daemon" in body


def test_relocate_uses_admin_connection_not_tenant():
    """Relocation crosses two sites — `tenant_connection(site_id=...)`
    would only see one. Must use `admin_connection` so the txn can
    read both sites + write to both."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "sites.py").read_text()
    relocate_idx = src.find("async def relocate_appliance(")
    body = src[relocate_idx : relocate_idx + 12000]
    assert "admin_connection(pool)" in body, (
        "relocate must use admin_connection — tenant_connection scopes to "
        "one site_id and would block the cross-site INSERT/UPDATE"
    )


def test_relocate_deactivates_source_api_keys():
    """The source api_key must be deactivated as part of the move so
    a daemon that hasn't been reconfigured yet can't keep authing under
    the old site."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "sites.py").read_text()
    relocate_idx = src.find("async def relocate_appliance(")
    body = src[relocate_idx : relocate_idx + 12000]
    assert "UPDATE api_keys" in body
    assert "SET active = false" in body

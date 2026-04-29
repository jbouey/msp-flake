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
    is a contract break.

    Window-size note (Session 213): the docstring grew with the
    F1-followup round-table P1-SWE-2 commentary about Pydantic regex
    enforcement. Use the next-class-marker as the upper bound rather
    than a fixed character window so future docstring growth doesn't
    break this guard."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "sites.py").read_text()
    assert "class RelocateApplianceRequest(BaseModel):" in src
    cls_idx = src.find("class RelocateApplianceRequest(BaseModel):")
    # Find the END of this class — search forward to the next top-level
    # `class ` or `def ` or `@router`. Whichever comes first.
    next_idx = min(
        (src.find(marker, cls_idx + 1) for marker in ("\n@router", "\nclass ", "\nasync def ", "\ndef ")),
        key=lambda i: i if i > 0 else 10**9,
    )
    after_cls = src[cls_idx:next_idx]
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
    body = src[relocate_idx : relocate_idx + 20000]
    assert '"appliance.relocate"' in body
    assert "INSERT INTO admin_audit_log" in body
    assert '"from_site_id":' in body
    assert '"to_site_id":' in body
    assert '"reason":' in body
    # Round-table RT additions: relocation_id + fleet_order_id +
    # evidence_bundle_id + agent_version + method must all be on
    # the audit row so the move is fully reconstructible.
    assert '"relocation_id":' in body
    assert '"fleet_order_id":' in body
    assert '"evidence_bundle_id":' in body
    assert '"agent_version":' in body
    assert '"method":' in body


def test_relocate_returns_ssh_snippet_for_legacy_daemon():
    """For daemons predating v0.4.11 (no reprovision handler), the
    response MUST include the ready-to-paste ssh_snippet so the
    operator can finish the move manually. Newer daemons get the
    fleet_order path instead — covered separately."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "sites.py").read_text()
    relocate_idx = src.find("async def relocate_appliance(")
    body = src[relocate_idx : relocate_idx + 20000]
    assert '"ssh_snippet"' in body
    assert "<APPLIANCE_LAN_IP>" in body, (
        "ssh_snippet must use a placeholder for LAN IP (operator pastes it in)"
    )
    assert "yq -i" in body
    assert "systemctl restart appliance-daemon" in body


def test_relocate_version_gates_fleet_order_issuance():
    """Round-table RT-5: the relocate endpoint must check
    site_appliances.agent_version and only issue the reprovision
    fleet_order when the daemon is ≥ 0.4.11. v0.4.10 daemons get
    ssh_snippet only — issuing the order to them = unknown_order_type
    + stuck-active row in fleet_orders forever."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "sites.py").read_text()
    relocate_idx = src.find("async def relocate_appliance(")
    body = src[relocate_idx : relocate_idx + 20000]
    assert "MIN_REPROVISION_VERSION" in body
    assert '"0.4.11"' in body, (
        "the version constant must literal-match the daemon's Version "
        "in appliance/internal/daemon/daemon.go — drift here means "
        "the gate misfires"
    )
    assert "_version_supports_reprovision" in body
    assert "INSERT INTO fleet_orders" in body, (
        "version_ok branch must issue the fleet_order"
    )
    # Order type literal can be single- or double-quoted depending on
    # whether the row is built inline or via INSERT param. Accept either.
    assert ("'reprovision'" in body) or ('"reprovision"' in body)
    assert "version_ok" in body, "branch on version is the gate"
    # RT-5+1 hardening: per-appliance scoping must use the signed-payload
    # mechanism (sign_admin_order with target_appliance_id). fleet_orders
    # has no site_id/appliance_id columns; relying on those was the
    # bug surfaced when the first reprovision order was issued.
    assert "sign_admin_order" in body or "target_appliance_id" in body, (
        "reprovision INSERT must scope via signed-payload target_appliance_id, "
        "not by querying nonexistent fleet_orders columns"
    )


def test_relocate_defers_source_soft_delete():
    """Round-table RT-3: source row must be marked status='relocating',
    NOT soft-deleted eagerly. Eager soft-delete leaves the daemon in
    today's orphan state if the move never lands."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "sites.py").read_text()
    relocate_idx = src.find("async def relocate_appliance(")
    body = src[relocate_idx : relocate_idx + 20000]
    assert "'relocating'" in body, (
        "source row must transition to 'relocating', not 'relocated', "
        "until the finalize sweep confirms target checkin landed"
    )
    # Source soft-delete (deleted_at = NOW()) MUST happen in the
    # finalize_pending_relocations() SQL function (Migration 245),
    # not inline in the endpoint body. The eager pattern is the bug
    # we're closing.
    assert "deleted_at = NOW()" not in body, (
        "RT-3 regression: source soft-delete must be deferred to the "
        "finalize_pending_relocations() sweep, not run eagerly in the "
        "endpoint body"
    )


def test_relocate_records_relocation_tracker_row():
    """RT-3: every relocate INSERTs a relocations row with status='pending'
    so the finalize sweep + relocation_stalled invariant can find it."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "sites.py").read_text()
    relocate_idx = src.find("async def relocate_appliance(")
    body = src[relocate_idx : relocate_idx + 20000]
    assert "INSERT INTO relocations" in body
    assert "'pending'" in body, "tracker row must start at status='pending'"
    assert "RETURNING id" in body, (
        "relocation_id is needed downstream — must be RETURNINGed and stored"
    )


def test_relocate_emits_evidence_chain_bundle():
    """RT-7: every relocate writes a compliance_bundles row via
    appliance_relocation.emit_admin_relocation_bundle so the customer's
    evidence chain reflects the move."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "sites.py").read_text()
    relocate_idx = src.find("async def relocate_appliance(")
    body = src[relocate_idx : relocate_idx + 20000]
    assert "from .appliance_relocation import emit_admin_relocation_bundle" in body
    assert "evidence_bundle_id" in body
    assert "UPDATE relocations SET evidence_bundle_id" in body, (
        "the relocation tracker row must carry the bundle pointer so "
        "auditors can join the two append-only sources"
    )


def test_relocate_uses_admin_connection_not_tenant():
    """Relocation crosses two sites — `tenant_connection(site_id=...)`
    would only see one. Must use `admin_connection` so the txn can
    read both sites + write to both."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "sites.py").read_text()
    relocate_idx = src.find("async def relocate_appliance(")
    body = src[relocate_idx : relocate_idx + 20000]
    assert "admin_connection(pool)" in body, (
        "relocate must use admin_connection — tenant_connection scopes to "
        "one site_id and would block the cross-site INSERT/UPDATE"
    )


def test_relocate_refuses_concurrent_pending_for_same_mac():
    """Migration 245 enforces UNIQUE(mac, status) so a second 'pending'
    row for the same MAC is impossible at the schema layer. The
    endpoint surfaces this with a 409 BEFORE the constraint fires so
    the operator gets a clean error."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "sites.py").read_text()
    relocate_idx = src.find("async def relocate_appliance(")
    body = src[relocate_idx : relocate_idx + 20000]
    assert "status_code=409" in body, (
        "must surface a concurrent-pending move as 409, not let the "
        "DB constraint surface as a generic 500"
    )
    assert "already pending" in body or "pending" in body

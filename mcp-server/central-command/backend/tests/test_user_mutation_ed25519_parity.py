"""Ed25519 attestation parity on user-mutation privileged actions.

Maya cross-cutting parity finding from the 2026-05-04 partner-portal
consistency round-table. Pre-fix: client_user role changes + partner_user
creation wrote audit_log + (sometimes) operator alerts, but the
cryptographic chain didn't reflect them. Both portals now attest via
Ed25519 — same chain shape, same severity-escalation pattern when the
attestation step fails.

Source-level tests pin the contract. Behavior tests (DB-live + actual
attestation insertion) run separately in CI against the prod-mirror.
"""
from __future__ import annotations

import ast
import pathlib

import pytest


_BACKEND = pathlib.Path(__file__).resolve().parent.parent


def _read(p: pathlib.Path) -> str:
    return p.read_text()


def _find_function(src: str, name: str) -> str:
    """Extract a function's source span. Returns '' if not found."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                # ast.get_source_segment requires Python 3.8+ which is fine
                seg = ast.get_source_segment(src, node, padded=False)
                return seg or ""
    return ""


# ─── Lockstep ALLOWED_EVENTS ─────────────────────────────────────


def test_user_mutation_events_in_allowed_events():
    """Both client_user_role_changed and partner_user_created must
    be in ALLOWED_EVENTS or attestation_create rejects them at runtime."""
    src = _read(_BACKEND / "privileged_access_attestation.py")
    for ev in ["client_user_role_changed", "partner_user_created"]:
        assert f'"{ev}"' in src, (
            f"ALLOWED_EVENTS missing `{ev}` — "
            f"create_privileged_access_attestation will reject the "
            f"event_type at runtime."
        )


# ─── Client-side Ed25519 hook ────────────────────────────────────


def test_client_user_role_change_writes_attestation():
    """client_portal.py update_user_role MUST call
    create_privileged_access_attestation with event_type=
    client_user_role_changed."""
    src = _read(_BACKEND / "client_portal.py")
    fn_src = _find_function(src, "update_user_role")
    assert fn_src, "update_user_role function not found in client_portal.py"
    assert "create_privileged_access_attestation" in fn_src, (
        "update_user_role no longer calls "
        "create_privileged_access_attestation. The cryptographic chain "
        "no longer reflects role changes — auditor kit will be missing "
        "these events."
    )
    assert 'event_type="client_user_role_changed"' in fn_src, (
        "update_user_role's attestation call uses wrong event_type — "
        "must match the ALLOWED_EVENTS entry."
    )


def test_client_user_role_change_handles_attestation_failure():
    """Must capture attestation_failed bool and pass it through to
    operator alert + response — chain-gap escalation pattern shipped
    in QA commit 0c710d3b."""
    src = _read(_BACKEND / "client_portal.py")
    fn_src = _find_function(src, "update_user_role")
    assert "role_change_attestation_failed" in fn_src, (
        "attestation_failed flag missing — operator alert can't "
        "escalate severity to P0-CHAIN-GAP when the chain breaks."
    )
    assert "PrivilegedAccessAttestationError" in fn_src, (
        "Specific attestation exception not caught — would mask the "
        "chain-gap class behind a generic Exception handler."
    )


# ─── Partner-side Ed25519 hook ───────────────────────────────────


def test_partner_user_create_writes_attestation():
    src = _read(_BACKEND / "partners.py")
    fn_src = _find_function(src, "create_partner_user")
    assert fn_src, "create_partner_user function not found in partners.py"
    assert "create_privileged_access_attestation" in fn_src, (
        "create_partner_user no longer calls "
        "create_privileged_access_attestation. Maya parity gap reopened."
    )
    assert 'event_type="partner_user_created"' in fn_src, (
        "create_partner_user's attestation call uses wrong event_type."
    )


def test_partner_user_create_logs_audit():
    """Pre-2026-05-04: create_partner_user wrote NEITHER audit nor
    attestation. Post-fix: both. This pins both halves."""
    src = _read(_BACKEND / "partners.py")
    fn_src = _find_function(src, "create_partner_user")
    assert "log_partner_activity" in fn_src, (
        "create_partner_user no longer calls log_partner_activity — "
        "the audit half of the gap reopened."
    )
    assert "PartnerEventType.PARTNER_UPDATED" in fn_src or \
           "PartnerEventType.partner_user" in fn_src.lower(), (
        "audit call not using a PartnerEventType — would write a "
        "string-typed event_type that bypasses the enum."
    )


def test_partner_user_create_handles_attestation_failure():
    src = _read(_BACKEND / "partners.py")
    fn_src = _find_function(src, "create_partner_user")
    assert "create_attestation_failed" in fn_src, (
        "attestation_failed flag missing on partner-side parity hook."
    )
    assert "P0-CHAIN-GAP" in fn_src, (
        "chain-gap severity escalation pattern not applied — operator "
        "wouldn't know if the chain broke for a partner_user creation."
    )


# ─── Anchor-site_id namespace consistency ────────────────────────


def test_partner_attestation_uses_partner_org_namespace():
    """Partner events anchor to 'partner_org:<partner_id>' synthetic
    site_id — not a real site_id. Auditor kit walks partner-event
    chains by this namespace prefix. If the namespace drifts, the
    auditor kit can't reconcile partner events with the rest of the
    chain."""
    src = _read(_BACKEND / "partners.py")
    fn_src = _find_function(src, "create_partner_user")
    assert 'partner_org:' in fn_src, (
        "Partner attestation no longer uses partner_org:<id> namespace "
        "— auditor kit cross-walk will break."
    )


# ─── digest-prefs audit gap (item D) ─────────────────────────────


# ─── Maya P1-1 closure (2026-05-04): 4 events promoted to chain ──


def test_org_deprovisioned_writes_attestation():
    """Maya P1-1 finding: pre-2026-05-04 fix, org_deprovisioned hook
    fired the operator alert with chain-gap-escalation pattern but
    NEVER wrote an Ed25519 bundle. Now writes via
    create_privileged_access_attestation. Anchor: org's primary site_id."""
    src = _read(_BACKEND / "org_management.py")
    fn_src = _find_function(src, "deprovision_org")
    assert fn_src, "deprovision_org function not found"
    assert "create_privileged_access_attestation" in fn_src, (
        "deprovision_org no longer writes Ed25519 attestation — "
        "Maya P1-1 reopened."
    )
    assert 'event_type="org_deprovisioned"' in fn_src, (
        "deprovision attestation uses wrong event_type."
    )
    assert "deprovision_attestation_failed" in fn_src, (
        "chain-gap escalation flag missing — operator can't see when "
        "the chain breaks."
    )


def test_org_reprovisioned_writes_attestation():
    src = _read(_BACKEND / "org_management.py")
    fn_src = _find_function(src, "reprovision_org")
    assert fn_src, "reprovision_org function not found"
    assert "create_privileged_access_attestation" in fn_src
    assert 'event_type="org_reprovisioned"' in fn_src
    assert "reprovision_attestation_failed" in fn_src


def test_partner_api_key_regen_writes_attestation():
    src = _read(_BACKEND / "partners.py")
    fn_src = _find_function(src, "regenerate_api_key")
    assert fn_src, "regenerate_api_key not found"
    assert "create_privileged_access_attestation" in fn_src, (
        "API key regen no longer writes attestation — Maya P1-1 "
        "reopened. Bearer of regenerated key has 365-day partner-API "
        "access; this is privileged-action-class."
    )
    assert 'event_type="partner_api_key_regenerated"' in fn_src
    assert "api_key_attestation_failed" in fn_src


def test_partner_org_delete_writes_attestation():
    src = _read(_BACKEND / "partners.py")
    fn_src = _find_function(src, "delete_partner")
    assert fn_src, "delete_partner not found"
    assert "create_privileged_access_attestation" in fn_src, (
        "Partner-org delete no longer writes attestation. Most "
        "destructive partner-portal action — auditor kit MUST capture."
    )
    assert 'event_type="partner_org_deleted"' in fn_src
    assert "delete_attestation_failed" in fn_src


def test_p11_events_in_allowed_events():
    src = _read(_BACKEND / "privileged_access_attestation.py")
    for ev in [
        "org_deprovisioned",
        "org_reprovisioned",
        "partner_api_key_regenerated",
        "partner_org_deleted",
    ]:
        assert f'"{ev}"' in src, (
            f"Maya P1-1 event `{ev}` not in ALLOWED_EVENTS — "
            f"create_privileged_access_attestation will reject at runtime."
        )


def test_p21_appliance_relocate_event_matches_attestation():
    """Maya P2-1: operator-email event_type MUST match attestation
    chain event_type for forensic correlation across inbox + auditor
    kit. Pre-fix the email said `appliance_relocated` while the chain
    wrote `appliance_relocation_acknowledged` — disjoint search."""
    src = _read(_BACKEND / "sites.py")
    fn_src = _find_function(src, "relocate_appliance")
    assert fn_src, "relocate_appliance not found"
    assert 'event_type="appliance_relocation_acknowledged"' in fn_src, (
        "operator-email event_type does not match the attestation "
        "chain entry — forensic search returns disjoint result sets."
    )
    assert 'event_type="appliance_relocated"' not in fn_src, (
        "old past-tense event_type still present — rename incomplete."
    )


def test_digest_prefs_audit_call_present():
    """Pre-2026-05-04: PUT /me/digest-prefs mutated partners.digest_
    enabled with NO audit row. Maya P3 parity finding."""
    src = _read(_BACKEND / "partners.py")
    fn_src = _find_function(src, "set_partner_digest_prefs")
    assert fn_src, "set_partner_digest_prefs handler not found"
    assert "log_partner_activity" in fn_src, (
        "digest-prefs handler no longer calls log_partner_activity — "
        "audit gap reopened."
    )
    assert '"digest_enabled"' in fn_src, (
        "audit event_data does not capture the field name — operator "
        "triaging an audit row wouldn't know what changed."
    )

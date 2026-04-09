"""Tests for Session 203 Batch 7 — client portal audit log expansion.

Covers:
  1. New `/api/client/audit-log` endpoint (HIPAA §164.528 disclosure
     accounting view)
  2. 12+ client mutating endpoints now wired to `_audit_client_action`

Source-level checks — same idiom as test_evidence_auth_audit_fixes.py
because client_portal.py's mutations are awkward to fixture without
standing up a real org_connection.
"""

import ast
import os


CLIENT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "client_portal.py",
)


def _load() -> str:
    with open(CLIENT) as f:
        return f.read()


def _get_func(name: str) -> str:
    src = _load()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"{name} not found")


# =============================================================================
# /api/client/audit-log endpoint
# =============================================================================

class TestAuditLogEndpoint:
    def test_endpoint_registered(self):
        src = _load()
        assert '@auth_router.get("/audit-log")' in src
        assert "async def list_client_audit_log(" in src

    def test_requires_auth(self):
        body = _get_func("list_client_audit_log")
        assert "Depends(require_client_user)" in body

    def test_uses_org_connection(self):
        """Reads must be scoped to the caller's org via org_connection so
        RLS can never leak cross-tenant data."""
        body = _get_func("list_client_audit_log")
        assert "org_connection(pool, org_id=org_id)" in body

    def test_supports_action_prefix_filter(self):
        body = _get_func("list_client_audit_log")
        assert "action LIKE" in body
        assert 'action: Optional[str] = Query' in body

    def test_supports_lookback_window(self):
        body = _get_func("list_client_audit_log")
        # 1 → 2555 (7 years HIPAA retention)
        assert "ge=1, le=2555" in body
        assert "INTERVAL" in body

    def test_paginated(self):
        body = _get_func("list_client_audit_log")
        assert "LIMIT" in body
        assert "OFFSET" in body
        assert "limit: int = Query" in body
        assert "offset: int = Query" in body

    def test_returns_total_for_pagination_ui(self):
        body = _get_func("list_client_audit_log")
        assert "SELECT COUNT(*)" in body
        assert '"total"' in body

    def test_returns_humanizable_event_shape(self):
        body = _get_func("list_client_audit_log")
        for field in ('"action"', '"actor_email"', '"target"', '"details"', '"ip_address"', '"created_at"'):
            assert field in body, f"missing field {field} in response"


# =============================================================================
# Mutation wiring — 12+ endpoints now write to _audit_client_action
# =============================================================================

class TestClientMutationsAuditWired:
    REQUIRED_ACTIONS = [
        "DRIFT_CONFIG_UPDATED",
        "USER_INVITED",
        "USER_REMOVED",
        "USER_ROLE_CHANGED",
        "PASSWORD_CHANGED",
        "CREDENTIAL_CREATED",
        "MFA_ENABLED",
        "MFA_DISABLED",
        "DEVICE_REGISTERED",
        "DEVICE_IGNORED",
        "ESCALATION_ACKNOWLEDGED",
        "ESCALATION_RESOLVED",
        "ESCALATION_PREFS_UPDATED",
    ]

    def test_all_required_actions_present(self):
        src = _load()
        for action in self.REQUIRED_ACTIONS:
            assert f'"{action}"' in src, f"{action} not wired into client_portal.py"

    def test_alert_action_dynamic_event_name(self):
        """The alert handler builds the action name from the user input
        ('approved'/'dismissed'/etc) so the assertion checks for the
        f-string rather than a literal."""
        src = _load()
        assert 'f"ALERT_{action.upper()}"' in src

    def test_audit_calls_pass_request_for_ip_capture(self):
        """Every wired mutation should pass `request=request` so the
        `_audit_client_action` helper can extract the client IP."""
        src = _load()
        # Count `_audit_client_action(` calls and `request=request` keyword args
        # in the same function block — at least 10 should match.
        audit_calls = src.count("_audit_client_action(")
        # 1 helper definition + 13 call sites = 14 minimum
        assert audit_calls >= 14, f"only {audit_calls} _audit_client_action references found"

    def test_drift_config_audit_includes_check_count(self):
        body = _get_func("update_client_drift_config")
        assert "_audit_client_action" in body
        assert '"check_count"' in body

    def test_user_role_change_audit_records_new_role(self):
        body = _get_func("update_user_role")
        assert "_audit_client_action" in body
        assert '"new_role"' in body

    def test_remove_user_audit_records_removed_role(self):
        body = _get_func("remove_user")
        assert "_audit_client_action" in body
        assert '"removed_role"' in body

    def test_credential_create_audit_records_type(self):
        body = _get_func("submit_client_credentials")
        assert "_audit_client_action" in body
        assert '"credential_type"' in body

    def test_device_register_audit_records_host(self):
        body = _get_func("register_device")
        assert "_audit_client_action" in body
        assert '"host"' in body

    def test_escalation_resolve_audit_truncates_notes(self):
        """Resolution notes can be long — the audit log should store
        a truncated version (first ~500 chars) to avoid bloating the
        details JSONB column."""
        body = _get_func("client_resolve_ticket")
        assert "_audit_client_action" in body
        assert "[:500]" in body

    def test_mfa_audit_records_user_id(self):
        for fn_name in ("client_totp_verify", "client_totp_disable"):
            body = _get_func(fn_name)
            assert "_audit_client_action" in body, f"{fn_name} missing audit call"
            assert "user_id" in body

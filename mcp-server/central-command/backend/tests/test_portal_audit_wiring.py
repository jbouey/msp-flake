"""Tests for Session 203 Batch 3 — portal audit log wiring.

Verifies that:
  1. The `client_audit_log` migration created the table with the right shape
  2. `client_portal._audit_client_action` helper exists
  3. Core client mutations are audit-wired (password, role, user remove)
  4. Partner mutations use the existing `partner_activity_logger` infra
     (DRIFT_CONFIG_UPDATED, MAINTENANCE_WINDOW_SET, MAINTENANCE_WINDOW_CANCELLED)
"""

import ast
import os


CLIENT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "client_portal.py",
)
PARTNERS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "partners.py",
)
LOGGER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "partner_activity_logger.py",
)
MIG_149 = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "migrations", "149_portal_audit_logs.sql",
)
MIG_150 = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "migrations", "150_drop_unused_partner_audit_log.sql",
)


def _load(path: str) -> str:
    with open(path) as f:
        return f.read()


def _get_func(src: str, name: str):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"{name} not found")


# =============================================================================
# Client side — new client_audit_log table + helper + wiring
# =============================================================================

class TestClientAuditLogMigration:
    def test_migration_149_creates_client_audit_log(self):
        src = _load(MIG_149)
        assert "CREATE TABLE IF NOT EXISTS client_audit_log" in src
        # Required columns
        for col in ("org_id", "actor_user_id", "actor_email", "action", "target", "details", "ip_address"):
            assert col in src, f"migration 149 missing column {col}"

    def test_migration_149_has_append_only_trigger(self):
        src = _load(MIG_149)
        assert "prevent_portal_audit_log_mutation" in src
        assert "enforce_client_audit_append_only" in src

    def test_migration_149_has_rls(self):
        src = _load(MIG_149)
        assert "ALTER TABLE client_audit_log ENABLE ROW LEVEL SECURITY" in src
        assert "client_audit_admin_bypass" in src

    def test_migration_149_does_not_create_partner_audit_log(self):
        """Migration 149 was refactored mid-flight to use the existing
        partner_activity_log infrastructure instead of a parallel table."""
        src = _load(MIG_149)
        assert "CREATE TABLE IF NOT EXISTS partner_audit_log" not in src


class TestMigration150DropsPartnerAuditLog:
    def test_migration_150_exists(self):
        assert os.path.exists(MIG_150)

    def test_migration_150_drops_partner_audit_log(self):
        src = _load(MIG_150)
        assert "DROP TABLE IF EXISTS partner_audit_log" in src

    def test_migration_150_is_transactional(self):
        src = _load(MIG_150)
        assert "BEGIN;" in src
        assert "COMMIT;" in src


class TestClientAuditHelper:
    def test_audit_client_action_exists(self):
        src = _load(CLIENT)
        assert "async def _audit_client_action(" in src

    def test_audit_helper_non_raising(self):
        src = _load(CLIENT)
        body = _get_func(src, "_audit_client_action")
        assert "try:" in body
        assert "except Exception" in body
        assert "logger.error" in body

    def test_audit_helper_writes_expected_columns(self):
        src = _load(CLIENT)
        body = _get_func(src, "_audit_client_action")
        for col in ("org_id", "actor_user_id", "actor_email", "action", "target", "details", "ip_address"):
            assert col in body, f"_audit_client_action missing column {col}"


class TestClientMutationsWired:
    def test_update_user_role_audits(self):
        src = _load(CLIENT)
        body = _get_func(src, "update_user_role")
        assert "_audit_client_action" in body
        assert '"USER_ROLE_CHANGED"' in body

    def test_set_password_audits(self):
        src = _load(CLIENT)
        body = _get_func(src, "set_password")
        assert "_audit_client_action" in body
        assert '"PASSWORD_CHANGED"' in body

    def test_remove_user_audits(self):
        src = _load(CLIENT)
        body = _get_func(src, "remove_user")
        assert "_audit_client_action" in body
        assert '"USER_REMOVED"' in body


# =============================================================================
# Partner side — extended existing enum, no parallel table
# =============================================================================

class TestPartnerActivityLoggerExtended:
    def test_drift_config_updated_event_type(self):
        src = _load(LOGGER)
        assert "DRIFT_CONFIG_UPDATED" in src

    def test_maintenance_window_event_types(self):
        src = _load(LOGGER)
        assert "MAINTENANCE_WINDOW_SET" in src
        assert "MAINTENANCE_WINDOW_CANCELLED" in src

    def test_new_events_have_categories(self):
        src = _load(LOGGER)
        # Each new event must appear in EVENT_CATEGORIES mapping
        for event in ("DRIFT_CONFIG_UPDATED", "MAINTENANCE_WINDOW_SET", "MAINTENANCE_WINDOW_CANCELLED"):
            assert f"PartnerEventType.{event}" in src.split("EVENT_CATEGORIES")[1], \
                f"{event} not in EVENT_CATEGORIES"


class TestPartnerMutationsWired:
    def test_update_partner_drift_config_logs_event(self):
        src = _load(PARTNERS)
        body = _get_func(src, "update_partner_drift_config")
        assert "log_partner_activity" in body
        assert "DRIFT_CONFIG_UPDATED" in body

    def test_set_partner_maintenance_logs_event(self):
        src = _load(PARTNERS)
        body = _get_func(src, "set_partner_maintenance")
        assert "log_partner_activity" in body
        assert "MAINTENANCE_WINDOW_SET" in body

    def test_cancel_partner_maintenance_logs_event(self):
        src = _load(PARTNERS)
        body = _get_func(src, "cancel_partner_maintenance")
        assert "log_partner_activity" in body
        assert "MAINTENANCE_WINDOW_CANCELLED" in body

    def test_partners_does_not_have_parallel_audit_helper(self):
        """Session 203 H3 uses the existing log_partner_activity infra —
        there should NOT be a parallel _audit_partner_action helper."""
        src = _load(PARTNERS)
        # The comment explaining this decision must be present
        assert "log_partner_activity" in src
        # And there should be no competing helper function
        tree = ast.parse(src)
        names = [
            n.name for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef)
        ]
        assert "_audit_partner_action" not in names

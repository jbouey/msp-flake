"""
Automated guardrails for the healing pipeline.

These tests prevent regression of fixes from Session 204's flywheel audit.
They verify structural invariants by inspecting source code and imports —
no running services required.

Round Table recommendation: run these in CI so the 7 fixes we shipped
can never silently break again.
"""
import json
import re
from pathlib import Path

import pytest

# Paths
BACKEND = Path(__file__).resolve().parent.parent
APPLIANCE = BACKEND.parent.parent.parent / "appliance"
RUNBOOKS_JSON = APPLIANCE / "internal" / "daemon" / "runbooks.json"
AGENT_API = BACKEND / "agent_api.py"
MAIN_PY = BACKEND.parent.parent / "main.py"
L2_PLANNER = BACKEND / "l2_planner.py"


# ---------------------------------------------------------------------------
# 1. Runbook keyword map IDs must exist in the embedded registry
# ---------------------------------------------------------------------------
class TestRunbookKeywordMapIntegrity:
    """Every runbook ID referenced in agent_api.py's keyword map must
    exist in the daemon's embedded runbooks.json.  Session 204 found 5
    non-existent IDs that caused 100% execution failure."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.runbooks = json.loads(RUNBOOKS_JSON.read_text())
        self.agent_api_src = AGENT_API.read_text()

    def _extract_map_ids(self) -> set:
        """Pull runbook IDs from the keyword map in agent_api.py."""
        # Match the block:  runbook_map = { ... }
        m = re.search(
            r"runbook_map\s*=\s*\{([^}]+)\}",
            self.agent_api_src,
            re.DOTALL,
        )
        assert m, "Could not find runbook_map in agent_api.py"
        ids = set(re.findall(r'"((?:RB|LIN|MAC)-[A-Z0-9-]+)"', m.group(1)))
        assert len(ids) > 0, "runbook_map appears empty"
        return ids

    def test_all_keyword_map_ids_exist_in_registry(self):
        map_ids = self._extract_map_ids()
        registry_ids = set(self.runbooks.keys())
        missing = map_ids - registry_ids
        assert not missing, (
            f"Runbook IDs in keyword map but NOT in runbooks.json: {missing}. "
            "These cause 100% execution failure on the appliance."
        )

    def test_registry_has_minimum_coverage(self):
        """Sanity: the registry should have a non-trivial number of runbooks."""
        assert len(self.runbooks) >= 20, (
            f"Only {len(self.runbooks)} runbooks in registry — expected 20+. "
            "Was the file truncated?"
        )


# ---------------------------------------------------------------------------
# 2. MONITORING_ONLY_CHECKS must be in sync between agent_api.py and main.py
# ---------------------------------------------------------------------------
class TestMonitoringOnlyChecksSync:
    """Both copies of MONITORING_ONLY_CHECKS must match.  A desync means
    one path blocks L2 for a check type while the other allows it."""

    def _extract_checks(self, source: str) -> set:
        """Extract the set literal from MONITORING_ONLY_CHECKS = {...}.
        Only considers uncommented lines (ignores # REMOVED notes)."""
        m = re.search(
            r"MONITORING_ONLY_CHECKS\s*=\s*\{([^}]+)\}",
            source,
            re.DOTALL,
        )
        assert m, "Could not find MONITORING_ONLY_CHECKS set"
        checks = set()
        for line in m.group(1).splitlines():
            stripped = line.strip()
            # Skip pure comment lines — they may mention removed items
            if stripped.startswith("#"):
                continue
            # Extract quoted strings from active (non-comment) code
            for item in re.findall(r'"([^"]+)"', stripped):
                checks.add(item)
        return checks

    def test_agent_api_and_main_in_sync(self):
        agent_checks = self._extract_checks(AGENT_API.read_text())
        main_checks = self._extract_checks(MAIN_PY.read_text())
        assert agent_checks == main_checks, (
            f"MONITORING_ONLY_CHECKS out of sync!\n"
            f"  Only in agent_api.py: {agent_checks - main_checks}\n"
            f"  Only in main.py:      {main_checks - agent_checks}"
        )

    def test_backup_not_configured_removed(self):
        """Session 204: backup_not_configured was blocking L2 targets.
        It MUST NOT be in MONITORING_ONLY_CHECKS."""
        agent_checks = self._extract_checks(AGENT_API.read_text())
        assert "backup_not_configured" not in agent_checks, (
            "backup_not_configured is back in MONITORING_ONLY_CHECKS! "
            "This blocks L2 healing for backup configuration."
        )


# ---------------------------------------------------------------------------
# 3. L1 remediation step recording exists in agent_api.py
# ---------------------------------------------------------------------------
class TestL1RemediationStepRecording:
    """agent_api.py must INSERT into incident_remediation_steps after
    creating an L1 order.  Without this, the flywheel auto-candidate
    scan never sees L1 successes and promotion stalls."""

    def test_insert_present(self):
        src = AGENT_API.read_text()
        assert "INSERT INTO incident_remediation_steps" in src, (
            "incident_remediation_steps INSERT missing from agent_api.py. "
            "L1 resolutions will be invisible to the promotion pipeline."
        )

    def test_insert_near_order_creation(self):
        """The INSERT should be within ~30 lines of 'Created remediation order'."""
        lines = AGENT_API.read_text().splitlines()
        insert_line = None
        log_line = None
        for i, line in enumerate(lines):
            if "INSERT INTO incident_remediation_steps" in line:
                insert_line = i
            if "Created remediation order" in line:
                log_line = i
        assert insert_line is not None, "INSERT not found"
        assert log_line is not None, "Log line not found"
        gap = abs(log_line - insert_line)
        assert gap < 30, (
            f"INSERT is {gap} lines from order creation log — "
            "may have been moved to the wrong code path"
        )


# ---------------------------------------------------------------------------
# 4. L2 circuit breaker exists in l2_planner.py
# ---------------------------------------------------------------------------
class TestCircuitBreakerExists:
    """l2_planner.py must have a circuit breaker to prevent burning
    compute credits when the LLM API is down."""

    def test_threshold_defined(self):
        src = L2_PLANNER.read_text()
        assert "CIRCUIT_BREAKER_THRESHOLD" in src, (
            "CIRCUIT_BREAKER_THRESHOLD missing from l2_planner.py"
        )

    def test_cooldown_defined(self):
        src = L2_PLANNER.read_text()
        assert "CIRCUIT_BREAKER_COOLDOWN_MINUTES" in src, (
            "CIRCUIT_BREAKER_COOLDOWN_MINUTES missing from l2_planner.py"
        )

    def test_threshold_is_reasonable(self):
        src = L2_PLANNER.read_text()
        m = re.search(r"CIRCUIT_BREAKER_THRESHOLD\s*=\s*(\d+)", src)
        assert m, "Cannot parse threshold value"
        val = int(m.group(1))
        assert 3 <= val <= 10, (
            f"Threshold is {val} — should be 3-10. "
            "Too low = false trips, too high = wasted credits."
        )

    def test_cooldown_is_reasonable(self):
        src = L2_PLANNER.read_text()
        m = re.search(r"CIRCUIT_BREAKER_COOLDOWN_MINUTES\s*=\s*(\d+)", src)
        assert m, "Cannot parse cooldown value"
        val = int(m.group(1))
        assert 5 <= val <= 60, (
            f"Cooldown is {val}min — should be 5-60. "
            "Too short = hammers a down API, too long = healing stalls."
        )


# ---------------------------------------------------------------------------
# 5. Check type registry covers all daemon check names
# ---------------------------------------------------------------------------
class TestCheckTypeRegistryCompleteness:
    """Every check name the Go daemon reports must exist in the check_type_registry
    migration. If a check is missing, it becomes invisible to scoring."""

    @pytest.fixture(autouse=True)
    def _load(self):
        migration = BACKEND / "migrations" / "157_check_type_registry.sql"
        assert migration.exists(), "Migration 157 (check_type_registry) not found"
        self.migration_sql = migration.read_text()

    def _extract_registry_names(self) -> set:
        """Pull all check_name values from INSERT statements in the migration."""
        # Match: ('check_name', ...
        names = set(re.findall(r"\('([a-zA-Z_\-]+)',\s*'(?:windows|linux|macos|network|system)'", self.migration_sql))
        return names

    def test_registry_has_minimum_checks(self):
        names = self._extract_registry_names()
        assert len(names) >= 50, (
            f"Only {len(names)} checks in registry — expected 50+. "
            "Was the migration truncated?"
        )

    def test_critical_windows_checks_present(self):
        """Core Windows compliance checks must be in the registry."""
        names = self._extract_registry_names()
        required = {
            "firewall_status", "windows_defender", "defender_exclusions",
            "bitlocker_status", "windows_update", "screen_lock_policy",
            "password_policy", "rogue_admin_users", "audit_logging",
            "smb_signing", "rdp_nla", "guest_account",
            "rogue_scheduled_tasks", "agent_status",
        }
        missing = required - names
        assert not missing, f"Critical Windows checks missing from registry: {missing}"

    def test_critical_linux_checks_present(self):
        """Core Linux compliance checks must be in the registry."""
        names = self._extract_registry_names()
        required = {
            "linux_firewall", "linux_ssh_config", "linux_user_accounts",
            "linux_audit_logging", "linux_disk_space", "linux_ntp_sync",
            "linux_kernel_params", "linux_unattended_upgrades",
        }
        missing = required - names
        assert not missing, f"Critical Linux checks missing from registry: {missing}"

    def test_monitoring_only_checks_flagged(self):
        """Known monitoring-only checks must have is_monitoring_only = true."""
        monitoring_checks = {
            "net_host_reachability", "device_unreachable",
            "AGENT-REDEPLOY-EXHAUSTED", "WIN-DEPLOY-UNREACHABLE",
            "linux_encryption", "backup_verification",
        }
        for check in monitoring_checks:
            assert check in self.migration_sql, f"{check} missing from registry"
            # Find the INSERT line for this check and verify is_monitoring_only
            pattern = rf"'{re.escape(check)}'.*?true\)"
            assert re.search(pattern, self.migration_sql, re.DOTALL), (
                f"{check} should have is_monitoring_only = true in registry"
            )

    def test_scoring_category_lookup_matches_registry(self):
        """The hardcoded CATEGORY_CHECKS in db_queries.py should be a SUBSET
        of what's in the registry. If a check is in the hardcoded dict but
        not in the registry, the registry is incomplete."""
        db_queries_src = (BACKEND / "db_queries.py").read_text()
        # Extract check names from CATEGORY_CHECKS list values (not dict keys).
        # Each category maps to a list of check names inside square brackets.
        hardcoded_checks = set()
        for m in re.finditer(r'\[([^\]]+)\]', db_queries_src[
            db_queries_src.index("CATEGORY_CHECKS"):db_queries_src.index("_CHECK_TYPE_TO_CATEGORY")
        ]):
            hardcoded_checks.update(re.findall(r'"([a-z][a-z0-9_]+)"', m.group(1)))

        registry_names = self._extract_registry_names()
        missing = hardcoded_checks - registry_names
        # Legacy aliases: old Python agent check names kept for backward compat.
        # These don't need to be in the registry — the daemon doesn't use them.
        legacy_aliases = {
            "bitlocker", "windows_bitlocker_status", "linux_crypto",
            "windows_smb_signing", "windows_screen_lock_policy",
            "windows_password_policy", "linux_accounts", "linux_permissions",
            "windows_service_spooler", "windows_service_w32time",
            "windows_service_wuauserv", "critical_services", "linux_services",
            "network_posture_windows", "windows_network_profile", "windows_dns_config",
            "linux_network", "ntp_sync", "linux_time_sync", "windows_smb1_protocol",
            "windows_audit_policy", "linux_audit", "linux_logging",
            "backup_status", "windows_backup_status", "windows_defender_exclusions",
            "firewall", "windows_firewall_status", "nixos_generation", "linux_patching",
            "disk_space", "linux_kernel", "linux_boot", "linux_integrity", "linux_mac",
            "linux_cron", "linux_banner", "linux_incident_response",
            "windows_registry_run_persistence", "windows_scheduled_task_persistence",
            "windows_wmi_event_persistence",
        }
        real_missing = missing - legacy_aliases
        assert not real_missing, (
            f"Check names in CATEGORY_CHECKS but not in registry or legacy aliases: {real_missing}. "
            "Add them to migration 157."
        )

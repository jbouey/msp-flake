-- Migration 284: bridge agent runbook IDs to canonical runbooks.runbook_id
--
-- Outside-audit finding (2026-05-06, RT-DM Issue #1):
-- The agent's L1 baseline rules use IDs like `L1-SVC-DNS-001`,
-- `L1-LIN-PERM-002`, etc. (per packages/compliance-agent/src/
-- compliance_agent/rules/l1_baseline.json). The backend `runbooks`
-- table uses `LIN-*`, `WIN-*`, `RB-*`, `ESC-*` IDs (per migrations
-- 010, 027, 050, 051).
--
-- The two namespaces never overlap. As a result:
--   `SELECT … FROM execution_telemetry et JOIN runbooks r ON
--    et.runbook_id = r.runbook_id`
-- returns 0 rows. Per-runbook execution counts on the Fleet
-- Intelligence dashboard show 0 forever. CLAUDE.md already documents
-- the workaround ("Match by incident_type + hostname/site_id, not
-- runbook_id") but that is a downstream patch, not a fix.
--
-- This migration adds an `agent_runbook_id TEXT UNIQUE` column on
-- `runbooks` and backfills it via a heuristic mapping for the L1
-- baseline rules that have a clear backend counterpart. For agent
-- L1-* rules with no obvious counterpart, we INSERT placeholder
-- runbook rows so every L1-* ID has a row in the canonical table
-- and the JOIN through `agent_runbook_id` always succeeds.
--
-- Dashboard queries should JOIN on `agent_runbook_id` (since
-- `execution_telemetry.runbook_id` holds the agent's L1-* form).
-- See db_queries.py + prometheus_metrics.py updates in the same
-- commit as this migration.

-- ─────────────────────────────────────────────────────────────────
-- 1. Add the bridge column + index
-- ─────────────────────────────────────────────────────────────────

ALTER TABLE runbooks
    ADD COLUMN IF NOT EXISTS agent_runbook_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_runbooks_agent_runbook_id
    ON runbooks (agent_runbook_id)
    WHERE agent_runbook_id IS NOT NULL;

COMMENT ON COLUMN runbooks.agent_runbook_id IS
    'Migration 284 (RT-DM Issue #1, 2026-05-06): bridge to the agent-'
    'side runbook ID convention (L1-*) used in '
    'packages/compliance-agent/src/compliance_agent/rules/l1_baseline.'
    'json. The agent emits this form in execution_telemetry.'
    'runbook_id; dashboards JOIN on this column to recover per-runbook '
    'execution counts.';

-- ─────────────────────────────────────────────────────────────────
-- 2. Backfill: existing runbooks rows whose runbook_id has an
--    obvious L1-* counterpart
-- ─────────────────────────────────────────────────────────────────

-- Linux baseline mapping: backend `LIN-*-NNN` → agent `L1-LIN-*-NNN`
UPDATE runbooks SET agent_runbook_id = 'L1-LIN-AUDIT-001' WHERE runbook_id = 'LIN-AUDIT-001';
UPDATE runbooks SET agent_runbook_id = 'L1-LIN-AUDIT-002' WHERE runbook_id = 'LIN-AUDIT-002';
UPDATE runbooks SET agent_runbook_id = 'L1-LIN-FW-001'    WHERE runbook_id = 'LIN-FW-001';
UPDATE runbooks SET agent_runbook_id = 'L1-LIN-MAC-001'   WHERE runbook_id = 'LIN-MAC-001';
UPDATE runbooks SET agent_runbook_id = 'L1-LIN-PATCH-001' WHERE runbook_id = 'LIN-PATCH-001';
UPDATE runbooks SET agent_runbook_id = 'L1-LIN-PERM-001'  WHERE runbook_id = 'LIN-PERM-001';
UPDATE runbooks SET agent_runbook_id = 'L1-LIN-PERM-002'  WHERE runbook_id = 'LIN-PERM-002';
UPDATE runbooks SET agent_runbook_id = 'L1-LIN-PERM-003'  WHERE runbook_id = 'LIN-PERM-003';
UPDATE runbooks SET agent_runbook_id = 'L1-LIN-SSH-001'   WHERE runbook_id = 'LIN-SSH-001';
UPDATE runbooks SET agent_runbook_id = 'L1-LIN-SSH-002'   WHERE runbook_id = 'LIN-SSH-002';
UPDATE runbooks SET agent_runbook_id = 'L1-LIN-SSH-003'   WHERE runbook_id = 'LIN-SSH-003';
UPDATE runbooks SET agent_runbook_id = 'L1-LIN-SVC-001'   WHERE runbook_id = 'LIN-SVC-001';
UPDATE runbooks SET agent_runbook_id = 'L1-LIN-SVC-002'   WHERE runbook_id = 'LIN-SVC-002';
UPDATE runbooks SET agent_runbook_id = 'L1-LIN-SVC-003'   WHERE runbook_id = 'LIN-SVC-003';
UPDATE runbooks SET agent_runbook_id = 'L1-LIN-SVC-004'   WHERE runbook_id = 'LIN-SVC-004';

-- ─────────────────────────────────────────────────────────────────
-- 3. INSERT placeholder runbook rows for every L1-* agent ID that
--    has no existing backend counterpart. This guarantees that the
--    JOIN `execution_telemetry.runbook_id = runbooks.agent_runbook_id`
--    always finds a row for any agent-emitted telemetry.
--
-- The placeholder rows carry the agent rule's category and a
-- "synced from agent" marker in name + description. Steps are
-- empty for now; if the backend later wants full step content for
-- one of these, a follow-up migration can populate it.
-- ─────────────────────────────────────────────────────────────────

INSERT INTO runbooks (
    runbook_id, agent_runbook_id, name, description, category,
    check_type, severity, is_disruptive, hipaa_controls, steps
) VALUES
    -- Active Directory + identity
    ('AGENT-L1-AD-001',           'L1-AD-001',           'AD domain join check (agent-synced)', 'L1 baseline rule synced from agent l1_baseline.json', 'identity', 'ad_domain_join', 'medium', false, ARRAY['164.308(a)(3)'], '[]'::jsonb),
    ('AGENT-L1-NLA-001',          'L1-NLA-001',          'NLA enabled check (agent-synced)', 'Network Level Authentication on RDP', 'identity', 'rdp_nla', 'medium', false, ARRAY['164.312(a)(2)'], '[]'::jsonb),
    ('AGENT-L1-NTLM-001',         'L1-NTLM-001',         'NTLMv1 disable check (agent-synced)', 'NTLMv1 must be disabled', 'identity', 'ntlmv1_disabled', 'high', false, ARRAY['164.312(a)(2)'], '[]'::jsonb),
    ('AGENT-L1-PASSWORD-001',     'L1-PASSWORD-001',     'Password policy check (agent-synced)', 'L1 password policy enforcement', 'identity', 'password_policy', 'high', false, ARRAY['164.308(a)(5)'], '[]'::jsonb),
    ('AGENT-L1-LOCKOUT-001',      'L1-LOCKOUT-001',      'Account lockout policy check (agent-synced)', 'Account lockout threshold + duration', 'identity', 'account_lockout', 'medium', false, ARRAY['164.308(a)(5)'], '[]'::jsonb),
    ('AGENT-L1-PROFILE-001',      'L1-PROFILE-001',      'User profile check (agent-synced)', 'L1 user profile baseline', 'identity', 'user_profile', 'low', false, ARRAY['164.308(a)(3)'], '[]'::jsonb),
    ('AGENT-L1-UAC-001',          'L1-UAC-001',          'UAC enabled check (agent-synced)', 'User Account Control must be enabled', 'identity', 'uac_enabled', 'high', false, ARRAY['164.312(a)(1)'], '[]'::jsonb),
    ('AGENT-L1-CREDGUARD-001',    'L1-CREDGUARD-001',    'Credential Guard check (agent-synced)', 'Credential Guard enabled (Win10+)', 'identity', 'credential_guard', 'medium', false, ARRAY['164.312(a)(2)'], '[]'::jsonb),

    -- Endpoint protection + persistence
    ('AGENT-L1-ANTIVIRUS-001',    'L1-ANTIVIRUS-001',    'Antivirus enabled check (agent-synced)', 'AV must be running + signatures fresh', 'av_endpoint', 'antivirus', 'critical', false, ARRAY['164.308(a)(5)'], '[]'::jsonb),
    ('AGENT-L1-DEFENDER-001',     'L1-DEFENDER-001',     'Defender enabled check (agent-synced)', 'Microsoft Defender enabled + tamper protected', 'av_endpoint', 'defender', 'high', false, ARRAY['164.308(a)(5)'], '[]'::jsonb),
    ('AGENT-L1-BACKDOOR-001',     'L1-BACKDOOR-001',     'Backdoor process check (agent-synced)', 'Detect known-backdoor process patterns', 'threat_detection', 'backdoor_process', 'critical', false, ARRAY['164.308(a)(6)'], '[]'::jsonb),
    ('AGENT-L1-PERSIST-REG-001',  'L1-PERSIST-REG-001',  'Registry persistence check (agent-synced)', 'Detect rogue Run/RunOnce registry entries', 'threat_detection', 'registry_persistence', 'high', false, ARRAY['164.308(a)(6)'], '[]'::jsonb),
    ('AGENT-L1-PERSIST-TASK-001', 'L1-PERSIST-TASK-001', 'Scheduled task persistence (agent-synced)', 'Detect rogue scheduled tasks', 'threat_detection', 'task_persistence', 'high', false, ARRAY['164.308(a)(6)'], '[]'::jsonb),

    -- Auditing + logging
    ('AGENT-L1-AUDIT-001',        'L1-AUDIT-001',        'Audit policy check (agent-synced)', 'L1 audit subcategory baseline', 'logging', 'audit_policy', 'high', false, ARRAY['164.312(b)'], '[]'::jsonb),
    ('AGENT-L1-EVENTLOG-001',     'L1-EVENTLOG-001',     'Event log size check (agent-synced)', 'Security event log retention + size', 'logging', 'event_log', 'medium', false, ARRAY['164.312(b)'], '[]'::jsonb),
    ('AGENT-L1-LOGGING-001',      'L1-LOGGING-001',      'Logging service check (agent-synced)', 'Required logging services running', 'logging', 'logging_service', 'high', false, ARRAY['164.312(b)'], '[]'::jsonb),

    -- Backup + integrity
    ('AGENT-L1-BACKUP-001',       'L1-BACKUP-001',       'Backup health check (agent-synced)', 'Last successful backup within RPO', 'backup', 'backup_health', 'high', false, ARRAY['164.308(a)(7)'], '[]'::jsonb),
    ('AGENT-L1-DISK-001',         'L1-DISK-001',         'Disk free space check (agent-synced)', 'Free space above threshold', 'system', 'disk_free', 'high', false, ARRAY[]::text[], '[]'::jsonb),
    ('AGENT-L1-BITLOCKER-001',    'L1-BITLOCKER-001',    'BitLocker encryption check (agent-synced)', 'System drive encrypted with BitLocker', 'encryption', 'bitlocker', 'critical', false, ARRAY['164.312(a)(2)','164.312(e)(2)'], '[]'::jsonb),

    -- Patching
    ('AGENT-L1-PATCH-001',        'L1-PATCH-001',        'Patch level check (agent-synced)', 'Required security patches installed', 'patching', 'patch_level', 'critical', false, ARRAY['164.308(a)(5)'], '[]'::jsonb),

    -- Networking + firewall
    ('AGENT-L1-FIREWALL-001',     'L1-FIREWALL-001',     'Host firewall enabled (agent-synced)', 'Host firewall service running + active', 'firewall', 'firewall_enabled', 'high', false, ARRAY['164.308(a)(5)'], '[]'::jsonb),
    ('AGENT-L1-FIREWALL-002',     'L1-FIREWALL-002',     'Firewall rule baseline (agent-synced)', 'Inbound rules match baseline', 'firewall', 'firewall_rules', 'medium', false, ARRAY['164.308(a)(5)'], '[]'::jsonb),
    ('AGENT-L1-NIXOS-FW-001',     'L1-NIXOS-FW-001',     'NixOS firewall check (agent-synced)', 'NixOS nftables rules baseline', 'firewall', 'nixos_firewall', 'high', false, ARRAY['164.308(a)(5)'], '[]'::jsonb),
    ('AGENT-L1-NETWORK-001',      'L1-NETWORK-001',      'Network baseline check (agent-synced)', 'L1 network configuration baseline', 'network', 'network_baseline', 'medium', false, ARRAY[]::text[], '[]'::jsonb),
    ('AGENT-L1-SMB-001',          'L1-SMB-001',          'SMBv1 disable check (agent-synced)', 'SMBv1 must be disabled', 'network', 'smbv1_disabled', 'critical', false, ARRAY['164.308(a)(5)'], '[]'::jsonb),

    -- DNS + naming
    ('AGENT-L1-DNS-001',          'L1-DNS-001',          'DNS configuration check (agent-synced)', 'L1 DNS resolver baseline', 'network', 'dns_config', 'medium', false, ARRAY[]::text[], '[]'::jsonb),
    ('AGENT-L1-DNSSVC-001',       'L1-DNSSVC-001',       'DNS service check (agent-synced)', 'DNS Client service running', 'service', 'dns_service', 'high', false, ARRAY[]::text[], '[]'::jsonb),
    ('AGENT-L1-SVC-DNS-001',      'L1-SVC-DNS-001',      'DNS service health (agent-synced)', 'DNS Client service responsive', 'service', 'svc_dns', 'high', false, ARRAY[]::text[], '[]'::jsonb),

    -- Time
    ('AGENT-L1-NTP-001',          'L1-NTP-001',          'NTP configuration check (agent-synced)', 'L1 time-source baseline', 'system', 'ntp_config', 'medium', false, ARRAY[]::text[], '[]'::jsonb),
    ('AGENT-L1-TIMESVC-001',      'L1-TIMESVC-001',      'Time service health (agent-synced)', 'Time service running + synced', 'service', 'time_service', 'medium', false, ARRAY[]::text[], '[]'::jsonb),
    ('AGENT-L1-SVC-W32TIME-001',  'L1-SVC-W32TIME-001',  'W32Time service (agent-synced)', 'Windows Time Service running', 'service', 'svc_w32time', 'medium', false, ARRAY[]::text[], '[]'::jsonb),

    -- Other Windows services
    ('AGENT-L1-DHCPSVC-001',      'L1-DHCPSVC-001',      'DHCP Client service (agent-synced)', 'DHCP Client service running', 'service', 'dhcp_service', 'low', false, ARRAY[]::text[], '[]'::jsonb),
    ('AGENT-L1-SERVICE-001',      'L1-SERVICE-001',      'Service baseline check (agent-synced)', 'L1 required-service baseline', 'service', 'service_baseline', 'medium', false, ARRAY[]::text[], '[]'::jsonb),
    ('AGENT-L1-SVC-SPOOLER-001',  'L1-SVC-SPOOLER-001',  'Print Spooler check (agent-synced)', 'Print Spooler disabled (PrintNightmare)', 'service', 'svc_spooler', 'high', false, ARRAY['164.308(a)(5)'], '[]'::jsonb),

    -- Linux baselines NOT mapped to existing LIN-* runbooks
    ('AGENT-L1-LIN-ACCT-001',     'L1-LIN-ACCT-001',     'Linux account baseline (agent-synced)', 'Linux user account L1 baseline', 'identity', 'lin_account', 'medium', false, ARRAY['164.308(a)(3)'], '[]'::jsonb),
    ('AGENT-L1-LIN-INTEGRITY-001','L1-LIN-INTEGRITY-001','Linux integrity check (agent-synced)', 'AIDE/integrity baseline', 'integrity', 'lin_integrity', 'high', false, ARRAY['164.312(c)'], '[]'::jsonb),
    ('AGENT-L1-LIN-IR-001',       'L1-LIN-IR-001',       'Linux IR readiness (agent-synced)', 'Incident-response logging present', 'logging', 'lin_ir_readiness', 'medium', false, ARRAY['164.308(a)(6)'], '[]'::jsonb),
    ('AGENT-L1-LIN-NTP-001',      'L1-LIN-NTP-001',      'Linux NTP check (agent-synced)', 'systemd-timesyncd active + synced', 'system', 'lin_ntp', 'medium', false, ARRAY[]::text[], '[]'::jsonb)
ON CONFLICT (runbook_id) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────
-- 4. Sanity assertion: every L1-* agent rule referenced above OR
--    backfilled in step 2 should now have a runbooks row.
--    Count check via NOTICE (informational; not a hard fail).
-- ─────────────────────────────────────────────────────────────────

DO $$
DECLARE
    n_with_agent_id INT;
BEGIN
    SELECT COUNT(*) INTO n_with_agent_id
      FROM runbooks
     WHERE agent_runbook_id IS NOT NULL;
    RAISE NOTICE 'mig 284: % runbooks rows now carry agent_runbook_id', n_with_agent_id;
END$$;

-- ─────────────────────────────────────────────────────────────────
-- Rollback (manual)
-- ─────────────────────────────────────────────────────────────────
-- DROP INDEX IF EXISTS idx_runbooks_agent_runbook_id;
-- ALTER TABLE runbooks DROP COLUMN IF EXISTS agent_runbook_id;
-- DELETE FROM runbooks WHERE runbook_id LIKE 'AGENT-L1-%';
-- (The placeholder rows are AGENT-L1-* prefixed; the bridge column
--  drop reverts the join semantics. Existing execution_telemetry
--  rows are unaffected.)

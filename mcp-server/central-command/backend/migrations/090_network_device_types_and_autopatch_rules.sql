-- Migration 090: Network device credential types + auto-patching L1 rules + backup verification
--
-- 1. Auto-patching L1 rules for Windows, Linux, macOS
-- 2. Backup verification L1 rules + runbooks
-- 3. Network device L1 rules + runbooks + framework mappings

-- =============================================================================
-- AUTO-PATCHING L1 RULES
-- =============================================================================

-- Windows patching (WIN-PATCH-001 runbook already exists)
INSERT INTO l1_rules (rule_id, incident_pattern, confidence, runbook_id, source, enabled)
VALUES ('L1-WIN-PATCH-001', '{"incident_type": "windows_updates"}', 0.75, 'WIN-PATCH-001', 'builtin', true)
ON CONFLICT (rule_id) DO NOTHING;

-- Linux patching (LIN-UPGRADES-001 runbook already exists)
INSERT INTO l1_rules (rule_id, incident_pattern, confidence, runbook_id, source, enabled)
VALUES ('L1-LIN-PATCH-001', '{"incident_type": "linux_unattended_upgrades"}', 0.75, 'LIN-UPGRADES-001', 'builtin', true)
ON CONFLICT (rule_id) DO NOTHING;

-- macOS patching (MAC-UPD-001 runbook already exists)
INSERT INTO l1_rules (rule_id, incident_pattern, confidence, runbook_id, source, enabled)
VALUES ('L1-MAC-PATCH-001', '{"incident_type": "macos_auto_update"}', 0.75, 'MAC-UPD-001', 'builtin', true)
ON CONFLICT (rule_id) DO NOTHING;

-- =============================================================================
-- BACKUP VERIFICATION L1 RULES + RUNBOOKS
-- =============================================================================

-- Windows backup runbook (escalation — VSS config is site-specific)
INSERT INTO runbooks (runbook_id, name, description, category, severity, steps, is_disruptive, check_type)
VALUES (
    'ESC-WIN-BACKUP',
    'Windows Backup Verification Failed',
    'Escalation: No recent VSS snapshot or Windows Backup found. Reports current backup status for human review.',
    'backup',
    'high',
    '[{"action": "powershell", "script": "Get-ComputerRestorePoint | Sort-Object -Property CreationTime -Descending | Select-Object -First 3 | ConvertTo-Json; vssadmin list shadows 2>$null | Select-String ''Shadow Copy'' | Select-Object -First 5; Get-WBSummary 2>$null | ConvertTo-Json"}]'::jsonb,
    false,
    'windows_backup'
)
ON CONFLICT (runbook_id) DO NOTHING;

-- Linux backup freshness runbook (escalation — cannot auto-configure backups)
INSERT INTO runbooks (runbook_id, name, description, category, severity, steps, is_disruptive, check_type)
VALUES (
    'ESC-LIN-BACKUP',
    'Linux Backup Verification Failed',
    'Escalation: No recent backup found. Checks common backup tools (restic, borg, rsync cron) and reports findings.',
    'backup',
    'high',
    '[{"action": "ssh", "script": "which restic borg rsync 2>/dev/null; crontab -l 2>/dev/null | grep -iE ''backup|restic|borg|rsync''; ls -la /var/backups/ 2>/dev/null | tail -5; find /var/backups/ -maxdepth 1 -mtime -1 -type f 2>/dev/null | head -5"}]'::jsonb,
    false,
    'linux_backup_freshness'
)
ON CONFLICT (runbook_id) DO NOTHING;

-- L1 rules for backup verification
INSERT INTO l1_rules (rule_id, incident_pattern, confidence, runbook_id, source, enabled)
VALUES
    ('L1-WIN-BACKUP-001', '{"incident_type": "windows_backup"}', 0.70, 'ESC-WIN-BACKUP', 'builtin', true),
    ('L1-LIN-BACKUP-001', '{"incident_type": "linux_backup_freshness"}', 0.70, 'ESC-LIN-BACKUP', 'builtin', true),
    ('L1-MAC-BACKUP-001', '{"incident_type": "macos_time_machine"}', 0.80, 'MAC-TM-001', 'builtin', true)
ON CONFLICT (rule_id) DO NOTHING;

-- =============================================================================
-- NETWORK DEVICE RUNBOOKS (all escalation — advisory only, never auto-remediate)
-- =============================================================================

INSERT INTO runbooks (runbook_id, name, description, category, severity, steps, is_disruptive, check_type)
VALUES
    ('ESC-NET-PORTS', 'Unexpected Network Ports Detected',
     'Escalation: Unexpected open ports found on network infrastructure. L2 generates vendor-specific remediation commands.',
     'network', 'high',
     '[{"action": "escalate", "description": "Review unexpected open ports on network device. L2 will generate vendor-specific shutdown commands if available."}]'::jsonb,
     false, 'network_unexpected_ports'),

    ('ESC-NET-SVC', 'Expected Network Service Missing',
     'Escalation: A monitored network service is not responding. L2 provides diagnostic commands.',
     'network', 'medium',
     '[{"action": "escalate", "description": "Expected network service is down. Check device power, connectivity, and service configuration."}]'::jsonb,
     false, 'network_expected_services'),

    ('ESC-NET-REACH', 'Network Host Unreachable',
     'Escalation: A monitored host is not reachable on expected ports. Check physical connectivity and device status.',
     'network', 'critical',
     '[{"action": "escalate", "description": "Network host is unreachable. Verify physical connectivity, power status, and routing."}]'::jsonb,
     false, 'network_host_unreachable'),

    ('ESC-NET-DNS', 'DNS Resolution Failure',
     'Escalation: DNS resolution test failed. Verify DNS server configuration and upstream connectivity.',
     'network', 'high',
     '[{"action": "escalate", "description": "DNS resolution failed. Check DNS server status, forwarder configuration, and upstream connectivity."}]'::jsonb,
     false, 'network_dns_resolution')
ON CONFLICT (runbook_id) DO NOTHING;

-- Network device L1 rules (all escalation)
INSERT INTO l1_rules (rule_id, incident_pattern, confidence, runbook_id, source, enabled)
VALUES
    ('L1-NET-PORT-001', '{"incident_type": "network_unexpected_ports"}', 0.90, 'ESC-NET-PORTS', 'builtin', true),
    ('L1-NET-SVC-001', '{"incident_type": "network_expected_services"}', 0.85, 'ESC-NET-SVC', 'builtin', true),
    ('L1-NET-REACH-001', '{"incident_type": "network_host_unreachable"}', 0.90, 'ESC-NET-REACH', 'builtin', true),
    ('L1-NET-DNS-001', '{"incident_type": "network_dns_resolution"}', 0.90, 'ESC-NET-DNS', 'builtin', true)
ON CONFLICT (rule_id) DO NOTHING;

-- =============================================================================
-- FRAMEWORK CONTROL MAPPINGS
-- =============================================================================

-- Windows patching controls
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.308(a)(5)(ii)(B)', 'WIN-PATCH-001', true),
    ('soc2', 'CC7.1', 'WIN-PATCH-001', true),
    ('pci_dss', '6.2', 'WIN-PATCH-001', true),
    ('nist_csf', 'PR.IP-12', 'WIN-PATCH-001', true),
    ('cis', '3.4', 'WIN-PATCH-001', true)
ON CONFLICT DO NOTHING;

-- Windows backup controls
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.308(a)(7)(ii)(A)', 'ESC-WIN-BACKUP', true),
    ('soc2', 'A1.2', 'ESC-WIN-BACKUP', false),
    ('pci_dss', '10.7', 'ESC-WIN-BACKUP', false),
    ('nist_csf', 'PR.IP-4', 'ESC-WIN-BACKUP', true),
    ('cis', '10.1', 'ESC-WIN-BACKUP', true)
ON CONFLICT DO NOTHING;

-- Linux backup controls
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.308(a)(7)(ii)(A)', 'ESC-LIN-BACKUP', false),
    ('soc2', 'A1.2', 'ESC-LIN-BACKUP', false),
    ('pci_dss', '10.7', 'ESC-LIN-BACKUP', false),
    ('nist_csf', 'PR.IP-4', 'ESC-LIN-BACKUP', false),
    ('cis', '10.1', 'ESC-LIN-BACKUP', false)
ON CONFLICT DO NOTHING;

-- Network device framework control mappings
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    -- Unexpected ports
    ('hipaa', '164.312(e)(1)', 'ESC-NET-PORTS', false),
    ('soc2', 'CC6.6', 'ESC-NET-PORTS', false),
    ('pci_dss', '1.1.6', 'ESC-NET-PORTS', true),
    ('nist_csf', 'DE.CM-7', 'ESC-NET-PORTS', true),
    ('cis', '9.2', 'ESC-NET-PORTS', true),
    -- Missing services
    ('hipaa', '164.308(a)(7)(i)', 'ESC-NET-SVC', false),
    ('soc2', 'A1.2', 'ESC-NET-SVC', false),
    ('pci_dss', '10.7', 'ESC-NET-SVC', false),
    ('nist_csf', 'DE.CM-1', 'ESC-NET-SVC', true),
    ('cis', '9.1', 'ESC-NET-SVC', true),
    -- Host unreachable
    ('hipaa', '164.308(a)(7)(ii)(A)', 'ESC-NET-REACH', false),
    ('soc2', 'A1.1', 'ESC-NET-REACH', false),
    ('pci_dss', '10.7', 'ESC-NET-REACH', false),
    ('nist_csf', 'DE.AE-5', 'ESC-NET-REACH', true),
    ('cis', '9.3', 'ESC-NET-REACH', true),
    -- DNS failure
    ('hipaa', '164.312(e)(1)', 'ESC-NET-DNS', false),
    ('soc2', 'CC6.6', 'ESC-NET-DNS', false),
    ('pci_dss', '1.1', 'ESC-NET-DNS', false),
    ('nist_csf', 'PR.AC-5', 'ESC-NET-DNS', false),
    ('cis', '9.4', 'ESC-NET-DNS', true)
ON CONFLICT DO NOTHING;

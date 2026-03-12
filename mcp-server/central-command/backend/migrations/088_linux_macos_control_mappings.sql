-- Migration 088: Linux + macOS framework control-to-runbook mappings
--
-- Fills the gap from Migration 086 which only seeded Windows/Network mappings.
-- Maps all Linux and macOS runbooks to HIPAA, SOC2, PCI-DSS, NIST CSF, and CIS controls.

-- =============================================================================
-- LINUX RUNBOOKS → FRAMEWORK CONTROLS
-- =============================================================================

-- linux_firewall → LIN-FW-001
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.312(e)(1)', 'LIN-FW-001', true),
    ('soc2', 'CC6.6', 'LIN-FW-001', true),
    ('pci_dss', '1.1', 'LIN-FW-001', true),
    ('nist_csf', 'PR.AC-5', 'LIN-FW-001', true),
    ('cis', '3.5', 'LIN-FW-001', true)
ON CONFLICT DO NOTHING;

-- linux_ssh_config → LIN-SSH-001
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.312(a)(2)(i)', 'LIN-SSH-001', true),
    ('soc2', 'CC6.1', 'LIN-SSH-001', true),
    ('pci_dss', '2.2.7', 'LIN-SSH-001', true),
    ('nist_csf', 'PR.AC-7', 'LIN-SSH-001', true),
    ('cis', '5.2', 'LIN-SSH-001', true)
ON CONFLICT DO NOTHING;

-- linux_failed_services → LIN-SVC-001
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.308(a)(7)(i)', 'LIN-SVC-001', true),
    ('soc2', 'A1.2', 'LIN-SVC-001', true),
    ('pci_dss', '10.7', 'LIN-SVC-001', true),
    ('nist_csf', 'DE.CM-4', 'LIN-SVC-001', true),
    ('cis', '6.1', 'LIN-SVC-001', true)
ON CONFLICT DO NOTHING;

-- linux_disk_space → LIN-DISK-001
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.310(d)(2)(iv)', 'LIN-DISK-001', true),
    ('soc2', 'A1.1', 'LIN-DISK-001', true),
    ('pci_dss', '10.7', 'LIN-DISK-001', false),
    ('nist_csf', 'PR.DS-4', 'LIN-DISK-001', true),
    ('cis', '6.2', 'LIN-DISK-001', true)
ON CONFLICT DO NOTHING;

-- linux_suid_binaries → LIN-SUID-001
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.312(c)(1)', 'LIN-SUID-001', true),
    ('soc2', 'CC7.1', 'LIN-SUID-001', true),
    ('pci_dss', '6.2', 'LIN-SUID-001', true),
    ('nist_csf', 'DE.CM-5', 'LIN-SUID-001', true),
    ('cis', '6.1.13', 'LIN-SUID-001', true)
ON CONFLICT DO NOTHING;

-- linux_audit_logging → LIN-AUDIT-001
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.312(b)', 'LIN-AUDIT-001', true),
    ('soc2', 'CC7.2', 'LIN-AUDIT-001', true),
    ('pci_dss', '10.2', 'LIN-AUDIT-001', true),
    ('nist_csf', 'DE.AE-3', 'LIN-AUDIT-001', true),
    ('cis', '4.1', 'LIN-AUDIT-001', true)
ON CONFLICT DO NOTHING;

-- linux_kernel_params → LIN-KERN-001
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.312(c)(1)', 'LIN-KERN-001', false),
    ('soc2', 'CC6.1', 'LIN-KERN-001', false),
    ('pci_dss', '2.2', 'LIN-KERN-001', true),
    ('nist_csf', 'PR.IP-1', 'LIN-KERN-001', true),
    ('cis', '3.1', 'LIN-KERN-001', true)
ON CONFLICT DO NOTHING;

-- linux_file_permissions → LIN-PERM-001
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.312(a)(1)', 'LIN-PERM-001', true),
    ('soc2', 'CC6.3', 'LIN-PERM-001', true),
    ('pci_dss', '7.1', 'LIN-PERM-001', true),
    ('nist_csf', 'PR.AC-4', 'LIN-PERM-001', true),
    ('cis', '6.1.2', 'LIN-PERM-001', true)
ON CONFLICT DO NOTHING;

-- linux_unattended_upgrades → LIN-UPGRADES-001
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.308(a)(5)(ii)(B)', 'LIN-UPGRADES-001', true),
    ('soc2', 'CC7.1', 'LIN-UPGRADES-001', false),
    ('pci_dss', '6.2', 'LIN-UPGRADES-001', false),
    ('nist_csf', 'PR.IP-12', 'LIN-UPGRADES-001', true),
    ('cis', '1.9', 'LIN-UPGRADES-001', true)
ON CONFLICT DO NOTHING;

-- linux_log_forwarding → LIN-LOG-001
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.312(b)', 'LIN-LOG-001', false),
    ('soc2', 'CC7.2', 'LIN-LOG-001', false),
    ('pci_dss', '10.5', 'LIN-LOG-001', true),
    ('nist_csf', 'PR.PT-1', 'LIN-LOG-001', true),
    ('cis', '4.2', 'LIN-LOG-001', true)
ON CONFLICT DO NOTHING;

-- linux_cron_review → LIN-CRON-001
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.308(a)(3)(ii)(A)', 'LIN-CRON-001', true),
    ('soc2', 'CC6.2', 'LIN-CRON-001', true),
    ('pci_dss', '10.2.7', 'LIN-CRON-001', true),
    ('nist_csf', 'DE.CM-3', 'LIN-CRON-001', true),
    ('cis', '5.1', 'LIN-CRON-001', true)
ON CONFLICT DO NOTHING;

-- linux_cert_expiry → LIN-CERT-001
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.312(e)(2)(ii)', 'LIN-CERT-001', true),
    ('soc2', 'CC6.7', 'LIN-CERT-001', true),
    ('pci_dss', '4.1', 'LIN-CERT-001', true),
    ('nist_csf', 'PR.DS-2', 'LIN-CERT-001', true),
    ('cis', '3.4', 'LIN-CERT-001', true)
ON CONFLICT DO NOTHING;

-- linux_open_ports → ESC-LIN-PORTS-001 (escalation)
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.312(e)(1)', 'ESC-LIN-PORTS-001', false),
    ('soc2', 'CC6.6', 'ESC-LIN-PORTS-001', false),
    ('pci_dss', '1.1.6', 'ESC-LIN-PORTS-001', true),
    ('nist_csf', 'DE.CM-7', 'ESC-LIN-PORTS-001', true),
    ('cis', '3.5', 'ESC-LIN-PORTS-001', false)
ON CONFLICT DO NOTHING;

-- linux_user_accounts → ESC-LIN-USERS-001 (escalation)
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.308(a)(3)(ii)(B)', 'ESC-LIN-USERS-001', true),
    ('soc2', 'CC6.2', 'ESC-LIN-USERS-001', false),
    ('pci_dss', '8.1.4', 'ESC-LIN-USERS-001', true),
    ('nist_csf', 'PR.AC-1', 'ESC-LIN-USERS-001', true),
    ('cis', '5.4', 'ESC-LIN-USERS-001', true)
ON CONFLICT DO NOTHING;

-- =============================================================================
-- macOS RUNBOOKS → FRAMEWORK CONTROLS
-- =============================================================================

-- macos_filevault → MAC-FV-001
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.312(a)(2)(iv)', 'MAC-FV-001', true),
    ('soc2', 'CC6.7', 'MAC-FV-001', true),
    ('pci_dss', '3.4', 'MAC-FV-001', true),
    ('nist_csf', 'PR.DS-1', 'MAC-FV-001', true),
    ('cis', '2.5.1', 'MAC-FV-001', true)
ON CONFLICT DO NOTHING;

-- macos_gatekeeper → MAC-GK-001
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.308(a)(5)(ii)(B)', 'MAC-GK-001', true),
    ('soc2', 'CC6.8', 'MAC-GK-001', true),
    ('pci_dss', '5.1', 'MAC-GK-001', true),
    ('nist_csf', 'DE.CM-5', 'MAC-GK-001', true),
    ('cis', '2.5.2', 'MAC-GK-001', true)
ON CONFLICT DO NOTHING;

-- macos_sip → ESC-MAC-SIP (escalation — requires Recovery Mode)
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.312(c)(1)', 'ESC-MAC-SIP', true),
    ('soc2', 'CC6.1', 'ESC-MAC-SIP', true),
    ('pci_dss', '5.1', 'ESC-MAC-SIP', false),
    ('nist_csf', 'PR.IP-1', 'ESC-MAC-SIP', true),
    ('cis', '5.1.3', 'ESC-MAC-SIP', true)
ON CONFLICT DO NOTHING;

-- macos_firewall → MAC-FW-001
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.312(e)(1)', 'MAC-FW-001', true),
    ('soc2', 'CC6.6', 'MAC-FW-001', true),
    ('pci_dss', '1.1', 'MAC-FW-001', false),
    ('nist_csf', 'PR.AC-5', 'MAC-FW-001', true),
    ('cis', '2.5.4', 'MAC-FW-001', true)
ON CONFLICT DO NOTHING;

-- macos_auto_update → MAC-UPD-001
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.308(a)(5)(ii)(B)', 'MAC-UPD-001', false),
    ('soc2', 'CC7.1', 'MAC-UPD-001', true),
    ('pci_dss', '6.2', 'MAC-UPD-001', false),
    ('nist_csf', 'PR.IP-12', 'MAC-UPD-001', true),
    ('cis', '1.2', 'MAC-UPD-001', true)
ON CONFLICT DO NOTHING;

-- macos_screen_lock → MAC-SCR-001
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.310(b)', 'MAC-SCR-001', true),
    ('soc2', 'CC6.1', 'MAC-SCR-001', false),
    ('pci_dss', '8.1.8', 'MAC-SCR-001', true),
    ('nist_csf', 'PR.AC-7', 'MAC-SCR-001', false),
    ('cis', '2.5.5', 'MAC-SCR-001', true)
ON CONFLICT DO NOTHING;

-- macos_file_sharing → MAC-SMB-001
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.312(e)(1)', 'MAC-SMB-001', false),
    ('soc2', 'CC6.6', 'MAC-SMB-001', false),
    ('pci_dss', '2.2.2', 'MAC-SMB-001', true),
    ('nist_csf', 'PR.AC-3', 'MAC-SMB-001', true),
    ('cis', '2.4.2', 'MAC-SMB-001', true)
ON CONFLICT DO NOTHING;

-- macos_time_machine → MAC-TM-001
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.308(a)(7)(ii)(A)', 'MAC-TM-001', true),
    ('soc2', 'A1.2', 'MAC-TM-001', true),
    ('pci_dss', '10.7', 'MAC-TM-001', false),
    ('nist_csf', 'PR.IP-4', 'MAC-TM-001', true),
    ('cis', '2.7.1', 'MAC-TM-001', true)
ON CONFLICT DO NOTHING;

-- macos_ntp_sync → MAC-NTP-001
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.312(b)', 'MAC-NTP-001', false),
    ('soc2', 'CC7.2', 'MAC-NTP-001', false),
    ('pci_dss', '10.4', 'MAC-NTP-001', true),
    ('nist_csf', 'PR.PT-1', 'MAC-NTP-001', false),
    ('cis', '2.5.7', 'MAC-NTP-001', true)
ON CONFLICT DO NOTHING;

-- macos_admin_users → MAC-ADM-001 (escalation for >3 admins)
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.308(a)(3)(ii)(B)', 'MAC-ADM-001', true),
    ('soc2', 'CC6.3', 'MAC-ADM-001', true),
    ('pci_dss', '8.1.4', 'MAC-ADM-001', false),
    ('nist_csf', 'PR.AC-4', 'MAC-ADM-001', true),
    ('cis', '5.4.2', 'MAC-ADM-001', true)
ON CONFLICT DO NOTHING;

-- macos_disk_space → MAC-DISK-001
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.310(d)(2)(iv)', 'MAC-DISK-001', false),
    ('soc2', 'A1.1', 'MAC-DISK-001', false),
    ('pci_dss', '10.7', 'MAC-DISK-001', false),
    ('nist_csf', 'PR.DS-4', 'MAC-DISK-001', false),
    ('cis', '2.7.2', 'MAC-DISK-001', true)
ON CONFLICT DO NOTHING;

-- macos_ssh → MAC-SSH-001
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.312(a)(2)(i)', 'MAC-SSH-001', false),
    ('soc2', 'CC6.1', 'MAC-SSH-001', false),
    ('pci_dss', '2.2.7', 'MAC-SSH-001', false),
    ('nist_csf', 'PR.AC-7', 'MAC-SSH-001', false),
    ('cis', '2.4.4', 'MAC-SSH-001', true)
ON CONFLICT DO NOTHING;

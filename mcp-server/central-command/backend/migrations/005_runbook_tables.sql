-- Migration: 005_runbook_tables.sql
-- Adds runbook catalog and per-site/appliance configuration
--
-- Depends on: 004_discovery_and_credentials.sql

BEGIN;

-- ============================================================
-- RUNBOOK CATALOG (master list of all runbooks)
-- ============================================================

CREATE TABLE IF NOT EXISTS runbooks (
    id VARCHAR(50) PRIMARY KEY,           -- e.g., RB-WIN-SVC-001
    name VARCHAR(200) NOT NULL,
    description TEXT,
    category VARCHAR(50) NOT NULL,        -- services, security, network, storage, updates, ad
    check_type VARCHAR(50) NOT NULL,      -- maps to CheckType enum
    severity VARCHAR(20) DEFAULT 'medium', -- low, medium, high, critical
    is_disruptive BOOLEAN DEFAULT FALSE,
    requires_maintenance_window BOOLEAN DEFAULT FALSE,
    hipaa_controls TEXT[],                -- HIPAA control mappings
    version VARCHAR(20) DEFAULT '1.0',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- SITE-LEVEL RUNBOOK CONFIGURATION
-- ============================================================

CREATE TABLE IF NOT EXISTS site_runbook_config (
    site_id VARCHAR(100) NOT NULL,
    runbook_id VARCHAR(50) NOT NULL REFERENCES runbooks(id) ON DELETE CASCADE,
    enabled BOOLEAN DEFAULT TRUE,
    modified_by VARCHAR(100),
    modified_at TIMESTAMPTZ DEFAULT NOW(),
    notes TEXT,
    PRIMARY KEY (site_id, runbook_id)
);

-- ============================================================
-- APPLIANCE-LEVEL RUNBOOK OVERRIDES
-- ============================================================

CREATE TABLE IF NOT EXISTS appliance_runbook_config (
    appliance_id VARCHAR(200) NOT NULL,
    runbook_id VARCHAR(50) NOT NULL REFERENCES runbooks(id) ON DELETE CASCADE,
    enabled BOOLEAN DEFAULT TRUE,
    modified_by VARCHAR(100),
    modified_at TIMESTAMPTZ DEFAULT NOW(),
    notes TEXT,
    PRIMARY KEY (appliance_id, runbook_id)
);

-- ============================================================
-- RUNBOOK EXECUTION HISTORY
-- ============================================================

CREATE TABLE IF NOT EXISTS runbook_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    runbook_id VARCHAR(50) NOT NULL REFERENCES runbooks(id),
    site_id VARCHAR(100) NOT NULL,
    appliance_id VARCHAR(200),
    target_hostname VARCHAR(255),

    -- Execution details
    triggered_by VARCHAR(50) DEFAULT 'auto',  -- auto, manual, chaos_probe
    incident_id UUID,

    -- Results
    status VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending, running, success, failed, skipped
    detect_result JSONB,
    remediate_result JSONB,
    verify_result JSONB,

    -- Timing
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    execution_time_ms INTEGER,

    -- Error tracking
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,

    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- SEED RUNBOOK CATALOG
-- ============================================================

-- Existing 7 runbooks (patching, av, backup, logging, firewall, encryption, ad)
INSERT INTO runbooks (id, name, description, category, check_type, severity, is_disruptive, hipaa_controls) VALUES
('RB-WIN-PATCH-001', 'Windows Patch Compliance', 'Check and apply missing Windows security updates via WSUS or Windows Update', 'patching', 'patching', 'high', true, ARRAY['164.308(a)(5)(ii)(B)', '164.312(c)(1)']),
('RB-WIN-AV-001', 'Windows Defender Health', 'Check Windows Defender status, signatures, and real-time protection', 'antivirus', 'antivirus', 'critical', false, ARRAY['164.308(a)(5)(ii)(B)', '164.312(b)']),
('RB-WIN-BACKUP-001', 'Backup Verification', 'Verify Windows Server Backup or Veeam backup status and age', 'backup', 'backup', 'critical', false, ARRAY['164.308(a)(7)(ii)(A)', '164.310(d)(2)(iv)']),
('RB-WIN-LOGGING-001', 'Windows Event Logging', 'Verify Windows audit policy and event log forwarding', 'logging', 'logging', 'high', false, ARRAY['164.312(b)', '164.308(a)(1)(ii)(D)']),
('RB-WIN-FIREWALL-001', 'Windows Firewall Status', 'Verify Windows Firewall is enabled on all profiles', 'firewall', 'firewall', 'critical', false, ARRAY['164.312(a)(1)', '164.312(e)(1)']),
('RB-WIN-ENCRYPTION-001', 'BitLocker Encryption', 'Verify BitLocker encryption status on system drives', 'encryption', 'encryption', 'critical', true, ARRAY['164.312(a)(2)(iv)', '164.312(e)(2)(ii)']),
('RB-WIN-AD-001', 'Active Directory Health', 'Check AD replication, DNS, and account lockout status', 'ad', 'service_health', 'high', false, ARRAY['164.312(a)(1)', '164.308(a)(3)(ii)(C)'])
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    updated_at = NOW();

-- NEW: 4 Service runbooks
INSERT INTO runbooks (id, name, description, category, check_type, severity, is_disruptive, hipaa_controls) VALUES
('RB-WIN-SVC-001', 'DNS Server Service Recovery', 'Monitor and restart Windows DNS Server service', 'services', 'service_health', 'critical', false, ARRAY['164.312(b)']),
('RB-WIN-SVC-002', 'DHCP Server Service Recovery', 'Monitor and restart Windows DHCP Server service', 'services', 'service_health', 'high', false, ARRAY['164.312(b)']),
('RB-WIN-SVC-003', 'Print Spooler Service Recovery', 'Monitor and restart Print Spooler service (security)', 'services', 'service_health', 'medium', false, ARRAY['164.308(a)(5)(ii)(B)']),
('RB-WIN-SVC-004', 'Windows Time Service Recovery', 'Ensure W32Time service running for NTP sync', 'services', 'ntp_sync', 'high', false, ARRAY['164.312(b)'])
ON CONFLICT (id) DO NOTHING;

-- NEW: 6 Security runbooks
INSERT INTO runbooks (id, name, description, category, check_type, severity, is_disruptive, hipaa_controls) VALUES
('RB-WIN-SEC-001', 'Windows Firewall Re-enable', 'Re-enable Windows Firewall if disabled', 'security', 'firewall', 'critical', false, ARRAY['164.312(a)(1)', '164.312(e)(1)']),
('RB-WIN-SEC-002', 'Audit Policy Remediation', 'Ensure HIPAA-required audit policies are configured', 'security', 'logging', 'high', false, ARRAY['164.312(b)', '164.308(a)(1)(ii)(D)']),
('RB-WIN-SEC-003', 'Account Lockout Policy Reset', 'Configure account lockout thresholds per policy', 'security', 'service_health', 'medium', false, ARRAY['164.312(a)(2)(i)']),
('RB-WIN-SEC-004', 'Password Policy Enforcement', 'Verify password complexity and expiration policies', 'security', 'service_health', 'high', false, ARRAY['164.312(d)']),
('RB-WIN-SEC-005', 'BitLocker Status Recovery', 'Check and resume BitLocker protection', 'security', 'encryption', 'critical', true, ARRAY['164.312(a)(2)(iv)', '164.312(e)(2)(ii)']),
('RB-WIN-SEC-006', 'Windows Defender Real-time Protection', 'Enable real-time protection and update signatures', 'security', 'windows_defender', 'critical', false, ARRAY['164.308(a)(5)(ii)(B)'])
ON CONFLICT (id) DO NOTHING;

-- NEW: 4 Network runbooks
INSERT INTO runbooks (id, name, description, category, check_type, severity, is_disruptive, hipaa_controls) VALUES
('RB-WIN-NET-001', 'DNS Client Configuration Reset', 'Reset DNS client settings to proper servers', 'network', 'service_health', 'medium', false, ARRAY['164.312(b)']),
('RB-WIN-NET-002', 'NIC Reset and Recovery', 'Reset network adapter if connectivity issues detected', 'network', 'service_health', 'high', true, ARRAY['164.312(a)(1)']),
('RB-WIN-NET-003', 'Network Profile Remediation', 'Ensure proper network profile (Domain/Private)', 'network', 'firewall', 'medium', false, ARRAY['164.312(e)(1)']),
('RB-WIN-NET-004', 'WINS/NetBIOS Configuration', 'Configure WINS and NetBIOS settings for domain', 'network', 'service_health', 'low', false, ARRAY['164.312(b)'])
ON CONFLICT (id) DO NOTHING;

-- NEW: 3 Storage runbooks
INSERT INTO runbooks (id, name, description, category, check_type, severity, is_disruptive, hipaa_controls) VALUES
('RB-WIN-STG-001', 'Disk Space Cleanup', 'Clean temp files, old logs, and Windows Update cache', 'storage', 'disk_space', 'high', false, ARRAY['164.312(c)(1)']),
('RB-WIN-STG-002', 'Shadow Copy Recovery', 'Verify and restore Volume Shadow Copy service', 'storage', 'backup', 'medium', false, ARRAY['164.308(a)(7)(ii)(A)']),
('RB-WIN-STG-003', 'Volume Health Check', 'Check disk SMART status and volume integrity', 'storage', 'disk_space', 'critical', false, ARRAY['164.310(d)(2)(iv)'])
ON CONFLICT (id) DO NOTHING;

-- NEW: 2 Update runbooks
INSERT INTO runbooks (id, name, description, category, check_type, severity, is_disruptive, hipaa_controls) VALUES
('RB-WIN-UPD-001', 'Windows Update Service Reset', 'Reset Windows Update components and clear cache', 'updates', 'patching', 'high', false, ARRAY['164.308(a)(5)(ii)(B)']),
('RB-WIN-UPD-002', 'WSUS Client Registration Fix', 'Re-register with WSUS server if sync broken', 'updates', 'patching', 'medium', false, ARRAY['164.308(a)(5)(ii)(B)'])
ON CONFLICT (id) DO NOTHING;

-- NEW: 1 Active Directory runbook
INSERT INTO runbooks (id, name, description, category, check_type, severity, is_disruptive, hipaa_controls) VALUES
('RB-WIN-AD-002', 'Computer Account Password Reset', 'Reset machine account password if trust broken', 'ad', 'service_health', 'high', true, ARRAY['164.312(a)(1)', '164.308(a)(4)(ii)(B)'])
ON CONFLICT (id) DO NOTHING;

-- ============================================================
-- INDEXES FOR PERFORMANCE
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_runbooks_category ON runbooks(category);
CREATE INDEX IF NOT EXISTS idx_runbooks_check_type ON runbooks(check_type);
CREATE INDEX IF NOT EXISTS idx_site_runbook_config_site ON site_runbook_config(site_id);
CREATE INDEX IF NOT EXISTS idx_appliance_runbook_config_appliance ON appliance_runbook_config(appliance_id);
CREATE INDEX IF NOT EXISTS idx_runbook_executions_site ON runbook_executions(site_id);
CREATE INDEX IF NOT EXISTS idx_runbook_executions_runbook ON runbook_executions(runbook_id);
CREATE INDEX IF NOT EXISTS idx_runbook_executions_status ON runbook_executions(status);
CREATE INDEX IF NOT EXISTS idx_runbook_executions_date ON runbook_executions(started_at);

COMMIT;

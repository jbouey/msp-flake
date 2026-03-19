-- Migration 094: Runbook category cleanup
-- Re-categorize runbooks from 'general' to proper HIPAA categories
-- Based on runbook_id prefix and name

BEGIN;

-- RB-WIN-* prefixes
UPDATE runbooks SET category = 'security' WHERE runbook_id LIKE 'RB-WIN-AV-%' AND category = 'general';
UPDATE runbooks SET category = 'backup' WHERE runbook_id LIKE 'RB-WIN-BACKUP-%' AND category = 'general';
UPDATE runbooks SET category = 'firewall' WHERE runbook_id LIKE 'RB-WIN-FIREWALL-%' AND category = 'general';

-- RB-PROMOTED-* (infer from name)
UPDATE runbooks SET category = 'firewall' WHERE runbook_id = 'RB-PROMOTED-FIREWALL' AND category = 'general';
UPDATE runbooks SET category = 'firewall' WHERE runbook_id = 'RB-PROMOTED-PROHIBITED_PORT' AND category = 'general';
UPDATE runbooks SET category = 'security' WHERE runbook_id = 'RB-PROMOTED-INCIDENT_RESPONSE' AND category = 'general';
UPDATE runbooks SET category = 'security' WHERE runbook_id = 'RB-PROMOTED-SCHEDULED_TASK_PERSISTENCE' AND category = 'general';

-- AUTO-* prefixes (infer from name)
UPDATE runbooks SET category = 'audit' WHERE runbook_id = 'AUTO-AUDIT' AND category = 'general';
UPDATE runbooks SET category = 'security' WHERE runbook_id = 'AUTO-INCIDENT_RESPONSE' AND category = 'general';
UPDATE runbooks SET category = 'security' WHERE runbook_id = 'AUTO-SCHEDULED_TASK_PERSISTENCE' AND category = 'general';
UPDATE runbooks SET category = 'services' WHERE runbook_id = 'AUTO-SERVICE_W32TIME' AND category = 'general';

-- L1-WIN-* prefixes
UPDATE runbooks SET category = 'security' WHERE runbook_id LIKE 'L1-WIN-SEC-%' AND category = 'general';
UPDATE runbooks SET category = 'services' WHERE runbook_id LIKE 'L1-WIN-SVC-%' AND category = 'general';
UPDATE runbooks SET category = 'firewall' WHERE runbook_id LIKE 'L1-WIN-FIREWALL-%' AND category = 'general';
UPDATE runbooks SET category = 'security' WHERE runbook_id = 'L1-WIN-DEFENDER-001' AND category = 'general';
UPDATE runbooks SET category = 'backup' WHERE runbook_id = 'L1-WIN-BACKUP-STATUS-001' AND category = 'general';
UPDATE runbooks SET category = 'security' WHERE runbook_id = 'L1-WIN-DNS-HIJACK' AND category = 'general';
UPDATE runbooks SET category = 'security' WHERE runbook_id = 'L1-WIN-NET-PROFILE' AND category = 'general';

-- L1-LIN-* prefixes (Linux runbooks)
UPDATE runbooks SET category = 'audit' WHERE runbook_id LIKE 'L1-LIN-AUDIT-%' AND category = 'general';
UPDATE runbooks SET category = 'security' WHERE runbook_id = 'L1-LIN-BANNER-001' AND category = 'general';
UPDATE runbooks SET category = 'services' WHERE runbook_id = 'L1-LIN-CRON-001' AND category = 'general';
UPDATE runbooks SET category = 'encryption' WHERE runbook_id = 'L1-LIN-CRYPTO-001' AND category = 'general';
UPDATE runbooks SET category = 'firewall' WHERE runbook_id = 'L1-LIN-FW-001' AND category = 'general';
UPDATE runbooks SET category = 'security' WHERE runbook_id = 'L1-LIN-IR-001' AND category = 'general';
UPDATE runbooks SET category = 'security' WHERE runbook_id = 'L1-LIN-KERN-001' AND category = 'general';
UPDATE runbooks SET category = 'audit' WHERE runbook_id = 'L1-LIN-LOG-001' AND category = 'general';
UPDATE runbooks SET category = 'security' WHERE runbook_id = 'L1-LIN-NET-001' AND category = 'general';
UPDATE runbooks SET category = 'services' WHERE runbook_id = 'L1-LIN-NTP-001' AND category = 'general';
UPDATE runbooks SET category = 'access_control' WHERE runbook_id LIKE 'L1-LIN-SSH-%' AND category = 'general';
UPDATE runbooks SET category = 'security' WHERE runbook_id = 'L1-LIN-SUID-001' AND category = 'general';
UPDATE runbooks SET category = 'services' WHERE runbook_id LIKE 'L1-LIN-SVC-%' AND category = 'general';

-- L1-* standalone prefixes
UPDATE runbooks SET category = 'audit' WHERE runbook_id = 'L1-AUDIT-001' AND category = 'general';
UPDATE runbooks SET category = 'security' WHERE runbook_id = 'L1-DEFENDER-001' AND category = 'general';
UPDATE runbooks SET category = 'firewall' WHERE runbook_id LIKE 'L1-FIREWALL-%' AND category = 'general';
UPDATE runbooks SET category = 'firewall' WHERE runbook_id = 'L1-FW-001' AND category = 'general';
UPDATE runbooks SET category = 'services' WHERE runbook_id LIKE 'L1-NIX-NTP-%' AND category = 'general';
UPDATE runbooks SET category = 'services' WHERE runbook_id = 'L1-NTP-001' AND category = 'general';
UPDATE runbooks SET category = 'access_control' WHERE runbook_id = 'L1-PASSWORD-001' AND category = 'general';
UPDATE runbooks SET category = 'security' WHERE runbook_id = 'L1-PERSIST-REG-001' AND category = 'general';
UPDATE runbooks SET category = 'security' WHERE runbook_id = 'L1-PERSIST-TASK-001' AND category = 'general';
UPDATE runbooks SET category = 'access_control' WHERE runbook_id = 'L1-SCREENLOCK-001' AND category = 'general';
UPDATE runbooks SET category = 'services' WHERE runbook_id = 'L1-SERVICE-001' AND category = 'general';
UPDATE runbooks SET category = 'security' WHERE runbook_id = 'L1-SMB-SIGNING-001' AND category = 'general';
UPDATE runbooks SET category = 'access_control' WHERE runbook_id LIKE 'L1-SSH-%' AND category = 'general';
UPDATE runbooks SET category = 'security' WHERE runbook_id = 'L1-SUID-001' AND category = 'general';
UPDATE runbooks SET category = 'services' WHERE runbook_id LIKE 'L1-SVC-%' AND category = 'general';

-- RB-TEST (keep as misc/security)
UPDATE runbooks SET category = 'security' WHERE runbook_id = 'RB-TEST' AND category = 'general';

COMMIT;

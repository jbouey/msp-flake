-- Migration 089: Add missing macos_cert_expiry L1 rule + control mapping
--
-- Gap found during coverage audit: macos_cert_expiry has daemon detection
-- but no L1 rule, so all cert expiry incidents bypass deterministic healing.

INSERT INTO l1_rules (
    rule_id, incident_pattern, confidence, runbook_id,
    source, enabled
)
VALUES (
    'L1-MAC-CERT-001',
    '{"incident_type": "macos_cert_expiry"}',
    0.85,
    'ESC-MAC-CERT',
    'builtin',
    true
)
ON CONFLICT (rule_id) DO NOTHING;

-- Control mapping for cert expiry
INSERT INTO control_runbook_mapping (framework_id, control_id, runbook_id, is_primary)
VALUES
    ('hipaa', '164.312(e)(2)(ii)', 'ESC-MAC-CERT', true),
    ('soc2', 'CC6.7', 'ESC-MAC-CERT', true),
    ('pci_dss', '4.1', 'ESC-MAC-CERT', false),
    ('nist_csf', 'PR.DS-2', 'ESC-MAC-CERT', true),
    ('cis', '2.5.8', 'ESC-MAC-CERT', true)
ON CONFLICT DO NOTHING;

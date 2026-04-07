-- MFA enforcement per org + compliance framework selection.

-- Per-org MFA requirement: when true, users without MFA set up are blocked at login
ALTER TABLE client_orgs
  ADD COLUMN IF NOT EXISTS mfa_required BOOLEAN DEFAULT false;

-- Admin-level MFA enforcement (global)
-- This is checked in auth.py login flow
ALTER TABLE admin_users
  ADD COLUMN IF NOT EXISTS mfa_required BOOLEAN DEFAULT false;

-- Partner-level MFA enforcement (per partner)
ALTER TABLE partners
  ADD COLUMN IF NOT EXISTS mfa_required BOOLEAN DEFAULT false;

-- Compliance framework per org: hipaa (default), soc2, glba, nist, pci_dss
ALTER TABLE client_orgs
  ADD COLUMN IF NOT EXISTS compliance_framework VARCHAR(20) DEFAULT 'hipaa';

-- Audit log retention: define policy (3 years = 1095 days)
-- Background task enforces this; column documents the policy per-org
ALTER TABLE client_orgs
  ADD COLUMN IF NOT EXISTS audit_retention_days INTEGER DEFAULT 1095;

SELECT 'Migration 134_mfa_enforcement_and_framework completed successfully' AS status;

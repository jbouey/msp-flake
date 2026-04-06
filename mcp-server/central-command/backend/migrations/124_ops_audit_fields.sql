-- 124_ops_audit_fields.sql
-- Adds BAA tracking and scheduled audit dates for audit readiness feature.

ALTER TABLE client_orgs ADD COLUMN IF NOT EXISTS baa_on_file BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE client_orgs ADD COLUMN IF NOT EXISTS baa_uploaded_at TIMESTAMPTZ;
ALTER TABLE client_orgs ADD COLUMN IF NOT EXISTS next_audit_date DATE;
ALTER TABLE client_orgs ADD COLUMN IF NOT EXISTS next_audit_notes TEXT;

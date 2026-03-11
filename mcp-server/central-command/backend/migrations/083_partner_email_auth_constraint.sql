-- Migration 083: Add 'email' to partners auth_provider check constraint
-- Partner email/password signup (partner_auth.py email_signup) uses auth_provider='email'
-- but the original constraint only allowed NULL, microsoft, google, api_key.

ALTER TABLE partners DROP CONSTRAINT IF EXISTS partners_auth_provider_check;
ALTER TABLE partners ADD CONSTRAINT partners_auth_provider_check CHECK (
    auth_provider IS NULL OR auth_provider IN ('microsoft', 'google', 'api_key', 'email')
);

INSERT INTO schema_migrations (version, name, applied_at, checksum, execution_time_ms)
VALUES ('083', 'partner_email_auth_constraint', NOW(), 'phase4-p2-fix-v2', 0)
ON CONFLICT (version) DO NOTHING;

-- Migration 072: TOTP 2FA for all portals
-- admin_users already has mfa_secret + mfa_enabled from migration 008

-- Partners TOTP
ALTER TABLE partners ADD COLUMN IF NOT EXISTS mfa_secret VARCHAR(255);
ALTER TABLE partners ADD COLUMN IF NOT EXISTS mfa_enabled BOOLEAN DEFAULT FALSE;
ALTER TABLE partners ADD COLUMN IF NOT EXISTS mfa_backup_codes TEXT; -- JSON array of hashed codes

-- Client users TOTP
ALTER TABLE client_users ADD COLUMN IF NOT EXISTS mfa_secret VARCHAR(255);
ALTER TABLE client_users ADD COLUMN IF NOT EXISTS mfa_enabled BOOLEAN DEFAULT FALSE;
ALTER TABLE client_users ADD COLUMN IF NOT EXISTS mfa_backup_codes TEXT; -- JSON array of hashed codes

-- Admin users backup codes (mfa_secret + mfa_enabled already exist)
ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS mfa_backup_codes TEXT;

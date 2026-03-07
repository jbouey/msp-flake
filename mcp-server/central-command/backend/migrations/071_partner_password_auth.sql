-- Migration 071: Add password-based auth for partners
-- Partners who sign up via email can set a password during registration

ALTER TABLE partners ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255);

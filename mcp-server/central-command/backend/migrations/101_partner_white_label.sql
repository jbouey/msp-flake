-- Migration 101: Partner white-label branding extensions
ALTER TABLE partners ADD COLUMN IF NOT EXISTS secondary_color VARCHAR(7) DEFAULT '#6366F1';
ALTER TABLE partners ADD COLUMN IF NOT EXISTS tagline TEXT;
ALTER TABLE partners ADD COLUMN IF NOT EXISTS support_email TEXT;
ALTER TABLE partners ADD COLUMN IF NOT EXISTS support_phone TEXT;

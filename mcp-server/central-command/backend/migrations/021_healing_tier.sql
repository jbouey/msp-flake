-- Healing tier support for per-site L1 rules configuration
-- Migration: 021_healing_tier.sql
-- Created: 2026-01-17
-- Purpose: Allow Central Command to control whether a site uses 'standard' or 'full_coverage' healing

-- Add healing_tier column to sites table
-- Default to 'standard' (4 core rules: firewall, defender, bitlocker, ntp)
-- 'full_coverage' enables all 21 L1 rules
ALTER TABLE sites ADD COLUMN IF NOT EXISTS healing_tier VARCHAR(50) DEFAULT 'standard';

-- Index for filtering sites by healing tier
CREATE INDEX IF NOT EXISTS idx_sites_healing_tier ON sites(healing_tier);

-- Comments for documentation
COMMENT ON COLUMN sites.healing_tier IS 'L1 healing tier: standard (4 rules) or full_coverage (21 rules)';

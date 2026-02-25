-- Migration 059: Add missing healing columns to orders table
--
-- The orders table was created in 002 for fleet command queuing.
-- main.py later added healing order creation (runbook execution)
-- which uses columns that were never added via migration:
--   runbook_id, ttl_seconds, issued_at
--
-- Migration 054 added nonce/signature/signed_payload to admin_orders
-- and fleet_orders but only added signed_payload to orders (comment
-- incorrectly claimed nonce+signature already existed).
--
-- This migration adds all missing columns.

-- Healing-specific columns
ALTER TABLE orders ADD COLUMN IF NOT EXISTS runbook_id VARCHAR(50);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS ttl_seconds INTEGER DEFAULT 900;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS issued_at TIMESTAMPTZ;

-- Signature columns (054 may have partially added these)
ALTER TABLE orders ADD COLUMN IF NOT EXISTS nonce VARCHAR(64);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS signature TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS signed_payload TEXT;

-- Index for order lookup by appliance + status (healing flow)
CREATE INDEX IF NOT EXISTS idx_orders_appliance_status ON orders(appliance_id, status);

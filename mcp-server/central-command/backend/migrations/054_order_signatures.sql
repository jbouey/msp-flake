-- Migration 054: Add Ed25519 signature fields to all order tables
--
-- P0 security fix: all orders from Central Command must be signed.
-- The appliance daemon verifies signatures before executing any order,
-- preventing a compromised Central Command or MITM from injecting
-- malicious orders into the fleet.
--
-- Also signs L1 rules bundles so agents can verify rule integrity.

-- Add signature fields to admin_orders
ALTER TABLE admin_orders ADD COLUMN IF NOT EXISTS nonce VARCHAR(64);
ALTER TABLE admin_orders ADD COLUMN IF NOT EXISTS signature TEXT;
ALTER TABLE admin_orders ADD COLUMN IF NOT EXISTS signed_payload TEXT;

-- Add signature fields to fleet_orders
ALTER TABLE fleet_orders ADD COLUMN IF NOT EXISTS nonce VARCHAR(64);
ALTER TABLE fleet_orders ADD COLUMN IF NOT EXISTS signature TEXT;
ALTER TABLE fleet_orders ADD COLUMN IF NOT EXISTS signed_payload TEXT;

-- The healing 'orders' table already has nonce + signature columns.
-- Add signed_payload for consistency (stores the canonical JSON that was signed).
ALTER TABLE orders ADD COLUMN IF NOT EXISTS signed_payload TEXT;

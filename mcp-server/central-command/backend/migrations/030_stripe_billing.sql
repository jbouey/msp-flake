-- Migration: 030_stripe_billing.sql
-- Description: Add Stripe billing fields to client_orgs
-- Created: 2026-01-24

-- Add subscription tracking columns
ALTER TABLE client_orgs
ADD COLUMN IF NOT EXISTS subscription_status VARCHAR(50) DEFAULT 'none',
ADD COLUMN IF NOT EXISTS subscription_plan VARCHAR(100),
ADD COLUMN IF NOT EXISTS next_billing_date TIMESTAMPTZ;

-- Add index for subscription status queries
CREATE INDEX IF NOT EXISTS idx_client_orgs_subscription ON client_orgs(subscription_status);

-- Add comments
COMMENT ON COLUMN client_orgs.subscription_status IS 'Stripe subscription status: none, active, trialing, past_due, cancelled';
COMMENT ON COLUMN client_orgs.subscription_plan IS 'Current subscription plan name';
COMMENT ON COLUMN client_orgs.next_billing_date IS 'Next billing/renewal date from Stripe';

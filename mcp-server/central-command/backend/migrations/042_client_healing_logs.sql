-- Migration 042: Client Healing Logs + Endorsement
-- Adds client endorsement fields to learning_promotion_candidates
-- so clients can forward/endorse patterns to their partner manager.
-- Created: 2026-02-14

-- Add client endorsement fields
ALTER TABLE learning_promotion_candidates
    ADD COLUMN IF NOT EXISTS client_endorsed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS client_endorsed_by UUID,
    ADD COLUMN IF NOT EXISTS client_notes TEXT;

-- Index for quick lookup of client-endorsed candidates
CREATE INDEX IF NOT EXISTS idx_lpc_client_endorsed
    ON learning_promotion_candidates(client_endorsed_at)
    WHERE client_endorsed_at IS NOT NULL;

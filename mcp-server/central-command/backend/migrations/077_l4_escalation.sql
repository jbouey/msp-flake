-- Migration 077: L4 Escalation + Recurrence Detection
-- Adds L4 escalation path from partner → central command
-- Adds recurrence tracking for drift that re-triggers after resolution

-- L4 escalation columns on existing escalation_tickets
ALTER TABLE escalation_tickets
    ADD COLUMN IF NOT EXISTS escalated_to_l4 BOOLEAN DEFAULT false,
    ADD COLUMN IF NOT EXISTS l4_escalated_at TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS l4_escalated_by TEXT,
    ADD COLUMN IF NOT EXISTS l4_notes TEXT,
    ADD COLUMN IF NOT EXISTS l4_resolved_at TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS l4_resolved_by TEXT,
    ADD COLUMN IF NOT EXISTS l4_resolution_notes TEXT,
    ADD COLUMN IF NOT EXISTS recurrence_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS previous_ticket_id TEXT;

-- Index for L4 queue queries (admin dashboard)
CREATE INDEX IF NOT EXISTS idx_escalation_tickets_l4
    ON escalation_tickets (escalated_to_l4, l4_resolved_at)
    WHERE escalated_to_l4 = true;

-- Index for recurrence detection (same incident_type + site_id)
CREATE INDEX IF NOT EXISTS idx_escalation_tickets_recurrence
    ON escalation_tickets (site_id, incident_type, status, resolved_at DESC);

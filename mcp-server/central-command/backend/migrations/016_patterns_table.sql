-- Migration: 016_patterns_table.sql
-- Creates patterns table for L2->L1 learning loop
--
-- Depends on: 001_portal_tables.sql

BEGIN;

-- ============================================================
-- PATTERNS TABLE (for L2->L1 promotion)
-- ============================================================

CREATE TABLE IF NOT EXISTS patterns (
    id SERIAL PRIMARY KEY,
    pattern_id VARCHAR(255) UNIQUE NOT NULL,
    pattern_signature VARCHAR(255) NOT NULL,
    
    -- Pattern details
    description TEXT,
    incident_type VARCHAR(100) NOT NULL,
    runbook_id VARCHAR(255),
    proposed_rule TEXT,
    
    -- Statistics
    occurrences INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    success_rate FLOAT DEFAULT 0.0,
    avg_resolution_time_ms FLOAT,
    total_resolution_time_ms FLOAT DEFAULT 0.0,
    
    -- Promotion status
    status VARCHAR(20) DEFAULT 'pending',  -- pending, approved, promoted, rejected
    promoted_at TIMESTAMPTZ,
    promoted_to_rule_id VARCHAR(255),
    
    -- Context
    example_incidents JSONB DEFAULT '[]'::jsonb,
    
    -- Timestamps
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    last_seen TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_patterns_status ON patterns(status);
CREATE INDEX IF NOT EXISTS idx_patterns_signature ON patterns(pattern_signature);
CREATE INDEX IF NOT EXISTS idx_patterns_status_rate ON patterns(status, success_rate);
CREATE INDEX IF NOT EXISTS idx_patterns_incident_type ON patterns(incident_type);

COMMIT;

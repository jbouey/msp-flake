-- Migration: 038_l1_rules_table.sql
-- Creates the l1_rules table for dashboard learning loop endpoints.
-- This table was referenced by routes.py, db_queries.py, and settings_api.py
-- but never created, causing HTTP 500 on the Learning page.

BEGIN;

CREATE TABLE IF NOT EXISTS l1_rules (
    id SERIAL PRIMARY KEY,
    rule_id VARCHAR(255) UNIQUE NOT NULL,
    incident_pattern JSONB NOT NULL DEFAULT '{}'::jsonb,
    runbook_id VARCHAR(255),
    confidence FLOAT DEFAULT 0.9,
    promoted_from_l2 BOOLEAN DEFAULT false,
    enabled BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_l1_rules_enabled ON l1_rules(enabled) WHERE enabled = true;
CREATE INDEX IF NOT EXISTS idx_l1_rules_promoted ON l1_rules(promoted_from_l2) WHERE promoted_from_l2 = true;
CREATE INDEX IF NOT EXISTS idx_l1_rules_rule_id ON l1_rules(rule_id);

COMMIT;

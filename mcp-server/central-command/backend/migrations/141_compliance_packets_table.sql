-- Migration 141: Compliance Packets Table
-- HIPAA §164.316(b)(2)(i): Retain documentation for 6 years.
-- Stores generated monthly compliance packets for audit trail.

CREATE TABLE IF NOT EXISTS compliance_packets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id VARCHAR(255) NOT NULL,
    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    year INTEGER NOT NULL CHECK (year BETWEEN 2024 AND 2100),
    packet_id VARCHAR(100) NOT NULL,
    compliance_score NUMERIC(5,2),
    critical_issues INTEGER DEFAULT 0,
    auto_fixes INTEGER DEFAULT 0,
    mttr_hours NUMERIC(8,2),
    framework VARCHAR(50) DEFAULT 'hipaa',
    controls_summary JSONB,
    markdown_content TEXT,
    generated_at TIMESTAMPTZ DEFAULT NOW(),
    generated_by VARCHAR(100) DEFAULT 'system',
    UNIQUE (site_id, month, year, framework)
);

CREATE INDEX IF NOT EXISTS idx_compliance_packets_site
    ON compliance_packets (site_id, year DESC, month DESC);

CREATE INDEX IF NOT EXISTS idx_compliance_packets_generated
    ON compliance_packets (generated_at);

COMMENT ON TABLE compliance_packets IS 'Monthly compliance attestation packets. Retention: 6 years (HIPAA §164.316(b)(2)(i)).';

-- Mark legacy OTS bundles (pre-OTS era, will never be anchored)
UPDATE compliance_bundles SET ots_status = 'legacy'
WHERE ots_status = 'none'
  AND created_at < '2025-10-01';

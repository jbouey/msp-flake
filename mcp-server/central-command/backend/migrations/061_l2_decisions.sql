-- L2 decision logging table for data flywheel
-- Records every L2 LLM planner decision for pattern analysis and promotion tracking

CREATE TABLE IF NOT EXISTS l2_decisions (
    id SERIAL PRIMARY KEY,
    incident_id VARCHAR(255) NOT NULL,
    runbook_id VARCHAR(255),
    reasoning TEXT,
    confidence FLOAT DEFAULT 0.0,
    pattern_signature VARCHAR(255),
    llm_model VARCHAR(100),
    llm_latency_ms INTEGER DEFAULT 0,
    requires_human_review BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_l2_decisions_incident ON l2_decisions (incident_id);
CREATE INDEX idx_l2_decisions_pattern ON l2_decisions (pattern_signature) WHERE pattern_signature IS NOT NULL;
CREATE INDEX idx_l2_decisions_created ON l2_decisions (created_at DESC);
CREATE INDEX idx_l2_decisions_runbook ON l2_decisions (runbook_id) WHERE runbook_id IS NOT NULL;

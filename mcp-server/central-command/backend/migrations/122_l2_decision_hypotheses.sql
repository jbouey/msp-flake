-- Add hypotheses column to l2_decisions for hypothesis-driven triage
-- Stores the ranked root-cause hypotheses generated before the LLM call
-- as JSONB for flywheel analysis and pattern refinement.

ALTER TABLE l2_decisions ADD COLUMN IF NOT EXISTS hypotheses JSONB;

COMMENT ON COLUMN l2_decisions.hypotheses IS 'Ranked root-cause hypotheses generated before LLM call (deterministic)';

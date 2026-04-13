-- Migration 171: Adaptive prompts (Phase 10)
--
-- The L2 system prompt is currently a hand-written static string. The
-- flywheel itself has information about what framing produces high-
-- confidence correct picks — we can mine it nightly and produce an
-- evolved prompt, version it, get human sign-off, then activate.
--
-- Two tables:
--
--   1. l2_prompt_versions: append-only history of prompt revisions.
--      Every l2_decisions row (via a new prompt_version column added
--      here) captures which version produced it, so we can audit
--      the exact prompt wording that birthed any promoted rule.
--      Compliance requirement: §164.312(b) change justification.
--
--   2. l2_prompt_exemplars: mined few-shot examples. A separate
--      table so exemplars can be curated/approved independently
--      of the prompt structure itself.
--
-- Security invariant: a new prompt version starts in status='draft';
-- an admin must set status='active' (at most one row per purpose at
-- a time). The L2 planner picks the 'active' version. Auto-generated
-- drafts DO NOT take effect without human approval.

BEGIN;

CREATE TABLE IF NOT EXISTS l2_prompt_versions (
    id              BIGSERIAL    PRIMARY KEY,
    version_tag     VARCHAR(64)  NOT NULL UNIQUE,
                      -- e.g. 'system-v1', 'system-v2-2026-04-15-draft'
    purpose         VARCHAR(32)  NOT NULL DEFAULT 'system',
                      -- 'system' | 'user-incident' | 'user-recurrence'
    prompt_text     TEXT         NOT NULL,
    status          VARCHAR(16)  NOT NULL DEFAULT 'draft',
                      -- 'draft' | 'active' | 'retired'
    created_by      VARCHAR(100) NOT NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    activated_at    TIMESTAMPTZ,
    activated_by    VARCHAR(100),
    retired_at      TIMESTAMPTZ,
    rationale       TEXT,
    -- Stats accumulated while this version was active
    decisions_count INTEGER      NOT NULL DEFAULT 0,
    success_count   INTEGER      NOT NULL DEFAULT 0,
    avg_confidence  NUMERIC(4,3)
);

-- At most one active version per purpose
CREATE UNIQUE INDEX IF NOT EXISTS uniq_active_prompt_per_purpose
    ON l2_prompt_versions (purpose)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_l2_prompt_versions_status
    ON l2_prompt_versions (status, purpose);

-- Seed the initial "system-v1" row representing the current hand-written
-- prompt in l2_planner.py. Marked active. Future versions start as
-- drafts via the adaptive-prompt nightly job.
INSERT INTO l2_prompt_versions (
    version_tag, purpose, prompt_text, status,
    created_by, activated_at, activated_by, rationale
) VALUES (
    'system-v1',
    'system',
    -- Placeholder marker — the real prompt is built dynamically in
    -- l2_planner.build_system_prompt() and injected runbook catalog
    -- changes per-incident. We record 'v1' as the baseline to give
    -- l2_decisions.prompt_version a non-null value.
    '[baseline: l2_planner.build_system_prompt() hand-written, dynamic catalog injection]',
    'active',
    'system-seed',
    NOW(),
    'system-seed',
    'Baseline seed (Phase 10 migration). Covers all L2 calls before adaptive-prompt versions activate.'
)
ON CONFLICT (version_tag) DO NOTHING;

-- Add prompt_version to l2_decisions so every decision traces back to
-- its exact prompt. Default for historical rows is 'system-v1' (the
-- baseline marker).
ALTER TABLE l2_decisions
    ADD COLUMN IF NOT EXISTS prompt_version VARCHAR(64) DEFAULT 'system-v1';

CREATE INDEX IF NOT EXISTS idx_l2_decisions_prompt_version
    ON l2_decisions (prompt_version);

-- Mined few-shot exemplars: (incident_type, canonical_runbook_id) →
-- exemplar reasoning text. Populated by the nightly miner.
CREATE TABLE IF NOT EXISTS l2_prompt_exemplars (
    id                   BIGSERIAL    PRIMARY KEY,
    incident_type        VARCHAR(100) NOT NULL,
    runbook_id           VARCHAR(255) NOT NULL,
    exemplar_text        TEXT         NOT NULL,
    source_decision_ids  BIGINT[]     NOT NULL,
    mined_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    status               VARCHAR(16)  NOT NULL DEFAULT 'draft',
                         -- 'draft' | 'approved' | 'rejected'
    approved_by          VARCHAR(100),
    approved_at          TIMESTAMPTZ,
    UNIQUE (incident_type, runbook_id)
);

CREATE INDEX IF NOT EXISTS idx_l2_exemplars_approved
    ON l2_prompt_exemplars (incident_type, runbook_id)
    WHERE status = 'approved';

COMMIT;

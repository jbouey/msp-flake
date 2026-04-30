-- Migration 262: F6 phase 2 foundation slice (Session 214 round-table
-- consensus SHIP_FOUNDATION_SLICE).
--
-- DESIGN REFERENCE:
--   docs/specs/2026-04-30-f6-federation-eligibility-tier-design.md
--   .agent/plans/f6-phase-2-enforcement-deferred.md
--
-- ROUND-TABLE CONSENSUS (unanimous, 4 reviewer voices):
--   Ship the foundation that calibration analysis needs without
--   committing to enforcement. Tier 1 + Tier 2 enforcement remain
--   DEFERRED pending HIPAA-specialist round-table + counsel review
--   of §164.528 disclosure accounting + outside-counsel BAA review.
--
-- WHAT THIS MIGRATION SHIPS:
--
-- 1. `promoted_rule_events.tier_at_promotion TEXT` (nullable, no CHECK)
--    Forward-compatibility for the eventual enforcement commit. Lets
--    the audit chain record which tier triggered each promotion when
--    enforcement does ship. NULL on every existing row + every Tier 0
--    promotion (current behavior).
--
-- 2. `flywheel_federation_candidate_daily` table — daily snapshot
--    of "patterns that WOULD clear current seed thresholds at tier N
--    if federation were enforced." Operator-visibility surface for
--    calibration analysis. Background loop writes one row per
--    (snapshot_date, tier_name, client_org_id) per day.
--    For tier_level=2 (platform), client_org_id is NULL — platform-
--    aggregation is by definition not org-scoped at the snapshot
--    layer.
--
-- WHAT THIS MIGRATION INTENTIONALLY DOES NOT SHIP:
--
-- * `promoted_rules.rollout_scope` enum — federation rollout
--   decision; deferred to enforcement round-table.
-- * Any CHECK constraint on `tier_at_promotion` — would lock the
--   set of tier names; the calibration round-table may want to
--   adjust before locking.
-- * Any background-loop schedule. The Python loop ships in the
--   companion code commit; this migration is schema-only.
-- * Any RLS or trigger on the new table. It's an operator-visibility
--   snapshot, not an audit-class table — DELETE/UPDATE allowed for
--   retention pruning.

ALTER TABLE promoted_rule_events
    ADD COLUMN IF NOT EXISTS tier_at_promotion TEXT;

COMMENT ON COLUMN promoted_rule_events.tier_at_promotion IS
    'F6 phase 2 foundation (mig 262): which tier triggered the promotion. Values: NULL (pre-federation, all current rows) | "local" | "org" | "platform". No CHECK constraint yet — calibration round-table may adjust the set before locking.';

CREATE TABLE IF NOT EXISTS flywheel_federation_candidate_daily (
    -- Surrogate PK (PRIMARY KEY columns are implicitly NOT NULL,
    -- which would conflict with the platform-tier rows where
    -- client_org_id is intentionally NULL). Uniqueness on the
    -- natural key is enforced via the COALESCE-based UNIQUE INDEX
    -- below.
    id               BIGSERIAL PRIMARY KEY,
    snapshot_date    DATE NOT NULL,
    tier_name        TEXT NOT NULL,
    -- NULL when tier_level=2 (platform aggregates across orgs by
    -- design — no per-org row at the platform tier).
    client_org_id    TEXT,
    candidate_count  INTEGER NOT NULL,
    p50_success_rate DOUBLE PRECISION,
    p95_success_rate DOUBLE PRECISION,
    snapshot_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT flywheel_fcd_tier_name_valid
        CHECK (tier_name IN ('local', 'org', 'platform')),
    CONSTRAINT flywheel_fcd_count_nonneg
        CHECK (candidate_count >= 0),
    CONSTRAINT flywheel_fcd_success_rate_range
        CHECK (
            (p50_success_rate IS NULL OR (p50_success_rate >= 0.0 AND p50_success_rate <= 1.0))
            AND (p95_success_rate IS NULL OR (p95_success_rate >= 0.0 AND p95_success_rate <= 1.0))
        ),
    -- Tier 2 (platform) MUST have NULL client_org_id; tier 0/1 MUST
    -- have non-NULL. Schema-level enforcement of the design intent.
    CONSTRAINT flywheel_fcd_org_scope_matches_tier
        CHECK (
            (tier_name = 'platform' AND client_org_id IS NULL)
            OR (tier_name IN ('local', 'org') AND client_org_id IS NOT NULL)
        )
);

-- Natural-key uniqueness via COALESCE so platform rows
-- (client_org_id NULL) play correctly with ON CONFLICT in the
-- snapshot writer's UPSERT path.
CREATE UNIQUE INDEX IF NOT EXISTS flywheel_fcd_natural_unique
    ON flywheel_federation_candidate_daily
    (snapshot_date, tier_name, COALESCE(client_org_id, ''));

COMMENT ON TABLE flywheel_federation_candidate_daily IS
    'F6 phase 2 foundation (mig 262): daily snapshot of "patterns that WOULD clear current seed thresholds at tier N if federation were enforced." Operator-visibility surface for calibration analysis. Background loop writes one row per (date, tier, org_or_null) per day. Retention: pruned by data_hygiene_gc_loop (TBD — initial recommendation 90 days for calibration window + 1 quarter trailing). NOT audit-class — UPDATE/DELETE allowed for retention.';

CREATE INDEX IF NOT EXISTS idx_flywheel_fcd_recent
    ON flywheel_federation_candidate_daily (snapshot_date DESC, tier_name);

CREATE INDEX IF NOT EXISTS idx_flywheel_fcd_org
    ON flywheel_federation_candidate_daily (client_org_id, snapshot_date DESC)
    WHERE client_org_id IS NOT NULL;

-- Audit log entry. Actor is the round-table operator
-- (jbouey2006@gmail.com) per the privileged-access convention used
-- across migrations 256/257/258/259/260/261. Note that this
-- migration is structural-only — no enforcement decisions baked in.
INSERT INTO admin_audit_log (action, target, username, details, created_at)
SELECT
    'substrate.flywheel_federation_foundation.created',
    'system',
    'jbouey2006@gmail.com',
    jsonb_build_object(
        'migration', '262',
        'tables_created', ARRAY['flywheel_federation_candidate_daily'],
        'columns_added', ARRAY['promoted_rule_events.tier_at_promotion'],
        'session', '214',
        'related_findings', ARRAY['F6-foundation'],
        'design_spec', 'docs/specs/2026-04-30-f6-federation-eligibility-tier-design.md',
        'deferred_card', '.agent/plans/f6-phase-2-enforcement-deferred.md',
        'round_table_verdict', 'SHIP_FOUNDATION_SLICE (unanimous)',
        'reason', 'Calibration-data collection foundation. No enforcement, no rollout, no cross-org WRITE. Lets the dedicated F6-phase-2-enforcement round-table use 2-3 weeks of observation data when it convenes with HIPAA specialist + counsel.'
    ),
    NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM admin_audit_log
     WHERE action = 'substrate.flywheel_federation_foundation.created'
       AND target = 'system'
);

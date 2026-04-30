-- Migration 261: F6 federation tier scaffolding (Session 214 MVP slice).
--
-- DESIGN SPEC: docs/specs/2026-04-30-f6-federation-eligibility-tier-design.md
--
-- This migration ships the SCHEMA + SEED DATA only. The `_flywheel_promotion_loop`
-- read path uses these thresholds ONLY when env var FLYWHEEL_FEDERATION_ENABLED
-- is "true"; default is "false", so this migration is a no-op for live behavior.
--
-- DELIBERATELY DEFERRED to a future session:
--   * Threshold calibration against 2-3 weeks of observation data.
--     The seed values below are conservative starting points, NOT
--     production-ready. The feature flag stays "false" in production
--     until calibration completes.
--   * Cross-org isolation enforcement (Tier 1 query MUST filter by
--     client_org_id; Tier 2 may aggregate but rollout MUST stay in
--     scope). Read path lands in a follow-up commit.
--   * Round-table on data-policy + cross-org HIPAA + threshold
--     calibration angles. This commit is structural only.
--
-- WHY SHIP THE SCAFFOLDING NOW:
--   F6 is multi-day; landing the schema as a separate, gated commit
--   lets calibration analysis use the table for threshold tuning
--   without committing to enforcement until the round-table approves
--   real values.

CREATE TABLE IF NOT EXISTS flywheel_eligibility_tiers (
    tier_name              TEXT PRIMARY KEY,
    tier_level             INTEGER NOT NULL UNIQUE,
    min_total_occurrences  INTEGER NOT NULL,
    min_success_rate       FLOAT   NOT NULL,
    min_l2_resolutions     INTEGER NOT NULL,
    max_age_days           INTEGER NOT NULL,
    -- distinct-orgs/sites: gating at tier_level >= 1 (org) or >= 2
    -- (platform); informational/NULL at tier 0 (local). Calibration
    -- migration MUST populate these for higher tiers — see CHECK
    -- constraints below.
    min_distinct_orgs      INTEGER,
    min_distinct_sites     INTEGER,
    description            TEXT NOT NULL,
    enabled                BOOLEAN NOT NULL DEFAULT FALSE,  -- per-tier kill switch
    -- Cross-org isolation gate. TRUE = Tier 1+ MUST filter eligibility
    -- queries by client_org_id, even though Tier 2 aggregates across
    -- orgs in `platform_pattern_stats` for distinct-counting. Round-
    -- table 2026-04-30 P2 — schema-level signal so the F6 phase 2
    -- implementer doesn't accidentally relax the HIPAA boundary.
    org_isolation_required BOOLEAN,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Threshold values are calibration-pending; this column tracks
    -- whether the tier_level has been calibrated against real data.
    calibrated_at          TIMESTAMPTZ,
    CONSTRAINT flywheel_tier_level_range
        CHECK (tier_level >= 0 AND tier_level <= 2),
    CONSTRAINT flywheel_tier_success_rate_range
        CHECK (min_success_rate >= 0.0 AND min_success_rate <= 1.0),
    -- Cross-org isolation is non-optional at tier_level >= 1 (HIPAA
    -- boundary). The schema enforces TRUE; tier 0 may be NULL.
    CONSTRAINT flywheel_tier_org_isolation_required
        CHECK (tier_level = 0 OR org_isolation_required = TRUE),
    -- Calibration discipline: when calibrated_at is set on a higher-
    -- tier row, distinct-counting thresholds MUST be set explicitly.
    -- Round-table 2026-04-30 P1: forces the calibration migration to
    -- choose values, not inherit defaults.
    CONSTRAINT flywheel_tier_distinct_orgs_required_when_calibrated
        CHECK (calibrated_at IS NULL OR tier_level < 2 OR min_distinct_orgs IS NOT NULL),
    CONSTRAINT flywheel_tier_distinct_sites_required_when_calibrated
        CHECK (calibrated_at IS NULL OR tier_level < 1 OR min_distinct_sites IS NOT NULL)
);

COMMENT ON TABLE flywheel_eligibility_tiers IS
    'F6 federation tier configuration (Session 214 MVP). Three tiers: 0=local, 1=org-aggregated, 2=platform-aggregated. Promotion eligibility query consults this table when FLYWHEEL_FEDERATION_ENABLED=true (env). Threshold values are calibration-pending; do NOT promote to enforce until round-table approves calibrated values.';

COMMENT ON COLUMN flywheel_eligibility_tiers.enabled IS
    'Per-tier kill switch. All seed rows ship with enabled=FALSE. Cutover to enforce flips this column AFTER round-table approves calibrated thresholds for that tier.';

COMMENT ON COLUMN flywheel_eligibility_tiers.calibrated_at IS
    'NULL until thresholds for this tier have been calibrated against real observation data. Round-table sign-off on calibrated values populates this timestamp; before then the tier is structural only.';

-- Seed conservative starting values. ALL DISABLED.
--
-- Round-table 2026-04-30 P1 catch: distinct_orgs/sites for higher
-- tiers ship as NULL, NOT placeholder numbers — placeholder values
-- in seed rows look like calibrated decisions and over-anchor the
-- calibration round-table. Schema CHECK constraints enforce that
-- calibrated_at cannot be set without filling these in. The
-- calibration migration will INSERT real values explicitly.
--
-- min_distinct_sites is set to 1 on the `local` tier row only because
-- per-site eligibility BY DEFINITION requires 1 distinct site
-- (informational at tier 0; gating at tier 1+).
INSERT INTO flywheel_eligibility_tiers (
    tier_name, tier_level,
    min_total_occurrences, min_success_rate, min_l2_resolutions,
    max_age_days, min_distinct_orgs, min_distinct_sites,
    description, enabled, org_isolation_required, calibrated_at
) VALUES
    ('local', 0,
     5, 0.90, 3,
     7, NULL, 1,
     'Per-site eligibility — current production thresholds preserved as the local tier baseline. Disabled until federation read path is wired AND calibration completes.',
     FALSE, NULL, NULL),
    ('org', 1,
     15, 0.90, 5,
     14, NULL, NULL,
     'Org-aggregated eligibility — pattern validated across multiple sites within same client_org_id. Cross-org leak: Tier 1 query MUST filter by client_org_id (org_isolation_required=TRUE enforces). min_distinct_sites is calibration-pending (NULL until calibration migration sets explicitly).',
     FALSE, TRUE, NULL),
    ('platform', 2,
     50, 0.95, 10,
     30, NULL, NULL,
     'Platform-aggregated eligibility — cross-org generalization signal. Aggregates via platform_pattern_stats but rollout MUST stay within scope (org_isolation_required=TRUE). min_distinct_orgs and min_distinct_sites are calibration-pending (NULL until calibration migration sets explicitly).',
     FALSE, TRUE, NULL)
ON CONFLICT (tier_name) DO NOTHING;

-- Audit log entry — operator (jbouey2006@gmail.com) is accountable
-- for the schema decision; threshold values are still pending review.
INSERT INTO admin_audit_log (action, target, username, details, created_at)
SELECT
    'substrate.flywheel_federation_scaffold.created',
    'system',
    'jbouey2006@gmail.com',
    jsonb_build_object(
        'migration', '261',
        'feature_flag', 'FLYWHEEL_FEDERATION_ENABLED',
        'feature_flag_default', 'false',
        'tiers_seeded', ARRAY['local', 'org', 'platform'],
        'tiers_enabled', ARRAY[]::TEXT[],
        'session', '214',
        'related_findings', ARRAY['F6'],
        'design_spec', 'docs/specs/2026-04-30-f6-federation-eligibility-tier-design.md',
        'calibration_status', 'PENDING — seed values are conservative placeholders, NOT production-ready. Do NOT enable any tier without round-table approval of calibrated values from observation window.',
        'reason', 'F6 MVP slice — schema + feature flag in OFF state. Lets calibration analysis use the table without committing to enforcement.'
    ),
    NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM admin_audit_log
     WHERE action = 'substrate.flywheel_federation_scaffold.created'
       AND target = 'system'
);

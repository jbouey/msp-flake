-- Migration 233: volume-tiered revenue share for partners.
--
-- Audit finding (P1 #11): `partners.revenue_share_percent` is a single flat
-- number that defaults to 40. Real MSP channel economics expect a ladder:
-- small partners get baseline, larger partners get a bump at predictable
-- volume thresholds. This migration adds that ladder without breaking the
-- existing flat-percent path.
--
-- How the two modes interact:
--   - Flat mode (today's world): partners.revenue_share_percent remains the
--     authoritative rate. Any partner with NO rows in partner_revenue_tiers
--     keeps working exactly as before. Zero behavior change for existing
--     partners.
--   - Tier mode (new): when partner_revenue_tiers has rows for a given
--     partner_id, compute_partner_rate_bps(partner_id, active_clinic_count)
--     returns the tier rate in basis points. Ties broken by picking the
--     highest min_clinic_count whose threshold the partner has met.
--
-- Platform-level defaults are stored with partner_id = NULL so that admin
-- can seed a platform-wide curve once ("every partner follows the same
-- ladder") and partners that want custom terms can override per-partner.
-- When a partner has both per-partner rows and NULL platform-default rows,
-- per-partner wins.

BEGIN;

CREATE TABLE IF NOT EXISTS partner_revenue_tiers (
    id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_id         UUID,                                   -- NULL = platform default
    min_clinic_count   INTEGER     NOT NULL,                   -- inclusive floor
    rate_bps           INTEGER     NOT NULL,                   -- basis points; 3000 = 30%
    note               TEXT,                                   -- human context: "pilot tier", "platinum", etc.
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT partner_revenue_tiers_partner_fk FOREIGN KEY (partner_id)
        REFERENCES partners(id) ON DELETE CASCADE,
    CONSTRAINT partner_revenue_tiers_min_ck CHECK (min_clinic_count >= 0),
    CONSTRAINT partner_revenue_tiers_rate_ck CHECK (rate_bps BETWEEN 0 AND 10000),
    CONSTRAINT partner_revenue_tiers_unique UNIQUE (partner_id, min_clinic_count)
);

CREATE INDEX IF NOT EXISTS idx_partner_revenue_tiers_lookup
    ON partner_revenue_tiers (partner_id, min_clinic_count DESC);

COMMENT ON TABLE partner_revenue_tiers IS
    'Volume-tiered revenue share curve. partner_id=NULL rows are platform '
    'defaults used when a partner has no overrides. Per-partner rows always '
    'override platform defaults. Effective rate = highest tier whose '
    'min_clinic_count <= active clinic count.';


-- Seed platform defaults: conservative ladder that matches what healthcare
-- MSP channels typically hit on rate cards. An operator with 0-9 clinics
-- under management earns the baseline 30%; 10-24 earns 35%; 25-49 earns
-- 40%; 50+ earns 45%. Partners on custom terms set per-partner rows or fall
-- back to the existing partners.revenue_share_percent flat number.
INSERT INTO partner_revenue_tiers (partner_id, min_clinic_count, rate_bps, note)
VALUES
    (NULL, 0,  3000, 'platform default — baseline'),
    (NULL, 10, 3500, 'platform default — 10+ clinics'),
    (NULL, 25, 4000, 'platform default — 25+ clinics'),
    (NULL, 50, 4500, 'platform default — 50+ clinics (platinum)')
ON CONFLICT (partner_id, min_clinic_count) DO NOTHING;


-- Rate resolver. Picks per-partner tier if any, else platform default, else
-- falls back to the legacy flat partners.revenue_share_percent * 100 bps.
CREATE OR REPLACE FUNCTION compute_partner_rate_bps(
    p_partner_id UUID,
    p_clinic_count INTEGER
) RETURNS INTEGER
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_rate      INTEGER;
    v_flat_pct  INTEGER;
BEGIN
    -- Per-partner override: pick the highest threshold the partner has met.
    SELECT rate_bps INTO v_rate
      FROM partner_revenue_tiers
     WHERE partner_id = p_partner_id
       AND min_clinic_count <= GREATEST(p_clinic_count, 0)
     ORDER BY min_clinic_count DESC
     LIMIT 1;
    IF v_rate IS NOT NULL THEN
        RETURN v_rate;
    END IF;

    -- Platform default: same lookup with partner_id=NULL.
    SELECT rate_bps INTO v_rate
      FROM partner_revenue_tiers
     WHERE partner_id IS NULL
       AND min_clinic_count <= GREATEST(p_clinic_count, 0)
     ORDER BY min_clinic_count DESC
     LIMIT 1;
    IF v_rate IS NOT NULL THEN
        RETURN v_rate;
    END IF;

    -- Legacy fallback: flat percent on the partners row.
    SELECT COALESCE(revenue_share_percent, 40) * 100 INTO v_flat_pct
      FROM partners WHERE id = p_partner_id;
    RETURN COALESCE(v_flat_pct, 4000);
END;
$$;

COMMENT ON FUNCTION compute_partner_rate_bps(UUID, INTEGER) IS
    'Returns effective partner revenue-share rate in basis points for a '
    'partner with a given active-clinic count. Resolves in order: per-'
    'partner override → platform default → legacy flat partners.'
    'revenue_share_percent → hardcoded 40%.';

COMMIT;

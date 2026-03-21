-- Migration 095: Site-level partner override
-- Allows a sub-partner to manage a specific site within an org,
-- overriding the org's current_partner_id for that site only.
-- If NULL, the site inherits the org's partner.

ALTER TABLE sites
    ADD COLUMN IF NOT EXISTS sub_partner_id UUID REFERENCES partners(id);

COMMENT ON COLUMN sites.sub_partner_id IS
    'Optional sub-partner override. If set, this partner manages the site instead of the org default partner.';

CREATE INDEX IF NOT EXISTS idx_sites_sub_partner ON sites(sub_partner_id) WHERE sub_partner_id IS NOT NULL;

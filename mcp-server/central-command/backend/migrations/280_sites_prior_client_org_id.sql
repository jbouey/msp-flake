-- Migration 280: sites.prior_client_org_id + canonical alias extension
--
-- Round-table 21 (2026-05-05) Linda DBA item. After a successful cross-
-- org relocate, sites.client_org_id flips to the new org and the prior
-- client_org_id is recorded here. Auditor kits walk the chain across
-- the org boundary by looking up this column.
--
-- Brian Option A from RT21: cryptographic chain is IMMUTABLE. The
-- compliance_bundles + provisioning_claim_events written under the
-- source org stay under the source site_id forever. This column
-- provides the lookup so an auditor reading the kit ZIP under the
-- TARGET org can find the bundles still under the source org's chain.

-- Add the column. Nullable — sites that never relocated keep NULL.
-- A site that has been through MULTIPLE cross-org moves chains via
-- self-references (column points at the immediately-prior org; the
-- chain attests the full lineage).
ALTER TABLE sites
    ADD COLUMN IF NOT EXISTS prior_client_org_id UUID
        REFERENCES client_orgs(id);

COMMENT ON COLUMN sites.prior_client_org_id IS
    'RT21 (2026-05-05): the client_org_id that owned this site immediately '
    'before the most-recent cross-org relocate. NULL for sites that have '
    'never relocated. Auditor kits walk the cryptographic chain across the '
    'org boundary via this column. The compliance_bundles + chain are '
    'IMMUTABLE — they remain anchored at the original site_id under the '
    'PRIOR org_id; this column is the lookup pointer.';

-- Index supports auditor-kit queries that resolve "which orgs has this
-- site lived under." Selective: only matters for sites that relocated,
-- and the rest are NULL (excluded by partial index).
CREATE INDEX IF NOT EXISTS idx_sites_prior_client_org_id
    ON sites (prior_client_org_id)
    WHERE prior_client_org_id IS NOT NULL;

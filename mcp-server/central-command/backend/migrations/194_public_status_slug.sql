-- Migration 194: public status page slug (#144 P2).
--
-- Customers can opt-in to a public status page at /status/{slug} showing
-- green/amber/red per appliance, backed by the rollup MV. Slug is random
-- + unguessable so the page is "secret-URL public" not full-public (customers
-- who want true public can share; customers who don't can keep private).
--
-- This also builds pressure to keep our liveness data honest — if the
-- public page disagrees with reality, the customer catches us.

BEGIN;

ALTER TABLE sites
    ADD COLUMN IF NOT EXISTS public_status_slug TEXT UNIQUE;

COMMENT ON COLUMN sites.public_status_slug IS
    'Unguessable slug for the public status page at /status/{slug}. '
    'Null means the site has no public page. Rotated by regenerate endpoint.';

-- Generate initial slugs for existing sites — NULL by default. Opt-in only.
-- Admins call POST /api/admin/sites/{site_id}/status-slug to generate.

COMMIT;

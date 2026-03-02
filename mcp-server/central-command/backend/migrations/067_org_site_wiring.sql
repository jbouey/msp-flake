-- Migration 067: Wire Organization -> Site relationship
-- Backfills client_orgs for orphan sites and enforces NOT NULL on client_org_id
--
-- Context: client_orgs table exists (migration 029) and sites.client_org_id FK exists,
-- but no backend code uses it. This migration ensures every site belongs to an org.

-- Step 1: Ensure sites exist for all site_appliances entries
-- (Some sites only exist in site_appliances from checkins, not in the sites table)
INSERT INTO sites (site_id, clinic_name, status, onboarding_stage)
SELECT DISTINCT
    sa.site_id,
    INITCAP(REPLACE(sa.site_id, '-', ' ')),
    'active',
    'active'
FROM site_appliances sa
LEFT JOIN sites s ON s.site_id = sa.site_id
WHERE s.site_id IS NULL
ON CONFLICT (site_id) DO NOTHING;

-- Step 2: Create a client_org for each site that has no org assignment
-- Uses clinic_name from the sites table, generates a placeholder email
INSERT INTO client_orgs (id, name, primary_email, status)
SELECT
    gen_random_uuid(),
    COALESCE(s.clinic_name, INITCAP(REPLACE(s.site_id, '-', ' '))),
    LOWER(REPLACE(s.site_id, ' ', '-')) || '@auto-generated.local',
    'active'
FROM sites s
WHERE s.client_org_id IS NULL;

-- Step 3: Link each orphan site to its newly created org (match by name)
UPDATE sites s
SET client_org_id = co.id
FROM client_orgs co
WHERE s.client_org_id IS NULL
  AND co.primary_email = LOWER(REPLACE(s.site_id, ' ', '-')) || '@auto-generated.local';

-- Step 4: Safety net - if any sites still have NULL client_org_id,
-- create a catch-all org and assign them
DO $$
DECLARE
    catchall_id UUID;
BEGIN
    IF EXISTS (SELECT 1 FROM sites WHERE client_org_id IS NULL) THEN
        INSERT INTO client_orgs (id, name, primary_email, status)
        VALUES (gen_random_uuid(), 'Unassigned Sites', 'unassigned@auto-generated.local', 'active')
        ON CONFLICT (primary_email) DO UPDATE SET name = 'Unassigned Sites'
        RETURNING id INTO catchall_id;

        IF catchall_id IS NULL THEN
            SELECT id INTO catchall_id FROM client_orgs WHERE primary_email = 'unassigned@auto-generated.local';
        END IF;

        UPDATE sites SET client_org_id = catchall_id WHERE client_org_id IS NULL;
    END IF;
END $$;

-- Step 5: Enforce NOT NULL going forward
ALTER TABLE sites ALTER COLUMN client_org_id SET NOT NULL;

-- Step 6: Index already exists from migration 029 but ensure it's there
CREATE INDEX IF NOT EXISTS idx_sites_client_org ON sites(client_org_id);

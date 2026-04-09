-- Migration 144: Add FK constraint go_agents.site_id → sites.site_id
-- Prevents orphaned agents under wrong/nonexistent sites.
-- Session 202 round table recommendation.

-- Clean up any orphaned agents first
DELETE FROM go_agents WHERE site_id NOT IN (SELECT site_id FROM sites);

-- Add the foreign key
ALTER TABLE go_agents
    ADD CONSTRAINT go_agents_site_id_fk
    FOREIGN KEY (site_id) REFERENCES sites(site_id)
    ON DELETE CASCADE;

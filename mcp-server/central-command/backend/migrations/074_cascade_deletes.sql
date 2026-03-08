-- Migration 074: Add CASCADE DELETE to prevent orphaned records
-- When a site is deleted, all child records must be cleaned up

-- Incidents cascade on site deletion
ALTER TABLE incidents DROP CONSTRAINT IF EXISTS incidents_site_id_fkey;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='incidents' AND column_name='site_id') THEN
        ALTER TABLE incidents ADD CONSTRAINT incidents_site_id_fkey
            FOREIGN KEY (site_id) REFERENCES sites(site_id) ON DELETE CASCADE;
    END IF;
END $$;

-- Site credentials cascade on site deletion
ALTER TABLE site_credentials DROP CONSTRAINT IF EXISTS site_credentials_site_id_fkey;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='site_credentials' AND column_name='site_id') THEN
        ALTER TABLE site_credentials ADD CONSTRAINT site_credentials_site_id_fkey
            FOREIGN KEY (site_id) REFERENCES sites(site_id) ON DELETE CASCADE;
    END IF;
END $$;

-- API keys cascade on site deletion
ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS api_keys_site_id_fkey;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='api_keys' AND column_name='site_id') THEN
        ALTER TABLE api_keys ADD CONSTRAINT api_keys_site_id_fkey
            FOREIGN KEY (site_id) REFERENCES sites(site_id) ON DELETE CASCADE;
    END IF;
END $$;

-- Go agents cascade on site deletion
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='go_agents' AND column_name='site_id') THEN
        ALTER TABLE go_agents DROP CONSTRAINT IF EXISTS go_agents_site_id_fkey;
        ALTER TABLE go_agents ADD CONSTRAINT go_agents_site_id_fkey
            FOREIGN KEY (site_id) REFERENCES sites(site_id) ON DELETE CASCADE;
    END IF;
END $$;

-- Workstations cascade on site deletion
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='workstations' AND column_name='site_id') THEN
        ALTER TABLE workstations DROP CONSTRAINT IF EXISTS workstations_site_id_fkey;
        ALTER TABLE workstations ADD CONSTRAINT workstations_site_id_fkey
            FOREIGN KEY (site_id) REFERENCES sites(site_id) ON DELETE CASCADE;
    END IF;
END $$;

-- Workstation checks cascade when workstation deleted
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='workstation_checks' AND column_name='workstation_id') THEN
        ALTER TABLE workstation_checks DROP CONSTRAINT IF EXISTS workstation_checks_workstation_id_fkey;
        ALTER TABLE workstation_checks ADD CONSTRAINT workstation_checks_workstation_id_fkey
            FOREIGN KEY (workstation_id) REFERENCES workstations(id) ON DELETE CASCADE;
    END IF;
END $$;

-- Site runbook config cascade on site deletion
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='site_runbook_config' AND column_name='site_id') THEN
        ALTER TABLE site_runbook_config DROP CONSTRAINT IF EXISTS site_runbook_config_site_id_fkey;
        ALTER TABLE site_runbook_config ADD CONSTRAINT site_runbook_config_site_id_fkey
            FOREIGN KEY (site_id) REFERENCES sites(site_id) ON DELETE CASCADE;
    END IF;
END $$;

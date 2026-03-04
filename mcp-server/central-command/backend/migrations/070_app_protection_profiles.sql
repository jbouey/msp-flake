-- Migration: 070_app_protection_profiles.sql
-- Application Protection Profiles: proprietary business application protection.
-- Partners register apps (Epic EHR, Dentrix, etc.), discovery swarm finds critical
-- assets, baseline locks golden state, auto-generated L1 rules enforce no-drift.

BEGIN;

-- ─── Protection Profiles ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_protection_profiles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id         VARCHAR(255) NOT NULL,
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    status          VARCHAR(50) NOT NULL DEFAULT 'draft',
    -- draft → discovering → discovered → baseline_locked → active → paused → archived
    created_by      VARCHAR(255),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    discovery_data  JSONB,          -- raw discovery results from appliance
    baseline_data   JSONB,          -- locked golden state snapshot
    template_id     UUID,           -- optional source template
    UNIQUE(site_id, name)
);

CREATE INDEX IF NOT EXISTS idx_app_profiles_site ON app_protection_profiles(site_id);
CREATE INDEX IF NOT EXISTS idx_app_profiles_status ON app_protection_profiles(status);

-- ─── Discovered & Baselined Assets ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_profile_assets (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id      UUID NOT NULL REFERENCES app_protection_profiles(id) ON DELETE CASCADE,
    asset_type      VARCHAR(50) NOT NULL,
    -- service, port, registry_key, scheduled_task, config_file, database_conn, iis_binding, odbc_dsn, process
    asset_name      VARCHAR(500) NOT NULL,
    display_name    VARCHAR(500),
    baseline_value  JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- e.g. {"state": "Running", "start_type": "Automatic"} for services
    -- e.g. {"port": 1433, "protocol": "TCP", "process": "sqlservr.exe"} for ports
    -- e.g. {"path": "HKLM\\Software\\Epic\\...", "value": "...", "type": "REG_SZ"} for registry
    enabled         BOOLEAN NOT NULL DEFAULT true,
    runbook_id      VARCHAR(255),   -- existing runbook that handles this asset type
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_app_assets_profile ON app_profile_assets(profile_id);
CREATE INDEX IF NOT EXISTS idx_app_assets_type ON app_profile_assets(asset_type);

-- ─── Auto-generated L1 Rules Per Asset ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_profile_rules (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id      UUID NOT NULL REFERENCES app_protection_profiles(id) ON DELETE CASCADE,
    asset_id        UUID NOT NULL REFERENCES app_profile_assets(id) ON DELETE CASCADE,
    l1_rule_id      VARCHAR(255) NOT NULL,  -- e.g. APP-{profile_id_prefix}-SVC-001
    rule_json       JSONB NOT NULL,         -- full L1 rule definition for sync
    enabled         BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_app_rules_profile ON app_profile_rules(profile_id);
CREATE INDEX IF NOT EXISTS idx_app_rules_l1 ON app_profile_rules(l1_rule_id);

-- ─── Template Library (pre-built discovery hints for common apps) ───────────
CREATE TABLE IF NOT EXISTS app_profile_templates (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL UNIQUE,
    description     TEXT,
    category        VARCHAR(100),   -- EHR, Practice_Management, Imaging, Lab, Billing
    discovery_hints JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- e.g. {"service_patterns": ["Epic*", "Hyperspace*"], "port_hints": [1433, 443],
    --       "registry_paths": ["HKLM\\Software\\Epic\\*"], "process_patterns": ["Epic*"]}
    icon            VARCHAR(50),    -- optional icon name for UI
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── Seed common healthcare app templates ───────────────────────────────────
INSERT INTO app_profile_templates (name, description, category, discovery_hints, icon) VALUES
    ('Epic EHR', 'Epic Systems electronic health records platform', 'EHR',
     '{"service_patterns": ["Epic*", "Hyperspace*", "Cache*", "EpicPrint*"],
       "port_hints": [1972, 443, 8443, 1433],
       "registry_paths": ["HKLM\\\\Software\\\\Epic\\\\*", "HKLM\\\\Software\\\\InterSystems\\\\*"],
       "process_patterns": ["Epic*", "cache*", "Hyperspace*"]}'::jsonb, 'hospital'),

    ('Dentrix', 'Henry Schein Dentrix dental practice management', 'Practice_Management',
     '{"service_patterns": ["Dentrix*", "DTX*", "eBackup*"],
       "port_hints": [1433, 3306, 9100],
       "registry_paths": ["HKLM\\\\Software\\\\Dentrix\\\\*", "HKLM\\\\Software\\\\Henry Schein\\\\*"],
       "process_patterns": ["Dentrix*", "DTX*"]}'::jsonb, 'tooth'),

    ('athenahealth', 'athenahealth cloud-based EHR (local components)', 'EHR',
     '{"service_patterns": ["athena*", "Athena*"],
       "port_hints": [443, 8080],
       "registry_paths": ["HKLM\\\\Software\\\\athenahealth\\\\*"],
       "process_patterns": ["athena*"]}'::jsonb, 'cloud'),

    ('eClinicalWorks', 'eClinicalWorks EHR and practice management', 'EHR',
     '{"service_patterns": ["eCW*", "eClinical*"],
       "port_hints": [1433, 443, 8080],
       "registry_paths": ["HKLM\\\\Software\\\\eClinicalWorks\\\\*"],
       "process_patterns": ["eCW*", "eClinical*"]}'::jsonb, 'hospital'),

    ('Eaglesoft', 'Patterson Dental Eaglesoft practice management', 'Practice_Management',
     '{"service_patterns": ["Eaglesoft*", "Patterson*", "PattersonServer*"],
       "port_hints": [1433, 5432],
       "registry_paths": ["HKLM\\\\Software\\\\Patterson\\\\*", "HKLM\\\\Software\\\\Eaglesoft\\\\*"],
       "process_patterns": ["Eaglesoft*", "Patterson*"]}'::jsonb, 'tooth')
ON CONFLICT (name) DO NOTHING;

COMMIT;

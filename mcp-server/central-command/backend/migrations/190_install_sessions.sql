-- Migration 190: install_sessions table + retire phantom installer registrations.
--
-- Root cause: the installer ISO runs the same daemon binary as a production
-- appliance, with the same checkin flow. When someone boots the installer on
-- target hardware and waits on the install screen, the daemon checks in and
-- registers itself as a real row in site_appliances (as "osiriscare-installer"
-- or "osiriscare-N"). This pollutes fleet counts, triggers mesh-assignment
-- loops, creates bogus evidence bundles, and confuses operators.
--
-- Fix: installer checkins (boot_source='live_usb') route to install_sessions
-- instead of site_appliances. Ephemeral, TTL-bounded (24h), no mesh, no
-- fleet orders, no evidence. Retire existing phantoms at migration time.

BEGIN;

CREATE TABLE IF NOT EXISTS install_sessions (
    session_id      TEXT PRIMARY KEY,                -- "{site_id}:{mac}"
    site_id         TEXT NOT NULL,
    mac_address     TEXT NOT NULL,
    hostname        TEXT,
    ip_addresses    JSONB DEFAULT '[]'::jsonb,
    agent_version   TEXT,
    nixos_version   TEXT,
    boot_source     TEXT,
    first_seen      TIMESTAMPTZ DEFAULT NOW(),
    last_seen       TIMESTAMPTZ DEFAULT NOW(),
    checkin_count   INTEGER DEFAULT 1,
    install_stage   TEXT DEFAULT 'live_usb',         -- live_usb | installing | installed | abandoned
    expires_at      TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '24 hours')
);

CREATE INDEX IF NOT EXISTS idx_install_sessions_site ON install_sessions(site_id);
CREATE INDEX IF NOT EXISTS idx_install_sessions_expires ON install_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_install_sessions_last_seen ON install_sessions(last_seen DESC);

COMMENT ON TABLE install_sessions IS
    'Ephemeral registrations for appliances booted from live USB installers. '
    'Isolated from site_appliances so phantom rows never pollute the fleet. '
    'Rows expire 24h after last_seen — cleaned up by a background task.';

-- Retire existing phantom installer rows in site_appliances.
-- Criteria: hostname matches known installer hostnames AND daemon_health
-- indicates live_usb boot_source. Soft-delete via deleted_at so we keep
-- the audit trail.
WITH phantoms AS (
    SELECT appliance_id, site_id, hostname, mac_address, last_checkin,
           ip_addresses, agent_version, nixos_version,
           daemon_health->>'boot_source' AS boot_source,
           first_checkin
    FROM site_appliances
    WHERE deleted_at IS NULL
      AND (
          hostname IN ('osiriscare-installer', 'nixos-installer', 'nixos')
          OR daemon_health->>'boot_source' = 'live_usb'
      )
),
migrated AS (
    INSERT INTO install_sessions (
        session_id, site_id, mac_address, hostname, ip_addresses,
        agent_version, nixos_version, boot_source,
        first_seen, last_seen, install_stage
    )
    SELECT
        p.site_id || ':' || p.mac_address AS session_id,
        p.site_id,
        p.mac_address,
        p.hostname,
        COALESCE(p.ip_addresses, '[]'::jsonb),
        p.agent_version,
        p.nixos_version,
        COALESCE(p.boot_source, 'live_usb'),
        p.first_checkin,
        p.last_checkin,
        CASE
            WHEN p.last_checkin < NOW() - INTERVAL '1 hour' THEN 'abandoned'
            ELSE 'live_usb'
        END
    FROM phantoms p
    ON CONFLICT (session_id) DO UPDATE SET
        last_seen = EXCLUDED.last_seen,
        ip_addresses = EXCLUDED.ip_addresses,
        install_stage = EXCLUDED.install_stage
    RETURNING session_id
)
UPDATE site_appliances
SET deleted_at = NOW(),
    deleted_by = 'migration_190_install_sessions'
WHERE appliance_id IN (SELECT appliance_id FROM phantoms)
  AND deleted_at IS NULL;

COMMIT;

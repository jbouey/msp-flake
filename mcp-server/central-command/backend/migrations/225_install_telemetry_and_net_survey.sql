-- Migration 225: install_sessions telemetry + first-boot network survey
--
-- Session 207 v36 — "generic install" hardening from the round-table post
-- t740 debug. See .agent/plans/v36-generic-install.md.
--
-- Adds per-MAC failure telemetry + the first-boot network environment
-- survey results, so Central Command has cloud-side visibility into
-- stuck installs BEFORE the first successful checkin. Today the box can
-- silently brick on a DNS-filtered network for hours with no signal;
-- after this migration + the paired ISO-side POST, the dashboard shows
-- "Installer attempted 12 times, last error: NXDOMAIN" within 5 min of
-- the first failed retry.
--
-- Zero-impact additive schema change: all new columns nullable (or
-- default zero). No backfill required. Existing reads/writes unaffected.

BEGIN;

ALTER TABLE install_sessions
    ADD COLUMN IF NOT EXISTS last_error_code     INT,
    ADD COLUMN IF NOT EXISTS last_error_detail   TEXT,
    ADD COLUMN IF NOT EXISTS last_error_at       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS dns_resolver_used   TEXT,
    ADD COLUMN IF NOT EXISTS api_resolved_ip     TEXT,
    ADD COLUMN IF NOT EXISTS provision_attempts  INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS net_survey          JSONB,
    ADD COLUMN IF NOT EXISTS net_survey_at       TIMESTAMPTZ;

-- Fast lookup for "appliances with active provisioning errors" —
-- used by the substrate_health panel to surface stuck installs.
CREATE INDEX IF NOT EXISTS idx_install_sessions_with_errors
    ON install_sessions (last_error_at DESC)
    WHERE last_error_code IS NOT NULL AND last_error_code <> 0;

-- Fast lookup for "appliances that completed net_survey recently" —
-- used to render the Network health matrix on appliance detail view.
CREATE INDEX IF NOT EXISTS idx_install_sessions_with_survey
    ON install_sessions (net_survey_at DESC)
    WHERE net_survey IS NOT NULL;

COMMENT ON COLUMN install_sessions.last_error_code IS
    'curl exit status or HTTP code from the most recent failed /api/provision/{mac} attempt. 0 = no error, 6 = DNS NXDOMAIN, 7 = connect refused, 28 = timeout, 3-digit = HTTP response code.';
COMMENT ON COLUMN install_sessions.last_error_detail IS
    'Human-readable curl error message or HTTP body snippet.';
COMMENT ON COLUMN install_sessions.dns_resolver_used IS
    'IP of the DNS server that answered (or failed). Helps detect DNS filters (Pi-hole, Umbrella).';
COMMENT ON COLUMN install_sessions.api_resolved_ip IS
    'IP that api.osiriscare.net resolved to (or NULL if NXDOMAIN). Should be 178.156.162.116 for the current VPS — mismatch means DNS hijacking or stale filter.';
COMMENT ON COLUMN install_sessions.provision_attempts IS
    'Monotonic counter of provisioning retry attempts. Grows indefinitely during DNS filter blocks (v35 persistent retry). Resets to 0 on successful provision (row effectively moves to site_appliances).';
COMMENT ON COLUMN install_sessions.net_survey IS
    'First-boot network environment survey — NTP skew, DNS path, HTTPS reach, captive portal, IPv6, VLAN. Schema: see iso/appliance-disk-image.nix msp-net-survey.service.';

COMMIT;

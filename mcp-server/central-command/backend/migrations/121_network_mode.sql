-- Migration 121: Network stability mode per site
-- Forces an explicit decision during onboarding: static_lease or dynamic_mdns.
-- No site should operate without a deliberate network stability choice.
-- Values: 'pending' (not yet decided), 'static_lease' (DHCP reservation confirmed),
--         'dynamic_mdns' (mDNS auto-discovery enabled, no router config)

ALTER TABLE sites ADD COLUMN IF NOT EXISTS network_mode VARCHAR(20) DEFAULT 'pending';

GRANT SELECT, UPDATE ON sites TO mcp_app;

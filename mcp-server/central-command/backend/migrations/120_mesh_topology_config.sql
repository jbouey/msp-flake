-- Migration 120: Mesh topology configuration per site
-- Allows sites with consumer-grade routers (no inter-VLAN routing)
-- to declare "independent" appliance topology, suppressing mesh alerts.
-- Values: 'auto' (default, mesh alerts active), 'independent' (mesh alerts suppressed)

ALTER TABLE sites ADD COLUMN IF NOT EXISTS mesh_topology VARCHAR(20) DEFAULT 'auto';

-- Grant to app role
GRANT SELECT, UPDATE ON sites TO mcp_app;

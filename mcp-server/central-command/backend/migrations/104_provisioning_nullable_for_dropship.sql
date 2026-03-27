-- Allow unclaimed appliances (drop-ship provisioning flow)
-- Appliance calls home before being assigned to a site
ALTER TABLE appliance_provisioning ALTER COLUMN site_id DROP NOT NULL;
ALTER TABLE appliance_provisioning ALTER COLUMN api_key DROP NOT NULL;

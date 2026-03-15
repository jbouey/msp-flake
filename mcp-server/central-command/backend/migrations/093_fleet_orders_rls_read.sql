-- Migration 093: Allow authenticated connections to read fleet_orders
--
-- Fleet orders are fleet-wide (no site_id column), but the RLS policy
-- was admin-only, preventing appliance checkins from seeing their orders.
-- This adds a SELECT-only policy for any connection with a valid tenant.
-- Fleet order completions also need the same treatment.

-- Allow any authenticated connection to SELECT fleet orders
CREATE POLICY fleet_orders_read_all ON fleet_orders
    FOR SELECT
    USING (true);

-- Allow any authenticated connection to SELECT and INSERT completions
-- (appliances need to record skipped/completed status)
CREATE POLICY fleet_order_completions_read_all ON fleet_order_completions
    FOR SELECT
    USING (true);

CREATE POLICY fleet_order_completions_insert_all ON fleet_order_completions
    FOR INSERT
    WITH CHECK (true);

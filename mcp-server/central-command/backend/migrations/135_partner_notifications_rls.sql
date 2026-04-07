-- Add tenant-scoped RLS policy to partner_notifications.
-- Partners should only see their own notifications.

CREATE POLICY partner_notifications_tenant ON partner_notifications
  FOR ALL
  USING (partner_id::text = current_setting('app.current_tenant', true));

SELECT 'Migration 135_partner_notifications_rls completed successfully' AS status;

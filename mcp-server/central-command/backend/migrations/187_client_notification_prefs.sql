-- Client portal notification preferences (Session 206 round-table P2).
--
-- Lets practice managers opt into/out of the email digest, critical
-- alerts, and the weekly summary email. Default is opt-in for digests
-- + critical alerts, opt-OUT for weekly (so we don't spam new clients).

BEGIN;

CREATE TABLE IF NOT EXISTS client_notification_prefs (
    site_id         TEXT PRIMARY KEY REFERENCES sites(site_id) ON DELETE CASCADE,
    email_digest    BOOLEAN DEFAULT TRUE,
    critical_alerts BOOLEAN DEFAULT TRUE,
    weekly_summary  BOOLEAN DEFAULT FALSE,
    notify_email    TEXT,
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

COMMIT;

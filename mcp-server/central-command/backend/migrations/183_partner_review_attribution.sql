-- Partner attribution for client portal hero card (Session 206 round-table).
--
-- Client-portal UX research: end customers (practice managers) trust a
-- named human signing off on compliance more than any number, so the
-- hero card surfaces "Partner: Acme IT (Jenn K.) · Last reviewed Apr 12".
--
-- Schema change: two columns on `sites` updated whenever a partner
-- opens the /partner/site/{id}/review page, plus a trigger so any
-- INSERT into `partner_activity_log` for event_type='site_reviewed'
-- keeps these columns in sync without requiring every write path to
-- remember the pattern.

BEGIN;

ALTER TABLE sites
    ADD COLUMN IF NOT EXISTS last_partner_reviewed_at TIMESTAMPTZ;

ALTER TABLE sites
    ADD COLUMN IF NOT EXISTS last_partner_reviewed_by TEXT;

-- partner_activity_log trigger to keep the denormalized columns fresh.
-- Event type 'site_reviewed' is the one we care about; skip all others.
CREATE OR REPLACE FUNCTION update_site_partner_review_attribution()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.event_type = 'site_reviewed' AND NEW.site_id IS NOT NULL THEN
        UPDATE sites
        SET last_partner_reviewed_at = NEW.created_at,
            last_partner_reviewed_by = COALESCE(NEW.actor_email, NEW.actor_name, NEW.actor_email)
        WHERE site_id = NEW.site_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Only install if partner_activity_log table exists (it should; Session 203)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'partner_activity_log') THEN
        DROP TRIGGER IF EXISTS trg_site_partner_review_attrib ON partner_activity_log;
        CREATE TRIGGER trg_site_partner_review_attrib
            AFTER INSERT ON partner_activity_log
            FOR EACH ROW EXECUTE FUNCTION update_site_partner_review_attribution();
    END IF;
END $$;

COMMIT;

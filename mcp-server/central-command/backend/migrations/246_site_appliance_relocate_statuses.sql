-- Migration 246 — Session 210-B 2026-04-25 hotfix
--
-- Migration 245 added relocate-flow code that sets
-- site_appliances.status='relocating' (during a move) and
-- site_appliances.status='relocated' (after the finalize sweep
-- completes the move). Both values violate the existing
-- site_appliances_status_check CHECK constraint, which only allowed
-- {pending, online, offline, auth_failed, decommissioned}.
--
-- Discovered when issuing the first reprovision fleet_order — the
-- source-row UPDATE failed with CheckViolationError. Endpoint code at
-- e50ed7f5/4aa28b1a would have failed the same way on first call.
--
-- Extending the allowed set with the two new transition states. They
-- are handled by:
--   * 'relocating' — set by sites.py::relocate_appliance during the
--     pending window (RT-3). Daemon's checkin handler must keep the
--     row queryable while the move is in-flight.
--   * 'relocated'  — set by finalize_pending_relocations() (M245)
--     after target_site.last_checkin > initiated_at. Soft-deleted
--     row stays for audit but no longer counts as a live appliance.

BEGIN;

ALTER TABLE site_appliances
    DROP CONSTRAINT IF EXISTS site_appliances_status_check;

ALTER TABLE site_appliances
    ADD CONSTRAINT site_appliances_status_check
    CHECK (
        status::text = ANY (ARRAY[
            'pending'::varchar,
            'online'::varchar,
            'offline'::varchar,
            'auth_failed'::varchar,
            'decommissioned'::varchar,
            'relocating'::varchar,
            'relocated'::varchar
        ]::text[])
    );

COMMIT;

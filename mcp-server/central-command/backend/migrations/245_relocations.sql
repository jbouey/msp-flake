-- Migration 245 — Session 210-B 2026-04-25 round-table follow-up
--
-- Track every appliance relocation from initiation through completion.
-- Closes the gaps surfaced in the round-table on the relocate endpoint:
--
--   RT-3: Source soft-delete must defer until target checkin confirms.
--         A relocations row in 'pending' state is the live tracker.
--   RT-4: Substrate `relocation_stalled` invariant queries this table
--         to detect daemons that never picked up their reprovision order.
--   RT-7: Customer evidence-chain entry references this row's id so the
--         compliance_bundles audit trail is queryable.
--
-- The table is APPEND-ONLY at the row level (UPDATEs flip status only);
-- combined with admin_audit_log (Migration 151 trigger), every move
-- is reconstructible from two independent sources.

BEGIN;

CREATE TABLE IF NOT EXISTS relocations (
    id BIGSERIAL PRIMARY KEY,

    -- Source state at relocation time (frozen — these point at the
    -- pre-move appliance_id which may be soft-deleted by completion time).
    source_appliance_id VARCHAR(50) NOT NULL,
    source_site_id VARCHAR(50) NOT NULL,

    -- Target state — these point at the new (site_id, appliance_id) pair.
    target_appliance_id VARCHAR(50) NOT NULL,
    target_site_id VARCHAR(50) NOT NULL,

    -- Identity that follows the appliance regardless of site_id.
    mac_address VARCHAR(17) NOT NULL,

    -- 'pending'   — relocate endpoint fired, daemon hasn't completed
    -- 'completed' — target site_appliances.last_checkin > initiated_at;
    --               source soft-deleted, source api_keys deactivated
    -- 'expired'   — daemon never picked up within 30 min; substrate fires
    -- 'failed'    — explicit failure (currently unused; reserved for
    --               daemon-reported reprovision failures in v0.4.12+)
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','completed','expired','failed')),

    -- Free-form audit context (≥20 chars enforced by relocate endpoint).
    reason TEXT NOT NULL,

    -- Operator name (admin or partner) who initiated the move.
    actor TEXT NOT NULL,

    -- Optional pointer to the fleet_order issued for the daemon-side
    -- step. NULL when v0.4.10 path used ssh_snippet (no order issued).
    fleet_order_id VARCHAR(50),

    -- Optional pointer to the compliance_bundles row written for the
    -- customer evidence chain. NULL until the bundle commits.
    evidence_bundle_id VARCHAR(64),

    initiated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,

    -- Lock the source MAC. A second pending relocation for the same
    -- MAC would race with the first; refuse it at the schema layer.
    CONSTRAINT relocations_one_pending_per_mac UNIQUE (mac_address, status)
        DEFERRABLE INITIALLY IMMEDIATE
);

-- Lookup paths that the substrate invariant + cleanup loop need.
CREATE INDEX IF NOT EXISTS idx_relocations_pending
    ON relocations (initiated_at)
    WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_relocations_target
    ON relocations (target_appliance_id, target_site_id);
CREATE INDEX IF NOT EXISTS idx_relocations_mac
    ON relocations (mac_address);

-- Append-only at the row level: trigger blocks DELETE.
-- (Status flips via UPDATE are allowed; row removal is not.)
CREATE OR REPLACE FUNCTION prevent_relocations_delete()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'relocations is append-only; rows cannot be deleted';
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_relocations_delete ON relocations;
CREATE TRIGGER trg_prevent_relocations_delete
    BEFORE DELETE ON relocations
    FOR EACH ROW EXECUTE FUNCTION prevent_relocations_delete();

-- ---------------------------------------------------------------------
-- finalize_pending_relocations — sweep called from background loop.
-- For every 'pending' relocation:
--   * If target.last_checkin > initiated_at → mark 'completed', soft-
--     delete the source site_appliances row, deactivate any leftover
--     active api_keys for the source.
--   * If initiated_at < NOW() - INTERVAL '30 minutes' → mark 'expired'
--     (daemon never picked up; operator must investigate). Source
--     site_appliances row stays as 'relocating' so the substrate
--     invariant keeps firing.
--
-- Returns counts: (completed_count, expired_count). The caller logs
-- these only when nonzero, so the data_hygiene_gc_loop stays quiet
-- under steady state.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION finalize_pending_relocations()
RETURNS TABLE (completed_count INTEGER, expired_count INTEGER)
LANGUAGE plpgsql
AS $$
DECLARE
    n_completed INTEGER := 0;
    n_expired   INTEGER := 0;
BEGIN
    -- Step 1: mark completed where target has checked in.
    WITH winners AS (
        SELECT r.id, r.source_appliance_id, r.source_site_id
          FROM relocations r
          JOIN site_appliances target
            ON target.appliance_id = r.target_appliance_id
           AND target.site_id      = r.target_site_id
         WHERE r.status = 'pending'
           AND target.last_checkin IS NOT NULL
           AND target.last_checkin > r.initiated_at
    ),
    upd AS (
        UPDATE relocations
           SET status = 'completed', completed_at = NOW()
         WHERE id IN (SELECT id FROM winners)
        RETURNING id, source_appliance_id, source_site_id
    ),
    del_src AS (
        UPDATE site_appliances
           SET deleted_at = NOW(),
               deleted_by = 'relocate.finalize',
               status = 'relocated'
         WHERE (appliance_id, site_id) IN (
                SELECT source_appliance_id, source_site_id FROM upd
           )
           AND deleted_at IS NULL
        RETURNING appliance_id, site_id
    ),
    deact_keys AS (
        UPDATE api_keys
           SET active = false
         WHERE (site_id, appliance_id) IN (
                SELECT site_id, appliance_id FROM del_src
           )
           AND active = true
        RETURNING 1
    )
    SELECT COUNT(*) INTO n_completed FROM upd;

    -- Step 2: mark expired where the 30-min window passed.
    UPDATE relocations
       SET status = 'expired', completed_at = NOW()
     WHERE status = 'pending'
       AND initiated_at < NOW() - INTERVAL '30 minutes';
    GET DIAGNOSTICS n_expired = ROW_COUNT;

    completed_count := n_completed;
    expired_count   := n_expired;
    RETURN NEXT;
END $$;

COMMIT;

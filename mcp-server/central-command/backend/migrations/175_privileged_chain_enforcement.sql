-- Migration 175: Unbreakable chain-of-custody for privileged orders
--
-- Session 205 user directive (non-negotiable architectural principle):
--
--   "client identity → policy approval → execution → attestation
--    must be an unbroken chain"
--   "make that chain impossible to break anywhere in the system"
--
-- This migration enforces the chain at the database layer. Even if a
-- future developer, a compromised admin endpoint, or a misbehaving
-- CLI tool attempts to INSERT a privileged fleet_orders row WITHOUT
-- a linked attestation_bundle_id that points at an actual
-- compliance_bundles row for the same site, the INSERT is REJECTED.
--
-- Belt + suspenders + glued-to-the-floor:
--   - fleet_cli.py enforces at CLI level (human-friendly errors)
--   - privileged_access_api.py enforces at API level (request queue)
--   - THIS migration enforces at the DB level (physical impossibility)
--
-- No code path can emit a privileged fleet order without a live
-- attestation bundle. The chain cannot be skipped, backdated, or
-- decoupled from the event it authorizes.

BEGIN;

CREATE OR REPLACE FUNCTION enforce_privileged_order_attestation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_privileged_types TEXT[] := ARRAY[
        'enable_emergency_access',
        'disable_emergency_access',
        'bulk_remediation',
        'signing_key_rotation'
    ];
    v_bundle_id TEXT;
    v_site_id TEXT;
    v_bundle_exists BOOLEAN;
BEGIN
    -- Only gate privileged types. Everything else passes unchanged.
    IF NOT (NEW.order_type = ANY(v_privileged_types)) THEN
        RETURN NEW;
    END IF;

    -- Extract the bundle reference + site from the order parameters
    v_bundle_id := NEW.parameters->>'attestation_bundle_id';
    v_site_id := NEW.parameters->>'site_id';

    IF v_bundle_id IS NULL OR length(v_bundle_id) = 0 THEN
        RAISE EXCEPTION
            'PRIVILEGED_CHAIN_VIOLATION: order_type %% requires '
            'parameters->>attestation_bundle_id (Session 205 Phase 14). '
            'Issue via fleet_cli --actor-email / --reason, or via the '
            '/api/partners/me/privileged-access request flow.',
            NEW.order_type
        USING HINT = 'An attestation bundle must be written BEFORE the '
                     'fleet order. See docs/security/emergency-access-policy.md';
    END IF;

    IF v_site_id IS NULL OR length(v_site_id) = 0 THEN
        RAISE EXCEPTION
            'PRIVILEGED_CHAIN_VIOLATION: order_type %% requires '
            'parameters->>site_id to match the attestation bundle site.',
            NEW.order_type;
    END IF;

    -- Verify the bundle actually exists and was written for THIS site
    -- under check_type='privileged_access' (WORM, chain-linked, signed).
    SELECT EXISTS (
        SELECT 1 FROM compliance_bundles
         WHERE bundle_id = v_bundle_id
           AND site_id = v_site_id
           AND check_type = 'privileged_access'
    ) INTO v_bundle_exists;

    IF NOT v_bundle_exists THEN
        RAISE EXCEPTION
            'PRIVILEGED_CHAIN_VIOLATION: attestation_bundle_id %% not '
            'found for site %% (order_type %%). The chain-of-custody '
            'requires an attestation bundle to exist for the same site '
            'before the fleet order is accepted.',
            v_bundle_id, v_site_id, NEW.order_type;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_enforce_privileged_chain ON fleet_orders;
CREATE TRIGGER trg_enforce_privileged_chain
    BEFORE INSERT ON fleet_orders
    FOR EACH ROW
    EXECUTE FUNCTION enforce_privileged_order_attestation();

-- Supporting index so the trigger's EXISTS probe is indexed (bundle_id
-- is already the unique key; this just narrows to privileged_access
-- bundles per site).
CREATE INDEX IF NOT EXISTS idx_compliance_bundles_priv
    ON compliance_bundles (bundle_id, site_id)
    WHERE check_type = 'privileged_access';

COMMIT;

-- ── DOCUMENTATION ──────────────────────────────────────────────────
--
-- To add a NEW privileged order type:
--   1. Add the string to v_privileged_types above (requires migration)
--   2. Add the string to fleet_cli.PRIVILEGED_ORDER_TYPES
--   3. Add the string to privileged_access_attestation.ALLOWED_EVENTS
--
-- All three MUST be updated together. CI should include a grep-check
-- that the three lists are identical. If they drift, the chain has
-- a gap, and the gap = a security vulnerability.

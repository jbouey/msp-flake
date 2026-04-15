-- Migration 218: extend v_privileged_types with the 6 watchdog_* types
--
-- Keeps the three-list lockstep per CLAUDE.md:
--   fleet_cli.PRIVILEGED_ORDER_TYPES           — client-side gate
--   privileged_access_attestation.ALLOWED_EVENTS — attestation catalog
--   migration 175 v_privileged_types           — DB trigger gate (here)
--
-- The scripts/check_privileged_chain_lockstep.py CI check asserts all
-- three contain the exact same set. Adding watchdog_* types requires
-- the DB-side update to match — this migration redefines both:
--   - enforce_privileged_order_attestation() (INSERT gate, migration 175)
--   - enforce_privileged_order_immutability() (UPDATE gate, migration 176)
--
-- The existing bodies are preserved byte-for-byte except for the
-- v_privileged_types array. Matching only the array changes keeps the
-- diff surgical and makes rollback a one-line revert.

BEGIN;

CREATE OR REPLACE FUNCTION enforce_privileged_order_attestation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_privileged_types TEXT[] := ARRAY[
        'enable_emergency_access',
        'disable_emergency_access',
        'bulk_remediation',
        'signing_key_rotation',
        'watchdog_restart_daemon',
        'watchdog_refetch_config',
        'watchdog_reset_pin_store',
        'watchdog_reset_api_key',
        'watchdog_redeploy_daemon',
        'watchdog_collect_diagnostics'
    ];
    v_bundle_id TEXT;
    v_site_id TEXT;
    v_bundle_exists BOOLEAN;
BEGIN
    IF NOT (NEW.order_type = ANY(v_privileged_types)) THEN
        RETURN NEW;
    END IF;

    v_bundle_id := NEW.parameters->>'attestation_bundle_id';
    v_site_id := NEW.parameters->>'site_id';

    IF v_bundle_id IS NULL OR length(v_bundle_id) = 0 THEN
        RAISE EXCEPTION
            'PRIVILEGED_CHAIN_VIOLATION: order_type % requires '
            'parameters->>attestation_bundle_id (Session 205 Phase 14). '
            'Issue via fleet_cli --actor-email / --reason, or via the '
            '/api/partners/me/privileged-access request flow.',
            NEW.order_type
        USING HINT = 'An attestation bundle must be written BEFORE the '
                     'fleet order. See docs/security/emergency-access-policy.md';
    END IF;

    IF v_site_id IS NULL OR length(v_site_id) = 0 THEN
        RAISE EXCEPTION
            'PRIVILEGED_CHAIN_VIOLATION: order_type % requires '
            'parameters->>site_id to match the attestation bundle site.',
            NEW.order_type;
    END IF;

    SELECT EXISTS (
        SELECT 1 FROM compliance_bundles
         WHERE bundle_id = v_bundle_id
           AND site_id = v_site_id
           AND check_type = 'privileged_access'
    ) INTO v_bundle_exists;

    IF NOT v_bundle_exists THEN
        RAISE EXCEPTION
            'PRIVILEGED_CHAIN_VIOLATION: attestation_bundle_id % not '
            'found for site % (order_type %). The chain-of-custody '
            'requires an attestation bundle to exist for the same site '
            'before the fleet order is accepted.',
            v_bundle_id, v_site_id, NEW.order_type;
    END IF;

    RETURN NEW;
END;
$$;

-- UPDATE guard — must mirror the INSERT guard's array exactly.
CREATE OR REPLACE FUNCTION enforce_privileged_order_immutability()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_privileged_types TEXT[] := ARRAY[
        'enable_emergency_access',
        'disable_emergency_access',
        'bulk_remediation',
        'signing_key_rotation',
        'watchdog_restart_daemon',
        'watchdog_refetch_config',
        'watchdog_reset_pin_store',
        'watchdog_reset_api_key',
        'watchdog_redeploy_daemon',
        'watchdog_collect_diagnostics'
    ];
    v_was_privileged  BOOLEAN := OLD.order_type = ANY(v_privileged_types);
    v_is_privileged   BOOLEAN := NEW.order_type = ANY(v_privileged_types);
BEGIN
    IF v_was_privileged <> v_is_privileged THEN
        RAISE EXCEPTION
            'PRIVILEGED_CHAIN_VIOLATION: order_type cannot be UPDATEd '
            'into or out of the privileged set (was %, now %). '
            'Create a new order with the correct type.',
            OLD.order_type, NEW.order_type;
    END IF;

    IF NOT v_was_privileged THEN
        RETURN NEW;
    END IF;

    IF COALESCE(NEW.parameters->>'attestation_bundle_id', '') IS DISTINCT FROM
       COALESCE(OLD.parameters->>'attestation_bundle_id', '') THEN
        RAISE EXCEPTION
            'PRIVILEGED_CHAIN_VIOLATION: attestation_bundle_id is '
            'immutable on privileged fleet_orders rows. '
            'old=% new=%',
            OLD.parameters->>'attestation_bundle_id',
            NEW.parameters->>'attestation_bundle_id';
    END IF;

    IF COALESCE(NEW.parameters->>'site_id', '') IS DISTINCT FROM
       COALESCE(OLD.parameters->>'site_id', '') THEN
        RAISE EXCEPTION
            'PRIVILEGED_CHAIN_VIOLATION: site_id is immutable on '
            'privileged fleet_orders rows. old=% new=%',
            OLD.parameters->>'site_id',
            NEW.parameters->>'site_id';
    END IF;

    RETURN NEW;
END;
$$;

COMMIT;

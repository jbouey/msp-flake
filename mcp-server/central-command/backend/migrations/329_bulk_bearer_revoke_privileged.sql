-- Migration 329: add `bulk_bearer_revoke` to v_privileged_types
--
-- #123 Sub-A closure per audit/coach-123-batch-bearer-revocation-
-- gate-a-2026-05-17.md (4-list lockstep P0-1).
--
-- bulk_bearer_revoke is the multi-device-fleet (>= 2 appliances per
-- site, target a subset by appliance_ids[]) batch primitive for
-- revoking authentication bearers. Issued via NEW admin endpoint
-- POST /api/admin/sites/{site_id}/appliances/revoke-bearers (Sub-B,
-- follows). Each invocation flips site_appliances.bearer_revoked=
-- TRUE for the requested subset PLUS api_keys.active=FALSE in the
-- same admin_transaction.
--
-- Why privileged-chain class:
--   - revocation IS a §164.308(a)(4) workforce-access action when
--     bearer compromise is suspected; the chain-of-custody record
--     IS the evidence the operator acted within policy bounds
--   - downstream daemon is left needing re-provisioning (operator
--     issues `watchdog_reset_api_key` per appliance as the recovery
--     path — separate privileged event with its own attestation)
--   - one attested bundle covers N appliances at one site (1-bundle:
--     N-orders shape per #118 fan-out precedent)
--
-- ADDITIVE-ONLY (Session 220 #4 lock-in): the v_privileged_types
-- ARRAY in BOTH functions extends from mig 305's 12 entries to 13
-- by appending 'bulk_bearer_revoke'. ALL prior function body
-- (PRIVILEGED_CHAIN_VIOLATION prefix + USING HINT + site_id cross-
-- bundle check + immutability checks) preserved VERBATIM from mig
-- 305. Per the additive-only rule, NEVER rewrite the body from
-- scratch — only append the new array entry.
--
-- Three-list lockstep (Python+Go) enforced by CI:
--   - fleet_cli.PRIVILEGED_ORDER_TYPES (Python)
--   - privileged_access_attestation.ALLOWED_EVENTS (Python)
--   - mig 329 v_privileged_types (this file — DB trigger)
--   - appliance/internal/orders/processor.go dangerousOrderTypes (Go)
-- Both `scripts/check_privileged_chain_lockstep.py` + `tests/
-- test_privileged_order_four_list_lockstep.py` updated in the same
-- commit.

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
        'watchdog_collect_diagnostics',
        'enable_recovery_shell_24h',
        'delegate_signing_key',
        'bulk_bearer_revoke'
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
        'watchdog_collect_diagnostics',
        'enable_recovery_shell_24h',
        'delegate_signing_key',
        'bulk_bearer_revoke'
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
            'immutable on privileged fleet_orders rows. old=% new=%',
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

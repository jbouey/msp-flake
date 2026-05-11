-- Migration 305: add `delegate_signing_key` to v_privileged_types
--
-- Session 219 (2026-05-11) — weekly audit cadence found that
-- POST /api/appliances/{appliance_id}/delegate-key (appliance_delegation.py:258)
-- was zero-auth. The handler issues an Ed25519 keypair the appliance uses
-- to sign evidence + audit-trail entries; that signing material then feeds
-- the customer-facing attestation chain.  Functionally equivalent to
-- `signing_key_rotation` which is already privileged.
--
-- Gate A on the hardening sprint (audit/coach-zero-auth-hardening-gate-a-
-- 2026-05-11.md) P0-1 mandated three-list registration BEFORE the auth
-- flip lands.  This migration completes the third list; the first two
-- (fleet_cli.PRIVILEGED_ORDER_TYPES + privileged_access_attestation.
-- ALLOWED_EVENTS) were updated in the same commit.
--
-- ADDITIVE-ONLY: mig 223's function bodies are preserved verbatim;
-- the ONLY change is appending 'delegate_signing_key' to v_privileged_types
-- in BOTH functions. Gate B (audit/coach-zero-auth-hardening-commit1-
-- gate-b-2026-05-11.md) BLOCKED an earlier draft that silently dropped
-- the site_id cross-bundle check + PRIVILEGED_CHAIN_VIOLATION error prefix
-- + HINT clause. This rewrite preserves all of them.
--
-- Three-list lockstep enforced by CI:
-- scripts/check_privileged_chain_lockstep.py.

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
        'delegate_signing_key'
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
        'delegate_signing_key'
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

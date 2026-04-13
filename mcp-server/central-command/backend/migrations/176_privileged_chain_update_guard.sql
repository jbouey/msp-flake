-- Migration 176: UPDATE guard on privileged fleet_orders (Phase 14 reinforcement)
--
-- Migration 175 catches INSERTs of privileged orders without an
-- attestation linkage. But an attacker with DB write could:
--   INSERT a benign order, then UPDATE it to a privileged order_type,
--   OR UPDATE parameters to strip attestation_bundle_id post-insert.
--
-- This migration adds a second trigger that REJECTS any UPDATE to a
-- privileged fleet_orders row that:
--   - changes order_type in/out of the privileged set
--   - removes or alters parameters->>'attestation_bundle_id'
--   - removes or alters parameters->>'site_id'
--
-- Intent: the chain is immutable once written. You can CANCEL an order
-- by UPDATE status='cancelled', but you cannot mutate its privilege-
-- critical metadata.

BEGIN;

CREATE OR REPLACE FUNCTION enforce_privileged_order_immutability()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_privileged_types TEXT[] := ARRAY[
        'enable_emergency_access',
        'disable_emergency_access',
        'bulk_remediation',
        'signing_key_rotation'
    ];
    v_was_privileged  BOOLEAN := OLD.order_type = ANY(v_privileged_types);
    v_is_privileged   BOOLEAN := NEW.order_type = ANY(v_privileged_types);
BEGIN
    -- Order type cannot cross the privileged boundary by UPDATE.
    IF v_was_privileged <> v_is_privileged THEN
        RAISE EXCEPTION
            'PRIVILEGED_CHAIN_VIOLATION: order_type cannot be UPDATEd '
            'into or out of the privileged set (was %, now %). '
            'Create a new order with the correct type.',
            OLD.order_type, NEW.order_type;
    END IF;

    IF NOT v_was_privileged THEN
        RETURN NEW;  -- non-privileged orders are not guarded here
    END IF;

    -- Attestation linkage + site_id must remain stable on privileged rows.
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
            'privileged fleet_orders rows.';
    END IF;

    -- Signature + signed_payload + nonce are the cryptographic core.
    -- They may only change if status is moving to 'cancelled' and we
    -- are nulling them (preserving the immutable view is conservative;
    -- keep them as-written).
    IF NEW.signed_payload IS DISTINCT FROM OLD.signed_payload
       OR NEW.signature IS DISTINCT FROM OLD.signature
       OR NEW.nonce IS DISTINCT FROM OLD.nonce THEN
        RAISE EXCEPTION
            'PRIVILEGED_CHAIN_VIOLATION: signed_payload/signature/nonce '
            'are immutable on privileged fleet_orders rows.';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_enforce_privileged_immutability ON fleet_orders;
CREATE TRIGGER trg_enforce_privileged_immutability
    BEFORE UPDATE ON fleet_orders
    FOR EACH ROW
    EXECUTE FUNCTION enforce_privileged_order_immutability();

COMMIT;

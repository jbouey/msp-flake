-- Migration 321: baa_signatures.client_org_id FK column
--
-- Task #93 v2 Option E (Counsel Rule 6 structural fix). Closes the
-- email-rename orphan class by making the BAA-status join key the
-- client_org_id, not LOWER(email). Pairs with the client_signup.py
-- sign_baa-endpoint refactor that pre-generates the org UUID before
-- writing baa_signatures, so the FK is set at INSERT time.
--
-- TRIGGER COMPAT: `prevent_baa_signature_modification` (mig 224) is
-- BEFORE UPDATE OR DELETE FOR EACH ROW. The backfill UPDATE below
-- requires per-tx trigger disable (mig 304 quarantine precedent).
-- Counsel position (audit/coach-93-baa-signatures-client-org-id-fk-
-- gate-a-2026-05-15.md §Maya): schema evolution is metadata, not
-- record modification under §164.316(b)(2)(i). admin_audit_log row
-- attests the operation.
--
-- v2 NOTE (audit/coach-93-v2-signup-flow-reorder-gate-a-2026-05-15.md):
-- v1 plan tried to backfill from the existing baa_signatures.email
-- join, but the SINGLE writer (client_signup.py:299) ran BEFORE
-- client_orgs materialization minutes-to-hours later via the Stripe
-- webhook. Option E pre-generates UUID Python-side at /signup/sign-baa
-- and inserts client_orgs (status='pending_provisioning') + baa_
-- signatures in the same admin_transaction. This mig adds the column;
-- the code refactor lands in the same commit so the next signup
-- writes a valid FK at INSERT time.
--
-- PROD SCAN 2026-05-15 21:00 UTC: baa_signatures has 1 row, synthetic
-- adversarial-test data, already orphaned (no matching client_orgs.
-- primary_email). Quarantined inline before ADD COLUMN. Zero real
-- customer exposure.

BEGIN;

-- 1. Pre-mig quarantine of synthetic orphan row (if present).
--    Constrained to email LIKE 'adversarial+%@example.com' — never
--    matches a real customer (example.com is RFC2606-reserved).
ALTER TABLE baa_signatures DISABLE TRIGGER trg_baa_no_update;

DO $$
DECLARE
    v_synthetic_count INT;
BEGIN
    SELECT COUNT(*) INTO v_synthetic_count
      FROM baa_signatures
     WHERE email LIKE 'adversarial+%@example.com';

    IF v_synthetic_count > 0 THEN
        DELETE FROM baa_signatures
         WHERE email LIKE 'adversarial+%@example.com';

        INSERT INTO admin_audit_log
            (user_id, username, action, target, details, ip_address)
        VALUES (
            NULL,
            'system',
            'baa_signatures_synthetic_quarantine',
            'baa_signatures',
            jsonb_build_object(
                'migration', '321_baa_signatures_client_org_id_fk',
                'reason', 'Pre-FK migration. Synthetic adversarial-test '
                         'row pattern (email LIKE adversarial+%@example.com) '
                         'never a real customer, already orphaned per LOWER(email) '
                         'scan against client_orgs. Counsel position: §164.316(b)(2)(i) '
                         'retention scope excludes example.com test data. See '
                         'audit/coach-93-v2-signup-flow-reorder-gate-a-2026-05-15.md.',
                'rows_quarantined', v_synthetic_count,
                'gate_a_artifact', 'audit/coach-93-v2-signup-flow-reorder-gate-a-2026-05-15.md'
            ),
            NULL
        );
    END IF;
END
$$;

-- 2. ADD COLUMN nullable first (no DEFAULT — value is row-dependent).
ALTER TABLE baa_signatures
    ADD COLUMN IF NOT EXISTS client_org_id UUID NULL;

-- 3. Backfill from client_orgs.primary_email via LOWER() join.
--    Zero real rows in prod today after step 1 → expected no-op.
--    Retained for forward-compatibility with any non-prod environment
--    that has legacy formal-BAA rows from before the v2 writer flow.
UPDATE baa_signatures bs
   SET client_org_id = co.id
  FROM client_orgs co
 WHERE LOWER(bs.email) = LOWER(co.primary_email)
   AND bs.client_org_id IS NULL;

-- 4. Verify zero orphans remain.
DO $$
DECLARE
    v_orphans INT;
BEGIN
    SELECT COUNT(*) INTO v_orphans
      FROM baa_signatures
     WHERE client_org_id IS NULL;

    IF v_orphans > 0 THEN
        RAISE EXCEPTION
            'mig 321 abort: % baa_signatures rows orphaned after backfill. '
            'Investigate before applying NOT NULL.', v_orphans;
    END IF;
END
$$;

-- 5. Lock the column NOT NULL + FK.
ALTER TABLE baa_signatures
    ALTER COLUMN client_org_id SET NOT NULL;

ALTER TABLE baa_signatures
    ADD CONSTRAINT baa_signatures_client_org_id_fk
    FOREIGN KEY (client_org_id) REFERENCES client_orgs(id) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_baa_signatures_client_org_id
    ON baa_signatures (client_org_id);

CREATE INDEX IF NOT EXISTS idx_baa_signatures_client_org_id_formal
    ON baa_signatures (client_org_id)
    WHERE is_acknowledgment_only = FALSE;

-- 6. signup_sessions.client_org_id — populated at /signup/sign-baa
--    time after the same-txn client_orgs INSERT, so the Stripe webhook's
--    _materialize_self_serve_tenant can be idempotent on it. Nullable
--    because rows created at /signup/start (before BAA sign) don't have
--    a client_org_id yet.
ALTER TABLE signup_sessions
    ADD COLUMN IF NOT EXISTS client_org_id UUID NULL;

CREATE INDEX IF NOT EXISTS idx_signup_sessions_client_org_id
    ON signup_sessions (client_org_id)
    WHERE client_org_id IS NOT NULL;

-- 7. Re-enable the append-only trigger BEFORE COMMIT.
ALTER TABLE baa_signatures ENABLE TRIGGER trg_baa_no_update;

-- 8. Audit-trail row.
INSERT INTO admin_audit_log
    (user_id, username, action, target, details, ip_address)
VALUES (
    NULL,
    'system',
    'baa_signatures_schema_update',
    'baa_signatures.client_org_id',
    jsonb_build_object(
        'migration', '321_baa_signatures_client_org_id_fk',
        'reason', 'Structural fix for email-rename orphan class. '
                 'Counsel Rule 6 (machine-enforce BAA state). '
                 'baa_status helpers migrate to client_org_id join in '
                 'follow-up commit after 24h soak.',
        'task', '#93',
        'gate_a_artifact', 'audit/coach-93-v2-signup-flow-reorder-gate-a-2026-05-15.md',
        'siblings', jsonb_build_array('#91', '#94', '#95')
    ),
    NULL
);

COMMIT;

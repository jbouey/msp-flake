-- Migration 322: client_orgs_status_ck CHECK constraint
--
-- Task #97 (deferred from Task #93 Gate B P1-1). Defensive belt for the
-- client_orgs.status column. Currently there is NO CHECK constraint
-- (verified prod via `\d+ client_orgs` 2026-05-15); any string value
-- under VARCHAR(50) would be accepted, including typos. This adds the
-- explicit allowlist.
--
-- Value-set discovery (2026-05-15):
--   - 'active'               — primary state. Set by org_management.py
--                              admin-create (line 160), client_signup.py
--                              webhook (post-BAA), reprovision path
--                              (org_management.py line 591).
--   - 'pending'              — legacy pre-BAA path in client_signup.py
--                              _materialize_self_serve_tenant (line 756).
--   - 'pending_provisioning' — Task #93 v2 sign_baa atomic-FK path
--                              (client_signup.py line 281+).
--   - 'deprovisioned'        — deprovision flow (org_management.py
--                              line 295).
--
-- Prod scan: SELECT status, count(*) FROM client_orgs → 2 rows, both
-- 'active'. The constraint cannot reject any existing row.
--
-- v2 NOTE: if a future status value is added to the code, this migration
-- must be amended in lockstep (same commit). The ALTER inside the
-- migration is idempotent via DROP-IF-EXISTS → ADD, so adding a value
-- means: bump the migration number, drop the old constraint, add the
-- new one with the expanded set. Counsel Rule 4 (no orphan coverage)
-- — any state value the platform writes MUST be enumerated here.

BEGIN;

-- Idempotent: drop the constraint if it exists, then recreate with the
-- current authoritative set. Future migrations follow the same shape
-- to extend the value-set without leaving stale constraints behind.
ALTER TABLE client_orgs
    DROP CONSTRAINT IF EXISTS client_orgs_status_ck;

ALTER TABLE client_orgs
    ADD CONSTRAINT client_orgs_status_ck
    CHECK (status IN (
        'active',
        'pending',
        'pending_provisioning',
        'deprovisioned'
    ));

-- Audit-trail row.
INSERT INTO admin_audit_log
    (user_id, username, action, target, details, ip_address)
VALUES (
    NULL,
    'system',
    'client_orgs_status_check_constraint',
    'client_orgs.status',
    jsonb_build_object(
        'migration', '322_client_orgs_status_ck',
        'reason', 'Defensive belt — defers from Task #93 Gate B P1-1. '
                 'No CHECK existed pre-mig; this adds the explicit allowlist '
                 'of 4 status values (active, pending, pending_provisioning, '
                 'deprovisioned) used by all client_orgs writers.',
        'task', '#97',
        'gate_a_artifact', 'audit/coach-93-v2-commit1-gate-b-2026-05-15.md',
        'value_set', jsonb_build_array(
            'active', 'pending', 'pending_provisioning', 'deprovisioned'
        )
    ),
    NULL
);

COMMIT;

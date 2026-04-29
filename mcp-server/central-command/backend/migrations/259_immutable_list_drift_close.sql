-- Migration 259: close the immutable-list drift surfaced by mig 258's
-- `rename_site_immutable_list_drift` substrate invariant (Session 213
-- F4-followup, sev2).
--
-- BACKGROUND:
--   Migration 257 introduced rename_site() + _rename_site_immutable_tables().
--   Migration 258 added a sev2 substrate invariant that detects tables
--   with `site_id` column AND a DELETE-blocking trigger AND NOT in the
--   immutable list — i.e. tables that are operationally append-only
--   yet rename_site() would happily rewrite their site_id.
--
--   On first deploy (2026-04-29) the invariant surfaced 7 candidates.
--   Per-table inspection of each trigger function source confirmed
--   every one is intentionally append-only for HIPAA / attestation /
--   identity-chain reasons. This migration extends
--   `_rename_site_immutable_tables()` to include all 7. Net effect:
--   the substrate invariant clears (returns 0 rows), and any future
--   rename_site() call is provably safe against these tables.
--
-- TABLES BEING ADDED:
--
--   appliance_heartbeats          (mig 121 partitioned heartbeat ledger;
--                                   trigger: prevent_heartbeat_deletion;
--                                   "Use partition detach + archive instead")
--
--   consent_request_tokens        (consent chain integrity;
--                                   trigger: prevent_audit_deletion;
--                                   "attestation integrity requires immutable records").
--                                   Note: tokens are time-bounded (72h TTL) but the
--                                   LINEAGE persists for auditors per mig 189; operational
--                                   state lives on `consumed_at`, not `site_id`, so making
--                                   it immutable does NOT break the consent flow.
--
--   integration_audit_log         (integration audit trail;
--                                   trigger: prevent_audit_modification;
--                                   "Audit log is append-only").
--                                   Note: `site_id` here is UUID-typed, not TEXT. A
--                                   rename_site() UPDATE would have type-errored at
--                                   runtime anyway — the immutable-list entry is the
--                                   correct belt-and-suspenders fix. Pre-mig consequence
--                                   was bounded (rename would fail-fast, not corrupt).
--
--   liveness_claims               (mig 206 reconcile identity chain;
--                                   trigger: prevent_liveness_claim_deletion;
--                                   "claim ledger invariant")
--
--   promotion_audit_log_recovery  (mig 253 Session 212 P0 — flywheel
--                                   audit DLQ;
--                                   trigger: enforce_promotion_audit_log_recovery_integrity;
--                                   "INSERT-only — DELETE blocked")
--
--   provisioning_claim_events     (mig 210 identity chain ledger;
--                                   trigger: prevent_claim_event_deletion;
--                                   "the identity chain is immutable by design")
--
--   watchdog_events               (watchdog attestation chain;
--                                   trigger: prevent_audit_deletion;
--                                   "attestation integrity requires immutable records")
--
-- This migration replaces the function body in lockstep — the function
-- is IMMUTABLE so changing the row set requires CREATE OR REPLACE.
-- The tests in test_rename_site_function.py
-- (test_rename_site_skips_immutable_tables) already require these
-- tables to be present; without this migration those assertions would
-- FAIL after a subsequent test refresh.

CREATE OR REPLACE FUNCTION _rename_site_immutable_tables()
RETURNS TABLE(table_name TEXT, reason TEXT)
LANGUAGE sql
IMMUTABLE
AS $$
    VALUES
        -- Parent identity row. PK update cascades unpredictably; mapping
        -- carries the alias (Session 213 F4 round-table P0-2).
        ('sites',                  'PK row — site_id is the canonical identity; alias via site_canonical_mapping instead'),
        -- Cryptographic / evidence
        ('compliance_bundles',     'Ed25519-signed + OTS-anchored — site_id is part of cryptographic binding'),
        ('compliance_packets',     'Monthly compliance attestations — HIPAA §164.316(b)(2)(i) 6-year retention'),
        ('compliance_attestations','Adversarial attestation chain — site_id is part of provenance'),
        ('compliance_scores',      'Compliance score history — auditor evidence'),
        ('evidence_bundles',       'Legacy evidence table — bound to issuing site_id'),
        ('audit_packages',         'Auditor evidence packages — site_id is part of package identity'),
        ('ots_proofs',             'OTS proofs bound to bundle_hash chain — downstream of compliance_bundles'),
        ('baa_signatures',         'BAA e-sign records (mig 224) — HIPAA §164.316(b)(2)(i) append-only'),
        -- Audit-class tables (§164.316(b)(2)(i) retention)
        ('appliance_audit_trail',  'Audit-class table — §164.316(b)(2)(i) retention'),
        ('journal_upload_events',  'Audit-class table — §164.316(b)(2)(i) retention'),
        ('client_audit_log',       'HIPAA §164.528 disclosure accounting — append-only'),
        ('admin_audit_log',        'Privileged-access audit trail — append-only'),
        ('partner_activity_log',   'Partner-side audit trail — append-only'),
        ('promotion_audit_log',    'Flywheel promotion chain-of-custody — append-only'),
        ('portal_access_log',      'Audit-class partitioned table (mig 138) — DELETE-blocked'),
        ('incident_remediation_steps', 'Audit-class remediation history (mig 137) — DELETE-blocked'),
        ('fleet_order_completions','Order completion ACKs — chain-of-custody for privileged orders via attestation_bundle_id'),
        ('sigauth_observations',   'Sigauth verification audit (Session 212) — append-only'),
        ('promoted_rule_events',   'Flywheel ledger (Session 209 mig 181) — partitioned, append-only'),
        ('reconcile_events',       'Time-travel reconciliation (mig 160) — append-only + RLS'),
        -- Mig 259 additions — drift surfaced by sev2 invariant on first deploy
        ('appliance_heartbeats',   'Partitioned heartbeat ledger (mig 121) — append-only; partition-detach + archive is the only mutation path'),
        ('consent_request_tokens', 'Consent chain integrity — magic-link consent attestation; trigger uses prevent_audit_deletion'),
        ('integration_audit_log',  'Integration audit trail — prevent_audit_modification; append-only by design'),
        ('liveness_claims',        'Liveness claim ledger (mig 206 reconcile) — claim chain invariant'),
        ('promotion_audit_log_recovery', 'Flywheel audit DLQ (mig 253, Session 212 P0) — INSERT-only, recovery-state-only UPDATEs'),
        ('provisioning_claim_events', 'Provisioning identity chain (mig 210) — append-only ledger, immutable by design'),
        ('watchdog_events',        'Watchdog attestation chain — attestation integrity requires immutable records'),
        -- Self-referential
        ('site_canonical_mapping', 'The mapping table itself — recursive rename would break canonical_site_id()'),
        ('relocations',            'Append-only relocate tracker (mig 245) — DELETE-blocked');
$$;

COMMENT ON FUNCTION _rename_site_immutable_tables() IS
    'Hard-coded list of tables whose site_id MUST NEVER be rewritten. Cryptographically-bound (compliance_bundles, evidence) or HIPAA §164.316(b)(2)(i) retention (audit logs) or attestation/identity chains (appliance_heartbeats, claim ledgers, watchdog events). Adding to this list is a privileged decision — review with the round-table. Substrate invariant rename_site_immutable_list_drift auto-surfaces tables that should be in this list but aren''t.';

-- Audit log entry for the drift close.
INSERT INTO admin_audit_log (action, target, username, details, created_at)
SELECT
    'substrate.rename_site_immutable_list.expanded',
    'system',
    'jbouey2006@gmail.com',
    jsonb_build_object(
        'migration', '259',
        'tables_added', ARRAY[
            'appliance_heartbeats',
            'consent_request_tokens',
            'integration_audit_log',
            'liveness_claims',
            'promotion_audit_log_recovery',
            'provisioning_claim_events',
            'watchdog_events'
        ],
        'invariant_that_fired', 'rename_site_immutable_list_drift',
        'session', '213',
        'related_findings', ARRAY['F4-followup'],
        'reason', 'sev2 invariant from mig 258 surfaced 7 tables with site_id + DELETE-blocking trigger that were not in _rename_site_immutable_tables(). Per-table inspection confirmed all 7 are intentionally append-only (HIPAA / attestation / identity-chain). Adding to the immutable list closes the drift; substrate invariant should clear on next 60s tick.'
    ),
    NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM admin_audit_log
     WHERE action = 'substrate.rename_site_immutable_list.expanded'
       AND target = 'system'
);

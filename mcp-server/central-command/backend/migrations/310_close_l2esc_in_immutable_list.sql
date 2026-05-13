-- Migration 310: add `l2_escalations_missed` to `_rename_site_immutable_tables()`.
-- Carol P1 from coach enterprise-readiness audit 2026-05-12:
--   audit/coach-enterprise-readiness-2026-05-12.md
--
-- Substrate sev2 invariant `rename_site_immutable_list_drift` fired 60s
-- after the ff7be044 deploy. Mig 308 created `l2_escalations_missed`
-- with DELETE/UPDATE-blocking triggers (operationally append-only by
-- design — Maya P0-C "parallel disclosure table" verdict), but the
-- `_rename_site_immutable_tables()` SQL function list wasn't updated
-- in lockstep. `rename_site()` would have happily rewritten the table's
-- site_id values — a chain-of-custody risk for the customer-facing
-- disclosure surface.
--
-- Substrate engine surfaced what the Gate A + Gate B + meta-audit forks
-- all missed. The QC-engine narrative validated; the gap closes here.
--
-- Per CLAUDE.md "function bodies are ADDITIVE-ONLY" rule (Session 220
-- lock-in): the prior function body from mig 294 is copied VERBATIM
-- and only the new `l2_escalations_missed` row is appended. NO
-- reformatting, no rewording, no inline cleanup. The lockstep checker
-- proves LIST parity but NOT body parity; review must verify by diff.

CREATE OR REPLACE FUNCTION _rename_site_immutable_tables()
RETURNS TABLE(table_name TEXT, reason TEXT)
LANGUAGE sql
IMMUTABLE
AS $$
    VALUES
        -- Parent identity row
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
        -- Mig 259 additions
        ('appliance_heartbeats',   'Partitioned heartbeat ledger (mig 121) — append-only; partition-detach + archive is the only mutation path'),
        ('consent_request_tokens', 'Consent chain integrity — magic-link consent attestation; trigger uses prevent_audit_deletion'),
        ('integration_audit_log',  'Integration audit trail — prevent_audit_modification; append-only by design'),
        ('liveness_claims',        'Liveness claim ledger (mig 206 reconcile) — claim chain invariant'),
        ('promotion_audit_log_recovery', 'Flywheel audit DLQ (mig 253, Session 212 P0) — INSERT-only, recovery-state-only UPDATEs'),
        ('provisioning_claim_events', 'Provisioning identity chain (mig 210) — append-only ledger, immutable by design'),
        ('watchdog_events',        'Watchdog attestation chain — attestation integrity requires immutable records'),
        -- Mig 263 addition
        ('go_agent_status_events', 'Workstation agent state-transition history (mig 263) — append-only forensic chain for fleet-edge liveness'),
        -- Mig 294 addition — cross-org relocate state machine
        ('cross_org_site_relocate_requests', 'Cross-org relocate state machine (mig 281) — chain-of-custody for cross-organization moves; site_id binds to original org for §164.504(e) disclosure-accounting integrity'),
        -- Mig 310 addition — BUG 2 / P1-persistence disclosure surface (this migration)
        ('l2_escalations_missed',  'BUG 2 P1-persistence-drift disclosure table (mig 308) — INSERT-only by trigger; site_id binds to historically-missed L2 escalations per Maya P0-C Option B parallel-disclosure verdict (audit/maya-p0c-backfill-decision-2026-05-12.md). rename_site() rewriting these site_ids would break the customer-facing disclosure surface + auditor-kit disclosures/missed_l2_escalations.json mapping'),
        -- Self-referential
        ('site_canonical_mapping', 'The mapping table itself — recursive rename would break canonical_site_id()'),
        ('relocations',            'Append-only relocate tracker (mig 245) — DELETE-blocked');
$$;

-- Audit log entry capturing the substrate-engine catch + Carol verdict.
INSERT INTO admin_audit_log (action, target, username, details, created_at)
VALUES (
    'migration_310_immutable_list_close_l2_escalations_missed',
    '_rename_site_immutable_tables',
    'jeff',
    jsonb_build_object(
        'migration', '310',
        'reason', 'Substrate sev2 invariant rename_site_immutable_list_drift surfaced l2_escalations_missed as drift after mig 308 created it with DELETE/UPDATE-blocking triggers. Coach enterprise-readiness audit 2026-05-12 Carol P1. Substrate engine caught what human review missed.',
        'audit_ref', 'audit/coach-enterprise-readiness-2026-05-12.md',
        'maya_disclosure_ref', 'audit/maya-p0c-backfill-decision-2026-05-12.md',
        'pattern', 'ADDITIVE-ONLY function-body rewrite per Session 220 lock-in: prior mig 294 body copied verbatim + one row appended'
    ),
    NOW()
);

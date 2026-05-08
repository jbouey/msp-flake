-- ============================================================================
-- Migration 294: Add `cross_org_site_relocate_requests` to the rename-immutable
--                list, closing the substrate sev2 invariant
--                `rename_site_immutable_list_drift` that has been alerting
--                continuously for 66+ hours.
--
-- BACKGROUND
--   The substrate integrity engine (mig 207) runs every 60s and checks
--   that every table with both (a) a `site_id` column AND (b) a
--   DELETE-blocking trigger appears in `_rename_site_immutable_tables()`.
--   The list is what `rename_site()` reads to refuse to mutate the table
--   on a site_id rename — append-only / cryptographic / audit-class
--   tables MUST stay out of the rename path because their `site_id` is
--   part of an immutable provenance chain.
--
--   Migration 281 created the cross-org relocate state-machine table.
--   It has `site_id NOT NULL` + a DELETE-blocking trigger
--   (immutable by design — chain-of-custody for cross-org moves) but
--   was missed from the immutable list. The substrate engine caught
--   the drift on the next 60s tick.
--
--   The audit caught this 2026-05-08 (F-P1-2). 4-voice round-table
--   approved 4/4. This is the 1-line fix.
--
-- IMPACT IF UNFIXED
--   A `rename_site()` call against a site mid-relocate would silently
--   rewrite the cross-org chain's `site_id` columns — the chain is
--   bound to the original site_id forever (per the canonical_site_id
--   exclusion rule — see CLAUDE.md, "compliance_bundles" carve-out).
--   Result: a corrupted chain-of-custody for the cross-org event.
--   No customer trigger today (flag is enabled=false, dual-admin gated,
--   counsel-blocked) but the bypass-path detector exists for exactly
--   this class.
-- ============================================================================

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
        -- Mig 294 addition — cross-org relocate state machine (this migration)
        ('cross_org_site_relocate_requests', 'Cross-org relocate state machine (mig 281) — chain-of-custody for cross-organization moves; site_id binds to original org for §164.504(e) disclosure-accounting integrity'),
        -- Self-referential
        ('site_canonical_mapping', 'The mapping table itself — recursive rename would break canonical_site_id()'),
        ('relocations',            'Append-only relocate tracker (mig 245) — DELETE-blocked');
$$;

-- Audit log entry capturing the round-table decision + auditor reference.
INSERT INTO admin_audit_log (action, target, username, details, created_at)
VALUES (
    'migration_294_immutable_list_close_cross_org_relocate',
    '_rename_site_immutable_tables',
    'jeff',
    jsonb_build_object(
        'migration', '294',
        'reason', 'Substrate sev2 invariant rename_site_immutable_list_drift surfaced cross_org_site_relocate_requests as drift after mig 281 created it. Round-table 2026-05-08 approved 4/4 (Carol/Sarah/Steve/Maya). Closes the alert that has been firing continuously for 66+ hours.',
        'audit_ref', 'audit/coach-e2e-attestation-audit-2026-05-08.md F-P1-2',
        'roundtable_ref', 'audit/round-table-verdict-2026-05-08.md RT-2.2'
    ),
    NOW()
);

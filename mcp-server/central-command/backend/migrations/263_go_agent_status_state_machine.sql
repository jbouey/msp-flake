-- Migration 263: Fleet-edge liveness — go_agents server-side state
-- machine + append-only transition history (Session 214 round-table
-- consensus 2026-04-30).
--
-- DOCTRINE GAP CLOSED:
--   Today: `go_agents.status='connected'` is gRPC-stream-write-only
--   and never decays. Empirically verified 2026-04-30: 4 chaos-lab
--   workstation agents showed `status='connected'` 7+ days after
--   their host (iMac) was powered off. The dashboard claimed all
--   agents healthy while reality was four dark boxes. **This
--   violates Session 207's substrate-integrity-engine doctrine:
--   "if our dashboard says X, X is true, cryptographically."**
--
-- WHAT THIS MIGRATION SHIPS:
--
-- 1. `go_agents.last_status_transition_at TIMESTAMPTZ` — when the
--    most recent state transition fired. Required so the state-
--    machine loop can avoid spurious re-writes (UPDATE only when
--    transitioning to a NEW status).
--
-- 2. `go_agent_status_events` — append-only audit table. Every
--    state transition writes one row. DELETE/UPDATE blocked at the
--    trigger level (audit-class). Enables operator forensic queries
--    like "when did NVWS01 first go stale" and "how many transitions
--    in last 24h" (alerting on flapping).
--
-- 3. Adds `go_agent_status_events` to `_rename_site_immutable_tables()`
--    via the standard pattern (the table has site_id + DELETE-block
--    trigger, would otherwise trip `rename_site_immutable_list_drift`
--    sev2 on next 60s tick).
--
-- DELIBERATELY OUT OF SCOPE (per round-table consensus):
--   * `partner_settings.stale_agent_scoring_mode` toggle —
--     compensation policy is operator-class, separate PR.
--   * Email digest / webhook to MSP — substrate-to-operator
--     notification routing requires its own round-table on partner
--     contact and digest scope.
--   * Direct clinic notification — never; that's BA territory.

ALTER TABLE go_agents
    ADD COLUMN IF NOT EXISTS last_status_transition_at TIMESTAMPTZ;

COMMENT ON COLUMN go_agents.last_status_transition_at IS
    'When the most recent state transition fired (set by go_agent_status_decay_loop). Mig 263, Session 214 round-table doctrine fix.';

CREATE TABLE IF NOT EXISTS go_agent_status_events (
    id            BIGSERIAL PRIMARY KEY,
    agent_id      VARCHAR(100) NOT NULL,
    site_id       VARCHAR(100) NOT NULL,
    from_status   VARCHAR(20),  -- NULL = initial registration
    to_status     VARCHAR(20) NOT NULL,
    last_heartbeat_at TIMESTAMPTZ,
    transitioned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reason        TEXT NOT NULL DEFAULT 'heartbeat_age_decay',
    CONSTRAINT go_agent_status_events_to_status_valid
        CHECK (to_status IN ('connected', 'stale', 'disconnected', 'dead', 'pending'))
);

CREATE INDEX IF NOT EXISTS idx_gase_recent
    ON go_agent_status_events (transitioned_at DESC);
CREATE INDEX IF NOT EXISTS idx_gase_agent
    ON go_agent_status_events (agent_id, transitioned_at DESC);

COMMENT ON TABLE go_agent_status_events IS
    'Append-only history of go_agents.status transitions. Substrate writes one row per state change via go_agent_status_decay_loop. DELETE/UPDATE blocked. Operators query for: forensic "when did NVWS01 first go stale", flapping detection (>3 transitions in 24h), recovery timing.';

-- Append-only enforcement (mirrors mig 151 prevent_audit_deletion()
-- + mig 256 prevent_site_canonical_mapping_modification()).
CREATE OR REPLACE FUNCTION prevent_go_agent_status_events_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'go_agent_status_events is append-only — % blocked. Audit invariant: HIPAA §164.316(b)(2)(i). Status transitions are forensic evidence; mutations would invalidate the operator-visibility chain.', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_block_go_agent_status_events_mutation
    ON go_agent_status_events;
CREATE TRIGGER trg_block_go_agent_status_events_mutation
BEFORE DELETE OR UPDATE ON go_agent_status_events
FOR EACH ROW EXECUTE FUNCTION prevent_go_agent_status_events_modification();

-- Add to immutable list so `rename_site` can never touch it. Also
-- prevents `rename_site_immutable_list_drift` sev2 from firing on
-- the next 60s tick (the table has site_id + DELETE-block trigger,
-- which is exactly the drift class the invariant catches).
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
        -- Mig 263 addition — fleet-edge liveness state-transition history
        ('go_agent_status_events', 'Workstation agent state-transition history (mig 263) — append-only forensic chain for fleet-edge liveness'),
        -- Self-referential
        ('site_canonical_mapping', 'The mapping table itself — recursive rename would break canonical_site_id()'),
        ('relocations',            'Append-only relocate tracker (mig 245) — DELETE-blocked');
$$;

-- Audit log entry.
INSERT INTO admin_audit_log (action, target, username, details, created_at)
SELECT
    'substrate.go_agent_state_machine.created',
    'system',
    'jbouey2006@gmail.com',
    jsonb_build_object(
        'migration', '263',
        'columns_added', ARRAY['go_agents.last_status_transition_at'],
        'tables_created', ARRAY['go_agent_status_events'],
        'triggers_created', ARRAY['trg_block_go_agent_status_events_mutation'],
        'immutable_list_addition', 'go_agent_status_events',
        'session', '214',
        'related_findings', ARRAY['fleet-edge-liveness'],
        'round_table_verdict', 'SHIP — 4-piece slice (state machine + 2 invariants + dashboard pane)',
        'doctrine_violated_before_fix', 'Session 207 substrate-integrity-engine: dashboard claimed status=connected for boxes dark 7+ days',
        'reason', 'Server-side state machine for go_agents.status. Background loop go_agent_status_decay_loop (in main.py) decays connected → stale (>5min) → disconnected (>30min) → dead (>24h). Every transition writes a go_agent_status_events row. Doctrine-fix: the dashboard now matches reality.'
    ),
    NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM admin_audit_log
     WHERE action = 'substrate.go_agent_state_machine.created'
       AND target = 'system'
);

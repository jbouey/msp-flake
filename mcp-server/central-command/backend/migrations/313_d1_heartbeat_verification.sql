-- Migration 313: D1 backend heartbeat-signature verification — schema scaffold
--
-- Counsel Rule 4 (orphan coverage at multi-device-enterprise fleet scale).
-- Task #40 reframed. Class-B 7-lens Gate A APPROVE-WITH-FIXES + 7-lens
-- protocol round-table UNANIMOUS APPROVE on hybrid path (option c).
--
-- BACKGROUND: the daemon-side heartbeat signing IS already implemented
-- (`appliance/internal/daemon/phonehome.go:827 SystemInfoSigned()` +
-- `daemon.go:867 runCheckin()`). The backend stored the signature in
-- `appliance_heartbeats.agent_signature` (mig 197) but never verified it.
--
-- This migration adds the schema scaffold for backend verification:
--   - `appliance_heartbeats.signature_valid BOOLEAN NULL`
--   - `appliance_heartbeats.signature_verified_at TIMESTAMPTZ NULL`
--   - `appliance_heartbeats.signature_canonical_format TEXT NULL`
--   - `appliance_heartbeats.signature_timestamp_unix BIGINT NULL`
--   - `site_appliances.previous_agent_public_key TEXT NULL`
--   - `site_appliances.previous_agent_public_key_retired_at TIMESTAMPTZ NULL`
--
-- The verifier itself (`signature_auth.py::verify_heartbeat_signature`)
-- and the soft-verify-at-insert hook in `sites.py:4205-4230` land in
-- separate commits.
--
-- HYBRID PROTOCOL (per round-table 2026-05-13):
--   Path A (v1a-daemon): daemon-supplied `heartbeat_timestamp` in request
--     body; backend reconstructs canonical payload using that timestamp.
--     Requires daemon v0.5.0+ with the new `heartbeat_timestamp` JSON field.
--   Path B (v1b-reconstruct): backend reconstructs canonical payload using
--     server NOW() and tries ±60s window of integer timestamps; signature
--     passes if ANY timestamp in window verifies. Backward-compat with
--     existing daemons (v0.4.x).
--
-- `signature_canonical_format` records which path was used per heartbeat
-- ('v1a-daemon' OR 'v1b-reconstruct' OR NULL when signature absent).
-- Substrate invariant `daemon_on_legacy_path_b` (sev3-info → sev2 on
-- deprecation clock) catches daemons stuck on path B past the deprecation
-- deadline.
--
-- The canonical payload format itself is daemon-defined and stays in
-- lockstep across 4 surfaces (daemon `phonehome.go:837`, backend verifier,
-- substrate runbook, auditor kit verify.sh). The CI gate
-- `test_canonical_heartbeat_format_lockstep.py` lands in a separate
-- commit and verifies all 4 surfaces share the same format string.

BEGIN;

-- ── appliance_heartbeats: per-heartbeat verification state ───────────

ALTER TABLE appliance_heartbeats
    ADD COLUMN IF NOT EXISTS signature_valid BOOLEAN NULL;
COMMENT ON COLUMN appliance_heartbeats.signature_valid IS
    'Result of backend verification: TRUE = signature verified against '
    'appliance''s agent_public_key; FALSE = signature present but did '
    'not verify (potential compromise); NULL = no signature attempted '
    'OR appliance has no agent_public_key on file.';

ALTER TABLE appliance_heartbeats
    ADD COLUMN IF NOT EXISTS signature_verified_at TIMESTAMPTZ NULL;
COMMENT ON COLUMN appliance_heartbeats.signature_verified_at IS
    'Wall-clock timestamp of the backend verification attempt. NULL if '
    'verification was not attempted.';

ALTER TABLE appliance_heartbeats
    ADD COLUMN IF NOT EXISTS signature_canonical_format TEXT NULL;
COMMENT ON COLUMN appliance_heartbeats.signature_canonical_format IS
    'Which canonical-format path verified the signature: '
    '''v1a-daemon'' = daemon-supplied heartbeat_timestamp used (path A); '
    '''v1b-reconstruct'' = backend reconstructed timestamp window (path B, '
    'legacy compat); NULL = signature absent or verification not attempted. '
    'Substrate invariant daemon_on_legacy_path_b watches for ''v1b-reconstruct'' '
    'past the deprecation clock.';

ALTER TABLE appliance_heartbeats
    ADD COLUMN IF NOT EXISTS signature_timestamp_unix BIGINT NULL;
COMMENT ON COLUMN appliance_heartbeats.signature_timestamp_unix IS
    'The Unix-epoch integer the daemon (or backend, under path B) used '
    'when constructing the canonical payload that was signed. Auditor-kit '
    'surface required for independent verification.';

-- Index supporting the substrate invariants daemon_heartbeat_unsigned
-- (sev2) + daemon_heartbeat_signature_invalid (sev1) + daemon_on_legacy_path_b
-- (sev3-info). Each invariant queries appliance_heartbeats by site_id +
-- recency + signature state.
-- IMPORTANT: NO NOW()-based WHERE predicate — non-IMMUTABLE predicates in
-- partial-index WHERE clauses are a documented outage class (2026-05-09
-- lesson, feedback_three_outage_classes_2026_05_09.md). The substrate
-- invariant queries themselves filter `observed_at > NOW() - INTERVAL
-- '24 hours'` (path-fast); the index supports both the recency-bounded
-- and the historical scans without re-creating across time.
CREATE INDEX IF NOT EXISTS idx_appliance_heartbeats_signature_state
    ON appliance_heartbeats (site_id, observed_at DESC, signature_valid);

-- ── site_appliances: rotation-grace columns ──────────────────────────

ALTER TABLE site_appliances
    ADD COLUMN IF NOT EXISTS previous_agent_public_key TEXT NULL;
COMMENT ON COLUMN site_appliances.previous_agent_public_key IS
    'Previous agent_public_key value, retained during a 15-minute '
    'rotation grace window. Verifier tries current key first, then '
    'previous within grace. Prevents sev1 alert storm on key rotation.';

ALTER TABLE site_appliances
    ADD COLUMN IF NOT EXISTS previous_agent_public_key_retired_at TIMESTAMPTZ NULL;
COMMENT ON COLUMN site_appliances.previous_agent_public_key_retired_at IS
    'Wall-clock timestamp at which previous_agent_public_key was retired '
    '(i.e. agent_public_key was rotated). Grace window expires 15 minutes '
    'after this timestamp.';

-- ── Audit trail row documenting the migration ───────────────────────

INSERT INTO admin_audit_log (
    user_id, username, action, target, details, ip_address
) VALUES (
    NULL,
    'system',
    'd1_heartbeat_verification_schema',
    'appliance_heartbeats + site_appliances',
    jsonb_build_object(
        'migration', '313_d1_heartbeat_verification',
        'rule', 'Counsel Rule 4 (orphan coverage at multi-device-enterprise scale)',
        'task', '#40 D1 daemon-side Ed25519 heartbeat signing (reframed: backend verification + substrate invariants)',
        'gate_a_artifact', 'audit/coach-d1-backend-verify-gate-a-2026-05-13.md',
        'protocol_rt_artifact', 'audit/coach-d1-heartbeat-timestamp-protocol-2026-05-13.md',
        'protocol_chosen', 'option_c_hybrid',
        'sibling_parity_precedent', 'signature_auth.py:71 MAX_CLOCK_SKEW=60s with X-Appliance-Timestamp for sigauth',
        'auditor_kit_version_bump_required', '2.1 -> 2.2 across 4 surfaces (lockstep)',
        'effective_date', '2026-05-13'
    ),
    NULL
);

COMMIT;

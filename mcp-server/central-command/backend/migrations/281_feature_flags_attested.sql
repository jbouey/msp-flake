-- Migration 281: feature_flags table — attestation-gated toggles
--
-- Round-table 21 (2026-05-05) Patricia ask. Feature flags that gate
-- privileged-class actions (cross-org relocate, future similar) MUST
-- NOT be env-var-controlled — an env var can be flipped silently by
-- anyone with deploy access, with no cryptographic audit. Instead the
-- flag is a DB row, and TOGGLING it is itself a privileged action
-- requiring named-actor + ≥40-char reason + Ed25519 attestation
-- bundle (matching the enable_emergency_access pattern, Session 205).
--
-- Why ≥40 chars instead of the usual ≥20: flag flips are RARE +
-- HIGH-IMPACT events (typically tied to a legal opinion or a major
-- release). The slightly-higher friction is intentional. The
-- attestation chain reason field is where the legal-opinion identifier
-- belongs ("Outside-counsel BAA review opinion 2026-XX-XX, doc-ID
-- ABC123: cross-org relocate covered under substrate-class BAA…").

CREATE TABLE IF NOT EXISTS feature_flags (
    flag_name TEXT PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT false,

    -- Set on enable; cleared on disable.
    enabled_at TIMESTAMP WITH TIME ZONE,
    enabled_by_email TEXT,
    enable_reason TEXT,
    enable_attestation_bundle_id TEXT,

    -- Set on disable; cleared on re-enable.
    disabled_at TIMESTAMP WITH TIME ZONE,
    disabled_by_email TEXT,
    disable_reason TEXT,
    disable_attestation_bundle_id TEXT,

    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    -- Defense in depth: an enabled flag MUST carry the three enable
    -- audit fields. A code path that flips enabled=true without
    -- populating actor + reason + timestamp fails the CHECK.
    --
    -- enable_reason ≥40 chars: deliberately higher friction than the
    -- general privileged-action ≥20-char floor. Flag flips are RARE +
    -- HIGH-IMPACT events typically tied to a legal opinion or major
    -- release; the ≥40-char field is where the legal-opinion identifier
    -- belongs. The asymmetry is intentional, NOT a parity bug.
    --
    -- attestation_bundle_id columns are KEPT but NOT CHECK-required:
    -- privileged_access bundles FK to sites(site_id), and a feature-
    -- flag flip has no natural site anchor (Marcus RT21 Gate 2). The
    -- flag's audit trail lives in this row itself (append-only via
    -- DELETE trigger) + admin_audit_log. If a future event_type IS
    -- site-anchored (e.g. per-site feature flags), it can write a
    -- privileged_access bundle and store the UUID here.
    CHECK (
        enabled = false
        OR (
            enabled_by_email IS NOT NULL
            AND enable_reason IS NOT NULL
            AND length(enable_reason) >= 40
            AND enabled_at IS NOT NULL
        )
    )
);

-- Audit-class: DELETE blocked. Disabling a flag is a state transition,
-- not a row removal — the row is the cryptographic record of "this
-- flag was enabled at time T by actor X with reason Y; later disabled
-- at time T2 by actor X2 with reason Y2." Deleting it would lose the
-- enable history.
CREATE OR REPLACE FUNCTION prevent_feature_flag_deletion()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'feature_flags is append-only audit-class. DELETE blocked. '
        'Use enabled=false (with disable attestation) instead.';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_feature_flag_deletion ON feature_flags;
CREATE TRIGGER trg_prevent_feature_flag_deletion
    BEFORE DELETE ON feature_flags
    FOR EACH ROW EXECUTE FUNCTION prevent_feature_flag_deletion();

-- Seed the cross_org_site_relocate flag in the disabled state. The
-- endpoint module reads this row at every request; if the flag isn't
-- present OR enabled=false, the endpoint returns 503 with the
-- "Feature pending outside-counsel BAA review" message.
INSERT INTO feature_flags (flag_name, enabled)
VALUES ('cross_org_site_relocate', false)
ON CONFLICT (flag_name) DO NOTHING;

COMMENT ON TABLE feature_flags IS
    'RT21 Patricia (2026-05-05): attestation-gated toggle table. Flag '
    'flips are themselves privileged-class actions requiring named '
    'actor + ≥40-char reason + Ed25519 attestation bundle. Match '
    'enable_emergency_access pattern (Session 205). DELETE blocked.';

-- Migration 279: cross_org_site_relocate_requests + delete-block trigger
--
-- Round-table 21 (2026-05-05 design + 2026-05-05 ship-A++ adversarial
-- consensus). Three-actor state machine for moving a site from one
-- client_org to another:
--
--   pending_source_release   →   source-org owner clicks magic-link
--   pending_target_accept    →   target-org owner clicks magic-link
--   pending_admin_execute    →   24h cooling-off window after target-accept
--   completed                →   Osiris admin pulls trigger; sites.client_org_id
--                                flipped + sites.prior_client_org_id set
--   canceled                 →   any of the 3 actors canceled
--   expired                  →   expires_at passed without progression
--
-- Each transition writes a privileged-access attestation bundle (Ed25519 +
-- hash-chained + OTS-anchored). Six event_types added to ALLOWED_EVENTS:
--   cross_org_site_relocate_initiated
--   cross_org_site_relocate_source_released
--   cross_org_site_relocate_target_accepted
--   cross_org_site_relocate_executed
--   cross_org_site_relocate_canceled
--   cross_org_site_relocate_expired
-- Plus a 7th for the feature-flag enable event:
--   enable_cross_org_site_relocate
-- (See migration 281 for the attested-flag table.)
--
-- NOT in PRIVILEGED_ORDER_TYPES or v_privileged_types — this is admin-API
-- class (DB state mutation), not a fleet_order. Same asymmetry as
-- break_glass_passphrase_retrieval, fleet_healing_global_pause, owner-
-- transfer events (Migration 273), etc. ALLOWED_EVENTS ⊇ those two; the
-- reverse is not required.

CREATE TABLE IF NOT EXISTS cross_org_site_relocate_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The site being moved. Cryptographic chain stays anchored here
    -- forever (Brian Option A from RT21 design). Auditor walks the
    -- chain across the org boundary via sites.prior_client_org_id
    -- (Migration 280) + the canonical alias mechanism.
    site_id TEXT NOT NULL REFERENCES sites(site_id),

    source_org_id UUID NOT NULL REFERENCES client_orgs(id),
    target_org_id UUID NOT NULL REFERENCES client_orgs(id),

    -- Defense in depth: never let a site relocate to itself. CHECK
    -- catches a code bug before it corrupts state.
    CHECK (source_org_id <> target_org_id),

    status TEXT NOT NULL DEFAULT 'pending_source_release'
        CHECK (status IN (
            'pending_source_release',
            'pending_target_accept',
            'pending_admin_execute',
            'completed',
            'canceled',
            'expired'
        )),

    -- Initiator (Osiris admin only — Steve mit 6: partner-mediated
    -- abuse is blocked because partners can't reach this endpoint).
    initiator_email TEXT NOT NULL,
    initiator_reason TEXT NOT NULL,
    -- ≥20-char reason matches the rest of the privileged-access chain.
    -- Application enforces 20-char minimum via the API layer; the
    -- CHECK catches a bypass.
    CHECK (length(initiator_reason) >= 20),

    -- Source-org-owner approval (lights up status=pending_target_accept)
    source_release_email TEXT,
    source_release_at TIMESTAMP WITH TIME ZONE,
    source_release_reason TEXT,

    -- Target-org-owner approval (lights up status=pending_admin_execute
    -- + cooling-off countdown).
    target_accept_email TEXT,
    target_accept_at TIMESTAMP WITH TIME ZONE,
    target_accept_reason TEXT,

    -- Admin execute (lights up status=completed; triggers the actual
    -- sites.client_org_id flip).
    executor_email TEXT,
    executed_at TIMESTAMP WITH TIME ZONE,

    -- Cancel
    canceled_by_email TEXT,
    canceled_at TIMESTAMP WITH TIME ZONE,
    cancel_reason TEXT,

    -- Magic-link tokens for source-release + target-accept endpoints.
    -- Stored as SHA256 — never plaintext. Same posture as
    -- client_org_owner_transfer_requests.accept_token_hash.
    source_release_token_hash TEXT,
    target_accept_token_hash TEXT,

    -- Patricia RT21 Gate 2: attribution-pinning columns. The link is
    -- ISSUED to a SPECIFIC human at initiate time; storing their email
    -- here prevents the §164.528 attribution gap that would otherwise
    -- arise from `LIMIT 1`-style picks across multi-owner orgs. The
    -- redeemer at click-time MUST match these emails (or be a current
    -- owner of the same org — defense-in-depth for owner email rename).
    expected_source_release_email TEXT,
    expected_target_accept_email TEXT,

    -- Lifecycle bounds. Both NOT NULL so a row can never be in a
    -- "lives forever" state.
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    -- Cooling-off window after target_accept_at. Default 24h, per-org
    -- configurable via the existing transfer_cooling_off_hours
    -- mechanism (Migration 275). Either source or target can cancel
    -- during this window — Steve mit 2 backed-out window.
    --
    -- Nullable BEFORE target_accept (no cooling window meaningful yet),
    -- but CHECK below enforces NOT NULL once status reaches
    -- pending_admin_execute. Marcus VETO from RT21 Gate 1: a code path
    -- that flips status to pending_admin_execute without setting
    -- cooling_off_until would let admin execute immediately, bypassing
    -- the 24h backed-out window entirely. The CHECK is the DB-layer
    -- guard that makes the bypass impossible regardless of API code.
    cooling_off_until TIMESTAMP WITH TIME ZONE,
    CHECK (
        status NOT IN ('pending_admin_execute', 'completed')
        OR cooling_off_until IS NOT NULL
    ),

    -- Array of attestation bundle IDs — one per state transition.
    -- Auditor kit ZIP pulls this and walks the chain across the
    -- entire flow.
    attestation_bundle_ids JSONB NOT NULL DEFAULT '[]'::jsonb,

    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- Steve's friction: ONE active relocate per site at any time.
-- A site can't be in flight on two simultaneous cross-org moves.
-- Re-initiation requires canceling/expiring the existing.
CREATE UNIQUE INDEX IF NOT EXISTS idx_cross_org_relocate_one_pending_per_site
    ON cross_org_site_relocate_requests (site_id)
    WHERE status IN (
        'pending_source_release',
        'pending_target_accept',
        'pending_admin_execute'
    );

-- Triage / dashboard queries
CREATE INDEX IF NOT EXISTS idx_cross_org_relocate_status
    ON cross_org_site_relocate_requests (status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_cross_org_relocate_source_release_token
    ON cross_org_site_relocate_requests (source_release_token_hash)
    WHERE status = 'pending_source_release';

CREATE INDEX IF NOT EXISTS idx_cross_org_relocate_target_accept_token
    ON cross_org_site_relocate_requests (target_accept_token_hash)
    WHERE status = 'pending_target_accept';

-- Audit-class table: append-only. UPDATE only allowed via the
-- application-layer state machine; DELETE blocked at the DB.
CREATE OR REPLACE FUNCTION prevent_cross_org_relocate_deletion()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'cross_org_site_relocate_requests is append-only audit-class. '
        'DELETE blocked. Use status=canceled or status=expired instead.';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_cross_org_relocate_deletion
    ON cross_org_site_relocate_requests;
CREATE TRIGGER trg_prevent_cross_org_relocate_deletion
    BEFORE DELETE ON cross_org_site_relocate_requests
    FOR EACH ROW EXECUTE FUNCTION prevent_cross_org_relocate_deletion();

COMMENT ON TABLE cross_org_site_relocate_requests IS
    'RT21 (2026-05-05): three-actor state machine for moving a site '
    'from one client_org to another. Cryptographic chain stays anchored '
    'at the original site_id forever (Brian Option A). Auditor walks '
    'the chain across the org boundary via sites.prior_client_org_id + '
    'site_canonical_aliases extension (Migration 280).';

-- Migration 325: load-test chain-contention site seed + pre-seeded bearers
--
-- #117 Sub-commit B closure (per audit/coach-117-chain-contention-load-
-- gate-a-2026-05-16.md Option C). Pre-seeds the synthetic site used by
-- Sub-commits C+D's k6 contention scenario, plus a dedicated
-- `load_test_chain_contention` flag on the sites table so the load-
-- harness scaffolding can be detected at runtime without overloading
-- the existing `synthetic` flag.
--
-- Why a NEW flag (NOT sites.synthetic=TRUE):
--   The existing `no_synthetic_bundles` CHECK constraint (mig 315)
--   rejects any compliance_bundles row whose site_id starts with
--   'synthetic-' prefix. This load-test site INTENTIONALLY writes real
--   bundles (the whole point of the chain-contention test is to drive
--   the per-site advisory lock under N-way concurrent writers — these
--   bundles must hit the actual evidence_chain.py path). So we cannot
--   use 'synthetic-' prefix AND we cannot use sites.synthetic=TRUE
--   (the `load_test_marker_in_compliance_bundles` invariant uses
--   sites.synthetic=TRUE as one of its trigger conditions).
--
--   Instead: site_id='load-test-chain-contention-site' (no 'synthetic-'
--   prefix) + sites.load_test_chain_contention=TRUE + sites.synthetic
--   stays FALSE + load_test_marker_in_compliance_bundles invariant
--   carve-out literal added in companion commit.
--
-- Why 20 pre-seeded appliances + bearers (not per-run mint):
--   Per Gate A P0-2b: per-run bearer minting needs a privileged-chain
--   path (signing_key_rotation already registered in mig 305) — load
--   harness shouldn't drive privileged-chain rate limits or audit-log
--   pollution. Pre-seeded bearers stay valid for the synthetic site's
--   lifetime. They CANNOT cross-site (auth flow's _enforce_site_id
--   binds the bearer to load-test-chain-contention-site only). They
--   live in api_keys with appliance_id binding so per-appliance scoping
--   works.
--
-- Companion deliverables (same commit):
--   - assertions.py: NEW sev2 invariant load_test_chain_contention_
--     site_orphan (fires when bundles exist for this site_id OUTSIDE
--     an active load_test_runs row)
--   - assertions.py: load_test_marker_in_compliance_bundles SQL gains
--     carve-out: AND cb.site_id != 'load-test-chain-contention-site'
--   - tests/fixtures/schema/prod_columns.json: sites gains
--     load_test_chain_contention column
--   - substrate_runbooks/load_test_chain_contention_site_orphan.md
--
-- Idempotency: ALL writes use ON CONFLICT … DO NOTHING / DO UPDATE so
-- re-applying the migration is safe. Test fixtures use the same shape.
--
-- Anchor for #117 Sub-commits C (chain_lock_metrics + admin endpoint
-- + k6 scenario) and D (30min soak run).

BEGIN;

-- ── 1. New column: sites.load_test_chain_contention ──────────────
-- Default FALSE — only the load-test seed row gets TRUE. Idempotent
-- via ADD COLUMN IF NOT EXISTS.
ALTER TABLE sites
    ADD COLUMN IF NOT EXISTS load_test_chain_contention BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN sites.load_test_chain_contention IS
    'Task #117 Sub-commit B (mig 325). TRUE on the single seed row '
    '''load-test-chain-contention-site''. The load-harness substrate '
    'invariant load_test_chain_contention_site_orphan uses this flag '
    '(not sites.synthetic) so the chain-contention test can write '
    'real compliance_bundles (no_synthetic_bundles CHECK bypass).';

-- Partial index keeps the substrate-invariant probe O(1). One row
-- expected forever (the synthetic seed). Cheap.
CREATE INDEX IF NOT EXISTS idx_sites_load_test_chain_contention
    ON sites (site_id)
    WHERE load_test_chain_contention = TRUE;

-- ── 2. Seed the synthetic site row ───────────────────────────────
-- Status='inactive' (per Gate B P2-1 Sub-A correction — sites_status_
-- check accepts pending|online|offline|inactive, NEVER the disallowed
-- legacy value that mig 303 explicitly noted as invalid).
-- client_org_id NULL — the site is OPERATIONALLY orphaned (no
-- customer), matching the Counsel Rule 4 expectation that synthetic
-- infrastructure NOT pretend to be customer-owned data.
INSERT INTO sites (
    site_id, clinic_name, tier, industry,
    status, client_org_id,
    synthetic, load_test_chain_contention,
    created_at, updated_at
) VALUES (
    'load-test-chain-contention-site',
    'Chain-Contention Load Test (Synthetic)',
    'small',     -- sites_tier_check accepts small|mid|large
    'synthetic',
    'inactive',  -- sites_status_check accepts pending|online|offline|inactive
    NULL,        -- NOT a customer org; load-harness internal only
    FALSE,       -- NOT synthetic-prefix-class (need real bundles)
    TRUE,        -- THIS flag — what makes us a load-test site
    NOW(),
    NOW()
)
ON CONFLICT (site_id) DO UPDATE SET
    load_test_chain_contention = TRUE,
    updated_at = NOW();

-- ── 3. Seed 20 site_appliances rows ──────────────────────────────
-- appliance_id pattern: load-test-appliance-{00..19}. Deterministic
-- so k6 can reference them by index. agent_public_key is a per-
-- appliance Ed25519 base64 placeholder; the load-test wrapper never
-- needs to verify these because it bypasses signature checks via the
-- admin-gated /api/admin/load-test/chain-contention/submit endpoint
-- (Sub-commit C). bearer_revoked stays FALSE.
INSERT INTO site_appliances (
    appliance_id, site_id, hostname, status,
    agent_public_key,
    created_at, last_checkin
)
SELECT
    'load-test-appliance-' || LPAD(i::text, 2, '0'),
    'load-test-chain-contention-site',
    'load-test-host-' || LPAD(i::text, 2, '0'),
    'online',
    -- 32-byte Ed25519 pubkey placeholder (base64 of the deterministic
    -- string 'load-test-pubkey-NN-padding-to-32b'). Distinct per
    -- appliance.
    encode(decode(
        rpad('load-test-pubkey-' || LPAD(i::text, 2, '0') || '-pad', 32, 'X'),
        'escape'
    ), 'base64'),
    NOW(),
    NOW()
FROM generate_series(0, 19) AS i
ON CONFLICT (appliance_id) DO UPDATE SET
    site_id = EXCLUDED.site_id,
    status = EXCLUDED.status,
    last_checkin = NOW();

-- ── 4. Seed 20 api_keys rows (one per appliance) ─────────────────
-- key_hash = sha256(plaintext) where plaintext = 'load-test-bearer-NN'.
-- The k6 wrapper computes the same plaintext on the client side and
-- sends as Bearer in Authorization header. These bearers ONLY work
-- against site_id='load-test-chain-contention-site' (require_appliance_
-- bearer's _enforce_site_id check rejects cross-site use).
INSERT INTO api_keys (
    site_id, appliance_id, key_hash, key_prefix, active,
    description, created_at
)
SELECT
    'load-test-chain-contention-site',
    'load-test-appliance-' || LPAD(i::text, 2, '0'),
    -- sha256 of 'load-test-bearer-NN' for k6-deterministic auth
    encode(digest('load-test-bearer-' || LPAD(i::text, 2, '0'), 'sha256'), 'hex'),
    'load-test-bearer-' || LPAD(i::text, 2, '0') || '-prefix',
    TRUE,
    'Task #117 Sub-commit B (mig 325) — load-harness pre-seeded bearer '
        || 'for chain-contention scenario, appliance ' || LPAD(i::text, 2, '0')
        || '. Deterministic so k6 can reproduce bearer per index. '
        || 'Cross-site blocked by require_appliance_bearer enforcement.',
    NOW()
FROM generate_series(0, 19) AS i
ON CONFLICT (key_hash) DO NOTHING;

-- ── 5. Documentation rows in admin_audit_log ──────────────────────
-- Belt-and-suspenders provenance: a SQL-grep for 'load-test-chain-
-- contention-site' in admin_audit_log surfaces the mig + when it ran.
-- Non-privileged action prefix — does NOT engage mig 175 enforce-
-- privileged-order-attestation chain (seed is an idempotent infra
-- write, not a runtime authorization event).
INSERT INTO admin_audit_log (action, target, details, username, created_at)
VALUES (
    'LOAD_TEST_SEED_APPLIED',
    'load-test-chain-contention-site',
    jsonb_build_object(
        'migration', '325',
        'task', '#117 Sub-commit B',
        'seeds', jsonb_build_object(
            'sites', 1,
            'site_appliances', 20,
            'api_keys', 20
        ),
        'flag', 'sites.load_test_chain_contention',
        'rationale', 'pre-seed; per-run minting requires privileged-chain'
    ),
    'mig-325',
    NOW()
)
ON CONFLICT DO NOTHING;

COMMIT;

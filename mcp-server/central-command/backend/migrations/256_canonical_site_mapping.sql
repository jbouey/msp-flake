-- Migration 256: Canonical site_id mapping (Session 213 F1 P0).
--
-- Closes the eligibility-fragmentation class architecturally. After this
-- lands, an appliance relocate / site rename / decommission writes ONE row
-- to site_canonical_mapping; future telemetry under the dead site_id
-- aggregates under the canonical site_id automatically. No more multi-table
-- migration cascades (cf. migration 255 which had to relocate
-- execution_telemetry, incidents, l2_decisions, aggregated_pattern_stats
-- because the rename only touched site_id by hand).
--
-- DESIGN:
--
-- 1. `site_canonical_mapping` — append-only audit-class table. UNIQUE on
--    from_site_id (one canonical per orphan). DELETE/UPDATE blocked at
--    the trigger level (HIPAA §164.316(b)(2)(i) — the mapping logically
--    rewrites historical evidence keys, so the trail must be tamper-
--    evident).
--
-- 2. `canonical_site_id(text) RETURNS text` — STABLE, follows the chain
--    transitively (A→B→C resolves to C). Cycle defense via WITH RECURSIVE
--    + LIMIT 16 (any chain longer than 16 hops is malformed). Returns the
--    input unchanged when no mapping exists.
--
-- 3. The flywheel aggregator (`_flywheel_promotion_loop` Step 1 in
--    main.py) is updated in the same commit to use
--    `canonical_site_id(et.site_id)` instead of raw `et.site_id`. New
--    rows continue landing under the original site_id (no daemon change
--    required), but aggregate under canonical automatically.
--
-- INTENTIONALLY NOT YET RESOLVED IN THIS MIGRATION:
--
-- * compliance_bundles: Ed25519 + OTS bind to original site_id. The
--   mapping is for OPERATIONAL aggregation; evidence stays under its
--   issuing site_id forever. This is the same boundary migration 255
--   honored.
--
-- * incidents / l2_decisions: not yet rewritten through canonical_site_id.
--   Migration 255 already moved their rows manually. F1 follow-up will
--   add views v_canonical_incidents + v_canonical_l2_decisions and
--   migrate the read paths.
--
-- BACKFILL: include the physical-appliance-pilot-1aea78 → north-valley-
-- branch-2 mapping that migration 255 manually executed, so the chain
-- function can resolve historical references correctly.

CREATE TABLE IF NOT EXISTS site_canonical_mapping (
    id BIGSERIAL PRIMARY KEY,
    from_site_id TEXT NOT NULL UNIQUE,
    to_site_id TEXT NOT NULL,
    effective_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    related_migration TEXT,
    CONSTRAINT site_canonical_mapping_no_self
        CHECK (from_site_id != to_site_id),
    CONSTRAINT site_canonical_mapping_reason_min_length
        CHECK (length(reason) >= 20),
    -- Actor must look like an email. CLAUDE.md privileged-access rule:
    -- never 'system', 'admin', 'migration:NNN', etc. — a named human is
    -- accountable for every chain-mapping decision.
    CONSTRAINT site_canonical_mapping_actor_is_email
        CHECK (actor ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$')
);

CREATE INDEX IF NOT EXISTS idx_site_canonical_mapping_to_site
    ON site_canonical_mapping (to_site_id);

-- DELETE/UPDATE block — append-only audit-class table.
--
-- DEPARTURE FROM PRECEDENT: migrations 151 (prevent_audit_deletion) and
-- 245 (prevent_relocations_delete) only block DELETE. This migration
-- additionally blocks UPDATE because the chain function's correctness
-- depends on `to_site_id` being immutable: an UPDATE that retargets a
-- mapping silently rewrites every aggregation that ever resolved
-- through it (the function is STABLE, so cached canonicalizations
-- become inconsistent across transactions). DELETE-only would let
-- `to_site_id` drift; we forbid both ops.
--
-- Corrective action for a mistaken `to_site_id`:
--   * DO NOT delete and re-insert (blocked).
--   * DO NOT update (blocked).
--   * Instead, INSERT a new row mapping the WRONG canonical onward to
--     the right one (e.g. if A→B was correct intent but B→C was a typo
--     for B→D, the chain function will resolve A→B→C; INSERT C→D and
--     it will resolve A→B→C→D). The audit trail records both the
--     original intent and the correction.
CREATE OR REPLACE FUNCTION prevent_site_canonical_mapping_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'site_canonical_mapping is append-only — % blocked. Audit invariant: HIPAA §164.316(b)(2)(i). If a mapping is wrong, INSERT a forward-correction row (see migration 256 trigger comment for pattern).', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_block_site_canonical_mapping_delete
    ON site_canonical_mapping;
CREATE TRIGGER trg_block_site_canonical_mapping_delete
BEFORE DELETE OR UPDATE ON site_canonical_mapping
FOR EACH ROW EXECUTE FUNCTION prevent_site_canonical_mapping_modification();

-- canonical_site_id: resolves any site_id to its canonical.
-- STABLE — same input → same output within a transaction (postgres
-- planner can cache).
-- Cycle defense: depth-bounded recursion. A pathologic chain
-- A→B→C→...→A would loop forever; LIMIT 16 caps it. 16 is comfortably
-- larger than any real-world rename chain (3 is excessive; 16 leaves
-- headroom).
CREATE OR REPLACE FUNCTION canonical_site_id(p_site_id TEXT)
RETURNS TEXT
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_result TEXT;
BEGIN
    IF p_site_id IS NULL THEN
        RETURN NULL;
    END IF;

    WITH RECURSIVE chain(site_id, depth) AS (
        SELECT p_site_id, 0
      UNION ALL
        SELECT scm.to_site_id, c.depth + 1
          FROM chain c
          JOIN site_canonical_mapping scm
            ON scm.from_site_id = c.site_id
         WHERE c.depth < 16
    )
    SELECT site_id INTO v_result
      FROM chain
     ORDER BY depth DESC
     LIMIT 1;

    RETURN COALESCE(v_result, p_site_id);
END;
$$;

COMMENT ON FUNCTION canonical_site_id(TEXT) IS
    'Resolves a site_id to its canonical via site_canonical_mapping chain. Returns the input unchanged when no mapping exists. STABLE — query planner can cache within a transaction. Cycle-defended via depth limit 16. Use everywhere telemetry/operational queries aggregate by site_id; do NOT use for compliance_bundles (those bind to issuing site_id forever).';

-- ACTOR CONTRACT (CLAUDE.md §"Privileged-Access Chain of Custody"):
-- `actor` MUST be a named human email — never 'system', 'fleet-cli',
-- 'admin', or 'migration:NNN'. The chain mapping logically rewrites
-- historical evidence-aggregation keys; that's a privileged-class
-- decision and the human accountable must be on the record. The
-- migration number itself goes in `related_migration`.
--
-- Backfill below: this row is the round-table close on F1 (2026-04-29).
-- The accountable operator is the round-table owner (jbouey2006@gmail.com).
-- Session log: .agent/sessions/2026-04-29-session-213-flywheel-orphan-relocation*.md.
INSERT INTO site_canonical_mapping (
    from_site_id, to_site_id, actor, reason, related_migration
)
VALUES (
    'physical-appliance-pilot-1aea78',
    'north-valley-branch-2',
    'jbouey2006@gmail.com',
    'F1 P0 backfill from 2026-04-29 round-table. Migration 255 manually relocated execution_telemetry/incidents/l2_decisions when the appliance physically moved 2026-04-25; this mapping makes future site renames a no-op for the flywheel aggregator. Session 213.',
    '255'
)
ON CONFLICT (from_site_id) DO NOTHING;

-- Audit-log the new mapping infrastructure. `username` carries the
-- human accountable; the migration number is in details.
INSERT INTO admin_audit_log (action, target, username, details, created_at)
SELECT
    'substrate.canonical_site_mapping.created',
    'system',
    'jbouey2006@gmail.com',
    jsonb_build_object(
        'table', 'site_canonical_mapping',
        'function', 'canonical_site_id(text)',
        'migration', '256',
        'backfilled_mappings', 1,
        'related_migrations', ARRAY['255'],
        'related_findings', ARRAY['F1', 'F2', 'F3'],
        'session', '213',
        'session_log', '.agent/sessions/2026-04-29-session-213-flywheel-orphan-relocation.md',
        'reason', 'Closes eligibility-fragmentation class. Future relocates write one mapping row instead of multi-table migration cascade.'
    ),
    NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM admin_audit_log
     WHERE action = 'substrate.canonical_site_mapping.created'
       AND target = 'system'
);

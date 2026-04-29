-- Migration 257: rename_site() — centralized site-rename function (Session 213 F4 P1).
--
-- BEFORE THIS MIGRATION:
--   "Site rename is a multi-table migration" (CLAUDE.md). The rule cited
--   site_credentials, appliance_provisioning, aggregated_pattern_stats,
--   platform_pattern_stats — but it's an ad-hoc list maintained by hand,
--   and Session 213 (migrations 254 + 255) found 4 missed tables
--   (execution_telemetry, incidents, l2_decisions). Every rename has
--   risked drift bugs because the canonical lockstep was an instruction,
--   not code.
--
-- THIS MIGRATION:
--   * `rename_site(p_from_site_id, p_to_site_id, p_actor, p_reason)`
--     SQL function — the SINGLE sanctioned path for renaming a site.
--   * Auto-discovers tables with a site_id column via
--     information_schema.columns; partitions are excluded (postgres
--     routes UPDATEs through the parent automatically).
--   * INTENTIONALLY-IMMUTABLE list: cryptographically-bound and
--     compliance-retention tables MUST stay under their original
--     site_id forever (compliance_bundles + Ed25519/OTS chain;
--     compliance_packets monthly attestations; evidence_bundles legacy;
--     audit-class tables under §164.316(b)(2)(i) retention).
--   * Always writes a `site_canonical_mapping` row first (mig 256) so
--     `canonical_site_id()` resolves immediately for any straggler
--     telemetry under the old site_id.
--   * Acquires `pg_advisory_xact_lock` to serialize concurrent renames
--     for the same source site_id.
--   * Audit-logs to admin_audit_log with structured details (per-table
--     row counts, immutable-list, function version).
--   * Enforces actor=email + reason ≥20 chars at function entry (same
--     contract as site_canonical_mapping CHECKs).
--
-- COMPANION CI GATE:
--   `tests/test_no_direct_site_id_update.py` (Session 213 F4 P1) scans
--   the codebase for any line matching `UPDATE … SET site_id = …`
--   outside this function and the existing per-appliance transfer
--   endpoints (which move ONE appliance, not rename a site).
--
-- CALL CONTRACT:
--   Always inside a transaction (the function uses pg_advisory_xact_lock
--   which auto-releases at commit/rollback; SET LOCAL app.allow_multi_row
--   is also transaction-scoped).
--   Returns SETOF (table_name TEXT, rows_affected BIGINT) — one row per
--   table touched, plus one summary row for site_canonical_mapping.

-- Hard-coded list of tables that MUST NEVER have their site_id rewritten.
-- These are cryptographically bound, under HIPAA §164.316(b)(2)(i)
-- retention, OR the parent identity row whose PK update would cascade
-- through the FK graph in surprising ways.
--
-- Maintained as a stable function so callers / tests can assert against
-- it without parsing strings. Adding to this list is a privileged
-- decision — please review with the round-table.
--
-- ARCHITECTURAL NOTE (Session 213 F4 round-table): the `sites` table
-- itself is INTENTIONALLY immutable. site_id is the PK that ~80
-- operational tables FK into; UPDATEing it triggers a cascade graph
-- that's hard to reason about in a long transaction. Instead, the
-- `sites` row stays at the original site_id forever, and the
-- `site_canonical_mapping` + `canonical_site_id()` infrastructure
-- (mig 256) carries the read-side aliasing. The function name
-- `rename_site` is therefore alias-style: from-site→to-site for
-- canonical resolution, while the original sites row remains the
-- authoritative record. Compliance bundles + evidence stay bound to
-- the original site_id forever (Ed25519 + OTS).
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
        -- Self-referential
        ('site_canonical_mapping', 'The mapping table itself — recursive rename would break canonical_site_id()'),
        ('relocations',            'Append-only relocate tracker (mig 245) — DELETE-blocked');
$$;

CREATE OR REPLACE FUNCTION rename_site(
    p_from_site_id TEXT,
    p_to_site_id TEXT,
    p_actor TEXT,
    p_reason TEXT
) RETURNS TABLE(touched_table TEXT, rows_affected BIGINT)
LANGUAGE plpgsql
AS $$
DECLARE
    v_table_name TEXT;
    v_count BIGINT;
    v_details JSONB := '{}'::jsonb;
    v_immutable_set TEXT[];
    v_views_set TEXT[];
BEGIN
    -- Phase 0 — input validation.
    IF p_from_site_id IS NULL OR p_to_site_id IS NULL THEN
        RAISE EXCEPTION 'rename_site: from_site_id and to_site_id are required';
    END IF;
    IF p_from_site_id = p_to_site_id THEN
        RAISE EXCEPTION 'rename_site: from and to must differ (% = %)', p_from_site_id, p_to_site_id;
    END IF;
    IF p_actor IS NULL OR p_actor !~ '^[^@\s]+@[^@\s]+\.[^@\s]+$' THEN
        RAISE EXCEPTION 'rename_site: actor must be a named human email per CLAUDE.md privileged-access rule (got: %)', COALESCE(p_actor, 'NULL');
    END IF;
    IF p_reason IS NULL OR length(p_reason) < 20 THEN
        RAISE EXCEPTION 'rename_site: reason must be ≥20 chars (got % chars)', COALESCE(length(p_reason), 0);
    END IF;

    -- Phase 0.5 — F4 round-table P1-2: refuse if from_site_id doesn't
    -- exist as a real site. Silent no-ops on typos pollute the
    -- canonical mapping (which is append-only). The corrective pattern
    -- (forward-correction row) works but is operator-toxic for typos.
    IF NOT EXISTS (SELECT 1 FROM sites WHERE site_id = p_from_site_id) THEN
        RAISE EXCEPTION 'rename_site: from_site_id % does not exist in sites table — refusing to write a mapping that points to nothing', p_from_site_id;
    END IF;

    -- Phase 1 — guardrails for long-running rename.
    -- F4 round-table P2-9: hard timeout so a runaway lock doesn't pin
    -- the fleet. 5 minutes is comfortably > the largest envisaged
    -- rename (1000-site fleet × 80 tables) and small enough to bound
    -- worst-case impact.
    PERFORM set_config('statement_timeout', '5min', true);

    -- Phase 1 — serialize concurrent renames for this source. The lock
    -- auto-releases at commit/rollback. hashtext keeps the lock key
    -- bounded to int4 — collision probability is non-zero across
    -- thousands of renames but a collision means false-serialization
    -- (two unrelated renames lock each other), NOT data corruption.
    -- Acceptable for an admin op that runs maybe once a year per site.
    PERFORM pg_advisory_xact_lock(hashtext('rename_site:' || p_from_site_id));

    -- Phase 2 — bypass the row-guard (migration 192). Rename is a
    -- legitimate bulk op. SET LOCAL stays scoped to this transaction.
    PERFORM set_config('app.allow_multi_row', 'true', true);

    -- Phase 3 — INSERT canonical mapping FIRST. Any straggler telemetry
    -- writes that race the physical UPDATEs below will canonicalize on
    -- read via canonical_site_id() (mig 256).
    INSERT INTO site_canonical_mapping
        (from_site_id, to_site_id, actor, reason, related_migration)
    VALUES
        (p_from_site_id, p_to_site_id, p_actor, p_reason, 'rename_site_function')
    ON CONFLICT (from_site_id) DO NOTHING;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    touched_table := 'site_canonical_mapping';
    rows_affected := v_count;
    RETURN NEXT;

    -- Phase 4 — physically move site_id in every base table EXCEPT the
    -- immutable list. Auto-discovery via information_schema; partition
    -- children skipped (UPDATE on parent routes through children).
    SELECT array_agg(table_name) INTO v_immutable_set
      FROM _rename_site_immutable_tables();

    -- Track view names so we don't try to UPDATE them.
    SELECT array_agg(table_name::TEXT) INTO v_views_set
      FROM information_schema.tables
     WHERE table_schema = 'public'
       AND table_type = 'VIEW';

    FOR v_table_name IN
        SELECT c.table_name
          FROM information_schema.columns c
          JOIN information_schema.tables t
            ON t.table_name = c.table_name
           AND t.table_schema = c.table_schema
         WHERE c.column_name = 'site_id'
           AND c.table_schema = 'public'
           AND t.table_type = 'BASE TABLE'
           -- Skip partition children. pg_inherits.inhrelid is the CHILD
           -- relation OID and inhparent is the PARENT. The NOT EXISTS
           -- below means "this table never appears as a child in any
           -- inheritance row" — equivalently "this is a top-level
           -- (parent or standalone) table." Postgres routes UPDATEs
           -- through partition parents to children automatically, so
           -- the parent table_name covers every partitioned child.
           -- Stable across pg14/15/16.
           AND NOT EXISTS (
               SELECT 1 FROM pg_inherits i
                 JOIN pg_class p ON p.oid = i.inhrelid
                WHERE p.relname = c.table_name
           )
           -- Skip date-suffixed backup snapshots (e.g.
           -- appliances_backup_20260415). Tightened from broad %_backup_%
           -- (F4 round-table P2-8) so a real operational table named
           -- `device_backup_state` would NOT be silently excluded.
           AND c.table_name !~ '_backup_[0-9]{6,8}$'
         ORDER BY c.table_name
    LOOP
        -- Skip immutable
        IF v_table_name = ANY(v_immutable_set) THEN
            CONTINUE;
        END IF;
        -- Skip views (defense in depth — already filtered by table_type
        -- above but information_schema.tables is sometimes squirrely
        -- with materialized views).
        IF v_table_name = ANY(v_views_set) THEN
            CONTINUE;
        END IF;

        EXECUTE format(
            'UPDATE %I SET site_id = $1 WHERE site_id = $2',
            v_table_name
        ) USING p_to_site_id, p_from_site_id;
        GET DIAGNOSTICS v_count = ROW_COUNT;

        IF v_count > 0 THEN
            touched_table := v_table_name;
            rows_affected := v_count;
            v_details := v_details || jsonb_build_object(v_table_name, v_count);
            RETURN NEXT;
        END IF;
    END LOOP;

    -- Phase 5 — audit log. Single structured row.
    INSERT INTO admin_audit_log (action, target, username, details, created_at)
    VALUES (
        'site.rename',
        'site:' || p_from_site_id,
        p_actor,
        jsonb_build_object(
            'from_site_id', p_from_site_id,
            'to_site_id', p_to_site_id,
            'reason', p_reason,
            'function_version', 'rename_site_v1',
            'related_migration', '257',
            'tables_moved', v_details,
            'tables_intentionally_skipped', (
                SELECT array_agg(table_name)
                  FROM _rename_site_immutable_tables()
            )
        ),
        NOW()
    );

    RETURN;
END;
$$;

COMMENT ON FUNCTION rename_site(TEXT, TEXT, TEXT, TEXT) IS
    'Centralized site-rename function (Session 213 F4 P1). Alias-style: the original sites row stays at from_site_id (the PK update class is intractable across the FK graph); site_canonical_mapping + canonical_site_id() carry the read-side aliasing instead. The function INSERTs the mapping, physically moves operational telemetry rows to the new site_id (so writes after this point hit canonical naturally), and audit-logs. SKIPS the `sites` table itself + all cryptographically-bound (compliance_bundles, OTS) + audit-class (HIPAA §164.316(b)(2)(i)) tables — see _rename_site_immutable_tables(). FAIL-FAST: any UPDATE error rolls back the entire rename including the canonical mapping; partial-success state is never persisted. Validates actor=email + reason≥20chars + same-site rejection + from_site_id exists. Acquires pg_advisory_xact_lock to serialize concurrent renames; SET LOCAL statement_timeout=5min bounds worst-case lock duration. Direct UPDATE site_id elsewhere is a CI-gated regression — see tests/test_no_direct_site_id_update.py.';

COMMENT ON FUNCTION _rename_site_immutable_tables() IS
    'Hard-coded list of tables whose site_id MUST NEVER be rewritten. Cryptographically-bound (compliance_bundles, evidence) or HIPAA §164.316(b)(2)(i) retention (audit logs). Adding to this list is a privileged decision — review with the round-table.';

-- Audit-log the new function.
INSERT INTO admin_audit_log (action, target, username, details, created_at)
SELECT
    'substrate.rename_site_function.created',
    'system',
    'jbouey2006@gmail.com',
    jsonb_build_object(
        'function', 'rename_site(text,text,text,text)',
        'helper', '_rename_site_immutable_tables()',
        'migration', '257',
        'related_findings', ARRAY['F4'],
        'session', '213',
        'reason', 'Centralizes site rename behind a single SQL function. Closes the multi-table-lockstep class architecturally. CI gate test_no_direct_site_id_update.py prevents drift.'
    ),
    NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM admin_audit_log
     WHERE action = 'substrate.rename_site_function.created'
       AND target = 'system'
);

-- ============================================================================
-- Migration 298: Add `site_id` indexes to 8 tenant-bearing tables that lack
--                them. Multi-tenant Phase 0 audit P1-5 (2026-05-09).
--
-- BACKGROUND
--   The Phase 0 inventory found 8 tables with a `site_id` column but no
--   index on it. At N=1 today these are append-write-mostly with low
--   cardinality, so the gap is invisible. At projected N=10 (30 sites
--   per organization × N customers), tenant-scoped reads degrade to
--   sequential scans on cumulative-write tables.
--
-- TABLES (with rationale)
--
--   client_approvals               — append-only consent ledger
--   enumeration_results            — Windows discovery output (JSONB
--                                    payloads can be 10s of KB each;
--                                    seq scan is expensive at scale)
--   go_agent_orders                — workstation agent order ledger
--                                    (already partitioned but missing
--                                    site_id index on default partition)
--   go_agent_status_events         — workstation agent state-machine
--                                    history (mig 263); append-only
--   liveness_claims                — per-host liveness ledger
--                                    (mig 206 reconcile path)
--   ots_batch_jobs                 — Bitcoin-anchor batch tracker
--                                    (small N today but high read freq
--                                    from auditor-kit)
--   pending_alerts                 — operator-alert dedup table
--   promotion_audit_log_recovery   — flywheel audit DLQ (mig 253)
--
--   Index shape: `(site_id)` for low-cardinality tables;
--                `(site_id, created_at DESC)` composite for
--                time-series tables where the next-most-common filter
--                is recency.
--
-- IDEMPOTENCY
--   `IF NOT EXISTS` on every index — re-run safe.
-- ============================================================================

-- Append-only / low-cardinality: simple site_id index suffices.
CREATE INDEX IF NOT EXISTS idx_client_approvals_site_id
    ON client_approvals (site_id);

CREATE INDEX IF NOT EXISTS idx_pending_alerts_site_id
    ON pending_alerts (site_id);

CREATE INDEX IF NOT EXISTS idx_promotion_audit_log_recovery_site_id
    ON promotion_audit_log_recovery (site_id);

-- Time-series tables: composite (site_id, created_at DESC) so
-- per-site recency queries are index-only scans.
CREATE INDEX IF NOT EXISTS idx_enumeration_results_site_id_created
    ON enumeration_results (site_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_go_agent_orders_site_id_created
    ON go_agent_orders (site_id, created_at DESC);

-- go_agent_status_events has `transitioned_at` not `created_at`
-- (audit verified 2026-05-09 against information_schema).
CREATE INDEX IF NOT EXISTS idx_go_agent_status_events_site_id_created
    ON go_agent_status_events (site_id, transitioned_at DESC);

-- liveness_claims has `claimed_at` not `created_at`.
CREATE INDEX IF NOT EXISTS idx_liveness_claims_site_id_created
    ON liveness_claims (site_id, claimed_at DESC);

CREATE INDEX IF NOT EXISTS idx_ots_batch_jobs_site_id_created
    ON ots_batch_jobs (site_id, created_at DESC);

-- Audit log entry capturing the multi-tenant scaling decision.
INSERT INTO admin_audit_log (action, target, username, details, created_at)
VALUES (
    'migration_298_site_id_index_coverage',
    'multi_tenant_phase0_p1_5',
    'jeff',
    jsonb_build_object(
        'migration', '298',
        'reason', 'Phase 0 multi-tenant audit P1-5: 8 tenant-bearing tables had no site_id index. Single-tenant N=1 was fine; projected N=10 requires composite (site_id, created_at DESC) on time-series tables for index-only recency reads.',
        'audit_ref', 'audit/multi-tenant-phase0-inventory-2026-05-09.md §C.4',
        'tables_indexed', jsonb_build_array(
            'client_approvals', 'enumeration_results', 'go_agent_orders',
            'go_agent_status_events', 'liveness_claims', 'ots_batch_jobs',
            'pending_alerts', 'promotion_audit_log_recovery'
        )
    ),
    NOW()
);

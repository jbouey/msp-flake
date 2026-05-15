-- Migration 323: substrate_violations.synthetic marker column
--
-- Task #66 B1 (Gate A: audit/coach-66-b1-concrete-plan-gate-a-
-- 2026-05-14.md). Phase B of the MTTR soak v2 ratchet rollout —
-- separates synthetic-site violation rows from real customer-site
-- violations so the soak reports + auditor-facing surfaces never
-- mix the two.
--
-- Derived at the single INSERT site (assertions.py — the
-- substrate_violations INSERT) via `site_id LIKE 'synthetic-%'`, NOT
-- threaded through the Violation dataclass. Default FALSE so real-
-- site rows never accidentally get marked synthetic; the soak's
-- synthetic-row writer is the only path that produces TRUE rows.
--
-- Belt-and-suspenders backfill: any existing rows where site_id
-- matches the synthetic prefix get flipped TRUE. Contract-guaranteed
-- no-op today (the soak doesn't write to substrate_violations
-- pre-Phase-B), but cheap insurance.

BEGIN;

ALTER TABLE substrate_violations
    ADD COLUMN IF NOT EXISTS synthetic BOOLEAN NOT NULL DEFAULT FALSE;

-- Belt-and-suspenders backfill (no-op today; covers any future
-- pre-Phase-B synthetic rows that slipped in).
UPDATE substrate_violations
   SET synthetic = TRUE
 WHERE site_id LIKE 'synthetic-%'
   AND synthetic IS FALSE;

-- Partial index for the soak's filtered scans of real-site
-- violations (the customer-facing case). The synthetic-row case
-- doesn't need a covering index — soak reports query directly via
-- the column predicate.
CREATE INDEX IF NOT EXISTS idx_substrate_violations_real_sites
    ON substrate_violations (invariant_name, detected_at)
    WHERE synthetic IS FALSE;

-- Audit-trail row.
INSERT INTO admin_audit_log
    (user_id, username, action, target, details, ip_address)
VALUES (
    NULL,
    'system',
    'substrate_violations_synthetic_marker_added',
    'substrate_violations.synthetic',
    jsonb_build_object(
        'migration', '323_substrate_violations_synthetic_marker',
        'reason', 'Task #66 B1 — separates synthetic-site soak rows '
                 'from real customer-site violations. Derived at '
                 'INSERT time via site_id LIKE pattern.',
        'task', '#66',
        'gate_a_artifact', 'audit/coach-66-b1-concrete-plan-gate-a-2026-05-14.md'
    ),
    NULL
);

COMMIT;

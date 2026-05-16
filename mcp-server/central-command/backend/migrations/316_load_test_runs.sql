-- Migration 316: load_test_runs — multi-tenant load harness run ledger
--
-- Task #62 v2.1 Commit 2 (2026-05-16). Tracks every load-harness run
-- (start → abort/complete) so the AlertManager-driven abort path has
-- a server-side anchor + so operators can query "is a run live right
-- now?" without polling the CX22 box.
--
-- Spec: `.agent/plans/40-load-testing-harness-design-v2.1-2026-05-16.md`
-- Gate A: `audit/coach-62-load-harness-v1-gate-a-2026-05-16.md`
--   (APPROVE-WITH-FIXES; v2.1 closes 3 P0s + 7 P1s structurally)
--
-- Design notes:
--   - status enum gates run lifecycle. Partial UNIQUE INDEX enforces
--     ≤1 active run (starting/running/aborting) at a time — prevents
--     overlapping multi-tenant load that would confuse regression
--     isolation.
--   - abort columns separate from completion columns so a forced
--     abort (alertmanager / operator) leaves an audit trail distinct
--     from a clean completion.
--   - scenario_sha pins the k6 script SHA at run start — operator
--     can reproduce the exact load shape from the row.
--   - metadata JSONB carries per-run k6 args + post-run metric
--     summary; flexible without schema churn.
--   - started_by/aborted_by MUST be named human emails — never
--     'system'/'alertmanager'/'admin'. AlertManager-driven aborts
--     carry the on-call rotation's named human in the body.
--
-- Companion endpoints (sites.py / admin_routes.py — Commit 2):
--   POST /api/admin/load-test/runs              — start (k6 wrapper)
--   POST /api/admin/load-test/{run_id}/abort    — abort (operator or AM)
--   POST /api/admin/load-test/{run_id}/complete — complete (k6 wrapper)
--   GET  /api/admin/load-test/status            — current active run
--   GET  /api/admin/load-test/runs              — history (paginated)
--
-- Substrate invariants (Commit 5):
--   load_test_run_stuck_active (sev2) — run with status IN
--     ('starting','running') AND started_at < now() - interval '6h'
--     (k6 should never run >6h; stuck row indicates orphaned process).
--   load_test_run_aborted_no_completion (sev3) — run with
--     abort_requested_at IS NOT NULL AND status NOT IN ('aborted',
--     'completed','failed') AND abort_requested_at < now() - interval
--     '30m' (k6 should react to abort within 30s; 30m without status
--     transition indicates abort-bridge regression).

BEGIN;

CREATE TABLE IF NOT EXISTS load_test_runs (
    run_id              UUID         PRIMARY KEY,
    started_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    started_by          TEXT         NOT NULL,
    scenario_sha        TEXT         NOT NULL,
    target_endpoints    TEXT[]       NOT NULL,
    status              TEXT         NOT NULL,
    abort_requested_at  TIMESTAMPTZ  NULL,
    abort_requested_by  TEXT         NULL,
    abort_reason        TEXT         NULL,
    completed_at        TIMESTAMPTZ  NULL,
    metadata            JSONB        NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT load_test_runs_status_ck CHECK (
        status IN ('starting','running','aborting','aborted','completed','failed')
    ),
    CONSTRAINT load_test_runs_started_by_named_ck CHECK (
        started_by ~ '^[^@]+@[^@]+\.[^@]+$'
    ),
    CONSTRAINT load_test_runs_abort_consistency_ck CHECK (
        (abort_requested_at IS NULL AND abort_requested_by IS NULL AND abort_reason IS NULL)
        OR
        (abort_requested_at IS NOT NULL AND abort_requested_by IS NOT NULL AND abort_reason IS NOT NULL)
    ),
    CONSTRAINT load_test_runs_completed_consistency_ck CHECK (
        completed_at IS NULL OR status IN ('aborted','completed','failed')
    )
);

COMMENT ON TABLE load_test_runs IS
    'Load-harness run ledger (Task #62 v2.1 mig 316). One row per k6 '
    'run. Active runs have status IN (''starting'',''running'',''aborting''). '
    'Enforces ≤1 active run via partial unique index. AlertManager '
    'aborts write a row with abort_requested_at + abort_reason; k6 '
    'polls GET /api/admin/load-test/status and exits within 30s.';

-- ≤1 active run at a time (prevents overlapping load that confuses
-- regression isolation). Partial unique index over a STABLE-shaped
-- predicate — `status IN (...)` is IMMUTABLE so safe in partial index.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_load_test_runs_one_active
    ON load_test_runs ((1))
    WHERE status IN ('starting','running','aborting');

-- History scan path.
CREATE INDEX IF NOT EXISTS idx_load_test_runs_started_at_desc
    ON load_test_runs (started_at DESC);

-- Status filter (operator dashboard: "show me all aborted runs in
-- the last 24h").
CREATE INDEX IF NOT EXISTS idx_load_test_runs_status_started_at
    ON load_test_runs (status, started_at DESC);

COMMIT;

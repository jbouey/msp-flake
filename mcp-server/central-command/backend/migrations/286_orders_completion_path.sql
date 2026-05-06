-- Migration 286: orders.status completion path (post-Maya 2nd-eye redesign)
--
-- Outside-audit finding (2026-05-06, RT-DM Issue #3):
-- The `orders` table (mig 002) defines status enum
-- `pending → acknowledged → executing → completed → failed → expired`.
-- `agent_api.py:557` transitions `pending → acknowledged`. **NO code
-- path transitions to `completed`.** Acknowledged orders sit forever;
-- order-completion dashboards (db_queries.py:1875) count `completed`
-- rows = 0.
--
-- Initial design (round-table consensus): trigger on
-- `execution_telemetry.metadata->>'order_id'`. Maya's 2nd-eye on the
-- shipped fix caught: `execution_telemetry` has NO metadata column
-- (verified: mig 031 base + mig 052 ALTERs are exhaustive). Trigger
-- would silently no-op forever.
--
-- Redesigned approach (this migration):
--   1. Add `execution_telemetry.order_id TEXT` column (explicit, no
--      JSONB indirection) so the column exists whenever the agent
--      starts emitting it.
--   2. Drop the trigger-based correlation. The primary completion
--      path is the new `/api/agent/orders/complete` endpoint (added
--      to agent_api.py in the same commit as this migration). The
--      agent calls it after execution; the endpoint UPDATEs orders
--      directly. No trigger needed.
--   3. Keep the `sweep_stuck_orders()` function as a backstop for
--      orders that ack'd but never had their /complete called
--      (agent crash, network gap, etc.).
--   4. No backfill of historical orders — the metadata-based
--      correlation never existed, so there's nothing to recover.
--      Sweeper will transition any historical stuck rows on its
--      next run.

-- ─────────────────────────────────────────────────────────────────
-- 1. Add order_id column to execution_telemetry
--    (forward-compatible: agent will emit when updated; backfill
--     when both sides are aligned)
-- ─────────────────────────────────────────────────────────────────

ALTER TABLE execution_telemetry
    ADD COLUMN IF NOT EXISTS order_id TEXT;

CREATE INDEX IF NOT EXISTS idx_execution_telemetry_order_id
    ON execution_telemetry (order_id)
    WHERE order_id IS NOT NULL;

COMMENT ON COLUMN execution_telemetry.order_id IS
    'Migration 286 (RT-DM Issue #3, 2026-05-06): correlation key '
    'linking telemetry to the originating fleet order. Currently '
    'NULL on all rows; agent will emit once /api/agent/orders/'
    'complete becomes the primary completion path. Analytics-side '
    'JOIN against orders.order_id for runbook-completion-by-order '
    'breakdowns.';

-- ─────────────────────────────────────────────────────────────────
-- 2. Sweep-loop helper: orders stuck in 'acknowledged' > 30 min
--    OR 'executing' > 1 hour transition to 'failed' with a
--    timeout marker.
-- ─────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION sweep_stuck_orders()
RETURNS TABLE (transitioned_count INT) AS $$
DECLARE
    v_count INT;
BEGIN
    UPDATE orders
       SET status = 'failed',
           completed_at = NOW(),
           error_message = 'Sweep: order stuck without completion call',
           result = jsonb_build_object(
               'success', false,
               'sweep_reason', 'timeout',
               'recorded_at', NOW()
           )
     WHERE (status = 'acknowledged' AND acknowledged_at < NOW() - INTERVAL '30 minutes')
        OR (status = 'executing' AND acknowledged_at < NOW() - INTERVAL '1 hour');
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN QUERY SELECT v_count;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION sweep_stuck_orders IS
    'Migration 286: sweeper for orders stuck in acknowledged/executing '
    'past their expected completion window. Wired into background_tasks. '
    'Returns count of rows transitioned. Idempotent — re-running '
    'finds 0 stuck rows.';

-- ─────────────────────────────────────────────────────────────────
-- 3. NO BACKFILL.
--
-- Maya 2nd-eye finding: the trigger-based design assumed an
-- `execution_telemetry.metadata` column that never existed. Any
-- backfill predicated on that column would be a no-op. Historical
-- orders stuck in `acknowledged` will be cleared by the next
-- sweep_stuck_orders() invocation from the bg-task loop.
-- ─────────────────────────────────────────────────────────────────

-- Sanity assertion: confirm execution_telemetry now has order_id.
DO $$
DECLARE
    has_col BOOLEAN;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'execution_telemetry'
           AND column_name = 'order_id'
    ) INTO has_col;
    IF NOT has_col THEN
        RAISE EXCEPTION 'mig 286 assertion: execution_telemetry.order_id missing';
    END IF;
    RAISE NOTICE 'mig 286: execution_telemetry.order_id present (forward-compat ready)';
END$$;

-- ─────────────────────────────────────────────────────────────────
-- Rollback (manual)
-- ─────────────────────────────────────────────────────────────────
-- DROP INDEX IF EXISTS idx_execution_telemetry_order_id;
-- ALTER TABLE execution_telemetry DROP COLUMN IF EXISTS order_id;
-- DROP FUNCTION IF EXISTS sweep_stuck_orders();
-- (No data loss; no historical state changes.)

-- 238_substrate_action_invocations.sql
-- Idempotency + audit-pointer table for POST /api/admin/substrate/action.
-- Append-only: no UPDATE/DELETE triggers (24h replay window is a query filter).

-- Write sequence: INSERT after action completes (result_status/result_body required).
-- Pre-flight duplicate check: SELECT FROM this table on (actor_email, idempotency_key).

CREATE TABLE IF NOT EXISTS substrate_action_invocations (
    id               BIGSERIAL PRIMARY KEY,
    idempotency_key  TEXT NOT NULL,
    actor_email      VARCHAR(255) NOT NULL,
    action_key       VARCHAR(64) NOT NULL,
    target_ref       JSONB NOT NULL,
    reason           TEXT,
    result_status    VARCHAR(32) NOT NULL,
    result_body      JSONB NOT NULL,
    admin_audit_id   INTEGER REFERENCES admin_audit_log(id),  -- nullable: audit-log INSERT failure must not block idempotency record
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS substrate_action_invocations_idem
    ON substrate_action_invocations (actor_email, idempotency_key);

CREATE INDEX IF NOT EXISTS substrate_action_invocations_actor_time
    ON substrate_action_invocations (actor_email, created_at DESC);

CREATE INDEX IF NOT EXISTS substrate_action_invocations_action_time
    ON substrate_action_invocations (action_key, created_at DESC);

COMMENT ON TABLE substrate_action_invocations IS
    'Idempotency + audit pointer for /api/admin/substrate/action. '
    'Append-only via app code. 24h replay window is a query filter.';

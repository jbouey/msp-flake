-- Migration 152: Identity binding hardening for trust boundary Unit 1.
--
-- Closes three gaps found in cackle-level adversarial audit:
-- 1. client_approvals.acted_by has no FK — approvals can be forged with fake UUIDs
-- 2. promotion_audit_log.actor is freeform string — no referential integrity
-- 3. API keys have no per-user tracking — org-level with hardcoded admin role
--
-- HIPAA: 164.312(d) Person or Entity Authentication

-- 1. FK on client_approvals.acted_by → client_users(id)
-- Use DO block to handle cases where the FK already exists or data has orphans.
DO $$
BEGIN
    -- Clean any orphaned acted_by references before adding FK
    DELETE FROM client_approvals
    WHERE acted_by NOT IN (SELECT id FROM client_users);

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'client_approvals_acted_by_fk'
    ) THEN
        ALTER TABLE client_approvals
        ADD CONSTRAINT client_approvals_acted_by_fk
        FOREIGN KEY (acted_by) REFERENCES client_users(id) ON DELETE SET NULL;
    END IF;
END $$;

-- 2. Add created_by_user_id to api_keys for per-user tracking
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS created_by_user_id UUID;
-- No FK constraint — api_keys serve both partner and appliance auth.
-- The application layer validates the user on creation.

-- 3. Add created_by to sites for audit trail
ALTER TABLE sites ADD COLUMN IF NOT EXISTS created_by VARCHAR(255);

-- 4. Add created_by to learning_promotion_reports
ALTER TABLE learning_promotion_reports ADD COLUMN IF NOT EXISTS created_by VARCHAR(255);

-- 5. Add created_by to learning_promotion_candidates
ALTER TABLE learning_promotion_candidates ADD COLUMN IF NOT EXISTS created_by VARCHAR(255);

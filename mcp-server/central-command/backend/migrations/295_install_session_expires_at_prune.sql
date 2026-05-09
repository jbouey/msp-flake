-- ============================================================================
-- Migration 295: Align `prune_install_sessions()` with the
--                `install_session_ttl` substrate invariant.
--
-- BACKGROUND
--   The substrate invariant `_check_install_session_ttl` (assertions.py
--   line 350) fires sev3 when ANY install_sessions row has
--   `expires_at < NOW() - 1 hour`.
--
--   The existing pruner (`prune_install_sessions(retention_days=30)`,
--   migration 244) only deletes rows where `first_seen < NOW() - 30d`
--   AND `install_stage IN ('live_usb', 'completed', NULL)`.
--
--   These two thresholds disagree: a session that expired 24h after
--   first_seen sits unpruned for 29+ more days, and the substrate
--   invariant alerts continuously through that window.
--
--   Audit caught this 2026-05-08 (F-P3-3 — 3 expired install sessions
--   from north-valley-branch-2 reflash on 2026-04-24, alert open
--   321+ hours).
--
-- FIX
--   Update `prune_install_sessions()` to delete rows whose
--   `expires_at < NOW() - INTERVAL '24 hours'` (the same window the
--   substrate invariant treats as a violation, plus a 24h grace).
--   The age-based 30-day rule is preserved as a fallback for rows
--   that somehow lack expires_at.
--
--   Result: the pruner clears the alert on the next 24h cycle of
--   `data_hygiene_gc_loop`, and the alert window for legitimately-
--   expired sessions narrows from 29+ days to ≤24h.
-- ============================================================================

CREATE OR REPLACE FUNCTION prune_install_sessions(retention_days INTEGER DEFAULT 30)
RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    deleted_count INTEGER;
    deleted_expired INTEGER;
BEGIN
    -- Path A: expires_at-based pruning. Anything past expires_at
    -- by more than 24h is genuinely abandoned (substrate invariant
    -- treats >1h past expires_at as a violation; we add a 23h grace
    -- to allow operators to inspect a recently-expired row before
    -- it gets cleared). The install_stage filter is omitted here —
    -- if a session is past expires_at by >24h, the install
    -- definitively did not progress.
    DELETE FROM install_sessions
     WHERE expires_at IS NOT NULL
       AND expires_at < NOW() - INTERVAL '24 hours';
    GET DIAGNOSTICS deleted_expired = ROW_COUNT;

    -- Path B: age-based pruning fallback for rows lacking expires_at
    -- (legacy data shape). Same shape as the original mig-244
    -- function — preserved for backwards compatibility.
    DELETE FROM install_sessions
     WHERE first_seen < NOW() - (retention_days * INTERVAL '1 day')
       AND install_stage IN ('live_usb', 'completed')
       AND expires_at IS NULL;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;

    RETURN deleted_expired + deleted_count;
END $$;

COMMENT ON FUNCTION prune_install_sessions(INTEGER) IS
  'Wave-2 (mig 295): expires_at-based pruning aligned with the '
  '`install_session_ttl` substrate invariant. Old behavior of '
  'age-based-only pruning preserved as Path B fallback for legacy '
  'rows lacking expires_at.';

-- Audit log entry capturing the fix.
INSERT INTO admin_audit_log (action, target, username, details, created_at)
VALUES (
    'migration_295_install_session_expires_at_prune',
    'prune_install_sessions',
    'jeff',
    jsonb_build_object(
        'migration', '295',
        'reason', 'Align pruner threshold with substrate invariant. Pre-fix the install_session_ttl invariant alerted continuously for 321+ hours on 3 expired-but-not-yet-30d-old sessions from north-valley-branch-2 reflash 2026-04-24. Post-fix the pruner removes rows past expires_at by 24h, narrowing the alert window from 29+ days to ≤24h.',
        'audit_ref', 'audit/coach-e2e-attestation-audit-2026-05-08.md F-P3-3'
    ),
    NOW()
);

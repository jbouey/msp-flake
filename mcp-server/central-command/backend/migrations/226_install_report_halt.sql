-- Migration 226: install_reports halt telemetry.
--
-- Session 208 v38 — round-table follow-up to the t740 silent-halt debug.
-- Until now the installer had two telemetry posts: /report/start and
-- /report/complete. When the HW-compat gate halted an uncertified box,
-- it went into `sleep 86400` BEFORE any complete post, so Central
-- Command saw only the start report and the box appeared silently stuck.
--
-- v38 adds a third post: /report/halt, called right before any
-- bounded-sleep halt path. This migration backs it with four columns
-- and the "install halt" substrate invariant can now distinguish a
-- zombie from a certified-halt.

BEGIN;

ALTER TABLE install_reports
    ADD COLUMN IF NOT EXISTS halted_at     TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS halt_stage    VARCHAR(80),
    ADD COLUMN IF NOT EXISTS halt_reason   VARCHAR(80),
    ADD COLUMN IF NOT EXISTS halt_log_tail TEXT;

CREATE INDEX IF NOT EXISTS idx_install_reports_halted
    ON install_reports (halted_at DESC)
    WHERE halted_at IS NOT NULL;

COMMENT ON COLUMN install_reports.halt_stage IS
    'Installer function that called post_halt_report (e.g. check_hardware_compat).';
COMMENT ON COLUMN install_reports.halt_reason IS
    'Short machine-readable halt code (e.g. unknown_product, tested_false).';
COMMENT ON COLUMN install_reports.halt_log_tail IS
    'Last ~20 lines of /tmp/msp-install.log at halt time. Troubleshooting aid.';

COMMIT;

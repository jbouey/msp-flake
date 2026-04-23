-- Migration 239: install_sessions.first_outbound_success_at
--
-- v40 FIX-15 (Session 209, 2026-04-23 round-table cont.). The v40 24h soak
-- gate reads "zero MSP_EGRESS_DROP lines for origin IP" — a retrospective
-- signal that only tells us AFTER the soak whether it passed. We also need
-- a LIVE positive signal: "the installed system made an outbound HTTPS
-- connection to the VPS origin at this timestamp."
--
-- This column is populated by the new /api/install/report/net-ready
-- endpoint, which the installed-system msp-auto-provision.service POSTs
-- immediately after the first successful `run_network_gate_check()` pass
-- (all four stages green: DNS, TCP/443, TLS, HTTP /health).
--
-- Paired with the install_sessions.first_seen timestamp already populated
-- at installer boot, this column lets /admin/substrate-installation-sla
-- report a *real* "minutes to first origin-reached" p50/p95/p99. Today
-- that endpoint uses `site_appliances.first_checkin - install_sessions
-- .first_seen` — which is later and includes the bearer-token handshake.
-- first_outbound_success_at is the earliest possible positive: "network
-- works" before "auth works." Tonight's 1D:0F:E5 incident showed the two
-- diverge by minutes to hours.
--
-- Additive-only change. No backfill — NULL means "never reported" OR
-- "pre-v40 installer that doesn't POST net-ready yet." Both are indistinguishable
-- at the DB level and both mean "don't treat first_outbound_success_at
-- as a completion signal for this row." The invariant
-- `provisioning_network_fail` handles the NULL case explicitly.

BEGIN;

ALTER TABLE install_sessions
    ADD COLUMN IF NOT EXISTS first_outbound_success_at TIMESTAMPTZ;

-- Partial index: only rows that have EVER succeeded. Used by the
-- substrate-installation-sla endpoint; excludes the NULL-heavy long
-- tail of legacy/pre-v40/still-broken installs.
CREATE INDEX IF NOT EXISTS idx_install_sessions_first_outbound_success
    ON install_sessions (first_outbound_success_at DESC)
    WHERE first_outbound_success_at IS NOT NULL;

COMMENT ON COLUMN install_sessions.first_outbound_success_at IS
    'Timestamp at which the installed system first completed a 4-stage '
    'network-gate check (DNS+TCP+TLS+HTTP to api.osiriscare.net / '
    'origin IP 178.156.162.116). Populated by /api/install/report/net-ready '
    'from the installed-system msp-auto-provision.service. NULL = never '
    'reached origin OR pre-v40 installer (no net-ready POST). '
    'Monotonic — never overwritten once set.';

COMMIT;

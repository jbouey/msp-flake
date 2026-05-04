# Weekly scheduled_followups digest — 2026-05-04

**Source:** `.agent/claude-progress.json::scheduled_followups` (11 entries; 2 marked closed in-file: #6 D5, #7 D7)
**Today (UTC):** 2026-05-04
**Notable today:** entry #2 (this digest) is DUE_TODAY and being re-armed; entry #0 (sigauth window-close) is DUE_TOMORROW — flag prominently.

## OVERDUE
None.

## DUE_TODAY (today == 2026-05-04)
- **[#2] Weekly scheduled_followups digest** — due 2026-05-04T13:43Z. Self-healing chain entry. Being executed now; re-armed to 2026-05-11.

## DUE_THIS_WEEK (1..7 days)
- **[#0] sigauth window-close** — due **2026-05-05T17:11Z (TOMORROW)**. Critical: task #169 was closed early on user override 2026-04-28 22:51Z with the explicit reopen criterion — re-fire of `sigauth_enforce_mode_rejections` OR `sigauth_post_fix_window_canary` through window-end auto-reopens task and pivots to MVCC / `deleted_at` / autovacuum hypothesis. **Both invariants are SILENT in prod as of digest run** (see snapshot below) — IF still silent through 2026-05-05 17:11Z, action: REMOVE `sigauth_post_fix_window_canary` from `ALL_ASSERTIONS` (one-line edit in `assertions.py`). Doc: `docs/security/sigauth-wrap-validation-2026-04-28.md`.
- **[#3] P1: persistence-drift recurrence not escalating to L2** — due 2026-05-08. 406 L1 resolutions in 7d for windows_update / defender_exclusions / rogue_scheduled_tasks but ZERO L2 decisions. CLAUDE.md mandates 3+ same-type in 4h → bypass L1 → L2. Investigate `_recurrence_velocity_loop` + threshold config + `l2_decisions` trigger gates. Multi-day.
- **[#4] P3: extend `incidents.resolution_tier` VARCHAR(10) → VARCHAR(32)** — due 2026-05-08. Architectural cleanup deferred from the mig 266 abandonment during the 2026-05-01 prod outage. Drop+recreate of `partner_site_weekly_rollup` matview required. Calm-session work.

## UPCOMING (8..14 days)
- **[#5] D5: extend `||`-INTERVAL lint to SQL function bodies** — due 2026-05-15. **Already closed in-file** (commit a5bb68ad, 2026-05-01).
- **[#1] EXPECTED_INTERVAL_S calibration sweep** — due 2026-05-15. Session 214 follow-up; AST-grep CI gate to catch `bg_heartbeat` calibration drift class. Doc: commit e820ca85.
- **[#6] D7: prod-fixture coverage for 4 new invariants** — due 2026-05-15. **Already closed in-file** (closed 2026-05-01).

## LATER (>14 days)
- **[#7] D1: per-control granularity in `calculate_compliance_score`** — due 2026-05-22. Multi-day schema design; design doc at `.agent/plans/d1-per-control-granularity-design.md`.
- **[#8] D8: `scheduled_followups.dependency_warnings` field** — due 2026-05-29. Quick schema extension.
- **[#9] BUG 2 sweep: 63 frontend `credentials: 'same-origin'` → `'include'`** — due 2026-05-29. Per-site verified, eyes-on; some uses (portal browser-verify) are legitimate.
- **[#10] BUG 3 cleanup: 10 legacy `compliance_status` readers → `get_per_device_compliance`** — due 2026-05-29. CI ratchet baseline 14; lower as each migrates.

## Substrate invariant snapshot (prod)
Query: `SELECT invariant_name, severity, COUNT(*) AS open_rows, MAX(detected_at) FROM substrate_violations WHERE resolved_at IS NULL GROUP BY ... ORDER BY ...`

| invariant_name | severity | open_rows | most_recent |
|---|---|---|---|
| install_session_ttl | sev3 | 1 | 2026-04-25 13:02Z |
| journal_upload_never_received | sev3 | 1 | 2026-05-01 10:29Z |

**Named-IN-list query for the 6 followup-referenced invariants (`sigauth_enforce_mode_rejections`, `sigauth_post_fix_window_canary`, `bg_loop_silent`, `substrate_assertions_meta_silent`, `compliance_packets_stalled`, `partition_maintainer_dry`): ZERO rows returned. All silent.**

This is the headline result for the 2026-05-05 sigauth window: both sigauth invariants currently SILENT → on track to permit `sigauth_post_fix_window_canary` removal tomorrow. Task #169 is **NOT** reopened.

## Self-healing chain status
- **Cron re-armed:** `CronCreate` schedule `"0 13 * * 1"` (Monday 13:00 UTC, unambiguous; the original `"43 8 * * 1"` had local-vs-UTC ambiguity). Recurring=true, durable=true. ID returned in reply.
- **JSON entry bumped:** `.agent/claude-progress.json` scheduled_followups[2].due `2026-05-04T13:43:00Z` → `2026-05-11T13:43:00Z`. Edit-tool surgery; everything else byte-preserved.
- **Cron prompt:** one-liner asking future-Claude to read `scheduled_followups`, run the digest, re-arm the cron, and bump THIS entry +7d.

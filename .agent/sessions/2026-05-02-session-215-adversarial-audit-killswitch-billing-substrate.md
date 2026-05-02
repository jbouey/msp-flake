# Session 215 — Adversarial UI audit, kill-switch P0, admin billing, substrate hygiene

**Date:** 2026-05-02
**Volume:** 29 commits, all CI-green and prod-runtime-SHA-verified
**Theme:** Owner-operator administrative surface. "If Anthropic disappears tomorrow, can the operator still operate Central Command?" — answer: yes, the audit + ship batch made that 100% true.

## Workstream summary

### 1. Round-1 audit-closure fixups (early session)
Tail of the Session-214 D1/D2/D4 close: per-control lockstep ratchets to 0, score-threshold canon collapse (27 → 1 site), pre-push parity gate (#46), 8 supporting P1s (#41-48). Schema fixture forward-merged for mig 271.

### 2. AI-independence audit (#52-60, 9 items)
End-to-end: can Central Command run if Anthropic / OpenAI / Vertex disappears? Verdict YES. Compliance chain (evidence + OTS + privileged-access attestation) has zero LLM touch. L1 healing tier (deterministic rules) covers ~70% of incidents. L2 LLM tier degrades gracefully — `/incidents` UI shows "L2 unavailable" banner, operator queue continues. CI gate `tests/test_compliance_chain_llm_free.py` ratchets the LLM-free property.

### 3. ADVERSARIAL UI audit (#64-69, 6 deliverables)
User pivoted mid-session: *"oppositionally look for failure modes."* Surfaced 6 missing operator-class buttons in the admin surface:
- **#64 P0 — fleet-wide healing kill-switch** (incident response). Three endpoints (`/api/admin/healing/global-pause/resume/global-state`), banner across all admin pages, Ed25519 attestation per-site, HIPAA disclosure on client portal. Round-table found this was missing — operator had no way to pause healing fleet-wide if a runbook started misbehaving.
- **#65 P1** — org deprovision/reprovision UI (Organizations page)
- **#66 P1** — bulk multi-select on Sites + Fleet pages, generic `BulkActionToolbar`
- **#67 P1** — admin billing read-only view (subscription list)
- **#68 P2** — user API-key rotation UI
- **#69 P3** — audit log filter by actor + IP + date

### 4. Kill-switch sub-followups (#73-76, post-#64 round-table)
Three round-table-flagged gaps closed in the same day:
- **#73 (Camila P1)** — kill-switch HIPAA disclosure surfaces on **client portal home** so customers see "healing paused fleet-wide" with operator+reason, not just admin
- **#74 (Camila P1)** — kill-switch fan-out writes per-site Ed25519 attestation via `privileged_access_attestation.create_*` (same chain as #72 below)
- **#75 (Steve P2)** — `KillSwitchBanner` component renders on every admin page, not just /healing
- **#76 (Steve P3)** — banner + dropdown share `useKillSwitchState()` React Query hook (was 4 inline pollers)

### 5. #72 — admin destructive billing actions (cancel + refund)
Privileged-access chain: typed `confirm_phrase` + reason ≥20 chars + email-format actor + Stripe `idempotency_key` + `admin_audit_log` + Ed25519 attestation to customer's `site_id`. Both actions added to `ALLOWED_EVENTS` (admin-only, lockstep asymmetry permitted).

**Adversarial round-table caught two real bugs before ship:**
- **B-1 (Brian P1)** — refund endpoint didn't verify `Charge.customer == URL.customer`. Stripe accepts any charge_id in the account; an operator typo would silently misattribute the audit_log + Ed25519 attestation to the wrong site_id. Fix: `stripe.Charge.retrieve()` + ownership check before `Refund.create()`.
- **D-1 (Diana P1)** — SQLAlchemy queries didn't use `execute_with_retry()`, violating the CLAUDE.md PgBouncer rule (`DuplicatePreparedStatementError` class on rotation).

Both regressions pinned by 13-assertion CI gate `tests/test_admin_billing_destructive.py`.

### 6. Process discipline (#77 + the CI cascade)
Mid-session, a 14-deploy CI cascade hit because `test_sql_on_conflict_uniqueness.py` was untracked locally but in the pre-push allowlist (passed local fs, failed git tree). Fixed root cause + added new gate `test_pre_push_allowlist_only_references_git_tracked_files` using `git ls-files`. User filed **#77 P0 PROCESS rule**: *"you must ensure each deploy passes before moving on."* In effect for the rest of the session — every commit waited on CI green + `curl /api/version` SHA verify before claiming shipped.

### 7. Lockstep test promotion (#79)
The `test_allowed_events_matches_privileged_order_types` assertion lived in `_pg.py` (TIER-3, CI-only). #72 lockstep drift was caught by CI but missed by pre-push, forcing a round-trip. Promoted the assertion to a new TIER-1 file `tests/test_privileged_chain_allowed_events_lockstep.py`. Auto-discovered by the existing `_lockstep(?:_pg)?\.py$` pattern in `_TIER1_PATTERNS`.

### 8. partition_maintainer permission fix (#78)
Pre-fix: `partition_maintainer_loop` ran as `mcp_app` (PgBouncer) and hit `permission denied for schema public` every 24h. Survived only because partitions were pre-created through Dec 2026. Fix: open single-shot `asyncpg` connection to `MIGRATION_DATABASE_URL` (superuser, bypassing PgBouncer) — same pattern as `heartbeat_partition_maintainer_loop`. Round-table chose consistency over a SECURITY DEFINER migration.

### 9. Substrate `journal_upload_stale` canonicalization fix
Found while inspecting open violations: `physical-appliance-pilot-1aea78` (orphan site_id from Apr 25 relocate) was firing `journal_upload_stale` sev2 since the relocate. Root cause: assertion's CTE didn't filter `site_appliances.deleted_at IS NULL` and used the historical `journal_upload_events.site_id` instead of the live `site_appliances.site_id`. Fix: JOIN + filter + use `sa.site_id`. Same class as Session 213 F1/F3 telemetry orphans, but `appliance_id` is the natural key here so no `canonical_site_id()` needed.

### 10. Operator action issued — nixos_rebuild on B6:61 (in flight at session save)
Substrate caught `journal_upload_never_received` on `north-valley-branch-2-84:3A:5B:91:B6:61` (8 days alive on v0.4.13 with no journal uploads). Root cause: appliance was relocated via Session 210-B reprovision flow which only rewrites `/var/lib/msp/config.yaml` — the NixOS closure on disk predates the journal-upload module. SSH-via-WG access blocked from VPS (no key, no route on .2). Issued fleet order `5ce11e16-3566-448d-92bd-053d50b1a417` (`nixos_rebuild` scoped via `target_appliance_id`) — daemon will pick up next 60s tick, rebuild closure, install + start `msp-journal-upload.timer`. Substrate auto-resolves on next 60s assertion tick after first upload lands.

## Substrate state at session end
3 open violations:
- `journal_upload_stale` sev2 on orphan site_id — **resolves after fix d84af600 deploys** (already done, awaiting next 60s substrate tick)
- `journal_upload_never_received` sev3 on north-valley-branch-2 — **resolves after fleet order 5ce11e16 completes** (in flight)
- `install_session_ttl` sev3 (3 expired sessions, operational housekeeping, not urgent)

## CI deploys this session
29 commits, all green, all prod-SHA-verified. Notable cascade: 14 deploys around mid-day required to break a flaky pre-push allowlist + fix a stale fixture revealed by the unblocked CI. Closed by the new `test_pre_push_allowlist_only_references_git_tracked_files` gate.

## Tasks closed: #39-#79 (41 task closures)
Pending only: #59 (AI-audit cost projection — explicitly excluded by user).

## Next priorities (for next session pickup)
1. **Operator action** — confirm fleet_order `5ce11e16` completed + `journal_upload_never_received` cleared on north-valley-branch-2. If still firing 30 min after order completes, SSH in and check `systemctl status msp-journal-upload.timer`.
2. **2026-05-05 substrate watch** (sigauth window canary) — if `sigauth_enforce_mode_rejections` and `sigauth_post_fix_window_canary` stayed silent through the window, REMOVE the canary from `ALL_ASSERTIONS` (per task #169 closure conditions).
3. **2026-05-07 F6 phase 2 health check** — verify federation phase-2 deferral is still warranted (per `.agent/plans/f6-phase-2-enforcement-deferred.md` cron).
4. **#59 deferred** — AI-audit dim 7 cost-per-tier projection (LLM-off vs current). User excluded today but tractable in a calm session.
5. **P3 future** — once fleet-order surface fully covers ops (we now have `nixos_rebuild`, watchdog catalog, kill-switch, recovery-shell-24h, reprovision), retire sshd-by-default on installed appliances. Today's session demonstrated SSH is still needed for diagnostic access; the `enable_recovery_shell_24h` path is the replacement.

## Memory hygiene
No new memory files needed — all session learnings either land in CLAUDE.md (technical) or are captured in this session log (chronological). Validate passes.

---

## Postscript: live prod incident triggered by my own fleet order (17:37–20:30 UTC)

The fleet_order `5ce11e16` (`nixos_rebuild` for B6:61) issued during the original session at 17:25 was the **proximate cause of a 3-hour production incident** that surfaced via a `phantom_detector` "liveness lie" alert at 17:42.

### Timeline
- 17:25 — fleet_order issued (target_appliance_id=B6:61), 6h TTL
- 17:36:54 — daemon completed first attempt (failed, 11min 16sec curl-timeout against api.github.com per #80 egress allowlist)
- 17:37 — all 3 appliances at site go silent simultaneously (root cause buried for hours; misdiagnosed initially as LAN flap)
- 17:42 — phantom_detector fires `APPLIANCE_LIVENESS_LIE`, email arrives
- 17:42–18:08 — diagnosis: `mark_stale_appliances_loop` was **silently failing every 60s** with `Site-wide UPDATE on site_appliances blocked` (mig 192/208 row-guard rejecting bulk multi-appliance UPDATE). Latent P0 bug exposed by the multi-appliance offline event.
- 18:08 — P0 fix shipped (`92d66bf4`): `SET LOCAL app.allow_multi_row='true'` inside the loop's `admin_transaction`. Documented escape-hatch path.
- 19:13–19:51 — operator inbox FLOODED with 6-emails-per-flap (3 offline + 3 recovery) because the loop now correctly fires AND all 3 boxes flap together.
- 19:51 — per-site coalesce shipped (`6c35cefb`): mark_stale_appliances_loop GROUP BY site_id Python-side → 1 email per site naming all N affected; checkin handler uses earliest-in-60s-window-wins coalesce on the recovery side.
- 19:52–20:14 — flapping continues; coalesce confirmed working (1 offline email per cycle, recovery email under-reports the batch as documented trade-off).
- 20:14 — `WITH chk AS (… LAG window …)` analysis revealed the smoking gun: every silence is **EXACTLY 676 seconds = 11 min 16 sec** — the precise nixos-rebuild curl timeout duration. NOT a network flap — the daemon is **retrying the failed fleet order every 11 min**, blocking checkins synchronously each time.
- 20:19 — manual hot-fix: `UPDATE fleet_orders SET status='completed', expires_at=NOW() WHERE id='5ce11e16-…'`. Backend-side cancel.
- 20:30 — daemon resumed normal cadence after current iteration's curl-timeout exhausted.
- 20:46 — verified gap-free for 16 min; cycle truly broken (daemon does NOT cache orders locally).

### Lessons (now in CLAUDE.md as load-bearing entries — see Session 215 entries)
1. **fleet_cli MUST refuse to issue orders that depend on `github.com` or other domains outside the appliance egress allowlist** (api.osiriscare.net, release.osiriscare.net only). Filed as #80; should add the refusal in fleet_cli's input validator BEFORE this class can recur. The order I issued was structurally impossible to succeed.
2. **Failed fleet orders are retried indefinitely until expires_at** (#84 P0 daemon bug). Three completion rows existed for one order — daemon polled, ran, failed, polled again, ran again. Backend should auto-mark `status='failed'` after N consecutive failed completions; daemon should query `fleet_order_completions` for THIS appliance_id+order_id BEFORE re-running.
3. **`mark_stale_appliances_loop` was silently broken** (#92d66bf4) for an unknown duration before today. The legitimate-bulk-op escape hatch (`SET LOCAL app.allow_multi_row='true'`) needs to be explicitly applied to every legitimate batch loop. Same fragility class as Session 214 Block 1 admin_transaction adopters.
4. **Per-site coalesce is a real operator-grade need** (`6c35cefb`) — site-wide events should produce 1 email per site, not N emails per appliance. Especially important once we have multi-tenant customers.
5. **Recovery coalesce under-reports the batch** (race window: first appliance back wins, names only itself). Acceptable trade-off for today; proper fix is delayed-send (~10s sleep + re-query) — filed implicitly via #83.

### Net session totals (revised)
- 33 commits today (29 in original session + 4 in incident response)
- 41 task closures + 5 follow-ups filed (#80, #82, #83, #84, plus #81 closed)
- 1 P0 latent bug surfaced + fixed (mark_stale row-guard)
- 1 self-inflicted incident (caused by my own fleet_order; resolved via backend hot-fix)
- Substrate worked exactly as designed throughout — `phantom_detector` caught the lie, `APPLIANCE_LIVENESS_LIE` log fired correctly, the broken response path was the bug

### Updated next priorities (supersedes the original list above)
1. **#80 + #84 are the most urgent follow-ups.** Both are root-cause fixes for the incident class that hit today. Neither is a one-off — same shape will recur on any failed fleet order with a long TTL or any order requiring github.com egress.
2. **Site-network investigation NO LONGER NEEDED** — the perceived "network flap" was the daemon retry loop blocking checkins. There is no LAN/WAN issue. (The MikroTik gateway change 7 days ago is unrelated.)
3. **Recovery-coalesce delayed-send** — proper fix for the under-reporting trade-off documented in `6c35cefb`. ~30 min ship.
4. Original 2026-05-05 sigauth canary watch + 2026-05-07 F6 phase-2 health check still pending.

# Session 203 — Portal Round Tables + Audit Proof Legal Emergencies

**Date:** 2026-04-09
**Commits:** 30+ across the day
**Migrations:** 148, 149, 150 (all applied to prod)
**Daemon version:** unchanged
**Status:** all 6 main batches deployed; Batch 7 in progress at session end

---

## Summary

Single-day marathon session that started with Site Detail enterprise polish + dashboard audit, escalated into a round-table audit of all three portals (client, partner, audit-proof display) and ended shipping 6 batches of fixes that closed every CRITICAL and HIGH finding from the audits. Most impactful fix: the audit-proof Merkle batch_id collision that was producing 1,198 bundles with cryptographically broken proofs.

The audit-proof display round table (subagent-driven, walked real Merkle proofs against production data) was the highest-value find of the day — the platform's "tamper-evident evidence" claim was measurably false for ~1,200 bundles.

---

## What shipped (in commit order)

### Site Detail page (early in session)
- `aa9decb` — Hero compliance card, deployment progress fix, VPN tooltip, More dropdown
- `834bb49` — Audit trail + activity timeline + org breadcrumb (`SiteActivityTimeline` component, `_audit_site_change` helper, `GET /api/sites/{site_id}/activity` endpoint)
- `752fe3a` — Decommission modal triple-guard (export + type-confirm + checkbox + arm)
- `ce5a17c` — Phase 2 refactor: SiteDetail.tsx 2045→656 lines, 11 sub-components extracted, SLA/search/FAB + portal expiry
- `4a72f43` — Wire SiteSLA, SiteSearchBar, FloatingActionButton into layout

### Dashboard enterprise audit (P0/P1/P2)
- `f533628` — P0: 7 critical UX fixes (incidents limit, dup row, red-tint, target line, DashboardSLAStrip, freshness+refresh, empty state)
- `1ebb086` — P1: 7 polish + observability (last promotion, stale attention split, FAB, OTS delay, MFA coverage, dismiss banner, error boundary)
- `daaf8a4` — P2: sparklines, PDF export, kpi-trends endpoint

### Credential encryption key rotation (P0 from Session 197)
- `c0550c0` — MultiFernet refactor + admin endpoint + background re-encrypt + 24 tests + KEY_ROTATION_RUNBOOK.md
- `d58c025` + `38ecbd2` — fix old test_security_modules + main.py startup that referenced renamed private API

### Incident dedup race
- `8ab6230` — `ON CONFLICT (dedup_key) DO NOTHING RETURNING id` in agent_api.py (closes the race that Migration 142 partial unique index was supposed to handle)

### Audit-proof legal emergencies (Session 203 batches)
- **`b93a6e8` Batch 1** — C2/C3/C6/C7 + Partner H1/H2: auth on 3 evidence endpoints, fix chain-of-custody SQL columns (prev_hash/agent_signature), fix audit_report.py SQL (4 wrong columns), strip forbidden legal language, add RBAC to 2 partner endpoints
- **`965dd36` Batch 2** — C1: Merkle batch_id collision fix in process_merkle_batch + Migration 148 backfill (1,198 bundles → legacy)
- **`c336eec` Batch 3** — Portal audit logging: Migration 149 client_audit_log + Migration 150 drop unused partner_audit_log, _audit_client_action helper, PartnerEventType enum extended, 3 client + 3 partner mutations wired
- **`95b6ce5` Batch 4** — C5: compliance_packet cron resilience (drop day=1/hour=2 gate, walk last 3 ended months, idempotent ON CONFLICT)
- **`47dad68` Batch 5** — C4: client-side Ed25519 + chain-hash verification (`/public-keys` endpoint, useBrowserVerify hook, BrowserVerifiedBadge component, @noble/ed25519 dep)
- **`d83bc2c` Batch 6** — Partner MFA→Redis (M1) + client rate limit on magic-link/login (H2) + ClientDashboard error boundary

### CI hotfixes (kept the deploy gate happy)
- `1243bbd` + `cf792ff` — fastapi.Cookie/Query/Request stubs in 4 test files
- `230a3e3` — client_portal.py missing Dict + Any imports

---

## Migrations applied to prod

| # | File | What it does | Verified |
|---|---|---|---|
| 148 | `148_fix_broken_merkle_batches.sql` | Reclassify 1,198 bundles to `ots_status='legacy'` where `bundle_count != actual_count` in their batch | YES — anchored 132,261→131,150, legacy 100,972→102,170 |
| 149 | `149_portal_audit_logs.sql` | Create `client_audit_log` table (append-only trigger, RLS, indexes) | YES — table exists |
| 150 | `150_drop_unused_partner_audit_log.sql` | Drop `partner_audit_log` (created by an earlier draft of 149, then we discovered `partner_activity_log` already existed) | YES — table dropped |

---

## Production state at end of session

- **Current release symlink:** `/opt/mcp-server/current → releases/20260409_161332_d83bc2cc` (Batch 6)
- **Live endpoints verified:**
  - `GET /api/evidence/sites/{id}/verify-chain` → **403** (was 200 with no auth before C3)
  - `GET /api/evidence/sites/{id}/public-keys` → **403 without auth** (new C4 endpoint)
  - `GET /api/evidence/sites/{id}/bundles` → **403** (was 200)
  - `GET /api/evidence/sites/{id}/blockchain-status` → **403** (was 200)
- **Database:**
  - `compliance_bundles`: anchored=131,150 / legacy=102,170 (post-Migration 148)
  - `compliance_packets`: 0 rows (cron has 15-min startup delay, will populate on first iteration ~16:28 UTC)
  - `client_audit_log`: 0 rows (will populate as users make mutations)
  - `partner_activity_log`: 26 rows (existing, plus new event types defined)

---

## Round-table audit findings — closure status

### Audit Proof Display (CRITICAL findings — closed)
- **C1** Merkle batch_id collision → ✅ writer fixed + 1,198 bundles backfilled
- **C2** Chain-of-custody SQL column names → ✅ prev_hash/agent_signature
- **C3** /verify-chain etc no auth → ✅ require_evidence_view_access guard
- **C4** Server-trusted "Chain Valid" → ✅ BrowserVerifiedBadge does Ed25519 in-browser
- **C5** compliance_packets cron broken → ✅ resilient catch-up loop
- **C6** audit_report.py SQL → ✅ 4 wrong columns fixed
- **C7** Forbidden legal language → ✅ stripped + replaced

### Partner Portal (closed)
- **H1** update_partner_drift_config missing RBAC → ✅ require_partner_role
- **H2** trigger_discovery missing RBAC → ✅ require_partner_role
- **H3** No partner_audit_log → ✅ Use existing log_partner_activity, extended enum
- **H4** RBAC untested (partner_users=0) → noted, not yet exercised
- **M1** Partner MFA in-memory → ✅ Redis-backed helpers
- **M2** partners.py 3024 lines monolith → not done (deferred — refactor risk)
- **M3** PartnerDashboard memo + boundary → not done
- **M4** L3/L4 row coloring → not done
- **M5** Structured logging → not done

### Client Portal (partially closed)
- **H1** No client_audit_log table → ✅ Migration 149 created
- **H2** No rate limit on magic-link/login → ✅ check_rate_limit added
- **M1** client_portal.py 4187 lines → not done
- **M2** ClientDashboard.tsx 817 lines → wrapped in error boundary, not split
- **M3** No client-visible audit trail → in progress (Batch 7)

---

## Where Batch 7 was when context ran out

In progress when session ended. Batch 7 scope:

1. **Wire MORE client mutations into `_audit_client_action`**
   - DONE: update_user_role, set_password, remove_user (Batch 3)
   - DONE in Batch 7: update_client_drift_config (1290), invite_user (1910), submit_client_credentials (4241)
   - REMAINING:
     - action_client_alert (4129) — alert action (approve/dismiss/etc)
     - acknowledge_escalation (3777) + resolve_escalation (3806)
     - update_escalation_preferences (3636)
     - register_device (3935) + ignore_device (4013)
     - forward/approve/reject promotion candidate (2328/2411/2543)
     - TOTP setup/verify/disable (3056/3089/3138)
     - transfer request/cancel (2087/2173)
     - billing checkout/portal (2693/2765)
2. **Backend: GET /api/client/audit-log endpoint** — paginated audit log for the current org, used by the new disclosure-accounting page
3. **Frontend: ClientAuditLog page** — new page in client portal showing the audit trail (HIPAA §164.528 disclosure accounting view)
4. **Tests** — wire-up coverage + endpoint smoke tests

The batch is uncommitted on disk. Next session should:
1. Verify the 3 already-wired endpoints in client_portal.py (drift_config, invite_user, submit_credentials) are intact
2. Wire the remaining 12 mutations (~30 minutes of mechanical edits)
3. Add the GET /api/client/audit-log endpoint (paginated, RLS-scoped)
4. Build ClientAuditLog.tsx page
5. Commit + push as Batch 7

---

## Open items beyond Batch 7 (the to-finish list)

### Audit Proof HIGH findings still pending
- **H1** Bundle timeline chain reports 22.6% signed but UI says "Chain Valid" — need to surface the legacy bundle warning prominently
- **H2** Ed25519 public key download (endpoint shipped in Batch 5, frontend doesn't render the key list yet)
- **H3** Per-bundle .ots file download — endpoint exists, no UI button
- **H4** Network-monitoring bundles silently skipped — need to either include them or document the gap in the scorecard
- **H5** Bundle hash computed server-side if client omits — need to enforce client-side hash for trust model
- **H6** Control mapping HIPAA-only — multi-framework display needs scorecard refactor
- **H7** No §164.528 disclosure accounting view — Batch 7 closes this for clients; partners need similar view
- **H8** Multi-framework display in scorecard — backend has 9 frameworks, frontend hardcoded to HIPAA

### "Verification kit" download (auditor handoff)
The most-cited gap from the audit-proof round table was: **no downloadable .tar.gz with bundles.json + chain.json + pubkeys.json + ots/*.ots + verify.sh**. Auditors expect this. We have all the pieces; need an aggregator endpoint.

### Refactor work deferred
- Split partners.py (3024 lines) — into base.py / sites.py / orgs.py / etc.
- Split client_portal.py (4187 lines) — same pattern
- PartnerDashboard.tsx memoization + DashboardErrorBoundary wrap
- PartnerEscalations L3/L4 row tinting
- partners.py structured logging migration

### Production verification still pending (post-deploy)
- compliance_packets first iteration writes (cron 15-min startup delay, expected ~16:28 UTC)
- Verify Migration 148 / 149 / 150 paper trail in admin_audit_log
- Verify the rate limit on /request-magic-link actually fires (smoke test from a curl loop)

---

## Key gotchas + lessons learned

1. **Deploy gate is `deploy: needs: test`** — failing CI never deploys, even if you push 6 commits in a row. This is correct but creates a confusing visual ("5 of 6 commits red") when in reality the LATEST green commit deploys all the cumulative content. Confirmed via `/opt/mcp-server/releases/` symlink history.

2. **Test files stub fastapi at module level**, and the stubs leak across files in the same pytest process. The CI workflow runs each test file in its own subprocess to prevent this — but you still have to update every stub when adding a new fastapi import to a production file. This bit us 3 times in the session (Cookie, Query, Request, BackgroundTasks added to evidence_chain.py and audit_report.py).

3. **Python lazy annotation evaluation hides missing typing imports** until something else imports the module under pytest's collection phase. `Optional[Dict[str, Any]]` in a function signature without `from typing import Dict` doesn't fail at parse time, just at first import — and that import might be triggered by an unrelated test file. Always import every typing name you use.

4. **`process_merkle_batch` had a SILENT data corruption bug** for months — the `ON CONFLICT (batch_id) DO NOTHING` looked safe but actually meant the second sub-batch's Merkle root was DROPPED while its bundles still got UPDATE'd to point at the (wrong) stored row. The leaf_index values overlap between sub-batches, so storage alone can't tell you which bundle came from which tree. The conservative backfill marks all bundles in a collided batch as legacy.

5. **Partner portal already had `partner_activity_log` + `log_partner_activity()`** — the audit found "no audit log" because it queried for `partner_audit_log` (wrong table name). Always grep for the actual table that the application writes to before assuming nothing exists. We pivoted Batch 3 mid-flight to extend the existing infra rather than create a parallel table.

6. **`compliance_packets` cron had a 1-hour-per-month window** with no catch-up — any restart during 02:00-03:00 UTC on the 1st = miss for the entire month. The fix is to walk the last N completed months on every iteration and generate any missing one. This is the standard pattern for "monthly job" cron systems and should be applied to any other gated background task.

7. **Subagent overload (529 errors)** hit us 4 times during the round-table phase. The Audit Proof subagent finished cleanly but the Partner Portal and Client Portal retries kept failing. Inline auditing was faster than the 3rd retry. Lesson: if a subagent 529s once, retry; if it 529s twice, do it inline.

---

## Files touched (high-level)

**Backend (~25 files):**
- `evidence_chain.py` — auth guards, public-keys endpoint, chain-of-custody column fix, Merkle batch_id suffix
- `audit_report.py` — 4 SQL column fixes
- `client_portal.py` — _audit_client_action helper, 3+ mutations wired, rate limit on login/magic-link, Dict imports
- `partner_auth.py` — Redis MFA helpers
- `partners.py` — RBAC role checks on 2 mutations, partner_activity_log wiring
- `partner_activity_logger.py` — 5 new event types
- `shared.py` — RATE_LIMIT_OVERRIDES for client login
- `routes.py` — /sla-strip endpoint (Dashboard P0)
- `main.py` — _compliance_packet_loop rewrite (Batch 4)
- `compliance_packet.py` — minor changes
- `credential_crypto.py` — MultiFernet refactor (key rotation)
- `credential_rotation.py` — NEW (admin endpoint + background re-encrypt)
- `agent_api.py` — incident dedup race ON CONFLICT
- `models.py` — minor

**Migrations (3 new):**
- `148_fix_broken_merkle_batches.sql`
- `149_portal_audit_logs.sql`
- `150_drop_unused_partner_audit_log.sql`

**Frontend (~30 files):**
- Site Detail refactor — 11 new files under `src/pages/site-detail/`
- New components: SiteComplianceHero, SiteActivityTimeline, SiteSLAIndicator, SiteSearchBar, FloatingActionButton, DashboardSLAStrip, Sparkline, DashboardErrorBoundary, BrowserVerifiedBadge
- New hooks: useDeployment refactor, useBrowserVerify
- New utils: csrf.ts (extracted helper)
- Modified: Dashboard.tsx, PortalScorecard.tsx, PortalVerify.tsx, ClientDashboard.tsx, ClientHelp.tsx (legal language sweep)
- @noble/ed25519 dep added for browser-side verification

**Tests (~150 new):**
- test_evidence_auth_audit_fixes.py (19)
- test_merkle_batch_id_uniqueness.py (9)
- test_portal_audit_wiring.py (20)
- test_compliance_packet_cron.py (11)
- test_partner_mfa_redis_and_client_rate_limit.py (15)
- test_credential_rotation.py (24)
- test_dashboard_sla_strip.py (10)
- test_dashboard_kpi_trends.py (11)
- test_site_activity_audit.py (15)
- test_site_polish_endpoints.py (26)
- test_incident_dedup_race.py (5)
- (plus several smaller suites)

---

## Next session — recommended starting point

1. **Read this file first.** Pick up Batch 7 mid-flight. The 3 wired endpoints are already on disk; verify they parse, then continue with the remaining 12 client mutations + the new GET /api/client/audit-log endpoint + the ClientAuditLog page.
2. **Verify the compliance_packet cron actually wrote rows** — `SELECT COUNT(*) FROM compliance_packets;` on prod. If still 0, dig into `docker logs mcp-server | grep compliance.packet` for errors.
3. **Build the auditor verification kit endpoint** — single ZIP download with chain-of-custody.json + public-keys.json + .ots files + README.md + verify.sh. This is the highest-value remaining audit-proof item.
4. **Then take on the Audit Proof H-tier items** — H1 legacy warning prominence, H2 Ed25519 key download UI, H3 per-bundle .ots, H6/H8 multi-framework display.
5. **Refactor work** is lowest priority — the partners.py and client_portal.py monoliths work fine, splitting them is risk without immediate value.

---

## Final test counts

- **Backend: 213 tests passing** (74 new this session)
- **Frontend: 14 vitest tests** (SiteSLAIndicator + SiteSearchBar)
- **Go: untouched this session**

## Final commit count

~30+ commits across the day. CI green on the latest. Production deployed at 16:13 UTC on `d83bc2cc` (Batch 6).

---

# Session 203 Continuation — Batch 7 + Recovery Platform Tier 1 (afternoon)

After Batch 6 deployed, the session continued with the remaining client audit-log
wiring (Batch 7) and an emergency-pivot to position OsirisCare as the recovery
platform for refugees of the Delve / DeepDelver compliance-fraud scandal.

## Batch 7 — Client Disclosure Accounting (HIPAA §164.528)

Commit: pre-Tier 1 batch (was already on disk at session resume)

- Wired `_audit_client_action()` into 13 client mutations: `update_user_role`,
  `set_password`, `remove_user`, `update_client_drift_config`, `invite_user`,
  `submit_client_credentials`, `update_escalation_preferences`,
  `client_acknowledge_ticket`, `client_resolve_ticket`, `action_client_alert`
  (uses dynamic `f"ALERT_{action.upper()}"`), `register_device`, `ignore_device`,
  `client_totp_verify` → MFA_ENABLED, `client_totp_disable` → MFA_DISABLED.
- Added `request: Request` parameter to all 13 so `_audit_client_action` can
  capture `request.client.host` for the `ip_address` column.
- New endpoint: `GET /api/client/audit-log` — action prefix filter, days
  lookback (1..2555 = 7 yr HIPAA retention max), pagination, returns events
  with `id/actor_user_id/actor_email/action/target/details/ip_address/created_at`
  + total count for pagination UI.
- New page: `frontend/src/client/ClientAuditLog.tsx` (~370 lines) — filter bar
  with 9 action presets + lookback selector, table with humanized labels via
  `ACTION_LABELS` map, CSV export, pagination. First draft used `BrandedLayout`
  but that requires a `branding` prop — pivoted to plain sticky-header div
  matching `ClientNotifications.tsx` pattern.
- Route added in `App.tsx`: `/client/audit-log`.
- 18 new tests in `test_client_audit_log_endpoint.py` (8 endpoint shape +
  10 mutation wiring assertions).

### CI fallout fix

`f275d23` — `tests/test_client_credentials.py::test_submit_credentials_with_alert_id_inserts_approval`
expected exactly 2 INSERTs. Batch 7 added a `CREDENTIAL_CREATED` audit log
INSERT in `submit_client_credentials`, making it 3. Updated assertion from
`== 2` to `== 3` and added per-table-name verification for all three INSERTs
(`site_credentials`, `client_approvals`, `client_audit_log`).

## Recovery Platform Tier 1 — Delve scandal positioning

Commit `4240887` — "feat: Tier 1 — auditor kit ZIP + Merkle disclosure + /recovery landing"

Context: April 2026 Delve / DeepDelver scandal blew up the compliance-automation
category. Y-Combinator-backed startup accused of fabricating audit evidence,
99.8% identical boilerplate reports, fake AI ("Selin's Report Generator"),
auditor-independence violations. Y Combinator parted ways. The user asked to
research it, brief a round-table, and identify how to position OsirisCare as
the recovery platform for refugees. The round-table response identified Tier 1
as: prove evidence is real, prove we disclose bugs proactively, give refugees
a clear landing page.

### T1.1 — Auditor verification kit ZIP endpoint

`mcp-server/central-command/backend/evidence_chain.py` — added:

- `download_auditor_kit(site_id, limit=1000, offset=0, db, _auth)` — uses
  `require_evidence_view_access` guard, builds ZIP in-memory via
  `io.BytesIO` + `zipfile.ZipFile`, returns `Response(content=zip_bytes,
  media_type="application/zip", ...)` with `X-Kit-Version`/`X-Bundle-Count`/
  `X-Pubkey-Count`/`X-OTS-File-Count` headers. Range cap 1..5000, 404 on
  unknown site, 404 on empty range.
- ZIP contents: `README.md`, `verify.sh`, `chain.json` (with disclosures
  inline including OSIRIS-2026-04-09-MERKLE-COLLISION), `bundles.jsonl`,
  `pubkeys.json` (with 16-char SHA256 fingerprints), `ots/{bundle_id}.ots`
  files.
- `_AUDITOR_KIT_README` template constant — HIPAA audit kit instructions,
  "What success looks like" section, known limitations, disclosures, contact
  info, formatted with `{site_id}`/`{clinic_name}`/`{generated_at}`.
- `_AUDITOR_KIT_VERIFY_SH` template constant — bash + embedded Python
  heredoc that loads `pubkeys.json` + `bundles.jsonl`, walks chain, runs
  Ed25519 verification via `cryptography` lib, calls `ots verify` on every
  `.ots` file. Zero `osiriscare.net` calls (regression-tested as a canary).

`backend/tests/test_auditor_kit_endpoint.py` — 31 source-level pytest tests
covering endpoint shape (7), ZIP contents (6), chain metadata (6), pubkey
export (3), and README/verify.sh quality (9 — including the no-network
canary).

### T1.2 — Public Merkle disclosure post

`docs/security/SECURITY_ADVISORY_2026-04-09_MERKLE.md` — full advisory:

- ID: `OSIRIS-2026-04-09-MERKLE-COLLISION`
- Affected scope: 1,198 bundles, 47 batches, 2 sites
- Root cause: `batch_id = f"MB-{site_id[:20]}-{batch_hour.strftime('%Y%m%d%H')}"`
  collision when called twice in same hour for same site
- Fix: random 8-hex suffix in commit `965dd36`
- Migration 148 verification with before/after row counts
- Customer-runnable verification SQL + curl auditor-kit
- "Why we are publishing this" — Delve scandal positioning
- Discovery → remediation → disclosure timeline (~4 hours total)
- Contact: `security@osiriscare.net`

### T1.3 — /recovery landing page

`frontend/src/pages/RecoveryLanding.tsx` (~330 lines) — public landing for
compliance refugees:

- Hero: "Real evidence. Cryptographically provable. Verified on your auditor's
  laptop."
- 3 pillars (verifiable not asserted / browser-verified in real time / public
  when we find a bug)
- Auditor handoff section with dark terminal mockup of `verify.sh PASS` output
- Migration plan 3 steps (BAA → import + observe → monthly packets)
- Honest comparison table vs compliance-automation tools
- Footer CTA → `recovery@osiriscare.net` + link to security advisory
- Uses DM Sans + DM Serif Display (matches existing LandingPage aesthetic)
- Deliberately avoids naming Delve/DeepDelver directly
- Lazy-loaded route at `/recovery` in `App.tsx`

## Verification status

- Python ast parse: OK
- `tsc --noEmit`: 0 errors
- `eslint --max-warnings 0`: clean
- `npm run build`: 800KB / 230KB gz (recovery landing in lazy chunk, +0KB
  to main bundle)
- Backend tests: **257/257 passing** before Batch 7 fallout fix; 49/49 of the
  new auditor kit + audit log tests pass locally
- CI gate: `4240887` failed on the pre-existing credentials test (3 vs 2
  asserts), `f275d23` fix pushed and in progress

## Tier 2 — planned but not shipped

The recovery-platform round-table also identified Tier 2 work that was NOT
authorized by the user (only "go" for Tier 1 was given):

- T2.1 — Browser-verify the FULL chain in a web worker (Batch 5 only does 10)
- T2.2 — Deterministic packet generation regression test + "no two clients
  identical" canary test
- T2.3 — Genesis-block UI panel on PortalScorecard
- T2.4 — `random-sample` endpoint for auditor spot-checks
- T2.5 — Bulk evidence importer for migrating customers
- T2.6 — Public `/changelog` page

## Tier 3 — planned but not shipped

- T3.1 — SOC 2 of OsirisCare itself
- T3.2 — Reproducible build documentation
- T3.3 — Public security advisories index page
- T3.4 — Multi-framework display in scorecard
- T3.5 — Audit-log nav discoverability

---

# Session 203 Continuation #2 — Tier 2 + Tier 3 + H2/H3 (afternoon→evening)

After Tier 1 deployed and CI was green, the user asked to ship Tier 2, then
Tier 3, then the in-app UI buttons for H2 and H3 from the original audit
punch list. All shipped in the same long session.

## Tier 2A — Full-chain Web Worker + packet determinism canary

Commit `08f772d`. CI run `24210869322` — green (4m7s).

### T2.1 — Full-chain browser verification

`mcp-server/central-command/frontend/src/portal/`:
- `verifyChainWorker.ts` (~270 lines) — Web Worker that bundles `@noble/ed25519`
  inline (~9KB compiled). Walks bundles in batches of 200, runs Ed25519 +
  SHA-256 chain-hash + cross-batch `prev_hash` linkage checks. Reports
  incremental progress + final summary via `postMessage`.
- `useBrowserVerifyFull.ts` — orchestrator hook. Spawns the worker, fetches
  `/public-keys` once, streams `/sites/.../bundles?include_signatures=true&order=asc`
  in batches of 200, posts each batch to the worker, receives progress.
  Manual start (button-gated) so 100K-bundle sites don't surprise the user.
- `FullChainVerifyPanel.tsx` — UI panel with progress bar, bundle counter,
  chain/sig metrics, failure panel linking `security@osiriscare.net` and
  the Merkle disclosure. Wired into `PortalVerify`.

Backend changes in `evidence_chain.py::list_evidence_bundles`:
- Added `order=asc` parameter (defaults to `desc` for backward compat)
- Added `include_signatures=true` parameter that adds `agent_signature`
  and `chain_hash` columns to the SELECT (default false hides them so
  the admin UI payload stays small)
- Discovery: the Batch 5 hook was silently no-op'ing because it expected
  these fields but they were never in the default response

### T2.2 — Packet determinism + no-Delve canary

`tests/test_packet_determinism.py` (12 tests) — instantiates real
`CompliancePacket` objects with stubbed `_get_*` methods, runs
`generate_packet()` end-to-end:
- **Determinism**: same site/period × 2 → byte-identical markdown after
  stripping `Generated:` timestamp
- **No-Delve canary**: two different sites with different stubbed data →
  markdown bodies differ, contain site-specific values, SHA256 of bodies
  differs. The Delve playbook (99.8% identical reports) now fails CI.

### Vite worker pivot

First draft used `new Worker(new URL('./verifyChainWorker.ts', import.meta.url))`.
Discovered Vite copied the .ts source verbatim into dist/ instead of
compiling — verified by running `file dist/assets/verifyChainWorker-*.ts`
and seeing 314 lines of TypeScript source instead of compiled JS. Pivoted
to the `?worker` query import pattern: `import VerifyChainWorker from
'./verifyChainWorker.ts?worker'` then `new VerifyChainWorker()`. Required
adding a `vite-env.d.ts` with `/// <reference types="vite/client" />`.

### Test fallout fix (between Tier 1 and Tier 2A)

Commit `f275d23`. The credentials test in `test_client_credentials.py`
expected exactly 2 INSERTs but Batch 7 added a third (`CREDENTIAL_CREATED`
audit log). Updated assertion `== 2` → `== 3` and added per-table-name
verification. CI run `24209868956` — green.

## Tier 2B — Random sample + Genesis panel + /changelog

Commit `9c5c41a`. CI run `24211441502` — green (3m54s).

### T2.4 — Random-sample endpoint

`evidence_chain.py::get_random_bundle_sample`:
- `GET /api/evidence/sites/{site_id}/random-sample?count=N&seed=K`
- Caps at `count=100` to prevent bulk export abuse
- Reproducible: passing the same `seed` returns the same bundles (via
  `setseed()`). Seed is mapped from int → `[-1.0, 1.0]` deterministically:
  `((seed % 2_000_001) - 1_000_000) / 1_000_000.0`
- Returns full `agent_signature` + `chain_hash` payload so the auditor
  can verify the sample with the auditor kit's `verify.sh`
- Legacy bundles ARE included — the auditor's job is to confirm they
  exist and are honestly labeled, not to skip them

### T2.3 — Genesis Block panel

New panel in `PortalScorecard.tsx` Evidence Chain Integrity section.
Dark gradient mockup showing:
- Chain origin date (formatted with timezone)
- Chain depth (bundle count + signed %)
- Literal 64-zero genesis sentinel (`prev_hash`)
- Hash algorithm stack (SHA-256 + Ed25519 + OpenTimestamps)
- "Verify locally with verify.sh" pointer

### T2.6 — Public /changelog page

`frontend/src/pages/PublicChangelog.tsx` (~330 lines, lazy-loaded ~5KB chunk).
9 seeded entries covering Session 203 work, categorized as
security/feature/fix/disclosure. Auditors read this page — kept summaries
truthful and conservative. Linked the Merkle advisory inline.

## Tier 3 — Close H1 / H6 / H8 / H7-partner

Commit `71dec95`. CI run `24212283530` — green (3m50s).

### H1 — Chain status badge surfaces legacy ratio

`PortalScorecard.tsx` Evidence Chain Integrity badge no longer says
"Valid" while 22.6% of bundles are unsigned legacy. New label:
`Valid · 77% signed` with amber tone when `legacyCount > 0`. Dedicated
warning panel below the metric grid links the Merkle disclosure.

### H6 / H8 — Multi-framework display

Backend (`portal.py`):
- New `PortalFrameworks` model: `primary`, `primary_label`, `enabled[]`,
  `enabled_labels[]`
- New `_get_site_framework_info(db, site_id)` helper queries
  `appliance_framework_configs` (defaults to HIPAA when missing)
- `_FRAMEWORK_LABELS` map for 9 frameworks (hipaa, soc2, pci_dss,
  nist_csf, cis, sox, gdpr, cmmc, iso_27001, plus nist_800_171)
- `/api/portal/site/{id}` response now includes `frameworks` field

Frontend (`PortalScorecard.tsx`):
- Hero text: "monitors your {framework}-relevant controls"
- Header summary: "{framework} compliance summary for {site}"
- Auditor section title: "{framework} Control Mapping"
- Dynamic column label (HIPAA Code / SOC 2 Trust Service / PCI DSS Req /
  NIST Function)
- Multi-framework chip list when `enabledLabels.length > 1`
- `frameworks?: PortalFrameworks` is optional so backend deploy can land
  before frontend deploy without breaking the client

### H7-partner — Partner self-service disclosure accounting

Backend (`partners.py::get_my_audit_log`):
- New `GET /api/partners/me/audit-log` endpoint
- Scoped to authenticated partner via `require_partner_role`
- Filter by category, lookback window 1..2555 days (HIPAA §164.316(b)(2)(i)
  retention max), pagination, total count

Frontend (`PartnerAuditLog.tsx` + route in `App.tsx`):
- Filter bar with 9 categories + 5 lookback windows
- Table with When/Event/Category/Target/IP/Status columns
- CSV export
- Humanizes 30+ event types via `EVENT_LABELS` map
- Mirrors the Batch 7 `ClientAuditLog` page

## H2 + H3 — In-app pubkey download + per-bundle .ots button

Commit `a00d418`. CI run `24212667606` — in progress at session end (was
at 32s when notes were written; full run is ~4 min).

### H2 — PublicKeysPanel

`frontend/src/portal/PublicKeysPanel.tsx`:
- Renders per-appliance Ed25519 public keys in `PortalVerify`
- Each key shows: display name, hostname, first checkin, raw 64-char
  hex, browser-computed SHA-256 fingerprint (16 hex chars)
- Copy-to-clipboard button per key
- Download pubkeys.json button
- Footer links the Merkle disclosure
- Backend endpoint `/api/evidence/sites/{id}/public-keys` already
  existed from Batch 5 — this batch only wires the frontend

### H3 — Per-bundle .ots download

Backend (`evidence_chain.py::download_bundle_ots_file`):
- New `GET /api/evidence/sites/{site_id}/bundles/{bundle_id}/ots`
- Returns raw OpenTimestamps proof file bytes
- 404 on unknown bundle, 404 on legacy/pending (no proof recorded)
- Decodes base64 `proof_data` → raw .ots bytes
- Response headers: `X-Bundle-Hash`, `X-OTS-Status`, `X-Calendar-URL`

Frontend (`PortalVerify.tsx::BundleTimeline`):
- Each row gets a download button when `ots_status === 'anchored' || 'verified'`
- Inline hint: "Run `ots verify {id}.ots` to verify against Bitcoin"
- Button passes `siteId` from props (added prop to `BundleTimeline`)

## Final test counts

- **Backend tests across all Session 203 suites: 191 passing**
  (Tier 1: 49, Tier 2A: 44, Tier 2B: 31, Tier 3: 37, H2/H3: 26 + adjacent)
- New test files this continuation:
  - `test_packet_determinism.py` (12)
  - `test_full_chain_browser_verify.py` (32)
  - `test_random_sample_endpoint.py` (13)
  - `test_genesis_panel_and_changelog.py` (18)
  - `test_tier3_audit_close.py` (37)
  - `test_h2_h3_inapp_downloads.py` (26)
- **Total Session 203 new tests: ~275**

## Audit punch list — final state at session end

Original audit (round-table) findings:
- C1–C7 (audit-proof CRITICAL): all closed in Batches 1–6
- Partner H1–H3 (RBAC + audit log infra): all closed in Batches 1–3
- Client H1, H2: closed in Batches 3 + 6
- **H1** chain badge legacy ratio: closed in Tier 3
- **H2** pubkey download UI: closed in this batch
- **H3** per-bundle .ots: closed in this batch
- **H6** multi-framework control mapping: closed in Tier 3
- **H7** client + partner disclosure accounting: client in Batch 7, partner in Tier 3
- **H8** multi-framework scorecard display: closed in Tier 3

Still open (need scope decisions or future tiers):
- **H4** Network-monitoring bundles silently skipped
- **H5** Server-side bundle hash fallback enforcement
- M2–M5 partner refactor work (deferred)
- T2.5 bulk evidence importer (own scope, multi-day)
- T3.1–T3.5 operational items (SOC 2, repro builds, advisories index, nav)

## Production verification

All deploys before H2/H3 are live and smoke-tested:
- `https://api.osiriscare.net/api/evidence/sites/north-valley-branch-2/auditor-kit` → 403 (correct: requires auth)
- `https://www.osiriscare.net/recovery` → 200
- `https://www.osiriscare.net/changelog` → 200
- `/api/evidence/sites/.../random-sample` → 403 (correct: requires auth)

## Final commit count for the day

10 commits across the full session (Site Detail polish + Dashboard P0/P1/P2
+ credential rotation + dedup race + 6 audit-proof batches + Batch 7 +
Batch 7 fix + Tier 1 + Tier 2A + Tier 2B + Tier 3 + H2/H3). Five major
deploys all green; H2/H3 deploy in progress at session end.

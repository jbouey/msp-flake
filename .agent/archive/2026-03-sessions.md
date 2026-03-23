# Session Archive - 2026-03


## 2026-03-01-session-145-agent-mgmt-plane-self-healing-winrm-dns-chaos-resilience.md

# Session 145 - Agent Management Plane Self-Healing + Chaos Resilience

**Date:** 2026-03-01
**Started:** 12:42
**Previous Session:** 144

---

## Goals

- [x] Complete 4 remaining gaps (agent sync, healing order signing, AD trust, version stamping)
- [x] Add management plane self-healing to Go agent (WinRM + DNS checks)
- [x] Rebuild and stage agent binary with new checks
- [ ] Deploy updated agent to ws01 (blocked: WinRM auth broken by chaos lab)

---

## Progress

### Completed

1. **Agent sync to VPS go_agents table** — Daemon includes connected agents in checkin, backend upserts into go_agents (ea35e77)
2. **Healing order host scope fix** — Orders now signed with canonical appliance_id (site_id + MAC) instead of UUID (ea35e77)
3. **Version stamping** — Makefile uses `git describe`, agent shows commit hash in logs (ea35e77)
4. **Management plane self-healing** — New `winrm` and `dns_service` checks + healing handlers (31a33b2)
5. **Agent binary staged** — v31a33b2 at `/var/lib/msp/agent/osiris-agent.exe` on physical appliance

### Blocked

- **ws01 agent down** — Self-update swapped binary but restart script failed (WinRM auth chaos)
- **WinRM NTLM auth** — Both DC and ws01 rejecting NTLM, Negotiate-only after reboot
- **iMac intermittent** — Can't check/fix chaos crons when unreachable

---

## Files Changed

| File | Change |
|------|--------|
| `appliance/internal/daemon/phonehome.go` | ConnectedAgent struct + CheckinRequest field |
| `appliance/internal/daemon/daemon.go` | Populate agent data from registry in runCheckin |
| `mcp-server/central-command/backend/sites.py` | ConnectedAgentInfo model + Step 3.7 upsert |
| `mcp-server/main.py` | Canonical appliance_id for healing order signing |
| `agent/Makefile` | Git-based VERSION + GIT_COMMIT ldflags |
| `agent/cmd/osiris-agent/main.go` | Version="dev", GitCommit var |
| `agent/internal/checks/winrm.go` | **NEW** WinRM compliance check |
| `agent/internal/checks/dns.go` | **NEW** DNS service compliance check |
| `agent/internal/checks/checks.go` | Register WinRMCheck + DNSCheck |
| `agent/internal/healing/executor.go` | healWinRM + healDNS handlers |
| `appliance/internal/grpcserver/server.go` | healMap + EnabledChecks for winrm, dns_service |

[truncated...]

---

## 2026-03-01-session-146-companion-alerts-feature-winrm-ws01-auth.md

# Session 146 - Companion Compliance Alerts + ws01 WinRM Auth

**Date:** 2026-03-01
**Started:** 15:29
**Previous Session:** 145

---

## Goals

- [x] Build companion portal alert system for HIPAA module deadline tracking
- [x] Deploy alerts feature (commit, push, apply migration)
- [x] Fix alert button not working in production
- [x] Fix ws01 WinRM auth (NTLM not offered, only Negotiate/Kerberos)
- [x] Add companion document upload/download endpoints
- [x] Deploy updated daemon with Basic auth WinRM to physical appliance

---

## Progress

### Completed

**Companion Compliance Alerts Feature (SHIPPED)**

Full-stack feature: companions set deadline alerts on HIPAA modules per client.

- Migration `066_companion_alerts.sql` — status lifecycle (active → triggered → resolved/dismissed)
- 5 CRUD endpoints in companion.py + `_evaluate_module_status()` + background check loop (6h)
- Email notifications via `send_companion_alert_email()` (teal-branded, 24h dedup)
- Background task registered in main.py lifespan()
- 5 React Query hooks in useCompanionApi.ts
- Alert indicators on module cards (CompanionClientDetail.tsx)
- Set Alert form in CompanionModuleWork.tsx
- Overdue badge on client list (CompanionClientList.tsx)
- Committed `06db58e`, pushed, CI/CD deployed
- Migration manually applied on VPS (was the root cause of alert button not working)

**Companion Document Upload/Download (SHIPPED)**

Tasha reported IR Plan section attachments not loading in companion portal. Root cause: companion backend had no document endpoints — only the client portal did.

- 4 new endpoints in `companion.py` (list, upload, download, delete) with `require_companion` auth
- MinIO storage integration with presigned URLs (15-min expiry)
- Soft-delete for regulatory compliance
- Fixed `detail` vs `details` kwarg bug in `_log_activity`
- Fixed `user` vs `user["id"]` parameter bug
- Committed `49b10c0`, pushed, CI/CD deployed

**ws01 WinRM Auth (FIXED)**

[truncated...]

---

## 2026-03-01-session-147-chaos-lab-timing-fix-agent-deploy.md

# Session 147 - Chaos Lab Timing Fix + Agent Deploy

**Date:** 2026-03-01
**Started:** 20:07
**Previous Session:** 146

---

## Goals

- [x] Analyze healing pipeline timing — is HEALING_WAIT_SECONDS sufficient?
- [x] Check if flap detection fires during cumulative campaigns
- [x] Commit chaos lab + agent changes
- [x] Build and deploy agent binary

---

## Progress

### Completed

1. **Healing pipeline timing analysis**
   - Traced full chain: PollInterval(60s) → driftScanInterval(15min) → L1(<100ms) → execute
   - **Root cause of low healing rates (12-31%):** `HEALING_WAIT_SECONDS=720` (12 min) < `driftScanInterval=15min`
   - Chaos lab was verifying BEFORE the appliance even scanned for drift
   - Fixed: bumped to `HEALING_WAIT_SECONDS=1200` (20 min) in config.env on iMac

2. **Flap detection analysis**
   - Confirmed flap detection CANNOT fire during campaigns:
     - Each scenario targets a different category → different cooldown keys
     - `defaultCooldown=10min` < `driftScanInterval=15min` → count always resets to 1
     - Agent gRPC and appliance drift scan use different check_type strings → no cross-source collision
   - Flap detection is structurally unable to reach threshold=3

3. **Committed agent changes** (`13ba93a`)
   - `winrm.go`: WinRM check now verifies Basic auth GPO policy (AllowBasic=1)
   - `executor.go`: healWinRM restores Basic auth + AllowUnencryptedTraffic GPO registry keys

4. **Built + deployed agent binary**
   - `make build-windows-nocgo` → `osiris-agent-nocgo.exe` (12.5MB)
   - SCP'd to `/var/lib/msp/agent/osiris-agent.exe` on appliance (192.168.88.241)
   - Updated VERSION file to `13ba93a`
   - Verified: appliance serves new version at `:8090/agent/version.json`
   - Autodeploy will stage to NETLOGON within 1 hour

### Key Timing Constants (reference)

| Constant | Value | Location |
|----------|-------|----------|
| PollInterval | 60s | daemon/config.go |

[truncated...]

---

## 2026-03-02-session-148-org-features-dbos-durability.md

# Session 148 - Org Features + DBOS Durability

**Date:** 2026-03-02
**Started:** 10:51
**Previous Session:** 147
**Commit:** d9f1fbb — deployed via CI/CD (56s)

---

## Goals

- [x] Complete org-level features (phases 1-5) from approved plan
- [x] Implement DBOS durability patterns in Go daemon (5 tasks)
- [x] Commit, push, deploy

---

## Progress

### Completed

**Organization-Level Features (Phases 1-5)**
1. Wire Org→Site: migration 067, sites.py JOIN, routes.py org endpoints, Sites.tsx grouping
2. Aggregated Org Dashboard: OrgDashboard.tsx (KPI row, compliance chart, sites table)
3. Org-Level Roles: migration 068, auth.py org_scope, query-level filtering
4. Cross-Site Evidence Bundles: ZIP endpoint, download button
5. Shared Credential Vault: migration 069, org_credentials.py CRUD, checkin merge

**DBOS Durability Patterns**
1. Persistent healing journal (healing_journal.go) — crash-safe checkpoints
2. Persistent cooldowns (state.go) — survive restarts
3. Queued telemetry with retry (telemetry_queue.go) — file-backed queue
4. WinRM context cancellation (executor.go) — ExecuteCtx wrapper
5. Per-order timeout enforcement (healing_executor.go) — 5min L1, 10min orders

### Blocked

- Migrations 067-069 need to be run on VPS database manually
- Go daemon binary not deployed to appliances yet (needs fleet order after next build)

---

## Files Changed

| File | Change |
|------|--------|
| appliance/internal/daemon/healing_journal.go | NEW: crash-safe healing journal |
| appliance/internal/l2planner/telemetry_queue.go | NEW: file-backed telemetry retry queue |
| appliance/internal/daemon/daemon.go | Wire journal, cooldown persistence, telemetry drain |
| appliance/internal/daemon/healing_executor.go | Journal calls, per-order timeouts, ExecuteCtx |

[truncated...]

---

## 2026-03-03-session-149-fleet-order-error-handling-ws01-gpo.md

# Session 149: Fleet Order Error Handling + ws01 GPO

**Date:** 2026-03-03
**Focus:** Fleet order signing fixes, update_daemon handler, ws01 GPO verification

## Completed

### 1. Fixed NixOS Self-Scan False Positives (v0.3.12)
- **Root cause:** Both appliances scanning themselves (NixOS) — produced 12 false positives per scan cycle across 6 Linux check types
- Removed `scanLinuxSelf()` from `linuxscan.go` — remote Linux scanning via SSH still works
- Added port 80 (`http-file-server`) to `expectedPorts` in `netscan.go`
- Deployed v0.3.12 to both physical (192.168.88.241) and VM (192.168.88.254) appliances via SCP

### 2. Fleet Order Error Handling (v0.3.13)
Three improvements to prevent the base64-vs-hex signing bug from recurring:

**a) Better signature error messages** (`appliance/internal/crypto/verify.go`)
- Detects base64-encoded signatures and provides clear diagnostic: "use signature.hex() in Python, not base64.b64encode()"
- Shows expected vs actual character count in all error paths

**b) `update_daemon` order handler** (`appliance/internal/orders/processor.go`)
- 18th registered order type
- Downloads binary from URL (validated against domain allowlist, HTTPS required)
- Verifies SHA256 hash before writing
- Writes to `/var/lib/msp/appliance-daemon` via atomic tmp+rename
- Creates systemd override (persistent or runtime fallback)
- Runs `systemctl daemon-reload` + schedules restart in 10s
- Parameters: `binary_url`, `binary_sha256`, `version`

**c) Hex format validation** (`mcp-server/central-command/backend/order_signing.py`)
- `_validate_signature_hex()` asserts exactly 128 lowercase hex chars
- Applied to both `sign_admin_order()` and `sign_fleet_order()`
- Catches misconfiguration immediately on the Python side

### 3. DC Recovery
- Fixed EventLog service (Stopped/Disabled by chaos lab) → enabled ADWS
- Created GPO "OsirisCare-WinRM" linked to domain root
- Startup scripts: Setup-WinRM.ps1, Deploy-Agent.ps1, psscripts.ini, scripts.ini
- Staged osiris-agent.exe (12MB) + osiris-config.json to NETLOGON
- Reset ws01 machine account password in AD

### 4. ws01 Status
- ws01 rebooted, pingable at 192.168.88.251
- **BLOCKER:** Trust relationship broken — ws01 can't authenticate to domain
- GPO scripts in SYSVOL ready but won't apply until trust restored
- Admin share accessible from DC (C:\OsirisCare does not exist yet)

## Files Changed
- `appliance/internal/daemon/linuxscan.go` — removed scanLinuxSelf(), findBash, bashCandidates
- `appliance/internal/daemon/netscan.go` — port 80 in expectedPorts

[truncated...]

---

## 2026-03-03-session-150-update_daemon systemd-run sandbox fix v0.3.14.md

# Session 150 - update_daemon systemd-run Sandbox Fix (v0.3.14)

**Date:** 2026-03-03
**Started:** 08:49
**Previous Session:** 149

---

## Goals

- [x] Fix update_daemon handler to work on NixOS (ProtectSystem=strict sandbox)
- [x] Deploy v0.3.14 to both appliances
- [x] End-to-end fleet order test
- [x] NixOS rebuild to bake v0.3.14 into nix store permanently
- [x] Fleet order CLI tooling — create/list/cancel with signing, Mac wrapper, e2e tested
- [ ] Fix DC clock (checked — likely fine, WinRM Kerberos works)
- [ ] Verify ws01 agent enrollment (agent not running — port 50051 closed on ws01)

---

## Progress

### Completed

- Fixed systemd override install: use systemd-run to escape ProtectSystem=strict sandbox
- Fixed NixOS bash path: `/run/current-system/sw/bin/bash` (not `/bin/bash`)
- Fixed systemd-run PATH: transient units have minimal PATH, set via --setenv
- Added `api.osiriscare.net` to allowedDownloadDomains
- Deployed v0.3.14 to physical + VM appliances + VPS updates dir
- Fleet order end-to-end verified: download -> SHA256 -> override -> restart -> success
- NixOS rebuild fleet order: both appliances rebuilt, `nixos-rebuild switch` persisted
- Removed runtime systemd overrides — now running nix store binary directly
- Nix store confirms: `/nix/store/0c51c3mnxwvw7qd3v0k9wxqp9cgsvcsy-appliance-daemon-0.3.14`
- Cancelled stale fleet orders, committed ec8633d (nix derivation bump)
- Fleet order CLI tool: `fleet_cli.py` (create/list/cancel with Ed25519 signing)
- Mac wrapper: `scripts/fleet-order.sh` (SSH → docker exec → fleet_cli.py)
- End-to-end verified: create force_checkin → both appliances completed → cancel

### Findings

- **DC clock**: Likely fine. WinRM session to DC works (Kerberos requires <5min skew). Drift scan 0 drifts. The 0x8009030d error on DC->ws01 is SEC_E_LOGON_DENIED, not time skew (0x80090324).
- **ws01 agent**: Port 50051 CLOSED on ws01 (192.168.88.251). WinRM (5985) and SMB (445) open. Agent service likely crashed or never started properly after manual deploy in Session 149.
- **NixOS watchdog gap**: `nixos-rebuild test` succeeds and daemon reports success, but watchdog can't persist with `switch` because the marker file (.rebuild-in-progress) gets cleaned up before watchdog runs. Need manual `nixos-rebuild switch` or fix the marker lifecycle.

---

## Files Changed

| File | Change |
|------|--------|

[truncated...]

---

## 2026-03-04-session-151-App protection profiles feature - full stack implementation.md

# Session 151 - App Protection Profiles Feature — Full Stack Implementation

**Date:** 2026-03-04
**Started:** 19:18
**Previous Session:** 150

---

## Goals

- [x] Design application protection profile architecture
- [x] Implement database schema (migration 070)
- [x] Build backend API (13 endpoints)
- [x] Add Go daemon discovery handler
- [x] Add 6 new parameterized runbooks
- [x] Build frontend pages (list + detail)
- [x] Add companion portal read-only section
- [x] Deploy backend + frontend via CI/CD
- [x] Run migration 070 on VPS
- [x] Build and deploy Go daemon v0.3.16

---

## Key Decision

**Auto-generated L1 rules** instead of custom runbook packages. Discovery identifies assets → baseline captures golden state → L1 rules auto-generated with parameters pointing to existing runbooks. Reuses entire existing rule sync and healing infrastructure.

## Progress

### Completed

- Designed full architecture: discovery → baseline → L1 rule generation → sync
- Created migration 070: 4 tables (profiles, assets, rules, templates) + 5 EHR template seeds
- Built 822-line backend router with 13 endpoints (CRUD, discover, lock-baseline, pause/resume)
- Added `app_discovery.go`: PowerShell discovery handler for services, ports, registry, tasks, config files
- Added 6 parameterized runbooks: config integrity, TCP, IIS, ODBC, service recovery, process health
- Built frontend list + detail views with discovery progress, asset toggles, baseline lock flow
- Added companion portal read-only protected apps section
- Committed `51a3963`, CI/CD deployed
- Migration 070 applied on VPS
- Go daemon v0.3.16 built and deployed via fleet order `88f3139e`
- Also included from prior session: autodeploy Kerberos fix + daemon restart sandbox escape (`a4edc3b`)

### Blocked

None

---

## Files Changed

[truncated...]

---

## 2026-03-06-session-152-Agent enrollment pipeline fix, reconnect logic, driftscan credential fix.md

# Session 152 - Agent Enrollment Pipeline Fix, Reconnect Logic, Driftscan Credential Fix

**Date:** 2026-03-06
**Started:** 00:23
**Previous Session:** 151

---

## Goals

- [ ]

---

## Progress

### Completed


### Blocked


---

## Files Changed

| File | Change |
|------|--------|

---

## Next Session

1.

---

## 2026-03-06-session-153-CI-pipeline-fix-test-coverage-kerberos-trust-chaos-lab.md

# Session 153 — CI Pipeline Fix, Test Coverage, Kerberos Trust, Chaos Lab

**Date:** 2026-03-06
**Started:** 09:22
**Previous Session:** 152

---

## Goals

- [x] Fix CI/CD pipeline — make pytest blocking, add vitest
- [x] Fix all test mock failures (3 test files, 36 failures → 0)
- [x] WinRM credential validation — full stack implementation
- [x] ws01 Kerberos domain trust — rejoin domain
- [x] Chaos lab hardening — prevent future time drift breaking Kerberos
- [x] Research email notifications + evidence bundle export status

---

## Progress

### Completed

1. **CI/CD pipeline green** (4 commits):
   - Removed `|| true` from pytest, added vitest step
   - Created `requirements.txt` for CI backend deps
   - Fixed test mocks: `dependency_overrides[get_pool]` → `unittest.mock.patch()` (get_pool called directly, not via Depends)
   - Fixed `promote_pattern` import (`dashboard_api._routes_impl`, not `dashboard_api.routes` package)
   - Fixed patch paths: `websocket_manager.broadcast_event`, `order_signing.sign_admin_order`
   - Fixed FakeConn key ordering, auth gating test approach

2. **WinRM credential validation** (full stack):
   - Backend: fleet order queuing in `partners.py`
   - Go daemon: `handleValidateCredential` with WinRM probe + AD read
   - Completion hook: updates `site_credentials.validation_status`

3. **ws01 Kerberos trust fixed**:
   - DC time corrected (was stuck at Jan 13 snapshot date)
   - Deleted stale NVWS01 AD object, rejoined ws01 to domain

4. **Chaos lab hardened**:
   - ForceTimeSync scheduled task on DC (boot-time `w32tm /resync /force`)
   - Fresh snapshot: `post-kerberos-fix-2026-03-06`
   - W32Time/NTP added to CRITICAL EXCLUSION in `generate_and_plan_v2.py`
   - All 5 lab VMs started

5. **Research**:
   - Email: Fully configured, all 7 flows wired, SMTP working
   - Evidence: Org ZIP works but queries wrong table (`evidence_bundles` vs `compliance_bundles`)


[truncated...]

---

## 2026-03-06-session152-agent-pipeline-v0.3.18.md

# Session 152: Agent Pipeline Fix + v0.3.18 Deploy

**Date:** 2026-03-06
**Focus:** Fix full agent enrollment pipeline, deploy v0.3.18 to both appliances

## Completed

### Agent Reconnect Logic
- Added `reconnectLoop()` with exponential backoff (30s → 5min) in `agent/cmd/osiris-agent/main.go`
- Added `tryRegisterAndSetup()` helper for registration flow
- Agent no longer runs offline forever if initial gRPC connect fails

### go_agents VPS Sync
- Fixed timestamp parsing in `sites.py` — asyncpg requires naive datetime, not offset-aware
- Added `_parse_ts()` helper: strips timezone info from ISO strings for `timestamp without time zone` columns
- ws01 agent `go-NVWS01-47d98ba3` now visible in go_agents table on VPS

### Autodeploy Version-Aware Probe
- `autodeploy.go` probe script now checks binary version + config correctness, not just service status
- Previously, `RUNNING` status caused skip even with stale binary/config
- New probe returns `STALE|ver=X|addr=Y` when version or config mismatch

### Pure-Go SQLite
- Replaced `github.com/mattn/go-sqlite3` (requires CGO) with `modernc.org/sqlite` (pure Go)
- Agent offline queue now cross-compiles for Windows without CGO toolchain
- Driver name changed from `"sqlite3"` to `"sqlite"`

### Driftscan Credential Fix
- Fixed hostname/IP mismatch in `driftscan.go` workstation target building
- Credentials stored by IP but lookups by AD hostname — added DNS resolution fallback
- `net.LookupHost()` resolves hostname → IP, then retries `LookupWinTarget()` with IP

### Agent Logging Fix
- Reordered `io.MultiWriter` args: `logFile` first, `os.Stderr` second
- Windows services have no valid stderr handle — first writer failing killed all logging
- Agent.log was 0 bytes on ws01 due to this

### Config BOM Encoding Fix
- PowerShell `ConvertTo-Json | Set-Content -Encoding UTF8` adds BOM
- Go JSON parser fails on BOM: `invalid character '�'`
- Fixed autodeploy to use `[System.IO.File]::WriteAllText()` with `UTF8Encoding($false)`

### Deployment
- Both appliances updated to v0.3.18 (daemon + driftscan fixes)
- VM appliance updated via SCP + systemd override
- Physical appliance updated via fleet order
- All changes pushed to main (commit 7b9fe91), CI/CD deployed to VPS

## Key Bugs Found
1. Agent had zero reconnect logic — offline forever on initial failure

[truncated...]

---

## 2026-03-06-session152-anti-slop-audit.md

# Session 152: Anti-Slop Audit (Full Codebase)

**Date:** 2026-03-06
**Duration:** Multi-session (context continuation)

## Summary

Comprehensive code quality audit across Go appliance daemon, Python backend, and React frontend. Three phases: quality gates, testing, and traceability.

## Results

### Go Appliance (appliance/)
- **golangci-lint**: 547 → 0 issues
  - errcheck: 50+ unchecked errors fixed (type assertions, Close, json.Unmarshal)
  - noctx: 25 fixes (DialContext, NewRequestWithContext, CommandContext)
  - gocritic: 35 fixes (hugeParam→pointer, ifElseChain→switch, equalFold)
  - staticcheck: 11 fixes (deprecated Execute→ExecuteWithContext, QF1012)
  - gosec: tuned exclusions for fleet/infrastructure patterns
- **maputil package**: New `appliance/internal/maputil/` — typed extractors for `map[string]interface{}`, replaces 50+ silent type assertions with logged mismatches
- **Dead code removed**: 5 items (~120 lines) — verifyAgentPostDeploy, writeB64ChunksToTarget, executeLocal, safeTaskPrefix, allCheckTypes
- **Latent bug fixed**: L2 planner token counts always 0 (JSON float64 vs int assertion)
- **Test fixes**: 3 tests (smb_signing rule, grpcserver check count, WinRM port default)
- **Config**: `.golangci.yml` v2 format with tuned exclusions for tests, PowerShell templates, infrastructure patterns

### Frontend (central-command/frontend/)
- **ESLint**: Installed + configured (flat config v10), 0 errors
- **Fixes**: eqeqeq (2), no-undef (globals added), no-redeclare (type rename), no-useless-escape (1)
- **no-explicit-any**: 29 warnings addressed with proper types (in progress)
- **vitest**: Installed + configured with jsdom, initial test suite (in progress)
- **package.json**: Updated lint + test scripts

### CI/CD (.github/workflows/)
- **deploy-central-command.yml**: Added `test` job as prerequisite to `deploy`
  - Python pytest (non-blocking `|| true`)
  - TypeScript `tsc --noEmit` (blocking)
  - ESLint `--max-warnings 100` (blocking)

### Python Backend (central-command/backend/)
- Integration tests for incident pipeline, checkin, evidence chain (in progress)

### Documentation
- **KNOWN_ISSUES.md**: Updated with full audit results + remaining gaps
- **docs/archive/**: 10 stale/duplicate docs moved (PHASE1-COMPLETE, IMPLEMENTATION-STATUS, etc.)

## Files Changed (Key)
- `appliance/.golangci.yml` — New lint config
- `appliance/internal/maputil/` — New package (maputil.go + maputil_test.go)
- `appliance/internal/daemon/*.go` — maputil migration, pointer params, context threading
- `appliance/internal/l2planner/planner.go` — float64 bug fix, pointer params
- `appliance/internal/sshexec/executor.go` — bytes.Equal, DialContext, Fprintf

[truncated...]

---

## 2026-03-07-session-154-learning-loop-settings-openclaw.md

# Session 154: Learning Loop Fixes, Settings Page, OpenClaw Config

**Date:** 2026-03-07
**Duration:** ~3 hours

## Summary

Fixed three bugs on the Learning Loop page, added five new Settings sections, and reconfigured the OpenClaw server for optimal model usage and origin access.

## Changes Made

### Learning Loop Page (3 bugs fixed)
1. **Invisible badge text** — `bg-level-l1/l2/l3` colors weren't defined in `tailwind.config.js`. Added `level.l1` (green), `level.l2` (orange), `level.l3` (red).
2. **Empty promotion timeline** — Frontend calls `/api/dashboard/learning/history` (routes.py), NOT `/api/learning/history` (main.py). Routes.py queried empty legacy `patterns` table. Fixed to query `learning_promotion_candidates` with execution stats via `execution_telemetry` lateral join.
3. **False coverage gaps (50% → 85%)** — Coverage query only checked `incident_pattern->>'check_type'` but most L1 rules store `incident_type`. Fixed by checking both JSONB keys + fuzzy rule_id matching.

### Settings Page (5 new sections)
- **Default Healing Tier** — standard/full_coverage/monitor_only dropdown
- **Learning Loop** — min success rate, min executions, auto-promote toggle
- **SMTP Configuration** — host, port, from, username, password, TLS toggle
- **Branding** — company name, logo URL, support email
- **Evidence Storage** — MinIO endpoint, WORM bucket, OTS calendar URL, retention days

### OpenClaw Server (178.156.243.221)
- Reconfigured model chain: `haiku → gpt-4o-mini → sonnet` (was haiku → sonnet → ollama)
- Added OpenAI API key as failover provider
- Removed Ollama (kept timing out, cascading failures)
- Lowered concurrency (2 main / 4 subagent)
- Fixed "origin not allowed" error: added `controlUi.allowedOrigins` + `trustedProxies` config
- Skills audit: 92/98 ready, fixed missing frontmatter on debug-pro + trend-watcher

## Commits
- `56d0dca` fix: learning loop page — invisible text + promotion timeline redesign
- `c023b9c` fix: add /api/learning/history endpoint — promotion timeline was empty
- `610438c` fix: add missing learning endpoints + fix coverage gap false negatives
- `6b7df22` feat: settings page — healing tier, SMTP, branding, evidence, learning thresholds
- `3bb3e6a` fix: routes.py learning history queries learning_promotion_candidates

## Key Insight
Frontend API routing: `API_BASE = '/api/dashboard'` means the frontend hits `routes.py` (mounted at `/api/dashboard`), NOT `main.py` endpoints (mounted at `/api`). Adding endpoints to main.py alone is invisible to the dashboard UI.

## Pending
- VM appliance stale — iMac host needs physical wake
- OpenClaw origin fix deployed, user should hard-refresh browser (Cmd+Shift+R)
- clawhub login needed for installing additional skills from hub (rate-limited without auth)

---

## 2026-03-07-session-155-OG-image-incident-type-consistency-audit.md

# Session 155 — OG Image + Incident Type Consistency Audit

**Date:** 2026-03-07
**Started:** 08:42
**Previous Session:** 154

---

## Goals

- [x] Add OG image for iMessage/WhatsApp/social link previews
- [x] Fix title showing "OsirisCare Dashboard" in link previews
- [x] Remove Dashboard link from public landing page
- [x] Fix all incidents showing "Backup drift"
- [x] Full audit of Go daemon check types vs frontend labels

---

## Progress

### Completed

1. **OG Image** — 1200x630 branded PNG with logo, headline, badges. OG + Twitter Card meta tags added.
2. **Title fix** — `<title>` now branded; dashboard users get JS override to keep "OsirisCare Dashboard"
3. **Nav cleanup** — removed Dashboard link from public landing page
4. **Backup drift bug** — CheckType enum had 13 types, Go sends 47. All defaulted to BACKUP. Fixed.
5. **Consistency audit** — 6 files fixed: db_queries, fleet, routes (backend) + types, TopIncidentTypes, IncidentList (frontend)
6. **18 missing labels added** — WMI, Registry Run, Cloud AV, Spooler, WinRM, 9 app protection types

### Blocked

- WhatsApp caches old link previews aggressively — new shares will show correctly

---

## Files Changed

| File | Change |
|------|--------|
| `frontend/index.html` | OG/Twitter meta tags, branded title |
| `frontend/public/og-image.png` | New 1200x630 branded OG image |
| `frontend/src/App.tsx` | JS title override for dashboard users |
| `frontend/src/pages/LandingPage.tsx` | Removed Dashboard nav link |
| `backend/models.py` | Incident.check_type: CheckType -> str |
| `backend/routes.py` | Removed _safe_check_type(), CheckType import |
| `backend/db_queries.py` | Removed hardcoded "backup" fallback |
| `backend/fleet.py` | Removed CheckType enum usage |
| `frontend/src/types/index.ts` | Widened CheckType to string, +18 labels |
| `frontend/src/components/command-center/TopIncidentTypes.tsx` | Use CHECK_TYPE_LABELS |
| `frontend/src/portal/components/IncidentList.tsx` | Use CHECK_TYPE_LABELS |

[truncated...]

---

## 2026-03-07-session-156-Partner portal fixes — pending approval URL, API key regen, email signup.md

# Session 156 — Partner Portal Fixes + Email Signup

**Date:** 2026-03-07
**Previous Session:** 155

---

## Goals

- [x] Fix pending partner approval not visible in admin dashboard
- [x] Fix "Regenerate API Key" not implemented alert
- [x] Add email-based partner signup (non-OAuth providers)
- [x] Add /admin/partners/pending redirect route
- [x] Verify client portal auth (already zero-friction)

---

## Progress

### Completed

1. **Pending partner approval invisible** — Email notification linked to `/admin/partners/pending` which had no frontend route. Pending approvals section exists on `/partners` page but the URL mismatch meant admins navigated to a blank page.
   - Added `<Route path="/admin/partners/pending">` → `Navigate to="/partners"` redirect
   - Fixed email notification link from `/admin/partners/pending` to `/partners`
   - Verified Jeffrey Bouey's record exists in DB with `pending_approval = true`

2. **API key regeneration stub** — `handleCopyApiKey` was a placeholder alert. Backend endpoint `POST /api/partners/{id}/regenerate-key` already existed.
   - Wired frontend button to actual API call with confirmation dialog
   - New key copied to clipboard automatically

3. **Email-based partner signup** — Partners using privateemail.com, ProtonMail, or any non-Google/Microsoft email couldn't self-register.
   - `POST /api/partner-auth/email-signup` endpoint (name, email, company)
   - Creates partner with `pending_approval = true`, notifies admins
   - Duplicate email detection (graceful "already pending" response)
   - PartnerLogin.tsx: "Request partner account" form with success/error states
   - Aligns with zero-friction appliance onboarding model

4. **Client portal verified** — Already uses magic link auth (any email provider). No changes needed.

### From Session 155 (carried over)

5. **L3 Escalation Queue** — PartnerEscalations.tsx built, notifications router mounted
6. **Partner/Client portal audit** — 71 partner + 54 client endpoints verified; compliance router gap fixed
7. **OG image + incident type consistency audit** — 18 missing labels, CheckType enum widened

---

## Auth Flow Summary

| Portal | Auth Methods | Any Email Provider? |

[truncated...]

---

## 2026-03-07-session-157-Portal-auth-hardening-2FA-user-management.md

# Session 157 - Portal Auth Hardening, 2FA, User Management

**Date:** 2026-03-07
**Commits:** cbf20aa, ce89837, 47e39ec, d2b94c5, c785181

---

## Completed

1. Email/password login for partner + client portals (tabbed UI, default tab)
2. Security audit: 4 critical, 7 high, 3 medium fixes (rate limiting, HMAC sessions, bcrypt-only, open redirect, CSRF narrowing, hashed magic tokens)
3. TOTP 2FA for all 3 portals (admin, partner, client) — shared totp.py, MFA pending flow, backup codes
4. Admin Users page: 5 tabs (Users, Invites, Sessions, Audit Log, Security/2FA) + Change Password + email edit

## Files Changed

| File | Change |
|------|--------|
| backend/totp.py | NEW — shared TOTP module |
| backend/migrations/071, 072 | NEW — password_hash, mfa columns |
| backend/partner_auth.py | Email login, TOTP, MFA flow, session_router |
| backend/client_portal.py | TOTP, hashed magic tokens, HMAC, bcrypt-only |
| backend/auth.py | MFA pending login flow |
| backend/users.py | Email update, session mgmt, TOTP endpoints |
| backend/routes.py | verify-totp endpoint |
| backend/rate_limiter.py | Auth endpoint coverage expanded |
| backend/csrf.py | Narrowed exemptions |
| frontend/src/pages/Users.tsx | 5 tabs, change password, 2FA setup |
| frontend/src/utils/api.ts | Session/TOTP/audit API methods |
| frontend/src/partner/PartnerLogin.tsx | Email/password form + TOTP login flow |
| frontend/src/client/ClientLogin.tsx | Email/password form + TOTP login flow |
| frontend/src/partner/PartnerSecurity.tsx | NEW — partner 2FA settings page |
| frontend/src/client/ClientSecurity.tsx | NEW — client 2FA settings page |
| frontend/src/App.tsx | Added /partner/security + /client/security routes |
| frontend/src/partner/PartnerDashboard.tsx | Security nav link |
| frontend/src/client/ClientSettings.tsx | Security (2FA) nav link |
| frontend/src/partner/index.ts | PartnerSecurity export |
| frontend/src/client/index.ts | ClientSecurity export |
| mcp-server/Dockerfile | pyotp dependency |
| mcp-server/main.py | partner session_router registration |

## Tests
- Backend: 199 passed
- Frontend: 89 passed, TypeScript clean

---

## 2026-03-08-session-158-Server-side pagination, dark mode, auth fixes.md

# Session 158 - Server Side Pagination, Dark Mode, Auth Fixes

**Date:** 2026-03-08
**Started:** 04:58
**Previous Session:** 157

---

## Goals

- [ ]

---

## Progress

### Completed


### Blocked


---

## Files Changed

| File | Change |
|------|--------|

---

## Next Session

1.

---

## 2026-03-08-session-158-Server-side-pagination-dark-mode-auth-fixes.md

# Session 158: Server-side Pagination, Dark Mode, Auth Fixes

**Date:** 2026-03-08
**Duration:** ~2 hours (continued from prior context)

## What Was Done

### 1. Partners Page Auth Fix (Critical)
- **Root cause:** `AuthContext.tsx` clears `localStorage.auth_token` on load — app uses cookie-based session auth, not Bearer tokens
- `getToken()` always returned null → `fetchPartners` returned early → `isLoading` stuck at `true` → infinite spinner
- **Fix:** Replaced all Bearer token auth with `credentials: 'same-origin'` (cookie auth) + CSRF tokens for mutations
- Affected all 15+ fetch functions in Partners.tsx

### 2. Partner Detail Crash Fix
- `GET /api/partners/{id}` returned 500: `column "site_id" does not exist` in incidents query
- `incidents` table has no `site_id` column — must join through `site_appliances`
- Fixed query to JOIN via `site_appliances.id = incidents.appliance_id`

### 3. View Logs Order Type Mismatch
- Frontend sent `view_logs` but backend `OrderType` enum only has `collect_logs`
- Synced all 16 backend order types to frontend `OrderType` type

### 4. Sites List Limit Validation
- Backend limited `limit` query param to 100, but Incidents page site dropdown sends `limit=200`
- Raised to 500 (admin-only endpoint)

### 5. Dark Mode
- CSS custom properties for all theme colors (light/dark)
- `useTheme` hook: reads localStorage, respects system preference, real-time media query listener
- Three modes: System / Light / Dark in Settings > Display
- iOS dark palette: pure black background, dark glass surfaces, inverted labels

### 6. Settings Page Auth Fix
- Same Bearer token bug as Partners — fixed to cookie auth + CSRF

### 7. Healing Tier Labels
- Removed hardcoded rule counts "(4 rules)" / "(21 rules)" from SiteDetail dropdown
- Now just shows "Standard" and "Full Coverage"

### 8. Users All Accounts Fix (from prior context)
- Removed `mfa_enabled` from `client_users` query — column doesn't exist on VPS (migration 072 never ran)

## Commits
- `6ecd586` fix: use array index instead of .at() for CI TS target compat
- `b0bd2e7` perf: fix Partners double-fetch on mount, use cookie auth
- `a6b1233` fix: remove mfa_enabled from client_users query
- `35bdd61` fix: restore Bearer auth in Partners fetchPartners (wrong fix)
- `3d808c9` fix: Partners page auth — use cookie auth (correct fix)
- `f5351e9` feat: dark mode with iOS-style theme + fix Settings auth
- `d5221cc` fix: View Logs order type mismatch + sites limit validation

[truncated...]

---

## 2026-03-08-session-159-Frontend design overhaul - glassmorphism, dark mode, sidebar fleet status.md

# Session 159 - Frontend Design Overhaul   Glassmorphism, Dark Mode, Sidebar Fleet Status

**Date:** 2026-03-08
**Started:** 06:14
**Previous Session:** 158

---

## Goals

- [ ]

---

## Progress

### Completed


### Blocked


---

## Files Changed

| File | Change |
|------|--------|

---

## Next Session

1.

---

## 2026-03-08-session-159-Frontend-design-overhaul.md

# Session 159: Frontend Design Overhaul — Glassmorphism, Dark Mode, Sidebar Fleet Status

**Date:** 2026-03-08
**Status:** Completed

## Summary

Major frontend design pass covering glassmorphism, dark mode fixes, new shared components, and sidebar redesign.

## Changes Made

### 1. Real Glassmorphism (index.css)
- Added `backdrop-filter: blur(24px) saturate(180%)` to `.glass`, `.glass-sidebar`, `.glass-header`
- Lowered opacity: dark `--glass-bg: rgba(28,28,30,0.55)`, light `--glass-bg: rgba(255,255,255,0.6)`
- Added rich `content-atmosphere` with 4 radial gradients behind content area
- Added `--accent-primary` CSS variable for both light/dark

### 2. Dark Mode Fixes (~190 replacements across 21 files)
- Replaced all hardcoded `bg-white`, `bg-slate-*`, `bg-gray-*` with theme-aware tokens
- Token mapping: `bg-white` → `bg-background-secondary`, `bg-slate-50` → `bg-fill-tertiary`, `text-gray-500` → `text-label-tertiary`
- Files: IncidentRow, CommandBar, ResolutionBreakdown, TopIncidentTypes, IncidentTrendChart, AddDeviceModal, IdleTimeoutWarning, RunbookDetail, ClientCard, PatternCard, SensorStatus, Header, Notifications, AuditLogs, FleetUpdates, Runbooks, RunbookConfig, SiteDetail, NotificationSettings, Documentation

### 3. Stagger Animation Fix
- `opacity: 0` + `animation-fill-mode: forwards` doesn't re-trigger on React re-renders
- Fixed: changed to `animation-fill-mode: both` (no separate opacity setting)
- Incidents page rows were invisible due to this bug

### 4. New Shared Components
- `StatCard.tsx` — KPI card with sparkline and trend arrow
- `Toast.tsx` — ToastProvider context + useToast hook
- `Modal.tsx` — Standardized modal with ESC/backdrop close
- `DataTable.tsx` — Generic sortable table with type-safe columns
- `FormInput.tsx` — Accessible input with label/error states

### 5. Sidebar Redesign: Fleet Status
- Replaced full CLIENTS list (doesn't scale beyond ~5 sites) with Fleet Status summary
- Shows online/warning/offline counts with colored dots and text labels
- Shows up to 3 sites needing attention below
- Clicking summary navigates to Sites page (clears site filter)
- Used `text-label-primary` for status labels (visible in dark mode)
- Added `border-separator-medium` between Fleet Status and Navigation sections

### 6. Typography & Animation
- Added Plus Jakarta Sans display font via Google Fonts
- Added keyframes: `stagger-in`, `slide-up`, `gauge-fill`, `count-up`
- Added `stagger-list` class to Dashboard KPI grid, Incidents, Sites, Partners tables

## Commits
- `8c0e989` fix: dark mode overhaul + real glassmorphism across entire dashboard
- `ae3e711` fix: stagger-list animation hiding incident rows on re-render

[truncated...]

---

## 2026-03-08-session-160-Production-hardening-audit-6-rounds-40-fixes.md

# Session 160 - Production Hardening Audit: 6 Rounds, 40+ Fixes

**Date:** 2026-03-08
**Previous Session:** 159

## Goals

- [x] Systematic production readiness audit (rounds 4-6)
- [x] Fix all onboarding pipeline technical issues (10 items)
- [x] Panic recovery for Go daemon goroutines
- [x] Timing-safe evidence chain verification
- [x] Partner onboarding visibility endpoints

## Rounds Completed

### Round 4: Panic Recovery, Timing Attacks, Input Validation (8 fixes)
- safeGo() wrapper with recover() for all daemon goroutines
- SSH timeout goroutine leak: session.Close() on timeout
- hmac.compare_digest() for 5 evidence chain hash comparisons
- Open redirect fix in OAuthCallback
- ge=1 lower bound on 10 limit query parameters
- All fire-and-forget goroutines converted to safeGo

### Round 5: Webhook Dedup, Input Bounds, JSON Safety (3 fixes)
- Stripe webhook replay protection (stripe_webhook_events dedup table)
- ip_addresses checkin field bounded to max 100 items
- 4 json.loads in cve_watch.py wrapped with try/except

### Round 6: Onboarding Pipeline (10 issues addressed)
1. API key generation on provisioning
2. Credential delivery race fix (compare updated_at vs last_checkin)
3. Credential format mismatch (handle both admin JSON + partner Fernet)
4. Stage transition validation (max 3 forward, 1 backward)
5. Site ID entropy increase (24→48 bits)
6. Discovery credential gate (block if no Windows creds)
7. Stage timestamps on auto-transition
8. Agent registry disk persistence
9. Partner onboarding + trigger-checkin endpoints
10. L1 rules sync confirmed working (audit false positive)

## Commits
- `51aa063` Round 4
- `8cdd6ec` Round 5
- `b1830f3` Round 6a: pipeline fixes
- `5cdcebd` Round 6b: credential delivery, partner endpoints, agent persistence

## Files Changed

| File | Change |
|------|--------|

[truncated...]

---

## 2026-03-08-session-161-Checkin-savepoint-fix-UI-workflow-polish.md

# Session 161 - Checkin Savepoint Fix + UI Workflow Polish

**Date:** 2026-03-08
**Previous Session:** 160

---

## Goals

- [x] Fix appliances showing "stale" on dashboard (checkin 500 errors)
- [x] Fix `_link_devices_to_workstations` `bundle_id` NOT NULL issue
- [x] Fix incident detail panel (NaN UUID)
- [x] Wire IncidentFeed row click (was console.log placeholder)
- [x] End-user workflow audit across all portals

---

## Progress

### Completed

1. **Checkin savepoint fix** (`736f3b8`): Steps 3.7b and 3.8 wrapped in `async with conn.transaction():` savepoints to prevent transaction poisoning. Appliances back to 200 OK.

2. **Workstation summary NOT NULL fix** (`e59015f`): `_update_workstation_summary` was missing `bundle_id`, `check_compliance`, `evidence_hash` columns (all NOT NULL). Added deterministic uuid5 bundle_id and sha256 evidence_hash.

3. **IncidentFeed click** (`06142bf`): Replaced `console.log('View incident', id)` with `navigate('/incidents')`.

4. **Incident detail NaN fix** (`e7dd8d3`): `getIncident(id: number)` → `getIncident(id: string)`. Incident IDs are UUIDs; `Number("uuid")` returned NaN, causing backend 500. Now passes string directly.

5. **Full UI walkthrough verified live**:
   - Dashboard: 2 Online, 93.3% compliance, incident chart, all cards working
   - Sites: Both online, "Just now" checkin, click-through to site detail works
   - Site detail: Devices/Workstations/Agents/Protection/Frameworks/Integrations tabs all render
   - Devices: 11 devices, summary stats, type breakdown
   - Incidents: 50+ incidents, filter by level/site/status, expandable detail panel shows drift data + HIPAA controls
   - Organizations: Renders with "+ New Organization" button
   - Client portal: Login page renders (Email & Password / Magic Link)
   - Partner portal: Login page renders (Microsoft/Google OAuth + Email/API Key)

### Remaining Issues

- Organizations page shows 0 orgs — `client_organizations` table is empty (sites use a different org reference). Data mismatch, not a code bug.

---

## Files Changed

| File | Change |
|------|--------|
| `backend/sites.py` | Savepoints for steps 3.7b + 3.8 |

[truncated...]

---

## 2026-03-08-session-162-macOS-drift-OUI-lookup-drift-config-portals.md

# Session 162: macOS Drift, OUI Lookup, Drift Config, Portal Fixes

**Date:** 2026-03-08
**Focus:** macOS drift scanning, per-site drift config, portal auth fixes, MAC OUI device hints

## Completed

### macOS Drift Scanning (14 HIPAA checks)
- Added `macosscan.go` with 13 active checks: FileVault, Gatekeeper, SIP, firewall, auto-updates, screen lock, file sharing, Time Machine, NTP, admin users, disk space, cert expiry
- Removed SSH/remote login check (flags the management channel itself)
- Routes macOS targets via `linuxscan.go` label detection (`lt.Label == "macos"`)

### Per-Site Drift Scan Configuration
- Migration 075: `site_drift_config` table with 47 default check types, `macos_remote_login` disabled by default
- Admin dashboard `DriftConfig.tsx`: toggle grid grouped by platform (Windows/Linux/macOS)
- Backend: GET/PUT `/api/dashboard/sites/{site_id}/drift-config`
- Daemon: `disabledChecks` map populated from checkin response `disabled_checks` array
- All 3 scan types (Windows, Linux, macOS) filter findings via `isCheckDisabled()`

### Drift Config in Partner + Client Portals
- Partner: GET/PUT `/me/sites/{site_id}/drift-config` with ownership verification
- Client: GET/PUT `/client/sites/{site_id}/drift-config` with org ownership verification
- Frontend: `PartnerDriftConfig.tsx` (indigo theme), `ClientDriftConfig.tsx` (teal theme)
- "Security Checks" button on partner + client dashboard site rows
- Compliance scoring (`db_queries.py`): all 3 scoring functions exclude disabled checks

### SRA Remediation Save Fix
- Root cause: CSRF middleware blocked companion/client portal PUT/POST requests
- Fix: exempted `/api/companion/` and `/api/client/` prefixes (session-auth protected)
- Added CSRF token headers to SRAWizard fetch calls
- Added visual save confirmation (checkmark + error states)

### MAC OUI Device Type Hints
- Created `oui_lookup.py`: ~500+ MAC prefix entries covering major manufacturers
- Device classes: server, workstation, network, printer, phone, iot, virtual, unknown
- Integrated into `device_sync.py` `get_site_devices()` — enriches API response on-the-fly
- Frontend: manufacturer shown italic under MAC column, type hint shown for "unknown" devices
- Expanded details: manufacturer + device class with "inferred" tooltip

### Daemon Deploy
- Built + deployed daemon v0.3.19 via fleet order (macOS scanning + drift config filtering)
- Fleet order `19e5bd7d` deployed to both appliances

## Key Commits
- `2949a50` feat: add macOS drift scanning (14 HIPAA security checks via SSH)
- `1c4ff8f` feat: per-site drift scan configuration with UI toggles
- `1517c0f` fix: SRA remediation save broken by CSRF + add save confirmation
- `a28422e` feat: drift config in partner + client portals, exclude disabled checks from compliance score

## Files Changed (Backend)

[truncated...]

---

## 2026-03-09-session-163-L2-pipeline-fix-incidents-SQL-join-fixes.md

# Session 163 - L2 Pipeline Fix + Incidents SQL Join Fixes

**Date:** 2026-03-09
**Started:** 12:54
**Previous Session:** 162
**Status:** Complete

---

## Goals

- [x] Fix site detail page not loading (SQL errors)
- [x] Fix L2 bypass — failed L1 orders skipping L2
- [x] L2 planner dynamic runbook loading
- [x] Add missing L1 rules for common incident types
- [x] Kick off chaos lab execution
- [ ] Shut down ws01 VM (iMac unreachable)
- [ ] Verify L2 fallback end-to-end with real incident

---

## Progress

### Completed

1. **Fixed 5 SQL queries** in `routes.py` referencing `i.site_id` (column doesn't exist on `incidents` table). All now join through `appliances` table. Commit `e542c99`.
2. **Added L2 fallback** in `sites.py` order completion handler. Failed L1 healing orders now try L2 LLM planner before escalating to L3. Commit `5ded789`.
3. **Dynamic runbook loading** in `l2_planner.py`. DB has 88 runbooks but `AVAILABLE_RUNBOOKS` only had 10. Added `_load_dynamic_runbooks()` with 5-min TTL cache. Commit `dbdd23b`.
4. **Added 8 L1 rules** directly to VPS DB: bitlocker, bitlocker_status, screen_lock, security_audit, winrm, service_status, rdp_nla, password_policy.
5. **Cleaned up duplicate** workstation entry for 192.168.88.250.
6. All 3 commits deployed via CI/CD.

### Blocked

- iMac (192.168.88.50) unreachable all session — couldn't shut down ws01 VM or fully test chaos lab
- WinRM timeouts due to iMac RAM pressure

---

## Files Changed

| File | Change |
|------|--------|
| `mcp-server/central-command/backend/routes.py` | Fixed 5 SQL queries joining incidents→appliances for site_id |
| `mcp-server/central-command/backend/sites.py` | Added L2 fallback on failed L1 healing orders |
| `mcp-server/central-command/backend/l2_planner.py` | Dynamic runbook loading from DB with TTL cache |

---

## Next Session

[truncated...]

---

## 2026-03-09-session-164-Compliance health infographic, lead swarm automation, daemon v0.3.20 fleet deploy.md

# Session 164 - Compliance Health Infographic, Lead Swarm Automation, Daemon V0.3.20 Fleet Deploy

**Date:** 2026-03-09
**Started:** 23:29
**Previous Session:** 163

---

## Goals

- [ ]

---

## Progress

### Completed


### Blocked


---

## Files Changed

| File | Change |
|------|--------|

---

## Next Session

1.

---

## 2026-03-09-session-164-Compliance-health-infographic-lead-swarm-daemon-deploy.md

# Session 164 — Compliance Health Infographic, Lead Swarm Automation, Daemon v0.3.20

**Date:** 2026-03-09/10
**Focus:** Client portal UX, sales automation, fleet deploy

## Completed

### 1. OpenClaw Lead Swarm Automation
- Made 4 scripts executable on OpenClaw server (178.156.243.221)
- Installed cron job: `0 6 * * *` daily at 6AM
- Installed Python dependencies: `anthropic`, `openai`, `requests`
- Set API keys in `.env`: Apollo, Anthropic (from OpenClaw auth-profiles), Hunter.io, Brave Search
- **Rewrote `fetch_daily_leads.py`** — Apollo free plan blocks search API despite claimed upgrade
  - New sources: HHS breach portal CSV + Brave Search API + Hunter.io email enrichment
  - Rotates through NEPA regions and practice types daily
  - Deduplicates and enriches leads with email addresses
- Fixed `generate_emails.py`: updated model to `claude-haiku-4-5-20251001`, fixed body parser
- Fixed `daily_lead_swarm.sh`: added `.env` sourcing, piped scan results to email generator
- **Full pipeline tested end-to-end**: 9 leads fetched → scanned → 9 personalized emails generated

### 2. Compliance Health Infographic (Client Portal)
- **Backend**: New `GET /api/client/sites/{site_id}/compliance-health` endpoint in `client_portal.py`
  - Returns 8-category breakdown, overall score, pass/fail/warn counts, 30-day trend, healing stats
  - Respects disabled drift checks per site
- **Frontend**: `ComplianceHealthInfographic.tsx` (612 lines)
  - Animated circular gauge with shield icon (ease-out cubic animation)
  - Outer 8-segment category ring (colored arcs per category health)
  - 8 category cards with icons, labels, progress bars
  - 30-day sparkline trend with up/down indicator
  - Auto-healing impact card with rate bar
  - "Protected by OsirisCare" status badge
  - Site selector dropdown for multi-site orgs
  - Loading skeleton + empty state
  - All using existing glassmorphism design system
- TypeScript clean, ESLint clean, production build succeeds

### 3. Daemon v0.3.20 Fleet Deploy
- Bumped version `0.3.18` → `0.3.20` in `daemon.go`
- Built Linux binary (16MB, CGO_ENABLED=0)
- Uploaded to VPS at `/opt/mcp-server/static/releases/appliance-daemon-linux`
- **Fixed fleet order URL**: old order pointed to `dashboard.osiriscare.net/static/` (wrong path)
- New fleet order `c1ef5242` active, expires in 48h, URL: `api.osiriscare.net/releases/`
- SHA256: `77848f1004dce10f7f54d84bf457e4be67b7c07f813205ec896090b3d278df95`
- Commit `aece44c` pushed → CI/CD deploying backend + frontend

## Issues Found
- **Apollo API**: Key `Pj5tgmb3bHI4NA_Spk3KVA` still returns "free plan" error despite user upgrading to Basic. Regenerated key didn't help. Workaround: use Brave Search + Hunter.io instead.
- **HHS breach CSV**: Primary URL returns 404. Fallback URL added but also returns 404. May need manual CSV download or different data source.
- **Old fleet order**: Failed because binary URL was wrong path (`/static/` vs `/releases/`). Cancelled and recreated.


[truncated...]

---

## 2026-03-09-session-165-L4-escalation-infographic-clickthrough-incident-category-filters.md

# Session 165 - L4 Escalation Infographic Clickthrough Incident Category Filters

**Date:** 2026-03-09
**Started:** 23:55
**Previous Session:** 164

---

## Goals

- [ ]

---

## Progress

### Completed


### Blocked


---

## Files Changed

| File | Change |
|------|--------|

---

## Next Session

1.

---

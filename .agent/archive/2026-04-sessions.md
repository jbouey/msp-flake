# Session Archive - 2026-04


## 2026-04-01-session-191-Healing pipeline enterprise hardening — 0% to 93% Windows healing, Phase 1-3, MikroTik fix, reverse tunnel.md

# Session 191 - Healing Pipeline Enterprise Hardening — 0% To 93% Windows Healing, Phase 1 3, Mikrotik Fix, Reverse Tunnel

**Date:** 2026-04-01
**Started:** 13:01
**Previous Session:** 190

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

## 2026-04-01-session-192-Production audit — 11 bugs fixed across 5 commits.md

# Session 192 — Production Audit: 11 Issues Fixed

**Date:** 2026-04-01 to 2026-04-02
**Commits:** 5 (3a482f1, fad1a7a, cd42a13, 1a26104)
**Daemon:** v0.3.66 built + fleet order 224b8619

## Round 1: Initial Dashboard Audit (4 bugs)

1. **Incident resolution churn** — Resolved incidents reopened every 5min scan cycle. Added 30-min grace period. Migration 112 adds reopen_count.
2. **go_agents column mapping** — os_version stored in os_name, query read wrong column. Fixed INSERT + query + backfilled.
3. **Stale incident auto-resolve** — health_monitor now cleans >7d stuck incidents.
4. **security-events/archive 401** — Go daemon missing site_id in payload.

## Round 2: Linux Healing + Performance (4 bugs)

5. **isSelfHost() didn't match IPs** — Root cause of ALL Linux healing 0%. Appliance tried SSH to itself (192.168.88.236) instead of local exec. Added net.InterfaceAddrs() check.
6. **6 wrong keyword fallback runbook IDs** — RB-BACKUP-001→RB-WIN-BACKUP-001, etc. Three types hitting "unknown runbook" errors.
7. **linux_encryption not in MONITORING_ONLY** — Was burning L2 LLM calls (63 failures/day) for un-automatable LUKS check.
8. **device/sync 52-65s** — N+1 queries + missing indexes. Migration 113 adds 3 composite indexes.

## Round 3: Flywheel Promotion Audit (3 bugs)

9. **716 dead CVE watch rules** — All disabled, 0 matches. Deleted (864→128 rules).
10. **Platform promotion threshold unreachable** — Required distinct_orgs >= 5 with 2-3 sites. Lowered to >= 1.
11. **42/53 promoted rules never matched** — Duplicated builtin rules (builtins fire first). Added dedup check against enabled builtin/synced rules before promoting.

## Architectural Findings

- `learning_promotion_reports` table: 0 rows. Designed for appliance-pushed reports but Go daemon doesn't call the endpoint. Dead architecture.
- Duplicate flywheel code in `main.py` AND `background_tasks.py`. Dead code in background_tasks.py (not imported).
- Dashboard 0% compliance on north-valley-branch-2: DB actually has 22.22%. Frontend cache or timing issue.

## Test Results
- 292 Python tests pass, 18 Go packages pass
- All changes deployed to VPS
- v0.3.66 daemon fleet order active

## Files Changed
- `mcp-server/main.py` — grace period, MONITORING_ONLY, keyword map, flywheel thresholds+dedup
- `mcp-server/central-command/backend/sites.py` — go_agents INSERT fix
- `mcp-server/central-command/backend/routes.py` — agent-health COALESCE query
- `mcp-server/central-command/backend/health_monitor.py` — stale incident cleanup
- `mcp-server/central-command/backend/agent_api.py` — MONITORING_ONLY sync
- `mcp-server/central-command/backend/tests/test_incident_pipeline.py` — updated for new runbook IDs
- `mcp-server/central-command/backend/migrations/112_incident_reopen_count.sql`
- `mcp-server/central-command/backend/migrations/113_device_sync_indexes.sql`
- `appliance/internal/daemon/healing_executor.go` — isSelfHost + imports
- `appliance/internal/daemon/devicelogs.go` — archive site_id

---

## 2026-04-01-session-192-Production audit — 4 bugs fixed incident churn agent display stale cleanup archive auth.md

# Session 192 — Production Audit: 4 Bugs Fixed

**Date:** 2026-04-01
**Duration:** ~45 min
**Commits:** 2 (3a482f1, fad1a7a)

## Trigger

User reviewed Pipeline Health and System Health dashboards, noticed:
- All 3 Go agents showing "offline" with "--" for OS/version despite active healing
- Only "1 Resolved 24h" despite 439 successful healings
- 23 permanently stuck incidents
- DB connections display question (not a bug)

## Root Causes Found

### 1. Incident Resolution Churn (HIGH)
**main.py:1916** — Resolved incidents immediately reopened by next scan cycle.
- Daemon heals drift → POST /incidents/resolve (success)
- Next scan (5 min) → drift still present → POST /incidents → reopens with resolved_at=NULL
- Dashboard shows perpetual "1 Resolved 24h" despite hundreds of healings
- **Fix:** 30-min grace period after resolve. Tracks reopen_count (Migration 112).

### 2. go_agents Column Mapping (MEDIUM)
**sites.py:2549** — `agent.os_version` stored in `os_name` column ($6), but agent-health query reads `os_version` (always empty).
- **Fix:** Populate both columns in INSERT. COALESCE in query. Backfilled 3 rows.

### 3. Stale Incident Accumulation (MEDIUM)
23 incidents stuck in open/escalated/resolving from Mar 22-31 — types that can't be healed automatically.
- **Fix:** `_resolve_stale_incidents()` in health_monitor.py — auto-resolves >7d incidents with no recent healing attempts.

### 4. security-events/archive 401 (LOW)
**devicelogs.go** — Archive payload missing `site_id`. `require_appliance_auth()` needs it.
- **Fix:** Added `site_id: cfg.SiteID` to payload. Needs Go daemon rebuild to deploy.

### 5. resolution_tier CHECK Constraint (QUICK FIX)
Stale cleanup used `'auto_stale'` which violated CHECK constraint (only L1/L2/L3/monitoring allowed).
- **Fix:** Changed to `'monitoring'`.

## Verification

| Fix | Verified |
|-----|----------|
| Grace period | rogue_scheduled_tasks resolved at 20:29 and STAYING resolved |
| go_agents OS | os_version column populated for all 3 agents |
| Stale cleanup | Deployed, awaiting first health_monitor cycle |
| Archive auth | Code committed, needs Go daemon deploy |
| Tests | 292 Python pass, 18 Go packages pass |

## Not A Bug

[truncated...]

---

## 2026-04-02-session-193-auto-rekey auth recovery, appliance network diagnosis.md

# Session 193 - Auto Rekey Auth Recovery, Appliance Network Diagnosis

**Date:** 2026-04-02
**Started:** 23:38
**Previous Session:** 192

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

## 2026-04-02-session-193-auto-rekey.md

# Session 193 — Auto-Rekey, 12-Point Hardening, Agent Error Classification

**Date:** 2026-04-03
**Commits:** 5
**Daemon Version:** 0.3.76
**Lines Changed:** ~450 added across 12 files

## Incident & Diagnosis

Appliance reported offline. Root cause chain:
1. Switch reset → appliance rebooted → DHCP assigned .235 (was .241)
2. ARP scan found it via MAC `7C:D3:0A:7C:55:18`
3. Real issue: API key mismatch → 401 on every checkin for 2.5h
4. Manual rekey restored checkins immediately

## Commit 1: Auto-Rekey Feature
- `POST /api/provision/rekey` — unauthenticated, MAC+site_id+hardware_id identity
- Daemon: `ErrAuthFailed` → `attemptRekey()` after 3 consecutive 401s
- `UpdateAPIKey()` atomically writes new key to config.yaml
- Dashboard: `auth_failed` orange badge (vs generic "Offline")
- Migration 118: auth_failure_since/count/last on site_appliances

## Commit 2: CSRF + Audit Fix
- `/api/provision/` prefix CSRF exempt
- audit_log column names (event_type not action)

## Commit 3: 12-Point Hardening
**P0:** SudoPassword propagation (unblocks ALL Linux healing), root cause in offline alerts
**P1:** Sudo failure logging, AD DNS fallback resolver, subnet-dark detection (≥80% unreachable → single incident), healing netscan IP fallback
**P2:** SSH credential auto-update in device_sync, NixOS msp-dns-hosts service, credential IP update endpoint, mass-unreachable circuit breaker

## Commit 4: Workstation Agent Error Classification
- `ClassifyConnectionError()` — 10 categories (dns_not_found, appliance_down, network_down, timeout, tls_error, auth_rejected, etc.)
- Consecutive failure counter + cert auto-re-enrollment after 5 auth rejections
- `ForceReEnrollment()` deletes stale certs for TOFU re-enrollment

## Commit 5: CI/CD Deploy Key Fix
- New ed25519 keypair for GitHub Actions → VPS SSH
- Deploys green after 2 failures with stale key

## Network Fixes (Manual)
- Linux VM .236→.233: promiscuous mode Allow All, credential IP updated
- NVDC01 DNS: /etc/hosts entry + ad_dns_server config
- NV appliance API key restored, v0.3.76 fleet order deployed
- iMac back on correct WiFi, VMs running

## Open Items
- iMac SSH port 2222: LaunchDaemon "Operation not permitted" — needs `sudo launchctl load -w` from iMac Terminal
- MikroTik DHCP reservations: needs admin credentials
- NVDC01 agent config still points to .241 — will auto-fix on next autodeploy cycle

[truncated...]

---

## 2026-04-01-session-191-Healing pipeline enterprise hardening — 0% to 93% Windows healing, Phase 1-3, MikroTik fix, reverse tunnel.md

# Session 191 - Healing Pipeline Enterprise Hardening — 0% To 93% Windows Healing, Phase 1 3, Mikrotik Fix, Reverse Tunnel

**Date:** 2026-04-01
**Started:** 13:01
**Previous Session:** 190

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

## 2026-04-01-session-192-Production audit — 11 bugs fixed across 5 commits.md

# Session 192 — Production Audit: 11 Issues Fixed

**Date:** 2026-04-01 to 2026-04-02
**Commits:** 5 (3a482f1, fad1a7a, cd42a13, 1a26104)
**Daemon:** v0.3.66 built + fleet order 224b8619

## Round 1: Initial Dashboard Audit (4 bugs)

1. **Incident resolution churn** — Resolved incidents reopened every 5min scan cycle. Added 30-min grace period. Migration 112 adds reopen_count.
2. **go_agents column mapping** — os_version stored in os_name, query read wrong column. Fixed INSERT + query + backfilled.
3. **Stale incident auto-resolve** — health_monitor now cleans >7d stuck incidents.
4. **security-events/archive 401** — Go daemon missing site_id in payload.

## Round 2: Linux Healing + Performance (4 bugs)

5. **isSelfHost() didn't match IPs** — Root cause of ALL Linux healing 0%. Appliance tried SSH to itself (192.168.88.236) instead of local exec. Added net.InterfaceAddrs() check.
6. **6 wrong keyword fallback runbook IDs** — RB-BACKUP-001→RB-WIN-BACKUP-001, etc. Three types hitting "unknown runbook" errors.
7. **linux_encryption not in MONITORING_ONLY** — Was burning L2 LLM calls (63 failures/day) for un-automatable LUKS check.
8. **device/sync 52-65s** — N+1 queries + missing indexes. Migration 113 adds 3 composite indexes.

## Round 3: Flywheel Promotion Audit (3 bugs)

9. **716 dead CVE watch rules** — All disabled, 0 matches. Deleted (864→128 rules).
10. **Platform promotion threshold unreachable** — Required distinct_orgs >= 5 with 2-3 sites. Lowered to >= 1.
11. **42/53 promoted rules never matched** — Duplicated builtin rules (builtins fire first). Added dedup check against enabled builtin/synced rules before promoting.

## Architectural Findings

- `learning_promotion_reports` table: 0 rows. Designed for appliance-pushed reports but Go daemon doesn't call the endpoint. Dead architecture.
- Duplicate flywheel code in `main.py` AND `background_tasks.py`. Dead code in background_tasks.py (not imported).
- Dashboard 0% compliance on north-valley-branch-2: DB actually has 22.22%. Frontend cache or timing issue.

## Test Results
- 292 Python tests pass, 18 Go packages pass
- All changes deployed to VPS
- v0.3.66 daemon fleet order active

## Files Changed
- `mcp-server/main.py` — grace period, MONITORING_ONLY, keyword map, flywheel thresholds+dedup
- `mcp-server/central-command/backend/sites.py` — go_agents INSERT fix
- `mcp-server/central-command/backend/routes.py` — agent-health COALESCE query
- `mcp-server/central-command/backend/health_monitor.py` — stale incident cleanup
- `mcp-server/central-command/backend/agent_api.py` — MONITORING_ONLY sync
- `mcp-server/central-command/backend/tests/test_incident_pipeline.py` — updated for new runbook IDs
- `mcp-server/central-command/backend/migrations/112_incident_reopen_count.sql`
- `mcp-server/central-command/backend/migrations/113_device_sync_indexes.sql`
- `appliance/internal/daemon/healing_executor.go` — isSelfHost + imports
- `appliance/internal/daemon/devicelogs.go` — archive site_id

---

## 2026-04-01-session-192-Production audit — 4 bugs fixed incident churn agent display stale cleanup archive auth.md

# Session 192 — Production Audit: 4 Bugs Fixed

**Date:** 2026-04-01
**Duration:** ~45 min
**Commits:** 2 (3a482f1, fad1a7a)

## Trigger

User reviewed Pipeline Health and System Health dashboards, noticed:
- All 3 Go agents showing "offline" with "--" for OS/version despite active healing
- Only "1 Resolved 24h" despite 439 successful healings
- 23 permanently stuck incidents
- DB connections display question (not a bug)

## Root Causes Found

### 1. Incident Resolution Churn (HIGH)
**main.py:1916** — Resolved incidents immediately reopened by next scan cycle.
- Daemon heals drift → POST /incidents/resolve (success)
- Next scan (5 min) → drift still present → POST /incidents → reopens with resolved_at=NULL
- Dashboard shows perpetual "1 Resolved 24h" despite hundreds of healings
- **Fix:** 30-min grace period after resolve. Tracks reopen_count (Migration 112).

### 2. go_agents Column Mapping (MEDIUM)
**sites.py:2549** — `agent.os_version` stored in `os_name` column ($6), but agent-health query reads `os_version` (always empty).
- **Fix:** Populate both columns in INSERT. COALESCE in query. Backfilled 3 rows.

### 3. Stale Incident Accumulation (MEDIUM)
23 incidents stuck in open/escalated/resolving from Mar 22-31 — types that can't be healed automatically.
- **Fix:** `_resolve_stale_incidents()` in health_monitor.py — auto-resolves >7d incidents with no recent healing attempts.

### 4. security-events/archive 401 (LOW)
**devicelogs.go** — Archive payload missing `site_id`. `require_appliance_auth()` needs it.
- **Fix:** Added `site_id: cfg.SiteID` to payload. Needs Go daemon rebuild to deploy.

### 5. resolution_tier CHECK Constraint (QUICK FIX)
Stale cleanup used `'auto_stale'` which violated CHECK constraint (only L1/L2/L3/monitoring allowed).
- **Fix:** Changed to `'monitoring'`.

## Verification

| Fix | Verified |
|-----|----------|
| Grace period | rogue_scheduled_tasks resolved at 20:29 and STAYING resolved |
| go_agents OS | os_version column populated for all 3 agents |
| Stale cleanup | Deployed, awaiting first health_monitor cycle |
| Archive auth | Code committed, needs Go daemon deploy |
| Tests | 292 Python pass, 18 Go packages pass |

## Not A Bug

[truncated...]

---

## 2026-04-02-session-193-auto-rekey auth recovery, appliance network diagnosis.md

# Session 193 - Auto Rekey Auth Recovery, Appliance Network Diagnosis

**Date:** 2026-04-02
**Started:** 23:38
**Previous Session:** 192

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

## 2026-04-02-session-193-auto-rekey.md

# Session 193 — Auto-Rekey, 12-Point Hardening, Agent Error Classification

**Date:** 2026-04-03
**Commits:** 5
**Daemon Version:** 0.3.76
**Lines Changed:** ~450 added across 12 files

## Incident & Diagnosis

Appliance reported offline. Root cause chain:
1. Switch reset → appliance rebooted → DHCP assigned .235 (was .241)
2. ARP scan found it via MAC `7C:D3:0A:7C:55:18`
3. Real issue: API key mismatch → 401 on every checkin for 2.5h
4. Manual rekey restored checkins immediately

## Commit 1: Auto-Rekey Feature
- `POST /api/provision/rekey` — unauthenticated, MAC+site_id+hardware_id identity
- Daemon: `ErrAuthFailed` → `attemptRekey()` after 3 consecutive 401s
- `UpdateAPIKey()` atomically writes new key to config.yaml
- Dashboard: `auth_failed` orange badge (vs generic "Offline")
- Migration 118: auth_failure_since/count/last on site_appliances

## Commit 2: CSRF + Audit Fix
- `/api/provision/` prefix CSRF exempt
- audit_log column names (event_type not action)

## Commit 3: 12-Point Hardening
**P0:** SudoPassword propagation (unblocks ALL Linux healing), root cause in offline alerts
**P1:** Sudo failure logging, AD DNS fallback resolver, subnet-dark detection (≥80% unreachable → single incident), healing netscan IP fallback
**P2:** SSH credential auto-update in device_sync, NixOS msp-dns-hosts service, credential IP update endpoint, mass-unreachable circuit breaker

## Commit 4: Workstation Agent Error Classification
- `ClassifyConnectionError()` — 10 categories (dns_not_found, appliance_down, network_down, timeout, tls_error, auth_rejected, etc.)
- Consecutive failure counter + cert auto-re-enrollment after 5 auth rejections
- `ForceReEnrollment()` deletes stale certs for TOFU re-enrollment

## Commit 5: CI/CD Deploy Key Fix
- New ed25519 keypair for GitHub Actions → VPS SSH
- Deploys green after 2 failures with stale key

## Network Fixes (Manual)
- Linux VM .236→.233: promiscuous mode Allow All, credential IP updated
- NVDC01 DNS: /etc/hosts entry + ad_dns_server config
- NV appliance API key restored, v0.3.76 fleet order deployed
- iMac back on correct WiFi, VMs running

## Open Items
- iMac SSH port 2222: LaunchDaemon "Operation not permitted" — needs `sudo launchctl load -w` from iMac Terminal
- MikroTik DHCP reservations: needs admin credentials
- NVDC01 agent config still points to .241 — will auto-fix on next autodeploy cycle

[truncated...]

---

## 2026-04-03-session-193b-chaos-lab-imac-agent-recovery.md

# Session 193b — Chaos Lab Fix + iMac Agent Recovery

**Date:** 2026-04-03
**Agent Version:** 0.4.2 (workstation), 0.3.76 (appliance daemon)

## Chaos Lab Fixes

All chaos lab scripts on iMac (.50) were broken due to stale IPs and service names from the Python agent era.

1. **IP updates**: `.246→.235` (appliance), `.242→.233` (linux) in `chaos_workstation_cadence.py`, `chaos_lab.sh`, `linux_chaos_lab.sh`
2. **Service name**: `compliance-agent→appliance-daemon` in cadence script + chaos_lab.sh
3. **SSH host key**: Cleared stale `.246` key on iMac known_hosts
4. **Cadence script rewrite**: Complete rewrite to match Go daemon log patterns — 8 regex patterns covering drift_scan, win_scan, netscan, checkin, adaptive interval, l1_healed, evidence, healing_rate. Replaces Python-era patterns (enumerate_from_ad, run_all_checks).

## iMac Agent Root Cause Chain

The iMac agent was dead since March 26 (8 days). Five cascading failures:

1. **Wrong platform binary**: Appliance update endpoint served `osiris-agent.exe` (Windows PE32+). Updater downloaded it, replaced the macOS Mach-O binary. LaunchDaemon got exit code 126 (cannot execute binary).
2. **updater.go chmod bug**: `downloadBinary()` uses `os.Create()` which defaults to 0666/umask (0644). No `os.Chmod(0755)` after download. Even if the right binary was downloaded, it wouldn't be executable.
3. **Config pointed to .241**: After DHCP reassigned the appliance to .235, the agent config/plist still had .241:50051.
4. **Stale TLS certs**: ca.crt, agent.crt, agent.key from old appliance CA identity. Even after fixing the IP, TLS handshake fails with `x509: ECDSA verification failure`.
5. **Go 1.26 incompatibility**: macOS 11.7 (Big Sur) doesn't have `SecTrustCopyCertificateChain` (requires macOS 12+). Go 1.26 uses this API. The amd64 build needed Go 1.24.

## Fixes Applied

| Layer | Fix |
|-------|-----|
| `updater.go` | Added `os.Chmod(destPath, 0755)` after download |
| `updater_test.go` | 12 new tests: chmod, content, HTTP errors, SHA256, concurrency, backoff |
| `Makefile` | Split Go toolchains: Go 1.24 for amd64 (macOS 11+), Go 1.26 for arm64 (M-series) |
| iMac config.json | `appliance_address` → `192.168.88.235:50051` |
| iMac plist | `--appliance 192.168.88.235:50051` |
| iMac TLS | Cleared ca.crt, agent.crt, agent.key, appliance_cert_pin.hex → TOFU re-enrolled |
| iMac binary | Installed Go 1.24-built amd64 binary, v0.4.2 |
| Appliance `/var/lib/msp/bin/` | Both darwin-amd64 + darwin-arm64 binaries + VERSION file |
| Appliance manifest | Auto-scanned on restart: `darwin/amd64` + `darwin/arm64` registered |

## Verified

- Chaos lab cadence: **6/6 PASSED** (daemon, targets, scan cadence, checkin cadence, healing, evidence)
- iMac agent: **v0.4.2 running**, registered as `go-MaCs-iMac.local-94967874`, mTLS connected
- iMac compliance: 9 pass, 3 fail (screen_lock, firewall, time_machine)
- Appliance sees agent: `agents=1` in cycle, drift streaming active
- Go tests: 4 packages, 0 failures (checks, transport, updater, wmi)

## Phase 2: Runbook Coverage + Agent Hardening

**4 commits, agent v0.4.4, daemon v0.3.77**


[truncated...]

---

## 2026-04-04-session-194-mesh-discovery-mdns-landing-page-blazytko-patterns.md

# Session 194 — Mesh Discovery + mDNS + Landing Page + Blazytko Patterns

**Date:** 2026-04-04 / 2026-04-05
**Commits:** 15
**Daemon:** v0.3.79 → v0.3.81
**Agent:** v0.4.2 → v0.4.5

## Summary

Cross-subnet mesh peer discovery with 14-point hardening from SWE/PM/CCIE roundtable. mDNS service discovery eliminates DHCP drift breakage. Landing page overhauled (pricing, legal, claims accuracy). Blazytko agentic pipeline patterns adapted for HIPAA compliance (hypothesis-driven L2, validation gates, audit trail, check catalog).

## Major Features

### 1. Cross-Subnet Mesh Peer Discovery
- Backend delivers sibling IPs+MACs in checkin response
- TLS probe with CA verification (TCP fallback)
- Parallel probing, backend-peer expiry, WireGuard IP filtering
- 14-point hardening: all P0/P1/P2 from roundtable review
- Ring convergence monitoring, split-brain detection
- Consumer-router topology handling (mesh_topology config, auto-reclassify alerts)

### 2. DHCP Drift Resilience (3-part solution)
- mDNS: Avahi publishes _osiris-grpc._tcp.local, agent resolves by service name
- Secondary static IP: 169.254.88.1/24 on appliance NIC
- Onboarding gate: forces network_mode decision (static_lease/dynamic_mdns)
- Agent discovery chain: mDNS → link-local → DNS SRV → offline
- Reconnect loop re-resolves after 3 failures

### 3. Landing Page Overhaul
- Pricing: $200 → $499/mo (roundtable consensus)
- Legal: Privacy Policy, ToS, BAA pages (were dead links)
- Claims qualified: reports → monitoring summaries, archive → bundles
- Animated ECG heartbeat in hero section
- Calendly link fixed
- Pricing strategy PDF generated

### 4. Blazytko Roundtable Patterns
- Hypothesis-driven L2 triage: 12 incident types × 3-5 ranked root causes
- L2 validation gate: schema check before execution (14 tests)
- Investigation audit trail: HealingEntry + hypothesis/confidence/reasoning
- Confidence tagging on evidence bundles
- Check catalog: /api/check-catalog — 58 checks, HIPAA mapping, no remediation scripts

### 5. Data Quality
- Workstation cleanup: 17→9 (duplicates, appliance IPs, router, stale artifacts)
- Backend prevention logic runs every checkin

## Migrations
- 120: mesh_topology on sites
- 121: network_mode on sites

[truncated...]

---

## 2026-04-06-session-195-ops-center-device-sync-fix-demo-guide-v3-evidence-pipeline-audit.md

# Session 195 — Ops Center, Device Sync Fix, Demo Guide v3

**Date:** 2026-04-06
**Commits:** 6
**Daemon:** v0.3.81 | **Agent:** v0.4.5

---

## Goals

- [x] Investigate and fix device sync pipeline (.250/.251 not updating)
- [x] Fix Lanzaboote Secure Boot error on nixos-rebuild switch
- [x] Build /ops Operations Center page (5 traffic lights + audit readiness)
- [x] Expand /docs with maintenance runbooks and reference material
- [x] Update demo video guide to v3
- [x] Fix rogue scheduled task allowlists

---

## Progress

### Device Sync Fix (Critical)
- Root cause: 3 competing sync sources — UUID replay overwriting IP-format device_ids
- CASE expression prevents UUID overwriting IP, GREATEST() prevents timestamp revert
- Cleaned 16 stale incidents, all 4 key devices updating live

### Ops Center
- ops_health.py: 5 traffic-light statuses + partner-scoped variant
- audit_report.py: per-org readiness badge + countdown + BAA config
- 56 unit tests passing
- OpsCenter.tsx + StatusLight.tsx + Documentation expansion (7 runbooks + 4 reference articles)

### Evidence Pipeline
- Discovered compliance_bundles has 231K entries (was querying legacy evidence_bundles table)
- 94% Ed25519 signed, 56% BTC-anchored, chain position 137,069 — production-ready

### Other
- Lanzaboote disabled (Secure Boot not in BIOS)
- Rogue task allowlist aligned (XblGameSaveTask, UserLogonTask)
- Sitemap + demo guide v3 PDF

### Blocked
- Mesh asymmetric routing (consumer router hardware)
- iMac SSH port 2222 (reverse tunnel workaround)
- Witness submit 500s
- Autodeploy file lock on agent.b64

---

## Files Changed

[truncated...]

---

## 2026-04-06-session-196-backend-authoritative-mesh-naming-keys-gui-fixes.md

# Session 196 — Backend-Authoritative Mesh, Per-Appliance Identity, GUI Fixes

**Date:** 2026-04-06
**Daemon:** v0.3.81 → v0.3.82
**Commit:** 89f0d28 (22 files, +2249/-150)

---

## Summary

Resolved 5 issues from user screenshots, then designed and implemented a backend-authoritative mesh architecture for the 3-node multi-subnet testbed.

## Issues Fixed

1. **Appliance cards unclickable** — `z-10` on GlassCard created stacking context. Removed.
2. **Notification panel hiding behind sidebar** — Header `z-10` couldn't escape stacking context above Sidebar `z-50`. Bumped Header to `z-50`.
3. **Chaos lab SSH failure** — Default IP in `chaos_workstation_cadence.py` updated from `.246` to `.235`.
4. **Evidence chain broken (2 rejections)** — Single `sites.agent_public_key` caused key collisions with 3 appliances. Fixed with per-appliance keys (migration 126).
5. **All appliances named "osiriscare"** — Added `display_name` column (migration 125) with iterative naming.

## Backend-Authoritative Mesh (Hybrid C+)

### Problem
3 appliances across 2 subnets (88.x + 0.x). T640 on 0.x couldn't probe 88.x peers back — asymmetric routing. Client-side hash ring diverged.

### Solution
Backend computes target assignments server-side during checkin using identical hash ring algorithm (Python port of Go). Daemon prefers server assignments, falls back to local ring after 15 min.

### What Shipped
- **hash_ring.py** — Python consistent hash ring, cross-language test vectors
- **STEP 3.8c** in checkin handler — server-side target assignment
- **Daemon v0.3.82** — `OwnsTarget()` prefers server assignments
- **Evidence dedup** — 15-min window prevents overlap during failover
- **Removed** — split-brain detection, Network Stability panel, independent mode UI, mesh_topology config
- **Migrations** — 125 (display_name), 126 (per-appliance keys), 127 (assigned_targets)

### Test Coverage
- 9 Python hash ring tests
- 7 target assignment tests
- 4 evidence dedup tests
- 4 Go server assignment + cross-language tests

### Testbed State
| Appliance | IP | Subnet | Version | Status |
|-----------|-----|--------|---------|--------|
| osiriscare (T640) | 192.168.0.11 | 0.x | 0.3.82 | Online, 0 targets (no creds) |
| osiriscare-2 (Physical) | 192.168.88.241 | 88.x | 0.3.82 | Online, 4 targets |
| osiriscare-installer (T740) | 192.168.88.232 | 88.x | 0.3.82 | Online, 0 targets (no creds) |

## Spec + Plan

[truncated...]

---

## 2026-04-06-session-197-cross-appliance dedup + alert routing + client approval workflow.md

# Session 197 — Multi-Appliance Maturity + Multi-Framework Compliance

**Date:** 2026-04-06
**Commits:** ~28
**Tests:** 64 passing (9 test files, 0 regressions)
**Migrations:** 128-134 deployed to production (7 total)

---

## What Shipped

### Spec 1: Cross-Appliance Dedup + Alert Routing
- Incident dedup by SHA256(site_id:incident_type:hostname)
- PHI-free digest emails to org contacts (4h batch, critical/high immediate)
- Per-site alert modes: self_service / informed / silent (org default, site override)
- Client portal /client/alerts page with approve/dismiss
- End-to-end verified: email delivered to jbouey@osiriscare.net

### Layer 4: Dashboard Multi-Appliance UX
- Expandable appliance cards with compliance breakdown grid
- Per-appliance incident filter chips
- display_name + assigned_target_count in backend

### Spec 2: Client Self-Service
- Non-engagement escalation: 48h unacted → partner notified (7-day dedup)
- Guided credential entry modal (4 types, 3-step wizard, Fernet encrypted)
- Partner notifications API (GET + mark-read)
- Compliance packet approval audit section

### Maturity Fixes
- MFA enforcement: per-org/per-user `mfa_required` flag, blocks login if not enrolled
- Audit log retention: 3-year policy with background purge
- Notes field disclaimer: "Do not enter patient names or PHI"

### Multi-Framework Compliance (THE BIG ONE)
- `client_orgs.compliance_framework` column (hipaa/soc2/pci_dss/nist_csf/glba/sox/gdpr/cmmc/iso_27001)
- `get_control_for_check()` routes through control_mappings.yaml crosswalk
- Email templates parameterized: "Compliance Controls" not "HIPAA Controls"
- Frontend copy.ts: "Compliance Monitoring Platform" not "HIPAA Compliance..."
- Checkin delivers compliance_framework to daemon
- 130+ check types map to all 9 frameworks via existing YAML infrastructure

---

## Production State

- 7 migrations (128-134) all successful
- 64 tests across 9 new test files
- alert_digest background task running
- Health: OK

[truncated...]

---

## 2026-04-07-session-198-production audit + audit fixes.md

# Session 198 — Production Audit + Fixes + Multi-Framework + Data Cleanup

**Date:** 2026-04-07
**Commits:** ~12
**Tests:** 82 passing (11 test files)
**Migrations:** 135 deployed

---

## What Shipped

### Production Security Audit (9 findings fixed)
- C1: DRY — alert enqueue uses module function not inline reimplementation
- C2: HTML-escaped org_name in all email templates
- C3: partner_notifications RLS tenant policy (migration 135)
- I1-I6: idempotency, connection consolidation, logging, site name, validation

### Pre-Existing Production Errors (4 fixed, all verified 0)
- go_agents RLS: admin_connection must SET LOCAL app.is_admin=true (PgBouncer stale GUC)
- Mesh isolation: ::text cast on asyncpg LIKE parameter
- MinIO WORM: http:// prefix for boto3 endpoint_url
- PgBouncer DuplicatePreparedStatementError: retry decorator on SQLAlchemy hot path

### Multi-Framework Compliance (9 frameworks)
- compliance_packet.py routes through control_mappings.yaml crosswalk
- Email templates parameterized (framework-agnostic)
- Frontend copy.ts: "Compliance Monitoring Platform" not "HIPAA..."
- Checkin delivers compliance_framework to daemon

### SOC 2 + GLBA Assessment Templates
- soc2_templates.py: 30 questions (CC/A/PI/C/P) + 8 policies
- glba_templates.py: 25 questions (admin/tech/physical/privacy/disposal) + 6 policies
- framework_templates.py: router for get_assessment_questions(framework)
- 17 template tests

### MFA Enforcement + Audit Retention
- Per-org/user mfa_required flag, blocks login if not enrolled
- 3-year audit log retention with background purge
- Migration 134

### Data Quality Cleanup
- 3 home network workstations removed
- 1 appliance IP workstation removed
- 1 duplicate iMac entry removed
- 28 home network discovered_devices removed
- 19 stale incidents resolved (deploy + unreachable + old IPs)
- Residential subnet exclusion (192.168.0/1.x) prevents recontamination

### VPN Page Dedup
- Backend: DISTINCT ON (site_id) instead of per-appliance rows

[truncated...]

---

## 2026-04-07-session-199-demo-polish.md

# Session 199 — Demo Video Polish: Full Round-Table Audit

**Date:** 2026-04-07
**Duration:** ~4 hours
**Commits:** 12 pushes to main
**Files Changed:** 80+ across frontend, backend, and tests
**CI:** Last 3 deploys GREEN

## Summary

Full front-to-back demo polish session. Established a "round table" of Principal SWE, Product Manager, CCIE Network Expert, Business Coach, and Compliance Attorney. Every page in the admin dashboard, partner portal, and client portal was audited and fixed.

## Key Accomplishments

### Admin Dashboard (19 pages audited)
- **"Drifted" eliminated** — replaced with "X/Y Failing" badges (severity-colored), staleness chips, severity-sorted tables
- **Device Inventory restructured** — "Managed Fleet" (compliance story) separated from "Network Discovery" (subnet context)
- **SiteDevices crash fixed** — React hooks violation (useState after conditional return)
- **Dashboard attention labels** — "Repeat drift:" → "Recurring:", raw check_types → human labels via CHECK_TYPE_LABELS
- **Sidebar** — "Not Deployed" for undeployed sites (was misleading "Offline")
- **Runbook success rate** — 6.2% → ~85% (excluded 0-execution rules from average)
- **19 alert() calls** → inline feedback banners (Partners + Users)
- **All raw IDs** removed from user-facing displays across all pages

### Partner Portal (round table from MSP perspective)
- Portfolio Health KPI card
- Hidden MAC addresses, raw site_ids
- L3/L4 → "Manual Review" / "Critical Escalation"

### Client Portal (round table from practice manager perspective)
- All jargon humanized: Healing→Automatic Fixes, Host→Device, HIPAA Control→Regulation
- L1/L2/L3 tier labels removed from client view
- Client-friendly alert labels (SOFTWARE_UPDATE_AVAILABLE, etc.)
- Plain English escalation routing

### Legal Language Sweep (compliance attorney)
- "ensures/prevents/protects" → monitors/helps/reduces (12 frontend + 8 backend files)
- "PHI never leaves" → "PHI scrubbed at appliance"
- "audit-ready" → "audit-supportive"
- All disclaimers preserved

### Production Fixes
- auth.py validate_session → execute_with_retry (fixed PgBouncer 500s on every auth request)
- Stale v0.2.2 release cleared from DB
- CI pipeline fixed (sqlalchemy stubs + indentation + ESLint)
- Pagination on workstations + devices tables

## DRY Improvements
- cleanAttentionTitle() extracted to constants/status.ts (shared by AttentionPanel + Notifications)
- CHECK_TYPE_LABELS used consistently across Dashboard, Incidents, Learning, TopIncidentTypes, CoverageGapPanel

[truncated...]

---

## 2026-04-07-session-199-demo-polish: full round-table audit, 80+ files, legal sweep, portal overhaul.md

# Session 199 - Demo Polish: Full Round Table Audit, 80+ Files, Legal Sweep, Portal Overhaul

**Date:** 2026-04-07
**Started:** 10:18
**Previous Session:** 198

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

## 2026-04-07-session-200-mesh-round-table-db-review.md

# Session 200 — Mesh Round Table + DB Scaling + UX Fixes

**Date:** 2026-04-07 / 2026-04-08
**Commits:** `b2f3d84`, `a47de9b`, `b4ccac8`, `550ccdf`, `002c952`, `cf2fe0e`, `402a48d` (9 total)
**Status:** All deployed to production

## Phase 2 (2026-04-08) — DB Scaling + UX

### DB Scaling (all 4 round table items executed):
1. Pool consolidation — asyncpg min=2/max=25, dual-pool documented in fleet.py
2. Checkin savepoints — 5 bare steps wrapped (3.5, 3.6, 4, 4.5, 6b-2)
3. Migration 137 — `incident_remediation_steps` relational table (replaces JSONB array)
4. Migration 138 — `compliance_bundles` partitioned (25 monthly, 232K rows), `portal_access_log` (31 monthly)

### Bug fix: assigned_target_count hardcoded to 0
- Root cause: `sites.py` `get_site()` at `/api/sites/{site_id}` had `'assigned_target_count': 0` hardcoded
- The query didn't SELECT `assigned_targets` at all
- `routes.py` at `/api/dashboard/fleet/{site_id}` had the correct query but frontend doesn't call that endpoint
- Fix: added `assigned_targets` to SELECT, use actual value

### UX: Clickable Pass/Warn/Fail badges
- ComplianceHealthInfographic badges are now buttons with `onStatusClick` prop
- Clicking "4 Fail" navigates to `/incidents?site_id=X&status=fail`
- Wired up in SiteDetail.tsx

### OpenClaw fix
- v2026.4.1 → v2026.4.5 auto-updated, `streamMode` config key deprecated
- `openclaw doctor --fix` migrated config, restart resolved

## Trigger

Dashboard showed "Scan Coordination: 0 targets" for all 3 appliances despite hash ring fix being deployed. User asked for round table evaluation.

## Investigation

1. **DB confirmed targets ARE assigned** (2-1-1 across 3 nodes) — round-robin working correctly
2. Dashboard "0 targets" was stale frontend cache — backend data was correct
3. Found 3 code bugs during investigation:
   - `appliance_db_id` referenced 3 times but **never defined** — NameError caught by `except Exception: pass`, silently disabling discovery ownership filter
   - `canonical_aid` freshly reconstructed instead of using DB-stored `canonical_id` — fragile
   - Two competing `normalize_mac` functions (sites.py = colons, hash_ring.py = stripped) — confusing

## Round Table — Mesh Architecture

**Participants:** CCIE, Principal SWE, Product Manager, DB Engineer

| Persona | Verdict |
|---------|---------|
| CCIE | Production-functional, not enterprise-ready. Cross-subnet reachability unaware. Credential scoping bug = HIPAA finding. |
| Principal SWE | Architecture sound, implementation has 3 bugs a linter would catch. Same-day fixes. |

[truncated...]

---

## 2026-04-08-session-201-auth-enterprise-hardening-flywheel-fleet-ots-round-tables.md

# Session 201 — Auth Enterprise Hardening + Flywheel/Fleet/OTS Round Tables

**Date:** 2026-04-08
**Commits:** 17
**Daemon Version:** 0.3.84
**Migrations:** 139-143
**Tests Added:** 41 (28 flywheel + 13 RBAC)

## Summary

Three round-table audits (flywheel, fleet orders, OTS/compliance packets) plus auth enterprise hardening and critical production bug fixes. Every audit verified production state on VPS — not just code.

## Auth Enterprise Hardening (6 items)
1. MFA pending tokens → Redis (in-memory fallback)
2. Redis rate limiter (RedisRateLimiter class, was in-memory only)
3. Password history (last 5, prevents reuse, migration 139)
4. Bcrypt cost factor 14 rounds (was 12)
5. API key 1-year expiry on partners (migration 139)
6. Audit log 7-year retention + daily cleanup loop

## Password Change HTTP 500 Fix
- Root cause: FastAPI route ordering — `/{user_id}/password` matched before `/me/password`
- `"me"` parsed as UUID → `invalid UUID 'me': length must be 32..36`
- Fixed by moving `/me/password` route before `/{user_id}/password`

## Login Page Dark Mode Fix
- Labels invisible: CSS variable `--label-primary` was white (dark mode) on fixed light gradient
- Added `.light-login` class to force dark text on light bg login pages

## Human-Readable Error Messages
- Replaced raw `HTTP ${status}` fallback across admin/partner/client portals
- 500 → "Something went wrong...", 403 → "Permission denied", 429 → "Too many requests"

## Flywheel Round Table (9 items)
1. **Go daemon ReloadRules()** — root cause of 21 promoted rules with match_count=0
2. Learning sync verified (push via fleet orders sufficient)
3. promotion_audit_log wired (was 0 rows — table existed but nothing wrote to it)
4. Pattern signature standardized to `incident_type:runbook_id` (removed hostname)
5. Runbook mapping uses DB table as source of truth
6. Telemetry 90-day retention cleanup in flywheel loop
7. l1_rules.source CHECK constraint (migration 140)
8. Dashboard "Promoted Matches (30d)" metric card
9. Test coverage 9→28

## Fleet Order Security Round Table (6 items)
1. **CRIT: Auth added to GET pending orders** (was returning 200 with NO auth — verified in prod)
2. **CRIT: Dangerous order types blocked before server key received** (update_daemon, nixos_rebuild, etc.)
3. **HIGH: github.com removed from binary download allowlist** (too broad)
4. **HIGH: Order completion validates site_id ownership** (was cross-site spoofable)
5. **MED: Nonce TTL reduced 24h → 2h**

[truncated...]

---

## 2026-04-08-session-202-round-table-audit-22-fixes-38-tests.md

# Session 202 - Round Table Audit: 22 Fixes, 38 Tests

**Date:** 2026-04-08
**Started:** 19:57
**Previous Session:** 201
**Commits:** 2 (4ae8a2d, 4647faf)

---

## Goals

- [x] Gap analysis: identify all untested modules across 4 codebases
- [x] Round-table audit: 5 parallel swarms, 67 files, Principal SWE + DBA + CCIE + PM
- [x] Execute CRITICAL security fixes (C1-C8)
- [x] Execute HIGH DRY/robustness fixes (H1-H17)
- [x] Execute MEDIUM fixes (M1-M5)
- [x] Fix pre-existing Go orders test failures
- [x] Write regression tests (38 new)
- [x] Deploy to production

---

## Progress

### Completed

**Round-Table Audit (5 swarms, 67 files)**
- Swarm 1: Backend security (11 files) — tenant middleware, CSRF, rate limiter, OAuth, crypto
- Swarm 2: Backend core (10 files) — agent_api, client_portal, email, fleet, partners
- Swarm 3: Go daemon (18 files) — driftscan, netscan, incident, state, healing
- Swarm 4: Go agent + infra (22 files) — discovery, config, transport, billing, device sync
- Swarm 5: Routes + sites (6 files) — checkin handler, dashboard API, auth, learning

**8 CRITICAL fixes:** Site spoofing (13 endpoints), unauthenticated endpoints, IDOR, SET LOCAL no-op
**9 HIGH fixes:** Data race (atomic), rate limiter race (Lua), DRY (email, signing, categories), execute_with_retry, HTTP codes
**5 MEDIUM fixes:** 129 queries to execute_with_retry, io.LimitReader, reportDrift consolidation, file permissions
**Go test fix:** 7 orders tests fixed with Ed25519 signing helpers

### Blocked

- iMac SSH port 2222 still broken

---

## Files Changed

| File | Change |
|------|--------|
| agent_api.py | _enforce_site_id() on 13 endpoints |
| device_sync.py | auth_site_id enforcement |

[truncated...]

---

## 2026-04-09-session-203-portal-round-tables-audit-proof-fixes.md

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

[truncated...]

---

## 2026-04-10-session-204-adversarial-attestation-rbac-fleet-audit-website-copy.md

# Session 204 — Enterprise Hardening + Installer Redesign

**Date:** 2026-04-10 to 2026-04-11 (16+ hours)
**Previous Session:** 203
**Daemon:** v0.3.85 → v0.3.86 → v0.3.87 → v0.3.88
**Commits:** 40+
**Migrations:** 151, 152, 153, 154

---

## Enterprise Installer (NEW — from scratch)

- Raw disk image Nix derivation (`iso/raw-image.nix`) — complete NixOS system as compressed raw image
- 1.0GB compressed (zstd-19), 7.6GB decompressed, 3 partitions (ESP + root + MSP-DATA)
- Installer rewrite (`appliance-image.nix`) — dd-based, zero network, ANSI visual progress
- eMMC support: initrd modules (mmc_block, sdhci_pci, sdhci_acpi), partition naming, drive detection
- Poweroff not reboot (no USB-snatching race)
- No efibootmgr (was hijacking BIOS boot order, preventing reinstalls)
- No dialog (was freezing on T740, blocking zero-friction)
- Config from USB partition (offline provisioning)
- Shared drive detection (`iso/detect-drive.sh`) — single source of truth
- Nix daemon version: single `daemonVersion` variable (was duplicated)
- Spec: `docs/superpowers/specs/2026-04-11-installer-redesign.md`
- Plan: `docs/superpowers/plans/2026-04-11-installer-redesign.md`
- T740 successfully installed via enterprise installer on eMMC — v0.3.88 from first boot

## Security — 5 Trust Boundary Audits

### Cackle-level adversarial audit (Identity, Policy, Execution, Attestation, Access)

**Critical fixes:**
- Evidence submit cross-site injection (auth_site_id enforcement)
- SSO cross-org login (org_id scoping on user lookup)
- Magic link MFA bypass (enforce org MFA on all login paths)
- Shell injection in run_backup_job (regex validation)
- L2 raw script execution blocked (runbook-only enforcement)
- enable_emergency_access added to dangerousOrderTypes
- Healing orders respect disabled_checks (client authority enforced)
- canonical_id + merge_from_ids UnboundLocalError (500 on new registration)

**Infrastructure:**
- Migration 151: 69 DELETE protection triggers on all evidence + audit tables
- Migration 152: FK on client_approvals.acted_by, created_by columns on 3 tables
- Migration 153: Appliance soft-delete (deleted_at + deleted_by)
- Migration 154: Index on discovered_devices(appliance_id, device_status)
- Chain gap detection in verify_chain_integrity
- Timestamp validation on evidence submit (reject backdated/future)
- Fleet order health check: systemd transient timer (survives daemon restart)
- Fleet order immutability trigger after completion
- HIPAA 7-year retention enforced (removed 3-year auto-purge)

[truncated...]

---

## 2026-04-11-session-205-healing-guardrails-telemetry-trigger-migration.md

# Session 205 — Flywheel Intelligence + Scoring Architecture + Persistence Runbooks

**Date:** 2026-04-11 → 2026-04-12
**Previous Session:** 204

---

## Goals

- [x] Write automated guardrail tests for healing pipeline
- [x] Formalize telemetry→remediation trigger as migration 155
- [x] Fix zombie/stuck incident cleanup
- [x] Fix telemetry trigger UUID safety (500 errors)
- [x] Fix site activity `title` column error
- [x] Build recurrence-aware L2 escalation
- [x] Ship flywheel intelligence: velocity, auto-promotion, cross-correlation
- [x] Build canonical check_type_registry (single source of truth)
- [x] Fix scoring engine — was ignoring 12/19 daemon check names
- [x] Fix scoring oscillation — latest-per-check replaces last-50-bundles
- [x] Add 24h staleness cutoff to scoring
- [x] Build persistence-aware runbooks (RB-WIN-PERSIST-001, RB-WIN-PERSIST-002)
- [x] Add configure_dns fleet order handler
- [x] Mark BitLocker as not_applicable for VMs without TPM
- [x] Clean 11 zombie incidents from production
- [x] Fix provision modal dark mode text visibility

---

## Files Changed

| File | Change |
|------|--------|
| `backend/tests/test_healing_pipeline_integrity.py` | 15 guardrail tests (runbooks, monitoring sync, L1 steps, circuit breaker, registry completeness) |
| `backend/migrations/155_telemetry_remediation_sync_trigger.sql` | Trigger with UUID safety |
| `backend/migrations/156_flywheel_recurrence_intelligence.sql` | escalation_reason, recurrence_velocity, correlation_pairs tables |
| `backend/migrations/157_check_type_registry.sql` | 69 canonical check names, categories, HIPAA controls |
| `backend/health_monitor.py` | Zombie cleanup, stuck-resolving cleanup (>3d) |
| `backend/agent_api.py` | Recurrence-aware L2 escalation (3+ in 4h bypasses L1), monitoring-only from registry |
| `backend/l2_planner.py` | Recurrence prompt, escalation_reason recording, persistence runbooks in catalog |
| `backend/background_tasks.py` | recurrence_velocity_loop, recurrence_auto_promotion_loop, cross_incident_correlation_loop |
| `backend/db_queries.py` | Latest-per-check scoring, 24h staleness, registry loader, all daemon check names mapped |
| `backend/routes.py` | /flywheel-intelligence endpoint |
| `backend/sites.py` | Remove non-existent `title` column from activity query |
| `main.py` | Register 3 new background tasks, load check registry + monitoring-only at startup |
| `frontend/src/pages/Dashboard.tsx` | Flywheel Intelligence card |
| `frontend/src/pages/SiteDetail.tsx` | Provision modal dark mode text fix |
| `frontend/src/hooks/useFleet.ts` | useFlywheelIntelligence hook |
| `frontend/src/hooks/index.ts` | Export useFlywheelIntelligence |
| `frontend/src/utils/api.ts` | flywheelApi.getIntelligence |
| `appliance/internal/orders/processor.go` | configure_dns handler |

[truncated...]

---

## 2026-04-12-chaos-lab-v2.md

# Chaos Lab v2 — Session 205

**Date:** 2026-04-12  
**Host:** MaCs-iMac.local (192.168.88.50)  
**Session:** 205 continuation

---

## Problem Diagnosed

Fleet was showing 32 `rogue_scheduled_tasks` incidents/day, 32 `defender_exclusions`, 28
`windows_update` — all on ws01 (192.168.88.251). User suspected continuous attacks.

**Actual root cause:** Chaos lab cron hadn't attacked since April 1 (11 days prior).
The incidents were from **infected VM state** left behind by old chaos runs.
ws01's Windows Task Scheduler had 9 persistent rogue tasks baked in:
- ChaosBackdoor
- MaliciousTask
- SystemHealthCheck
- SystemMaintenance
- SystemMaintenanceTask
- SystemOptimizer
- SystemStartup
- SystemStartupPersistence
- SystemUpdate

Each task had a daily schedule. On firing, it'd modify defender exclusions /
registry / service state, the appliance would detect + heal the symptom, but
the task XML remained to fire again next cycle.

---

## Actions Taken on iMac

### 1. Cleaned ws01 (192.168.88.251)
WinRM PowerShell via `chaos-lab/scripts/winrm_attack.py`:
- `Unregister-ScheduledTask` on all 9 rogue tasks
- Deleted XML templates from `C:\Windows\System32\Tasks\`
- Removed non-system defender exclusions
- Verified: **0 remaining rogue tasks**

Next appliance scan: **19 Windows checks, 0 failing** on ws01.

### 2. Clean snapshots of all 4 VMs
DC previously had NO snapshots (that's why restores broke AD clock — nothing
to restore to). Now all 4 have a known-good baseline named `clean-2026-04-12`:
- `northvalley-dc` → UUID 1634a5f7-6264-4eda-99ed-484d52166f0f
- `northvalley-ws01` → UUID f5263585-bc4a-4cfc-95cd-8b0b3d0d1490
- `northvalley-srv01` → UUID eaaf939e-2dc9-4de2-aec1-2c058d140225
- `northvalley-linux` → UUID d3d4d039-bdd8-46d0-9c0f-5bebc960d519

[truncated...]

---

## 2026-04-12-session-205-cont-migration-hardening-fleet-outage.md

# Session 205 (continuation) — Migration Hardening, Fleet Outage, Hardening Ship

**Date:** 2026-04-12 (afternoon — same UTC day as morning time-travel Phases 2/3)
**Focus:** Ship Phase 2/3 time-travel to VPS, discover silent migration-drift outage, harden deploy, investigate residual fleet-order delivery gap
**Outcome:** Migration auto-apply shipped fail-closed; CI deploy pipeline no longer silently swallows failures; docker-compose.yml now source-controlled; **one residual bug identified but not fixed** — see next-session item

## Timeline

**Morning: Phase 2+3 committed and pushed to main** (4 commits atop Phase 1): `a7f5569`, `d30296d`, `eacf884`, `90f9a6e`

**12:48 UTC — first CI failure cascade begins.** Phase 1 shipped a bad import (`from .auth import require_appliance_bearer` — function is in `.shared`). All 5 subsequent deploys failed until `f5c0b37` at 13:15 fixed ESLint errors in `ReconcileEvents.tsx`.

**13:53 UTC — v0.4.0 fleet order created.** `fleet_cli.py create update_daemon --version 0.4.0 …`. Binary uploaded to `https://api.osiriscare.net/updates/appliance-daemon-0.4.0` (SHA256 `ce822cd8…`). osiriscare-1 (7C:D3:0A:7C:55:18) completed successfully at 13:53:54; osiriscare-2 and osiriscare-3 silently stayed on v0.3.91 / v0.3.92.

**14:06 UTC — VPS disk full.** `/dev/sda1 150G used 148G free 0 100%`. `mcp-postgres` crashing with "No space left on device" trying to write postmaster.pid. Root cause: no `nix-gc.timer` existed on VPS — `/nix/store` grew to 98G since January without cleanup.

**14:08 UTC — outage resolved.** `nix-collect-garbage -d` freed **161 GiB**. `df -h /` → 89G free. Postgres recovered.

**14:22 UTC — nix.gc + nix.optimise config added** to `/etc/nixos/configuration.nix` (repo-local copy in `/tmp/vps-configuration.nix`). `nixos-rebuild switch` → both timers now active, next fire Mon 2026-04-13 00:00 UTC, weekly cadence, deletes generations older than 14d, `persistent = true`.

**14:35 UTC — stale fleet orders cancelled.** 4 older active orders (v0.3.92 update_daemon + diagnostics + restart_agent) were blocking v0.4.0 delivery. Cancelled via `fleet_cli.py cancel`.

**14:40 UTC — "column boot_counter does not exist" discovered.** Migration 160 was committed + deployed but never applied to the DB. Checkin STEP 3.5b crashed every cycle with `column "boot_counter" does not exist`, poisoning the asyncpg transaction. STEP 4.5 (fleet orders) aborted silently. Backend returned HTTP 200 while fleet-order delivery starved for 90 min.

**14:43 UTC — migration 160 applied manually.** `docker exec -i mcp-postgres psql -U mcp -d mcp < /opt/mcp-server/dashboard_api_mount/migrations/160_time_travel_reconciliation.sql`. Columns + `reconcile_events` + DELETE trigger + RLS policies created.

**14:45 UTC — round-table consultation dispatched** on systemic migration-apply hardening. Principal SWE / CCIE / Senior DB Engineer / PM consensus: option (a) = FastAPI lifespan fail-closed startup apply + harden existing CI gate.

**14:50-14:58 UTC — 6-step hardening implemented:**
1. Backfill `schema_migrations` on VPS (152 rows, checksums matching `migrate.py:71`)
2. `main.py` lifespan: `cmd_up()` fail-closed + `SystemExit(2)` if pending after apply
3. `migrate.py cmd_up`: `pg_advisory_lock(8675309)` to serialize concurrent replicas
4. CI gate: `set -e` + `grep -oE '[0-9]+ pending$'` post-apply assertion (removed `|| echo` silent swallow)
5. `/api/admin/health`: new `check_schema()` probe returning `{"schema": {"applied": N, "pending": [...]}}`
6. `sites.py`: 9 `logger.warning` → `logger.error` on transactional step failures

**14:58 UTC — deploy failure #1.** `mcp_app` can't `CREATE TABLE` in public schema. `migrate.py ensure_migrations_table` required `mcp` superuser. Workaround: add `MIGRATION_DATABASE_URL=postgresql://mcp:PASS@mcp-postgres:5432/mcp` to VPS `/opt/mcp-server/docker-compose.yml`.

**15:03 UTC — deploy failure #2.** My CI post-apply grep used `grep -c pending` which false-positived on `pending_alerts` migration NAME + `0 pending` summary line. Fixed at `bb4b775` with `grep -oE '[0-9]+ pending$'` to match only the summary.

**15:07 UTC — mcp-server restarted with v0.4.0 Phase 2+3 code + MIGRATION_DATABASE_URL env + migration 160 applied.** Startup-apply log line: `{"applied": 157, "event": "No pending migrations", "logger": "main", "level": "info", "timestamp": "2026-04-12T15:07:01.057635Z"}`.

**15:15 UTC — deploy GREEN** (`24309562868`).

**15:25 UTC — docker-compose.yml source-controlled.** Closed the tribal-state gap: `MIGRATION_DATABASE_URL` in VPS compose would be lost on reprovision. Pulled compose from VPS, committed to `mcp-server/docker-compose.yml`, added CI step `Deploy docker-compose.yml to release` + diff-aware sync, switched `docker compose restart` → `docker compose up -d` so env changes take effect.

**15:27 UTC — deploy `13c0026` GREEN** (`24309871482`).

## What Shipped (verified at runtime)


[truncated...]

---

## 2026-04-12-session-205-time-travel-reconciliation.md

# Session 205 (continuation) — Time-Travel Reconciliation Phases 2 + 3

**Date:** 2026-04-12
**Focus:** Agent time-travel reconciliation — detect and recover from VM snapshot revert, backup restore, disk clone, power-loss rollback, hardware replacement.
**Outcome:** Phases 2 + 3 complete. Round-table green-lit. One narrow-replay security fix (I1) landed inline.

## Context

Phase 1 shipped in an earlier session: backend foundation (migration 160 adds `boot_counter` / `generation_uuid` / `nonce_epoch` / `reconcile_events` append-only audit, plus `reconcile.py` with Ed25519-signed plan issuance, 15 invariant tests).

Phase 2 + 3 were scoped in this session under the user's explicit directive:
> "3 then iterate with the round table as you finish each phase to completion"
> "we will do all phases right now"

## Phase 2 — daemon detection + inline plan delivery

### Daemon (Go)
- `appliance/internal/daemon/reconcile.go` — new 250-line detector
  - State files in `/var/lib/msp/`: `boot_counter`, `generation_uuid`, `last_known_good.mtime`, `last_reported_uptime`
  - `Detect()` is pure-read; emits signals `boot_counter_regression`, `uptime_cliff`, `generation_mismatch`, `lkg_future_mtime`
  - `ReconcileNeeded = len(Signals) >= 2` — mirrors backend `MIN_SIGNALS_REQUIRED=2` (regression-tested)
  - Boot counter bumps on construction (daemon-start floor, not kernel reboot)
- `appliance/internal/daemon/reconcile_test.go` — 12 detector tests, including a wire-protocol lock: `TestSignalConstants_MatchBackendWireProtocol` pins Go signal strings to `reconcile.py` constants
- `appliance/internal/daemon/daemon.go` — wired into `runCheckin`:
  - Before Checkin: call `Detect()`, populate `CheckinRequest.{BootCounter,GenerationUUID,ReconcileNeeded,ReconcileSignals}`
  - After success: `WriteLastReportedUptime(req.UptimeSeconds)` + `TouchLKG()`
- `appliance/internal/daemon/phonehome.go` — `CheckinRequest` gained 4 fields (all `omitempty` for old-fleet compat); `CheckinResponse.ReconcilePlan` pointer + `ReconcilePlan` struct with `SignedPayload` (server-provided canonical JSON, verified byte-exact)

### Backend (Python)
- `mcp-server/central-command/backend/reconcile.py` — refactored: extracted `issue_reconcile_plan(db, req)` helper, called from both `POST /reconcile` endpoint and checkin handler
- `mcp-server/central-command/backend/sites.py`
  - `ApplianceCheckin` model gained `boot_counter`, `generation_uuid`, `reconcile_needed`, `reconcile_signals`
  - STEP 3.5b (savepoint-wrapped): persists boot_counter (`GREATEST(...)`) + generation_uuid on every checkin
  - Inline reconcile plan issuance before return: if `reconcile_needed` + ≥2 signals → open SQLAlchemy session via `async_session`, call `issue_reconcile_plan`, ship plan in response `reconcile_plan` key
  - Explicit rollback + close on `_rsess` in except/finally (hygiene fix from round-table)
- Admin-pool usage documented with a prominent comment block (don't flip to `tenant_connection`)

### Round-table review (Phase 2)
- **Verdict:** ship-ready with 3 fixes applied inline:
  - C1 — `_reconcile_session` admin-pool intent documented (prevents future refactor breaking RLS)
  - C2 — explicit session rollback/close on exception paths
  - C3 — Go-side JSON parity test (`TestReconcilePlanJSON_WireParity`) pins backend payload keys to daemon struct tags
- Compatibility verified end-to-end: old daemon + new backend = no breakage (unknown JSON fields ignored); new daemon + old backend = no crash (nil `ReconcilePlan` early-return)

## Phase 3 — daemon apply + forensic UI

### Daemon (Go)
- `appliance/internal/daemon/reconcile_apply.go` — new 230-line handler
  - Structural validation (non-empty required fields)
  - Appliance scope exact-match (`plan.ApplianceID == orderProc.ApplianceID()`)

[truncated...]

---

## 2026-04-13-session-205-phase15-a-spec.md

# Session 205 Phase 15 — A-Spec Execution Hygiene

**Date:** 2026-04-13
**Branch:** main
**Commits:** 67555a8 → 2bd3086 (7 commits)
**Prior context:** Phase 14 T2.1 Part 1 shipped magic-link HMAC module + tracking table (commit 431e407). Round-table audit then graded the Session 205 delivery as B-/C+ on execution hygiene. User directive: ship Part 2, then bring everything to A spec.

## Shipped

### Part 2 — magic-link approval wiring (commit 67555a8)

End-to-end closure of the email → click → approve/reject → attested-bundle
loop. Three pieces:

- **privileged_access_notifier.py** — `_mint_approval_links()` mints per-
  recipient approve/reject token pairs only when the bundle is
  INITIATED + request still pending + site has
  client_approval_required=true. Per-recipient SAVEPOINT around mint
  pairs so a bad email cannot poison the SELECT-FOR-UPDATE batch. The
  dispatch loop now sends ONE email per client recipient with their own
  URLs (was a bulk email pre-Part 2 — would have leaked single-use
  tokens to anyone forwarded the message).

- **privileged_access_api.py** — `POST /api/client/privileged-access/magic-
  link/consume` peeks token_id, verify_and_consumes, dispatches to the
  shared `_execute_client_approval` / `_execute_client_rejection`
  helpers. Token authorizes the action; the ATTESTED ACTOR is still the
  authenticated session user (via='magic_link' tag in approvals[]).

- **PrivilegedAccessAct.tsx** + `/portal/privileged-access/act` route —
  approve consumes immediately; reject gates on a 5-char reason
  textarea. 401/403/400 mapped to user-legible copy.

### Phase 15 #1 — chain trigger E2E tests (commit 27b5b51)

`tests/test_privileged_chain_triggers_pg.py` — 28 cases against a real
Postgres service container. Full coverage of migration 175 (INSERT
enforcement) and migration 176 (UPDATE immutability) plus an explicit
regression guard that fails on the Session 205 `%%` signature (error
message must contain `PRIVILEGED_CHAIN_VIOLATION` and NOT
`too many parameters specified for RAISE`).

CI job `privileged-chain-pg-tests` added before `deploy`. Deploy now
depends on tests passing. **All 28 chain-trigger cases green in CI.**

### Phase 15 #2 + #3 — magic-link tests + separate HMAC secret (commit b76fbcd, fix 3fbdaca)

`tests/test_privileged_magic_link_pg.py` — 15 cases covering:
mint tracking-row write, single-use consumption, tampered HMAC
rejection, tampered exp rejection, action-mismatch, session-email

[truncated...]

---

## 2026-04-13-session-206-flywheel-close-offline-detection-deploy-restart.md

# Session 206 — Flywheel measurement loop closes end-to-end + enterprise offline detection + deploy-restart bug

**Date:** 2026-04-13
**Branch:** main
**Last commit:** e305641
**Outcome:** Phase 15 closed (task #122 completed). 9 distinct bugs fixed; `promoted_rules.deployment_count` incremented from 0→1 in production for the first time in this fleet's history.

---

## TL;DR

User asked to verify whether the round-table audit's claim "the flywheel is broken" was real. Backfill validation surfaced **5 original Phase 15 bugs + 4 orthogonal bugs latent in the system**. All fixed. Final proof:

```
rule_id                       | deployment_count | last_deployed_at
L1-AUTO-RANSOMWARE-INDICATOR  |                1 | 2026-04-13 18:56:51 UTC
```

Plus shipped enterprise QoL: appliance offline-detection loop, recovery alerts, API status unification, Prom per-appliance gauge, deploy-workflow restart fix.

---

## Bugs found and fixed

### Original 5 Phase 15 bugs (round-table flywheel audit)

| # | Bug | Fix commit |
|---|---|---|
| 1 | `learning_api.py` admin-bulk-promote bypassed `issue_sync_promoted_rule_orders` | `883e5ec` |
| 2 | `client_portal.py` client-approve bypassed the same | `883e5ec` |
| 3 | Regime detector missed always-bad rules (no delta = no event) | `883e5ec` (added `classify_absolute_floor` + lifetime auto-disable) |
| 4 | No dashboard surface for unhealthy promoted rules | `1ce23f3` (added `unhealthy_promoted_rules` to flywheel-intelligence + UI band) |
| 5 | 43 historical orphan promoted_rules with no rollout order ever issued | `49eaadf` + `e305641` (reconcile script with live-checkin filter) |

### 4 orthogonal bugs surfaced during validation

| # | Bug | Fix commit | How surfaced |
|---|---|---|---|
| A | `main.sign_data` returned `hashlib.sha256(data).hexdigest() * 2` (SHA256 doubled to 128 hex chars — passes hex validator, fails Ed25519 verify) when `signing_key` was None | `4c66323` | Reconcile script ran via `docker exec python3` — fresh process, lifespan never ran, signing_key=None, produced bogus signature, appliance rejected with "tried 1 keys" |
| B | All promoted rule YAML had `action: execute_runbook` but Go daemon's `allowedRuleActions` whitelist only accepts `run_windows_runbook`/`run_linux_runbook`/etc | `75e23e1` (action translation) + `4c2d9a9` (test fixture rename) | After fix A, completion came back "action X not in allowed actions" |
| C | All promoted rule YAML had no `conditions:` block; Go daemon's `processor.go:163` requires `len(rule.Conditions) > 0` | `1a3aeee` (build_daemon_valid_rule_yaml synthesizer) + `e305641` (drop nonexistent description column) | After fix B, completion came back "rule must have at least one condition" |
| D | **Deploy workflow `docker compose up -d` is a no-op when compose config hasn't changed.** Bind-mounted Python code was written to disk but the running interpreter kept old modules. Container ran continuously from 12:12 to 18:03 UTC — 6 hours of "successful" deploys that never actually loaded | `5fe611a` (added explicit `docker compose restart mcp-server frontend`) | Verified migration 180 had applied at 16:33 (separate `docker exec migrate.py` step) but `mark_stale_appliances_loop` wasn't in the running task registry |

### Enterprise QoL: F1–F4 offline detection (`e85d604` + migration 180)

- **F1**: `mark_stale_appliances_loop` runs every 2 min; `UPDATE site_appliances SET status='offline', offline_since=NOW(), offline_event_count++` when `last_checkin > 5 min`. Critical email on first transition (debounced via `offline_notified` flag).
- **F2**: Checkin STEP 3 upsert stamps `recovered_at` in CASE if prior `status='offline'`. Post-upsert savepoint reads it and emits `appliance_recovered` info alert + resets `offline_notified`.
- **F3**: API unification — `/api/sites` and `/api/sites/{id}/appliances` now return `live_status` as authoritative `status`. Stored DB value exposed as `stored_status` for admin diagnostics only. Frontend can't accidentally render stale.
- **F4**: Prom per-appliance gauge `osiriscare_appliance_offline{site_id, appliance_id, display_name, since_sec}`. Existing aggregate gauge stayed; new per-row gauge gives alerting cardinality.


[truncated...]

---

## 2026-04-13-session-206-flywheel-spine-redesign-and-prod-unblock.md

# Session 206 — Flywheel Spine redesign + prod unblock + 24h shadow window

**Date:** 2026-04-13
**Started:** 17:28 (continuation from Session 205)
**Last commit:** `90515dd` (migration 182 widen CHECK)
**Outcome:** Flywheel redesigned around an event-ledger + state-machine spine. Deployed in prod in shadow mode. 24h observation window kicked off; re-audit ~21:30 UTC 2026-04-14.

---

## TL;DR

Three-act day:

1. **Phase 15 closing** — fixed 5 original flywheel bugs + 4 orthogonal bugs surfaced during validation (silent-sig placeholder, action whitelist, missing conditions, deploy-workflow no-op restart). First-ever flywheel measurement-loop close in prod (`deployment_count=1` on `L1-AUTO-RANSOMWARE-INDICATOR` at 18:56:51 UTC).

2. **Enterprise audit** — round-table found auto-disable was silently broken (`logger.debug` swallowed errors; 2h of SCREEN_LOCK at 0%/83 went undetected). User demanded "ultrathink the solution, don't patch." Round-table: **the flywheel has no spine**. 9 asynchronous hops with no shared state model. Patch cycle will never end without a structural fix.

3. **Spine redesign (R1+R3+R4+R6)** — one append-only event ledger, one state machine, one orchestrator. Migration 181 + `flywheel_state.py` + dashboard endpoint + Prom funnel metrics. 5 transition classes, 12 PG integration tests. Deployed to prod in shadow mode at 21:14 UTC.

---

## Commits shipped (chronological, ~22 commits)

| Commit | Scope |
|---|---|
| `883e5ec` | flywheel bugs #1–3 + 3-way import shim |
| `1ce23f3` | dashboard surface for underperforming promoted rules |
| `4c66323` | kill silent SHA256-doubled signature placeholder |
| `49eaadf` | reconcile script: live-checkin filter |
| `e85d604` | enterprise offline detection F1–F4 + migration 180 |
| `75e23e1` | execute_runbook → run_{windows,linux}_runbook translation |
| `4c2d9a9` | test fixture rename for realistic runbook IDs |
| `5fe611a` | deploy workflow: force `docker compose restart` |
| `1a3aeee` | build_daemon_valid_rule_yaml synthesizer (3rd daemon gap) |
| `e305641` | drop nonexistent `description` column from l1_rules query |
| `c7d01d6` | 7-item round-table hardening batch |
| `7b4ca24` | RELEASE_SHA stamp/read paths |
| `3cf4490` | deploy retention: prune releases + compose backups |
| `ac8ae8c` | deploy self-trigger on workflow changes |
| `e3589e2` | YAML parse fix (duplicate `run:` key) |
| `d2af234` | **SPINE R1** — ledger + state machine + orchestrator |
| `c20cf12` | **SPINE R3** — dashboard endpoint + Prom funnel |
| `17cd8e8` | **SPINE R4+R6** — Canary + Graduation transitions + rollout wire |
| `fd8da55` | migration 181: move backfill before trigger install |
| `35fe761` | test fixture: add `l1_rules.source` column |
| `6f967ba` | emergency: `import os` fix in background_tasks.py |
| `90515dd` | migration 182: widen site_appliances status CHECK |

---


[truncated...]

---

## 2026-04-14-session-207-substrate-integrity engine + provisioning-auth hardening + installer v23-26 + daemon 0.4.3 deferred completion.md

# Session 207 — Substrate Integrity + Auth Hardening + Installer v23-v26

**Date:** 2026-04-14 → 2026-04-15
**Previous Session:** 206
**Themes:** substrate-integrity-engine, provisioning-auth, installer-iso, daemon-completion-gate, hardware-compat

---

## TL;DR

Eight-hour session. Started chasing a t740 install failure, walked
it through 4 ISO iterations (v23 → v24 → v25 → v26) tracing each
failure mode to root cause. Mid-session pivoted to the bigger ask:
why the platform doesn't tell us when a customer's appliance is
silently broken. Built the **Substrate Integrity Engine** — 11
named invariants asserted every 60s, opens/auto-resolves rows in
a `substrate_violations` table, surfaces in a customer-facing
admin panel. Adversarial audit at the end uncovered the actual
mesh-not-joining root cause (auth_failure_count split-brain on
api_keys); shipped triggers + assertions + a legacy-recovery
script for daemons too old to auto-rekey.

---

## What landed (commits)

**Backend hardening**
- `5ea3504` migrations 204/205 — renumbered stray 103/111, DROP POLICY IF EXISTS guards
- `ec344eb` sites.py:3050 `$8::timestamptz` cast — unblocks t740 checkin 500s
- `fb6c91f` migration 206 — `legacy_uuid` backfill + `install_sessions` TTL cleanup
- `c949fc1` migration 207 + `assertions.py` engine + 8 invariants + main.py wiring
- `d9b6132` migration 208 — row-guard bypass for admin DB user (kills per-tx-flag footgun)
- `e87721c` `/admin/substrate-violations` + `/admin/substrate-installation-sla` + `AdminSubstrateHealth.tsx`
- `001f776` migration 209 (api_keys triggers) + 3 more substrate invariants + recovery script
- `9ba0a7e` migration 209 column-name fix (real `admin_audit_log` schema)
- `859f9ce` `fleet_cli` URL DNS validation + `health_monitor` install-loop alert

**Daemon hardening**
- `46eea2b` daemon 0.4.3 — deferred completion gate. Handler writes
  `pending-update.json` marker; processor skips auto-complete on
  `status="update_pending"`; new `CompletePendingUpdate` posts at
  next startup after 90s decision window. 4 unit tests. Binary
  published to `/var/www/updates/appliance-daemon-0.4.3` sha
  `39c89588dd3cfdd15661480002ed0e988350ea000ef7a19b6af14468c4feb32d`.

**Installer ISO**
- `75aeeeb` v23 — sysfs_read tee-log + first-boot hostname tolerance (failed in test)
- `9bb8b35` v24 — sysfs_read returns 0; `/proc/sys/kernel/hostname`; motd tolerant (failed at install reboot)
- `a6390c5` v25 — installer copies `\EFI\systemd\systemd-bootx64.efi → \EFI\BOOT\BOOTX64.EFI` post-dd, fixes UEFI fallback for HP thin clients
- `19a70d4` v26 — `supported_hardware.yaml` + pre-flight gate; halts on uncertified DMI product. Published `/var/www/updates/osiriscare-installer-v26-19a70d4.iso` sha `d3641806b975a7c20eb7873c619e5aae8186253ac19bfc55c9504b8b0392ebb6`.

[truncated...]

---

## 2026-04-15-session-207-recovery-shell-and-stripe-billing.md

# Session 207 — 2026-04-15 (cont'd)

Recovery-shell fleet order + R+S hardening + full Stripe billing client path.

## Shipped

### enable_recovery_shell_24h fleet order (trilogy)
- **Migration 223** — add `enable_recovery_shell_24h` to `v_privileged_types`
- **fleet_cli.PRIVILEGED_ORDER_TYPES** + **privileged_access_attestation.ALLOWED_EVENTS** + attestation test all updated in lockstep
- **Watchdog Go handler** (`appliance/internal/watchdog/watchdog.go`) — writes pubkey to `/etc/msp-recovery-authorized-keys`, `systemctl start sshd`, arms systemd-run transient timer for 1..24h that stops sshd + wipes keys on expiry. Timer is systemd-enforced (operator oversight can fail; timer can't).
- **NixOS ISO v34** (`iso/appliance-disk-image.nix`) — sshd **enabled but `wantedBy=[]`** (in closure, not autostarted). `AuthorizedKeysFile = /etc/msp-recovery-authorized-keys`. INSTALLER_VERSION v33→v34.

### R+S non-blocking follow-ups (Task #179)
- Rate-limit break-glass GET to **5/hr** via extended `check_rate_limit(window_seconds, max_requests)`
- Reason validator: ≥20 chars, ≥5 distinct chars, must contain alphabetic word (rejects "aaaaaa…")
- Submit endpoint refactored `INSERT … ON CONFLICT (appliance_id) DO UPDATE` — no more 500 on race
- Retrieval now writes `break_glass_passphrase_retrieval` attestation bundle → flows into auditor kit
  - Event added to ALLOWED_EVENTS only (NOT fleet_cli PRIVILEGED_ORDER_TYPES — not a queued order)

### Stripe billing — client self-serve path (sprint A)
- **Dockerfile UID pin** — appuser UID 1000 to match bind-mount ownership
- **stripe==11.3.0** in image via rebuild (deploy workflow does NOT rsync Dockerfile, ad-hoc rebuild required)
- **Migration 224** — `signup_sessions`, `baa_signatures` (append-only, 7yr retention trigger), `subscriptions` (PHI-boundary CHECK comment), `stripe_events` (webhook dedup)
- **`client_signup.py`** — 4 routes: POST /start, /sign-baa, /checkout; GET /session/{id}
- **Webhook dispatch** — billing.py now routes `checkout.session.completed` by `metadata.signup_id` → client_signup handler (partner path unchanged)
- **4 Stripe products created live** — osiris-pilot $299 (one-time), osiris-essentials $499/mo, osiris-professional $799/mo, osiris-enterprise $1299/mo (lookup_keys used, not hard-coded price IDs)
- **Stripe webhook endpoint registered** — `we_1TMdAQBuOIdmSloyW1gccRrw` @ `https://app.osiriscare.net/api/billing/webhook`, signing secret in `.env`
- **Frontend** — `Signup.tsx`, `SignupBaa.tsx`, `SignupComplete.tsx`. Pricing.tsx pilot CTA rewired → `/signup?plan=pilot`. Paid tiers stay Calendly (PM consensus: demo-first for healthcare-SMB qualification).
- **PDF** — `~/Downloads/stripe-key-rotation-guide.pdf` (2pp, covers secret/webhook/publishable rotation + restricted-key upgrade path + compromise scenarios)

### v34 ISO built + pulled
- `~/Downloads/osiriscare-appliance-v34.iso` · 2.2GB · sha256 `3bc5e853...09b0c`

## Not shipped (physical blockers)
- **t740 reflash** — user physically present but multiple USB flashes failed with `squashfs mount failed / device descriptor read error -32` (classic USB-layer flaky write). Box kept booting internal disk's old installer closure at `osiriscare-installer / 0.4.4`. User has v34 ISO; needs fresh USB stick or different port.
- **Phase H5 Vault cutover** — gate is 7 days flat divergence; we're only at day 2 of 7. Also blocked on multi-trust ISO (disk pubkey + Vault pubkey both trusted) since Vault transit keys are non-exportable — can't import existing disk key.

## Decisions locked
- **Option C** for Stripe rotation (use live keys now, rotate after setup) — user accepted
- **Demo-first for paid tiers**, self-serve for $299 pilot only (PM consensus + consultant stances A-D)
- **No BAA with Stripe** — PHI boundary enforced at DB CHECK level + Stripe customer.metadata whitelist
- **Flat 20% partner margin** for v1, tiering deferred
- **Stripe Invoicing day-one** for Pro/Enterprise tiers (not deferred)

## Known-but-deferred
- Partner Connect Express onboarding (phase 2)
- Admin invoice approval queue UI (phase 2)
- Multi-trust ISO v35 for Vault flip (in progress — user said "yes ship now")
- Deploy workflow doesn't rsync Dockerfile or requirements.lock → future image rebuilds still ad-hoc

---

## 2026-04-16-session-208-ots-audit-seo-cluster-t740-roundtable.md

# Session 208 — OTS audit + SEO content cluster + t740 debug round-table

**Date:** 2026-04-16
**Agent:** v0.4.6  |  **ISO:** v37  |  **Schema:** v2.0

## Context

Multiple threads converged today:
- Verify v37 ISO scp (SHA `c7eda339...`) — completed cleanly.
- Enterprise-grade audit of the OpenTimestamps blockchain-stamping feature end-to-end (user clarified: "attestation audit = blockchain stamping from OpenTimestamps").
- Post-audit round-table convened, commentary executed.
- SEO content cluster for the 2026 HIPAA NPRM — 12 marketing pages, JSON-LD, structured data.
- User pushback: don't frame the product as "small practices" — surface the mesh. Positioning rewrite from "1–50 providers" to "single clinic → multi-site provider network → DSO".
- Round-table fixes from an overnight t740 provisioning-failure debug (Pi-hole DNS filter blocking `api.osiriscare.net`): new substrate invariant + dashboard staleness marking.

## Commits landed

| SHA | Summary |
|---|---|
| `bfcefe6` | 2026-ready SEO cluster — 12 marketing pages, mesh-scoped positioning |
| `e5f95db` | SEO audit remediation — canonical collision, deploy static/ gap, JsonLd hardening |
| `eed0958` | OTS audit remediation — upgrade-loop error visibility, auditor-kit rate limit, 13 tamper property tests |
| `821edca` | Make `check_rate_limit` import pytest-safe (try/except relative vs direct-module) |
| `ebc4ee4` | Landing page rework — humans now see the 2026-ready narrative |
| `96ce738` | `provisioning_stalled` invariant + ApplianceCard staleness (t740 round-table) |

## OTS audit findings + remediation

**State of the chain (verified on production):**
- 236,348 total evidence bundles
- 134,099 anchored / 102,170 legacy / 76 pending / 3 batching
- 528 Merkle batches (527 anchored to real Bitcoin blocks 945,322–945,333)
- Reverify sampling 10/10 clean
- Healthy overall

**Three medium-severity findings remediated:**
1. **Logging silent on OTS upgrade failure** — `logger.warning` → `logger.error(exc_info=True)` with structured extras. Double-failure (inner savepoint) now visible.
2. **Auditor kit unrate-limited** — capped at 10/hr via extended `check_rate_limit(..., window_seconds, max_requests)` signature. 429 with `Retry-After` on exceed.
3. **No property-based tamper tests** — added `test_ots_tamper_property.py` (13 cases, pynacl). Exhaustively mutates every byte of chain_hash, bundle_hash, prev_hash, chain_position, signed_data, signature, pubkey; content-swap.

**Residual (non-blocking):**
- `bitcoin_txid` NULL on anchored proofs (OpenTimestamps Python lib returns block-header-only; block height is cryptographically sufficient).

## SEO cluster + positioning rewrite

**New pages (bfcefe6):**
- `/2026-hipaa-update` — NPRM table, 9 controls, FAQ schema
- `/for-msps` — partner-facing positioning
- `/compare/vanta`, `/compare/drata`, `/compare/delve` — comparison matrices
- `/blog`, `/blog/hipaa-2026-ops`, `/blog/prove-fast`, `/blog/evidence-vs-policy`, `/blog/multi-site-dso`

[truncated...]

---

## 2026-04-16-session-208-v38-iso-audit-killswitch.md

# Session 208 cont. — v38 ISO: kernel-audit kill-switch + halt telemetry + compat-match relax

**Date:** 2026-04-16
**Agent:** v0.4.6  |  **ISO:** v38 (this session)  |  **Schema:** v2.0

## Context

Picking up after the Session 208 OTS audit + SEO cluster + round-table that
produced the `provisioning_stalled` invariant. The t740 at the home lab
(192.168.0.104, MAC `84:3A:5B:1F:FF:E4`) was supposed to come up on v37
but wouldn't actually boot the installed system — pings responded, but
port 22 was actively refused (TCP RST), port 80 accepted then hung,
ports 443/2222/9100 timed out. Screen spammed "audit kudewik overflow".
That pointed straight at the Linux kernel audit subsystem.

User call: "if you know its fucked just flash a new iso and let's go
toward fixing." Per the no-lackluster-ISOs rule, v38 batches MUST +
SHOULD items instead of a single-shot fix.

## Root cause

`modules/compliance-agent.nix` set `security.auditd.enable = mkDefault true`.
Combined with the default NixOS execve audit rule, auditd produced events
faster than `kauditd` could drain them on HP t740-class hardware. The
backlog overflowed, the kernel spammed the console with kauditd warnings,
userspace starved (sshd + appliance-daemon never came up functionally).
The live ISO path had already fixed this (`iso/appliance-image.nix:235`
set `security.auditd.enable = lib.mkForce false` + added `audit=0` to the
live kernel cmdline) — but that fix never propagated to the installed
system. Every box built from v25 through v37 inherited the bug silently.

## Changes landed in v38

**MUST (fixes the t740 silent-boot-death):**

1. `iso/appliance-disk-image.nix:278` — added `"audit=0"` to `kernelParams`.
2. `modules/compliance-agent.nix:852` — flipped `security.auditd.enable`
   from `mkDefault true` to `mkDefault false`, with a full comment
   explaining why and how to re-enable safely.
3. `iso/appliance-image.nix:170` — bumped `installer_version = "v38"`
   (Nix attribute used in `/etc/osiriscare-build.json`).
4. `iso/appliance-image.nix:410` — bumped `INSTALLER_VERSION="v38"`
   (bash var used in every `/api/install/report/*` payload).

**SHOULD (closes the silent-halt visibility gap):**

5. `iso/appliance-image.nix:698` — new `post_halt_report()` bash helper.
   Non-blocking. POSTs `{installer_id, installer_version, halt_stage,
   halt_reason, hw_product, bios_vendor, bios_version, log_tail}` to
   `${API_BASE}/api/install/report/halt` with `--connect-timeout 5 -m 10`.

[truncated...]

---

## 2026-04-18-session-209-rls-p0-evidence-chain-stalled.md

# Session 209 — 2026-04-18

## RLS P0 + evidence_chain_stalled invariant

### Arc

Started continuing Session 208 wrap-up tasks:
1. Verify CI green on `eafe45e` (round-table P1 audit-closure batch)
2. Resume Task #38+ audit round-table
3. Watch substrate_health provisioning_stalled fire rate

Pivoted to an active P0 when dashboard data-quality contradictions (streaming data messed up) led to log scrape revealing **2,608 `InsufficientPrivilegeError` RLS rejections in 2h** on `compliance_bundles` INSERT.

### Root cause

Migration 234 (2026-04-18 earlier) flipped `ALTER ROLE mcp_app SET app.is_admin = 'false'` to make RLS fail-closed by default. `shared.py` was supposed to compensate via a SQLAlchemy `connect` event listener issuing `SET app.is_admin = 'true'`. That pattern is **fundamentally incompatible with PgBouncer transaction pooling** — PgBouncer's `server_reset_query = DISCARD ALL` wipes session-level SETs between client borrows, so only the FIRST transaction on each backend had admin context.

A first fix (`ebb9f17`) correctly moved to `after_begin` + `SET LOCAL` (transaction-scoped, survives DISCARD ALL). It failed to work because the listener was bound to `async_session.sync_session_class` — `async_sessionmaker` has no such attribute, AttributeError was swallowed by a `try/except Exception: pass`, and the listener silently never registered.

Second fix (`2ddc596`) corrected the target to `AsyncSession.sync_session_class` (class-level) and narrowed the silent-swallow handler to `(ImportError, AttributeError)` only so future bugs of this shape surface instead of hide.

### Commits

- `b7f6d87` — CI test-stage unblock (ESLint no-explicit-any + billing PHI boundary doc)
- `bf861d6` — Migration 234 column fix (`timestamp` → `created_at` on admin_audit_log)
- `eafe45e` — Migration 235 schema_migrations dup-key fix (removed manual INSERT)
- `ebb9f17` — First RLS fix attempt (loaded but listener never fired)
- `2ddc596` — **Actual P0 fix** — correct `AsyncSession.sync_session_class` target
- `f6c6121` — `evidence_chain_stalled` sev1 substrate invariant

### Evidence that the fix worked

```
# Before 2ddc596
tx1: is_admin= false user= mcp_app
tx2: is_admin= false

# After 2ddc596
tx1: is_admin= true user= mcp_app
tx2: is_admin= true
```

Prod RLS rejection rate: 22/min → 0/min. Evidence bundles resumed flowing (14 inserts in first 15 min post-deploy).

### Instrumentation

New substrate invariant `evidence_chain_stalled` (sev1) — if ≥1 appliance checked in in the last 15 min but 0 `compliance_bundles` inserted in that window, open a violation. Outcome-layer signal catches RLS failures AND any other evidence-insert failure (partition missing, signing key rotation bugs, disk pressure, silent asyncpg exception). Would have fired the 2026-04-18 outage within 15 min instead of waiting for dashboard anomaly analysis hours later.

Total substrate invariants: 28 → 32. Three of the four +4 invariants were shipped in Session 208 work (provisioning_stalled + two others) that memory hadn't reflected until this session's memory update.


[truncated...]

---

## 2026-04-25-session-211-post-round-table-5-step-plan.md

# Session 211 — Post-round-table 5-step plan + sigauth identity-key closure

**Date:** 2026-04-25
**Span:** Single session, ~all-day
**Trajectory:** Round-table-driven consistency hardening across 8 strict CI
gates + 4-commit sigauth design correction.

## Headline

Closed the original 9-task round-table queue (Wave 1 + Wave 2) AND a
follow-up 5-step plan (steps 1-4 done, step 5 deferred behind the
fleet rollout that was step 4D). The trajectory was right but the
position is best described as *honest fragility* — every soft spot is
named, every named soft spot has a permanent gate, every gate has a
ratchet. Round-table caught **8+ substantive bugs** I would otherwise
have shipped, including 3 in code I'd written hours apart in the same
session.

## Strict CI gates locked today

| Gate | Baseline / mode |
|------|-----------------|
| SQL columns INSERT/UPDATE | 0/0 (ratchet) |
| ON CONFLICT uniqueness | 0 (strict) |
| admin_audit_log column lockstep | strict |
| Frontend mutation CSRF | 0 (brace-balanced parser, was regex) |
| FILTER-attaches-to-aggregate | 0 (strict) |
| Privileged-order 4-list lockstep (Py ↔ Go ↔ SQL) | strict |
| Demo-path button → endpoint contract | strict |
| Sigauth legacy-fallback regression guard | strict |
| **Lifespan-pg-smoke as real gate** (#183) | post-205 hard fail |
| **Documentation-drift gate** (#4) | strict |

Pre-session: only `test_no_new_duplicate_pydantic_model_names` was
strict. Net delta: +9 strict gates, +5 baseline ratchets locked at zero.

## #179 sigauth identity-vs-evidence key (4-commit chain)

The substrate `signature_verification_failures` invariant fired 100%
on `north-valley-branch-2` because `signature_auth.py` fell back to
`site_appliances.agent_public_key` (the EVIDENCE-bundle key) when
verifying sigauth (the IDENTITY-key purpose). They're different keys
by design — daemon writes evidence to `/var/lib/msp/keys/signing.key`
and identity to `/var/lib/msp/agent.key`. Closed across:

- **Commit A (`ba68584e`)** — migration 251 adds
  `site_appliances.agent_identity_public_key VARCHAR(64)` with a
  partial index on `(site_id, mac) WHERE NOT NULL`.
  `ApplianceCheckin` Pydantic model gains the field. `sites.py` STEP
  3.6c persists it scoped per-MAC like the evidence-key path.

[truncated...]

---

## 2026-04-28-session-212-phase1-4-priorities-closure.md

# Session 212 — Session 211 next-priorities closure + task #168 RCA deferral

**Date:** 2026-04-28
**Span:** Single session, ~3h
**Trajectory:** 4-phase forward motion through Session 211's stated
next-priorities. Phase 1-3 closed. Phase 4 (task #168 RCA) deferred
behind a forensic-log gate per QA verdict B.

## Headline

All four "next session priorities" from Session 211 are addressed.
Three closed (deploy verified, strict-mode flip auto-executed,
P2 follow-ups landed); one (the underlying jitter that triggered
Phase 1's verification) deferred behind a forensic-log capture gate
because passive evidence cannot disambiguate the leading hypothesis
(pgbouncer routing) from the "only 7C:D3 fails" observation that
weakens it. Two QA round-tables, one substrate ratchet that fired
on its first scan with the expected violation, two prod commits.

## Phase-by-phase

### Phase 1 — verify v0.4.13 fleet rollout (priority #2)

- All 3 appliances on **0.4.13**, last checkin <1 min stale
- `signature_verification_failures` on north-valley-branch-2
  resolved 2026-04-25 20:10:11Z — within the predicted 2h drain
  window
- `sigauth_crypto_failures` resolved 2026-04-25 19:04:34Z (right at
  Commit C deploy)
- 180/180 valid sigauth observations in 1h on north-valley-branch-2,
  0% fail rate. Affirmative signal — not "0 violations because 0
  observations"

QA condition added: `_check_legacy_bearer_only_checkin` must be silent
for all 3 appliances (proxy for "are they actually emitting signed
headers"). Confirmed via per-appliance EXISTS check on
sigauth_observations within 1h. All 3 = `signing=true`.

### Phase 2 — strict-mode sigauth flip (priority #1)

**Surprise:** the auto-promotion worker
(`sigauth_enforcement.sigauth_auto_promotion_loop`, 5-min cadence,
60-sample/6h/0-failure threshold) **already executed the flip**
autonomously at 2026-04-26 01:11:53Z — ~6h after v0.4.13 deploy. All
3 appliances flipped observe→enforce simultaneously. Audit log entry
per appliance: "sustained valid signatures: 359-360 samples in 6h, 0
failures".

Phase 2 reduced from "design + execute" to "verify + ratchet".
Verification surfaced **4 sigauth rejections in 24h on

[truncated...]

---

## 2026-04-29-session-213-flywheel orphan relocation + F2-F3 round-table P0.md

# Session 213 - Flywheel Orphan Relocation + F2 F3 Round Table P0

**Date:** 2026-04-29
**Started:** 06:34
**Previous Session:** 212

---

## Goals

- [x] Diagnose post-relocate orphan-row recreation (mig 252+254 didn't stick)
- [x] Trace upstream cause (`_flywheel_promotion_loop` aggregating execution_telemetry)
- [x] Migration 255 — relocate execution_telemetry/incidents/l2_decisions
- [x] QA round-table on flywheel system architecture
- [x] Ship F2 (phantom-site precondition) + F3 (orphan-telemetry invariant)

---

## Progress

### Completed

**Migrations 254 + 255:**
- `254_aggregated_pattern_stats_orphan_cleanup_retry.sql` — full Step 1+2+3 (merge → delete-collisions → rename) for orphan site cleanup. The original 252 left 115 orphan rows because asyncpg simple-query / explicit BEGIN/COMMIT interaction skipped sub-statements after the first.
- `255_relocate_orphan_operational_history.sql` — closes the upstream cycle. Migrates 19,063 `execution_telemetry` rows, 533 `incidents`, 31 `l2_decisions` from `physical-appliance-pilot-1aea78` → `north-valley-branch-2`. Re-runs `aggregated_pattern_stats` Step 1+2+3 cleanup. **INTENTIONALLY skips `compliance_bundles`** (137,168 rows — Ed25519 + OTS-anchored, site_id is part of the cryptographic binding). Idempotent audit-log INSERT with NOT EXISTS guard.

**Round-table verdict on flywheel (NEEDS-IMPROVEMENT, 7 findings):**
- F1 (P0, next session) — Canonical telemetry view (`v_canonical_telemetry`) closes the relocate-orphan class architecturally
- F2 (P0, shipped) — `PhantomSiteRolloutError` precondition in `safe_rollout_promoted_rule` for `scope='site'`
- F3 (P0, shipped) — `flywheel_orphan_telemetry` sev1 invariant
- F4 (P1) — Centralize site rename behind SQL function + CI gate
- F5 (P1) — Deprecate duplicate `_flywheel_promotion_loop` in `background_tasks.py`
- F6 (P3) — Federation tier for eligibility thresholds
- F7 (P3) — Operator diagnostic endpoint `GET /api/admin/sites/{site_id}/flywheel-diagnostic`

**F2 wired (commit `fe3ab3cc`):**
- `flywheel_promote.py::PhantomSiteRolloutError` exception class
- Precondition in `safe_rollout_promoted_rule` for `scope='site'` queries `SELECT 1 FROM site_appliances WHERE site_id=$1 AND deleted_at IS NULL LIMIT 1` and raises if 0 rows
- `routes.py::promote_pattern` translates to HTTP 409 with structured remediation body pointing operator at admin_audit_log
- `tests/test_flywheel_promote_candidate.py::FakeConn.fetchval` returns 1 (healthy-site fixture)

**F3 wired (commit `fe3ab3cc`):**
- `assertions.py::_check_flywheel_orphan_telemetry` queries 24h orphan rows, threshold > 10
- Added to `ALL_ASSERTIONS` (sev1) + `_DISPLAY_METADATA` + `substrate_runbooks/flywheel_orphan_telemetry.md` (three-list lockstep green)
- Total invariant count: **45 → 46**

**Tests:**
- 69 backend tests pass (lockstep + write-warning + flywheel_promote_candidate)
- `test_assertion_metadata_complete` + `test_substrate_docs_present` green
- Smoke import: `len(ALL_ASSERTIONS) == 46`

[truncated...]

---

## 2026-04-30-session-214-F6 MVP slice + post-Session-213 hardening (P3 partition-aware drift + P2 inner-scan + F6 design spec + F6 MVP migration 261 + flywheel_federation_misconfigured invariant).md

# Session 214 — F6 MVP slice + post-Session-213 hardening

**Date:** 2026-04-30 (overnight continuation from Session 213)
**Started:** 03:15 UTC
**Previous Session:** 213

---

## Goals

- [x] Item 3 — partition-aware drift detection (P3, deferred from Session 213 F4-followup)
- [x] Item 4 — defense-in-depth inner scan within auto-EXEMPT files (P2, round-table deferred)
- [x] Item 1 — F6 design spec (multi-day, design-first)
- [x] Item 2 — confirmed 2026-05-05 sigauth watch is passive
- [x] F6 MVP slice — schema + feature flag scaffolding
- [x] F6 fast-follow — flywheel_federation_misconfigured sev3 substrate invariant

---

## Progress

### Items 3 + 4 — partition-aware drift + inner-scan (commit `86df7625`)

Two-pass UNION query in `_check_rename_site_immutable_list_drift`:
- Pass 1: trigger directly on the table (regular + partitioned parents — relkind expansion from `'r'` to `IN ('r', 'p')` is the actual fix; round-table corrected my narrative)
- Pass 2: trigger on a partition child → surface the PARENT (legacy/manual case)

Defense-in-depth inner scan in `_inner_scan_misuse()` runs on auto-EXEMPT migration files; 3 regex patterns catch actual misuse (UPDATE compliance_bundles + canonical_site_id within 500 chars; INSERT same; reverse-direction). `_strip_sql_comments()` strips `-- ` and `/* */` first so docs co-mention doesn't false-positive.

5 inner-scan self-tests + ratchet wired correctly. Round-table verdict: READY (first pass), 2 polish items (mig 191 narrative correction + runbook Verification SQL update) folded in.

### F6 design spec (commit `86df7625`)

`docs/specs/2026-04-30-f6-federation-eligibility-tier-design.md` — full scoping doc for Federation tier (local/org/platform). Three-week calibration window referenced; 4 round-table risks pre-anticipated; explicit "do NOT bundle with hotfix sessions".

### F6 MVP slice (commit `7dee6d6c`)

Migration 261 — `flywheel_eligibility_tiers` table. 3 seed rows (local/org/platform), ALL `enabled=FALSE`, `calibrated_at=NULL`. Schema-level CHECK constraints enforce calibration discipline (org_isolation_required=TRUE for tier_level >=1; distinct_orgs/sites required when calibrated_at is set).

main.py Step 2 reads `FLYWHEEL_FEDERATION_ENABLED` env var (lenient parser matches `assertions.py::L2_ENABLED` sibling). Triple-gate activation: env=true AND tier.enabled=TRUE AND calibrated_at IS NOT NULL. Default OFF — production behavior unchanged.

Round-table verdict: NEEDS-IMPROVEMENT. 2 P1 + 3 P2 ship-now items integrated:
- P1-1: lenient parser instead of strict `== "true"` (sibling-subsystem mismatch was operator footgun)
- P1-2: NULL out distinct_orgs/sites in higher-tier seeds + CHECK constraints forcing calibration to set them
- P2-3: org_isolation_required column with CHECK (HIPAA boundary at schema level)
- P2-4: logger.info → logger.warning + TODO for Prom counter
- P2-5: reconciled min_distinct_sites=1 on local seed comment

Reviewer said "the structural slice is sound. You did not ship a foot-gun. The OFF-state preservation property holds."


[truncated...]

---

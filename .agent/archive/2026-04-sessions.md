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

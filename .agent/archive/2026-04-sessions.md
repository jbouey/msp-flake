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

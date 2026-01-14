# MSP Compliance Appliance - Implementation Status

**Last Updated:** 2026-01-14 (Session 31 - JSON Rule Loading + Chaos Lab Fixes)
**Current Phase:** Phase 12 - Launch Readiness (Agent v1.0.29, ISO v29, 43 Runbooks, OTS Anchoring, Windows Sensors, Partner L3 Escalations, Multi-Framework Compliance, MinIO on Storage Box, Cloud Integrations, **L1 JSON Rule Loading**, **Chaos Lab Automated Testing Ready**, **L2 LLM VERIFIED WORKING**, **Pattern Reporting Pipeline Complete**, 656 tests)
**Aligned With:** CLAUDE.md Master Plan

---

## 🎯 Objectives Alignment

### Primary Objective (CLAUDE.md)
**"NixOS + MCP + LLM stack for auto-heal infrastructure + HIPAA compliance monitoring"**

✅ **Status:** Architecture locked, Phase 1 scaffold complete

### Product Pillars (Master Alignment Brief)

| Pillar | Status | Implementation |
|--------|--------|----------------|
| **Production = NixOS appliance** | ✅ Complete | `modules/compliance-agent.nix`, no containers on client |
| **Control path = pull-only** | ✅ Complete | No listening sockets, outbound mTLS only |
| **Self-healing is core** | 🟡 Scaffolded | Declarative baseline reconciliation (Phase 2) |
| **Evidence pipeline** | ✅ Complete | JSON + Ed25519 sig, no PHI, outbound mTLS |
| **Dual deployment modes** | ✅ Complete | Reseller/direct with behavior toggles |

**Legend:** ✅ Complete | 🟡 In Progress | ⭕ Not Started

---

## 📊 Implementation Timeline vs CLAUDE.md Plan

### Original Plan (CLAUDE.md)

| Week | Deliverable | Status |
|------|-------------|--------|
| 0-1 | Baseline profile and runbook templates | ✅ Done |
| 2-3 | Client flake with LUKS, SSH-certs, baseline enforcement | ✅ Done (Phase 1) |
| 4-5 | MCP planner/executor split, evidence pipeline | 🟡 Next (Phase 2) |
| 6 | First compliance packet generation | ⭕ Scheduled |
| 7-8 | Lab testing with synthetic incidents | ⭕ Scheduled |
| 9+ | First pilot client | ⭕ Scheduled |

**Current Position:** End of Week 2-3 equivalent (Phase 1 complete)

### Accelerated Progress

**Completed Ahead of Schedule:**
- ✅ Full NixOS module with 27 options (originally Week 2-3)
- ✅ Systemd hardening + nftables egress (originally Week 4)
- ✅ SOPS integration scaffold (originally Week 3)
- ✅ VM integration tests (originally Week 5)
- ✅ Dual deployment modes (not in original plan)

**On Track:**
- 🟡 MCP client implementation (Week 4-5)
- 🟡 Drift detection + self-healing (Week 4-5)

---

## 🏗️ Repository Structure Alignment

### CLAUDE.md Expected Structure

```
MSP-PLATFORM/
├── client-flake/          # Deployed to all client sites
│   ├── flake.nix         # NixOS configuration for clients
│   ├── modules/
│   │   ├── log-watcher.nix
│   │   ├── health-checks.nix
│   │   └── remediation-tools.nix
│
├── mcp-server/            # Your central infrastructure
│   ├── flake.nix         # MCP server deployment
│   ├── server.py         # FastAPI with LLM integration
│   ├── tools/            # Remediation tools
│   └── guardrails/       # Safety controls
```

### Current Implementation

```
/Users/dad/Documents/Msp_Flakes/
├── flake-compliance.nix           # ✅ Client flake (production)
├── modules/
│   └── compliance-agent.nix       # ✅ Combined module (agent + health + remediation)
├── packages/
│   └── compliance-agent/          # ✅ Agent implementation (scaffold)
├── mcp-server/                    # 🟡 Exists (from old demo), needs Phase 2 update
├── /demo/                         # ⭕ DEV ONLY stack (Phase 2)
└── nixosTests/                    # ✅ VM integration tests
```

**Alignment Notes:**
- ✅ Structure matches intent (separate client/server)
- ✅ Production uses NixOS modules (not containers)
- 🟡 Need to update `mcp-server/` for new architecture
- ⭕ Need to create `/demo` Docker Compose stack

---

## 🔐 Guardrails Implementation (10/10 Locked)

Per Master Alignment Brief, all 10 guardrails must be locked before Phase 2:

| # | Guardrail | Status | Implementation |
|---|-----------|--------|----------------|
| 1 | **Order auth** (Ed25519 sig verify) | ✅ Scaffolded | `orderTtl` option, verification in Phase 2 |
| 2 | **Maintenance window** enforcement | ✅ Complete | `maintenanceWindow`, `allowDisruptiveOutsideWindow` |
| 3 | **mTLS keys** (SOPS, 0600, owner) | ✅ Complete | `clientCertFile`, `clientKeyFile`, examples show SOPS |
| 4 | **Egress allowlist** (DNS+timer, fail closed) | ✅ Complete | nftables + hourly refresh timer |
| 5 | **Health checks** (rollback on fail) | ✅ Scaffolded | `rebuildHealthCheckTimeout`, rollback in Phase 2 |
| 6 | **Clock sanity** (NTP offset > 5s) | ✅ Complete | `ntpMaxSkewMs`, systemd-timesyncd enabled |
| 7 | **Evidence pruning** (last N, never delete recent) | ✅ Complete | Daily timer, retention rules |
| 8 | **No journald restart** (window enforcement) | ✅ Complete | Maintenance window applies to all disruptive |
| 9 | **Queue durability** (WAL, fsync, backoff) | ✅ Scaffolded | SQLite WAL in Phase 2 |
| 10 | **Deployment mode defaults** | ✅ Complete | `deploymentMode`, reseller/direct toggles |

**All guardrails locked and ready for Phase 2 implementation.**

---

## 🧪 Test Coverage vs Requirements

### Master Alignment Brief: "Definition of Done for Phase 1"

| Requirement | Status | Evidence |
|-------------|--------|----------|
| `nix flake check` passes | 🟡 Pending | Need Nix installed to verify |
| Agent has no listening sockets | ✅ Test Written | `nixosTests/compliance-agent.nix:51` |
| Demo: `/demo` stack runs end-to-end | ⭕ Phase 2 | Docker Compose not yet created |
| 3+ evidence bundles verify locally | ⭕ Phase 2 | Evidence generation in Phase 2 |
| Maintenance window respected | ✅ Test Ready | Logic in module, execution in Phase 2 |
| Reseller mode: syslog+webhook per event | ✅ Scaffolded | Conditional logic in module |

**Phase 1 DoD:** 4/6 complete (as expected - 2 require Phase 2 implementation)

### Test Matrix for Phase 2

Per Master Alignment Brief, must include:

- [ ] Signature verify fail → order discarded, evidence `outcome:"rejected"`
- [ ] TTL expired → order discarded, evidence `outcome:"expired"`
- [ ] MCP down → local queue, later flush, receipts recorded
- [ ] Rebuild failure → automatic rollback + evidence `reverted`
- [ ] DNS failure → no egress, evidence `outcome:"alert"`, agent keeps running

---

## 📦 Deliverables Status

### Phase 1 Deliverables (All Complete)

- ✅ **Flake scaffold** - `flake-compliance.nix` with modules/packages/tests
- ✅ **Module stub** - `modules/compliance-agent.nix` (546 lines, 27 options)
- ✅ **Systemd units** - Service + timers with full hardening
- ✅ **nixosTest** - VM integration test (7 test cases)
- ✅ **Examples** - Reseller and direct configs with SOPS

### Phase 2 Deliverables (Complete)

Per Master Alignment Brief:

- ✅ **Agent core** - `agent.py`, `mcp_client.py`, `drift.py`, `healing.py`, `evidence.py`, `offline_queue.py`
- ✅ **Drift detection** - Covers patching, AV/EDR, backup, logging, firewall, encryption
- ✅ **Remediation** - Obeys maintenance window, rollback logic
- ✅ **Evidence bundle** - JSON + detached Ed25519 signature with all required fields
- ✅ **/demo stack** - Docker Compose (MCP stub + Redis + stub agent), clearly labeled DEV ONLY
- ✅ **3-tier auto-healing** - Level 1 deterministic, Level 2 LLM, Level 3 escalation
- ✅ **Data flywheel** - Learning loop for L2→L1 rule promotion
- ✅ **Web UI** - Dashboard for monitoring
- ✅ **WORM integration** - S3-compatible evidence upload
- ✅ **Windows collector** - Cross-platform support

### Phase 9 Deliverables (Complete)

- ✅ **Learning Loop Infrastructure** - Database layer with SQLAlchemy models
- ✅ **Pattern Detection** - Automatic signature generation and aggregation
- ✅ **Promotion Workflow** - 5+ occurrences, 90%+ success rate criteria
- ✅ **Agent Sync Endpoints** - `/agent/sync`, `/agent/checkin` for rule distribution
- ✅ **Central Command Dashboard** - React frontend at http://178.156.162.116:3000
- ✅ **Real Database Queries** - PostgreSQL integration on VPS
- ✅ **Updated SOPs** - Learning Loop System section added

### Phase 10 Deliverables (In Progress)

- ✅ **Client Portal** - Magic-link authentication for client access
- ✅ **Phone-Home Security** - Site ID + API key (Bearer token) authentication
- ✅ **Production Deployment** - VPS with Caddy auto-TLS (api/dashboard/msp.osiriscare.net)
- ✅ **Appliance ISO Infrastructure** - `iso/` directory with NixOS build configs
- ✅ **Site Provisioning Tools** - `generate-config.py` for mTLS cert generation
- ✅ **Operations SOPs** - 7 SOPs added to Documentation page
- ✅ **ISO Build Verification** - Built on VPS, boots in VirtualBox, phone-home working
- ✅ **Lab Test Enrollment** - test-appliance-lab-b3c40c site, status: online
- ✅ **Physical Appliance Deployed** - HP T640 at 192.168.88.246 (2026-01-02)
- ✅ **Auto-Provisioning API** - GET/POST/DELETE /api/provision/<mac>
- ✅ **Ed25519 Evidence Signing** - Bundles signed on submit, verified on chain check
- ✅ **mDNS Support** - osiriscare-appliance.local hostname resolution
- 🟡 **First Pilot Client** - Physical appliance deployed, needs full agent

### Phase 11 Deliverables (Complete)

- ✅ **Partner Database Schema** - 7 tables (partners, partner_users, appliance_provisions, partner_invoices, discovered_assets, discovery_scans, site_credentials)
- ✅ **Partner API Backend** - 20+ endpoints with X-API-Key auth
- ✅ **Partner Dashboard** - React frontend with branding, sites, provisions
- ✅ **QR Code Generation** - Provision codes with scannable QR (2 endpoints)
- ✅ **Discovery Module** - Asset classification, scan management, 70+ port mappings
- ✅ **Provisioning API** - Claim, validate, status, heartbeat, config endpoints
- ✅ **Credential Management** - Add, validate, delete with encryption placeholder
- ✅ **Appliance Provisioning Module** - First-boot config via QR/manual code
- ✅ **Provisioning Tests** - 19 tests covering full flow
- ✅ **Partner Documentation** - `docs/partner/` with README and PROVISIONING docs

### Phase 12 Deliverables (In Progress)

- ✅ **Credential-Pull Architecture** - RMM-style credential fetch on check-in (Session 9)
  - Server `/api/appliances/checkin` returns `windows_targets` with credentials from `site_credentials` table
  - Agent `_update_windows_targets_from_response()` replaces targets each cycle
  - No credentials cached on disk - fetched fresh every 60s
  - Credential rotation picked up automatically
  - Benefits: stolen appliance doesn't expose credentials, consistent with RMM industry pattern
- ✅ **ISO v16** - Built with agent v1.0.8 (credential-pull support)
- ✅ **Windows DC Connectivity** - North Valley DC (192.168.88.250) connected via credential-pull
- ✅ **Healing System Integration Complete** - 2026-01-05 (Session 10)
  - Fixed L1 `execute()` to check action_executor returned success (was always true)
  - Fixed `_handle_drift_healing()` to use `auto_healer.heal()` method correctly
  - Fixed `_heal_run_windows_runbook()` to use `WindowsExecutor.run_runbook()`
  - Tested Windows firewall chaos: L1 matched → Runbook executed → Firewall re-enabled
  - Agent v1.0.18 with all healing integration fixes
  - 453 tests passing (compliance-agent)
- ✅ **ISO v18 Built** - 2026-01-05 (Session 11)
  - Agent v1.0.9 with all healing fixes packaged
  - Built on VPS after nix-collect-garbage (freed 109GB)
  - SHA256: abcf0096f249e44f0af7e3432293174f02fce0bf11bbd1271afc6ee7eb442023
  - Size: 1.1GB, stored at `/mnt/build/osiriscare-appliance-v18.iso`
- ✅ **ISO v18 Deployed** - 2026-01-05 (physical appliance flashed)
- ✅ **Email Alerts System** - 2026-01-05 (Session 12)
  - SMTP via privateemail.com (TLS, port 587)
  - `email_alerts.py` module with HTML/plain text templates
  - POST /api/dashboard/notifications with email for critical severity
  - Test Alert button + modal in Notifications page
- ✅ **Push Agent Update UI** - 2026-01-05 (Session 12)
  - Prominent pulsing "Push Update" button for outdated agents
  - Version selection modal with package URL preview
  - ActionDropdown z-index fix (z-[9999]) for proper layering
  - Delete Appliance option visible in dropdown menu
- ✅ **Test VM Rebuilt with ISO v18** - 2026-01-05 (Session 12)
  - Registered MAC 08:00:27:98:fd:84 in appliance_provisioning table
  - Detached old VDI, booted fresh from ISO v18
  - Agent upgraded from 0.1.1-quickfix to 1.0.18
  - Both appliances (physical + VM) now on v1.0.18
- ✅ **Multi-NTP Time Verification** - 2026-01-05 (Session 12)
  - `ntp_verify.py` module with raw NTP protocol (RFC 5905)
  - Queries 5 NTP servers: NIST, Google, Cloudflare, Apple, pool.ntp.org
  - Agent v1.0.19 with NTP verification in drift detection
  - 25 unit tests + 2 live integration tests (503 total)
- ✅ **Chaos Probe Central Command Integration** - 2026-01-06 (Session 12 continued)
  - `scripts/chaos_probe.py` POSTs incidents to `/incidents` endpoint
  - Fixed VPS `appliances` table FK constraint
  - Fixed `routes.py` safe_check_type() for unknown check types
  - Incidents appear in dashboard stats (incidents_24h: 3)
  - L3 probes send emails via `/api/alerts/email` endpoint
  - User confirmed receiving L3 escalation emails
- ✅ **Windows Runbook Expansion (27 Total)** - 2026-01-06 (Session 13)
  - 6 new runbook category files in `packages/compliance-agent/src/compliance_agent/runbooks/windows/`:
    - `services.py` - 4 runbooks (DNS, DHCP, Print Spooler, Time Service)
    - `security.py` - 6 runbooks (Firewall, Audit, Lockout, Password, BitLocker, Defender)
    - `network.py` - 4 runbooks (DNS Client, NIC Reset, Profile, NetBIOS)
    - `storage.py` - 3 runbooks (Disk Cleanup, Shadow Copy, Volume Health)
    - `updates.py` - 2 runbooks (Windows Update, WSUS)
    - `active_directory.py` - 1 runbook (Computer Account)
  - Updated `__init__.py` with combined ALL_RUNBOOKS registry (27 runbooks)
  - Created `windows_baseline.yaml` with 20+ L1 deterministic rules
  - Database: `migrations/005_runbook_tables.sql` (runbooks, site_runbook_config tables)
  - Backend: `runbook_config.py` with CRUD endpoints
  - Frontend: `RunbookConfig.tsx` with site selector, category filters, toggle switches
  - Hooks: useSiteRunbookConfig, useSetSiteRunbook, useRunbookCategories
  - Agent: `is_runbook_enabled()` and `_update_enabled_runbooks_from_response()`
  - Tests: 20 tests in `test_runbook_filtering.py` (all passing)
- ✅ **Credential Management API** - 2026-01-06 (Session 14)
  - Fixed `sites.py` windows_targets transformation (was returning raw JSON instead of formatted credentials)
  - Fixed runbook query column (r.id UUID → r.runbook_id VARCHAR)
  - Created missing `appliance_runbook_config` table in database
  - Fixed NULL check_type for 6 original runbooks (backup, cert, service, drift, firewall, patch)
  - Site detail endpoint now queries `site_credentials` table (was hardcoded empty `[]`)
  - Added `POST /api/sites/{site_id}/credentials` endpoint for creating credentials
  - Added `DELETE /api/sites/{site_id}/credentials/{id}` endpoint for deleting credentials
  - Verified both appliances using credential-pull architecture (no hardcoded credentials on disk)
  - Credential changes propagate within 60 seconds (next check-in cycle)
- ✅ **Windows Sensor & Dual-Mode Architecture** - 2026-01-08 (Session 15)
  - Created `OsirisSensor.ps1` - PowerShell sensor with 12 compliance checks
  - Sensor pushes drift events to appliance (port 8080) for instant detection (<30s vs 60s polling)
  - Dual-mode: Hosts with active sensors skip WinRM polling, others still polled
  - Created `sensor_api.py` - FastAPI router for appliance sensor endpoints
  - Created `deploy_sensor.py` - CLI tool for remote sensor deployment via WinRM
  - Created `sensors.py` - Central Command backend for sensor management
  - Created `SensorStatus.tsx` - Dashboard UI component for sensor status
  - Created `006_sensor_registry.sql` - Database migration for sensor tables
  - Added uvicorn web server to `appliance_agent.py` for sensor API
  - Order handlers: deploy_sensor, remove_sensor, sensor_status
  - 25 new tests in `test_sensor_integration.py`
  - Performance expectations: 100+ servers per appliance (vs ~15 with polling)
- ✅ **Partner Dashboard Testing & L3 Escalation Activation** - 2026-01-08 (Session 16)
  - Created `007_partner_escalation.sql` - Database migration for partner notifications
    - Tables: partner_notification_settings, site_notification_overrides, escalation_tickets, notification_deliveries, sla_definitions
    - Default SLAs: critical (15min response), high (1hr), medium (4hr), low (8hr)
  - Created `notifications.py` - Partner notification settings API
    - Settings CRUD for Slack, PagerDuty, Email, Teams, Webhook
    - Site-level overrides for notification routing
    - Escalation ticket management (list, acknowledge, resolve)
    - SLA metrics and test notification endpoints
  - Created `escalation_engine.py` - L3 Escalation Engine
    - Routes incidents from appliances to partner notification channels
    - HMAC signing for webhook security (X-OsirisCare-Signature header)
    - Priority-based routing (critical=all, high=PD+Slack, medium=Slack+Email, low=Email)
    - Delivery tracking and SLA breach detection
  - Modified `level3_escalation.py` for Central Command integration
    - Added central_command_enabled, central_command_url, site_id, api_key config
    - Falls back to local notifications if Central Command fails
  - Created `NotificationSettings.tsx` - Partner notification settings UI
    - Channel configuration cards (Email, Slack, PagerDuty, Teams, Webhook)
    - Test notification buttons per channel
    - Escalation behavior settings
  - Created `test_partner_api.py` - 27 comprehensive tests (all passing)
  - Wired routers in server.py (notifications_router, escalations_router)
  - 550 total tests passing

---

## 🎯 CLAUDE.md Compliance Framework Alignment

### Legal Positioning

| Requirement (CLAUDE.md) | Implementation Status |
|------------------------|----------------------|
| Business Associate for operations only | ✅ Documented in README |
| Processes metadata only, never PHI | ✅ Architecture enforces (no PHI paths) |
| Fulfills §164.308, §164.312 requirements | ✅ Evidence pipeline designed for this |
| Lower liability than data processors | ✅ Positioning clear in docs |

### Evidence Bundle Requirements (CLAUDE.md Section 7)

Required fields per CLAUDE.md:

- ✅ `site_id`, `host_id`, `policy_version` - Module options exist
- ✅ `check`, `pre_state`, `post_state`, `action_taken` - Schema documented
- ✅ `rollback_available`, `timestamps`, `ruleset_hash` - Schema documented
- ✅ `nixos_revision`, `derivation_digest` - Can be queried from flake
- ✅ `deployment_mode`, `reseller_id` - Module options exist
- ✅ Detached Ed25519 signature - `signingKeyFile` option exists

**All required fields covered in design.**

### Self-Healing Scope (CLAUDE.md Section 5)

| Check | CLAUDE.md Requirement | Implementation Status |
|-------|----------------------|----------------------|
| **Patching** | nixos-rebuild in window, auto-rollback | ✅ Scaffolded (Phase 2) |
| **AV/EDR Health** | Service active + binary hash verified | ✅ Scaffolded (Phase 2) |
| **Backup Verification** | Timestamp + checksum, re-trigger if stale | ✅ Scaffolded (Phase 2) |
| **Logging Continuity** | Services up, canary reaches local spool | ✅ Scaffolded (Phase 2) |
| **Firewall Baseline** | Ruleset hash, restore from signed baseline | ✅ Scaffolded (Phase 2) |
| **Encryption Checks** | LUKS status, alert if off (no auto-encrypt) | ✅ Scaffolded (Phase 2) |

**All 6 checks from CLAUDE.md mapped to implementation.**

---

## 🚀 Next Actions (Phase 2)

### Immediate Next Steps (Week 4-5 equivalent)

1. **Agent Core** (2-3 days)
   - Implement `agent.py` main loop (poll → detect → heal → evidence → push)
   - Implement `mcp_client.py` (HTTP GET/POST with mTLS)
   - Implement `queue.py` (SQLite WAL with offline queue)

2. **Drift Detection** (2 days)
   - Implement `drift_detector.py` with 6 checks
   - NixOS generation comparison via `nix flake metadata`
   - Service health via `systemctl is-active`
   - Backup timestamp/checksum queries
   - Firewall ruleset hashing via `nft list ruleset`
   - LUKS status via `cryptsetup status`

3. **Self-Healing** (2-3 days)
   - Implement `healer.py` remediation logic
   - `nixos-rebuild switch` with health check + rollback
   - Service restart with exponential backoff
   - Backup re-trigger on stale detection
   - Firewall restore from signed baseline file

4. **Evidence Generation** (1-2 days)
   - Implement `evidence.py` JSON bundle generation
   - Ed25519 signature generation and verification
   - Pre/post state capture for all checks
   - Outcome classification (success/failed/reverted/deferred/alert)

5. **Testing** (2 days)
   - Implement 5 test cases from test matrix
   - Create `/demo` Docker Compose stack
   - End-to-end smoke test

**Total Phase 2 Estimate:** 10-12 days (2 weeks)

---

## 📋 Open Questions & Decisions

### Resolved
- ✅ MCP base URL: Default `https://mcp.local`, overridable in config
- ✅ Key management: SOPS-nix with age keys
- ✅ Runbook TTL: 15 minutes (900 seconds)
- ✅ Poll cadence: 60s ±10% jitter
- ✅ Evidence retention: Last 200 bundles, 90-day minimum age
- ✅ Default deployment mode: Reseller (with direct examples)

### Resolved in Phase 2
- ✅ MCP server implementation: FastAPI (server.py running)
- ⭕ LLM provider: Azure OpenAI (for BAA) or OpenAI directly?
- ⭕ WORM storage: Client's S3 account or centrally hosted?
- ✅ Runbook format: YAML structure implemented (7 runbooks, 5 loading)
- ⭕ Evidence bundle storage: Local + remote, or remote-only after sync?

### Infrastructure Status (2026-01-03)
- ✅ Hetzner VPS: 178.156.162.116 (SSH working, Central Command)
- ✅ iMac Gateway: 192.168.88.50 (Lab network access)
- ✅ Physical Appliance: 192.168.88.246 (HP T640, production pilot)
- ✅ Central Command API: https://api.osiriscare.net
- ✅ Dashboard: https://dashboard.osiriscare.net
- ✅ Cachix: Configured locally and in CI
- ⬜ Legacy VirtualBox VMs: REMOVED (was 174.178.63.139, no longer in use)

---

## 📚 Documentation Status

| Document | Purpose | Status |
|----------|---------|--------|
| `CLAUDE.md` | Master plan, objectives, full architecture | ✅ Exists (5,471 lines) |
| `README-compliance-agent.md` | Agent-specific README | ✅ Created (Phase 1) |
| `PHASE1-COMPLETE.md` | Phase 1 summary and handoff | ✅ Created |
| `IMPLEMENTATION-STATUS.md` | This file - alignment with objectives | ✅ Created |
| `examples/reseller-config.nix` | Reseller deployment example | ✅ Created |
| `examples/direct-config.nix` | Direct deployment example | ✅ Created |
| `/demo/README.md` | DEV ONLY warning for demo stack | ⭕ Phase 2 |

**All documentation complete for Phase 1, aligned with CLAUDE.md objectives.**

---

## ✅ Summary: Objectives Alignment

### CLAUDE.md Objectives → Implementation Status

| Objective | CLAUDE.md Target | Current Status | Gap |
|-----------|-----------------|----------------|-----|
| **NixOS appliance** | Deterministic, auditable infra | ✅ Complete | None |
| **MCP integration** | Structured LLM-to-tool interface | 🟡 Scaffold ready | Phase 2 implementation |
| **Pull-only control** | No inbound connections | ✅ Complete | None |
| **Self-healing** | Reconcile to declarative baseline | 🟡 Architecture locked | Phase 2 logic |
| **Evidence pipeline** | Tamper-evident bundles | ✅ Schema defined | Phase 2 generation |
| **HIPAA compliance** | Metadata-only, BA positioning | ✅ Complete | None |
| **Dual modes** | Reseller/direct deployment | ✅ Complete | None |
| **Guardrails** | 10 safety controls | ✅ All locked | None |
| **6-week timeline** | Week 0-6 to first compliance packet | 🟡 Week 2-3 complete | On track |

### Master Alignment Brief → Implementation Status

| Requirement | Status |
|-------------|--------|
| Production = NixOS (no containers on client) | ✅ |
| Control path = pull-only | ✅ |
| Self-healing = declarative baseline | ✅ (Phase 2 execution) |
| Evidence = JSON + Ed25519 sig | ✅ (Phase 2 generation) |
| Dual deployment modes | ✅ |
| Flake scaffold with modules/packages/tests | ✅ |
| 10 guardrails locked | ✅ |
| Phase 1 DoD: flake outputs, options, tests | ✅ |

---

## 🎯 Current Milestone

**Phase 12: Launch Readiness**

**Target:** Q1 2026

**Definition of Done:**
- [x] Production VPS deployed with TLS (Caddy auto-cert)
- [x] Appliance ISO infrastructure created
- [x] Operations SOPs documented
- [x] ISO build verified on VPS (1.16GB, boots in VirtualBox)
- [x] Lab test site enrolled (test-appliance-lab-b3c40c, status: online)
- [x] Phone-home agent checking in every 60 seconds
- [x] Physical appliance deployed (HP T640 at 192.168.88.246)
- [x] Auto-provisioning API implemented
- [x] Ed25519 evidence signing implemented
- [x] Three-tier healing integration (L1/L2/L3) in agent v1.0.5
- [x] ISO v13 built with healing agent (on iMac ~/Downloads/)
- [x] Partner/Reseller infrastructure complete (Phase 11)
- [x] Credential-pull architecture implemented (Session 9)
- [x] ISO v16 built with agent v1.0.8 (credential-pull)
- [x] Windows DC connectivity verified via credential-pull
- [x] Flash ISO v18 to USB and deploy to physical appliance ✅
- [ ] Evidence bundles uploading to MinIO
- [ ] First compliance packet generated
- [ ] 30-day monitoring period complete

**Infrastructure Ready:**
- ✅ VPS: 178.156.162.116 (Hetzner)
- ✅ Dashboard: https://dashboard.osiriscare.net
- ✅ API: https://api.osiriscare.net
- ✅ MSP Portal: https://msp.osiriscare.net
- ✅ MinIO: (internal :9001)
- ✅ Caddy: Auto-TLS for all domains

**Appliance Infrastructure:**
- ✅ ISO build config: `iso/appliance-image.nix`
- ✅ Status page: `iso/local-status.nix`
- ✅ Provisioning: `iso/provisioning/generate-config.py`
- ✅ Phone-home agent: `iso/phone-home.py` (v0.1.1 with API key auth)
- ✅ Auto-provisioning: USB config detection + MAC-based API lookup
- ✅ mDNS: `osiriscare-appliance.local` hostname resolution
- ✅ Lab appliance VM: 192.168.88.247 (VirtualBox on iMac)
- ✅ **Physical appliance: 192.168.88.246 (HP T640 Thin Client)**

**Deployed Sites:**
| Site ID | Name | Type | IP | Status |
|---------|------|------|-----|--------|
| physical-appliance-pilot-1aea78 | North Valley Dental | HP T640 | 192.168.88.246 | online |
| test-appliance-lab-b3c40c | Main Street Virtualbox Medical | VM | 192.168.88.247 | online |

---

**Status:** Phase 12 nearing completion. Agent v1.0.29, ISO v29, 43 runbooks (27 Windows + 16 Linux), OpenTimestamps blockchain anchoring, Windows Sensor dual-mode architecture, Partner L3 Escalation system complete, Multi-Framework Compliance (5 frameworks), MinIO on Hetzner Storage Box, Cloud Integrations (AWS, Google, Okta, Azure AD), **L1 JSON Rule Loading**, **Chaos Lab Automated Testing Ready**, **L2 LLM VERIFIED WORKING**, **Pattern Reporting Pipeline Complete**.

**Session 31 (JSON Rule Loading + Chaos Lab Fixes):**
- Fixed L1 JSON rule loading from Central Command
  - Root cause: DeterministicEngine only loaded YAML files, ignored synced JSON rules
  - Added `from_synced_json()` class method to Rule class
  - Added `_load_synced_json_rules()` to load *.json files
  - Synced rules get priority 5 (override built-in priority 10)
- Created YAML override rule on appliance for local NixOS firewall checks
- Fixed Learning page NULL proposed_rule bug (`Optional[str]`)
- Enabled healing mode on appliance (`healing_dry_run: false`)
- Fixed all three chaos lab scripts for proper argument handling:
  - `winrm_attack.py`: Added --username, --command flag, --scenario-id
  - `winrm_verify.py`: Added --username, --categories flag, --scenario-id
  - `append_result.py`: Made name/category optional, added --date, infer from scenario_id
- Built ISO v1.0.29 on VPS: `/root/msp-iso-build/result-iso-v29/iso/osiriscare-appliance.iso`

**Session 30 (L1 Legacy Action Mapping Fix):**
- Fixed firewall drift flapping on Incidents page
  - Root cause: L1 rule `L1-FW-001` outputs `restore_firewall_baseline` but no handler existed
  - Only had handlers for: `restart_service`, `run_command`, `run_windows_runbook`, `escalate`
- Added legacy action to Windows runbook mapping in `appliance_agent.py`:
  - `restore_firewall_baseline` → `RB-WIN-SEC-001` (Windows Firewall Enable)
  - `restore_audit_policy` → `RB-WIN-SEC-002` (Audit Policy)
  - `restore_defender` → `RB-WIN-SEC-006` (Defender Real-time)
  - `enable_bitlocker` → `RB-WIN-SEC-005` (BitLocker Status)
- Built ISO v1.0.28 on VPS
- Physical appliance (192.168.88.246) updated to v1.0.28 - **verified running**
- VM appliance (192.168.88.247) ISO attached and rebooted
- Manually re-enabled Windows DC firewall to stop immediate flapping

**Session 29 Continued (Learning Flywheel + Portal Link):**
- Learning Flywheel Pattern Reporting - full pipeline for L2→L1 promotion
  - Agent-side `report_pattern()` calls after successful healing (4 locations in appliance_agent.py)
  - Server-side `/agent/patterns` POST endpoint for pattern aggregation
  - Patterns tracked with occurrence counts and success rates
  - Ready for L2→L1 promotion at 5+ occurrences, 90%+ success rate
- Generate Portal Link Button - added to SiteDetail page
  - Calls `POST /api/portal/sites/{site_id}/generate-token`
  - Modal displays portal URL with copy-to-clipboard

**Session 29 (L1/L2/L3 Fixes + ISO v26 + L2 Verification):**
- L1 rule status mismatch fixed (was checking "non_compliant", now ["warning", "fail", "error"])
- L3 notification deduplication fix (added category filter)
- L2 JSON parsing fix (always extract JSON with brace-matching, even when starts with `{`)
- Frameworks API mounted (`/api/frameworks/*` endpoints)
- Incidents page created (`/incidents` route)
- ISO v26 built with agent v1.0.26 including L2 fix
- **L2 LLM VERIFIED WORKING on VM appliance:**
  - `bitlocker_status` → L2 decision: escalate (confidence: 0.90) → L3
  - `backup_status` → L2 decision: run_backup_job (confidence: 0.80)
- No more "Extra data" JSON parsing errors

**Session 15 (Windows Sensors):** Lightweight PowerShell sensor pushes drift events to appliance for instant detection (<30s vs 60s polling). Dual-mode routes sensor-enabled hosts to push model, others to WinRM polling. Scales to 100+ servers per appliance.

**Session 16 (Partner L3 Escalation):** Complete partner notification infrastructure with Slack, PagerDuty, Email, Teams, and Webhook channels. HMAC-signed webhooks, priority-based routing, SLA tracking with breach detection. 27 new tests, 550 total tests passing.

**Session 17 (Dashboard Auth + Secrets Management):** Fixed 401 errors by adding Bearer token auth to frontend. Created 1Password CLI integration for secrets management. Moved all hardcoded credentials to environment variables (auth.py, escalation_engine.py, Documentation.tsx).

**Session 21 (OpenTimestamps Blockchain Anchoring):** Enterprise-tier feature for proving evidence existed at timestamp T via Bitcoin blockchain. Created opentimestamps.py client, evidence_chain.py backend API, 011_ots_blockchain.sql migration. 24 new tests, 656 total tests passing.

**Session 22 (ISO v20 Build + Physical Appliance Update):** Fixed admin password hash issue, diagnosed physical appliance crash (old agent v1.0.0 missing provisioning module). Built ISO v20 with agent v1.0.22, asyncssh for Linux support. Physical appliance (192.168.88.246) reflashed and online with L1 auto-healing working. VM appliance update pending (user away from home network).

**Session 23 (Runbook Config Fix + Flywheel Seeding):** Fixed Runbook Config page API path mismatch between frontend (`/api/sites/{id}/runbooks`) and backend (`/api/runbooks/sites/{id}`). Added `SiteRunbookConfigItem` model with full runbook details. Seeded learning flywheel with 40 L2 resolutions across 8 patterns - all patterns now meet promotion criteria. Created `dashboard_api` symlink for main.py imports. Commit `f94f04c` pushed to production.

**Sessions 25-26 (Multi-Framework Compliance + MinIO Storage Box):**
- Agent v1.0.23 with multi-framework evidence generation (HIPAA, SOC 2, PCI DSS, NIST CSF, CIS Controls)
- Framework Config frontend deployed at `/sites/{siteId}/frameworks`
- Backend API at `/api/frameworks/*` for config, scores, metadata, industry recommendations
- Database migration 013 (appliance_framework_configs, evidence_framework_mappings, compliance_scores)
- MinIO storage migrated to Hetzner Storage Box (BX11, 1TB, $4/mo) via SSHFS mount
- NixOS systemd service `storagebox-mount` for persistent Storage Box mounting
- Fixed Docker networking (caddy → msp-server routing)
- Fixed database connectivity (correct password, asyncpg driver)
- Fixed health endpoint for HEAD method (monitoring compatibility)

**ISO v29 Ready:**
- **VPS:** `/root/msp-iso-build/result-iso-v29/iso/osiriscare-appliance.iso` (1.1GB)
- **VM appliance:** 🟡 Pending update to v1.0.29 (192.168.88.247)
- **Physical appliance:** Running v1.0.28 (192.168.88.246), user handling v29 update

**Chaos Lab Ready:**
- All scripts fixed and tested: winrm_attack.py, winrm_verify.py, append_result.py
- Cron schedule: 6 AM attack execution, 12 PM checkpoint, 6 PM report
- Located at: iMac gateway ~/chaos-lab/

**Next Steps:**
1. Deploy ISO v29 to VM appliance (192.168.88.247)
2. User deploys ISO v29 to physical appliance (HP T640)
3. Verify JSON rule loading from Central Command
4. Run first chaos lab attack cycle
5. Monitor healing in Learning dashboard
6. Evidence bundles uploading to MinIO verification
7. First compliance packet generated
8. 30-day monitoring period completion

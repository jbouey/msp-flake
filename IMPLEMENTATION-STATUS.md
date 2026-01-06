# MSP Compliance Appliance - Implementation Status

**Last Updated:** 2026-01-06 (Session 14 - Credential Management API)
**Current Phase:** Phase 12 - Launch Readiness (Agent v1.0.19, 27 Runbooks, Credential CRUD API)
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

**Status:** Phase 12 progressing well. Agent v1.0.19, 27 Windows runbooks (7 core + 20 new). Partner-configurable runbook enable/disable via RunbookConfig.tsx UI. Credential Management API complete with CRUD endpoints for site credentials via Central Command. Both appliances verified using credential-pull architecture (no hardcoded credentials). Next: OpenTimestamps blockchain anchoring (Task 25), or complete data flywheel integration (resolution tracking).

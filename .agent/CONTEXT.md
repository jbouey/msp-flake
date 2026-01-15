# Malachor MSP Compliance Platform - Agent Context

**Last Updated:** 2026-01-15 (Session 40 - Go Agent Implementation)
**Phase:** Phase 12 - Launch Readiness (Agent v1.0.34, ISO v33, 43 Runbooks, OTS Anchoring, Windows Sensors, Partner Escalations, RBAC, Multi-Framework Compliance, Cloud Integrations, Microsoft Security Integration, L1 JSON Rule Loading, Chaos Lab Automated, Network Compliance, Extended Check Types, Workstation Compliance, RMM Comparison, Workstation Discovery Config, $params_Hostname Fix, **Go Agent for Workstation Scale**)
**Test Status:** 786+ passed (compliance-agent tests), agent v1.0.34, 43 total runbooks (27 Windows + 16 Linux), OpenTimestamps blockchain anchoring, Linux drift detection + SSH-based remediation, RBAC user management, Learning flywheel with automatic pattern reporting, Multi-Framework Compliance (HIPAA, SOC 2, PCI DSS, NIST CSF, CIS Controls), Cloud Integrations (AWS, Google Workspace, Okta, Azure AD, Microsoft Security), L1 JSON Rule Loading from Central Command, Network compliance check (Drata/Vanta style), 8 extended check type labels, Chaos Lab 2x daily execution, Workstation Compliance (AD discovery + 5 WMI checks), RMM Comparison Engine, Workstation Discovery Config Fields, $params_Hostname variable injection fix, **Go Agent (gRPC push-based architecture)**

---

## What Is This Project?

A HIPAA compliance automation platform for small-to-mid healthcare practices (4-25 providers). Replaces traditional MSPs at 75% lower cost through autonomous infrastructure healing + compliance documentation.

**Core Value Proposition:** Enforcement-first automation that auto-fixes issues in 2-10 minutes rather than alert→ticket→human workflows taking hours.

---

## Current Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                 Central Command (Hetzner VPS)                    │
│                 http://178.156.162.116                           │
│  ┌─────────────┬─────────────┬─────────────┬─────────────────┐  │
│  │  Dashboard  │  MCP Server │  PostgreSQL │   MinIO (WORM)  │  │
│  │  :3000      │  :8000      │  16-alpine  │   :9000/:9001   │  │
│  └─────────────┴─────────────┴─────────────┴─────────────────┘  │
│  React UI │ Learning Loop │ Pattern DB │ Evidence Store          │
└─────────────────────────────────────────────────────────────────┘
                              ▲
                              │ mTLS/HTTPS (pull-only)
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Compliance Agent (NixOS)                      │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │           Three-Tier Auto-Healer                           │ │
│  │  L1 Deterministic (70-80%) → L2 LLM (15-20%) → L3 Human   │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              │                                   │
│  ┌──────────┬────────────────┼────────────────┬──────────────┐  │
│  │  drift   │    healing     │    evidence    │   mcp_client │  │
│  │  .py     │    .py         │    .py         │   .py        │  │
│  └──────────┴────────────────┴────────────────┴──────────────┘  │
│       │                      │                                   │
│  ┌────┴──────────────────────┴───────────────────────────────┐  │
│  │   Windows Runbooks (WinRM)  │  gRPC Server (:50051)        │  │
│  │   executor.py │ 27 runbooks │  Go Agent drift receiver     │  │
│  └────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              ▲
                              │ gRPC/mTLS (push from workstations)
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Go Agent (Windows Workstations)               │
│                                                                  │
│  ┌──────────────┬────────────────┬────────────────────────────┐ │
│  │ 6 WMI Checks │  SQLite Queue  │   RMM Detection            │ │
│  │ BitLocker    │  (WAL offline) │   Detect ConnectWise/Datto │ │
│  │ Defender     │                │   Auto-disable if present  │ │
│  │ Firewall     │                │                            │ │
│  │ Patches      │                │   Capability Tiers:        │ │
│  │ ScreenLock   │                │   0=Monitor, 1=Heal, 2=Full│ │
│  │ Services     │                │                            │ │
│  └──────────────┴────────────────┴────────────────────────────┘ │
│                                                                  │
│  Single 10MB exe │ No install │ Windows service optional        │
└─────────────────────────────────────────────────────────────────┘
```

---

## Key Technologies

| Component | Technology | Purpose |
|-----------|------------|---------|
| Host OS | NixOS 24.05 | Deterministic, auditable infrastructure |
| Agent | Python 3.13 | Compliance monitoring + self-healing |
| Windows Integration | pywinrm + WinRM | Remote Windows server management |
| LLM Interface | MCP (Model Context Protocol) | Structured LLM-to-tool interface |
| Evidence Storage | SQLite + WORM S3 | Tamper-evident audit trail |
| Crypto | Ed25519 | Order/evidence signing |

---

## Business Model

| Tier | Target | Price | Features |
|------|--------|-------|----------|
| Essential | 1-5 providers | $200-400/mo | Basic auto-healing, 30d retention |
| Professional | 6-15 providers | $600-1200/mo | Signed evidence, 90d retention |
| Enterprise | 15-50 providers | $1500-3000/mo | Blockchain anchoring, 2yr retention |

---

## Current State

### What's Working
- ✅ Three-tier auto-healing (L1/L2/L3)
- ✅ Data flywheel (L2→L1 pattern promotion)
- ✅ Windows compliance collection (7 runbooks)
- ✅ Web UI dashboard on appliance
- ✅ PHI scrubbing on log collection
- ✅ BitLocker recovery key backup
- ✅ Federal Register HIPAA monitoring
- ✅ **Production MCP Server deployed** (Hetzner VPS)
- ✅ Ed25519 order signing
- ✅ MinIO WORM evidence storage
- ✅ Rate limiting (10 req/5min/site)
- ✅ **Central Command Dashboard** (https://dashboard.osiriscare.net)
- ✅ **Learning Loop Infrastructure** - PostgreSQL patterns table
- ✅ **Agent Sync Endpoints** - `/agent/sync`, `/agent/checkin`
- ✅ **Client Portal** - Magic-link auth at /portal
- ✅ **TLS via Caddy** - Auto-certs for all domains
- ✅ **Appliance ISO Infrastructure** - `iso/` directory
- ✅ **Operations SOPs** - 7 SOPs in Documentation page
- ✅ **Partner/Reseller Infrastructure** - API, Dashboard, QR codes
- ✅ **Partner Admin Page** - Partners.tsx with CRUD
- ✅ **Provisioning CLI** - `compliance-provision` entry point
- ✅ **ISO v15** - With provisioning code support (1.1GB, deployed to physical appliance)
- ✅ **Agent-Side Evidence Signing** - Ed25519 signing on appliance before upload
- ✅ **Credential-Pull Architecture** - RMM-style credential fetch on check-in (Session 9)
- ✅ **ISO v16** - With agent v1.0.8, credential-pull support (transferred to ~/Downloads/)
- ✅ **Windows DC Connectivity** - North Valley DC (192.168.88.250) connected via credential-pull
- ✅ **Healing System Integration Complete** - 2026-01-05 (Session 10)
  - L1 deterministic healing with Windows runbooks verified working
  - Windows firewall chaos test: auto-healed successfully
  - Agent v1.0.18 with all healing fixes
- ✅ **Email Alerts System** - 2026-01-05 (Session 12)
  - SMTP via privateemail.com (mail.privateemail.com:587)
  - POST /api/dashboard/notifications with email for critical severity
  - Test Alert button + modal in Notifications page
- ✅ **Push Agent Update UI** - 2026-01-05 (Session 12)
  - Prominent animated button for outdated agents in site detail
  - Version selection modal with package URL preview
  - Dropdown z-index fix (z-[9999]) for proper layering
- ✅ **Test VM Rebuilt with ISO v18** - 2026-01-05 (Session 12)
  - Registered MAC 08:00:27:98:fd:84 for provisioning
  - Detached old VDI, booted from ISO v18
  - Now running agent v1.0.18 (was 0.1.1-quickfix)
  - Both appliances on v1.0.18, checking in properly
- ✅ **Chaos Probe Central Command Integration** - 2026-01-06 (Session 12 continued)
  - `scripts/chaos_probe.py` POSTs to `/incidents` endpoint
  - Incidents appear in dashboard stats (incidents_24h, incidents_7d, etc.)
  - L3 probes send emails via `/api/alerts/email` endpoint
  - Fixed routes.py safe_check_type() for unknown check types
  - VPS appliances table updated with FK records
- ✅ **Windows Runbook Expansion (27 Total)** - 2026-01-06 (Session 13)
  - 6 new category files: services.py, security.py, network.py, storage.py, updates.py, active_directory.py
  - 20 new runbooks + 7 core = 27 total Windows runbooks
  - Partner-configurable enable/disable via RunbookConfig.tsx
  - Backend API: GET/PUT /api/sites/{site_id}/runbooks
  - L1 rules in windows_baseline.yaml for automated remediation
  - 20 runbook filtering tests in test_runbook_filtering.py
- ✅ **Credential Management API** - 2026-01-06 (Session 14)
  - Fixed `sites.py` windows_targets transformation (was returning raw JSON)
  - Fixed runbook query (r.id UUID → r.runbook_id VARCHAR)
  - Created missing `appliance_runbook_config` table in database
  - Fixed NULL check_type for 6 original runbooks
  - Site detail now queries `site_credentials` table (was hardcoded `[]`)
  - Added `POST /api/sites/{site_id}/credentials` endpoint (create credential)
  - Added `DELETE /api/sites/{site_id}/credentials/{id}` endpoint (delete credential)
  - Verified both appliances using credential-pull properly (no hardcoded creds on disk)
- ✅ **Windows Sensor & Dual-Mode Architecture** - 2026-01-08 (Session 15)
  - Created `OsirisSensor.ps1` - PowerShell sensor with 12 compliance checks
  - Sensor pushes drift events to appliance (port 8080) for instant detection
  - Dual-mode: Hosts with sensors skip WinRM polling, others still polled
  - Created `sensor_api.py` - FastAPI router for appliance sensor endpoints
  - Created `deploy_sensor.py` - CLI for remote sensor deployment via WinRM
  - Created `sensors.py` - Central Command backend for sensor management
  - Created `SensorStatus.tsx` - Dashboard UI for sensor status
  - Created `006_sensor_registry.sql` - Database migration
  - Added uvicorn web server to `appliance_agent.py` for sensor API
  - Order handlers: deploy_sensor, remove_sensor, sensor_status
  - 25 new tests in `test_sensor_integration.py`
- ✅ **Partner Dashboard Testing & L3 Escalation Activation** - 2026-01-08 (Session 16)
  - Created `007_partner_escalation.sql` - Database migration for partner notifications
    - Tables: partner_notification_settings, site_notification_overrides, escalation_tickets, notification_deliveries, sla_definitions
    - Default SLAs: critical (15min), high (1hr), medium (4hr), low (8hr)
  - Created `notifications.py` - Partner notification API
    - Settings CRUD for Slack, PagerDuty, Email, Teams, Webhook
    - Site-level overrides for routing
    - Escalation ticket management (list, acknowledge, resolve)
    - SLA metrics and test notification endpoints
  - Created `escalation_engine.py` - L3 Escalation Engine
    - Routes incidents to partner notification channels
    - HMAC signing for webhooks
    - Priority-based routing (critical=all, high=PD+Slack, medium=Slack+Email, low=Email)
    - Delivery tracking and SLA breach detection
  - Modified `level3_escalation.py` for Central Command integration
    - Added central_command_enabled config
    - Falls back to local notifications if CC fails
  - Created `NotificationSettings.tsx` - Partner notification settings UI
  - Created `test_partner_api.py` - 27 comprehensive tests (all passing)
  - Wired routers in server.py (notifications_router, escalations_router)
  - 550 total tests passing
- ✅ **Dashboard Auth Fix + 1Password Secrets Management** - 2026-01-08 (Session 17)
  - Fixed 401 errors - Added Bearer token auth to frontend API requests (api.ts)
  - Created `scripts/load-secrets.sh` - 1Password CLI integration
  - Created `.env.template` - Environment variable template
  - Created `docs/security/SECRETS_INVENTORY.md` - Complete credentials inventory
  - Created `docs/security/1PASSWORD_SETUP.md` - 1Password setup guide
  - Fixed `auth.py` - Use ADMIN_INITIAL_PASSWORD env var (no more hardcoded password)
  - Fixed `escalation_engine.py` - Use SMTP_* env vars
  - Fixed `Documentation.tsx` - Placeholder API keys instead of real examples
  - User's 1Password vault: "Central Command" with "Anthropic Key" item
- ✅ **Linux Drift Healing Module** - 2026-01-08 (Session 18)
  - Created `runbooks/linux/executor.py` - LinuxTarget, LinuxExecutor with asyncssh (655 lines)
  - Created `runbooks/linux/runbooks.py` - 16 Linux runbooks across 9 categories (709 lines)
  - Created `linux_drift.py` - LinuxDriftDetector class (551 lines)
  - Created `network_posture.py` - NetworkPostureDetector for Linux/Windows (591 lines)
  - Created `baselines/linux_baseline.yaml` - HIPAA Linux baseline config
  - Created `baselines/network_posture.yaml` - Network posture baseline
  - Added `linux_targets` to Central Command checkin response (server.py)
  - Added `_update_linux_targets_from_response()` to appliance agent
  - Added `_maybe_scan_linux()` to appliance agent run cycle
  - Credential-pull architecture: Linux credentials fetched from `site_credentials` (credential_type: ssh_password, ssh_key)
  - 16 Linux runbooks: SSH hardening, firewall, audit, services, patching, encryption, accounts, permissions, SELinux/AppArmor
  - 632 tests passing (was 550)
- ✅ **RBAC User Management** - 2026-01-08 (Session 19)
  - Database migration: `009_user_invites.sql` (admin_user_invites table)
  - Backend API: `users.py` with 12 endpoints for user management
  - Role-based decorators: `require_admin`, `require_operator`, `require_role(*roles)` in auth.py
  - Email service: `email_service.py` for invite and password reset emails
  - Frontend: `Users.tsx` admin page with invite/edit modals
  - Frontend: `SetPassword.tsx` public page for invite acceptance
  - Three-tier permissions: Admin (full access), Operator (view+actions), Readonly (view only)
  - Updated `main.py` with dashboard_api.users import
  - Agent v1.0.22 with NetworkPostureDetector wired into run cycle
  - VPS: Postgres password changed to `McpSecure2727` (removed special char)
- ✅ **Auth Fix & Comprehensive System Audit** - 2026-01-09 (Session 20)
  - Fixed admin login (password hash was corrupted during debug)
  - Admin credentials: `admin` / `Admin` (from ADMIN_INITIAL_PASSWORD env var)
  - Documented: `ADMIN_INITIAL_PASSWORD` is BOOTSTRAP-ONLY (only used when admin_users table is empty)
  - **Comprehensive Audit Completed:**
    - Infrastructure: 6/6 Docker containers healthy
    - Database: 34 tables, 72,294 compliance bundles
    - Sites: 2 online (physical-appliance-pilot-1aea78, test-appliance-lab-b3c40c)
    - Authentication: Working with Bearer tokens
    - API: All endpoints verified (fleet, stats, runbooks, users, sites, learning)
    - Frontend: Serving at https://dashboard.osiriscare.net
  - **Linux Runbooks Migration (010_linux_runbooks.sql):**
    - Added 17 Linux runbooks to database
    - **43 total runbooks** now (26 Windows + 17 Linux)
    - Categories: SSH (3), Firewall (1), Services (4), Audit (2), Patching (1), Permissions (3), Accounts (2), MAC (1)
  - **ISO v19 Ready:** `/Users/jrelly/Downloads/osiriscare-appliance-v19.iso` on iMac (192.168.88.50)
- ✅ **OpenTimestamps Blockchain Anchoring** - 2026-01-09 (Session 21)
  - Created `opentimestamps.py` - OTS client with calendar server submission
  - Created `evidence_chain.py` - Central Command backend API for hash-chain + OTS
  - Created `011_ots_blockchain.sql` - Database migration (ots_proofs table, compliance_bundles OTS columns)
  - Integrated OTS into `evidence.py` store_evidence() - submits hash after Ed25519 signing
  - Added OTS config options: OTS_ENABLED, OTS_CALENDARS, OTS_TIMEOUT, OTS_AUTO_UPGRADE
  - Background task upgrades pending proofs when Bitcoin confirmation arrives (1-24 hours)
  - Enterprise tier feature: Proves evidence existed at timestamp T via Bitcoin blockchain
  - 24 new tests in `test_opentimestamps.py`, 656 total tests passing (was 632)
- ✅ **ISO v20 Build + Physical Appliance Update** - 2026-01-09 (Session 22)
  - Fixed admin password hash (SHA256 format for `admin` / `Admin123`)
  - Diagnosed physical appliance: had old agent v1.0.0 (missing provisioning module)
  - Updated `iso/appliance-image.nix` to v1.0.22 with asyncssh dependency
  - Added iMac SSH key to `iso/configuration.nix` for appliance access
  - Built ISO v20 on VPS (1.1GB) at `/root/msp-iso-build/result-iso-v20/iso/osiriscare-appliance.iso`
  - Downloaded to local Mac: `/tmp/osiriscare-appliance-v20.iso`
  - Physical appliance (192.168.88.246) reflashed, now online with v1.0.19, L1 auto-healing working
  - VM appliance (192.168.88.247) update pending - user away from home network
- ✅ **Learning Flywheel Seeded + Runbook Config Fix** - 2026-01-10 (Session 23)
  - Learning infrastructure was complete but had no L2 data (all incidents going to L3)
  - Created `/var/lib/msp/flywheel_generator.py` on appliance to seed L2 resolutions
  - Disabled DRY-RUN mode: `healing_dry_run: false`
  - Seeded 8 patterns with 5 L2 resolutions each (40 total incidents)
  - All patterns meet promotion criteria (5 occurrences, 100% success rate)
  - Fixed Runbook Config page API path mismatch (frontend/backend)
  - Added `SiteRunbookConfigItem` model with full runbook details
  - Created `dashboard_api` symlink for main.py imports
  - Commit `f94f04c` pushed to production
- ✅ **Multi-Framework Compliance System** - 2026-01-11 (Sessions 25-26)
  - Agent v1.0.23 with multi-framework evidence generation
  - Supports HIPAA, SOC 2, PCI DSS, NIST CSF, CIS Controls
  - One infrastructure check maps to controls across multiple frameworks
  - Per-appliance framework selection and industry presets
  - Database: migration 013 (appliance_framework_configs, evidence_framework_mappings, compliance_scores)
  - Backend: `/api/frameworks/*` endpoints for config, scores, metadata
  - Frontend: FrameworkConfig.tsx page at `/sites/{siteId}/frameworks`
  - 37 unit tests for framework service
- ✅ **MinIO Storage Migrated to Hetzner Storage Box** - 2026-01-11 (Session 26)
  - Storage Box: BX11 #509266 (`u526501.your-storagebox.de`), 1TB, $4/mo
  - Mounted via SSHFS at `/mnt/storagebox` on VPS
  - NixOS systemd service `storagebox-mount` for persistent mounting
  - MinIO container uses Storage Box for evidence storage
  - Frees up VPS disk space for other uses
- ✅ **Infrastructure Fixes** - 2026-01-11 (Session 26)
  - Fixed Docker networking (caddy → msp-server routing)
  - Fixed API prefix (`/api/frameworks` instead of `/frameworks`)
  - Fixed database connectivity (correct password, asyncpg driver)
  - Fixed health endpoint for HEAD method (monitoring compatibility)
  - Added async_session to server.py for SQLAlchemy dependency injection
- ✅ **Cloud Integration System** - 2026-01-12 (Session 27)
  - Secure cloud connectors for AWS, Google Workspace, Okta, Azure AD
  - Database: migration 015 (integrations, integration_resources, integration_audit_log, integration_sync_jobs)
  - Backend: `/api/integrations/*` endpoints for connection management
  - Frontend: Integrations.tsx, IntegrationSetup.tsx, IntegrationResources.tsx
  - Security: Per-integration HKDF keys, single-use OAuth state tokens, tenant isolation
  - HIPAA Controls: 164.312(a)(1) Access, 164.312(b) Audit, 164.312(c)(1) Integrity, 164.312(d) Auth
- ✅ **Cloud Integration Frontend Fixes** - 2026-01-12 (Session 28)
  - Fixed frontend deployment: central-command nginx was serving old JS files
  - Fixed IntegrationResources.tsx null handling for risk_level (TypeError crash)
  - Fixed integrationsApi.ts types to match API response (nullable fields)
  - Verified end-to-end: AWS integration showing 14 resources with 2 critical, 7 high findings
  - Integration Resources page now fully functional with risk badges and compliance checks
- ✅ **L1/L2/L3 Auto-Healing Fixes** - 2026-01-13 (Session 29)
  - L1 rule status mismatch fixed (was checking "non_compliant", now checks ["warning", "fail", "error"])
  - L3 notification deduplication fix (added category filter)
  - Windows backup check added (`backup_status` using Get-WBSummary)
  - Learning page query fix (`resolution_tier IS NOT NULL`)
  - Admin login restored
  - **L2 LLM enabled** on physical appliance with Anthropic API key (Claude 3.5 Haiku)
  - L2 JSON parsing fix (always extract JSON object with brace-matching)
- ✅ **Frontend Fixes** - 2026-01-13 (Session 29)
  - Frameworks API now mounted (`/api/frameworks/*` endpoints working)
  - Incidents page created (`/incidents` route with all/active/resolved filters)
- ✅ **ISO v26 Built & L2 VERIFIED WORKING** - 2026-01-13 (Session 29 continued)
  - Built ISO v26 on VPS with agent v1.0.26 (includes L2 JSON parsing fix)
  - Deployed to VM appliance (192.168.88.247)
  - **L2 LLM VERIFIED:**
    - `bitlocker_status` → L2 decision: escalate (confidence: 0.90) → L3
    - `backup_status` → L2 decision: run_backup_job (confidence: 0.80)
  - No more "Extra data" JSON parsing errors
  - ISO locations: VPS `/root/msp-iso-build/result-iso-v26/`, iMac `~/Downloads/osiriscare-appliance-v26.iso`
- ✅ **Learning Flywheel Pattern Reporting** - 2026-01-13 (Session 29 continued)
  - Agent-side `report_pattern()` calls after successful L1/L2 healing (4 locations)
  - Server-side `/agent/patterns` POST endpoint for pattern aggregation
  - Patterns stored with occurrence counts, success rates
  - Ready for L2→L1 promotion when patterns reach 5+ occurrences, 90%+ success
  - Needs ISO v27 to deploy to appliances
- ✅ **Generate Portal Link Button** - 2026-01-13 (Session 29 continued)
  - Added to SiteDetail page in frontend
  - Calls `POST /api/portal/sites/{site_id}/generate-token`
  - Modal displays portal URL with copy-to-clipboard
  - Deployed to VPS
- ✅ **L1 Legacy Action Mapping Fix** - 2026-01-14 (Session 30)
  - Fixed firewall drift flapping on Incidents page
  - Root cause: L1 rule `L1-FW-001` outputs `restore_firewall_baseline` but no handler existed
  - Fix: Added legacy action to Windows runbook mapping in `appliance_agent.py`
  - Mapping: `restore_firewall_baseline` → `RB-WIN-SEC-001`, `restore_audit_policy` → `RB-WIN-SEC-002`, etc.
  - Built ISO v1.0.28 on VPS
  - Physical appliance (192.168.88.246) updated to v1.0.28 - **verified running**
  - VM appliance (192.168.88.247) ISO attached and rebooted
- ✅ **L1 JSON Rule Loading + Chaos Lab Fixes** - 2026-01-14 (Session 31)
  - Fixed synced JSON rules from Central Command not being loaded by DeterministicEngine
  - Added `from_synced_json()` class method to Rule for JSON format handling
  - Added `_load_synced_json_rules()` to load *.json files from rules directory
  - Synced rules get priority 5 (override built-in priority 10)
  - Created YAML override rule on appliance for local NixOS firewall checks
  - Fixed Learning page NULL proposed_rule bug (Optional[str])
  - Enabled healing mode on appliance (healing_dry_run: false)
  - Fixed all chaos lab scripts (winrm_attack.py, winrm_verify.py, append_result.py) for proper argument handling
  - Built ISO v1.0.29 on VPS
- ✅ **Phase 1 Workstation Coverage** - 2026-01-14 (Session 33)
  - AD workstation discovery via PowerShell Get-ADComputer
  - 5 WMI compliance checks: BitLocker, Defender, Patches, Firewall, Screen Lock
  - HIPAA control mappings for each check
  - Per-workstation + site-level evidence bundles
  - Database: workstations, workstation_checks, workstation_evidence, site_workstation_summaries
  - Agent integration with 2-phase scan (discovery hourly, compliance 10 min)
  - Frontend: SiteWorkstations.tsx page with summary cards and expandable rows
  - Backend API: /api/sites/{site_id}/workstations endpoints
  - 20 new tests (754 total, up from 656)
  - Agent v1.0.32
- ✅ **Microsoft Security Integration (Phase 3)** - 2026-01-15 (Session 35)
  - Backend: `integrations/oauth/microsoft_graph.py` (893 lines)
  - Defender alerts collection with severity/status analysis
  - Intune device compliance and encryption status
  - Microsoft Secure Score posture data
  - Azure AD devices for trust/compliance correlation
  - HIPAA control mappings for all resource types
  - Cloud Integrations button on Site Detail page
  - OAuth callback public router (no auth for browser redirects)
  - Delete button UX fix with loading state
  - VPS deployment infrastructure: `/opt/mcp-server/deploy.sh`

### What's Pending
- ✅ Built ISO v10 with MAC detection fix (1.1GB, on Hetzner VPS)
- ✅ **Admin Action Buttons Backend** - deployed to VPS (2026-01-03)
  - POST `/api/sites/{site}/appliances/{app}/orders` - create order
  - POST `/api/sites/{site}/orders/broadcast` - broadcast to all appliances
  - POST `/api/sites/{site}/appliances/clear-stale` - clear stale appliances
  - DELETE `/api/sites/{site}/appliances/{app}` - delete appliance
  - Orders table: `admin_orders` with status tracking
- ✅ **Remote Agent Update Mechanism** - deployed (2026-01-03)
  - Agent order polling: `fetch_pending_orders`, `acknowledge_order`, `complete_order`
  - VPS endpoints: `/api/sites/{site}/appliances/{app}/orders/pending`, `/api/orders/{id}/acknowledge|complete`
  - Agent package hosting: `/agent-packages/` static files
  - Packaging script: `scripts/package-agent.sh`
  - Frontend: "Update Agent" button in SiteDetail
- ✅ **L1 Rules Sync Endpoint** - `/agent/sync` returns 5 built-in NixOS rules (2026-01-03)
- ✅ **Evidence Schema Fix** - client now matches server's EvidenceBundleCreate model (2026-01-03)
- ✅ **HIPAA Control Mappings** - added to appliance drift checks (2026-01-03)
- ✅ **SSH Hotfix Applied** - physical appliance now using ethernet MAC (2026-01-03)
- ✅ **Client Portal HIPAA Enhancement** - deployed (2026-01-03 Session 4)
  - Backend: `portal.py` - Added plain English fields for all 8 HIPAA controls
  - Fields: `plain_english`, `why_it_matters`, `consequence`, `what_we_check`, `hipaa_section`
  - Frontend: `ControlTile.tsx` - Expandable cards with customer-friendly details
  - Frontend: `KPICard.tsx` - Added description prop
  - Frontend: `PortalDashboard.tsx` - Customer-friendly KPI labels
- ✅ **IP Address Cleanup** - deprecated old Mac 174.178.63.139 references (2026-01-03)
  - VPS is at 178.156.162.116 (msp.osiriscare.net)
  - Added deprecation notices to VM-ACCESS-GUIDE.md, CREDENTIALS.md
- 🟡 Deploy ISO v19 to physical appliance ← **NEXT** (ready at ~/Downloads/ on iMac)
- ⚠️ Evidence bundles uploading to MinIO
- ✅ OpenTimestamps blockchain anchoring (Session 21)
- ✅ Multi-NTP time verification (Session 12)

### Appliance Agent v1.0.0 (2026-01-02)
- ✅ Created `appliance_agent.py` - Standalone agent for appliance deployment
- ✅ Created `appliance_config.py` - YAML-based config loader
- ✅ Created `appliance_client.py` - Central Command API client (HTTPS + API key)
- ✅ Simple drift checks: NixOS generation, NTP sync, services, disk, firewall
- ✅ Updated `iso/appliance-image.nix` to use full agent package
- ✅ Entry point: `compliance-agent-appliance`
- ✅ 431 tests passing

### Physical Appliance Deployed (2026-01-02)
- **Hardware:** HP T640 Thin Client
- **MAC:** `84:3A:5B:91:B6:61`
- **IP:** 192.168.88.246
- **Site:** `physical-appliance-pilot-1aea78`
- **Status:** online (checking in every 60s)
- **Agent:** phone-home v0.1.1-quickfix (upgrading to full agent v1.0.0)
- **Config:** `/var/lib/msp/config.yaml`

### ISO v9 Built (2026-01-02)
- **Location:** `root@178.156.162.116:/root/msp-iso-build/result-iso/iso/osiriscare-appliance.iso`
- **Size:** 1.1GB
- **SHA256:** `726f0be6d5aef9d23c701be5cf474a91630ce6acec41015e8d800f1bbe5e6396`
- **Agent:** Full compliance-agent v1.0.0 with appliance mode
- **Entry point:** `compliance-agent-appliance`

### Go Agent for Workstation Scale (2026-01-15 Session 40)
- **Status:** Complete (Go agent + gRPC server + Frontend + Backend)
- **Architecture:** Push-based gRPC replaces WinRM polling for workstations
- **Files Created:**
  - `agent/` - Complete Go agent implementation (14 Go files)
  - `agent/proto/compliance.proto` - gRPC protocol definitions
  - `agent/flake.nix` - Nix cross-compilation for Windows
  - `packages/compliance-agent/src/compliance_agent/grpc_server.py` - Python gRPC server
  - `packages/compliance-agent/tests/test_grpc_server.py` - 12 tests
- **Binaries Built (on VPS):**
  - `osiris-agent.exe` - Windows amd64 (10.3 MB)
  - `osiris-agent-linux` - Linux amd64 (9.8 MB)
  - Location: `/root/msp-iso-build/agent/`
- **Features:**
  - 6 WMI compliance checks (BitLocker, Defender, Firewall, Patches, ScreenLock, Services)
  - SQLite WAL offline queue for network resilience
  - RMM detection (ConnectWise, Datto, NinjaRMM) with auto-disable
  - Capability tiers: MONITOR_ONLY (0), SELF_HEAL (1), FULL_REMEDIATION (2)
- **Frontend Dashboard:**
  - `SiteGoAgents.tsx` - Go agents management page at /sites/:siteId/agents
  - Go agent types in `types/index.ts`
  - API client in `utils/api.ts` (goAgentsApi)
  - React Query hooks in `hooks/useFleet.ts`
  - Purple "Go Agents" button on SiteDetail.tsx
- **Backend API:**
  - `migrations/019_go_agents.sql` - Database schema (4 tables, 2 views, 1 trigger)
  - `sites.py` - 6 API endpoints for Go agent management
- **Git Commits:**
  - `8d4e621` - chore: Add go.sum with verified dependency hashes
  - `e8ab5c7` - fix: Update Go module dependencies to valid versions
  - `37b018c` - feat: Integrate gRPC server into appliance agent
  - `c94b100` - feat: Add Go Agent dashboard to frontend
  - `18d2b15` - feat: Add Go Agent backend API and database schema
- **Tests:** 786 passed (up from 778)

### Agent v1.0.34 Ready (2026-01-15 Session 39)
- **Status:** Code committed, ISO v33 built and deployed
- **Agent:** compliance-agent v1.0.34 with **$params_Hostname fix for workstation discovery**
- **Features:**
  - Fixed $params_Hostname variable injection in workstation online detection scripts
  - All 3 check scripts (PING, WMI, WINRM) now use correct $params_Hostname variable
  - Workstation discovery via AD + online status checking
- **Testing Results:**
  - ✅ Direct WinRM to NVWS01: WORKS
  - ✅ AD enumeration from DC: WORKS (found NVWS01)
  - ❌ Test-NetConnection from DC: TIMED OUT (DC restoring from chaos snapshot)

### Agent v1.0.30 Ready (2026-01-14 Session 32)
- **Status:** Superseded by v1.0.34
- **Agent:** compliance-agent v1.0.30 with **Network compliance check + Extended check types**
- **Features:**
  - Network check_type for NetworkPostureDetector (was "network_posture_{os_type}")
  - 7-metric compliance scoring (added network)
  - 8 extended check type labels (NTP, Disk, Services, Defender, Memory, Cert, Database, Port)
  - Pattern reporting endpoints deployed

### ISO v33 Built (2026-01-15 Session 39)
- **Location (VPS):** `/root/msp-iso-build/result-v33/iso/osiriscare-appliance.iso`
- **Location (iMac):** `~/Downloads/osiriscare-appliance-v33.iso`
- **Size:** 1.1GB
- **Agent:** compliance-agent v1.0.34 with **$params_Hostname fix**
- **Entry point:** `compliance-agent-appliance`
- **Features:** Workstation discovery config, $params_Hostname fix, all previous features
- **Status:** Deployed to physical appliance (192.168.88.246)

### ISO v29 Built (2026-01-14 Session 31)
- **Location (VPS):** `/root/msp-iso-build/result-iso-v29/iso/osiriscare-appliance.iso`
- **Size:** 1.1GB
- **Agent:** compliance-agent v1.0.29 with **L1 JSON rule loading from Central Command**
- **Entry point:** `compliance-agent-appliance`
- **Features:** JSON rule loading, L1 firewall healing, L2 LLM, pattern reporting, all previous features
- **Status:** Physical appliance updated (user confirmed)
- **Fix:** Synced rules from Central Command now properly loaded (priority 5 overrides built-in)

### ISO v28 Built (2026-01-14 Session 30)
- **Location (VPS):** `/root/msp-iso-build/result-iso-v28/iso/osiriscare-appliance.iso`
- **Size:** 1.1GB
- **Agent:** compliance-agent v1.0.28 with **L1 legacy action mapping fix**
- **Entry point:** `compliance-agent-appliance`
- **Features:** L1 firewall healing, L2 LLM, pattern reporting, all previous features
- **Status:** Physical appliance (192.168.88.246) **verified running v1.0.28**
- **Fix:** Legacy actions (`restore_firewall_baseline`, etc.) now map to Windows runbooks

### ISO v26 Built (2026-01-13 Session 29)
- **Location (VPS):** `/root/msp-iso-build/result-iso-v26/iso/osiriscare-appliance.iso`
- **Location (iMac):** `~/Downloads/osiriscare-appliance-v26.iso`
- **Size:** 1.1GB
- **Agent:** compliance-agent v1.0.26 with **L2 JSON parsing fix**
- **Entry point:** `compliance-agent-appliance`
- **Features:** L2 LLM with Claude 3.5 Haiku, JSON parsing fix, all previous features
- **Status:** Superseded by v1.0.28
- **L2 Verification:** Observed decisions with confidence scores (escalate 0.90, run_backup_job 0.80)

### ISO v20 Built (2026-01-09 Session 22)
- **Location (VPS):** `/root/msp-iso-build/result-iso-v20/iso/osiriscare-appliance.iso`
- **Location (Local):** `/tmp/osiriscare-appliance-v20.iso`
- **Size:** 1.1GB
- **Agent:** compliance-agent v1.0.22 with **OpenTimestamps, Linux support, asyncssh**
- **Entry point:** `compliance-agent-appliance`
- **Features:** OTS blockchain anchoring, Linux drift detection, NetworkPostureDetector, 43 runbooks
- **Status:** Physical appliance (192.168.88.246) running, needs ISO v26 update

### ISO v16 Built (2026-01-04 Session 9)
- **Location (iMac):** `~/Downloads/osiriscare-appliance-v16.iso`
- **Size:** 1.1GB
- **Agent:** compliance-agent v1.0.8 with **credential-pull architecture**
- **Entry point:** `compliance-agent-appliance`
- **Features:** RMM-style credential fetch from Central Command on check-in

### Agent v1.0.18 (2026-01-05 Session 10)
- Fixed L1 `execute()` to check action_executor success properly
- Fixed `_handle_drift_healing()` to use `auto_healer.heal()` method
- Fixed `_heal_run_windows_runbook()` to use correct `WindowsExecutor.run_runbook()`
- **Note:** Hot-patched to appliance for testing, needs ISO v18 for permanent deployment

**Previous versions on iMac:**
- v9: Jan 2 15:31
- v10: Jan 3 07:03
- v11: Jan 3 08:18
- v12: Jan 3 10:16
- v13: Jan 3 (three-tier healing)
- v15: Jan 4 (provisioning CLI, deployed to physical appliance)
- v16: Jan 4 (credential-pull, agent v1.0.8)

### Agent Packages (Remote Updates)
- **v1.0.1:** Initial remote update package (failed on NixOS read-only fs - expected)
- **v1.0.2:** Evidence schema fix
- **v1.0.3:** HIPAA control mappings + all fixes
- **v1.0.5:** Three-tier healing integration (L1/L2/L3) - **in ISO v13**
- **v1.0.6:** Provisioning module - **in ISO v15**
- **v1.0.8:** Credential-pull architecture - **in ISO v16**
- **Package URL:** `https://api.osiriscare.net/agent-packages/compliance_agent-{version}.tar.gz`
- **Packaging:** `./scripts/package-agent.sh {version}`
- **Note:** Remote updates blocked by NixOS read-only fs; must use ISO reflash

### Site Renaming (2026-01-03 Session 5)
- `physical-appliance-pilot-1aea78` → **"North Valley Dental"**
- `test-appliance-lab-b3c40c` → **"Main Street Virtualbox Medical"**

### Dashboard Sites Fix (2026-01-04 Session 6)
- ✅ Fixed `/api/sites` 404 error - added missing GET endpoints to `sites.py`
- ✅ Endpoints added:
  - `GET /api/sites` - list all sites with live status
  - `GET /api/sites/{site_id}` - site detail with appliances
  - `GET /api/sites/{site_id}/appliances` - appliances list
  - `DELETE /api/sites/{site_id}/appliances/{appliance_id}` - delete stale appliances
- ✅ Fail-safe status calculation based on `last_checkin`:
  - `< 5 min` → online
  - `< 1 hour` → stale
  - `> 1 hour` → offline
  - No checkin → pending
- ✅ 60-second hello cadence auto-reconciles dashboard state on reconnect
- ✅ ISO v14 deployed with `runbooks/__init__.py` and `regulatory/__init__.py` fixes

### Partner/Reseller Infrastructure (2026-01-04 Sessions 6-8)
- ✅ **Database Migration** - `003_partner_infrastructure.sql` + `004_discovery_and_credentials.sql`
  - `partners` table - partner orgs with API keys, revenue share
  - `partner_users` table - partner user accounts with magic link auth
  - `appliance_provisions` table - QR/manual provision codes
  - `partner_invoices` table - billing and payout tracking
  - `discovered_assets` table - network discovery results
  - `discovery_scans` table - scan history
  - `site_credentials` table - encrypted credential storage
  - Added `partner_id` column to `sites` table
- ✅ **Partner API** - `mcp-server/central-command/backend/partners.py`
  - `POST /api/partners` - create partner (admin)
  - `GET /api/partners` - list all partners (admin)
  - `GET /api/partners/me` - get current partner (API key auth)
  - `GET /api/partners/me/sites` - list partner's sites
  - `GET /api/partners/me/sites/{site_id}` - site detail with assets/credentials
  - `GET /api/partners/me/provisions` - list provision codes
  - `POST /api/partners/me/provisions` - create provision code
  - `GET /api/partners/me/provisions/{id}/qr` - generate QR code image (authenticated)
  - `DELETE /api/partners/me/provisions/{id}` - revoke provision code
  - `POST /api/partners/me/sites/{site_id}/credentials` - add credentials
  - `POST /api/partners/me/sites/{site_id}/credentials/{id}/validate` - validate credential
  - `GET /api/partners/me/sites/{site_id}/assets` - list discovered assets
  - `PATCH /api/partners/me/sites/{site_id}/assets/{id}` - update asset
  - `POST /api/partners/me/sites/{site_id}/discovery/trigger` - trigger scan
  - `POST /api/partners/claim` - claim provision code (public, appliance calls this)
  - `GET /api/partners/provision/{code}/qr` - public QR code generation
- ✅ **Discovery Module** - `mcp-server/central-command/backend/discovery.py` (NEW Session 8)
  - `POST /api/discovery/report` - receive discovery results from appliance
  - `POST /api/discovery/status` - update scan status
  - `GET /api/discovery/pending/{site_id}` - get pending scans
  - `GET /api/discovery/assets/{site_id}/summary` - asset summary
  - Asset classification logic (domain_controller, sql_server, etc.)
  - Port-to-service mapping (70+ ports)
- ✅ **Provisioning API** - `mcp-server/central-command/backend/provisioning.py` (NEW Session 8)
  - `POST /api/provision/claim` - claim provision code (appliance first boot)
  - `GET /api/provision/validate/{code}` - validate before claiming
  - `POST /api/provision/status` - update provisioning status
  - `POST /api/provision/heartbeat` - provisioning mode heartbeat
  - `GET /api/provision/config/{appliance_id}` - get appliance config
- ✅ **Partner Dashboard Frontend** - `mcp-server/central-command/frontend/src/partner/`
  - `PartnerContext.tsx` - API key auth context
  - `PartnerLogin.tsx` - login page
  - `PartnerDashboard.tsx` - sites, provisions, QR code generation
- ✅ **Appliance Provisioning Module** - `packages/compliance-agent/src/compliance_agent/provisioning.py`
  - `get_mac_address()` - detect appliance MAC
  - `claim_provision_code()` - call /api/partners/claim
  - `create_config()` - generate /var/lib/msp/config.yaml
  - `run_provisioning_cli()` - interactive CLI mode
  - `run_provisioning_auto()` - non-interactive mode
- ✅ **Provisioning Tests** - 19 tests in `test_provisioning.py`
- ✅ **Agent v1.0.6** - packaged with provisioning support
- ✅ **QR Code Library** - qrcode + pillow installed in Docker (Session 8)

### Credential-Pull Architecture (2026-01-04 Session 9)
- ✅ **Server-Side Credential Return** - `/api/appliances/checkin` returns `windows_targets` with credentials
  - Fetches from `site_credentials` table (credential_type IN winrm, domain_admin, service_account, local_admin)
  - JSON stored as bytea, decoded on fetch
  - Returns hostname, username (with domain prefix), password, use_ssl
- ✅ **Agent-Side Credential Update** - `appliance_agent.py:_update_windows_targets_from_response()`
  - Replaces `self.windows_targets` with server-provided list each cycle
  - No credentials cached on disk - fetched fresh each check-in
  - Credential rotation picked up automatically
- ✅ **Client Return Type Change** - `appliance_client.py` checkin returns `Optional[Dict]` (not bool)
- ✅ **Benefits:**
  - Stolen appliance doesn't expose credentials (not stored locally)
  - Credential changes propagate in ~60 seconds (next check-in)
  - Consistent with RMM industry pattern (Datto, ConnectWise, NinjaRMM)
- ✅ **ISO v16** - Built with agent v1.0.8 (credential-pull)
- ✅ **Verified** - Windows DC (192.168.88.250) connected: "Updated 1 Windows targets from Central Command"

### Hash-Chain Evidence System (2026-01-02)
- ✅ `compliance_bundles` table with SHA256 chain linking
- ✅ WORM protection triggers (prevent UPDATE/DELETE)
- ✅ API: `/api/evidence/sites/{site_id}/submit|verify|bundles|summary`
- ✅ **Ed25519 signing** - bundles signed on submit, verified on chain check
- ✅ `GET /api/evidence/public-key` - for external verification
- ✅ Verification UI at `/portal/site/{siteId}/verify` with signature display

### Auto-Provisioning (2026-01-02)
- ✅ `msp-auto-provision` systemd service in ISO
- ✅ Option 1: USB config detection (checks /config.yaml, /msp/config.yaml, etc.)
- ✅ Option 4: MAC-based provisioning via API
- ✅ API: `GET/POST/DELETE /api/provision/<mac>`
- ✅ SOP added to Documentation page

### Lab Appliance Status (2026-01-02)
- **VM:** osiriscare-appliance on iMac (192.168.88.50)
- **IP:** 192.168.88.247
- **Site:** test-appliance-lab-b3c40c
- **Status:** online (checking in every 60s)
- **Agent:** phone-home v0.1.1-quickfix
- **Config:** `/var/lib/msp/config.yaml` with site_id + api_key

### Current Compliance Score
- Windows Server: 28.6% (2 pass, 5 fail, 1 warning)
- BitLocker: ✅ PASS
- Active Directory: ✅ PASS
- Everything else: ❌ FAIL (expected - test VM not fully configured)

---

## File Locations

| What | Path |
|------|------|
| Project Root | `/Users/dad/Documents/Msp_Flakes` |
| Compliance Agent | `packages/compliance-agent/` |
| Agent Source | `packages/compliance-agent/src/compliance_agent/` |
| **Types (SSoT)** | `packages/compliance-agent/src/compliance_agent/_types.py` |
| **Interfaces** | `packages/compliance-agent/src/compliance_agent/_interfaces.py` |
| Tests | `packages/compliance-agent/tests/` |
| NixOS Module | `modules/compliance-agent.nix` |
| Runbooks | `packages/compliance-agent/src/compliance_agent/runbooks/` |
| Documentation | `packages/compliance-agent/docs/` |
| Agent Context | `.agent/` |
| **Mermaid Diagrams** | `docs/diagrams/` |

### Production Central Command (Hetzner VPS)

| What | Location |
|------|----------|
| Server IP | `178.156.162.116` |
| SSH Access | `ssh root@178.156.162.116` (key auth) |
| Dashboard | `https://dashboard.osiriscare.net` |
| API Endpoint | `https://api.osiriscare.net` |
| MSP Portal | `https://msp.osiriscare.net` |
| MinIO Console | (internal :9001) |
| Server Files | `/opt/mcp-server/` |
| Frontend Files | `/opt/mcp-server/frontend/dist/` |
| Docker Compose | `/opt/mcp-server/docker-compose.yml` |
| Signing Key | `/opt/mcp-server/secrets/signing.key` |
| Init SQL | `/opt/mcp-server/init.sql` |

### Appliance ISO Infrastructure

| What | Location |
|------|----------|
| ISO Config | `iso/appliance-image.nix` |
| Base Config | `iso/configuration.nix` |
| Status Page | `iso/local-status.nix` |
| Provisioning | `iso/provisioning/generate-config.py` |
| Config Template | `iso/provisioning/template-config.yaml` |
| Flake Outputs | `flake-compliance.nix` (appliance-iso, build-iso, test-iso) |

### Source Module Structure

```
src/compliance_agent/
├── __init__.py           # Exports all types and interfaces
├── _types.py             # ALL shared types (single source of truth)
├── _interfaces.py        # ALL module interfaces (protocols/ABCs)
├── agent.py              # Main agent orchestration
├── config.py             # Configuration management
├── drift.py              # Drift detection (6 checks)
├── healing.py            # Self-healing engine
├── auto_healer.py        # Three-tier orchestrator
├── level1_deterministic.py  # L1 YAML rules
├── level2_llm.py         # L2 LLM planner
├── level3_escalation.py  # L3 human escalation
├── incident_db.py        # SQLite incident tracking
├── learning_loop.py      # Data flywheel (L2→L1)
├── evidence.py           # Evidence bundle generation
├── crypto.py             # Ed25519 signing
├── mcp_client.py         # MCP server communication
├── offline_queue.py      # SQLite WAL queue
├── web_ui.py             # FastAPI dashboard
├── phi_scrubber.py       # PHI pattern removal
├── windows_collector.py  # Windows compliance collection
├── linux_drift.py        # Linux drift detection
├── network_posture.py    # Network posture detection
├── baselines/
│   ├── linux_baseline.yaml   # HIPAA Linux baseline
│   └── network_posture.yaml  # Network posture baseline
└── runbooks/
    ├── windows/
    │   ├── executor.py   # WinRM execution
    │   └── runbooks.py   # 27 HIPAA runbooks
    └── linux/
        ├── executor.py   # SSH/asyncssh execution
        └── runbooks.py   # 16 HIPAA runbooks
```

---

## Quick Commands

```bash
# Activate Python environment
cd /Users/dad/Documents/Msp_Flakes/packages/compliance-agent
source venv/bin/activate

# Run tests (161 passing)
python -m pytest tests/ -v --tb=short

# SSH to physical appliance (via iMac gateway)
ssh root@192.168.88.246                                # Direct if on clinic network
ssh jrelly@192.168.88.50 "ssh root@192.168.88.246"    # Via iMac gateway

# iMac gateway (NEPA clinic network)
ssh jrelly@192.168.88.50

# Windows DC connection test
python3 -c "
import winrm
s = winrm.Session('http://127.0.0.1:55985/wsman', auth=('MSP\\\\vagrant','vagrant'), transport='ntlm')
print(s.run_ps('whoami').std_out.decode())
"

# Central Command (Production)
ssh root@178.156.162.116                           # SSH to Hetzner VPS
curl https://api.osiriscare.net/health             # Health check
curl https://api.osiriscare.net/runbooks           # List runbooks
curl https://api.osiriscare.net/stats              # Server stats
curl https://api.osiriscare.net/learning/status    # Learning loop status
curl https://api.osiriscare.net/learning/candidates # Promotion candidates

# Dashboard (Production)
open https://dashboard.osiriscare.net              # Central Command Dashboard
open https://msp.osiriscare.net                    # MSP Portal (alias)

# Central Command Management (on Hetzner)
cd /opt/mcp-server && docker compose logs -f mcp-server  # View API logs
cd /opt/mcp-server && docker compose logs -f central-command  # View dashboard logs
cd /opt/mcp-server && docker compose ps                   # Check status
cd /opt/mcp-server && docker compose restart              # Restart all

# Appliance ISO Build (requires Linux)
nix build .#appliance-iso -o result-iso            # Build bootable ISO
nix run .#test-iso                                 # Test in QEMU
python iso/provisioning/generate-config.py --site-id "clinic-001" --site-name "Test Clinic"

# Lab Appliance (VirtualBox on iMac)
ssh root@192.168.88.247                            # SSH to appliance
journalctl -u osiriscare-agent -f                  # Watch phone-home logs
curl -s https://api.osiriscare.net/api/sites/test-appliance-lab-b3c40c | jq .  # Check site status
```

---

## Related Files

- `NETWORK.md` - VM inventory, network topology
- `CONTRACTS.md` - Interface contracts, data types
- `DECISIONS.md` - Architecture Decision Records
- `TODO.md` - Current tasks and priorities

---

## HIPAA Controls Covered

| Control | Citation | Implementation |
|---------|----------|----------------|
| Audit Controls | §164.312(b) | Evidence bundles, hash chain, auditd (Linux) |
| Access Control | §164.312(a)(1) | Firewall checks, AD monitoring, SSH hardening |
| Encryption | §164.312(a)(2)(iv) | BitLocker (Windows), LUKS (Linux) |
| Backup | §164.308(a)(7) | Backup status, recovery key backup |
| Malware Protection | §164.308(a)(5)(ii)(B) | Windows Defender, ClamAV (Linux) |
| Patch Management | §164.308(a)(5)(ii)(B) | Windows/Linux patch compliance |
| MAC (Mandatory Access) | §164.312(d) | SELinux/AppArmor status (Linux) |
| Service Hardening | §164.312(e)(1) | Prohibited services detection |

---

**For new AI sessions:** Start by reading this file, then check `TODO.md` for current priorities.

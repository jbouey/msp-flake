# Current Tasks & Priorities

**Last Updated:** 2026-01-02
**Sprint:** Phase 10 - Production Deployment + First Pilot Client

---

## 🔴 Critical (This Week)

### 1. Evidence Bundle Signing (Ed25519)
**Status:** ✅ COMPLETE (2025-12-03)
**Why Critical:** HIPAA §164.312(b) requires tamper-evident audit controls
**Files:** `evidence.py`, `crypto.py`, `agent.py`
**Acceptance:**
- [x] Ed25519 key pair generation on first run (`ensure_signing_key()`)
- [x] Sign bundles immediately after creation (in `store_evidence()`)
- [x] Signature stored in bundle + separate .sig file
- [x] Verification function for audit (`verify_evidence()`)

### 2. Auto-Remediation Approval Policy
**Status:** ✅ COMPLETE (2025-12-03)
**Why Critical:** Disruptive actions (patching, BitLocker) need governance
**Files:** `approval.py`, `healing.py`, `web_ui.py`
**Acceptance:**
- [x] Document which actions need approval (`ACTION_POLICIES` in approval.py)
- [x] Add approval queue to web UI (`/approvals`, `/api/approvals/*`)
- [x] Block disruptive actions until approved (integrated in healing.py)
- [x] Audit trail of approvals (SQLite with `approval_audit_log` table)

### 3. Fix datetime.utcnow() Deprecation
**Status:** ✅ COMPLETE (2025-12-03)
**Why Critical:** Python 3.12+ deprecation, causes log noise
**Files:** Fixed in `drift.py`, `src/agent.py`
**Acceptance:**
- [x] Replace all `datetime.utcnow()` with `datetime.now(timezone.utc)`
- [x] Zero deprecation warnings in test run
- [x] All 169 tests passing

---

## 🟡 High Priority (Next 2 Weeks)

### 4. Windows VM Setup & WinRM Configuration
**Status:** ✅ COMPLETE (2025-12-04)
**Why:** Windows VM needed for integration testing
**Files:** `~/win-test-vm/Vagrantfile` (on 2014 iMac)
**Acceptance:**
- [x] Recreated Windows VM with proper WinRM port forwarding (port 55987)
- [x] WinRM connectivity verified via SSH tunnel
- [x] Windows integration tests passing (3/3)
- [x] Auto healer integration tests passing with USE_REAL_VMS=1

### 5. Web UI Federal Register Integration Fix
**Status:** ✅ COMPLETE (2025-12-03)
**Why:** Regulatory monitoring not showing in dashboard
**Files:** `web_ui.py`
**Acceptance:**
- [x] Fix indentation/syntax error (integration was missing, now added)
- [x] `/api/regulatory` returns HIPAA updates
- [x] Dashboard shows regulatory alerts (via `/api/regulatory/updates`, `/api/regulatory/comments`)

### 6. Test BitLocker Runbook
**Status:** ✅ COMPLETE (2025-12-04)
**Files:** `runbooks/windows/runbooks.py` (RB-WIN-ENCRYPTION-001)
**Acceptance:**
- [x] Detection phase tested - AllEncrypted=True, Drifted=False
- [x] Verified via WinRM SSH tunnel (127.0.0.1:55985)
- [x] Windows integration tests passing (3/3)

### 7. Test PHI Scrubbing with Windows Logs
**Status:** ✅ COMPLETE (2025-12-04)
**Files:** `phi_scrubber.py`, `tests/test_phi_windows.py` (17 tests)
**Acceptance:**
- [x] Fetched real Windows Security Event logs via WinRM
- [x] Verified all PHI patterns redacted (SSN, MRN, email, IP, phone, CC, DOB, address, Medicare)
- [x] Created comprehensive test suite for Windows log formats
- [x] All 17 Windows PHI tests passing

---

## 🟢 Medium Priority (This Month)

### 8. Implement Action Parameters Extraction
**Status:** ✅ COMPLETE (2025-12-03)
**Files:** `learning_loop.py:194-297`, `tests/test_learning_loop.py`
**Why:** Data flywheel can't promote L2 patterns without params
**Acceptance:**
- [x] Extract parameters from successful L2 resolutions (already implemented with action-specific keys, majority voting, list handling)
- [x] Store in incident_db for pattern matching (integrated with PromotionCandidate)
- [x] Unit tests for extraction (33 tests added covering all methods)

### 9. Implement Rollback Tracking
**Status:** ✅ COMPLETE (2025-12-03)
**Files:** `learning_loop.py:534-739`, `web_ui.py:526-543, 1330-1457`
**Why:** Can't measure remediation stability without rollback data
**Acceptance:**
- [x] Track if remediation was rolled back (`monitor_promoted_rules()`, `_rollback_rule()`, `get_rollback_history()`)
- [x] Factor into pattern promotion decisions (`rollback_on_failure_rate` config, auto-rollback when >20% failure)
- [x] Dashboard shows rollback rate (Web UI: `/api/rollback/stats`, `/api/rollback/history`, `/api/rollback/monitoring`)
- [x] Fixed `outcome` column bug in post-promotion stats query
- [x] Added 7 rollback tests to test_learning_loop.py, 7 tests to test_web_ui.py

### 10. Web UI Evidence Listing Performance
**Status:** ✅ COMPLETE (2025-12-03)
**Files:** `web_ui.py:807-914`
**Why:** Recursive glob on every request
**Acceptance:**
- [x] Cache evidence file list (`_get_evidence_cache()` with 60-second TTL)
- [x] Invalidate on new bundle (`invalidate_evidence_cache()` method)
- [x] Pagination for large lists (already existed, now uses cached data)
- [x] Fixed ZeroDivisionError on invalid per_page parameter
- [x] Added 5 cache tests to test_web_ui.py

### 11. Fix incident_type vs check_type Column
**Status:** ✅ COMPLETE (2025-12-03)
**Files:** `web_ui.py:875`
**Why:** Causes SQL errors on incident queries
**Acceptance:**
- [x] Change query to use `check_type`
- [x] Verify incidents display in web UI (query fixed)

---

## 🔵 Low Priority (Backlog)

### 12. L2 LLM Guardrails Enhancement
**Status:** ✅ COMPLETE (2025-12-04)
**Files:** `level2_llm.py`, `tests/test_level2_guardrails.py` (42 tests)
**Acceptance:**
- [x] Full blocklist implemented (70+ dangerous patterns)
- [x] Regex patterns for complex commands (rm variants, wget|bash, etc.)
- [x] Action parameter validation (recursive checking)
- [x] All 42 guardrail tests passing
- [x] Note: Crypto mining patterns removed due to AV false positives (strings trigger AV even in blocklist)

### 13. Unskip Test Cases
**Status:** ✅ MOSTLY COMPLETE (2025-12-04)
**Files:** `test_drift.py`, `test_auto_healer_integration.py`
**Why:** 7 tests were skipped due to Windows VM dependency
**Acceptance:**
- [x] Windows VM connectivity restored (port 55987)
- [x] 6 of 7 skipped tests now passing with USE_REAL_VMS=1
- [x] Only 1 test still skipped: NixOS VM connectivity (no NixOS VM configured)
- [x] Test count: 429 passed, 1 skipped (was 423 passed, 7 skipped)

### 14. Async Pattern Improvements
**Status:** ✅ COMPLETE (2025-12-04)
- [x] Drift checks use `asyncio.gather()` for parallel execution (drift.py:92-99)
- [x] Evidence upload batch processing (`store_evidence_batch()`, `sync_to_worm_parallel()`)
- [x] Semaphore-based concurrency control with progress callbacks
- [x] 8 new batch processing tests in test_evidence.py

### 15. Backup Restore Testing Runbook
**Status:** ✅ COMPLETE (2025-12-04)
**Files:** `backup_restore_test.py`, `tests/test_backup_restore.py` (27 tests)
**HIPAA:** §164.308(a)(7)(ii)(A)
**Acceptance:**
- [x] Weekly automated restore test (`BackupRestoreTester.run_restore_test()`)
- [x] Verify checksums (`_verify_restored_files()` with SHA256)
- [x] Evidence of successful restore (`RestoreTestResult` with action trail)
- [x] Support for restic and borg backup types
- [x] Status tracking with history (`backup-status.json`)
- [x] Integration with healing engine (`run_restore_test` action)

---

## ✅ Recently Completed

- [x] Three-tier auto-healing (L1/L2/L3)
- [x] Data flywheel (L2→L1 promotion)
- [x] PHI scrubber module
- [x] BitLocker recovery key backup enhancement
- [x] Federal Register HIPAA monitoring
- [x] Windows compliance collection (7 runbooks)
- [x] Web UI dashboard
- [x] Evidence bundle signing (Ed25519)
- [x] Auto-remediation approval policy
- [x] Federal Register regulatory integration
- [x] L2 LLM Guardrails (70+ patterns, 42 tests) - 2025-12-04
- [x] BitLocker runbook tested on Windows VM - 2025-12-04
- [x] PHI scrubbing with Windows logs (17 tests) - 2025-12-04
- [x] 396 passing tests, 4 skipped (was 300)
- [x] Backup Restore Testing Runbook (27 tests) - 2025-12-04
- [x] Fix Starlette TemplateResponse deprecation - 2025-12-04
- [x] Windows VM recreated with WinRM port 55987 - 2025-12-04
- [x] 6 of 7 skipped tests now passing - 2025-12-04
- [x] 429 passed, 1 skipped (with USE_REAL_VMS=1)
- [x] Evidence batch processing (parallel uploads) - 2025-12-04
- [x] Async Pattern Improvements complete - 2025-12-04
- [x] NixOS module: Added local MCP server + Redis - 2025-12-08
- [x] **Client Portal Complete** - 2026-01-01
  - Magic link authentication with SendGrid email
  - httpOnly cookie sessions (30-day expiry)
  - PDF report generation with WeasyPrint
  - HIPAA control mapping in reports
  - Mobile-responsive dashboard
- [x] **MinIO WORM Storage** - 2026-01-01
  - evidence-worm bucket with Object Lock
  - GOVERNANCE mode, 7-year retention
  - Versioning enabled for audit trail
- [x] NixOS module: Updated firewall for local loopback + WinRM - 2025-12-08
- [x] NixOS module: Default mcpUrl now http://127.0.0.1:8000 - 2025-12-08
- [x] **Production MCP Server deployed to Hetzner VPS** - 2025-12-28
  - FastAPI + PostgreSQL + Redis + MinIO (WORM)
  - Ed25519 signed orders with 15-min TTL
  - 6 default runbooks loaded from DB
  - Rate limiting: 10 req/5min/site_id
  - URL: http://178.156.162.116:8000
- [x] **Architecture diagrams created** - 2025-12-28
  - docs/diagrams/system-architecture.mermaid
  - docs/diagrams/data-flow.mermaid
  - docs/diagrams/deployment-topology.mermaid
  - docs/diagrams/README.md
- [x] **North Valley Clinic Lab Setup** - 2026-01-01
  - Windows Server 2019 AD DC on iMac VirtualBox
  - Domain: northvalley.local, Host: NVDC01
  - IP: 192.168.88.250, WinRM: port 5985
  - Updated .agent/NETWORK.md and TECH_STACK.md
- [x] **North Valley Lab Environment Build** - 2026-01-01
  - 9 phases executed and verified
  - File Server: 5 SMB shares (PatientFiles, ClinicDocs, Backups$, Scans, Templates)
  - AD Structure: 6 OUs, 7 security groups, 8 users
  - Security: Audit logging, password policy, Defender, Firewall
  - Verification: 8/8 checks passed
- [x] **North Valley Workstation (NVWS01)** - 2026-01-01
  - Windows 10 Pro domain-joined to northvalley.local
  - IP: 192.168.88.251, WinRM enabled
  - IT Admin remote management verified
- [x] **Appliance ISO Boot Verified** - 2026-01-02
  - Built on VPS: 1.16GB with phone-home service
  - SHA256: e05bd758afc6584bdd47a0de62726a0db19a209f7974e9d5f5776b89cc755ed2
  - Boots in VirtualBox (12GB RAM, 4 CPU)
  - SSH access working at 192.168.88.247
- [x] **Lab Appliance Test Enrollment** - 2026-01-02
  - Site: test-appliance-lab-b3c40c
  - Phone-home v0.1.1-quickfix with API key auth
  - Checking in every 60 seconds
  - Status: online in Central Command
- [x] **Hash-Chain Evidence System** - 2026-01-02
  - `compliance_bundles` table with SHA256 chain linking
  - WORM protection triggers (prevent UPDATE/DELETE)
  - API endpoints: submit, verify, bundles, summary
  - Verification UI at `/portal/site/{siteId}/verify`
- [x] **ISO v7 Built** - 2026-01-02
  - Built on Hetzner VPS with fixed mkForce conflicts
  - Available at `iso/osiriscare-appliance-v7.iso` (1.1GB)
- [x] **Physical Appliance Deployed** - 2026-01-02
  - HP T640 Thin Client flashed with ISO
  - Site: physical-appliance-pilot-1aea78
  - MAC: 84:3A:5B:91:B6:61, IP: 192.168.88.246
  - Phone-home checking in every 60s
- [x] **Auto-Provisioning System** - 2026-01-02
  - API: GET/POST/DELETE /api/provision/<mac>
  - msp-auto-provision systemd service in ISO
  - USB config detection + MAC-based lookup
  - SOP added to Documentation page
- [x] **Ed25519 Evidence Signing (Central Command)** - 2026-01-02
  - evidence_chain.py signs bundles on submit
  - Signature verification in /verify endpoint
  - GET /api/evidence/public-key for external verification
  - PortalVerify.tsx shows signature status

---

## 🔴 Phase 10: Production Deployment + First Pilot Client (Current)

### 16. Create Appliance ISO Infrastructure
**Status:** ✅ COMPLETE (2025-12-31)
**Why:** Need bootable USB for HP T640 thin clients
**Files:** `iso/`, `flake-compliance.nix`
**Acceptance:**
- [x] Created `iso/appliance-image.nix` - Main ISO config
- [x] Created `iso/configuration.nix` - Base system config
- [x] Created `iso/local-status.nix` - Nginx status page with Python API
- [x] Created `iso/provisioning/generate-config.py` - Site provisioning
- [x] Updated `flake-compliance.nix` with ISO outputs

### 17. Add Operations SOPs to Documentation
**Status:** ✅ COMPLETE (2025-12-31)
**Why:** Need documented procedures for daily operations
**Files:** `mcp-server/central-command/frontend/src/pages/Documentation.tsx`
**Acceptance:**
- [x] SOP-OPS-001: Daily Operations Checklist
- [x] SOP-OPS-002: Onboard New Clinic
- [x] SOP-OPS-003: Image Compliance Appliance
- [x] SOP-OPS-004: Provision Site Credentials
- [x] SOP-OPS-005: Replace Failed Appliance
- [x] SOP-OPS-006: Offboard Clinic
- [x] SOP-OPS-007: L3 Incident Response

### 18. Deploy Production VPS with TLS
**Status:** ✅ COMPLETE (2025-12-31)
**Server:** Hetzner VPS (178.156.162.116)
**URLs:** api.osiriscare.net, dashboard.osiriscare.net, msp.osiriscare.net
**Acceptance:**
- [x] Docker Compose stack running (FastAPI, PostgreSQL, Redis, MinIO)
- [x] Caddy reverse proxy with auto-TLS
- [x] HTTPS for all endpoints
- [x] Client portal at /portal with magic-link auth
- [x] Frontend deployed with Operations SOPs

### 19. Test ISO Build on Linux
**Status:** ✅ COMPLETE (2026-01-02)
**Why:** ISO build requires x86_64-linux
**Acceptance:**
- [x] Run `nix build .#appliance-iso` on Hetzner VPS (NixOS)
- [x] ISO built successfully: 1.16GB with phone-home service
- [x] SHA256: `e05bd758afc6584bdd47a0de62726a0db19a209f7974e9d5f5776b89cc755ed2`
- [x] Verify ISO boots in VirtualBox (VM: osiriscare-appliance, 12GB RAM, 4 CPU)
- [x] SSH access working (192.168.88.247)
- [x] Phone-home to api.osiriscare.net working (60s interval, status: online)

### 20. Lab Appliance Test Enrollment
**Status:** ✅ COMPLETE (2026-01-02)
**Why:** Validate phone-home flow before real client
**Acceptance:**
- [x] Created test site: `test-appliance-lab-b3c40c` via API
- [x] Updated phone-home.py with API key (Bearer token) authentication
- [x] Configured appliance with site_id and api_key in `/var/lib/msp/config.yaml`
- [x] Verified phone-home checkins (every 60s)
- [x] Site status: "online", onboarding stage: "connectivity"
- [x] Agent version reporting correctly: v0.1.1-quickfix

### 21. First REAL Pilot Client Enrollment
**Status:** 🟡 IN PROGRESS (Physical appliance deployed 2026-01-02)
**Why:** Validate end-to-end deployment at actual healthcare site
**Acceptance:**
- [x] Identify pilot clinic (NEPA region) → physical-appliance-pilot-1aea78
- [x] Create production site via dashboard
- [x] Provision config with generate-config.py
- [x] Flash ISO to USB, install on HP T640
- [x] Verify phone-home in Central Command (checking in every 60s)
- [ ] Deploy full compliance-agent (not just phone-home) ← **NEXT**
- [ ] Confirm L1 rules syncing
- [ ] Evidence bundles uploading to MinIO

### 22. MinIO Object Lock Configuration
**Status:** ✅ COMPLETE (2026-01-01)
**Why:** Evidence must be immutable per HIPAA §164.312(b)
**Acceptance:**
- [x] Enable versioning on evidence bucket
- [x] Configure Object Lock with GOVERNANCE mode
- [x] Set 7-year retention for compliance tier
- [x] Test evidence cannot be deleted (delete creates marker, original retained)

### 22. North Valley Clinic Lab Setup (DC)
**Status:** ✅ COMPLETE (2026-01-01)
**Why:** Need Windows AD DC for compliance agent testing
**Location:** iMac (192.168.88.50) → VirtualBox → northvalley-dc
**Acceptance:**
- [x] VM created with VBoxManage (4GB RAM, 2 CPU, 60GB disk)
- [x] Windows Server 2019 Standard installed
- [x] Renamed to NVDC01, static IP 192.168.88.250
- [x] AD DS installed and promoted to DC (northvalley.local)
- [x] WinRM configured and accessible
- [x] Ping and WinRM tested from MacBook
- [x] Documentation updated (NETWORK.md, TECH_STACK.md)

**Lab Environment Build (9 Phases) - COMPLETE:**
- [x] Phase 1: File Server role + 5 SMB shares (PatientFiles, ClinicDocs, Backups$, Scans, Templates)
- [x] Phase 2: Windows Server Backup feature installed
- [x] Phase 3: AD Structure (6 OUs, 7 security groups, 8 users)
- [x] Phase 4: Audit logging (Logon/Account Management/Object Access)
- [x] Phase 5: Password policy (12 char min, 24 history, 90-day max, lockout after 5)
- [x] Phase 6: Windows Defender (real-time protection enabled)
- [x] Phase 7: Windows Firewall (all profiles enabled)
- [x] Phase 8: Test data files created in shares
- [x] Phase 9: Verification passed (8/8 checks)

**AD Users Created:**
| User | Role | Username |
|------|------|----------|
| Dr. Sarah Smith | Provider | ssmith |
| Dr. Michael Chen | Provider | mchen |
| Lisa Johnson | Nurse | ljohnson |
| Maria Garcia | Front Desk | mgarcia |
| Tom Wilson | Billing | twilson |
| Admin IT | IT Admin | adminit |
| SVC Backup | Service | svc.backup |
| SVC Monitoring | Service | svc.monitoring |

### 23. North Valley Clinic Workstation (Windows 10)
**Status:** ✅ COMPLETE (2026-01-01)
**Why:** Test owner/end-user perspective of compliance platform
**Location:** iMac (192.168.88.50) → VirtualBox → northvalley-ws01
**Acceptance:**
- [x] VM created with VBoxManage (4GB RAM, 2 CPU, 50GB disk)
- [x] Bridged networking configured
- [x] Windows 10 Pro installed
- [x] Static IP configured (192.168.88.251)
- [x] DNS pointing to DC (192.168.88.250)
- [x] Joined to northvalley.local domain
- [x] WinRM enabled for remote management
- [x] IT Admin (adminit) remote access verified
- [x] Domain secure channel verified (nltest)

---

## 🟡 Phase 11: Launch Readiness (Should Have)

### 24. Deploy Full Compliance Agent to Appliance
**Status:** ⭕ PENDING
**Why:** Physical appliance only runs phone-home, need full agent
**Files:** `packages/compliance-agent/`, `iso/appliance-image.nix`
**Acceptance:**
- [ ] Package compliance-agent as Nix derivation
- [ ] Update ISO to include full agent (not just phone-home.py)
- [ ] L1 rules download from Central Command on startup
- [ ] Evidence bundles upload to MinIO
- [ ] Rebuild ISO v9 with full agent

### 25. OpenTimestamps Blockchain Anchoring
**Status:** ⭕ PENDING
**Why:** Enterprise tier feature, proves evidence existed at time T
**Files:** TBD
**Acceptance:**
- [ ] Submit bundle hash to OpenTimestamps on bundle creation
- [ ] Store OTS proof in `anchor_proof` column
- [ ] Verify OTS proofs in verification endpoint
- [ ] UI shows "Anchored" status with Bitcoin block info

### 26. Multi-NTP Time Verification
**Status:** ⭕ PENDING
**Why:** Ensures timestamp integrity for evidence
**Files:** TBD
**Acceptance:**
- [ ] Query 3+ NTP servers before signing bundle
- [ ] Reject if time skew > 5 seconds between sources
- [ ] Store NTP source + offset in bundle metadata
- [ ] Alert if time verification fails

---

## Quick Reference

**Run tests:**
```bash
cd /Users/dad/Documents/Msp_Flakes/packages/compliance-agent
source venv/bin/activate
python -m pytest tests/ -v --tb=short
```

**Check for deprecation warnings:**
```bash
python -m pytest tests/ 2>&1 | grep -c "DeprecationWarning"
```

**SSH to appliance:**
```bash
ssh -p 4444 root@174.178.63.139
```

**Web UI tunnel:**
```bash
ssh -f -N -L 9080:192.168.56.103:8080 jrelly@174.178.63.139
open http://localhost:9080
```

**North Valley Lab (Windows DC + Workstation):**
```bash
# Access iMac lab host
ssh jrelly@192.168.88.50

# Ping Windows DC
ping 192.168.88.250

# WinRM test (DC)
python3 -c "
import winrm
s = winrm.Session('http://192.168.88.250:5985/wsman',
                  auth=('NORTHVALLEY\\\\Administrator', 'NorthValley2024!'),
                  transport='ntlm')
print(s.run_ps('hostname').std_out.decode())
"

# VM management
ssh jrelly@192.168.88.50 '/Applications/VirtualBox.app/Contents/MacOS/VBoxManage list runningvms'
ssh jrelly@192.168.88.50 '/Applications/VirtualBox.app/Contents/MacOS/VBoxManage startvm "northvalley-dc" --type headless'
ssh jrelly@192.168.88.50 '/Applications/VirtualBox.app/Contents/MacOS/VBoxManage startvm "northvalley-ws01" --type headless'
```

**North Valley Lab VMs:**
| VM | Hostname | IP | Role | Credentials |
|----|----------|-----|------|-------------|
| northvalley-dc | NVDC01 | 192.168.88.250 | AD Domain Controller | NORTHVALLEY\Administrator / NorthValley2024! |
| northvalley-ws01 | NVWS01 | 192.168.88.251 | Windows 10 Workstation | NORTHVALLEY\adminit / ClinicAdmin2024! |

**Domain Users (for interactive login):**
| User | Password | Role |
|------|----------|------|
| ssmith | ClinicUser2024! | Provider |
| adminit | ClinicAdmin2024! | IT Admin (has local admin) |

**Central Command (Production):**
```bash
ssh root@178.156.162.116
curl https://api.osiriscare.net/health
open https://dashboard.osiriscare.net
```

**Physical Appliance (HP T640):**
```bash
ssh root@192.168.88.246                # SSH to physical appliance
journalctl -u compliance-agent -f     # Watch agent logs
curl -s https://api.osiriscare.net/api/sites/physical-appliance-pilot-1aea78 | jq .
```

**Provisioning API:**
```bash
# Register MAC for auto-provisioning
curl -X POST https://api.osiriscare.net/api/provision \
  -H "Content-Type: application/json" \
  -d '{"mac_address":"XX:XX:XX:XX:XX:XX", "site_id":"...", "api_key":"..."}'

# Check MAC config
curl https://api.osiriscare.net/api/provision/XX%3AXX%3AXX%3AXX%3AXX%3AXX
```

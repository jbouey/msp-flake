# Session Archive - 2025-12


## 2025-12-03-context-scaffold.md

# Session: 2025-12-03 - Context Scaffold Implementation

**Duration:** ~1 hour  
**Focus Area:** AI agent context persistence system

---

## What Was Done

### Completed
- [x] Created `.agent/` directory scaffold
- [x] Created `CONTEXT.md` - Project overview with architecture, business model, current state
- [x] Created `NETWORK.md` - VM inventory, network topology, access procedures
- [x] Created `CONTRACTS.md` - Interface contracts, data types, API specs
- [x] Created `DECISIONS.md` - 7 ADRs documenting key architecture decisions
- [x] Created `TODO.md` - Prioritized task list with acceptance criteria
- [x] Created `sessions/SESSION_TEMPLATE.md` - Reusable handoff template
- [x] Created `README.md` - Documentation for the scaffold system

### Partially Done
- None

### Not Started (planned but deferred)
- Actual implementation work deferred to next session
- Context scaffold was prerequisite

---

## Key Decisions Made

| Decision | Rationale | Impact |
|----------|-----------|--------|
| Use 5-file structure | Separation of concerns, easier updates | Each file has single purpose |
| Sessions as separate files | Append-only history | No risk of overwriting context |
| Include quick commands | Copy-paste ready | Faster AI onboarding |

---

## Files Created

| File | Purpose |
|------|---------|
| `.agent/README.md` | Scaffold documentation |
| `.agent/CONTEXT.md` | Project overview |
| `.agent/NETWORK.md` | VM inventory |
| `.agent/CONTRACTS.md` | Interface specs |
| `.agent/DECISIONS.md` | ADRs |
| `.agent/TODO.md` | Task tracking |
| `.agent/sessions/SESSION_TEMPLATE.md` | Handoff template |
| `.agent/sessions/2025-12-03-context-scaffold.md` | This file |

[truncated...]

---

## 2025-12-04-guardrails-phi-windows.md

# Session Handoff Template

## Session: 2025-12-04 - L2 Guardrails, PHI Scrubbing, Windows Integration

**Duration:** ~2 hours
**Focus Area:** L2 LLM Guardrails completion, PHI scrubbing with Windows logs, BitLocker runbook testing

---

## What Was Done

### Completed
- [x] L2 LLM Guardrails - 70+ dangerous patterns implemented with regex support
- [x] Guardrails test suite - 42 tests covering all pattern categories
- [x] BitLocker runbook tested on Windows VM - AllEncrypted=True, Drifted=False
- [x] PHI scrubbing with Windows logs - tested against real Windows Security Events
- [x] Created `tests/test_phi_windows.py` - 17 comprehensive tests for Windows log formats
- [x] Fixed AV false positive issue - removed crypto mining pattern strings from blocklist
- [x] Verified files restored correctly after AV quarantine fix
- [x] Updated TODO.md with all completed items
- [x] Discovered async parallel drift checks already implemented (asyncio.gather)

### Not Started (planned but deferred)
- [ ] Backup Restore Testing Runbook - not urgent
- [ ] Phase 3 Cognitive Function Split - planning phase

---

## Key Decisions Made

| Decision | Rationale | Impact |
|----------|-----------|--------|
| Remove crypto mining patterns from blocklist | AV software flags strings like "xmrig", "minerd" even when in a blocklist | Added comment at lines 728-731 in level2_llm.py explaining removal |
| Keep 4 skipped tests as-is | They're intentional VM-dependent tests, not broken | Tests require USE_REAL_VMS=1 flag when VMs available |
| PHI scrubber handles Windows log formats | Windows Security Events have specific formats | Added 17 tests covering AD events, HIPAA audits, timestamps |

---

## Files Modified

| File | Change |
|------|--------|
| `src/compliance_agent/level2_llm.py` | Contains 70+ dangerous patterns (crypto mining patterns removed) |
| `tests/test_level2_guardrails.py` | 42 tests for guardrails, removed crypto mining test |
| `tests/test_phi_windows.py` | **NEW** - 17 tests for Windows log PHI scrubbing |
| `.agent/TODO.md` | Updated with completed items, test counts |

---

## Tests Status

[truncated...]

---

## 2025-12-04-windows-vm-batch-processing.md

# Session: 2025-12-04 - Windows VM Fix & Evidence Batch Processing

**Duration:** ~2 hours
**Focus Area:** Windows VM setup, test suite fixes, evidence batch processing

---

## What Was Done

### Completed
- [x] Fixed Windows VM connectivity (port 55985 → 55987 due to VBoxNetNA conflict)
- [x] Recreated Windows VM with proper WinRM port forwarding
- [x] Verified WinRM connectivity via SSH tunnel
- [x] Windows integration tests passing (3/3)
- [x] Auto healer integration tests passing with USE_REAL_VMS=1 (15/16)
- [x] Reduced skipped tests from 7 to 1 (NixOS VM still not configured)
- [x] Implemented evidence upload batch processing
- [x] Added `store_evidence_batch()` method with concurrency control
- [x] Added `sync_to_worm_parallel()` method with semaphore and progress callbacks
- [x] Added 8 new batch processing tests
- [x] Updated TODO.md (removed Phase 3 per user request)
- [x] Committed all Phase 2 changes

### Not Started (planned but deferred)
- [ ] NixOS VM setup - reason: no NixOS VM configured on 2014 iMac

---

## Key Decisions Made

| Decision | Rationale | Impact |
|----------|-----------|--------|
| Changed WinRM port to 55987 | Port 55985 was occupied by VBoxNetNA from old VM | Requires updated tunnel command |
| Removed Phase 3 Planning from TODO | User requested it | Cleaner TODO.md focused on Phase 2 |
| Used semaphore for batch uploads | Better control than batch slicing | More responsive progress tracking |

---

## Files Modified

| File | Change |
|------|--------|
| `evidence.py` | Added `store_evidence_batch()` and `sync_to_worm_parallel()` methods |
| `test_evidence.py` | Added 8 batch processing tests |
| `TODO.md` | Marked #4, #13, #14 complete; removed Phase 3 |
| `~/win-test-vm/Vagrantfile` (2014 iMac) | Changed WinRM port to 55987 |

---

## Tests Status

[truncated...]

---

## 2025-12-04-windows-vm-fix.md

# Session: 2025-12-04 - Windows VM Connection Fix & Test Fixes

**Duration:** ~2 hours
**Focus Area:** Windows integration testing, test suite fixes

---

## What Was Done

### Completed
- [x] Fixed 8 pre-existing test failures in test_web_ui.py (292→300 passed)
- [x] Fixed Windows VM WinRM connectivity issue
- [x] Updated test_windows_integration.py to support host:port format
- [x] Ran Windows integration tests successfully (3 passed)
- [x] Updated CREDENTIALS.md with correct SSH tunnel command

### Partially Done
- None

### Not Started (planned but deferred)
- None

---

## Key Decisions Made

| Decision | Rationale | Impact |
|----------|-----------|--------|
| Use host-only IP (192.168.56.102) instead of NAT forwarding | NAT port forwarding (55985→5985) was not working correctly - WinRM accepted connections but never responded to HTTP requests | Stable WinRM connectivity via SSH tunnel |
| Parse host:port format in test config | Allows flexible configuration without modifying test code | Tests work with `WIN_TEST_HOST="127.0.0.1:55985"` |

---

## Files Modified

| File | Change |
|------|--------|
| `tests/test_web_ui.py` | Fixed sample_evidence fixture to create proper directory structure with bundle.json in subdirs; fixed /api/health→/health endpoint; fixed total_bundles→total assertion; fixed _get_hash_chain_status→_verify_hash_chain method |
| `tests/test_windows_integration.py` | Added host:port parsing to is_windows_vm_available() and get_test_config(); updated WindowsTarget creation to use parsed port |
| `docs/CREDENTIALS.md` | Updated SSH tunnel command to use host-only IP (192.168.56.102:5985) |

---

## Tests Status

```
Core tests: 300 passed, 7 skipped, 0 failed
Windows integration: 3 passed
Total: 303 passed, 7 skipped, 0 failed
New tests added: None

[truncated...]

---

## 2025-12-28-mcp-server-deploy.md

# Session: Production MCP Server Deployment

**Date:** 2025-12-28
**Duration:** ~1 hour
**Focus:** Deploy production MCP server to Hetzner VPS + create architecture diagrams

---

## Summary

Deployed the production MCP Server stack to a Hetzner VPS and created comprehensive Mermaid architecture diagrams for the platform.

---

## Work Completed

### 1. Architecture Diagrams Created

Created `docs/diagrams/` with:

| File | Description |
|------|-------------|
| `system-architecture.mermaid` | Component relationships (NixOS, MCP, Agent, Backup) |
| `data-flow.mermaid` | Compliance checks → Healing → Evidence → Reports |
| `deployment-topology.mermaid` | Network boundaries, HIPAA zones, encrypted paths |
| `README.md` | Documentation for viewing and updating diagrams |

### 2. Production MCP Server Deployed

**Server:** Hetzner VPS at `178.156.162.116`

**Stack Components:**
- FastAPI application (:8000)
- PostgreSQL 16 (8 tables)
- Redis 7 (rate limiting + caching)
- MinIO (WORM evidence storage, :9000/:9001)

**Features Implemented:**
- Ed25519 signed orders with 15-minute TTL
- Rate limiting: 10 requests / 5 minutes / site_id
- L1 deterministic runbook selection
- 6 default HIPAA runbooks in database
- Pull-only architecture (appliances poll server)

**Server Location:** `/opt/mcp-server/`
```
/opt/mcp-server/
├── docker-compose.yml
├── .env (secrets)
├── init.sql (8-table schema)

[truncated...]

---

## 2025-12-31-appliance-iso-sops.md

# Session: Appliance ISO + SOPs Documentation

**Date:** 2025-12-31
**Duration:** Extended session
**Phase:** Phase 10 - Production Deployment + Appliance Imaging

---

## Session Objectives

1. Build compliance appliance ISO image for HP T640 thin clients
2. Add SOPs to Central Command documentation page
3. Deploy updated frontend to production VPS

---

## Completed Work

### 1. Appliance ISO Infrastructure

Created complete bootable USB/ISO infrastructure for lean compliance appliances.

**Target Hardware:** HP T640 Thin Client (4-8GB RAM, ~300MB working set)

**Files Created:**
- `iso/appliance-image.nix` - Main NixOS ISO configuration
- `iso/configuration.nix` - Base system configuration
- `iso/local-status.nix` - Nginx status page with Python API
- `iso/provisioning/generate-config.py` - Site provisioning script
- `iso/provisioning/template-config.yaml` - Configuration template

**Updated:**
- `flake-compliance.nix` - Added ISO outputs, apps, and nixosConfigurations

**Key Features:**
- Pull-only architecture (phones home every 60s)
- No local MCP server or Redis (lean mode)
- Local nginx status page on :80
- 8 HIPAA controls in phone-home payload
- mTLS certificate generation for site provisioning
- First-boot setup script

**Build Commands (requires Linux):**
```bash
nix build .#appliance-iso -o result-iso
# Test in QEMU
nix run .#test-iso
# Provision new site
python iso/provisioning/generate-config.py --site-id "clinic-001" --site-name "Test Clinic"
```

[truncated...]

---

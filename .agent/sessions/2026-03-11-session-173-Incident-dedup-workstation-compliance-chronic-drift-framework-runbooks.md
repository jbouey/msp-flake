# Session 173 — Incident Dedup Fix, Workstation Compliance, Chronic Drift Escalation, Framework Runbook Mapping

**Date:** 2026-03-11/12
**Previous Session:** 172

---

## Commits

| Hash | Description |
|------|-------------|
| 9c0a30f | fix: incident dedup 2h→24h, derive workstation compliance from incidents |
| 3e04af3 | feat: seed 160 control→runbook mappings for framework compliance |
| a73b76e | feat: chronic drift escalation + macOS label fix for device join |
| 049161b | fix: add scalar() to FakeResult in incident pipeline tests |

## Changes

### 1. Incident Dedup Window — 2h → 24h (CRITICAL FIX)
- **Problem**: Patching/update incidents recurring every few hours. Dedup window for resolved incidents was only 2 hours — after that, same drift created new incident each scan cycle.
- **Root cause**: `main.py` line 1466: `resolved_at > NOW() - INTERVAL '2 hours'`
- **Fix**: Extended to `INTERVAL '24 hours'`, outer window from 4h → 48h
- **Impact**: Stops `windows_update`, `linux_unattended_upgrades`, `firewall_status` etc. from creating 5+ duplicate incidents/day

### 2. Workstation Compliance Derived from Incidents (CRITICAL FIX)
- **Problem**: Workstation page showed "Unknown" / "Last Check: Never" for all devices despite active driftscans
- **Root cause**: `_link_devices_to_workstations()` copied `compliance_status` from `discovered_devices`, which defaults to 'unknown' and was never updated
- **Fix**: Now queries incidents table for per-hostname status (via `details->>'hostname'`) with platform-level fallback for pre-existing data
- **Also**: Sets `last_compliance_check` from most recent incident timestamp
- **Result**: DC (.250) and ws01 (.251) now show "drifted" with timestamps

### 3. Hostname Stored in Incident Details
- `host_id` (the target hostname/IP) now injected into incident `details` JSON on creation
- Enables future per-workstation incident linkage (was previously discarded)

### 4. Chronic Drift → L3 Escalation
- **Logic**: If same `incident_type` resolved 5+ times in 7 days for same appliance → skip L1, escalate to L3
- **Purpose**: Catches WIN-DEPLOY-UNREACHABLE (30 incidents in 7 days), windows_update with stopped WU service, etc.
- L1 rule matching skipped when chronic drift detected
- Test mocks updated with `FakeResult.scalar()` method

### 5. Framework Control → Runbook Mapping (Migration 086)
- **Problem**: `control_runbook_mapping` table existed but was empty — no way to find remediation runbook for a failed framework control
- **Fix**: Generated 160 mappings from `control_mappings.yaml` across HIPAA/SOC2/PCI/NIST/CIS
- Seeded on VPS immediately, saved as Migration 086 for persistence

### 6. macOS Label Fix in Device Join
- **Problem**: "Join Device" with OS type "macos" didn't set `label: "macos"` in credential JSON
- **Effect**: Daemon would route macOS targets through `linuxScanScript` instead of `macosScanScript`
- **Fix**: `_add_manual_device()` in `sites.py` now sets `label: 'macos'` when `os_type == 'macos'`

### 7. Framework Compliance Assessment
- Audited full framework pipeline: selection → evidence tagging → per-framework scoring → OTS anchoring
- All wired and working. Selecting framework doesn't change which checks run (by design — "one check, many reports")
- Framework selection is scoring attribution only

### 8. Chaos Lab Notes Saved
- **AD DC (.250) MUST have 6GB+ RAM** — 4GB causes WinRM failures, OOM, WIN-DEPLOY-UNREACHABLE spam
- **Workstations (.251) MUST have 6GB+ RAM** — same issue
- Saved to MEMORY.md for future sessions

## Test Results
- CI/CD: 195 passed, 0 failed (after test fix in 049161b)
- TypeScript: 0 errors
- ESLint: 14 pre-existing warnings (not from this session's changes)

## Next Session Priorities

1. **Bump DC + ws01 VMs to 6GB RAM** — root cause of all WinRM failures
2. **Add iMac (.50) via "Join Device" UI** with macOS OS type + SSH creds → enables 14 macOS compliance checks
3. **app.is_admin default flip** — wire `tenant_connection` into client portal + dashboard admin endpoints (Phase 4 P2)
4. **Compliance report PDF export** — auditor-facing deliverable: framework scores → evidence hashes → OTS proofs → runbook links
5. **Per-workstation incident detail view** — click a workstation → see its incidents (data now available via hostname in details)
6. **HIPAA 2025 NPRM delta audit** — verify control_mappings.yaml covers final rule requirements
7. **Appliance API key enforcement** — remove "no API keys" checkin fallback before onboarding real clients

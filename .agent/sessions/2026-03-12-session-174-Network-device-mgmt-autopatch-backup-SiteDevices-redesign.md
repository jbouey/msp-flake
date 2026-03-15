# Session 174: Network Device Management, Auto-Patch Rules, Backup Verification, SiteDevices Redesign

**Date:** 2026-03-12
**Status:** Complete

## What Was Done

### 1. Network Device Management (Phase 1)
- **Backend:** `NetworkDeviceAdd` Pydantic model with SNMP (v2c/v3), SSH, and REST API credential types
- **Backend:** `_add_network_device()` function + endpoints on admin (`/api/sites/{id}/devices/network`) and portal routes
- **Frontend:** `AddNetworkDeviceModal.tsx` — full modal with protocol-specific fields, vendor selection (Cisco/Ubiquiti/Aruba/Juniper/Meraki/Fortinet/MikroTik), device categories (switch/router/firewall/AP)
- **Safety:** All network devices are advisory-only. Modal includes warning that appliance never pushes changes.
- **Credentials:** Stored in `site_credentials` with types `network_snmp`, `network_ssh`, `network_api`
- **Device inventory:** Registered in `discovered_devices` with `device_type = 'network'`

### 2. Auto-Patching L1 Rules
- Windows: `L1-WIN-PATCH-001` (windows_updates → WIN-PATCH-001), confidence 0.75
- Linux: `L1-LIN-PATCH-001` (linux_unattended_upgrades → LIN-UPGRADES-001)
- macOS: `L1-MAC-PATCH-001` (macos_auto_update → MAC-UPD-001)
- 5 framework control mappings for Windows patching (HIPAA/SOC2/PCI/NIST/CIS)

### 3. Backup Verification
- Windows backup runbook `ESC-WIN-BACKUP` — checks VSS snapshots, restore points, WBSummary (escalation)
- Linux backup runbook `ESC-LIN-BACKUP` — checks restic/borg/rsync cron + /var/backups freshness (escalation)
- macOS Time Machine rule `L1-MAC-BACKUP-001` (already had MAC-TM-001 runbook)
- 10 framework control mappings for backup across Windows + Linux
- All backup findings escalate to L3 — can't auto-configure backup targets

### 4. Network Device L1 Rules
- 4 escalation rules: unexpected ports, missing services, unreachable hosts, DNS failure
- 4 new runbooks (ESC-NET-PORTS/SVC/REACH/DNS) with advisory escalation steps
- 20 framework control mappings

### 5. SiteDevices Page Redesign
- Replaced cluttered two-button + badge header with single "Add Device" dropdown
- Dropdown shows "Join Endpoint" (SSH) and "Add Network Device" (read-only) with descriptions
- Device count integrated into subtitle
- Removed redundant info banner

### Migration 090
Applied to VPS. Totals: **112 L1 rules, 169 runbooks, 330 framework mappings**

### Architecture Decision: Network Device Remediation
- **L1:** Detect only (open ports, service down, DNS fail)
- **L2:** Diagnose + generate vendor-specific advisory commands (copy-paste-ready)
- **L3:** ALWAYS for network changes — human execution required
- Rationale: Network misconfiguration can sever the management channel itself

## Commits
- `dca4e13` feat: network device management + auto-patching L1 rules + backup verification

## Files Changed
- `mcp-server/central-command/backend/sites.py` — NetworkDeviceAdd model + _add_network_device()
- `mcp-server/central-command/backend/portal.py` — Portal network device endpoint
- `mcp-server/central-command/backend/migrations/090_*.sql` — New migration
- `mcp-server/central-command/frontend/src/components/shared/AddNetworkDeviceModal.tsx` — New modal
- `mcp-server/central-command/frontend/src/pages/SiteDevices.tsx` — Dropdown redesign

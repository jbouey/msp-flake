# Session 162: macOS Drift, OUI Lookup, Drift Config, Portal Fixes

**Date:** 2026-03-08
**Focus:** macOS drift scanning, per-site drift config, portal auth fixes, MAC OUI device hints

## Completed

### macOS Drift Scanning (14 HIPAA checks)
- Added `macosscan.go` with 13 active checks: FileVault, Gatekeeper, SIP, firewall, auto-updates, screen lock, file sharing, Time Machine, NTP, admin users, disk space, cert expiry
- Removed SSH/remote login check (flags the management channel itself)
- Routes macOS targets via `linuxscan.go` label detection (`lt.Label == "macos"`)

### Per-Site Drift Scan Configuration
- Migration 075: `site_drift_config` table with 47 default check types, `macos_remote_login` disabled by default
- Admin dashboard `DriftConfig.tsx`: toggle grid grouped by platform (Windows/Linux/macOS)
- Backend: GET/PUT `/api/dashboard/sites/{site_id}/drift-config`
- Daemon: `disabledChecks` map populated from checkin response `disabled_checks` array
- All 3 scan types (Windows, Linux, macOS) filter findings via `isCheckDisabled()`

### Drift Config in Partner + Client Portals
- Partner: GET/PUT `/me/sites/{site_id}/drift-config` with ownership verification
- Client: GET/PUT `/client/sites/{site_id}/drift-config` with org ownership verification
- Frontend: `PartnerDriftConfig.tsx` (indigo theme), `ClientDriftConfig.tsx` (teal theme)
- "Security Checks" button on partner + client dashboard site rows
- Compliance scoring (`db_queries.py`): all 3 scoring functions exclude disabled checks

### SRA Remediation Save Fix
- Root cause: CSRF middleware blocked companion/client portal PUT/POST requests
- Fix: exempted `/api/companion/` and `/api/client/` prefixes (session-auth protected)
- Added CSRF token headers to SRAWizard fetch calls
- Added visual save confirmation (checkmark + error states)

### MAC OUI Device Type Hints
- Created `oui_lookup.py`: ~500+ MAC prefix entries covering major manufacturers
- Device classes: server, workstation, network, printer, phone, iot, virtual, unknown
- Integrated into `device_sync.py` `get_site_devices()` — enriches API response on-the-fly
- Frontend: manufacturer shown italic under MAC column, type hint shown for "unknown" devices
- Expanded details: manufacturer + device class with "inferred" tooltip

### Daemon Deploy
- Built + deployed daemon v0.3.19 via fleet order (macOS scanning + drift config filtering)
- Fleet order `19e5bd7d` deployed to both appliances

## Key Commits
- `2949a50` feat: add macOS drift scanning (14 HIPAA security checks via SSH)
- `1c4ff8f` feat: per-site drift scan configuration with UI toggles
- `1517c0f` fix: SRA remediation save broken by CSRF + add save confirmation
- `a28422e` feat: drift config in partner + client portals, exclude disabled checks from compliance score

## Files Changed (Backend)
- `backend/oui_lookup.py` (new) — MAC OUI lookup module
- `backend/device_sync.py` — OUI hint enrichment in device list API
- `backend/migrations/075_drift_scan_config.sql` (new) — drift config table
- `backend/routes.py` — drift config admin endpoints
- `backend/sites.py` — checkin disabled_checks delivery
- `backend/partners.py` — partner drift config endpoints
- `backend/client_portal.py` — client drift config endpoints
- `backend/db_queries.py` — compliance score exclusions
- `backend/csrf.py` — portal CSRF exemptions

## Files Changed (Frontend)
- `frontend/src/pages/DriftConfig.tsx` (new)
- `frontend/src/partner/PartnerDriftConfig.tsx` (new)
- `frontend/src/client/ClientDriftConfig.tsx` (new)
- `frontend/src/pages/SiteDevices.tsx` — OUI manufacturer hints in device table
- `frontend/src/utils/api.ts` — manufacturer_hint type + drift config API
- `frontend/src/client/compliance/SRAWizard.tsx` — CSRF fix + save confirmation

## Files Changed (Go Daemon)
- `appliance/internal/daemon/macosscan.go` (new)
- `appliance/internal/daemon/driftscan.go` — isCheckDisabled filter
- `appliance/internal/daemon/linuxscan.go` — macOS routing + disabled filter
- `appliance/internal/daemon/daemon.go` — disabledChecks map
- `appliance/internal/daemon/phonehome.go` — DisabledChecks in response
- `appliance/internal/checkin/models.go` — DisabledChecks field

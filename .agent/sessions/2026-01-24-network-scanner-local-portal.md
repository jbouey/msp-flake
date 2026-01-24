# Session 69 - Network Scanner & Local Portal Implementation

**Date:** 2026-01-24
**Duration:** ~2 hours
**Focus:** Implement network scanning and local portal for device transparency

## Summary

Implemented the complete "Sovereign Appliance" architecture with two new services:
- **network-scanner.service (EYES)** - Device discovery and classification
- **local-portal.service (WINDOW)** - React-based local UI for device transparency

## Key Decisions Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Medical Devices | **EXCLUDE COMPLETELY** | Patient safety - require manual opt-in |
| Scanner Credentials | Separate from healer | Blast radius containment |
| Local Portal UI | React (matching Central Command) | Consistent UX |
| Daily Scan Time | 2 AM | Minimal disruption |
| Database | SQLite with WAL | Offline-first, crash-safe |

## Packages Created

### 1. `packages/network-scanner/` (92 tests)

```
src/network_scanner/
├── _types.py              # Device, ScanResult, MEDICAL_DEVICE_PORTS
├── config.py              # ScannerConfig (separate credentials)
├── device_db.py           # SQLite operations
├── classifier.py          # Device type from ports/OS
├── scanner_service.py     # Main service loop + API
└── discovery/
    ├── ad_discovery.py    # Active Directory LDAP
    ├── arp_discovery.py   # ARP table scanning
    ├── nmap_discovery.py  # Port scanning
    └── go_agent.py        # Go agent check-ins
```

### 2. `packages/local-portal/` (23 tests)

```
src/local_portal/
├── main.py                # FastAPI app
├── config.py              # PortalConfig
├── db.py                  # Database access
├── routes/
│   ├── dashboard.py       # KPIs
│   ├── devices.py         # Device CRUD
│   ├── scans.py           # Scan management
│   ├── compliance.py      # Compliance status
│   ├── exports.py         # CSV/PDF generation
│   └── sync.py            # Central Command sync
└── services/
    ├── pdf_generator.py   # ReportLab PDF
    └── central_sync.py    # Push to Central Command

frontend/
├── src/
│   ├── components/        # GlassCard, Badge, KPICard
│   ├── pages/             # Dashboard, Devices, Compliance, Exports
│   ├── hooks/             # React Query hooks
│   └── api/               # API client
├── tailwind.config.js     # Matching Central Command design
└── package.json           # React + Vite + TailwindCSS
```

### 3. NixOS Modules

- `modules/network-scanner.nix` - Systemd service with daily timer
- `modules/local-portal.nix` - Systemd service with nginx integration

### 4. Central Command Sync API

- `backend/device_sync.py` - Receive device inventory
- `backend/routes/device_sync.py` - REST endpoints
- SQL migration for `discovered_devices` table

## Medical Device Detection

Ports that trigger EXCLUDED classification:
- **DICOM:** 104, 11112, 2761, 2762, 4242, 8042, 8043, 11113-11115
- **HL7:** 2575

Hostname patterns:
- modality, pacs, dicom, xray, ct-, mri-, ultrasound
- ventilator, ecg, ekg, infusion, philips, ge-healthcare, siemens

## Test Results

```
network-scanner:  92 tests passing
local-portal:     23 tests passing
frontend:         builds successfully
─────────────────────────────────
Total:           115 tests
```

## Files Created

| Package | Files |
|---------|-------|
| network-scanner | 16 Python files, 5 test files |
| local-portal backend | 10 Python files, 1 test file |
| local-portal frontend | 15 TypeScript files |
| NixOS modules | 2 Nix files |
| Central Command | 3 Python files |

## API Endpoints

### Network Scanner (port 8082)
- `POST /api/scans/trigger` - Trigger on-demand scan
- `GET /api/scans/status` - Get scan status

### Local Portal (port 8083)
- `GET /api/dashboard` - Dashboard KPIs
- `GET /api/devices` - Device inventory
- `GET /api/devices/{id}` - Device detail
- `PUT /api/devices/{id}/policy` - Update scan policy
- `GET /api/compliance/summary` - Compliance summary
- `GET /api/compliance/drifted` - Drifted devices
- `GET /api/exports/csv/devices` - CSV export
- `GET /api/exports/pdf/compliance` - PDF report
- `POST /api/sync` - Push to Central Command

### Central Command Sync
- `POST /api/devices/sync` - Receive device inventory
- `GET /api/devices/sites/{site_id}` - List site devices
- `GET /api/devices/sites/{site_id}/summary` - Device summary
- `GET /api/devices/sites/{site_id}/medical` - Medical devices

## Architecture

```
Physical Appliance (NixOS)
├── compliance-agent.service (HANDS) - existing
├── network-scanner.service (EYES) - NEW
│   └── /var/lib/msp/devices.db
└── local-portal.service (WINDOW) - NEW
    ├── FastAPI backend on port 8083
    └── React frontend (static files)
```

## Next Steps

1. **Integration Test:** Deploy to physical appliance
2. **ISO Update:** Add modules to appliance-image.nix
3. **Central Command UI:** Add device inventory view to dashboard
4. **Documentation:** Create user guide for local portal

## Commits

(Pending - implementation complete, ready for commit)

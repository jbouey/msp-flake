# Session 126 - Non-AD Device Join UI, Flywheel Data Gaps, Fleet Order Delivery

**Date:** 2026-02-22
**Previous Session:** 125

---

## Goals

- [x] Audit flywheel data gaps and fix L1 telemetry + field name mismatches
- [x] Add fleet order delivery to Go checkin-receiver
- [x] Deploy v0.2.2 Go daemon to both appliances via fleet order
- [x] Build non-AD device join UI for portal + dashboard

---

## Progress

### Completed

1. **Flywheel data gap fixes** — Go daemon L1 telemetry was completely missing. Added `ReportL1Execution()` to telemetry.go, wired into daemon heal paths. Fixed field name mismatch in backend ingestion (level→resolution_level, duration_ms→duration_seconds, error→error_message).

2. **Fleet order delivery** — Go checkin-receiver only fetched admin_orders + healing_orders. Added `FetchFleetOrders()` to db.go, cross-compiled new binary, deployed to VPS. Created fleet order, both appliances rebuilt to v0.2.2.

3. **Non-AD device join** — Full-stack feature: backend endpoints (admin + portal), shared AddDeviceModal component, wired into SiteDevices.tsx and PortalDashboard.tsx. No migration needed — uses existing site_credentials + discovered_devices tables. Appliance picks up new linux_targets on next checkin.

### Blocked

None.

---

## Files Changed

| File | Change |
|------|--------|
| `backend/sites.py` | CredentialCreate extended, ManualDeviceAdd model, _add_manual_device helper, POST endpoint |
| `backend/portal.py` | device_count in PortalData, POST portal device endpoint |
| `frontend/src/components/shared/AddDeviceModal.tsx` | **New** shared modal component |
| `frontend/src/pages/SiteDevices.tsx` | "Join Device" button + modal wiring |
| `frontend/src/portal/PortalDashboard.tsx` | "Managed Devices" section + modal wiring |
| `appliance/internal/l2planner/telemetry.go` | ReportL1Execution method |
| `appliance/internal/daemon/daemon.go` | Telemetry reporter wiring |
| `appliance/internal/checkin/db.go` | FetchFleetOrders |
| `.claude/skills/docs/backend/backend.md` | Updated with new endpoints |

## Commits

- `b143db4` — fix: close flywheel data gaps — L1 telemetry + field name compat
- `efbe532` — chore: bump Go daemon to v0.2.2
- `b04e82b` — feat: fleet order delivery in Go checkin-receiver
- `ee954fa` — feat: non-AD device join — portal + dashboard UI for standalone Mac/Linux

---

## Next Session

1. Test device join end-to-end (add a real device, verify appliance scans it)
2. Verify VM appliance completed rebuild to v0.2.2
3. Confirm L1 telemetry is now flowing into execution_telemetry table
4. HIPAA administrative compliance modules (session 122 planned)

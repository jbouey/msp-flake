# Session 178 — Resilience Layers, v0.3.21 Fleet Deploy, MFA Migration

**Date:** 2026-03-12/13
**Focus:** Implement resilience architecture layers 1-3, build+deploy Go daemon v0.3.21, apply MFA migration, scaffold demo video pipeline

## Completed

### 1. Health Monitor (Resilience Layer 1)
- **`health_monitor.py`** — Background loop every 5min (3min startup delay)
- Detects offline appliances (15min threshold), sends warning (30min), critical (2hr), recovery notifications
- Uses `admin_connection(pool)` pattern, `clinic_name` for site labels
- **Migration 092** — `offline_since`, `offline_notified` columns + last_checkin index on `site_appliances`
- Wired into `main.py` lifespan via `_supervised()` wrapper
- Tests: `test_health_monitor.py` (3 tests)

### 2. Billing Guard (Resilience Layer 2)
- **`billing_guard.py`** — `check_billing_status(conn, site_id)` returns (status, is_active)
- Statuses: active/trialing/none → allowed; past_due → 7-day grace; canceled → blocked
- Fails open on DB errors (HIPAA monitoring continues)
- Integrated at checkin Step 7b — strips healing orders on billing_hold
- Tests: `test_billing_guard.py` (9 tests)

### 3. Kill Switch (Resilience Layer 3)
- Backend endpoints: `POST /{site_id}/disable-healing` and `/{site_id}/enable-healing`
- Creates fleet order + audit log entry for traceability
- Go daemon: `handleDisableHealing()` / `handleEnableHealing()` write persistent flag to `/var/lib/msp/healing_enabled`
- `IsHealingEnabled()` checked in daemon healing dispatch (defaults true if file missing)
- Handler count test updated 19 → 21

### 4. Go Daemon v0.3.21
- Version bumped in `daemon.go` + `appliance-disk-image.nix`
- Built successfully on VPS (nix build, verified `appliance-daemon 0.3.21` output)
- Fleet order `359717f5` created — targets all appliances, skip-version 0.3.21, expires 72h
- Note: VM appliance may need manual rebuild (VBox can't self-restart daemon)

### 5. Migration 072 (MFA Columns)
- Verified all 9 columns present on admin_users, partner_users, client_users
- All returned "already exists, skipping" — previously applied

### 6. Demo Video Pipeline (TODO'd)
- Scaffolded `demo-videos/` with ElevenLabs + HeyGen integration
- 6 demo scripts (dashboard tour through client portal)
- FFmpeg compose script for circle-crop avatar overlay
- Set aside for later execution

### 7. Cleanup
- Dead `checkin-receiver` container already removed
- Caddy route clean

## Deployment Issues Fixed
- `require_admin` import missing in sites.py → added to import line
- Migration 092 not auto-applied → manual ALTER TABLE + CREATE INDEX on VPS
- `s.name` → `s.clinic_name` in health_monitor.py and sites.py (4 occurrences)
- Go test handler count 19 → 21

## System State at End
- All 7 containers healthy on VPS
- Both appliances checking in on v0.3.20, fleet order pending for v0.3.21
- Health monitor running, billing guard active, kill switch endpoints live
- 0% healing rate in chaos report was transient (5min window during deploy)

## Next Priorities
1. **Verify v0.3.21 fleet deploy** — confirm both appliances upgrade
2. **Offline queue** (Resilience Layer 4) — SQLite queue for connectivity loss
3. **Apollo lead enrichment** — People Search 403 on trial; evaluate upgrade vs Brave+Hunter fallback
4. **Appliance API key rotation** — still a security blocker
5. **Demo video production** — ElevenLabs + HeyGen signup, first video

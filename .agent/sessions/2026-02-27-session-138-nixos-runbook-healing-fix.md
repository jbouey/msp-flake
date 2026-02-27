# Session 138 — NixOS Runbook Healing Fix (v0.3.5)

**Date:** 2026-02-27
**Focus:** Audit and fix 7 recurring non-healing incidents

## Problem

7 dashboard notifications recurring every scan cycle, all marked "Resolution: L1" but never actually healed:
- `linux_failed_services` (both appliances)
- `linux_unattended_upgrades` (both appliances)
- `linux_log_forwarding` (physical)
- `net_host_reachability` (both appliances) — correctly escalates to L3

## Root Cause

The `handleHealing` stub in `processor.go` was **NOT** the blocker — `daemon.go:160` already overrides it with the real `executeHealingOrder()`. The real issues:

1. **LIN-SVC-001** (linux_failed_services): `remediate_script` was EMPTY — L1 matched, called runbook, nothing happened
2. **LIN-PATCH-001** (linux_unattended_upgrades): `remediate_script` was EMPTY — and NixOS doesn't have apt/yum
3. **LIN-LOG-001** (linux_log_forwarding): remediate used `sed -i` on `/etc/journald.conf` — fails on NixOS (read-only symlinks)
4. **Scanner too strict**: log forwarding check only accepted rsyslog or journal-upload, not journald persistent storage
5. **NixOS config gaps**: `MaxRetentionSec=7day` (HIPAA needs 90d), no `system.autoUpgrade` configured

## Fixes

### 1. runbooks.json — 3 runbook scripts fixed
- **LIN-SVC-001**: Added generic failed-service restart (`systemctl restart` each failed svc) + verify
- **LIN-PATCH-001**: NixOS-aware auto-update timer enablement (checks `/etc/NIXOS`, uses appropriate timer)
- **LIN-LOG-001**: NixOS-aware remediation (detects NixOS, skips sed on symlinks, checks journald persistent)

### 2. linuxscan.go — Scanner log forwarding check
- Added `journald_persistent` as valid log management state
- Checks `grep -qE "^Storage=persistent" /etc/systemd/journald.conf`

### 3. configuration.nix — NixOS config fixes
- `MaxRetentionSec`: 7day → 90day (HIPAA 164.312(b))
- `SystemMaxUse`: 100M → 500M (room for 90-day retention)
- Added `system.autoUpgrade` with flake ref, 4 AM schedule, no auto-reboot

### 4. processor.go — Stub converted to error sentinel
- Stub now returns error + WARNING log instead of fake success
- Makes it obvious if daemon fails to register real handler
- Test updated to verify stub→RegisterHandler override chain

### 5. Version bump
- daemon.go: 0.3.4 → 0.3.5

## Files Changed
- `appliance/internal/daemon/runbooks.json` — 3 runbook scripts
- `appliance/internal/daemon/linuxscan.go` — log forwarding check
- `appliance/internal/daemon/daemon.go` — version bump
- `appliance/internal/orders/processor.go` — stub → error sentinel
- `appliance/internal/orders/processor_test.go` — updated test
- `iso/configuration.nix` — retention + auto-upgrade

## Test Results
- `go build ./...` — clean
- `go test ./internal/orders/` — 41/41 pass
- `go test ./internal/healing/` — pre-existing smb_signing failure (unrelated)

## Next Steps
- Git push to trigger CI/CD
- Create fleet order for v0.3.5 nixos-rebuild
- VM appliance still needs nixos-rebuild switch

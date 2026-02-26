# Session 135 - Resilience Hardening: sd_notify, State Persistence, Subscription Gating

**Date:** 2026-02-26
**Started:** 02:35
**Previous Session:** 134

---

## Goals

- [x] Systemd crash-loop protection on all services
- [x] sd_notify watchdog integration for appliance-daemon
- [x] Go daemon state persistence across restarts
- [x] Subscription enforcement — gate healing on active/trialing
- [x] Connectivity error classification for better diagnostics
- [x] Build, deploy, create fleet order

---

## Progress

### Completed

1. **Crash-loop protection** — `StartLimitBurst=5/IntervalSec=300` on appliance-daemon, network-scanner, local-portal
2. **sd_notify watchdog** — new `sdnotify` package (zero-cgo), `WatchdogSec=120s` on all 3 services, `Type=notify` for daemon, Ready/Watchdog/Stopping calls in daemon.go
3. **State persistence** — new `state.go`, saves linux_targets + l2_mode + subscription_status to `/var/lib/msp/daemon_state.json` with atomic write (tmp+rename), loaded on startup
4. **Subscription enforcement** — `FetchSubscriptionStatus()` JOINs sites→partners, `SubscriptionStatus` in CheckinResponse, `isSubscriptionActive()` gates auto-deploy + heal requests, drift detection continues in degraded mode
5. **Connectivity classification** — `classifyConnectivityError()` using `errors.As` for DNS/OpError, string matching for timeout/tls/5xx
6. **Deployed** — binaries to VPS, checkin-receiver restarted, CI/CD triggered, fleet order 3bf579c6

### Blocked

- WinRM 401 on DC — needs home network
- HIPAA compliance at 56% — needs more check coverage

---

## Files Changed

| File | Change |
|------|--------|
| `iso/appliance-disk-image.nix` | StartLimitBurst, WatchdogSec, Type=notify |
| `appliance/internal/sdnotify/sdnotify.go` | NEW — zero-cgo sd_notify helper |
| `appliance/internal/daemon/state.go` | NEW — state persistence (save/load JSON) |
| `appliance/internal/daemon/daemon.go` | sd_notify calls, subscription gating, state save/load, connectivity classification |
| `appliance/internal/daemon/phonehome.go` | SubscriptionStatus field, classifyConnectivityError() |
| `appliance/internal/checkin/db.go` | FetchSubscriptionStatus(), Step 9 in ProcessCheckin |
| `appliance/internal/checkin/models.go` | SubscriptionStatus field |
| `.claude/skills/docs/hipaa/compliance.md` | Resilience & Offline Operation section |
| `.claude/skills/docs/nixos/infrastructure.md` | sd_notify, state persistence, crash-loop docs |

## Commits

- `19a0177` feat: resilience hardening — sd_notify, state persistence, subscription gating, crash-loop protection

---

## Next Session

1. WinRM 401 on DC — investigate when on home network
2. HIPAA compliance at 56% — add more check coverage to reach 80%+
3. Python service watchdog pings (network-scanner, local-portal)
4. A/B partition rollback (deferred from session 133)
5. Verify fleet order 3bf579c6 was picked up by both appliances

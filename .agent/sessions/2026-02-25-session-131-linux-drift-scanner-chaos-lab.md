# Session 131 — Linux Drift Scanner + Chaos Lab Full-Spectrum

**Date:** 2026-02-25
**Duration:** ~90 minutes
**Focus:** Fix Linux drift scanning, run chaos lab, deploy v0.2.3 daemon

---

## Completed

### 1. Fleet Updates Version Fix
- Fleet Updates page showed stale `v1.0.52` (old Python agent) for a month
- Added auto-detection: query `appliances` table for most common `agent_version` from recent checkins
- Added "Deployed Version" card (5-column grid) showing live version + appliance count
- Inserted `v0.2.2` release in `update_releases`, deactivated old Python releases
- **Commit:** `cbfe457`

### 2. Go Daemon SudoPassword Support (linuxscan.go)
- **Root cause:** `sshexec.Target.SudoPassword` was never set during Linux scan
  - `linuxTarget` struct: added `SudoPassword` field
  - `parseLinuxTargets()`: extract `sudo_password` with password fallback
  - Target construction: added `target.SudoPassword = &lt.SudoPassword`
- **Backend (sites.py):** Added `sudo_password` passthrough — uses `password` as fallback
- **Commit:** `837426d` (backend), `6f699f3` (Go daemon)

### 3. Linux Scan Script Fixes
- **ufw detection:** Ubuntu uses ufw, not nft/iptables. Added `ufw status` check first
- **Numeric sanitization:** `grep -c` can output multi-line values causing Python SyntaxError
  - Added `head -1 | tr -dc '0-9'` for `fw_rules`, `failed_count`, `disk_pct`
  - Pre-Python sanitization block ensures all numeric vars are clean integers
- **Better error logging:** Show exit code + stderr on scan failure (was empty string)

### 4. Credential Fixes
- Linux target password was `msp123` (wrong) — fixed to `NorthValley2024!` for both appliances
- Added WinRM credentials for VM appliance (`test-appliance-lab-b3c40c`)

### 5. Chaos Lab Full-Spectrum Test
Injected drift on 192.168.88.242 (northvalley-linux):
- SSH: PermitRootLogin=yes, PasswordAuth=yes, MaxAuthTries=10
- Firewall: ufw disabled
- Service: auditd stopped
- Permissions: /etc/shadow=644, sshd_config=666

**Results — 7 drift findings detected:**

| Finding | Rule | Outcome |
|---------|------|---------|
| SSH config drift | L1-SSH-001 | Runbook dispatched |
| Failed services | L1-LIN-SVC-001 | Runbook dispatched |
| SUID binaries | L1-SUID-001 | Auto-healed (4.5s) |
| User accounts | L1-LIN-USERS-001 | Escalated to L3 |
| File permissions | L1-LIN-PERM-001 | Runbook dispatched |
| No auto-updates | L1-LIN-UPGRADES-001 | Runbook dispatched |
| No log forwarding | L1-LIN-LOG-001 | Auto-healed (4.2s) |

### 6. Daemon Deployment
- Cross-compiled v0.2.3 binary (`GOOS=linux GOARCH=amd64 CGO_ENABLED=0`)
- Deployed to both appliances via bind mount over nix store binary
- Verified: both showing `OsirisCare Appliance Daemon v0.2.3 starting`

---

## Bugs Found & Fixed

1. **Empty scan error** — sshexec.Execute returns Success=false with empty Error when exit code != 0 (no Go-level error). Added stderr + exit code to log message.
2. **grep -c multiline** — `nft list ruleset | grep -c "rule"` can return `0\n0` on Ubuntu. Broke Python JSON interpolation.
3. **Wrong credential password** — DB had `msp123` but actual osiris password is `NorthValley2024!`.
4. **Missing ufw detection** — Script only checked nft/iptables, not ufw (standard on Ubuntu).

## Files Changed

| File | Change |
|------|--------|
| `appliance/internal/daemon/linuxscan.go` | SudoPassword, ufw detection, numeric sanitization, error logging |
| `mcp-server/central-command/backend/sites.py` | sudo_password passthrough in checkin |
| `mcp-server/central-command/backend/fleet_updates.py` | Auto-detect deployed version |
| `mcp-server/central-command/frontend/src/pages/FleetUpdates.tsx` | Deployed Version card |
| `mcp-server/central-command/frontend/src/utils/api.ts` | FleetStats.fleet field |

## Commits

- `cbfe457` — fix: fleet updates show current Go daemon — auto-detect deployed version
- `837426d` — fix: pass sudo_password in linux_targets — enables sudo scan commands
- `6f699f3` — fix: Linux drift scanner — SudoPassword support, ufw detection, numeric sanitization

---

## Known Issues

- **WinRM 401 on DC (192.168.88.250):** Windows drift scan failing with HTTP 401 "invalid content type". May be credential or WinRM configuration issue.
- **NixOS self-scan `bash not found`:** Appliance's NixOS doesn't have `bash` in PATH (uses `/run/current-system/sw/bin/bash`). Self-scan always fails. Low priority — remote scanning is the primary use case.
- **Bind mount not persistent:** v0.2.3 deployment via bind mount survives service restarts but not reboots. Need nixos-rebuild for permanent deployment.

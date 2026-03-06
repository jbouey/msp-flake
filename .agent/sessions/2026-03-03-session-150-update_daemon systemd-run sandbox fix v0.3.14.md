# Session 150 - update_daemon systemd-run Sandbox Fix (v0.3.14)

**Date:** 2026-03-03
**Started:** 08:49
**Previous Session:** 149

---

## Goals

- [x] Fix update_daemon handler to work on NixOS (ProtectSystem=strict sandbox)
- [x] Deploy v0.3.14 to both appliances
- [x] End-to-end fleet order test
- [x] NixOS rebuild to bake v0.3.14 into nix store permanently
- [x] Fleet order CLI tooling — create/list/cancel with signing, Mac wrapper, e2e tested
- [ ] Fix DC clock (checked — likely fine, WinRM Kerberos works)
- [ ] Verify ws01 agent enrollment (agent not running — port 50051 closed on ws01)

---

## Progress

### Completed

- Fixed systemd override install: use systemd-run to escape ProtectSystem=strict sandbox
- Fixed NixOS bash path: `/run/current-system/sw/bin/bash` (not `/bin/bash`)
- Fixed systemd-run PATH: transient units have minimal PATH, set via --setenv
- Added `api.osiriscare.net` to allowedDownloadDomains
- Deployed v0.3.14 to physical + VM appliances + VPS updates dir
- Fleet order end-to-end verified: download -> SHA256 -> override -> restart -> success
- NixOS rebuild fleet order: both appliances rebuilt, `nixos-rebuild switch` persisted
- Removed runtime systemd overrides — now running nix store binary directly
- Nix store confirms: `/nix/store/0c51c3mnxwvw7qd3v0k9wxqp9cgsvcsy-appliance-daemon-0.3.14`
- Cancelled stale fleet orders, committed ec8633d (nix derivation bump)
- Fleet order CLI tool: `fleet_cli.py` (create/list/cancel with Ed25519 signing)
- Mac wrapper: `scripts/fleet-order.sh` (SSH → docker exec → fleet_cli.py)
- End-to-end verified: create force_checkin → both appliances completed → cancel

### Findings

- **DC clock**: Likely fine. WinRM session to DC works (Kerberos requires <5min skew). Drift scan 0 drifts. The 0x8009030d error on DC->ws01 is SEC_E_LOGON_DENIED, not time skew (0x80090324).
- **ws01 agent**: Port 50051 CLOSED on ws01 (192.168.88.251). WinRM (5985) and SMB (445) open. Agent service likely crashed or never started properly after manual deploy in Session 149.
- **NixOS watchdog gap**: `nixos-rebuild test` succeeds and daemon reports success, but watchdog can't persist with `switch` because the marker file (.rebuild-in-progress) gets cleaned up before watchdog runs. Need manual `nixos-rebuild switch` or fix the marker lifecycle.

---

## Files Changed

| File | Change |
|------|--------|
| appliance/internal/orders/processor.go | systemd-run for override install, api.osiriscare.net allowlist |
| appliance/internal/daemon/daemon.go | Version 0.3.13 -> 0.3.14 |
| iso/appliance-disk-image.nix | Nix daemon derivation 0.3.10 -> 0.3.14 |
| mcp-server/central-command/backend/fleet_cli.py | NEW: Fleet order CLI with Ed25519 signing |
| scripts/fleet-order.sh | NEW: Mac convenience wrapper |

## Commits

- `9dd55cf` — fix: update_daemon handler escapes NixOS sandbox via systemd-run (v0.3.14)
- `ec8633d` — chore: bump Nix daemon derivation to v0.3.14
- `038eac1` — feat: fleet order CLI tool for signed order creation

---

## Next Session

1. Fix ws01 agent — service not running (port 50051 closed). Needs console access or WinRM Basic auth to ws01
2. Fix DC->ws01 Kerberos delegation (0x8009030d SEC_E_LOGON_DENIED)
3. Fix NixOS watchdog marker lifecycle so fleet rebuild orders auto-persist with switch
4. ~~Fleet order CLI tooling~~ — DONE (038eac1)
5. iMac SSH keeps timing out — may need sshd restart again

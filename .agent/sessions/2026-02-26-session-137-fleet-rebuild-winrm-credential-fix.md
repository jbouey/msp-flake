# Session 137 — Fleet Rebuild + WinRM Credential Fix

**Date:** 2026-02-26
**Started:** 13:07
**Previous Session:** 136

---

## Goals

- [x] Deploy resilience hardening via fleet rebuild (sd_notify, state persistence, crash-loop protection)
- [x] Fix WinRM 401 — load Windows credentials from checkin response
- [x] Bump appliance daemon version to 0.3.1
- [x] Verify appliances report v0.3.1 after rebuild — Physical at 0.3.1, VM stuck at 0.2.5 (restart goroutine fails on VirtualBox)
- [x] Verify WinRM connections succeed — DC at 192.168.88.250, GPO configured, AD enumeration running
- [x] Fix DC targeting — domain_admin credential prioritized over workstations (v0.3.2)
- [x] Fix healing order pipeline — runbook_id missing from parameters JSON (v0.3.3)

---

## Progress

### Completed

1. **Fleet order fix** — Cancelled broken `3bf579c6` (status 'pending', wrong params). Created proper `db4b76d5` with correct `flake_ref` and 'active' status.
2. **Resilience hardening deployed** — Both appliances completed fleet order `db4b76d5` (nixos_rebuild). Physical at 0.3.0, VM completing.
3. **Version bump to 0.3.0** — Updated `daemon.go`, `appliance-disk-image.nix`, `appliance-image.nix` (Go source + ldflags + Nix version).
4. **WinRM root cause found** — `runCheckin()` in daemon.go processed LinuxTargets, L2Mode, SubscriptionStatus but completely ignored `WindowsTargets`. DC credentials from Central Command were never loaded into config. Drift scanner and auto-deployer early-exited with nil credentials.
5. **WinRM fix implemented** — Added `loadWindowsTargets()` to extract DC hostname/username/password from first Windows target in checkin response. Config's DomainController/DCUsername/DCPassword now populated dynamically.
6. **Version bump to 0.3.1** — Includes WinRM credential loading fix.
7. **Fleet order for v0.3.1** — Created `42d63738` (active, skip_version=0.3.1, 48h expiry). Both appliances will rebuild.
8. **DC targeting fix (v0.3.2)** — Backend was returning workstation cred (.244) before DC cred (.250). Fixed: backend orders domain_admin first, adds `role` field; Go `loadWindowsTargets()` prefers `role=domain_admin`.
9. **WinRM verified working** — Physical appliance: DC at 192.168.88.250 connected, GPO configured, AD enumeration started against DC.
10. **Healing pipeline fix (v0.3.3)** — 40 failed healing orders/day with "runbook_id is required". Root cause: backend stored runbook_id in DB column but `parameters = {}`. Go daemon's `processOrders()` only extracted `parameters` map. Fixed both: backend embeds runbook_id in parameters JSON; Go injects top-level runbook_id into params map.

### Pending

- Fleet order `de656138` (v0.3.3) active — appliances need to rebuild for healing fix
- VM appliance still at v0.3.1 — needs manual `nixos-rebuild switch`
- `handleHealing` in processor.go is a stub — returns success without executing runbook steps

---

## Files Changed

| File | Change |
|------|--------|
| `appliance/internal/daemon/daemon.go` | Added `loadWindowsTargets()`, bumped Version 0.2.5→0.3.3, added win_targets to checkin log, inject runbook_id into order params |
| `iso/appliance-disk-image.nix` | Version 0.3.3 in buildGoModule + ldflags |
| `iso/appliance-image.nix` | Version 0.3.3 in buildGoModule + ldflags |
| `mcp-server/central-command/backend/sites.py` | Order domain_admin credentials first, add `role` field to windows_targets |
| `mcp-server/main.py` | Embed runbook_id in healing order parameters JSON |

## Commits

| Hash | Description |
|------|-------------|
| `64a632e` | chore: bump appliance daemon to v0.3.0 |
| `a400c4a` | fix: load Windows credentials from checkin response — unblocks WinRM |
| `794f2de` | chore: bump appliance daemon to v0.3.1 — includes WinRM credential loading |
| `bdb7738` | fix: use domain_admin credential as DC, not first workstation (v0.3.2) |
| `ed76a5a` | fix: healing orders include runbook_id in parameters + Go injects top-level (v0.3.3) |

## Fleet Orders

| ID | Type | Skip | Status | Completions |
|----|------|------|--------|-------------|
| `db4b76d5` | nixos_rebuild | — | active | 2/2 (resilience hardening) |
| `697e8deb` | nixos_rebuild | 0.3.0 | cancelled | 1/2 (superseded) |
| `42d63738` | nixos_rebuild | 0.3.1 | active | 2/2 (physical 0.3.1, VM 0.2.5 — restart goroutine failed) |
| `820c91c8` | restart_agent | — | active | sent to force VM restart (also failed) |
| `de656138` | nixos_rebuild | 0.3.3 | active | pending — healing pipeline fix |

---

## Root Cause Analysis: WinRM 401

The WinRM "401" was a misnomer — the daemon never attempted WinRM at all. The credential loading was missing:

```
Central Command → checkin response → windows_targets: [{hostname, username, password, use_ssl}]
                                            ↓
Daemon runCheckin() → ✗ SKIPPED WindowsTargets processing
                                            ↓
config.DomainController = nil → driftScanner.scanWindowsTargets() → early exit
                              → autoDeployer → early exit
```

Fix: Added `loadWindowsTargets()` that extracts the first Windows target as the domain controller and populates `config.DomainController`, `config.DCUsername`, `config.DCPassword`.

---

## Known Issue: VM Self-Restart

The VirtualBox VM appliance can't self-restart via `exec.Command("systemctl", "restart", "appliance-daemon")` from within the daemon goroutine. The `nixos-rebuild test` succeeds (new profile activated) but the daemon restart fails silently. The physical appliance on real hardware doesn't have this issue.

**Workaround:** SSH to VM (`root@192.168.88.254`) and run `systemctl restart appliance-daemon` manually, or reboot the VM.

---

## Root Cause Analysis: Healing Pipeline Failure

48 recurring L1 incidents every 4 hours. L1 matched rules but healing never executed:

```
Backend: INSERT INTO admin_orders ... parameters = '{}', runbook_id = 'RB-AUTO-...'
                                          ↓
Go processOrders(): raw["parameters"] → params map → params["runbook_id"] = "" (missing!)
                                          ↓
handleHealing(): "runbook_id is required" → order failed → incident recurs next cycle
```

Fix (both sides):
1. Backend `main.py`: `"parameters": json.dumps({"runbook_id": runbook_id})` (was `json.dumps({})`)
2. Go `daemon.go`: `processOrders()` injects `raw["runbook_id"]` into params map as fallback

## Final State

- **Physical appliance:** v0.3.2 — DC connected, GPO configured, AD enumeration running (needs v0.3.3 for healing fix)
- **VM appliance:** v0.3.1 — needs `nixos-rebuild switch` (VirtualBox self-restart broken)
- **WinRM:** Working on physical. DC at 192.168.88.250, NTLM auth with NORTHVALLEY\Administrator
- **AD:** Enumeration started against DC, workstation discovery in progress
- **Healing pipeline:** Backend fix deployed via CI/CD. Go fix in v0.3.3 fleet order `de656138` pending rebuild.
- **Known limitation:** `handleHealing` in `processor.go:733` is a stub — returns success without executing runbook steps. Real healing relies on L1 engine during drift scans.

## Session 138 Continuation

### Real Healing Execution (v0.3.4)

11. **Implemented `executeHealingOrder()`** — Healing orders from Central Command now execute runbooks via WinRM/SSH instead of returning a stub success.
    - Daemon registers real handler via `RegisterHandler("healing", ...)` (same pattern as `run_drift`)
    - Looks up runbook from embedded registry → determines platform → dispatches to `executeRunbook()`
    - Falls back to DC hostname for Windows, localhost for Linux when hostname missing
12. **Backend enriched healing order parameters** — Both L1 and L2 code paths now include `hostname` (from `incident.host_id`) and `check_type` in parameters JSON
13. **Fleet order `ecf3c42b`** (v0.3.4) active, `de656138` (v0.3.3) cancelled

### Commits (Session 138)

| Hash | Description |
|------|-------------|
| `d1b4589` | feat: implement real healing order execution — orders now run runbooks via WinRM/SSH (v0.3.4) |

## Next Session

1. Verify fleet order `ecf3c42b` completed — both appliances at v0.3.4
2. VM appliance — manual `nixos-rebuild switch` if fleet order fails (VirtualBox self-restart broken)
3. Monitor healing orders — confirm they succeed and recurring incidents stop
4. Verify workstations discovered via AD enumeration
5. HIPAA compliance push past 56% — workstation data now flowing
6. Fix daemon self-restart on VirtualBox (investigate ProtectSystem/NoNewPrivileges + systemctl)

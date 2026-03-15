# Session 179 — Chaos Lab Healing Gaps

**Date:** 2026-03-14
**Focus:** Address 5 chaos lab healing gaps (43.8% → target improvement)

## Problem

Chaos lab daily report showed 43.8% healing rate with 5 identified gaps:
1. Audit policy modifications not healing (object access, process tracking)
2. Registry persistence (Winlogon key path not covered)
3. Hidden admin accounts only escalated, never auto-removed
4. Firewall inbound rules persist after profile recovery
5. DNS service fails to restart when disabled (not just stopped)

## Changes

### Audit Policy (RB-WIN-SEC-026)
- Expanded subcategory coverage: 6 → 12 (added File System, Registry, Handle Manipulation, Detailed File Share, Process Termination, DPAPI Activity)
- Added `gpupdate /force` before `auditpol /set` to prevent GPO override
- Scanner `driftscan.go` updated to check same 12 subcategories

### Registry Persistence (RB-WIN-SEC-019)
- Added Winlogon key path: `HKLM:\...\Windows NT\CurrentVersion\Winlogon`
- Whitelisted safe entries: Userinit, Shell, AutoRestartShell
- Targets suspicious entries: Taskman, AppSetup, AlternateShell

### Rogue Admin (L1-WIN-ROGUE-ADMIN-001 + RB-WIN-SEC-027)
- Changed L1 rule from `escalate` to `run_windows_runbook`
- New runbook RB-WIN-SEC-027: Remove from Administrators group + Disable account
- Verify phase confirms no rogue admins remain

### Firewall Inbound Rules (new end-to-end)
- Scanner: check #24 — `firewall_dangerous_rules` (any-port rules, risky ports outside safe groups)
- L1 rule: `L1-WIN-FW-RULES-001` → `RB-WIN-SEC-028`
- Runbook: Remove/disable dangerous inbound allow rules, preserve standard Windows services
- Frontend: label added to CHECK_TYPE_LABELS

### DNS Service (RB-WIN-SVC-001)
- Dependency-aware restart (checks RequiredServices first)
- Handles disabled StartType → re-enables to Automatic
- Better error reporting

## Fleet Order

- Cancelled expired `359717f5` (0/2 delivered after 2 days)
- Created `48e4e7f6` — nixos_rebuild, skip v0.3.21, 7-day expiry (Mar 22)
- VM already at v0.3.21 (marked skipped)
- iMac unreachable from Mac (SSH timeout) — can't debug appliance-side fleet order issue

## Test Results

- Go: `go build ./...` clean, `go test ./...` all pass (59 builtin rules)
- TypeScript: `tsc --noEmit` clean
- ESLint: clean

## Files Changed

- `appliance/internal/daemon/driftscan.go`
- `appliance/internal/healing/builtin_rules.go`
- `appliance/internal/daemon/runbooks.json`
- `mcp-server/central-command/frontend/src/types/index.ts`

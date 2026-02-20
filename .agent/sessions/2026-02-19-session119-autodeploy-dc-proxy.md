# Session 119: Autodeploy Hardening + WS01 WinRM Auth Battle

**Date:** 2026-02-19
**Duration:** ~3 hours
**Branch:** main

## Summary

Continued from session 118. Hardened the Go daemon's autodeploy pipeline with failure tracking, WMI fallback, and GPO startup script creation. Battled WS01 WinRM authentication — got port 5985 open but NTLM auth from non-domain machines rejected. DC→WS01 Kerberos also broken (stale machine trust, last logon Jan 11).

## Completed

1. **False-positive deploy fix** (commit 364866f)
   - Empty WinRM stdout was treated as success → now checks for expected output

2. **Drift scanner** (commit 364866f)
   - `driftscan.go`: periodic Windows drift scanning (firewall, Defender, rogue tasks)
   - Runs on configurable interval alongside deploy cycle

3. **Autodeploy error handling** (commit 38176c2)
   - Failure tracking: `map[string]int` tracks consecutive failures per host
   - Escalation: after 3 failures, creates `WIN-DEPLOY-UNREACHABLE` incident via `healIncident()`
   - 4-hour backoff: skips hosts escalated recently
   - WMI fallback (Attempt 5): `Invoke-WmiMethod -Class Win32_Process -Name Create` from DC
   - GPO startup script: auto-creates `Setup-WinRM.ps1` in SYSVOL with `Enable-PSRemoting -Force`
   - Bumps GPO version (AD + GPT.INI) to force client re-download

4. **GPO auto-logon for lab WS01**
   - Set `DisableCAD=1`, `AutoAdminLogon=1`, default credentials via GPO registry
   - WS01 now boots to desktop without Ctrl+Alt+Del

5. **nixos-rebuild on physical appliance**
   - Deployed 38176c2 to physical appliance
   - New daemon running with drift scanner + autodeploy hardening

## WS01 WinRM Status (UNRESOLVED)

**Port 5985:** OPEN (confirmed via nc from Mac and appliance)
**WinRM service:** Responding (HTTP 405 on GET, HTTP 401 on POST = correct)
**Auth advertised:** `Negotiate`, `Kerberos` only — NO `Basic`

### Why it's stuck:
- `Enable-PSRemoting -Force` (from GPO startup script) enables Negotiate+Kerberos only
- Non-domain machines (appliance, Mac) can't do Kerberos → NTLM fallback needed
- WS01 doesn't advertise Basic or accept NTLM from untrusted sources
- DC→WS01 PSSession fails with 0x80090322 (Kerberos SPN/trust error, machine last logon Jan 11)
- DC→WS01 WMI fails with 0x800706BA (RPC server unavailable, port 135 closed)
- Scheduled tasks via `schtasks /S NVWS01` — untested (DC scripts too large, hit HTTP 400)

### What would fix it:
1. **Console access:** Type `winrm set winrm/config/service @{AllowUnencrypted="true"}` and `winrm set winrm/config/service/auth @{Basic="true"}` at the WS01 desktop
2. **Fix machine trust:** `Reset-ComputerMachinePassword` from WS01 (can't do remotely when WinRM auth fails)
3. **Updated GPO startup script:** Modify `Setup-WinRM.ps1` in SYSVOL to also set Basic+AllowUnencrypted, then reboot WS01
4. **schtasks from DC** with a small command (not tested yet due to script size issues)

## Key Lessons

- `Enable-PSRemoting -Force` is NOT enough for non-domain WinRM access — must also enable Basic auth and AllowUnencrypted
- Machine trust breaks when Windows machines don't contact the DC for extended periods
- pywinrm `ntlm` transport requires the server to advertise Basic or Negotiate with NTLM fallback
- Go raw string backticks conflict with PowerShell backtick escapes — use `[char]13 + [char]10` instead

## Files Changed

- `appliance/internal/daemon/autodeploy.go` — failure tracking, WMI fallback, GPO startup script
- `appliance/internal/daemon/driftscan.go` — new file, Windows drift scanner
- `.agent/claude-progress.json` — session 118→119 update

## Commits

- `364866f` feat: zero-friction auto-deploy — spread agent to all AD workstations
- `38176c2` feat: autodeploy error handling — failure tracking, WMI fallback, GPO startup script

# Session 116: GPO Live Integration Test + WS Trust Relationship Fix

**Date:** 2026-02-17
**Status:** COMPLETE

## Commits

No code commits this session — all work was live lab infrastructure testing/repair.

## GPO Integration Test — PASSED

Ran the full GPO deployment pipeline against the live AD domain controller (NVDC01 at 192.168.88.250) from the physical appliance (192.168.88.241). Script: `/tmp/gpo_test.py`

| Step | Test | Result |
|------|------|--------|
| 1 | Verify AD domain | `northvalley.local` confirmed |
| 2 | List existing GPOs | Default Domain Policy + Default DC Policy |
| 3 | SYSVOL read access | Policies + scripts visible |
| 4 | Create OsirisCare dir in SYSVOL | `DIR_OK` |
| 5 | Write test file to SYSVOL | `CONTENT:integration-test` verified |
| 6 | Create test GPO | ID: `ed3e8df4-d63c-4715-b2e3-4f51056afab1` |
| 7 | Link GPO to domain root | `DC=northvalley,DC=local` linked |
| 8 | Verify GPO + link | `AllSettingsEnabled`, `LINK_VERIFIED` |
| 9 | Cleanup | GPO removed, test file cleaned |

**Conclusion:** GPO deployment pipeline code (`gpo_deployment.py`) is validated against real AD infrastructure.

## WS (.251) Trust Relationship Fix — RESOLVED

### Problem
- WS (.251) WinRM port 5985 was open but domain admin creds were rejected
- `Get-LocalGroupMember` returned error 1789 (broken trust relationship)
- Local admin (`localadmin`/`NorthValley2024!`) still worked via NTLM

### Attempts
1. **`Test-ComputerSecureChannel -Repair` via WinRM on WS** — HTTP 400 error (the broken trust itself caused the WinRM session to fail mid-command)
2. **`netdom resetpwd` on WS** — `netdom` not installed on Windows 10 (server tool only)
3. **`Reset-ComputerMachinePassword -Server NVDC01` from DC side** — SUCCESS

### Root Cause
Machine account password out of sync between WS and DC. NTLM auth still works (goes directly to DC for password verification), but Kerberos/machine trust was broken.

### Verification
- `Test-ComputerSecureChannel`: `True`
- Domain admin WinRM session: `NVWS01 / northvalley\administrator`
- `gpresult /scope computer /r`: Working (Member Workstation in `northvalley.local`)

### Key Lesson
When trust relationship is broken, don't try to repair from the broken WS via WinRM — the WinRM session itself becomes unstable. Instead, reset the computer account from the DC side using `Reset-ComputerMachinePassword`.

## NixOS PYTHONPATH Note

After the session 115 nixos-rebuild, all nix store hashes changed. Previous PYTHONPATH strings became invalid. Solved by building PYTHONPATH dynamically:
```bash
for pkg in pywinrm requests-2 charset-normalizer idna urllib3 certifi xmltodict requests-ntlm cryptography cffi pycparser six pyspnego; do
  ls -d /nix/store/*${pkg}*/lib/python*/site-packages 2>/dev/null | head -1
done
```

## Lab Infrastructure Status

| System | IP | Status |
|--------|-----|--------|
| DC (NVDC01) | .250 | Healthy, domain admin auth OK, GPO management OK |
| WS (NVWS01) | .251 | Trust repaired, domain auth OK, GPO applicable |
| Linux (NVLIN01) | .242 | SSH from iMac still times out (pending) |
| Server (NVSRV01) | .252 | Running (not tested this session) |
| Appliance (VM) | .254 | Running, DHCP IP drift from .247 |
| Physical Appliance | .241 | Healthy, used as WinRM relay for all tests |

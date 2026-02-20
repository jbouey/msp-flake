# Session 120: WinRM Recovery + Chaos Lab Safety + VM Rebalance

**Date:** 2026-02-20
**Duration:** ~1 hour
**Focus:** Infrastructure recovery, chaos lab hardening, VM resource management

## Problem

All Windows VMs (DC + WS01) had WinRM auth broken. Port 5985 was open but all authentication was rejected. Root cause traced to chaos lab LLM-generated campaign from Feb 17 (`scn_services_stop_winrm` in `campaigns/2026-02-17.json`) which ran:

```powershell
Stop-Service -Name WinRM -Force; Set-Service -Name WinRM -StartupType Disabled
```

The v2 execution plan only restores snapshots at campaign START, not end. So the DC was left with WinRM dead after that campaign. Everything cascaded from there.

## What Was Done

### 1. WinRM Recovery (DC + WS01)
- User ran `Enable-PSRemoting -Force` on both VMs
- User ran `Set-Item WSMan:\localhost\Service\AllowUnencrypted`, `Auth\Basic`, `Client\TrustedHosts` on both
- Verified: DC works with `.\Administrator` (NTLM + Basic), WS01 works with `localadmin`
- Domain accounts on WS01 still fail (stale machine trust from Jan 11)

### 2. Chaos Lab Safety Patches (on iMac 192.168.88.50)
**`scripts/winrm_attack.py` + `winrm_attack.py`:**
- Added `is_blocked_command()` safety filter with 6 regex patterns
- Blocks: Stop-Service WinRM, Set-Service WinRM Disabled, Disable-PSRemoting, Disable-WSMan, Remove-Item WSMan
- Returns structured error JSON instead of executing

**`scripts/generate_and_plan.py`:**
- Added to LLM prompt: "NEVER target WinRM, PSRemoting, or WSMan services/config"

**`scripts/generate_and_plan_v2.py`:**
- Added `CRITICAL EXCLUSION` block before L2-trigger instruction

### 3. VM Management
- Started `northvalley-linux` and `northvalley-srv01` (were down)
- Rebalanced RAM: DC 10->6GB, SRV01 6->4GB, Appliance 2->6GB (total 24->22GB)
- All 5 VMs running
- User installed VirtualBox Guest Additions on DC + WS01

### 4. VM Appliance Rebuild
- Appliance was down, started it
- Inserted nixos_rebuild admin order (12hr window) for static IP config
- First order failed (appliance was down), inserted fresh one

## Files Changed (on iMac, not in git)
- `/Users/jrelly/chaos-lab/scripts/winrm_attack.py` — safety filter
- `/Users/jrelly/chaos-lab/winrm_attack.py` — safety filter (copy)
- `/Users/jrelly/chaos-lab/scripts/generate_and_plan.py` — prompt exclusion
- `/Users/jrelly/chaos-lab/scripts/generate_and_plan_v2.py` — prompt exclusion

## Current State
- DC: WinRM working (local admin), online
- WS01: WinRM working (localadmin), domain accounts broken (stale trust)
- Linux VM: running at 192.168.88.242
- SRV01: running at 192.168.88.244
- VM Appliance: booted with 6GB RAM, rebuild order pending
- Physical Appliance: online, checking in normally
- Chaos lab: safety filter active, management plane protected

## Next Priorities
1. **VM appliance rebuild** — verify it picks up the rebuild order and gets static IP
2. **WS01 machine trust repair** — `Reset-ComputerMachinePassword -Server NVDC01` from DC
3. **Update VirtualBox snapshots** — DC + WS01 snapshots should capture WinRM fix so future chaos restores don't break management plane
4. **Marketing automation** — user's stated next business priority
5. **New iMac/Mac Mini** — current hardware struggling with 5 VMs

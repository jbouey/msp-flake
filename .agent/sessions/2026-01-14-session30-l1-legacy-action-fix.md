# Session 30 - L1 Legacy Action Mapping Fix

**Date:** 2026-01-14
**Duration:** ~2 hours
**Agent Version:** 1.0.28
**Status:** COMPLETE

---

## Summary

Fixed firewall drift flapping on Central Command Incidents page. Root cause was a missing handler for legacy L1 action names. L1 rules were matching correctly but healing was silently failing because the action type `restore_firewall_baseline` had no handler.

---

## Problem Statement

1. Firewall drift showing as L1 AUTO on Incidents page but not being healed
2. 100+ incidents accumulated with repeating firewall drift
3. L1 rule `L1-FW-001` correctly matching but no healing occurring

---

## Root Cause Analysis

1. L1 rule `L1-FW-001` (in `level1_deterministic.py`) outputs action `restore_firewall_baseline`
2. `appliance_agent.py` only had handlers for:
   - `restart_service`
   - `run_command`
   - `run_windows_runbook`
   - `escalate`
3. **No handler for `restore_firewall_baseline`** - the action fell through silently
4. This is a legacy action name from before the Windows runbook system was implemented

---

## Solution Implemented

Added legacy action to Windows runbook mapping in `appliance_agent.py`:

```python
# Map legacy action names to Windows runbook IDs
legacy_action_runbooks = {
    "restore_firewall_baseline": "RB-WIN-SEC-001",  # Windows Firewall Enable
    "restore_audit_policy": "RB-WIN-SEC-002",       # Audit Policy
    "restore_defender": "RB-WIN-SEC-006",           # Defender Real-time
    "enable_bitlocker": "RB-WIN-SEC-005",           # BitLocker Status
}

# In _execute_healing():
handler = action_handlers.get(action)
if handler:
    return await handler(params, incident)
elif action in legacy_action_runbooks:
    # Translate legacy action to Windows runbook
    runbook_id = legacy_action_runbooks[action]
    logger.info(f"Translating legacy action '{action}' to runbook {runbook_id}")
    runbook_params = {
        **params,
        "runbook_id": runbook_id,
        "phases": ["remediate", "verify"],
    }
    return await self._heal_run_windows_runbook(runbook_params, incident)
else:
    logger.warning(f"Unknown healing action: {action}")
    return {"error": f"Unknown action: {action}"}
```

---

## Files Modified

| File | Change |
|------|--------|
| `packages/compliance-agent/src/compliance_agent/appliance_agent.py` | Added legacy action to runbook mapping |
| `iso/appliance-image.nix` | Version bump 1.0.27 → 1.0.28 |

---

## Deployment Steps

1. Built ISO v1.0.28 on VPS (`nix build .#nixosConfigurations...`)
2. Copied ISO to iMac lab host via MacBook relay (VPS → local → iMac)
3. User updated physical appliance (192.168.88.246) - **verified running v1.0.28**
4. Updated VM appliance (192.168.88.247) - ISO attached, VM rebooted
5. Manually re-enabled Windows DC firewall to stop immediate flapping

---

## Verification

Physical appliance verified running v1.0.28:
```
$ ssh root@192.168.88.246 "ls /nix/store/ | grep compliance-agent"
myhbngbx768b29dzwa3nzsb3yn6mch82-compliance-agent-1.0.28
```

---

## Known Issues

- VM appliance (192.168.88.247) not responding to SSH after reboot - may need more boot time or network adapter check
- Backup drift still shows L3 ESC - this is expected as backup requires manual intervention

---

## Next Steps

1. Verify VM appliance comes online with v1.0.28
2. Monitor Incidents page to confirm firewall flapping stopped
3. Watch for L1 healing events in Central Command dashboard
4. Check Learning page for pattern reporting from healed incidents

---

## Session Context Files Updated

- [x] `.agent/TODO.md` - Added Session 30 section
- [x] `.agent/CONTEXT.md` - Updated version info and added session notes
- [x] `IMPLEMENTATION-STATUS.md` - Added Session 30 notes

---

## ISO Locations

| Version | Location | Purpose |
|---------|----------|---------|
| v1.0.28 | VPS: `/opt/msp-flakes/result-iso/iso/osiriscare-appliance.iso` | Source build |
| v1.0.28 | iMac: `/tmp/osiriscare-appliance-v1.0.28.iso` | VM deployment |
| v1.0.28 | MacBook: `/tmp/osiriscare-appliance-v1.0.28.iso` | Relay copy |

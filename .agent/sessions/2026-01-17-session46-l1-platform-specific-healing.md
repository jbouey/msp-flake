# Session Handoff: 2026-01-17 - L1 Platform-Specific Healing Fix

**Duration:** ~3 hours
**Focus Area:** L1 deterministic healing rules, platform-specific conditions, chaos lab verification

---

## What Was Done

### Completed
- [x] Fixed NixOS firewall drift triggering Windows runbook ("No Windows target available")
- [x] Created L1-NIXOS-FW-001 rule with platform condition for NixOS
- [x] Fixed L1 rules action format from colon-separated to proper action_params
- [x] Fixed Defender runbook ID: RB-WIN-SEC-006 -> RB-WIN-AV-001
- [x] Saved proper L1 rules to codebase (l1_baseline.json)
- [x] Fixed executor.py import: RUNBOOKS (7) -> ALL_RUNBOOKS (27)
- [x] Ran diverse chaos lab attack battery
- [x] Verified L1 healing for firewall and defender attacks
- [x] Git commit: 2d5a9e2

### Not Started (planned but deferred)
- [ ] Add L1 rules for password policy, audit policy attacks - reason: need to create appropriate runbooks first

---

## Key Decisions Made

| Decision | Rationale | Impact |
|----------|-----------|--------|
| Escalate NixOS firewall to L3 | NixOS firewall is declarative, cannot auto-fix | Platform-specific handling |
| Use action_params format | Handler lookup expects action name only | Proper runbook execution |
| Use RB-WIN-AV-001 for Defender | RB-WIN-SEC-006 only in SECURITY_RUNBOOKS | Consistent with ALL_RUNBOOKS |

---

## Files Modified

| File | Change |
|------|--------|
| `/var/lib/msp/rules/l1_rules.json` (appliance) | Platform-specific rules with proper action_params |
| `packages/compliance-agent/src/compliance_agent/rules/l1_baseline.json` | Saved rules to codebase |
| `executor.py` | Changed import to use ALL_RUNBOOKS with lazy import |
| `appliance_agent.py` | Error propagation fix for runbook failures |

---

## Tests Status

```
Total: 811 passed, 7 skipped
New tests added: None
Tests now failing: None
```

---

## Blockers Encountered

| Blocker | Status | Resolution |
|---------|--------|------------|
| Colon-separated action format | Resolved | Changed to action_params format |
| RB-WIN-SEC-006 not found | Resolved | Changed to RB-WIN-AV-001 |
| NixOS firewall triggering Windows runbook | Resolved | Added platform condition |

---

## Next Session Should

### Immediate Priority
1. Add L1 rules for password_policy check type
2. Add L1 rules for audit_policy check type
3. Create RB-WIN-SEC-002 (Audit Policy) if not exists
4. Create RB-WIN-SEC-003 (Password Policy) if not exists

### Context Needed
- Password policy attacks detected but escalated to L3 (no L1 rules)
- Audit policy attacks detected but escalated to L3 (no L1 rules)
- Current L1 rules only cover: NTP, Services, Firewall, Defender, Disk

### Commands to Run First
```bash
# SSH to appliance
ssh root@192.168.88.246

# Check L1 rules
cat /var/lib/msp/rules/l1_rules.json

# View healing logs
journalctl -u osiriscare-agent -f --no-hostname | grep -i "L1\|healing\|runbook"
```

---

## Environment State

**VMs Running:** Yes
**Tests Passing:** 811/818
**Web UI Status:** Working
**Last Commit:** 2d5a9e2 - L1 platform-specific healing rules fix

---

## Chaos Lab Results (Session 46)

### Attacks That Healed Successfully (L1)
| Attack Type | L1 Rule | Runbook | Result |
|-------------|---------|---------|--------|
| Firewall disable | L1-FIREWALL-002 | RB-WIN-FIREWALL-001 | SUCCESS |
| Defender disable | L1-DEFENDER-001 | RB-WIN-AV-001 | SUCCESS |

### Attacks That Escalated to L3 (No L1 Rules)
| Attack Type | Detection | Why No L1 |
|-------------|-----------|-----------|
| Password policy | pass -> fail | No L1 rule for password_policy check type |
| Audit policy | pass -> fail | No L1 rule for audit_policy check type |
| SMB signing | N/A | No check exists |
| NTLM | N/A | No check exists |
| Backdoor user | N/A | No check exists |
| NLA | N/A | No check exists |
| UAC | N/A | No check exists |
| Event logs | N/A | No check exists |

---

## Notes for Future Self

- L1 rules need `actions` and `action_params` as separate fields, not colon-separated
- NixOS platform uses `"platform": "eq": "nixos"` condition to escalate instead of auto-heal
- Priority field controls rule matching order (lower = higher priority)
- ALL_RUNBOOKS (27) is the complete set; RUNBOOKS (7) is just the basic set
- Lazy import in executor.py avoids circular dependency issues

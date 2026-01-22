# Session: 2026-01-22 - Chaos Lab Healing-First & Multi-VM Testing

**Duration:** ~4 hours
**Focus Area:** Chaos lab optimization, clock drift fixes, multi-VM testing

---

## What Was Done

### Completed
- [x] Created EXECUTION_PLAN_v2.sh with healing-first philosophy
- [x] Fixed clock drift on DC (was 8 days behind)
- [x] Fixed WinRM authentication across all 3 VMs
- [x] Changed credential format to local account style (`.\Administrator`)
- [x] Enabled AllowUnencrypted on WS and SRV for Basic auth
- [x] Created FULL_COVERAGE_5X.sh (5-round stress test)
- [x] Ran full coverage test - DC healed 5/5 (100%)
- [x] Created FULL_SPECTRUM_CHAOS.sh (5 attack categories)
- [x] Created NETWORK_COMPLIANCE_SCAN.sh (Vanta/Drata style)
- [x] Updated config.env with SRV and new credentials
- [x] Created CLOCK_DRIFT_FIX.md documentation

### Partially Done
- [ ] WS/SRV healing investigation - identified issue (Go agents not healing)

### Not Started (planned but deferred)
- [ ] Enterprise network scanning architecture - user wants to think on it

---

## Key Decisions Made

| Decision | Rationale | Impact |
|----------|-----------|--------|
| Disable VM restores by default | Restores cause clock drift, defeat purpose of testing healing | Tests now rely on healing to fix issues |
| Use local credential format (`.\`) | Domain format failing due to clock skew | More reliable auth across all VMs |
| Enable Basic auth + AllowUnencrypted | NTLM failing on WS/SRV | Allows time sync commands to work |
| Defer enterprise network scanning | Complex architecture decision | User will decide approach later |

---

## Files Created (on iMac chaos-lab)

| File | Purpose |
|------|---------|
| `~/chaos-lab/EXECUTION_PLAN_v2.sh` | Healing-first chaos testing (ENABLE_RESTORES=false) |
| `~/chaos-lab/FULL_COVERAGE_5X.sh` | 5-round stress test across all VMs |
| `~/chaos-lab/FULL_SPECTRUM_CHAOS.sh` | 5-category attack test |
| `~/chaos-lab/NETWORK_COMPLIANCE_SCAN.sh` | Network compliance scanner |
| `~/chaos-lab/CLOCK_DRIFT_FIX.md` | Clock drift fix documentation |
| `~/chaos-lab/scripts/force_time_sync.sh` | Time sync helper script |

## Files Modified (on iMac chaos-lab)

| File | Change |
|------|--------|
| `~/chaos-lab/config.env` | Added SRV config, changed credential formats, ENABLE_RESTORES=false |

---

## Tests Status

```
Python tests: 834 passed
Go tests: 24 passed
Total: 858 passed

Chaos Lab Results:
- DC Firewall: 5/5 healed (100%)
- WS Firewall: 0/5 healed (Go agent issue)
- SRV Firewall: 0/5 healed (Go agent issue)
```

---

## Blockers Encountered

| Blocker | Status | Resolution |
|---------|--------|------------|
| Clock drift breaking auth | Resolved | Basic auth Set-Date, credential format change |
| NTLM failing on WS/SRV | Resolved | Enabled AllowUnencrypted for Basic auth |
| w32tm resync failing | Resolved | Set-Date manually instead of NTP resync |
| WS/SRV not healing | Open | Go agents running but not healing - needs investigation |

---

## Next Session Should

### Immediate Priority
1. Investigate WS/SRV Go agent healing failure
2. Check Go agent gRPC connection to appliance
3. Add L1 rules for additional attack types (DNS, SMB, persistence)

### Context Needed
- DC healing works via WinRM runbooks from appliance
- WS/SRV have Go agents but aren't healing firewall attacks
- Enterprise network scanning architecture decision pending

### Commands to Run First
```bash
# SSH to iMac
ssh jrelly@192.168.88.50

# Check chaos lab scripts
ls -la ~/chaos-lab/

# Test WinRM to all VMs
cd ~/chaos-lab
source config.env
python3 scripts/winrm_attack.py --host $DC_HOST --user "$DC_USER" --password "$DC_PASS" "hostname"
python3 scripts/winrm_attack.py --host $WS_HOST --user "$WS_USER" --password "$WS_PASS" "hostname"
python3 scripts/winrm_attack.py --host $SRV_HOST --user "$SRV_USER" --password "$SRV_PASS" "hostname"
```

---

## Environment State

**VMs Running:** Yes (DC, WS, SRV all accessible)
**Tests Passing:** 858/858
**Web UI Status:** Working (dashboard.osiriscare.net)
**Last Commit:** Pending (documentation updates)

---

## Notes for Future Self

1. **Clock Drift Pattern:** After VM snapshot restore, always check time sync before running attacks. Use `TIME_SYNC_BEFORE_ATTACK=true` in config.env.

2. **Credential Format:** Use `.\Administrator` (local) instead of `NORTHVALLEY\Administrator` (domain) when auth is failing. Local format bypasses domain controller time check.

3. **WinRM Basic Auth:** WS and SRV needed `Set-Item WSMan:\localhost\Service\AllowUnencrypted -Value $true` to enable Basic auth.

4. **Healing Results:**
   - DC: L1 healing via WinRM runbooks works perfectly (100%)
   - WS/SRV: Go agents running but not healing - likely missing gRPC connection or L1 rules

5. **Enterprise Network Scanning:** User wants appliance-based discovery + scanning. Need to figure out how to keep it "wholesome" (compliance-focused, not offensive).

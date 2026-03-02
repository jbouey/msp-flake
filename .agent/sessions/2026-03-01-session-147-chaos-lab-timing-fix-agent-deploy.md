# Session 147 - Chaos Lab Timing Fix + Agent Deploy

**Date:** 2026-03-01
**Started:** 20:07
**Previous Session:** 146

---

## Goals

- [x] Analyze healing pipeline timing — is HEALING_WAIT_SECONDS sufficient?
- [x] Check if flap detection fires during cumulative campaigns
- [x] Commit chaos lab + agent changes
- [x] Build and deploy agent binary

---

## Progress

### Completed

1. **Healing pipeline timing analysis**
   - Traced full chain: PollInterval(60s) → driftScanInterval(15min) → L1(<100ms) → execute
   - **Root cause of low healing rates (12-31%):** `HEALING_WAIT_SECONDS=720` (12 min) < `driftScanInterval=15min`
   - Chaos lab was verifying BEFORE the appliance even scanned for drift
   - Fixed: bumped to `HEALING_WAIT_SECONDS=1200` (20 min) in config.env on iMac

2. **Flap detection analysis**
   - Confirmed flap detection CANNOT fire during campaigns:
     - Each scenario targets a different category → different cooldown keys
     - `defaultCooldown=10min` < `driftScanInterval=15min` → count always resets to 1
     - Agent gRPC and appliance drift scan use different check_type strings → no cross-source collision
   - Flap detection is structurally unable to reach threshold=3

3. **Committed agent changes** (`13ba93a`)
   - `winrm.go`: WinRM check now verifies Basic auth GPO policy (AllowBasic=1)
   - `executor.go`: healWinRM restores Basic auth + AllowUnencryptedTraffic GPO registry keys

4. **Built + deployed agent binary**
   - `make build-windows-nocgo` → `osiris-agent-nocgo.exe` (12.5MB)
   - SCP'd to `/var/lib/msp/agent/osiris-agent.exe` on appliance (192.168.88.241)
   - Updated VERSION file to `13ba93a`
   - Verified: appliance serves new version at `:8090/agent/version.json`
   - Autodeploy will stage to NETLOGON within 1 hour

### Key Timing Constants (reference)

| Constant | Value | Location |
|----------|-------|----------|
| PollInterval | 60s | daemon/config.go |
| driftScanInterval | 15min | daemon/driftscan.go |
| defaultCooldown | 10min | daemon/daemon.go |
| flapCooldown | 1hr | daemon/daemon.go |
| flapThreshold | 3 | daemon/daemon.go |
| flapWindow | 30min | daemon/daemon.go |
| HEALING_WAIT_SECONDS | 1200 (was 720) | chaos-lab/config.env |

---

## Files Changed

| File | Change |
|------|--------|
| `agent/internal/checks/winrm.go` | Added Basic auth GPO policy verification |
| `agent/internal/healing/executor.go` | healWinRM restores Basic auth + unencrypted GPO keys |
| `chaos-lab/config.env` (iMac) | HEALING_WAIT_SECONDS 720→1200 |

---

## Next Session

1. Check tomorrow's chaos lab results — healing rates should jump with 1200s wait
2. Verify agent autodeploy pushed to NETLOGON
3. Confirm workstation agents updated to `13ba93a` after GPO refresh

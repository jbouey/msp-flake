# Session 134: L2 Planner Enable + Flywheel Promotion Fix

**Date:** 2026-02-25
**Focus:** L2 was completely dead — everything escalated to L3. Fixed three-layer bug in L2 pipeline + broken flywheel promotion aggregation.

## Problem

All non-L1 incidents went straight to L3 email alerts. L2 LLM planner was never called. Learning flywheel had zero promotion candidates despite 912 L2 executions in telemetry.

## Root Causes Found

### L2 Never Called (3 bugs)
1. **config.go:79** — `L2Enabled` defaulted to `false`. Daemon logged `l2=disabled` at startup, `l2Planner` was nil.
2. **main.py:2511** — Backend set `escalate_to_l3 = action == "escalate" or decision.requires_human_review`. Even with valid runbook at 0.75 confidence, `requires_human_review=true` forced escalation.
3. **daemon.go:600** — `ShouldExecute()` required `!RequiresApproval`, blocking auto-mode execution. Auto mode should override this.

### Flywheel Dead (1 bug)
4. **main.py:617** — Flywheel loop Step 2 updated `promotion_eligible` on `aggregated_pattern_stats` rows, but nothing created those rows. Go daemon doesn't call `/api/agent/sync/pattern-stats`. The table was empty for L2 patterns.

## Fixes

| File | Change |
|------|--------|
| `appliance/internal/daemon/config.go:79` | `L2Enabled: false` → `true` |
| `mcp-server/main.py:2511` | `escalate = action == "escalate"` (removed `or requires_human_review`) |
| `appliance/internal/daemon/daemon.go:600` | `canExecute = !EscalateToL3 && Confidence >= 0.6` (ignores RequiresApproval in auto mode) |
| `mcp-server/main.py:617` | Added Step 1: aggregate `execution_telemetry` → `aggregated_pattern_stats` in flywheel loop |

## Deployment

- **Commit 8771d36** — L2 enable + escalation fix → CI/CD deployed to VPS
- **Commit f9cd525** — Flywheel aggregation bridge → CI/CD deployed to VPS
- **Go binary** — Cross-compiled, uploaded to VPS `/var/www/updates/appliance-daemon`
- **Fleet order 7aa80c25** — `nixos_rebuild` active, 48h expiry, both appliances will rebuild
- **DB fix** — Manually marked 2 L2 patterns as promotion_eligible (786 firewall heals, 110 backup heals)

## Verified

- L2 endpoint returns `escalate_to_l3: false` for valid runbooks (was `true`)
- CI/CD both succeeded
- Both appliances checking in (v0.2.5, last checkin <60s ago)
- 37 patterns now promotion-eligible in learning dashboard
- 912 L2 executions in telemetry (100% success rate)

## Next

- Verify appliances pick up fleet rebuild order and restart with `l2=native`
- Monitor L2 decisions on live incidents (should see L2 handling instead of L3)
- WinRM 401 on DC (192.168.88.250) still needs investigation

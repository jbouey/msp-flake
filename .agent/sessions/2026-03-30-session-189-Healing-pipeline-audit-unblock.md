# Session 189 — Healing Pipeline Audit & Unblock

**Date:** 2026-03-30
**Duration:** ~30 min
**Trigger:** Chaos lab daily report showing 50% healing rate ceiling

## Problem

Chaos lab healing rate stuck at 50% across all testing. 6 of 16 attack scenarios consistently failing to heal: firewall, defender, audit, credential_policy, registry_persistence, screenlock/bitlocker.

## Root Causes Found (Production Audit)

### 1. MONITORING_ONLY blocking remediable checks (~25-30% lost)
`main.py` lines 126-149 blocked bitlocker, screen_lock, screen_lock_policy, bitlocker_status, backup_status from ever entering the L1/L2/L3 pipeline. `screen_lock_policy` had 100% L1 success rate (38/38) but was never tried.

### 2. L2 endpoint dead — 404 in production (~10-15% lost)
`/api/agent/l2/plan` defined in `agent_api.py` but router never registered in `main.py`. Daemon getting 404 every ~15 minutes. The old duplicate in main.py was removed (Session 188) but the agent_api router was never wired up.

### 3. Keyword fallback map missing critical types
`security_audit` (3 L3 escalations in 7 days) had no L1 rule match AND no keyword fallback. Same for `defender` and `registry` incident types.

### 4. Broken L1 runbooks (0% success rates)
- LIN-CRYPTO-001: 729 matches, 0 successes
- LIN-PERM-001: 21 matches, 0 successes
- LIN-NTP-001: 8 matches, 0 successes

### 5. Null-pattern synced rules
4 synced/promoted rules had `check_type` but no `incident_type` in their JSON pattern, making them unmatchable by the L1 query.

## Fixes Applied

1. **Removed 5 checks from MONITORING_ONLY** in both `main.py` and `agent_api.py`: bitlocker, bitlocker_status, screen_lock, screen_lock_policy, backup_status
2. **Restored L2 endpoint**: `app.post("/api/agent/l2/plan")(agent_l2_plan_handler)` — delegates to agent_api.py canonical implementation
3. **Added 5 keywords to fallback map**: audit→RB-WIN-SEC-002, defender→RB-WIN-AV-001, registry→RB-WIN-SEC-019, bitlocker→RB-WIN-SEC-005, screen_lock→RB-WIN-SEC-016
4. **Disabled 3 dead rules** in production DB (0% success, 758 combined failures)
5. **Fixed 4 null-pattern rules** in production DB (added incident_type to jsonb pattern)
6. **Resolved 3 stale incidents** to unblock dedup for fresh pipeline flow

## Production Verification

- `bitlocker_status` → L1 healing, `success=True` in telemetry (was blocked)
- `/api/agent/l2/plan` → 200 OK from daemon (was 404)
- `backup_not_configured` → correctly stays monitoring-only
- `device_unreachable` → correctly stays monitoring-only
- Tests: 290 passed, 0 failures

## Files Changed

- `mcp-server/main.py` — MONITORING_ONLY, L2 route, keyword map
- `mcp-server/central-command/backend/agent_api.py` — MONITORING_ONLY sync
- `mcp-server/central-command/backend/tests/test_l2_spend.py` — updated assertions

## Projected Impact

Healing rate: 50% → 70-85% on next chaos lab run.

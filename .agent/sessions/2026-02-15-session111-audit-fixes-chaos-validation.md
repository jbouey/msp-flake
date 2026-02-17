# Session 111: Audit Fixes + Chaos Lab Validation

**Date:** 2026-02-15
**Duration:** ~2 hours
**Version:** v1.0.70 overlay on v1.0.57 NixOS base

## What Was Done

### 1. Runbook ID Mismatch Fix (Migration 046)
- **Problem:** Three incompatible ID namespaces — agent builtins (L1-SVC-DNS-001), promoted rules (RB-AUTO-XXXXXXXX truncated to 8 chars), and counter trigger (045) that never matched builtins. Data flywheel blind to 65% of telemetry.
- **Fix:** Created `046_runbook_id_fix.sql`:
  - ALTER patterns.pattern_signature VARCHAR(64) → VARCHAR(255)
  - Added `source` column to l1_rules (builtin vs promoted)
  - Seeded 51 builtin L1 rule IDs from execution_telemetry
  - Backfilled counters for all 62 rules (12K+ records)
  - Fixed pattern_signature truncation in db_queries.py, learning_api.py, store.py
  - Filtered builtin rules from /api/agent/l1-rules to prevent double-serving
- **Commit:** `e38ba5d`

### 2. Remediation Order Delivery Fix
- **Problem:** complete_order/acknowledge_order endpoints only handled admin_orders table, leaving healing orders stuck permanently.
- **Fix:** Updated sites.py to fall back to `orders` table with JOIN to appliances for site_id. Added auto-expiration of stale orders during polling. Expired 425 stale orders.
- **Commit:** `0ad09ba`

### 3. Flywheel Pattern Generation Fix
- **Problem:** Flywheel promotion pipeline architecturally complete but entry point dead — no patterns being generated from L2 telemetry.
- **Fix:** Added Step 0 to _flywheel_promotion_loop() that generates patterns from L2 execution_telemetry (requires 5+ occurrences). All 56 existing patterns already promoted.
- **Commit:** `d67766f`

### 4. DB Index Cleanup
- Confirmed small tables (sites: 2 rows, runbooks: 51 rows) make seq scans optimal
- Cleaned 2 duplicate indexes

### 5. Chaos Lab Testing
- **Run 2:** 13/16 (81%) — Linux 8/8 HEALED, Windows 5/8
- **Run 3:** 8/16 (50%) — Linux 6/8, Windows 2/8 (degraded due to WinRM timeouts)
- **Observer run:** Launched at end of session

## Files Modified
- `mcp-server/central-command/backend/migrations/046_runbook_id_fix.sql` (NEW)
- `mcp-server/central-command/backend/db_queries.py` (truncation fix)
- `mcp-server/central-command/backend/learning_api.py` (truncation fix)
- `mcp-server/database/store.py` (truncation fix)
- `mcp-server/main.py` (builtin filter + flywheel Step 0)
- `mcp-server/central-command/backend/sites.py` (order completion + auto-expiration)
- `.claude/skills/docs/database/database.md` (migration count update)

## Commits Pushed
- `e38ba5d` — fix: Runbook ID mismatch — seed 51 builtin rules + fix truncation
- `0ad09ba` — fix: Order completion now handles both admin_orders and healing orders
- `d67766f` — feat: Flywheel generates patterns from L2 telemetry + filter fix

## Next Priorities
1. **Windows healing gaps:** DC-DNS never heals (no L1 rule for DNS server address), SRV-Firewall inconsistent, WS-Registry persistence
2. **Promoted rules with 0% success rate:** RB-AUTO-SMB_SIGN, RB-AUTO-KERNEL:K — placeholder runbook IDs that don't map to real runbooks
3. **Agent-side pattern reporting:** Agent should report patterns directly instead of relying on server-side bridge
4. **OTS blockchain anchoring:** bitcoin_block=3 stuck — needs investigation
5. **WinRM reliability:** DC/SRV WinRM timeouts causing healing failures

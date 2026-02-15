# Session 110 - Flywheel Pipeline Fix + Production Audit Fixes

**Date:** 2026-02-15 (continuation)
**Agent Version:** 1.0.70

## Completed

### Migration 045 - Audit Fixes (5 bugs fixed)
- **Evidence_bundles index**: Used `appliance_id` not `site_id` (column didn't exist)
- **patterns.site_id index**: Removed (column doesn't exist in patterns table)
- **l1_rules.incident_type index**: Removed (column doesn't exist, uses `incident_pattern JSONB`)
- **Counter trigger**: Fixed 3 column references:
  - `NEW.rule_id` → `NEW.runbook_id` (execution_telemetry column)
  - `NEW.outcome = 'success'` → `NEW.success` (boolean column)
  - `WHERE runbook_id = ...` → `WHERE rule_id = ...` (agents store rule_id in runbook_id field)
  - Removed `success_rate = ...` SET (GENERATED ALWAYS column — auto-computed)
- **appliance_commands table**: Fixed schema to match learning_api.py:
  - `site_id` → `appliance_id`
  - `payload` → `params`
  - Added unique index for ON CONFLICT clause

### Flywheel Promotion Pipeline Validation
- **Pipeline is WORKING**: 46 patterns auto-promoted to L1 rules
- **9 enabled promoted rules** with real runbook IDs served via `/agent/sync`
- **11 broken rules disabled**: Had `AUTO-*` placeholder runbook IDs (100% failure rate)
- **Counter trigger**: Backfilled 13,131 execution_telemetry records → 5 rules with counters
- **Flywheel scan**: Confirmed real-time promotion (3 patterns promoted within 5 min of crossing threshold)

### Production Database State
- 56 total patterns (10 pending, 46 promoted)
- 20 l1_rules (9 enabled, 11 disabled)
- 13,135 execution_telemetry records (12,221 L1, 8,745 successful)
- Counter trigger active on execution_telemetry INSERT
- appliance_commands table created for deployment pipeline

### Top L1 Rules (by success rate)
| Rule | Matches | Success Rate |
|------|---------|-------------|
| RB-AUTO-FIREWALL | 166 | 100% |
| RB-AUTO-SSH_CONF | 20 | 100% |
| RB-AUTO-AUDIT_PO | 24 | 67% |
| RB-AUTO-BACKUP_S | 260 | 56% |
| RB-AUTO-BITLOCKE | 3,057 | 30% |

### Other Fixes
- **CSP dedup**: Removed Content-Security-Policy from SecurityHeadersMiddleware (Caddy is single source)
- **run_windows_runbook: handler**: Added colon-format handler in appliance_agent.py
- **Flywheel query**: Excludes `AUTO-*` placeholder runbook IDs from auto-promotion

## Commits
- `3bc32d0` fix: Migration 045 audit fixes + CSP dedup + Windows runbook colon handler
- `83b9b76` fix: Migration 045 evidence_bundles index uses appliance_id not site_id
- `46a3db1` fix: Trigger must not SET generated column l1_rules.success_rate
- `6a5271e` fix: Flywheel trigger matches rule_id + exclude AUTO-* placeholders

## Known Issues
- Appliance running v1.0.56 — needs NixOS rebuild for `run_windows_runbook:` handler
- `Unknown action: run_windows_runbook:RB-WIN-SEC-016` errors in agent logs
- RB-AUTO-BITLOCKE at 30% success rate — rule needs investigation/tuning
- Execution telemetry POST blocked by CSRF when called from external scripts

## Next Priorities
1. **Appliance rebuild** — Deploy new agent code with `run_windows_runbook:` handler
2. **BitLocker rule tuning** — Investigate 70% failure rate
3. **Chaos lab full spectrum** — Re-run with cleared flap suppressions + new agent

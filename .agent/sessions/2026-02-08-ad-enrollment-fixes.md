# Session 101: AD Enrollment Fixes + Domain Discovery 500

**Date:** 2026-02-08
**Session:** 101 (continued from 100)

## Summary

Fixed three issues from lab testing of auto-enrollment feature:
1. Domain discovery 500 on Central Command
2. BitLocker "Healing failed: None" misleading log
3. Deployed all fixes to physical appliance via nixos-rebuild

## Changes Made

### 1. Domain Discovery 500 (Central Command)
**File:** `mcp-server/central-command/backend/sites.py`
**Commit:** `ff71bd8`

- **Root cause:** `report_discovered_domain()` JOINed a `partners` table that doesn't exist in the DB schema. Partner info (`contact_email`, `client_contact_email`) lives directly on the `sites` table.
- **Fix:** Removed the `partners` JOIN entirely. `send_critical_alert()` sends to a configured `ALERT_EMAIL` (env var), not a dynamic partner email, so no partner lookup needed.
- Also fixed `send_critical_alert()` call: was using `recipient`/`subject`/`body` params that don't exist. Corrected to `title`/`message`/`site_id`.
- Notification INSERT now always runs (was gated behind failing partner lookup).

### 2. BitLocker Escalation Logging
**File:** `packages/compliance-agent/src/compliance_agent/appliance_agent.py`
**Commit:** `940b035`

- **Root cause:** L1 rule `L1-WIN-BITLOCKER-001` matches `bitlocker_status` with `action: escalate`. The `_try_level1()` returns `None` for escalate actions, falling through to L3 which creates an escalation ticket. L3 returns `HealingResult(success=False, error=None)`. The appliance agent logged this as "Healing failed: None".
- **Fix:** Added `elif getattr(heal_result, 'escalated', False)` check before the failure branch. Now logs "escalated to L3 for human review".
- Fixed in both healing paths (`_attempt_healing` and Windows scanning loop).

### 3. Physical Appliance Deployment
- `nixos-rebuild test --flake github:jbouey/msp-flake#osiriscare-appliance-disk --refresh`
- Agent restarted successfully
- Verified: domain discovery (northvalley.local), AD enumeration (2 servers, 1 workstation), domain report to Central Command (no more 500)

## Also Completed (from previous session context)
- FQDN-to-IP resolution pipeline (`resolve_missing_ips()`, direct TCP tests, skip PHI scrub)
- Auto-enrollment of domain workstations alongside servers
- 23 auto-enrollment tests

## Test Results
- 940 passed, 13 skipped, 0 failures
- CI/CD deploy successful

## Commits
| Hash | Message |
|------|---------|
| `940b035` | fix: Domain discovery 500 + distinguish L3 escalation from healing failure |
| `ff71bd8` | fix: Domain discovery 500 - remove nonexistent partners table JOIN |
| `7737582` | fix: FQDN-IP resolution for AD-discovered machines + skip PHI scrub |
| `05443f8` | feat: Zero-friction auto-enrollment of domain workstations |

## Next Priorities
1. Rotate leaked credentials (task #2 - high priority)
2. WinRM HTTPS configuration for production (currently HTTP plaintext in lab)
3. Re-submit expired OTS proofs (task #10)
4. Credential versioning migration (task #6)

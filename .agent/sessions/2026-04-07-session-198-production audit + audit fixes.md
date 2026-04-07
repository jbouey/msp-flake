# Session 198 - Production Audit + Audit Fixes

**Date:** 2026-04-07
**Commits:** ~4
**Tests:** 65 passing (9 test files)
**Migrations:** 135 deployed

---

## Goals

- [x] Full production security audit (DRY, logging, auth, tests, idempotency, robustness)
- [x] Verify production state on VPS (docker logs, DB state, health endpoint)
- [x] Fix all critical and important findings
- [x] Deploy fixes

---

## Progress

### Production Errors Found (Pre-Existing)
- `go_agents` RLS violation: 120 errors/2h — agent sync without tenant GUC. Pre-existing.
- `AmbiguousParameterError: text vs varchar` in mesh isolation check. Pre-existing.
- `MinIO WORM: Invalid endpoint minio:9000`. Pre-existing.
- None of these are from Session 197 code.

### Audit Findings — Fixed

**3 Critical:**
1. Alert enqueue in agent_api.py reimplemented inline instead of calling module → refactored to use `get_effective_alert_mode()`
2. Unescaped org_name in partner email HTML → all templates now use `html.escape()`
3. `partner_notifications` missing tenant RLS policy → migration 135 added

**6 Important:**
1. Duplicate `_esc()` / `_escape_html()` → replaced with `html.escape()` everywhere
2. Silent `except: pass` in sites.py → `logger.debug()`
3. Digest loop acquires 4 connections → consolidated to 1 per cycle
4. No idempotency on approvals → duplicate returns existing record
5. Critical alert uses hardcoded "Your site" → now fetches real clinic_name
6. Credential entry doesn't validate alert_id org ownership → added check

### Multi-Framework Compliance Verified in Production
- Framework mapping logs show active execution: `mappings=38 frameworks=['hipaa']`
- `client_orgs` columns confirmed: `compliance_framework='hipaa'`, `mfa_required=false`, `audit_retention_days=1095`

---

## Files Changed

| File | Change |
|------|--------|
| agent_api.py | C1: DRY alert enqueue |
| alert_router.py | C2: html.escape(), I1: kill _esc(), I3: connection consolidation, I5: real site name |
| client_portal.py | I4: idempotency guard, I6: alert_id validation |
| email_alerts.py | I1: kill _escape_html() |
| sites.py | I2: log instead of silent pass |
| migrations/135 | C3: partner_notifications tenant RLS policy |

---

## Next Session

1. Add node to 88.x subnet — test mesh target distribution across 3 appliances
2. Key rotation design + implementation (last P0 gap)
3. Fix pre-existing production errors (go_agents RLS, mesh isolation type mismatch, MinIO endpoint)
4. SOC 2 / GLBA assessment template data files
5. Test non-engagement escalation (fires after 48h — monitor)

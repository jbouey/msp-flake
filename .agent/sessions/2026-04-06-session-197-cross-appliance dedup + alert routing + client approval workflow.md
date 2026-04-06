# Session 197 - Cross-Appliance Dedup + Alert Routing + Client Approval Workflow

**Date:** 2026-04-06
**Commits:** 12
**Tests:** 46 passing (35 new, 0 regressions)

---

## Goals

- [x] Cross-appliance incident dedup (hostname-based instead of appliance-scoped)
- [x] Alert routing with PHI-free digest emails to org contacts
- [x] Per-site alert mode (self_service/informed/silent) with org inheritance
- [x] Client portal alerts page with approve/dismiss
- [x] Client approval audit trail (HIPAA accountability)
- [x] Welcome email on first device discovery
- [x] Partner alert config endpoints
- [x] Deploy to production

---

## Progress

### Completed

- Designed full spec with round-table validation (Approach B: dedup first, then alerts)
- Wrote spec doc + implementation plan (14 TDD tasks)
- Executed via subagent-driven development (8 batched tasks)
- Migrations 128-132 deployed (fix: sites.site_id is VARCHAR not UUID)
- alert_digest background task running in production
- alert_email seeded from primary_email for 2 existing orgs

### Blocked

- T640 + T740 still have 0 scan targets (no credentials)
- iMac SSH port 2222 still broken

---

## Files Changed

| File | Change |
|------|--------|
| migrations/128-132 | 5 new SQL files (dedup_key, org alerts, site mode, pending_alerts, client_approvals) |
| agent_api.py | Dedup query: site_id+dedup_key, severity upgrade, alert enqueue |
| alert_router.py | NEW: classify, enqueue, digest render, welcome email, background loop |
| email_alerts.py | send_digest_email() with 3-retry SMTP |
| partners.py | 3 alert-config endpoints (GET/PUT org, PUT site) |
| client_portal.py | GET /alerts + POST /alerts/{id}/action |
| sites.py | client_alert_mode in checkin response |
| main.py | alert_digest background task registered |
| ClientAlerts.tsx | NEW: approve/dismiss UI, severity badges |
| App.tsx + index.ts | Route wiring |
| test_cross_appliance_dedup.py | 4 tests |
| test_alert_router.py | 11 tests |
| test_partner_alert_config.py | 11 tests |
| test_client_alerts.py | 9 tests |

---

## Next Session

1. Test alert flow end-to-end (set alert_email, trigger drift, verify digest)
2. Spec 2: Client self-service approval UX + partner notification tier
3. T640/T740 credentials for scan targets
4. Layer 4 dashboard: per-appliance incident drill-down

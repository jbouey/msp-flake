# Session 124 - Dashboard Audit: 7-Bug Fix + Deploy

**Date:** 2026-02-21
**Started:** 19:55
**Previous Session:** 123

---

## Goals

- [x] Audit and fix 7 live dashboard issues reported by user
- [x] Deploy fixes via CI/CD (push to main)
- [x] Restart appliance-daemon on physical appliance
- [x] Fix WS01 machine trust (reboot after DC-side password reset)

---

## Progress

### Completed

1. **Incidents page 500 crash** — `Severity(i["severity"])` threw `ValueError` on unknown/null DB values. Added `_safe_severity()` and `_safe_resolution_level()` wrappers in routes.py with try/except fallback.

2. **Portal 0% compliance score** — Controls with no data counted as "passing" (inflating pass count while actual score was 0%). Changed to skip no-data controls. Added `LIMIT 500` to `get_control_results_for_site` query (was fetching 128K+ rows unbounded).

3. **Magic link emails not delivered** — Portal used SendGrid (not configured), ignoring available SMTP credentials. Added SMTP fallback in portal.py using SMTP_HOST/SMTP_USER/SMTP_PASSWORD env vars.

4. **No real-time streaming** — Evidence submissions didn't broadcast WebSocket events; checkin events didn't invalidate workstation caches. Added `broadcast_event("compliance_drift", ...)` in evidence_chain.py. Updated useWebSocket.ts to invalidate `workstations`, `goAgents`, and `site` caches.

5. **Email nested details rendering** — `_build_details_section` silently skipped dict/list values. Now renders as formatted JSON in `<pre>` blocks. Fixed `datetime.utcnow()` deprecation.

6. **Portal scope_summary** — Showed "All checks passing" for unmonitored controls. Now shows "No data yet" when `pass_rate is None`.

7. **Infrastructure** — User restarted `appliance-daemon` on physical appliance (pkill + systemctl start). User rebooted WS01 to re-negotiate machine trust after DC-side `Reset-ComputerMachinePassword`.

### Commit

`3bad2e1` — fix: incidents crash, portal 0% score, email delivery, live streaming
Deployed via CI/CD (GitHub Actions run 22267455140, 53s)

### Blocked

None.

---

## Files Changed

| File | Change |
|------|--------|
| `backend/routes.py` | Safe enum converters for Severity/ResolutionLevel |
| `backend/db_queries.py` | Skip no-data controls, LIMIT 500 on bundles |
| `backend/email_alerts.py` | JSON rendering for nested details, datetime fix |
| `backend/evidence_chain.py` | WebSocket broadcast on evidence submission |
| `backend/portal.py` | SMTP fallback, scope_summary "No data yet" |
| `frontend/src/hooks/useWebSocket.ts` | Cache invalidations for workstations/goAgents/sites |
| `.claude/skills/docs/backend/backend.md` | Added safe enum pattern, email_alerts entry |
| `.agent/scripts/context-manager.py` | Fix blocker string vs dict crash |

---

## Key Lessons

- **Safe enum conversion**: Always wrap `Enum(value)` in try/except when sourcing from DB — unknown values crash the endpoint.
- **No-data != passing**: Portal KPIs must distinguish "no data" from "compliant". Counting no-data as passing inflates scores.
- **SMTP fallback**: When multiple email transports exist (SendGrid, SMTP), implement fallback chain rather than hard-requiring one.
- **Appliance service names**: On NixOS appliance, the service is `appliance-daemon` (not `compliance-agent` or `msp-agent`). Filesystem is read-only — can't disable services via symlinks.

---

## Next Session

1. Verify deployed fixes: incidents page loads, portal shows correct %, magic link emails arrive, workstation data refreshes
2. Monitor WS01 machine trust after reboot — confirm fresh scan data flows through
3. Check evidence archive — verify firewall_status spam is reduced with proper WebSocket streaming
4. Consider HIPAA administrative compliance modules (session 122 planning)

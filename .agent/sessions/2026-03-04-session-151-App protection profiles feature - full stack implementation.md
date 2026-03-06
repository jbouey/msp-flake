# Session 151 - App Protection Profiles Feature — Full Stack Implementation

**Date:** 2026-03-04
**Started:** 19:18
**Previous Session:** 150

---

## Goals

- [x] Design application protection profile architecture
- [x] Implement database schema (migration 070)
- [x] Build backend API (13 endpoints)
- [x] Add Go daemon discovery handler
- [x] Add 6 new parameterized runbooks
- [x] Build frontend pages (list + detail)
- [x] Add companion portal read-only section
- [x] Deploy backend + frontend via CI/CD
- [x] Run migration 070 on VPS
- [x] Build and deploy Go daemon v0.3.16

---

## Key Decision

**Auto-generated L1 rules** instead of custom runbook packages. Discovery identifies assets → baseline captures golden state → L1 rules auto-generated with parameters pointing to existing runbooks. Reuses entire existing rule sync and healing infrastructure.

## Progress

### Completed

- Designed full architecture: discovery → baseline → L1 rule generation → sync
- Created migration 070: 4 tables (profiles, assets, rules, templates) + 5 EHR template seeds
- Built 822-line backend router with 13 endpoints (CRUD, discover, lock-baseline, pause/resume)
- Added `app_discovery.go`: PowerShell discovery handler for services, ports, registry, tasks, config files
- Added 6 parameterized runbooks: config integrity, TCP, IIS, ODBC, service recovery, process health
- Built frontend list + detail views with discovery progress, asset toggles, baseline lock flow
- Added companion portal read-only protected apps section
- Committed `51a3963`, CI/CD deployed
- Migration 070 applied on VPS
- Go daemon v0.3.16 built and deployed via fleet order `88f3139e`
- Also included from prior session: autodeploy Kerberos fix + daemon restart sandbox escape (`a4edc3b`)

### Blocked

None

---

## Files Changed

| File | Change |
|------|--------|
| `backend/migrations/070_app_protection_profiles.sql` | NEW — 4 tables + indexes + 5 templates |
| `backend/protection_profiles.py` | NEW — 822-line router, 13 endpoints |
| `appliance/internal/daemon/app_discovery.go` | NEW — PowerShell discovery handler |
| `frontend/src/pages/ProtectionProfiles.tsx` | NEW — list + detail views (530 lines) |
| `mcp-server/main.py` | Router registration + protection_profile rules in sync |
| `backend/sites.py` | discovery_results handling in checkin |
| `appliance/internal/daemon/daemon.go` | Discovery dispatch + results in checkin |
| `appliance/internal/daemon/phonehome.go` | DiscoveryResults field in CheckinRequest |
| `appliance/internal/daemon/runbooks.json` | 6 new parameterized runbooks |
| `frontend/src/App.tsx` | Routes for protection profiles |
| `frontend/src/pages/SiteDetail.tsx` | "App Protection" nav link |
| `frontend/src/utils/api.ts` | Types + protectionProfilesApi (12 methods) |
| `frontend/src/companion/CompanionClientDetail.tsx` | Read-only protected apps section |

---

## Next Session

1. Verify fleet order picked up by appliances (v0.3.16 running)
2. Test discovery flow end-to-end on lab Windows targets
3. Test baseline lock → L1 rule generation → sync → healing loop
4. Seed additional EHR templates based on real-world application configurations

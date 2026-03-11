# Session 171 — Enterprise Blockers: IDOR Sweep, app.is_admin Triage, BAA

**Date:** 2026-03-11
**Previous Session:** 170

---

## Commits

| Hash | Description |
|------|-------------|
| (pending) | security: IDOR checks on 5 learning/onboarding endpoints + shared check_site_access_sa helper + BAA doc |

## Blocker 1: app.is_admin Default — RESOLVED (no code change needed)

### Triage Findings
- `app.is_admin = 'true'` is a **database-level default** (`ALTER DATABASE mcp SET app.is_admin = 'true'`)
- `admin_connection()` does NOT SET LOCAL — relies on DB default
- `tenant_connection(pool, site_id)` explicitly sets `is_admin='false'` per-transaction
- **Migration 081 already attempted the flip → broke all SQLAlchemy endpoints → reverted by 082**
- `routes.py` has 51 `get_db()` (SQLAlchemy) calls — would all return empty with `is_admin='false'`
- `sites` and `appliances` tables have NO RLS policies (only 27 other tables do)

### Architecture Decision
The current `app.is_admin = 'true'` default is **architecturally correct**:
1. Tenant-scoped paths (partner/client/companion) use `tenant_connection()` → forces `is_admin='false'`
2. Admin paths need `is_admin='true'` by design
3. The security boundary is `tenant_connection()` on portal paths + `require_site_access` on admin paths
4. **No code change needed** — the default is NOT a vulnerability

## Blocker 2: routes.py IDOR — COMPLETE

### Changes
- **auth.py**: Added shared `check_site_access_sa()` helper (SQLAlchemy-compatible)
- **runbook_config.py**: Replaced local `_check_site_access` with shared import
- **routes.py**: Added IDOR checks to 5 endpoints:
  - `POST /learning/promote/{pattern_id}` — checks pattern's site_id against org_scope
  - `POST /learning/reject/{pattern_id}` — same
  - `PATCH /onboarding/{client_id}/stage` — checks client_id (site_id) access
  - `PATCH /onboarding/{client_id}/blockers` — same
  - `POST /onboarding/{client_id}/note` — same
- **test_flywheel_promotion.py**: Updated 3 tests to pass `user` param

### Test Results
- Backend: 199 passed, 0 failed
- TypeScript: 0 errors
- ESLint: 0 errors, 14 warnings

## Blocker 3: BAA Sub-Processor Documentation — COMPLETE

- Created `docs/BAA_SUBPROCESSORS.md` (v1.0)
- 9 sub-processors documented with BAA status
- PHI data flow section explaining on-prem architecture
- Technical controls (encryption, RLS, audit, PHI scrubbing)
- Review schedule (quarterly sub-processor, annual BAA audit)

## Next Session Priorities

1. Wire `tenant_connection` into remaining client portal + dashboard endpoints (Phase 4 P2)
2. A/B partition rollback test (lab access required)
3. Add `require_site_access` to remaining routes.py site_id endpoints (fleet, stats, drift-config, compliance-health, devices-at-risk)
4. Swap HTTP rate limiter middleware to Redis version

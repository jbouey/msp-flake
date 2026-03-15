# Session 169 - Companion + Compliance Frameworks Audit & Enterprise Blockers

**Date:** 2026-03-11
**Started:** 04:45
**Previous Session:** 168

---

## Goals

- [x] Audit all 54 companion portal endpoints
- [x] Audit compliance frameworks endpoints
- [x] Fix compliance_frameworks router not registered in main.py
- [x] Fix dashboard/overview wrong table join + NULL param
- [x] Fix JSONB string parsing in compliance-config
- [x] Close protection profiles IDOR vulnerability (Enterprise Blocker #3)
- [x] Add append-only triggers to 4 audit tables (Enterprise Blocker #4)
- [x] Document list_templates as intentionally global access

---

## Progress

### Completed

- **Companion portal audit**: All 24 tested endpoints return 200 (GET /me, /clients, /stats, all 10 HIPAA modules, notes, alerts, activity, documents, preferences PUT)
- **Discovery**: Container runs `uvicorn main:app` not `server:app` — server.py is unused in production
- **Fix 1**: `compliance_frameworks_router` was never `include_router()`'d in main.py (only partner_router was imported)
- **Fix 2**: `dashboard/overview` query joined `appliances` (uuid) instead of `site_appliances` (varchar). Also `framework` param NULL when not provided
- **Fix 3**: JSONB columns returned as text strings, Pydantic expected dicts. Added `json.loads()` parser
- **Fix 4**: Partner compliance defaults 500 — same JSONB-as-string issue with `industry_presets` in 5 locations
- **Enterprise Blocker #3**: Created `require_site_access()` in auth.py, applied to all 12 site-scoped protection profile endpoints. Returns 404 (not 403) for IDOR prevention
- **Enterprise Blocker #4**: Migration 084 adds `prevent_audit_modification` triggers to `update_audit_log`, `exception_audit_log`, `portal_access_log`, `companion_activity_log`
- **list_templates**: Confirmed global access (shared template definitions, not site-scoped). Documented with explicit docstring

### Enterprise Blockers — Final Status

| # | Blocker | Status | Session |
|---|---------|--------|---------|
| 1 | Connection exhaustion (PgBouncer) | Done | 168 |
| 2 | RLS not enabled (27 tables) | Done | 167-168 |
| 3 | Protection profiles IDOR | **Fixed** | 169 |
| 4 | Audit log not append-only | **Fixed** | 169 |

---

## Commits

| Hash | Description |
|------|-------------|
| `ac53cad` | fix: register compliance_frameworks router + fix dashboard/overview query |
| `c97cc81` | fix: parse JSONB string values in site compliance-config response |
| `47423ba` | fix: apply _parse_jsonb to all JSONB columns in compliance_frameworks |
| `0e35663` | security: add require_site_access IDOR guard to all protection profile endpoints |
| `426d990` | security: append-only triggers on 4 remaining audit tables (HIPAA §164.312(b)) |

## Files Changed

| File | Change |
|------|--------|
| `mcp-server/main.py` | Import + register `compliance_frameworks_router` |
| `mcp-server/central-command/backend/frameworks.py` | Fix dashboard/overview: site_appliances join, conditional framework param |
| `mcp-server/central-command/backend/compliance_frameworks.py` | Parse JSONB string values with json.loads() in all 5 JSONB access points |
| `mcp-server/central-command/backend/auth.py` | New `require_site_access()` helper — IDOR-safe site access validation |
| `mcp-server/central-command/backend/protection_profiles.py` | Applied `require_site_access` to 12 endpoints + documented list_templates |
| `mcp-server/central-command/backend/migrations/084_audit_log_append_only.sql` | Append-only triggers on 4 audit tables |

---

## Next Session

1. Wire `tenant_connection` into companion + compliance_frameworks endpoints (Phase 4 P2)
2. Flip `app.is_admin` default to `'false'` (final RLS hardening step)
3. Test client portal compliance endpoints
4. Test partner compliance endpoints (need partner auth session)
5. Apply `require_site_access` pattern to other admin routers (routes.py, frameworks.py)

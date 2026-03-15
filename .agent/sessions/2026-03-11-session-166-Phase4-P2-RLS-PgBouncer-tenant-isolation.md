# Session 166: Phase 4 P2 — RLS Enforcement, PgBouncer, Tenant Isolation

**Date:** 2026-03-11
**Status:** Complete

## What Was Done

### PgBouncer (deployed on VPS)
- `edoburu/pgbouncer:latest` (v1.25.1) in docker-compose
- Transaction pooling mode, SCRAM-SHA-256 auth
- `ignore_startup_parameters = extra_float_digits,statement_timeout`
- `prepared_statement_cache_size=0` via URL param on both SQLAlchemy engines (main.py, server.py) and asyncpg pool (fleet.py)

### RLS on All Remaining Tables (Migrations 078-081)
- **Migration 080**: RLS + FORCE on orders, evidence_bundles, discovered_devices, device_compliance_details, fleet_orders
- Added `site_id` column + backfill + auto-populate triggers on 4 tables
- Fleet_orders: admin-only policy (no site_id by design)
- **Migration 081**: Flipped `app.is_admin` default to `'false'` — fail-closed RLS enforcement

### tenant_connection/admin_connection Wiring
- ~340 `pool.acquire()` calls replaced across 27 backend files
- Site-scoped endpoints use `tenant_connection(pool, site_id=site_id)`
- Admin/auth/portfolio endpoints use `admin_connection(pool)`
- All partner portal, client portal, admin dashboard, companion, and internal modules covered

### Redis Cache Key Tenant Scoping
- Global admin caches prefixed `admin:compliance:all_scores`, `admin:healing:all_metrics`
- Portal sessions already scoped (`portal:{type}:{id}`)
- Rate limiting per-IP (no tenant data)
- OAuth state uses random tokens (no leakage risk)

### Test Fixes
- Added `transaction()` method to `FakeConn` in test_companion.py and test_partner_auth.py
- Fixed 29 test failures caused by admin_connection wrapping queries in conn.transaction()

## Commits
- `78a791e` feat: Phase 4 P2 — PgBouncer, RLS on remaining tables, tenant_connection wiring
- `ee2a218` fix: PgBouncer prepared_statement_cache via URL param + tenant-prefix Redis cache keys
- `5c2f2cb` fix: add transaction() to FakeConn test mocks for RLS tenant_connection

## Remaining Hardening (Future)
- Per-workstation cert revocation (CRL/OCSP)
- Strict protobuf field validation
- Daemon v0.3.21 verification (appliances unreachable over WiFi)

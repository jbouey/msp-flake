# Session 168 — Phase 4 P2: PgBouncer + RLS Remaining Tables

**Date:** 2026-03-10/11
**Previous Session:** 167

---

## Goals

- [x] Migration 080 — RLS on remaining tables (orders, evidence_bundles, discovered_devices, device_compliance_details, fleet_orders)
- [x] Deploy PgBouncer on VPS
- [x] Wire tenant_connection() into partner portal endpoints
- [x] Add statement_cache_size=0 to all connection paths
- [ ] Wire tenant_connection() into client portal + dashboard endpoints
- [ ] Flip app.is_admin default to 'false'
- [ ] Redis cache key scoping

---

## Progress

### Migration 080 — RLS on Remaining Tables
- Added `site_id` column + backfill to: orders (2179), evidence_bundles (2), discovered_devices (54), device_compliance_details (48)
- Auto-populate triggers on all 4 tables (BEFORE INSERT)
- RLS + FORCE + policies on all 5 tables
- `fleet_orders` has admin-only policy (no site_id — fleet-wide by design)
- Total RLS-protected tables: 27
- Verified: non-matching tenant sees 0 rows, admin bypass sees all rows

### PgBouncer Deployed
- Image: `edoburu/pgbouncer:latest` (v1.25.1)
- Auth: `scram-sha-256` (PgBouncer plain passwords → SCRAM exchange with PG)
- Pool mode: `transaction` (compatible with SET LOCAL for RLS)
- `ignore_startup_parameters = extra_float_digits,statement_timeout`
- `statement_cache_size=0` on asyncpg pool (fleet.py) + SQLAlchemy engine (main.py, server.py)
- DATABASE_URL switched: `postgres:5432` → `pgbouncer:6432`
- Health: 12 xacts/s, 14 queries/s, 1.3ms avg, 15μs wait

### Partner Portal — tenant_connection() Wired
10 site-scoped endpoints now use `tenant_connection(pool, site_id=site_id)`:
- get_partner_site_detail, add_site_credentials, validate_credential, delete_credential
- get/update_partner_drift_config, trigger_site_checkin
- list_site_assets, update_asset, trigger_discovery

### Prompt Injection + gRPC Status Check
- **Prompt injection**: Already remediated in l2_planner.py (regex sanitization + untrusted data notice)
- **mTLS**: Implemented for agent↔appliance gRPC (CA + per-agent cert enrollment)
- **Not done**: Per-workstation cert revocation (no CRL/OCSP), strict protobuf field validation

---

## Files Changed

| File | Change |
|------|--------|
| `migrations/080_rls_remaining_tables.sql` | NEW — RLS on 5 remaining tables |
| `backend/fleet.py` | statement_cache_size=0 |
| `backend/partners.py` | tenant_connection() on 10 endpoints |
| `backend/tenant_middleware.py` | (imported, not modified) |
| `mcp-server/main.py` | statement_cache_size=0 |
| `mcp-server/server.py` | statement_cache_size=0 |
| `mcp-server/pgbouncer/pgbouncer.ini` | scram-sha-256, ignore_startup_parameters |
| `mcp-server/pgbouncer/userlist.txt` | Updated for SCRAM auth |
| VPS docker-compose.yml | PgBouncer service + DATABASE_URL → pgbouncer:6432 |

---

## Next Session

1. Wire tenant_connection() into client portal + dashboard endpoints
2. Flip app.is_admin default to 'false' after all endpoints migrated
3. Redis cache key scoping — tenant-prefix keys
4. Per-workstation cert revocation mechanism (CRL/OCSP)
5. Strict protobuf field validation

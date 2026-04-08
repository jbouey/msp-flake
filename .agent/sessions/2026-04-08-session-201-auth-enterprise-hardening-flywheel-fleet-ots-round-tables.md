# Session 201 — Auth Enterprise Hardening + Flywheel/Fleet/OTS Round Tables

**Date:** 2026-04-08
**Commits:** 17
**Daemon Version:** 0.3.84
**Migrations:** 139-143
**Tests Added:** 41 (28 flywheel + 13 RBAC)

## Summary

Three round-table audits (flywheel, fleet orders, OTS/compliance packets) plus auth enterprise hardening and critical production bug fixes. Every audit verified production state on VPS — not just code.

## Auth Enterprise Hardening (6 items)
1. MFA pending tokens → Redis (in-memory fallback)
2. Redis rate limiter (RedisRateLimiter class, was in-memory only)
3. Password history (last 5, prevents reuse, migration 139)
4. Bcrypt cost factor 14 rounds (was 12)
5. API key 1-year expiry on partners (migration 139)
6. Audit log 7-year retention + daily cleanup loop

## Password Change HTTP 500 Fix
- Root cause: FastAPI route ordering — `/{user_id}/password` matched before `/me/password`
- `"me"` parsed as UUID → `invalid UUID 'me': length must be 32..36`
- Fixed by moving `/me/password` route before `/{user_id}/password`

## Login Page Dark Mode Fix
- Labels invisible: CSS variable `--label-primary` was white (dark mode) on fixed light gradient
- Added `.light-login` class to force dark text on light bg login pages

## Human-Readable Error Messages
- Replaced raw `HTTP ${status}` fallback across admin/partner/client portals
- 500 → "Something went wrong...", 403 → "Permission denied", 429 → "Too many requests"

## Flywheel Round Table (9 items)
1. **Go daemon ReloadRules()** — root cause of 21 promoted rules with match_count=0
2. Learning sync verified (push via fleet orders sufficient)
3. promotion_audit_log wired (was 0 rows — table existed but nothing wrote to it)
4. Pattern signature standardized to `incident_type:runbook_id` (removed hostname)
5. Runbook mapping uses DB table as source of truth
6. Telemetry 90-day retention cleanup in flywheel loop
7. l1_rules.source CHECK constraint (migration 140)
8. Dashboard "Promoted Matches (30d)" metric card
9. Test coverage 9→28

## Fleet Order Security Round Table (6 items)
1. **CRIT: Auth added to GET pending orders** (was returning 200 with NO auth — verified in prod)
2. **CRIT: Dangerous order types blocked before server key received** (update_daemon, nixos_rebuild, etc.)
3. **HIGH: github.com removed from binary download allowlist** (too broad)
4. **HIGH: Order completion validates site_id ownership** (was cross-site spoofable)
5. **MED: Nonce TTL reduced 24h → 2h**
6. **MED: Path traversal guard on sync_promoted_rule**

## OTS + Compliance Packet Round Table (5 items)
1. **CRIT: compliance_packets table created** (migration 141) — packets now persisted for HIPAA 6yr retention
2. OTS resubmission loop: backoff instead of hard-exit
3. 100,972 legacy bundles marked `legacy` (Jan 2-10 2026, pre-OTS era)
4. `mark_proof_anchored()` DRY helper (replaces 4 duplicate update paths)
5. Audit logging on proof anchoring (admin_audit_log)

## Evidence Chain Fix
- compliance_bundles ON CONFLICT (bundle_id) broken since migration 138 (partitioning)
- Partitioned tables can't have global unique constraints on non-partition-key columns
- Fixed with DELETE+INSERT upsert pattern
- Evidence submission was 500-ing on every cycle — now 200 OK

## Daemon v0.3.84
- SetRuleReloader(l1Engine.ReloadRules) after sync_promoted_rule
- dangerousOrderTypes map blocks pre-checkin RCE
- github.com removed from download domains
- Nonce TTL 24h → 2h
- Path traversal guard on promoted rule paths
- Deployed to 2/3 appliances via fleet order

## Incident Pipeline Round Table (4 items)
- Dedup race condition fixed: INSERT ON CONFLICT (dedup_key) with partial unique index
- Dead unauthenticated routes/device_sync.py deleted
- Dedup_key unique index + resolve lookup index (migration 142)
- Device sync endpoint confirmed authenticated (auth agent found dead file)

## Portal Auth + Alert Routing + Checkin Round Table (8 items)
- CRIT: Checkin auth_site_id enforced (was using body site_id = site spoofing)
- CRIT: Alert digest crash RETURNING COUNT(*) → conn.execute() (alerts weren't sending)
- CRIT: Undefined variable site_id → checkin.site_id in alert mode resolution
- CRIT: go_agents SQL $6,$6 → NULL,$6 (os_name was getting os_version value)
- Partner/client account lockout: 5 attempts → 15-min (migration 143)
- Pending alerts dedup: unique index on (org_id, incident_id) (migration 143)
- DRY: shared.py session token functions (generate + hash)
- Merkle proof verification endpoint: GET /verify-merkle/{bundle_id}
- Checkin workstation sync: executemany() replaces N+1 loop
- Partner RBAC enforcement: 13 tests
- SSO auto-provision: partner_notification on new viewer user

## Migrations
- 139: password_history, API key expiry, audit log retention index
- 140: l1_rules source CHECK, telemetry index, platform stats index
- 141: compliance_packets table, legacy bundle marking
- 142: incident dedup_key unique index, resolve lookup index
- 143: partner/client lockout columns, pending_alerts dedup index

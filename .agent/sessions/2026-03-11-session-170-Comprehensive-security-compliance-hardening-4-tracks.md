# Session 170 - Comprehensive Security & Compliance Hardening (4 Tracks)

**Date:** 2026-03-11
**Started:** 05:28
**Previous Session:** 169

---

## Commits

| Hash | Description |
|------|-------------|
| `426d990` | security: append-only triggers on 4 remaining audit tables |
| `c321668` | fix: OTS upgrade loop transaction poisoning + proof parser version byte |
| `398f7c1` | security: IDOR sweep + CSRF hardening across 3 routers |
| `5cb2c7b` | ops: auto-run migrations on deploy + fix migrate.py URL parsing |
| `a08374c` | docs: HIPAA risk analysis + breach notification runbook |

## Track Status

### Track 1: Security — COMPLETE
- frameworks.py: 9 endpoints were completely unauthenticated → router-level auth
- compliance_frameworks.py: site endpoints → require_site_access
- runbook_config.py: 3 mutations → _check_site_access
- CSRF: /api/partners/me/ and /api/billing/ exempted (partner session-auth)
- SHA-256 passwords: 0 legacy hashes across all portals
- OTS: savepoints prevent transaction poisoning, version byte parser fixed
- Audit triggers: 6/6 tables now immutable

### Track 2: Operational — PARTIAL
- MinIO: Working (no 502s, evidence submitting 200)
- TLS: Valid until May 16 2026, Caddy auto-renewal confirmed
- A/B rollback: DEFERRED (requires physical lab access to HP T640)

### Track 3: Compliance Docs — COMPLETE
- docs/RISK_ANALYSIS.md: 10 assets with threats, controls, residual risk
- docs/BREACH_NOTIFICATION_RUNBOOK.md: Operational 2am incident response
- BAA sub-processor: No BAA template exists yet (separate task)

### Track 4: Technical Debt — MOSTLY DONE
- Redis rate limiter: Already in use (check_rate_limit uses redis_client.incr)
- Go CGO: Already pure Go (modernc.org/sqlite)
- Migration runner: Wired into CI/CD, URL parsing fixed
- localStorage: Clean (only removeItem cleanup exists)
- HTTP middleware: Still in-memory (single worker, functional)

## Next Session

1. A/B partition rollback test (lab access required)
2. Wire tenant_connection into admin routers (Phase 4 P2)
3. Flip app.is_admin default to 'false'
4. routes.py IDOR: learning/onboarding endpoints need require_site_access
5. Swap HTTP rate limiter middleware to Redis version
6. Create BAA sub-processor template

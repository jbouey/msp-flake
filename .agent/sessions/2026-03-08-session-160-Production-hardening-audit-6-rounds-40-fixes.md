# Session 160 - Production Hardening Audit: 6 Rounds, 40+ Fixes

**Date:** 2026-03-08
**Previous Session:** 159

## Goals

- [x] Systematic production readiness audit (rounds 4-6)
- [x] Fix all onboarding pipeline technical issues (10 items)
- [x] Panic recovery for Go daemon goroutines
- [x] Timing-safe evidence chain verification
- [x] Partner onboarding visibility endpoints

## Rounds Completed

### Round 4: Panic Recovery, Timing Attacks, Input Validation (8 fixes)
- safeGo() wrapper with recover() for all daemon goroutines
- SSH timeout goroutine leak: session.Close() on timeout
- hmac.compare_digest() for 5 evidence chain hash comparisons
- Open redirect fix in OAuthCallback
- ge=1 lower bound on 10 limit query parameters
- All fire-and-forget goroutines converted to safeGo

### Round 5: Webhook Dedup, Input Bounds, JSON Safety (3 fixes)
- Stripe webhook replay protection (stripe_webhook_events dedup table)
- ip_addresses checkin field bounded to max 100 items
- 4 json.loads in cve_watch.py wrapped with try/except

### Round 6: Onboarding Pipeline (10 issues addressed)
1. API key generation on provisioning
2. Credential delivery race fix (compare updated_at vs last_checkin)
3. Credential format mismatch (handle both admin JSON + partner Fernet)
4. Stage transition validation (max 3 forward, 1 backward)
5. Site ID entropy increase (24→48 bits)
6. Discovery credential gate (block if no Windows creds)
7. Stage timestamps on auto-transition
8. Agent registry disk persistence
9. Partner onboarding + trigger-checkin endpoints
10. L1 rules sync confirmed working (audit false positive)

## Commits
- `51aa063` Round 4
- `8cdd6ec` Round 5
- `b1830f3` Round 6a: pipeline fixes
- `5cdcebd` Round 6b: credential delivery, partner endpoints, agent persistence

## Files Changed

| File | Change |
|------|--------|
| daemon/daemon.go | safeGo wrapper, persistent registry |
| grpcserver/registry.go | JSON disk persistence for agent IDs |
| sshexec/executor.go | session.Close on timeout |
| sites.py | credential delivery (both formats), freshness check |
| partners.py | onboarding + trigger-checkin endpoints |
| routes.py | stage validation, query param bounds |
| evidence_chain.py | hmac.compare_digest |
| billing.py | Stripe webhook dedup |
| provisioning.py | API key gen, entropy, timestamps |
| protection_profiles.py | credential gate before discovery |
| OAuthCallback.tsx | open redirect fix |

## Next Session

1. Partner onboarding frontend UI (PartnerOnboarding.tsx component)
2. Credential envelope encryption in checkin responses
3. Certificate pinning on appliance checkin
4. Fleet order signing key rotation mechanism
5. Fleet order delivery state tracking + dead letter queue

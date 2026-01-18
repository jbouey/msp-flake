# Current Tasks & Priorities

**Last Updated:** 2026-01-17 (Session 52 - Security Audit & Healing Tier Toggle)
**Sprint:** Phase 12 - Launch Readiness (Agent v1.0.40, ISO v40, 43 Runbooks, OTS Anchoring, Linux+Windows Support, Windows Sensors, Partner Escalations, RBAC, Multi-Framework, Cloud Integrations, Microsoft Security Integration, L1 JSON Rule Loading, Chaos Lab v2 Multi-VM, Network Compliance Check, Extended Check Types, Workstation Compliance, RMM Comparison Engine, Workstation Discovery Config, $params_Hostname Fix, Go Agent Implementation, VM Network/AD Fix, Zero-Friction Deployment Pipeline, Go Agent Testing, gRPC Stub Implementation, L1 Platform-Specific Healing Fix, Comprehensive Security Runbooks, Go Agent Compliance Checks, Go Agent gRPC Integration Testing, ISO v40 gRPC Working, Active Healing & Chaos Lab v2, FULL COVERAGE L1 Healing Tier, **Security Audit & Healing Tier Toggle**)

---

## Session 52 (2026-01-17) - Security Audit & Healing Tier Toggle

### 1. Healing Tier Toggle (Central Command Integration)
**Status:** COMPLETE
**Details:**
- Created database migration `021_healing_tier.sql` - Added `healing_tier` column to sites table
- Added API endpoints in `sites.py`:
  - `GET /api/sites/{site_id}/healing-tier` - Get current tier
  - `PUT /api/sites/{site_id}/healing-tier` - Update tier (standard/full_coverage)
- Added frontend UI toggle in `SiteDetail.tsx` (toggle switch under Appliances section)
- Added `useUpdateHealingTier` hook in `useFleet.ts`
- Updated `appliance_client.py` to sync tier-specific rules on check-in

### 2. Comprehensive Security Audit
**Status:** COMPLETE
**Details:** Identified 13 critical security vulnerabilities and fixed all of them.

#### Backend Fixes
| File | Issue | Fix |
|------|-------|-----|
| `auth.py` | Weak token hashing (plain SHA256) | HMAC-SHA256 with server secret |
| `auth.py` | Admin password logged on init | Write to secure file, not logs |
| `auth.py` | No password complexity | 12+ chars, upper/lower/digit/special |
| `evidence_chain.py` | Hardcoded MinIO credentials | Required env vars (MINIO_ACCESS_KEY, MINIO_SECRET_KEY) |
| `partners.py` | Weak API key hashing | HMAC with required API_KEY_SECRET env var |
| `partners.py` | Missing magic link POST endpoint | Added POST /auth/magic for secure token validation |
| `portal.py` | Token in URL query param | Added POST /auth/validate and /auth/validate-legacy |
| `server_minimal.py` | CORS wildcard with credentials | Specific origins from CORS_ORIGINS env var |
| `main.py` | No rate limiting | Added RateLimitMiddleware (60/min, 1000/hr, 10/burst) |
| `main.py` | Missing security headers | Added SecurityHeadersMiddleware |
| `users.py` | No password complexity on reset | Added validation to all password endpoints |

#### Frontend Fixes
| File | Issue | Fix |
|------|-------|-----|
| `PortalLogin.tsx` | Open redirect via siteId | Added siteId validation regex |
| `PortalLogin.tsx` | Token exposed in URL | Changed to POST with token in body |
| `IntegrationSetup.tsx` | Open redirect via auth_url | Added OAuth provider whitelist validation |
| `PartnerLogin.tsx` | Token exposed in URL | Changed to POST with token in body |

#### New Files Created
| File | Purpose |
|------|---------|
| `rate_limiter.py` | Sliding window rate limiting middleware |
| `security_headers.py` | CSP, X-Frame-Options, HSTS, X-Content-Type-Options, etc. |
| `021_healing_tier.sql` | Database migration for healing tier column |

### 3. Security Headers Added
**Status:** COMPLETE
**Details:**
- Content-Security-Policy (CSP) - Prevent XSS, clickjacking
- X-Frame-Options: DENY - Prevent clickjacking
- X-Content-Type-Options: nosniff - Prevent MIME sniffing
- X-XSS-Protection: 1; mode=block - Legacy XSS protection
- Strict-Transport-Security (HSTS) - Force HTTPS
- Referrer-Policy: strict-origin-when-cross-origin
- Permissions-Policy - Restrict browser features
- Cross-Origin-Opener-Policy, Cross-Origin-Embedder-Policy, Cross-Origin-Resource-Policy

### 4. Password Complexity Requirements
**Status:** COMPLETE
**Details:**
- Minimum 12 characters
- At least one uppercase letter
- At least one lowercase letter
- At least one digit
- At least one special character (!@#$%^&* etc.)
- Not a commonly breached password
- No 4+ repeating characters
- No 4+ sequential characters (1234, abcd)

### Files Modified This Session
| File | Change |
|------|--------|
| `mcp-server/central-command/backend/auth.py` | Token hashing, credential logging, password validation |
| `mcp-server/central-command/backend/evidence_chain.py` | Removed hardcoded MinIO credentials |
| `mcp-server/central-command/backend/partners.py` | API key hashing, POST magic link endpoint |
| `mcp-server/central-command/backend/portal.py` | POST validation endpoints |
| `mcp-server/central-command/backend/sites.py` | Healing tier endpoints |
| `mcp-server/central-command/backend/users.py` | Password complexity validation |
| `mcp-server/central-command/backend/rate_limiter.py` | NEW - Rate limiting middleware |
| `mcp-server/central-command/backend/security_headers.py` | NEW - Security headers middleware |
| `mcp-server/central-command/backend/migrations/021_healing_tier.sql` | NEW - Healing tier migration |
| `mcp-server/main.py` | Added rate limiting and security headers middleware |
| `mcp-server/server_minimal.py` | Fixed CORS, added security headers |
| `mcp-server/central-command/frontend/src/pages/SiteDetail.tsx` | Healing tier toggle UI |
| `mcp-server/central-command/frontend/src/pages/IntegrationSetup.tsx` | OAuth redirect validation |
| `mcp-server/central-command/frontend/src/portal/PortalLogin.tsx` | Open redirect fix, POST validation |
| `mcp-server/central-command/frontend/src/partner/PartnerLogin.tsx` | POST magic link |
| `mcp-server/central-command/frontend/src/hooks/useFleet.ts` | useUpdateHealingTier hook |
| `mcp-server/central-command/frontend/src/hooks/index.ts` | Export useUpdateHealingTier |
| `mcp-server/central-command/frontend/src/utils/api.ts` | updateHealingTier API function |
| `packages/compliance-agent/src/compliance_agent/appliance_client.py` | Tier-specific rule sync |

### Remaining Tasks
1. **Run tests** - Verify security fixes don't break anything
2. **Build ISO v41** - Include healing tier and security fixes
3. **Deploy to appliances** - Flash ISO v41 to get all fixes
4. **Deploy backend to VPS** - Update production with security fixes
5. **Run database migration** - Execute 021_healing_tier.sql on VPS

---

## Session 51 (2026-01-17) - FULL COVERAGE L1 Healing Tier

### 1. FULL COVERAGE L1 Rules Created
**Status:** COMPLETE
**Details:**
- Created 21 L1 rules covering all check types in `config/l1_rules_full_coverage.json`
- Created 4 core rules in `config/l1_rules_standard.json` (firewall, defender, bitlocker, ntp)
- Deployed 21 rules to appliance (`/var/lib/msp/rules/l1_rules.json`)
- Rules loading verified: "Loaded 21 synced rules from l1_rules.json"

### 2. Alert-to-Runbook Mappings Expanded
**Status:** COMPLETE
**Details:**
- Expanded `appliance_agent.py` with 18 new mappings
- Mappings: audit_policy, password_policy, lockout_policy, screen_lock, smb_signing, ntlm, unauthorized_admin, nla, uac, eventlog_protection, credguard, time_service, dns_client, patches

### 3. Coverage Tiers Defined
**Status:** COMPLETE
**Details:**
| Tier | Rules | Check Types | Use Case |
|------|-------|-------------|----------|
| Standard | 4 | firewall, defender, bitlocker, ntp | Safe, low-risk auto-remediation |
| Full Coverage | 21 | All check types including security policies | Premium upsell, chaos lab validation |

---

## Session 50 (2026-01-17) - Active Healing & Chaos Lab v2

### 1. Chaos Lab v2 Implementation
**Status:** COMPLETE
**Details:**
- Created multi-VM campaign generator (`generate_and_plan_v2.py` on iMac)
- Campaign-level restore instead of per-scenario (21 → 3 restores per run)
- Added workstation (NVWS01 - 192.168.88.251) as second target

### 2. Active Healing Enabled
**Status:** COMPLETE
**Details:**
- Root cause: `HEALING_DRY_RUN=true` prevented learning data collection
- Fixed by setting `healing_dry_run: false` in `/var/lib/msp/config.yaml` on appliance
- Added `healingDryRun` NixOS option to `modules/compliance-agent.nix`

---

## Immediate (Next Session)

### 1. Run Tests
**Status:** PENDING
**Command:**
```bash
cd /Users/dad/Documents/Msp_Flakes/packages/compliance-agent
source venv/bin/activate && python -m pytest tests/ -v --tb=short
```

### 2. Build ISO v41
**Status:** PENDING
**Details:**
- Agent with healing tier support
- Security middleware changes
- Build on VPS: `nix build .#appliance-iso -o result-iso-v41`

### 3. Deploy Security Fixes to VPS
**Status:** PENDING
**Details:**
- Run database migration 021_healing_tier.sql
- Rebuild Docker container with new code
- Set required env vars: `SESSION_TOKEN_SECRET`, `API_KEY_SECRET`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`

---

## Quick Reference

**Run tests:**
```bash
cd /Users/dad/Documents/Msp_Flakes/packages/compliance-agent
source venv/bin/activate
python -m pytest tests/ -v --tb=short
```

**SSH to VPS:**
```bash
ssh root@178.156.162.116
```

**SSH to Physical Appliance:**
```bash
ssh root@192.168.88.246
```

**SSH to iMac Gateway:**
```bash
ssh jrelly@192.168.88.50
```

**Git commit (Session 52):**
```bash
git add -A && git commit -m "feat: Security audit fixes and healing tier toggle (Session 52)"
```

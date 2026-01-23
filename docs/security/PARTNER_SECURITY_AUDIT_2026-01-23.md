# Partner Portal Security Audit Report

**Date:** 2026-01-23
**Auditor:** Claude Opus 4.5 (Session 65)
**Scope:** White Box + Black Box Testing of Partner Portal

---

## Executive Summary

| Severity | Count | Status |
|----------|-------|--------|
| CRITICAL | 1 | ✅ FIXED |
| HIGH | 0 | - |
| MEDIUM | 1 | ⚠️ OPEN |
| LOW | 1 | ⚠️ OPEN |
| INFO | 2 | - |

**Critical Issue Fixed:** Partner admin endpoints had no authentication, allowing anyone to modify OAuth configuration and approve/reject partners.

---

## Critical Findings

### CRIT-001: Unauthenticated Partner Admin Endpoints (FIXED)

**Severity:** CRITICAL
**Status:** ✅ FIXED (commit `3d5c4a1`)
**CVSS:** 9.8 (Critical)

**Description:**
All partner admin endpoints in `partner_auth.py` were accessible without authentication:
- `GET /api/admin/partners/pending`
- `POST /api/admin/partners/approve/{partner_id}`
- `POST /api/admin/partners/reject/{partner_id}`
- `GET /api/admin/partners/oauth-config`
- `PUT /api/admin/partners/oauth-config`

**Attack Scenario:**
1. Attacker reads `/api/admin/partners/oauth-config` to see domain whitelist
2. Attacker modifies config to add `attacker-owned.com` to whitelist
3. Attacker sets `require_approval: false`
4. Attacker performs OAuth login from their domain
5. Attacker gets immediate partner access without approval

**Proof of Concept:**
```bash
# Read config (worked without auth)
curl https://api.osiriscare.net/api/admin/partners/oauth-config
# {"allowed_domains":["testpartner.com"],"require_approval":true,...}

# Modify config (worked without auth!)
curl -X PUT -H "Content-Type: application/json" \
  -d '{"allowed_domains":["attacker.com"],"require_approval":false}' \
  https://api.osiriscare.net/api/admin/partners/oauth-config
# {"status":"updated"}
```

**Fix Applied:**
Added `require_admin` dependency to all admin endpoints:
```python
from .auth import require_admin

@admin_router.get("/pending")
async def list_pending_partners(request: Request, user: Dict = Depends(require_admin)):
    ...
```

**Verification:**
All endpoints now return 401 Unauthorized without valid admin session.

---

## Medium Findings

### MED-001: No Rate Limiting on Partner Endpoints

**Severity:** MEDIUM
**Status:** ⚠️ OPEN
**CVSS:** 5.3

**Description:**
Partner endpoints lack rate limiting, allowing:
- Brute force attacks on API keys
- DoS via resource exhaustion
- Enumeration attacks

**Evidence:**
```bash
# 15 requests in rapid succession - all returned 401, no 429
for i in {1..15}; do
  curl -s -o /dev/null -w "%{http_code} " https://api.osiriscare.net/api/partners/me
done
# 401 401 401 401 401 401 401 401 401 401 401 401 401 401 401
```

**Recommendation:**
- Implement rate limiting middleware
- 10 requests/minute for auth endpoints
- 100 requests/minute for authenticated endpoints
- Return 429 Too Many Requests when exceeded

---

## Low Findings

### LOW-001: Verbose Error Messages

**Severity:** LOW
**Status:** ⚠️ OPEN

**Description:**
Some error responses provide detailed information that could aid attackers:
- "Partner is not pending approval" reveals partner exists
- "Invalid provision code" vs "Provision code expired" reveals state

**Recommendation:**
Use generic error messages for security-sensitive operations.

---

## Positive Findings (Good Practices)

### ✅ SQL Injection Protection
All database queries use parameterized queries (`$1`, `$2`):
```python
await conn.fetchrow("""
    SELECT id, name FROM partners WHERE api_key_hash = $1
""", key_hash)
```

### ✅ Security Headers Present
```
strict-transport-security: max-age=31536000; includeSubDomains; preload
x-frame-options: DENY
x-content-type-options: nosniff
content-security-policy: default-src 'self'; ...
```

### ✅ CORS Origin Filtering
- Only `https://dashboard.osiriscare.net` allowed
- Credentials not exposed to other origins

### ✅ PKCE OAuth Implementation
- S256 code challenge used
- Single-use state tokens with 10-minute TTL
- State stored in database, not client-side

### ✅ Session Token Security
- Tokens hashed with SHA-256 before storage
- HttpOnly, Secure, SameSite=Lax cookies
- 7-day expiration

### ✅ API Key Security
- HMAC-SHA256 with server secret
- Constant-time comparison (`secrets.compare_digest`)
- Keys not logged or exposed in URLs

### ✅ Magic Link Token Handling
- Token sent in POST body, not URL
- Prevents exposure in server logs and browser history

---

## Test Coverage

### White Box Testing
| Component | Files Reviewed | Status |
|-----------|----------------|--------|
| `partner_auth.py` | OAuth flow, session management | ✅ |
| `partners.py` | API key auth, provision claiming | ✅ |
| `PartnerLogin.tsx` | Frontend OAuth flow | ✅ |
| `PartnerDashboard.tsx` | Session handling | ✅ |

### Black Box Testing
| Test | Result |
|------|--------|
| Unauthenticated access to /api/partners/me | ✅ 401 |
| Unauthenticated access to /api/partners/me/sites | ✅ 401 |
| Unauthenticated access to admin endpoints | ❌ Was 200, now ✅ 401 |
| SQL injection in API key | ✅ Protected |
| SQL injection in provision code | ✅ Protected |
| XSS in provision code | ✅ Protected (JSON response) |
| Rate limiting | ⚠️ Not implemented |
| CORS from evil.com | ✅ Blocked |
| Security headers | ✅ Present |

---

## Remediation Timeline

| Issue | Severity | Status | Fix Date |
|-------|----------|--------|----------|
| CRIT-001 Admin auth | CRITICAL | ✅ FIXED | 2026-01-23 |
| MED-001 Rate limiting | MEDIUM | ⚠️ TODO | - |
| LOW-001 Verbose errors | LOW | ⚠️ TODO | - |

---

## Files Modified

| File | Change |
|------|--------|
| `mcp-server/central-command/backend/partner_auth.py` | Added require_admin to admin endpoints |

## Git Commits

| Commit | Description |
|--------|-------------|
| `3d5c4a1` | security(CRITICAL): Add authentication to partner admin endpoints |

---

**Audit Complete**

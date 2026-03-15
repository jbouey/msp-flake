# Session 157 - Portal Auth Hardening, 2FA, User Management

**Date:** 2026-03-07
**Commits:** cbf20aa, ce89837, 47e39ec, d2b94c5, c785181

---

## Completed

1. Email/password login for partner + client portals (tabbed UI, default tab)
2. Security audit: 4 critical, 7 high, 3 medium fixes (rate limiting, HMAC sessions, bcrypt-only, open redirect, CSRF narrowing, hashed magic tokens)
3. TOTP 2FA for all 3 portals (admin, partner, client) — shared totp.py, MFA pending flow, backup codes
4. Admin Users page: 5 tabs (Users, Invites, Sessions, Audit Log, Security/2FA) + Change Password + email edit

## Files Changed

| File | Change |
|------|--------|
| backend/totp.py | NEW — shared TOTP module |
| backend/migrations/071, 072 | NEW — password_hash, mfa columns |
| backend/partner_auth.py | Email login, TOTP, MFA flow, session_router |
| backend/client_portal.py | TOTP, hashed magic tokens, HMAC, bcrypt-only |
| backend/auth.py | MFA pending login flow |
| backend/users.py | Email update, session mgmt, TOTP endpoints |
| backend/routes.py | verify-totp endpoint |
| backend/rate_limiter.py | Auth endpoint coverage expanded |
| backend/csrf.py | Narrowed exemptions |
| frontend/src/pages/Users.tsx | 5 tabs, change password, 2FA setup |
| frontend/src/utils/api.ts | Session/TOTP/audit API methods |
| frontend/src/partner/PartnerLogin.tsx | Email/password form + TOTP login flow |
| frontend/src/client/ClientLogin.tsx | Email/password form + TOTP login flow |
| frontend/src/partner/PartnerSecurity.tsx | NEW — partner 2FA settings page |
| frontend/src/client/ClientSecurity.tsx | NEW — client 2FA settings page |
| frontend/src/App.tsx | Added /partner/security + /client/security routes |
| frontend/src/partner/PartnerDashboard.tsx | Security nav link |
| frontend/src/client/ClientSettings.tsx | Security (2FA) nav link |
| frontend/src/partner/index.ts | PartnerSecurity export |
| frontend/src/client/index.ts | ClientSecurity export |
| mcp-server/Dockerfile | pyotp dependency |
| mcp-server/main.py | partner session_router registration |

## Tests
- Backend: 199 passed
- Frontend: 89 passed, TypeScript clean

# Session 158: Server-side Pagination, Dark Mode, Auth Fixes

**Date:** 2026-03-08
**Duration:** ~2 hours (continued from prior context)

## What Was Done

### 1. Partners Page Auth Fix (Critical)
- **Root cause:** `AuthContext.tsx` clears `localStorage.auth_token` on load — app uses cookie-based session auth, not Bearer tokens
- `getToken()` always returned null → `fetchPartners` returned early → `isLoading` stuck at `true` → infinite spinner
- **Fix:** Replaced all Bearer token auth with `credentials: 'same-origin'` (cookie auth) + CSRF tokens for mutations
- Affected all 15+ fetch functions in Partners.tsx

### 2. Partner Detail Crash Fix
- `GET /api/partners/{id}` returned 500: `column "site_id" does not exist` in incidents query
- `incidents` table has no `site_id` column — must join through `site_appliances`
- Fixed query to JOIN via `site_appliances.id = incidents.appliance_id`

### 3. View Logs Order Type Mismatch
- Frontend sent `view_logs` but backend `OrderType` enum only has `collect_logs`
- Synced all 16 backend order types to frontend `OrderType` type

### 4. Sites List Limit Validation
- Backend limited `limit` query param to 100, but Incidents page site dropdown sends `limit=200`
- Raised to 500 (admin-only endpoint)

### 5. Dark Mode
- CSS custom properties for all theme colors (light/dark)
- `useTheme` hook: reads localStorage, respects system preference, real-time media query listener
- Three modes: System / Light / Dark in Settings > Display
- iOS dark palette: pure black background, dark glass surfaces, inverted labels

### 6. Settings Page Auth Fix
- Same Bearer token bug as Partners — fixed to cookie auth + CSRF

### 7. Healing Tier Labels
- Removed hardcoded rule counts "(4 rules)" / "(21 rules)" from SiteDetail dropdown
- Now just shows "Standard" and "Full Coverage"

### 8. Users All Accounts Fix (from prior context)
- Removed `mfa_enabled` from `client_users` query — column doesn't exist on VPS (migration 072 never ran)

## Commits
- `6ecd586` fix: use array index instead of .at() for CI TS target compat
- `b0bd2e7` perf: fix Partners double-fetch on mount, use cookie auth
- `a6b1233` fix: remove mfa_enabled from client_users query
- `35bdd61` fix: restore Bearer auth in Partners fetchPartners (wrong fix)
- `3d808c9` fix: Partners page auth — use cookie auth (correct fix)
- `f5351e9` feat: dark mode with iOS-style theme + fix Settings auth
- `d5221cc` fix: View Logs order type mismatch + sites limit validation
- `1eacb89` fix: partner detail crash — incidents table has no site_id column

## Key Lesson
**Dashboard auth is cookie-based, not Bearer token.** `AuthContext.tsx` actively clears `auth_token` from localStorage. All manual `fetch()` calls must use `credentials: 'same-origin'` (never Bearer headers). The `api.ts` `fetchApi` utility already does this correctly — pages that bypass it (Partners, Settings) need to match the pattern. Mutations need `X-CSRF-Token` header from the `csrf_token` cookie.

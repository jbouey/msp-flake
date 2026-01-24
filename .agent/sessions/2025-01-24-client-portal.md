# Session 68: Client Portal Implementation

**Date:** 2025-01-24
**Duration:** ~1 hour
**Focus:** Direct healthcare practice access portal

## Summary

Implemented complete client portal giving healthcare practices direct access to their HIPAA compliance data. This creates platform leverage by owning the client relationship independent of MSP partners.

## Completed

### 1. Database Migration (029_client_portal.sql)
- `client_orgs` - Healthcare practice entity
- `client_users` - Portal users with magic link auth
- `client_sessions` - Cookie-based sessions (30-day)
- `client_notifications` - Direct alerts from OsirisCare
- `client_invites` - User invitation tokens
- `partner_transfer_requests` - MSP transfer workflow
- `client_monthly_reports` - Cached report summaries
- Added `client_org_id` column to `sites` table

### 2. Backend API (client_portal.py)
- **Auth:** Magic link login, session cookies, optional password
- **Dashboard:** KPIs, sites list, compliance scores
- **Evidence:** List, detail, download, verify hash chain
- **Reports:** Monthly PDF downloads
- **Notifications:** List, mark read
- **User Management:** Invite, remove, change roles (RBAC)
- **Partner Transfer:** Request, status, cancel (Phase 3)

### 3. Frontend (src/client/)
- `ClientContext.tsx` - Auth context with session management
- `ClientLogin.tsx` - Magic link request form
- `ClientVerify.tsx` - Token validation page
- `ClientDashboard.tsx` - Main dashboard with KPIs and sites
- `ClientEvidence.tsx` - Evidence bundle list with filters
- Routes added to App.tsx

### 4. VPS Deployment
- Copied files to `/opt/mcp-server/dashboard_api_mount/`
- Ran migration on PostgreSQL
- Restarted mcp-server container
- Verified endpoints working via public URL

## Technical Notes

### Auth Flow
1. User enters email at `/client/login`
2. Backend sends magic link (60-min expiry)
3. Click link → `/client/verify?token=xxx`
4. Frontend POSTs token to validate
5. Backend creates session, sets httpOnly cookie
6. Redirect to `/client/dashboard`

### Security
- Session tokens: HMAC-SHA256 hashed
- Magic links: 60-min expiry, single-use, POST body only
- Cookies: httpOnly, Secure, SameSite=Lax
- RBAC: owner > admin > viewer (server-side enforced)

### VPS Volume Mount
The docker-compose uses a volume mount that overrides the built-in code:
```yaml
/opt/mcp-server/dashboard_api_mount:/app/dashboard_api:ro
```
New backend files must be copied to `dashboard_api_mount/` not just `app/dashboard_api/`.

## Files Changed

### Created
- `mcp-server/central-command/backend/migrations/029_client_portal.sql`
- `mcp-server/central-command/backend/client_portal.py`
- `mcp-server/central-command/frontend/src/client/ClientContext.tsx`
- `mcp-server/central-command/frontend/src/client/ClientLogin.tsx`
- `mcp-server/central-command/frontend/src/client/ClientVerify.tsx`
- `mcp-server/central-command/frontend/src/client/ClientDashboard.tsx`
- `mcp-server/central-command/frontend/src/client/ClientEvidence.tsx`
- `mcp-server/central-command/frontend/src/client/index.ts`

### Modified
- `mcp-server/server.py` - Added client portal router imports
- `mcp-server/central-command/frontend/src/App.tsx` - Added /client/* routes

## Commits
- `cf53170` - feat: Add client portal for direct healthcare practice access

## Access
- URL: https://dashboard.osiriscare.net/client/login
- Backend: /api/client/auth/* and /api/client/*

## Next Steps
1. Create initial client_orgs and client_users in database
2. Configure SendGrid for magic link emails
3. Build frontend for reports page
4. Test end-to-end magic link flow
5. Set up notification triggers from compliance events

## Related
- Plan file: `/Users/dad/.claude/plans/keen-honking-tiger.md`
- Evidence fix from Session 67 (signature validation)

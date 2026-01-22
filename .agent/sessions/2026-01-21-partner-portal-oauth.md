# Session 57 - Partner Portal OAuth + ISO v44 Deployment

**Date:** 2026-01-21/22
**Status:** COMPLETE
**Phase:** 13 (Zero-Touch Update System)

---

## Summary

Fixed Partner Portal OAuth authentication flow, enabling partners to sign in via Google Workspace or Microsoft Entra ID. Added admin UI for viewing and approving pending partner signups. Added domain whitelisting config UI. Deployed ISO v44 to physical appliance and verified A/B partition system working.

---

## Problems Solved

### 1. Email Notification Import Error
**Error:** `cannot import name 'send_email' from 'dashboard_api.notifications'`

**Root Cause:** The `partner_auth.py` was importing from a non-existent `notifications` module.

**Fix:** Changed import to use existing `email_alerts.send_critical_alert` function.

```python
# Before (broken)
from .notifications import send_email

# After (working)
from .email_alerts import send_critical_alert
```

### 2. Partner Dashboard Spinning Forever
**Symptom:** `/partner/dashboard` showing loading spinner indefinitely for OAuth users.

**Root Cause:** `PartnerDashboard.tsx` had dependency on `apiKey` variable, but OAuth users have session cookies instead of API keys. The condition `if (isAuthenticated && apiKey)` never evaluated to true for OAuth users.

**Fix:** Changed dependency from `apiKey` to `isAuthenticated` and added dual-auth support for API calls.

```typescript
// Before (broken)
useEffect(() => {
  if (isAuthenticated && apiKey) {
    loadData();
  }
}, [isAuthenticated, apiKey]);

// After (working)
useEffect(() => {
  if (isAuthenticated) {
    loadData();
  }
}, [isAuthenticated]);
```

### 3. Backend require_partner() Only Supported API Key
**Symptom:** OAuth-authenticated partners got 401 errors on partner API endpoints.

**Root Cause:** `require_partner()` function in `partners.py` only checked for `X-API-Key` header.

**Fix:** Added support for `osiris_partner_session` cookie as fallback authentication.

```python
async def require_partner(
    x_api_key: str = Header(None),
    osiris_partner_session: Optional[str] = Cookie(None)
):
    # Try API key first
    if x_api_key:
        partner = await get_partner_from_api_key(x_api_key)
        if partner:
            return partner

    # Try session cookie
    if osiris_partner_session:
        session_hash = hashlib.sha256(osiris_partner_session.encode()).hexdigest()
        # Look up session in partner_sessions table
        ...
```

---

## Files Modified

| File | Changes |
|------|---------|
| `mcp-server/central-command/backend/partner_auth.py` | Fixed email notification import |
| `mcp-server/central-command/backend/partners.py` | Added session cookie support to require_partner() |
| `mcp-server/central-command/frontend/src/pages/Partners.tsx` | Added pending approvals UI |
| `mcp-server/central-command/frontend/src/partner/PartnerDashboard.tsx` | Fixed OAuth session support |
| VPS `main.py` | Added partner_admin_router registration |

---

## VPS Deployment

Changes deployed via bind mount at `/opt/mcp-server/dashboard_api_mount`:

1. Copied `partner_auth.py` to bind mount
2. Copied `partners.py` to bind mount
3. Added `partner_admin_router` to `main.py`
4. Restarted mcp-server container

Frontend deployed via container rebuild.

---

## Testing Verification

1. **Google OAuth login:** Working - partners can sign in via Google Workspace
2. **Microsoft OAuth login:** Working - partners can sign in via Microsoft Entra
3. **Partner Dashboard:** Loading properly for OAuth-authenticated users
4. **Admin pending approvals:** UI shows pending partners with approve/reject buttons
5. **Email notification:** Sends via existing L3 alert infrastructure

---

## Related Migrations

- `025_oauth_state.sql` - OAuth PKCE state storage
- `026_partner_approval.sql` - Partner approval workflow (pending_approval, approved_by, approved_at)

---

## ISO v44 Deployment

### Physical Appliance (192.168.88.246)

Deployed ISO v44 to physical appliance via USB flash.

**Verification:**
```
=== Health Gate ===
Active partition: A
Boot attempts: 0/3
No pending update

=== Update Agent ===
Active partition: A
Standby partition: B
Active device: /dev/sda2
Standby device: /dev/sda3
Boot count: 0

=== Agent Service ===
compliance-agent.service - OsirisCare Compliance Agent
Active: active (running)
```

The appliance is now ready for zero-touch remote updates via Fleet Updates.

---

## Domain Whitelisting Config UI

Added "Partner OAuth Settings" section to Partners.tsx:
- Configure whitelisted domains for auto-approval
- Toggle approval requirement
- Shows current whitelist status
- Uses `/api/admin/partners/oauth-config` endpoint

---

## Next Steps

1. Test remote ISO update via Fleet Updates (push v45 to appliance)
2. Test partner signup with domain whitelisting (auto-approve vs manual)
3. Deploy Go Agent to additional workstations

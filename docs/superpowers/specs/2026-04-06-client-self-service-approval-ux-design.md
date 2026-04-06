# Client Self-Service Approval UX (Spec 2)

**Date:** 2026-04-06
**Scope:** Spec 2 of 2 — Multi-Appliance Maturity
**Sessions:** Est. 1-2 implementation sessions

## Problem

Spec 1 sends PHI-free digest emails and has basic approve/dismiss. But:
1. Unacted alerts accumulate silently — no escalation to partner
2. "Enter credentials" alert has no self-service form — client must call MSP
3. Partners get no automated org-health notifications
4. Approvals exist in DB but don't appear in compliance packets

## Components

### 1. Client Non-Engagement Escalation (P0)

**Trigger:** Pending alerts sent to client but unacted after 48 hours.

**Flow:**
```
Alert sent to client → 48h no action → partner notified
  → 72h partner also ignores → escalate to admin L4 queue
```

**Background check:** Runs in digest_sender_loop, queries pending_alerts where `sent_at IS NOT NULL AND dismissed_at IS NULL AND created_at < NOW() - INTERVAL '48 hours'`. Groups by org, creates partner_notifications, sends partner email. Dedup: don't re-escalate same org within 7 days.

**Configurable:** `NON_ENGAGEMENT_HOURS` env var, default 48.

### 2. Guided Credential Entry (P0)

**Flow:** Client clicks "Enter Credentials" on alert → modal with type selection → form → POST → stored encrypted → next checkin delivers to appliance.

**Credential types:**
- Windows Domain: domain, username, password
- Windows Local: username, password
- SSH Key: username, private key (paste), passphrase (optional)
- SSH Password: username, password

**Endpoint:** `POST /api/client/credentials`
```json
{
  "site_id": "string",
  "credential_type": "winrm | domain_admin | ssh_key | ssh_password",
  "credential_name": "string",
  "data": {
    "username": "string",
    "password": "string (optional for ssh_key)",
    "domain": "string (optional, winrm/domain_admin only)",
    "private_key": "string (optional, ssh_key only)",
    "passphrase": "string (optional, ssh_key only)"
  }
}
```

**Security:**
- HTTPS transit only — no browser-side encryption needed (same trust as partner portal)
- Fernet encryption server-side before DB write (existing pattern)
- Never in browser storage, never logged
- RLS enforced: client can only write to their org's sites
- Rate-limited: 10 submissions per hour per org
- Audit: `client_approvals` record with action `credentials_entered`

**Frontend:** `CredentialEntryModal.tsx` — 3 steps: type select → form → confirmation.

### 3. Partner Notification Tier (P1)

**Extends alert_router.py** with partner-tier classifications.

| Type | Trigger | Summary |
|------|---------|---------|
| `non_engagement` | Client unacted > 48h | "{org_name} has {count} unacted alerts" |
| `healing_rate_drop` | Site healing < 50% | "{site_name} healing at {rate}%" |
| `compliance_drop` | Site compliance < 70% | "{site_name} compliance at {score}%" |

**Routing:** Partner email from `partners.email` or first admin-role `partner_users.email`. Same digest pattern (4h batch, critical/high immediate). PHI-free.

**Endpoint:** `GET /api/partners/me/notifications` — returns unread partner notifications.

### 4. Compliance Packet Approval Audit (P2)

Query `client_approvals` joined with `client_users` and `pending_alerts` for last 30 days. Render "APPROVAL LOG" section in compliance packet with: date, user email, action, alert summary.

No new tables, no new endpoints — query + template addition to packet builder.

## Migration

### Migration 133: partner_notifications

```sql
CREATE TABLE partner_notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_id UUID NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
    org_id UUID REFERENCES client_orgs(id) ON DELETE SET NULL,
    notification_type VARCHAR(50) NOT NULL,
    summary TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    read_at TIMESTAMPTZ,
    escalated_to_admin_at TIMESTAMPTZ
);

CREATE INDEX idx_partner_notifications_unread
  ON partner_notifications(partner_id, created_at DESC)
  WHERE read_at IS NULL;
```

## New Files

| File | Purpose |
|------|---------|
| `migrations/133_partner_notifications.sql` | Partner notification table |
| `frontend/src/client/CredentialEntryModal.tsx` | Credential entry wizard |

## Modified Files

| File | Change |
|------|--------|
| `alert_router.py` | Non-engagement check, partner classify/digest |
| `client_portal.py` | POST /api/client/credentials |
| `partners.py` | GET /api/partners/me/notifications |
| `ClientAlerts.tsx` | Wire credential modal |
| `email_alerts.py` | Partner digest email template |
| Packet builder | Approval audit section |

## Out of Scope

- Complex approval wizards (current approve/dismiss/acknowledge suffices)
- Partner notification portal page (just API + email for now)
- Per-device credential targeting (whole-site in v1)
- SMS/push notifications

# Partner/Reseller Infrastructure

**Last Updated:** 2026-01-21 (Session 57)
**Status:** Complete (Phase 11 Backend + Phase 12 Partner L3 Escalations + **OAuth Login**)

## Recent Updates (Session 57)

### Partner OAuth Login (Google + Microsoft)
- Partners can now sign in via Google Workspace or Microsoft Entra ID
- New partner OAuth signups require admin approval
- Admin can view and approve/reject pending partners in Partners page
- Domain whitelisting available for auto-approval
- Dual-auth support: API key OR OAuth session

## Overview

The Partner/Reseller Infrastructure enables white-label distribution of OsirisCare compliance appliances through MSP partners. This follows the Datto distribution model where partners onboard their own clients while OsirisCare handles the compliance technology.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    OsirisCare Central                        │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Partner API (/api/partners/*)                       │    │
│  │  - Partner management                                │    │
│  │  - Provision code generation                         │    │
│  │  - Revenue tracking                                  │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ HTTPS + API Key
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Partner Dashboard                         │
│  - View assigned sites                                       │
│  - Create provision codes                                   │
│  - Generate QR codes for appliance onboarding               │
│  - Track revenue share                                       │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ QR Code / Manual Code
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Appliance (First Boot)                    │
│  - Scan QR code or enter provision code                     │
│  - Call /api/partners/claim                                 │
│  - Receive site_id and create config.yaml                   │
│  - Begin normal operation                                   │
└─────────────────────────────────────────────────────────────┘
```

## Database Schema

See migrations:
- `mcp-server/central-command/backend/migrations/003_partner_infrastructure.sql`
- `mcp-server/central-command/backend/migrations/004_discovery_and_credentials.sql`

### Tables

| Table | Purpose |
|-------|---------|
| `partners` | Partner organizations (MSPs) |
| `partner_users` | Partner user accounts with magic link auth |
| `appliance_provisions` | QR/manual provision codes |
| `partner_invoices` | Billing and payout tracking |
| `discovered_assets` | Network discovery results |
| `discovery_scans` | Scan history and status |
| `site_credentials` | Encrypted credential storage |
| `partner_notification_settings` | Partner notification channels (Slack, PagerDuty, Email, Teams, Webhook) |
| `site_notification_overrides` | Site-level notification routing overrides |
| `escalation_tickets` | L3 escalation tickets from appliances |
| `notification_deliveries` | Delivery logs for notifications |
| `sla_definitions` | SLA response/resolution times by priority |

### Key Relationships

- `sites.partner_id` → `partners.id` (nullable, for direct sales)
- `appliance_provisions.partner_id` → `partners.id`
- `appliance_provisions.claimed_site_id` → `sites.site_id`
- `discovered_assets.site_id` → `sites.id`
- `discovered_assets.credential_id` → `site_credentials.id`
- `discovery_scans.site_id` → `sites.id`
- `site_credentials.site_id` → `sites.id`

## API Endpoints

### Admin Endpoints (OsirisCare staff)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/partners` | Create new partner |
| GET | `/api/partners` | List all partners |
| GET | `/api/partners/{id}` | Get partner details |
| PUT | `/api/partners/{id}` | Update partner |
| POST | `/api/partners/{id}/regenerate-key` | Regenerate API key |
| POST | `/api/partners/{id}/users` | Create partner user |
| POST | `/api/partners/{id}/users/{uid}/magic-link` | Generate magic login link |

### Partner Endpoints (API Key auth via X-API-Key header)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/partners/me` | Get current partner info |
| GET | `/api/partners/me/sites` | List partner's sites |
| GET | `/api/partners/me/sites/{site_id}` | Get site detail with assets/credentials |
| GET | `/api/partners/me/provisions` | List provision codes |
| POST | `/api/partners/me/provisions` | Create provision code |
| GET | `/api/partners/me/provisions/{id}/qr` | Generate QR code image |
| DELETE | `/api/partners/me/provisions/{id}` | Revoke provision code |
| POST | `/api/partners/me/sites/{site_id}/credentials` | Add site credentials |
| POST | `/api/partners/me/sites/{site_id}/credentials/{id}/validate` | Validate credential |
| DELETE | `/api/partners/me/sites/{site_id}/credentials/{id}` | Delete credential |
| GET | `/api/partners/me/sites/{site_id}/assets` | List discovered assets |
| PATCH | `/api/partners/me/sites/{site_id}/assets/{id}` | Update asset status |
| POST | `/api/partners/me/sites/{site_id}/discovery/trigger` | Trigger network scan |

### Partner Notification Endpoints (API Key auth)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/partners/me/notifications/settings` | Get notification channel settings |
| PUT | `/api/partners/me/notifications/settings` | Update notification settings |
| POST | `/api/partners/me/notifications/settings/test` | Test notification channel |
| GET | `/api/partners/me/notifications/sites/{site_id}/overrides` | Get site overrides |
| PUT | `/api/partners/me/notifications/sites/{site_id}/overrides` | Set site overrides |
| GET | `/api/partners/me/escalations` | List escalation tickets |
| POST | `/api/partners/me/escalations/{id}/acknowledge` | Acknowledge ticket |
| POST | `/api/partners/me/escalations/{id}/resolve` | Resolve ticket |
| GET | `/api/partners/me/sla/metrics` | Get SLA metrics |
| GET | `/api/partners/me/sla/definitions` | Get SLA definitions |
| PUT | `/api/partners/me/sla/definitions` | Update SLA definitions |

### Escalation Endpoints (Agent L3)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/escalations` | Create L3 escalation from agent |

### Public Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/partners/claim` | Claim provision code (appliance) |
| GET | `/api/partners/provision/{code}/qr` | Get QR code by provision code |

### Discovery Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/discovery/report` | Receive discovery results from appliance |
| POST | `/api/discovery/status` | Update scan status |
| GET | `/api/discovery/pending/{site_id}` | Get pending scans for appliance |
| GET | `/api/discovery/assets/{site_id}/summary` | Asset summary for dashboard |

### Provisioning Endpoints (Appliance first-boot)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/provision/claim` | Claim provision code (creates site) |
| GET | `/api/provision/validate/{code}` | Validate code before claiming |
| POST | `/api/provision/status` | Update provisioning progress |
| POST | `/api/provision/heartbeat` | Heartbeat from provisioning appliance |
| GET | `/api/provision/config/{appliance_id}` | Get appliance configuration |

## Authentication

Partners can authenticate via two methods:

### 1. API Key (Traditional)
```bash
curl -H "X-API-Key: your-api-key" https://api.osiriscare.net/api/partners/me
```

### 2. OAuth (Google/Microsoft)
Partners can sign in via Google Workspace or Microsoft Entra ID at:
- `https://dashboard.osiriscare.net/partner/login`

OAuth uses session cookies (`osiris_partner_session`) for authentication.

**New OAuth signups require admin approval** before dashboard access.

### OAuth Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/partner-auth/providers` | Get available OAuth providers |
| GET | `/api/partner-auth/microsoft` | Start Microsoft OAuth flow |
| GET | `/api/partner-auth/google` | Start Google OAuth flow |
| GET | `/api/partner-auth/callback` | OAuth callback handler |
| POST | `/api/partners/auth/magic` | Magic link token validation |

### Admin Approval Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/admin/partners/pending` | List partners pending approval |
| POST | `/api/admin/partners/approve/{partner_id}` | Approve pending partner |
| POST | `/api/admin/partners/reject/{partner_id}` | Reject pending partner |

## Provisioning Flow

1. **Partner creates provision code** via dashboard
2. **QR code generated** containing claim URL
3. **Appliance boots** and enters provisioning mode (no config.yaml)
4. **User scans QR** or enters 16-character code
5. **Appliance calls** `POST /api/partners/claim` with code + MAC
6. **Server responds** with site_id, partner branding, API endpoint
7. **Appliance creates** `/var/lib/msp/config.yaml`
8. **Appliance starts** normal compliance monitoring

## Revenue Model

Default split: 40% partner / 60% OsirisCare

Configurable per partner in the `revenue_share_percent` field.

## File Locations

| Component | Path |
|-----------|------|
| Partner API | `mcp-server/central-command/backend/partners.py` |
| **Partner OAuth** | `mcp-server/central-command/backend/partner_auth.py` |
| Discovery API | `mcp-server/central-command/backend/discovery.py` |
| Provisioning API | `mcp-server/central-command/backend/provisioning.py` |
| Partner Dashboard | `mcp-server/central-command/frontend/src/partner/` |
| **Partner Login** | `mcp-server/central-command/frontend/src/partner/PartnerLogin.tsx` |
| **Partner Context** | `mcp-server/central-command/frontend/src/partner/PartnerContext.tsx` |
| Agent Provisioning | `packages/compliance-agent/src/compliance_agent/provisioning.py` |
| Migration 003 | `mcp-server/central-command/backend/migrations/003_partner_infrastructure.sql` |
| Migration 004 | `mcp-server/central-command/backend/migrations/004_discovery_and_credentials.sql` |
| Migration 007 | `mcp-server/central-command/backend/migrations/007_partner_escalation.sql` |
| **Migration 025** | `mcp-server/central-command/backend/migrations/025_oauth_state.sql` |
| **Migration 026** | `mcp-server/central-command/backend/migrations/026_partner_approval.sql` |
| Notifications API | `mcp-server/central-command/backend/notifications.py` |
| Escalation Engine | `mcp-server/central-command/backend/escalation_engine.py` |
| Notification Settings UI | `mcp-server/central-command/frontend/src/pages/NotificationSettings.tsx` |
| Provisioning Tests | `packages/compliance-agent/tests/test_provisioning.py` |
| Partner API Tests | `packages/compliance-agent/tests/test_partner_api.py` |

## Related Documentation

- [Provisioning Module](./PROVISIONING.md) - Agent-side provisioning
- [Discovery System](../DISCOVERY.md) - Network discovery concepts

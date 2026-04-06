# Cross-Appliance Dedup + Alert Routing + Minimal Approval

**Date:** 2026-04-06
**Scope:** Spec 1 of 2 — Multi-Appliance Maturity
**Sessions:** Est. 1-2 implementation sessions

## Problem

1. Incident dedup is scoped per-appliance (`WHERE appliance_id = :appliance_id`). Two appliances reporting the same issue on the same host create duplicate incidents, inflating metrics and eroding auditor trust.
2. All alerts route to a single hardcoded email (`administrator@osiriscare.net`). Clients receive nothing. Partners receive nothing org-specific.
3. No mechanism for clients to approve remediation actions, leaving accountability on the MSP instead of the covered entity.

## Accountability Model

```
ADMIN (MSP operator)     PARTNER                  CLIENT (doctor/operator)
────────────────────     ───────                  ───────────────────────
Mesh breakage            Org health drops         Device drift detected
Appliance offline        SLA breach               Rogue device found
Central Command health   Client non-engagement    Credential needed
L4 escalation queue                               Patch approval request
```

The system pushes clients to engage without human intervention from MSP or partner — zero friction. Clients receive automated alerts, click through to the portal, and take action (approve patches, acknowledge devices, enter credentials). This keeps accountability on the end client: "you approved it" vs. "it's not working, Jeff."

## Alert Mode (Per-Site, Org-Inherited)

| Mode | Client Emails | Approval Buttons | Who Remediates |
|------|--------------|------------------|----------------|
| `self_service` | Yes, with actions | Yes | Client approves, system executes |
| `informed` (default) | Yes, informational | No | Partner/system handles it |
| `silent` | No client emails | No | Partner/system handles it |

- Org sets the default via `client_orgs.client_alert_mode`
- Individual sites override via `sites.client_alert_mode` (NULL = inherit from org)
- Resolution: `site.client_alert_mode OR org.client_alert_mode OR 'informed'`
- Partner configures via their portal

## Component 1: Cross-Appliance Incident Dedup

### Migration 128: dedup_key column

```sql
ALTER TABLE incidents ADD COLUMN dedup_key VARCHAR(64);
CREATE INDEX idx_incidents_dedup_key ON incidents(dedup_key)
  WHERE dedup_key IS NOT NULL;

-- Backfill open incidents
UPDATE incidents
SET dedup_key = encode(
  sha256((site_id || ':' || incident_type || ':' || COALESCE(details->>'hostname', ''))::bytea),
  'hex'
)
WHERE status IN ('open', 'resolving', 'escalated');
```

### Dedup key computation

```python
dedup_key = hashlib.sha256(
    f"{site_id}:{incident_type}:{hostname}".encode()
).hexdigest()
```

Where `hostname` = `details.get('hostname', '')`. If empty, falls back to appliance-scoped dedup (current behavior) for backward compatibility.

### Modified dedup query (agent_api.py)

```sql
SELECT id, status, severity, appliance_id FROM incidents
WHERE site_id = :site_id
  AND dedup_key = :dedup_key
  AND (
    status IN ('open', 'resolving', 'escalated')
    OR (status = 'resolved' AND resolved_at > NOW() - INTERVAL '30 minutes')
  )
  AND created_at > NOW() - INTERVAL '48 hours'
ORDER BY created_at DESC
LIMIT 1
```

### Behaviors

- First appliance to report creates the incident (appliance_id = reporter)
- Second appliance hits dedup, returns `{status: "deduplicated"}`, no new row
- If second report has higher severity, UPDATE existing incident to higher severity
- No hostname in details = fallback to `appliance_id`-scoped dedup (legacy compat)
- Resolved grace period: 30 minutes (matches healing pipeline grace period)

## Component 2: Org/Site Alert Config

### Migration 129: org alert fields

```sql
ALTER TABLE client_orgs ADD COLUMN alert_email VARCHAR(255);
ALTER TABLE client_orgs ADD COLUMN cc_email VARCHAR(255);
ALTER TABLE client_orgs ADD COLUMN client_alert_mode VARCHAR(20) DEFAULT 'informed'
  CHECK (client_alert_mode IN ('self_service', 'informed', 'silent'));
```

### Migration 130: site-level override

```sql
ALTER TABLE sites ADD COLUMN client_alert_mode VARCHAR(20)
  CHECK (client_alert_mode IN ('self_service', 'informed', 'silent'));
-- NULL = inherit from org
```

### Endpoints

- `PUT /api/partners/me/orgs/{org_id}/alert-config` — set org defaults (alert_email, cc_email, client_alert_mode)
- `PUT /api/partners/me/sites/{site_id}/alert-config` — override client_alert_mode per site
- `GET /api/partners/me/orgs/{org_id}/alert-config` — read current config
- `GET /api/partners/me/sites/{site_id}/alert-config` — read site override + effective mode

Partner RBAC: requires `admin` or `tech` role.

## Component 3: Alert Router + Email Digest

### New module: alert_router.py

Called after incident creation in `report_incident()` and after health_monitor detections.

**Routing logic:**
1. Resolve effective alert mode: `get_alert_mode(site, org)`
2. If `silent` — skip client email, done
3. Classify alert tier:
   - Admin: mesh down, appliance offline, L4 queue (existing paths, unchanged)
   - Partner: org health drop, SLA breach, client non-engagement (future Spec 2)
   - Client: device drift, rogue device, credential needed, patch available
4. Client alerts → enqueue to `pending_alerts` table

### Migration 131: pending_alerts

```sql
CREATE TABLE pending_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES client_orgs(id),
    site_id UUID NOT NULL REFERENCES sites(id),
    alert_type VARCHAR(50) NOT NULL,
    severity VARCHAR(20) DEFAULT 'medium',
    summary TEXT NOT NULL,
    incident_id UUID REFERENCES incidents(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at TIMESTAMPTZ,
    dismissed_at TIMESTAMPTZ
);

CREATE INDEX idx_pending_alerts_unsent ON pending_alerts(org_id, sent_at)
  WHERE sent_at IS NULL;
```

### Alert types

| alert_type | Trigger | Summary Template |
|------------|---------|------------------|
| `patch_available` | Drift: patching check fails | "N devices have patch updates available" |
| `service_stopped` | Drift: service check fails | "N devices have stopped services" |
| `rogue_device` | Netscan: unknown MAC | "N unrecognized devices detected" |
| `credential_needed` | Device has no scan credentials | "N devices need credentials configured" |
| `firewall_off` | Drift: firewall check fails | "N devices have firewall disabled" |
| `encryption_off` | Drift: encryption check fails | "N devices have encryption disabled" |

All summaries are PHI-free. Counts, not specifics. Details behind portal auth.

### Digest sender

Background task in health_monitor loop:
- Runs every **4 hours** (configurable via `ALERT_DIGEST_INTERVAL_HOURS` env var)
- Groups unsent alerts by org_id
- Renders one HTML email per org
- Sends to `org.alert_email`, CC to `org.cc_email`
- Marks `sent_at` on all flushed alerts
- **Critical/high severity alerts bypass digest** — sent immediately (still PHI-free)

### PHI-free email template

```
Subject: OsirisCare — N items need attention at {org_name}

We detected {count} items that need attention:

  * {count} devices have patch updates available (Site: {site_name})
  * {count} unrecognized devices detected (Site: {site_name})
  * {count} devices need credentials configured (Site: {site_name})

View details: https://portal.osiriscare.net/alerts

No action required — your compliance team is monitoring this.
[OR, for self_service sites:]
Review and take action: https://portal.osiriscare.net/alerts

-- OsirisCare Compliance Platform
```

Footer varies by alert mode:
- `informed`: "No action required — your compliance team is monitoring this."
- `self_service`: "Review and take action at the link above."

## Component 4: Alert Landing Page (Client Portal)

### New route: /client/alerts

**Page structure:**
- Grouped by site
- Each alert: type icon, summary, timestamp, status badge (pending/acknowledged/resolved)
- Click-through to incident detail (existing page, PHI-filtered by `phi_boundary.py`)
- For `self_service` sites: approve/dismiss action buttons inline

### Backend endpoint

`GET /api/client/alerts` — returns alerts for authenticated client's org.
- RLS-enforced via `app.current_org` GUC (existing tenant isolation pattern)
- Returns: `{alerts: [{id, site_name, alert_type, summary, severity, status, created_at, actions_available}]}`
- `actions_available` is true only when site's effective mode is `self_service`

## Component 5: Minimal Approve/Dismiss

For `self_service` sites only. `informed` sites see alerts but no action buttons.

### Actions by alert type

| Alert Type | Actions | Effect |
|-----------|---------|--------|
| `patch_available` | **Approve** / Dismiss | Approve creates L1 healing order |
| `service_stopped` | **Approve** / Dismiss | Approve creates L1 healing order |
| `rogue_device` | **Acknowledge** / Ignore | Acknowledge adds to device inventory |
| `credential_needed` | **Enter Credentials** | Opens guided credential entry |
| `firewall_off` | **Approve Fix** / Dismiss | Approve creates L1 healing order |
| `encryption_off` | **Approve Fix** / Dismiss | Approve creates L1 healing order |

### Migration 132: client_approvals

```sql
CREATE TABLE client_approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES client_orgs(id),
    site_id UUID NOT NULL REFERENCES sites(id),
    incident_id UUID REFERENCES incidents(id),
    alert_id UUID NOT NULL REFERENCES pending_alerts(id),
    action VARCHAR(20) NOT NULL
      CHECK (action IN ('approved', 'dismissed', 'acknowledged', 'ignored', 'credentials_entered')),
    acted_by UUID NOT NULL,
    acted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes TEXT
);

CREATE INDEX idx_client_approvals_incident ON client_approvals(incident_id);
```

### Endpoint

`POST /api/client/alerts/{alert_id}/action`
- Body: `{action: "approved", notes: "optional"}`
- Requires authenticated client user
- Validates alert belongs to user's org (RLS)
- Validates site's effective mode is `self_service` (403 otherwise)
- Inserts into `client_approvals`
- If `approved`: triggers L1 healing order for linked incident
- If `acknowledged` (rogue device): adds device to `discovered_devices` with `status: 'acknowledged'`
- Returns: `{status: "ok", action_taken: "approved", incident_id: "..."}`

### Accountability artifact

Every approval row is queryable evidence:
- "Client user X approved patch remediation for incident Y at timestamp Z"
- Surfaced in compliance packets and audit reports
- Spec 2 will expand this into the full audit trail UX

## Component 6: Onboarding Welcome Email

Triggered on **first appliance checkin that discovers devices** (device_count > 0) for an org that has `alert_email` set and has never received a welcome email. This avoids sending "we found 0 devices" on the very first checkin before netscan runs.

**Tracking:** `client_orgs.welcome_email_sent_at TIMESTAMPTZ` (add in migration ~129).

**Template:**
```
Subject: OsirisCare is now protecting {org_name}

Your network compliance monitoring is active.

We found {device_count} devices across {site_count} locations.

Log in to review your compliance status:
https://portal.osiriscare.net

This is an automated message from your compliance platform.
No action is required at this time.

-- OsirisCare Compliance Platform
```

Sent once. Never repeated.

## Migration Summary

| Migration | Table | Changes |
|-----------|-------|---------|
| 128 | incidents | `dedup_key VARCHAR(64)`, index, backfill |
| 129 | client_orgs | `alert_email`, `cc_email`, `client_alert_mode`, `welcome_email_sent_at` |
| 130 | sites | `client_alert_mode` (nullable, inherits from org) |
| 131 | pending_alerts | New table (digest buffer) |
| 132 | client_approvals | New table (audit trail) |

## New Files

| File | Purpose |
|------|---------|
| `alert_router.py` | Alert classification + routing + digest scheduling |
| `frontend/src/pages/ClientAlerts.tsx` | Client portal alerts page |

## Modified Files

| File | Change |
|------|--------|
| `agent_api.py` | Dedup query uses `dedup_key` instead of `appliance_id` |
| `health_monitor.py` | Calls alert_router after detections, runs digest sender |
| `email_alerts.py` | New digest email template, org-routed sending |
| `partners.py` | Alert config endpoints |
| `client_portal.py` | Alert list + action endpoints |
| `main.py` | Register new routes |
| `sites.py` | Deliver `client_alert_mode` in checkin response |

## Spec 2 (Future)

- Full approval workflow UX (multi-step wizards, credential entry forms)
- Partner notification tier (org health, SLA, client engagement tracking)
- Alert preferences (frequency, types, quiet hours)
- Approval audit trail in compliance packets
- "Client non-engagement" escalation to partner when alerts go unacted

## Out of Scope

- Rogue device lifecycle state machine (detection exists, lifecycle is separate feature)
- Scheduled patch windows / rollback
- SMS/push notifications (email only for now)
- Per-user alert preferences (org-level only for Spec 1)

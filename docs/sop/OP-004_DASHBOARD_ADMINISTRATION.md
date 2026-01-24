# OP-004: Dashboard Administration

**Document Type:** Operator Manual
**Version:** 2.0
**Last Updated:** 2026-01-24 (Session 68)
**Owner:** MSP Operations Team
**Review Cycle:** Quarterly
**Classification:** Level 3 (Reference Guide)

---

## Purpose

This manual provides detailed instructions for using the Central Command dashboard, including all pages, features, and common workflows.

---

## Access Information

| Environment | URL | Purpose |
|-------------|-----|---------|
| Production | https://dashboard.osiriscare.net | Live dashboard |
| API | https://api.osiriscare.net | REST API endpoints |
| Client Portal | https://dashboard.osiriscare.net/client/* | Client self-service |
| Partner Portal | https://dashboard.osiriscare.net/partner/* | Partner dashboard |

### Authentication

- **Admin Users:** Username/password login
- **Partners:** OAuth (Google/Microsoft) or API key
- **Clients:** Magic link email authentication

---

## Dashboard Pages Reference

### 1. Overview Dashboard (`/`)

**Purpose:** High-level system health and key metrics at a glance.

**Key Sections:**
- **Fleet Status:** Online/Offline/Stale appliance counts
- **Compliance Score:** Average across all sites
- **Recent Incidents:** Last 10 incidents with severity
- **Quick Stats:** Total sites, active alerts, pending updates

**Usage:**
- Check this page first when starting your shift
- Look for any critical (red) status indicators
- Click on any metric to drill down to details

---

### 2. Sites Page (`/sites`)

**Purpose:** Manage all client sites and monitor appliance connectivity.

**Key Features:**
- Real-time site status (Online/Stale/Offline/Pending)
- Filter by status, tier, partner
- Create new sites
- Bulk actions

**Status Indicators:**

| Status | Color | Check-in Age | Action |
|--------|-------|--------------|--------|
| Online | Green | < 5 min | None |
| Stale | Yellow | 5-60 min | Monitor |
| Offline | Red | > 60 min | Investigate |
| Pending | Gray | Never | Await appliance |

**Creating a New Site:**
1. Click **"+ New Site"** button
2. Enter clinic name (required)
3. Add contact information (optional)
4. Select tier (small/mid/large)
5. Optionally assign to partner
6. Click **"Create Site"**

---

### 3. Site Detail Page (`/sites/:siteId`)

**Purpose:** Complete view of a single site with all management options.

**Sections:**

| Section | Description |
|---------|-------------|
| Header | Site name, status badge, quick actions |
| Contact Info | Name, email, phone, address |
| Appliances | Connected appliances with status |
| Credentials | Stored credentials (encrypted) |
| Compliance Score | Per-check breakdown |
| Recent Checks | Last compliance check results |

**Quick Actions:**

| Button | Description |
|--------|-------------|
| Frameworks | Configure compliance frameworks |
| Workstations | View workstation compliance |
| Go Agents | Manage Go agent deployments |
| Integrations | Cloud service integrations |
| Generate Portal Link | Create client portal access |
| Healing Tier Toggle | Switch Standard/Full Coverage |

---

### 4. Framework Config (`/sites/:siteId/frameworks`)

**Purpose:** Configure which compliance frameworks apply to a site.

**Available Frameworks:**

| Framework | Description |
|-----------|-------------|
| HIPAA | Healthcare (default) |
| SOC 2 | Service organizations |
| PCI DSS | Payment card industry |
| NIST CSF | Cybersecurity framework |
| CIS Controls | Security best practices |

**Features:**
- Enable/disable frameworks per site
- Industry presets (Healthcare, Financial, Technology)
- Custom control mapping
- Framework-specific evidence generation

---

### 5. Workstations Page (`/sites/:siteId/workstations`)

**Purpose:** Monitor workstation compliance across the site.

**Compliance Checks:**

| Check | Description | HIPAA Control |
|-------|-------------|---------------|
| BitLocker | Disk encryption status | 164.312(a)(2)(iv) |
| Defender | Antimalware status | 164.308(a)(5) |
| Firewall | Windows Firewall profiles | 164.312(c)(1) |
| Patches | Windows Update status | 164.308(a)(5)(ii)(B) |
| Screen Lock | Lock timeout settings | 164.312(a)(2)(iii) |

**Features:**
- Per-workstation compliance status
- Site-level summary statistics
- Export compliance report
- RMM comparison tool

---

### 6. RMM Comparison (`/sites/:siteId/workstations/rmm-compare`)

**Purpose:** Compare OsirisCare coverage against existing RMM tools.

**Detected RMMs:**
- ConnectWise
- Datto
- NinjaRMM
- Kaseya
- Continuum

**Comparison Metrics:**
- Device coverage percentage
- Check type coverage
- Response time comparison
- Cost analysis

---

### 7. Go Agents Page (`/sites/:siteId/agents`)

**Purpose:** Manage lightweight Go agents on workstations.

**Agent Status:**

| Status | Description |
|--------|-------------|
| Active | Agent reporting via gRPC |
| Offline | No recent heartbeat |
| Pending | Awaiting first connection |

**Capabilities:**
- Deploy agents to workstations
- View agent version and health
- Trigger manual compliance scan
- Configure capability tier (Monitor/Heal/Full)

---

### 8. Cloud Integrations (`/sites/:siteId/integrations`)

**Purpose:** Connect cloud services for expanded compliance coverage.

**Supported Integrations:**

| Service | Data Collected |
|---------|----------------|
| AWS | IAM users, S3 buckets, CloudTrail |
| Google Workspace | Users, devices, audit logs |
| Okta | Users, MFA status, sign-in logs |
| Azure AD | Users, devices, conditional access |
| Microsoft Security | Defender alerts, Intune compliance, Secure Score |

**Setup Flow:**
1. Click **"+ Add Integration"**
2. Select service type
3. Complete OAuth authorization
4. Configure sync settings
5. View discovered resources

---

### 9. Notifications Page (`/notifications`)

**Purpose:** View all system notifications and alerts.

**Notification Types:**

| Type | Description |
|------|-------------|
| Alert | Critical/High severity issues |
| Warning | Medium severity issues |
| Info | Informational messages |
| System | Platform announcements |

**Features:**
- Filter by type, severity, read status
- Mark individual or all as read
- Link to related incidents

---

### 10. Incidents Page (`/incidents`)

**Purpose:** View and manage all incidents across the fleet.

**Filters:**

| Filter | Options |
|--------|---------|
| Status | All, Active, Resolved |
| Severity | Critical, High, Medium, Low |
| Site | All sites or specific |
| Tier | L1 (auto), L2 (LLM), L3 (human) |

**Incident Details:**
- Check type and description
- Resolution tier (L1/L2/L3)
- Resolution action taken
- Timestamp and duration
- Raw incident data

---

### 11. Notification Settings (`/notification-settings`)

**Purpose:** Configure system notification channels.

**Channels:**

| Channel | Configuration |
|---------|---------------|
| Email | SMTP settings, recipients |
| Slack | Webhook URL |
| PagerDuty | Integration key |
| Teams | Webhook URL |
| Webhook | Custom endpoint |

---

### 12. Onboarding Page (`/onboarding`)

**Purpose:** Pipeline view of prospects through onboarding stages.

**Stages:**

| Phase | Stages |
|-------|--------|
| Phase 1 (Sales) | Lead → Discovery → Proposal → Contract → Intake → Creds → Shipped |
| Phase 2 (Deploy) | Received → Connectivity → Scanning → Baseline → Compliant → Active |

**Metrics:**
- At Risk: >7 days in stage
- Stalled: >14 days in stage
- Blockers: Active issues preventing progress

---

### 13. Partners Page (`/partners`)

**Purpose:** Manage partner/reseller accounts.

**Sections:**

| Section | Description |
|---------|-------------|
| Active Partners | List of approved partners |
| Pending Approvals | OAuth signups awaiting approval |
| OAuth Settings | Domain whitelist configuration |

**Partner Management:**
- Create/edit partner accounts
- Generate/regenerate API keys
- View assigned sites
- Approve/reject OAuth signups

---

### 14. Users Page (`/users`)

**Purpose:** Manage admin user accounts.

**User Roles:**

| Role | Permissions |
|------|-------------|
| Admin | Full access, user management |
| Operator | View all, execute actions |
| Readonly | View only |

**Features:**
- Invite new users via email
- Resend invites
- Revoke pending invites
- Change user roles
- Disable/enable accounts

---

### 15. Runbooks Page (`/runbooks`)

**Purpose:** Browse all available remediation runbooks.

**Runbook Categories:**

| Category | Count | Examples |
|----------|-------|----------|
| Windows Security | 13 | Firewall, Defender, BitLocker |
| Windows Services | 4 | Critical services, DNS |
| Windows Network | 3 | DNS client, NLA |
| Linux SSH | 3 | SSH hardening, key management |
| Linux Firewall | 1 | iptables/nftables |
| Linux Services | 4 | Critical services |

**Total: 43 runbooks** (27 Windows + 16 Linux)

---

### 16. Runbook Config (`/runbook-config`)

**Purpose:** Enable/disable runbooks per site.

**Configuration:**
- Toggle runbooks on/off for each site
- Set execution priority
- Configure parameters
- View execution history

---

### 17. Learning Page (`/learning`)

**Purpose:** Monitor the data flywheel and L2→L1 pattern promotion.

**Sections:**

| Section | Description |
|---------|-------------|
| Pattern Stats | Total patterns, promotion candidates |
| Promotion Queue | Patterns ready for L1 promotion |
| Recent Promotions | Successfully promoted patterns |
| Learning Metrics | Success rates by check type |

**Promotion Criteria:**
- 5+ successful occurrences
- 90%+ success rate
- No recent failures

---

### 18. Audit Logs Page (`/audit-logs`)

**Purpose:** Security audit trail of all admin actions.

**Logged Events:**

| Event Type | Description |
|------------|-------------|
| LOGIN_SUCCESS | Successful authentication |
| LOGIN_FAILED | Failed login attempt |
| USER_CREATED | New user invited |
| USER_UPDATED | User role/status changed |
| SITE_CREATED | New site created |
| CREDENTIAL_ADDED | Credential stored |
| RUNBOOK_EXECUTED | Manual runbook execution |

**Filters:**
- Date range
- Event type
- User
- Resource

---

### 19. OAuth Settings (`/settings/oauth`)

**Purpose:** Configure OAuth providers for partner authentication.

**Settings:**

| Provider | Configuration |
|----------|---------------|
| Google | Client ID, Client Secret |
| Microsoft | Client ID, Client Secret, Tenant ID |

**Domain Whitelist:**
- Auto-approve partners from whitelisted domains
- Comma-separated domain list

---

### 20. Fleet Updates (`/fleet-updates`)

**Purpose:** Manage appliance ISO updates across the fleet.

**Sections:**

| Section | Description |
|---------|-------------|
| Stats | Latest version, active releases/rollouts |
| Releases | List of available ISO releases |
| Rollouts | Active deployment rollouts |
| Update History | Per-appliance update history |

**Creating a Release:**
1. Click **"+ New Release"**
2. Enter version (e.g., v47)
3. Enter ISO URL
4. Enter SHA256 hash
5. Enter agent version
6. Add release notes
7. Click **"Create Release"**

**Creating a Rollout:**
1. Select release
2. Click **"Create Rollout"**
3. Configure stages (e.g., 5% → 25% → 100%)
4. Set pause between stages
5. Click **"Start Rollout"**

**Rollout Controls:**
- Pause/Resume rollout
- Advance to next stage
- Cancel rollout

---

### 21. Documentation (`/docs`)

**Purpose:** Built-in operational documentation and SOPs.

**Available SOPs:**
- Daily Operations
- Incident Response
- Disaster Recovery
- Client Escalation
- Evidence Verification
- Sites Management
- Dashboard Administration

---

## Common Workflows

### Daily Operations Check

1. **Overview Dashboard:** Check for critical alerts
2. **Sites Page:** Verify all sites online
3. **Incidents Page:** Review active incidents
4. **Onboarding:** Check for stalled prospects
5. **Fleet Updates:** Verify update rollout progress

### New Client Onboarding

1. **Onboarding Page:** Click "+ New Prospect"
2. **Site Detail:** Add contact info and credentials
3. **Partners Page:** Assign to partner (if applicable)
4. **Provision Code:** Generate for appliance
5. **Monitor:** Watch for first phone-home
6. **Validate:** Verify compliance checks passing

### Troubleshooting Offline Site

1. **Sites Page:** Identify offline site
2. **Site Detail:** Check last check-in time
3. **Verify:** Network connectivity at client
4. **Check:** Appliance power status
5. **Review:** Appliance logs via SSH
6. **Escalate:** Contact client if needed

### User Management

1. **Users Page:** Click "+ Invite User"
2. **Enter:** Email, role, display name
3. **Send:** Invite email sent automatically
4. **User:** Clicks link to set password
5. **Verify:** User appears in active list

### Partner Approval (OAuth)

1. **Partners Page:** View Pending Approvals
2. **Review:** Partner company and email domain
3. **Approve:** Click approve button
4. **Notify:** Partner receives access email
5. **Alternative:** Reject if suspicious

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Cmd+K` / `Ctrl+K` | Open command palette |
| `g h` | Go to home/overview |
| `g s` | Go to sites |
| `g o` | Go to onboarding |
| `g i` | Go to incidents |
| `g u` | Go to users |
| `g f` | Go to fleet updates |
| `?` | Show shortcuts help |

---

## API Quick Reference

### Authentication

All API requests require Bearer token:

```bash
curl -H "Authorization: Bearer YOUR_TOKEN" \
  https://api.osiriscare.net/api/sites
```

### Common Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/sites` | GET | List all sites |
| `/api/sites/{id}` | GET | Get site detail |
| `/api/fleet/status` | GET | Fleet overview |
| `/api/incidents` | GET | List incidents |
| `/api/users` | GET | List admin users |
| `/api/partners` | GET | List partners |

---

## Troubleshooting

### Dashboard Not Loading

1. Check API health: `curl https://api.osiriscare.net/health`
2. Clear browser cache (Cmd+Shift+R)
3. Check browser console for errors
4. Verify network connectivity
5. Try incognito/private window

### Data Not Updating

1. Hard refresh: `Cmd+Shift+R`
2. Check if polling is working (network tab)
3. Verify API is responding
4. Check for JavaScript errors in console

### User Cannot Log In

1. Verify username is correct
2. Check account is not disabled
3. Verify password meets requirements
4. Check for account lockout (5 failed attempts)
5. Reset password via invite if needed

### Invite Email Not Received

1. Check spam/junk folders
2. Verify email address is correct
3. Check SMTP configuration
4. Resend invite from Users page
5. Verify mail server logs

---

## Security Notes

### Password Requirements

- Minimum 12 characters
- Must include: uppercase, lowercase, digit, special character
- Cannot be in common password list
- Cannot be similar to username

### Session Security

- Sessions expire after 24 hours
- Account locks after 5 failed attempts (15 min)
- All actions logged to audit trail
- HTTPS required for all connections

### Credential Storage

- All credentials encrypted with AES-256
- Never displayed in plain text
- Audit logged on access
- Rotated on suspicion of compromise

---

## Related Documents

- [SOP-015: Sites & Appliance Management](SOP-015_SITES_APPLIANCE_MANAGEMENT.md)
- [Partner Dashboard Guide](../partner/PARTNER_DASHBOARD_GUIDE.md)
- [Client Portal Documentation](../client/CLIENT_PORTAL_GUIDE.md)
- [Fleet Updates](../ZERO_FRICTION_UPDATES.md)
- [Learning System](../LEARNING_SYSTEM.md)

---

**Document Version:** 2.0
**Last Updated:** 2026-01-24
**Next Review:** 2026-04-24
**Owner:** MSP Operations Team

**Change Log:**
- 2026-01-24: v2.0 - Complete rewrite with all new pages (Session 68)
- 2025-12-31: v1.0 - Initial version

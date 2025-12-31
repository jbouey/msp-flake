# OP-004: Dashboard Administration

**Document Type:** Operator Manual
**Version:** 1.0
**Last Updated:** 2025-12-31
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

---

## Dashboard Pages Reference

### 1. Overview Page (`/`)

**Purpose:** High-level system health and key metrics at a glance.

**Key Features:**
- Fleet status summary (online/offline/stale counts)
- Recent incidents with severity indicators
- System health indicators
- Quick navigation to critical items

**Usage:**
- Check this page first when starting your shift
- Look for any critical (red) status indicators
- Click on any metric to drill down to details

---

### 2. Sites Page (`/sites`)

**Purpose:** Manage all client sites and monitor appliance connectivity.

**Key Features:**
- Real-time site status (Online/Stale/Offline/Pending)
- Filter by status
- Create new sites
- View appliance count and tier

**Status Indicators:**
| Status | Icon | Meaning | Action |
|--------|------|---------|--------|
| Online | Green | Checkin < 5 min | None |
| Stale | Yellow | Checkin 5-15 min | Monitor |
| Offline | Red | Checkin > 15 min | Investigate |
| Pending | Gray | Never connected | Await appliance |

**Creating a New Site:**
1. Click **"+ New Site"** button
2. Enter clinic name (required)
3. Add contact information (optional)
4. Select tier (small/mid/large)
5. Click **"Create Site"**
6. Note the generated `site_id` for appliance configuration

**Viewing Site Details:**
- Click any row to navigate to `/sites/{site_id}`
- See appliance information, credentials, and timestamps

---

### 3. Site Detail Page (`/sites/:siteId`)

**Purpose:** Complete view of a single site with all related data.

**Sections:**
- **Header:** Clinic name, status badge, site_id
- **Contact Info:** Name, email, phone, address
- **Appliances:** List of connected appliances with individual status
- **Credentials:** Encrypted credential storage
- **Timeline:** Onboarding stage timestamps

**Adding Credentials:**
1. Scroll to Credentials section
2. Click **"+ Add Credential"**
3. Select credential type (Router, AD, EHR, Backup, Other)
4. Enter details (name, host, username, password)
5. Click **"Add Credential"**

**Security Note:** Credentials are encrypted with Fernet and never exposed in API responses.

---

### 4. Onboarding Page (`/onboarding`)

**Purpose:** Pipeline view of prospects moving through onboarding stages.

**Pipeline Stages:**

**Phase 1: Sales & Contracting**
| Stage | Description |
|-------|-------------|
| Lead | Initial prospect identified |
| Discovery | Needs assessment meeting scheduled |
| Proposal | Proposal sent to prospect |
| Contract | Contract negotiation/signing |
| Intake | Gathering requirements and credentials |
| Creds | Credentials received and verified |
| Shipped | Appliance shipped to site |

**Phase 2: Technical Deployment**
| Stage | Description |
|-------|-------------|
| Received | Appliance arrived at site |
| Connectivity | First phone-home received |
| Scanning | Network discovery in progress |
| Baseline | Baseline configuration applied |
| Compliant | Meeting compliance requirements |
| Active | Fully operational client |

**Key Metrics:**
- **At Risk:** >7 days in current stage
- **Stalled:** >14 days in current stage
- **Connectivity Issues:** Sites with offline appliances

**Creating a New Prospect:**
1. Click **"+ New Prospect"** button
2. Enter clinic name (required)
3. Add contact information (optional)
4. Select practice size tier
5. Click **"Add Prospect"**
6. Navigate to site detail page to continue setup

**Phase Filtering:**
- Use filter buttons to show All, Phase 1, or Phase 2 prospects
- Prospects are sorted by days in stage (at-risk first)

**Blockers Alert:**
- Yellow banner shows prospects with active blockers
- Click through to resolve blocker issues

---

### 5. Fleet Page (`/fleet`)

**Purpose:** Monitor all client appliances and their compliance status.

**Features:**
- Client health overview
- Compliance score tracking
- Recent check results
- Quick access to client details

---

### 6. Incidents Page (`/incidents`)

**Purpose:** View and manage automated incident responses.

**Features:**
- List of all incidents with severity
- Filter by client, level, resolution status
- View incident details and remediation steps
- Track automated vs manual resolution

**Severity Levels:**
| Level | Color | Description |
|-------|-------|-------------|
| Critical | Red | Immediate action required |
| Warning | Orange | Attention needed soon |
| Info | Blue | Informational only |

---

### 7. Learning Page (`/learning`)

**Purpose:** Monitor the data flywheel and pattern promotion.

**Features:**
- View L2 patterns being tracked
- Promote successful patterns to L1 rules
- Review promotion history
- Monitor learning metrics

---

### 8. Runbooks Page (`/runbooks`)

**Purpose:** Browse and manage remediation runbooks.

**Features:**
- List all available runbooks
- View runbook details and steps
- Check recent executions
- Verify runbook effectiveness

---

## Common Workflows

### Workflow 1: Daily Status Check

1. Navigate to Overview page
2. Check for critical incidents (red indicators)
3. Review offline sites on Sites page
4. Check Onboarding page for at-risk prospects
5. Address any blockers or issues

### Workflow 2: New Client Onboarding

1. Go to Onboarding page
2. Click **"+ New Prospect"**
3. Fill in clinic information
4. Navigate to Site Detail page
5. Add any known credentials
6. Configure appliance with site_id
7. Ship appliance to client
8. Monitor for first phone-home
9. Verify stage progression

### Workflow 3: Troubleshooting Offline Site

1. Navigate to Sites page
2. Filter to show Offline sites
3. Click on affected site
4. Check last checkin timestamp
5. Review appliance information
6. Possible causes:
   - Network issue at client site
   - Appliance powered off
   - Firewall blocking HTTPS
   - DNS resolution failure
7. Use troubleshooting commands from SOP-015

### Workflow 4: Adding Client Credentials

1. Navigate to `/sites/{site_id}`
2. Scroll to Credentials section
3. Click **"+ Add Credential"**
4. Select appropriate type:
   - **Router:** Network equipment access
   - **Active Directory:** Windows domain
   - **EHR:** Electronic Health Record system
   - **Backup:** Backup service credentials
   - **Other:** Custom credentials
5. Enter all required fields
6. Click **"Add Credential"**
7. Verify credential appears in list

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Cmd+K` | Open command palette |
| `g h` | Go to home/overview |
| `g s` | Go to sites |
| `g o` | Go to onboarding |
| `g i` | Go to incidents |
| `?` | Show keyboard shortcuts |

---

## API Quick Reference

### Sites API

```bash
# List all sites
curl https://api.osiriscare.net/api/sites

# Get site details
curl https://api.osiriscare.net/api/sites/{site_id}

# Create site
curl -X POST https://api.osiriscare.net/api/sites \
  -H "Content-Type: application/json" \
  -d '{"clinic_name": "Acme Dental", "tier": "mid"}'

# Update site stage
curl -X PUT https://api.osiriscare.net/api/sites/{site_id} \
  -H "Content-Type: application/json" \
  -d '{"onboarding_stage": "shipped"}'
```

### Onboarding API

```bash
# Get pipeline data
curl https://api.osiriscare.net/api/onboarding/pipeline

# Get metrics
curl https://api.osiriscare.net/api/onboarding/metrics
```

---

## Troubleshooting

### Dashboard Not Loading

1. Check API health: `curl https://api.osiriscare.net/health`
2. Clear browser cache
3. Check browser console for errors
4. Verify network connectivity

### Data Not Updating

1. Check if using stale browser tab
2. Hard refresh: `Cmd+Shift+R`
3. Check API response times
4. Verify database connectivity

### Create Site Fails

1. Check for required fields (clinic_name)
2. Verify API connectivity
3. Check browser console for error details
4. Review server logs if needed

---

## Related Documents

- [SOP-015: Sites & Appliance Management](SOP-015_SITES_APPLIANCE_MANAGEMENT.md)
- [SOP-010: Client Onboarding](../CLIENT_ONBOARDING_SOP.md)
- [ARCHITECTURE.md](../ARCHITECTURE.md)

---

**End of Manual**

**Document Version:** 1.0
**Last Updated:** 2025-12-31
**Next Review:** 2026-03-31
**Owner:** MSP Operations Team

**Change Log:**
- 2025-12-31: v1.0 - Initial version created

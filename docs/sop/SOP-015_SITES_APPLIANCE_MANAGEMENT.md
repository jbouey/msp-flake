# SOP-015: Sites & Appliance Management

**Document Type:** Standard Operating Procedure (SOP)
**Version:** 1.0
**Last Updated:** 2025-12-31
**Owner:** MSP Operations Team
**Review Cycle:** Monthly
**Classification:** Level 2 (Operational)

---

## Purpose

This SOP defines procedures for managing client sites and appliances through the Central Command dashboard, including site creation, appliance monitoring, credential storage, and troubleshooting.

---

## Scope

**Applies to:**
- Creating and managing client sites
- Monitoring appliance phone-home status
- Storing and managing encrypted credentials
- Troubleshooting connectivity issues

**Does NOT apply to:**
- Client onboarding workflow (see SOP: CLIENT_ONBOARDING_SOP.md)
- Evidence bundle generation
- Incident response

---

## Prerequisites

- Access to Central Command dashboard: https://dashboard.osiriscare.net
- Admin or Operator role credentials
- Understanding of onboarding pipeline stages

---

## Production URLs

| Service | URL | Purpose |
|---------|-----|---------|
| Dashboard | https://dashboard.osiriscare.net | Central Command UI |
| API | https://api.osiriscare.net | REST API |
| Health Check | https://api.osiriscare.net/health | System status |

---

## Procedure 1: Creating a New Site

### Step 1.1: Access Sites Page

1. Navigate to https://dashboard.osiriscare.net
2. Log in with your credentials
3. Click **"Sites"** in the sidebar navigation
4. Verify you see the Sites list view

### Step 1.2: Create Site

1. Click **"+ New Site"** button (top right)
2. Fill in required fields:
   - **Clinic Name**: Full legal name of the practice
   - **Contact Name**: Primary IT or admin contact
   - **Contact Email**: Email for notifications
   - **Tier**: Select based on practice size
     - Small: 1-5 providers ($200-400/mo)
     - Mid: 6-15 providers ($600-1200/mo)
     - Large: 15-50 providers ($1500-3000/mo)
3. Click **"Create Site"**
4. Note the generated `site_id` (e.g., `acme-dental-a1b2c3`)

### Step 1.3: Verify Creation

1. Site appears in Sites list with status "Pending"
2. Navigate to Site Detail page (`/sites/{site_id}`)
3. Verify contact information is correct
4. Note the site_id for appliance configuration

---

## Procedure 2: Monitoring Appliance Status

### Step 2.1: Understanding Status Indicators

| Status | Indicator | Meaning | Action Required |
|--------|-----------|---------|-----------------|
| Online | Green | Checkin < 5 min ago | None |
| Stale | Yellow | Checkin 5-15 min ago | Monitor |
| Offline | Red | Checkin > 15 min ago | Investigate |
| Pending | Gray | Never connected | Await first checkin |

### Step 2.2: Checking Site Status

1. Navigate to Sites page (`/sites`)
2. Review status column for all sites
3. Use filter tabs to show only specific statuses
4. Click on a site to view detailed information

### Step 2.3: Investigating Offline Sites

If a site shows "Offline" status:

1. Navigate to Site Detail page
2. Check last checkin timestamp
3. Review appliance information (IP addresses, version)
4. Possible causes:
   - Network connectivity issue at client site
   - Appliance powered off or crashed
   - Firewall blocking outbound HTTPS
   - DNS resolution failure

**Troubleshooting Commands:**

```bash
# Check API health
curl https://api.osiriscare.net/health

# Get site details
curl https://api.osiriscare.net/api/sites/{site_id}

# View recent checkins (check server logs)
ssh root@178.156.162.116 "docker logs mcp-server --since 1h | grep {site_id}"
```

---

## Procedure 3: Managing Credentials

### Step 3.1: Adding Credentials

1. Navigate to Site Detail page (`/sites/{site_id}`)
2. Scroll to Credentials section
3. Click **"+ Add Credential"**
4. Select credential type:
   - **Router**: Network device access
   - **Active Directory**: Windows domain credentials
   - **EHR**: Electronic Health Record system
   - **Backup**: Backup service credentials
   - **Other**: Custom credentials
5. Enter credential details:
   - **Name**: Descriptive name (e.g., "Main Router")
   - **Host/IP**: Target system address
   - **Username**: Login username
   - **Password**: Login password
6. Click **"Add Credential"**

### Step 3.2: Credential Security

- All credentials are encrypted using Fernet symmetric encryption
- Encryption key is stored securely on the VPS
- Credentials are never logged or exposed in API responses
- Only the appliance can retrieve credentials (via secure channel)

### Step 3.3: Removing Credentials

Currently, credentials must be removed via API:

```bash
# List credentials (shows ID, type, name only)
curl https://api.osiriscare.net/api/sites/{site_id}

# Remove credential (requires direct database access)
ssh root@178.156.162.116 "docker exec -it mcp-postgres psql -U mcp -c \"DELETE FROM site_credentials WHERE id = '{credential_id}'\""
```

---

## Procedure 4: Appliance Phone-Home Configuration

### Step 4.1: Appliance Configuration

The appliance must be configured with the correct `site_id` before deployment:

```nix
# In appliance NixOS configuration
{
  services.msp-agent = {
    enable = true;
    siteId = "acme-dental-a1b2c3";  # From Central Command
    mcpServer = "https://api.osiriscare.net";
    checkinInterval = 60;  # seconds
  };
}
```

### Step 4.2: First Checkin

When the appliance first phones home:

1. API receives POST to `/api/appliances/checkin`
2. If site_id exists, appliance is registered
3. Site status updates to "Online"
4. Site stage advances to "Connectivity"
5. Dashboard shows appliance details:
   - Hostname
   - MAC address
   - IP addresses
   - Agent version
   - NixOS version
   - Uptime

### Step 4.3: Ongoing Checkins

Every 60 seconds:
1. Appliance sends checkin with current system info
2. `last_checkin` timestamp updates
3. `live_status` recalculates
4. Dashboard reflects real-time status

---

## Procedure 5: Onboarding Stage Management

### Stage Progression

Sites progress through these stages:

```
Lead → Discovery → Proposal → Contract → Intake → Credentials →
Shipped → Received → Connectivity → Scanning → Baseline → Active
```

### Automatic Stage Updates

| Stage | Trigger |
|-------|---------|
| Connectivity | First appliance checkin |
| Scanning | Network discovery complete |
| Baseline | Baseline configuration applied |

### Manual Stage Updates

Some stages require manual advancement via API:

```bash
# Advance to shipped stage
curl -X PUT https://api.osiriscare.net/api/sites/{site_id} \
  -H "Content-Type: application/json" \
  -d '{"onboarding_stage": "shipped", "tracking_number": "1Z999AA10123456784"}'

# Mark as active
curl -X PUT https://api.osiriscare.net/api/sites/{site_id} \
  -H "Content-Type: application/json" \
  -d '{"onboarding_stage": "active"}'
```

---

## Procedure 6: Troubleshooting

### Issue: Site Not Appearing After Creation

**Symptoms:** Created site not visible in Sites list

**Resolution:**
1. Refresh the page (Cmd+R)
2. Check browser console for errors
3. Verify API is healthy: `curl https://api.osiriscare.net/health`
4. Check server logs: `ssh root@178.156.162.116 "docker logs mcp-server --tail 50"`

### Issue: Appliance Not Checking In

**Symptoms:** Site status stuck at "Pending" or "Offline"

**Resolution:**
1. Verify appliance network connectivity
2. Check DNS resolution: `nslookup api.osiriscare.net`
3. Test HTTPS connectivity: `curl -v https://api.osiriscare.net/health`
4. Check appliance service: `systemctl status msp-agent`
5. Review appliance logs: `journalctl -u msp-agent -n 100`

### Issue: Credentials Not Saving

**Symptoms:** Credential add fails or credentials missing

**Resolution:**
1. Check for JavaScript errors in browser console
2. Verify API connectivity
3. Check server logs for encryption errors
4. Ensure Fernet key is configured: `ssh root@178.156.162.116 "docker exec mcp-server env | grep FERNET"`

---

## API Reference

### Sites Endpoints

```bash
# List all sites
GET /api/sites
GET /api/sites?status=online

# Get site details
GET /api/sites/{site_id}

# Create site
POST /api/sites
{
  "clinic_name": "Acme Dental",
  "contact_name": "Dr. Smith",
  "contact_email": "smith@acme.com",
  "tier": "mid"
}

# Update site
PUT /api/sites/{site_id}
{
  "onboarding_stage": "shipped",
  "tracking_number": "1Z999..."
}
```

### Appliance Endpoints

```bash
# Appliance checkin
POST /api/appliances/checkin
{
  "site_id": "acme-dental-a1b2c3",
  "mac_address": "aa:bb:cc:dd:ee:ff",
  "hostname": "msp-appliance-01",
  "ip_addresses": ["192.168.1.100"],
  "agent_version": "1.0.0",
  "nixos_version": "24.05",
  "uptime_seconds": 86400
}
```

### Credential Endpoints

```bash
# Add credential
POST /api/sites/{site_id}/credentials
{
  "credential_type": "router",
  "credential_name": "Main Router",
  "username": "admin",
  "password": "secret",
  "host": "192.168.1.1"
}
```

---

## HIPAA Considerations

- **Credentials:** Encrypted at rest using Fernet
- **PHI:** No PHI is processed or stored
- **Audit Trail:** All API calls logged
- **Access Control:** Role-based dashboard access

---

## Related Documents

- [CLIENT_ONBOARDING_SOP.md](../CLIENT_ONBOARDING_SOP.md) - Full onboarding workflow
- [ONBOARDING_QUICK_REFERENCE.md](../ONBOARDING_QUICK_REFERENCE.md) - Quick reference
- [NETWORK.md](../../.agent/NETWORK.md) - Network topology

---

**End of SOP**

**Document Version:** 1.0
**Last Updated:** 2025-12-31
**Next Review:** 2026-01-31
**Owner:** MSP Operations Team

**Change Log:**
- 2025-12-31: v1.0 - Initial version created

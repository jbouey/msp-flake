# Partner Dashboard User Guide

**Last Updated:** 2026-01-24 (Session 68)
**Version:** 2.0
**URL:** https://dashboard.osiriscare.net/partner/login

---

## Overview

The Partner Dashboard provides MSP partners with a self-service portal to manage their OsirisCare compliance deployments. Partners can view client sites, generate provisioning codes, manage credentials, configure notifications, and track escalation tickets.

---

## Authentication Methods

Partners can authenticate via two methods:

### 1. Google/Microsoft OAuth (Recommended)

1. Navigate to https://dashboard.osiriscare.net/partner/login
2. Click **"Sign in with Google"** or **"Sign in with Microsoft"**
3. Complete the OAuth flow with your business account
4. **First-time OAuth users require admin approval** before accessing the dashboard

### 2. API Key

1. Navigate to https://dashboard.osiriscare.net/partner/login
2. Enter your API key in the text field
3. Click **"Sign In"**

**API Key Format:** `osk_xxxxxxxxxxxxxxxxxxxxxxxxxxxx`

---

## Dashboard Sections

### Header

| Element | Description |
|---------|-------------|
| Partner Logo | Your brand logo (if configured) |
| Partner Name | Your organization name |
| Logout Button | Sign out of the dashboard |

### Main Content

#### Sites Overview

Displays all client sites assigned to your partner account.

| Column | Description |
|--------|-------------|
| Site Name | Client clinic/practice name |
| Status | Online (green), Stale (yellow), Offline (red), Pending (gray) |
| Appliances | Number of connected appliances |
| Compliance Score | Overall compliance percentage |
| Last Check-in | Time since last appliance communication |

**Status Definitions:**

| Status | Meaning | Check-in Age |
|--------|---------|--------------|
| Online | Appliance actively reporting | < 5 minutes |
| Stale | Appliance slow to report | 5-60 minutes |
| Offline | Appliance not communicating | > 60 minutes |
| Pending | Never connected | Never |

#### Site Detail View

Click any site row to expand details:

- **Contact Information:** Primary contact name, email, phone
- **Appliances:** List of appliances with version, IP, last check-in
- **Credentials:** Stored credentials for this site (masked)
- **Compliance Checks:** Recent check results with pass/fail status

---

## Provisioning Codes

Provisioning codes allow appliances to auto-register with your partner account.

### Creating a Provision Code

1. Click **"+ New Provision Code"** button
2. Enter client details:
   - **Client Name:** Practice/clinic name (required)
   - **Contact Email:** Primary contact (optional)
   - **Contact Phone:** Primary phone (optional)
   - **Practice Tier:** Small (1-5 providers), Mid (6-15), Large (16+)
3. Click **"Create Code"**
4. A 16-character code and QR code are generated

### Using Provision Codes

**Method 1: QR Code**
1. Print or display the QR code
2. During appliance first boot, scan with mobile device
3. Appliance auto-registers with your account

**Method 2: Manual Entry**
1. During appliance first boot, select "Enter Code Manually"
2. Type the 16-character provision code
3. Appliance registers with your account

### Managing Provision Codes

| Action | Description |
|--------|-------------|
| View QR | Display full-size QR code for printing |
| Copy Code | Copy 16-character code to clipboard |
| Revoke | Invalidate unused provision code |

**Note:** Provision codes expire after 30 days if unused.

---

## Credentials Management

Store encrypted credentials for client sites to enable automated compliance checks.

### Credential Types

| Type | Use Case |
|------|----------|
| WinRM | Windows Remote Management for servers |
| Domain Admin | Active Directory domain credentials |
| Local Admin | Local administrator accounts |
| SSH Password | Linux/Unix SSH access |
| SSH Key | SSH private key authentication |
| Service Account | Application service credentials |

### Adding Credentials

1. Navigate to site detail
2. Click **"+ Add Credential"**
3. Select credential type
4. Enter details:
   - **Name:** Descriptive label
   - **Hostname/IP:** Target system
   - **Username:** Account username
   - **Password/Key:** Secret (encrypted at rest)
5. Click **"Save Credential"**

### Validating Credentials

1. Click the **"Test"** button next to any credential
2. System attempts to connect using the credential
3. Results show success or failure with details

**Security Note:** Credentials are encrypted using AES-256 and never exposed in API responses.

---

## Notification Settings

Configure how you receive L3 escalation alerts from OsirisCare.

### Notification Channels

| Channel | Description | Requirements |
|---------|-------------|--------------|
| Email | Email alerts | Valid email address |
| Slack | Slack channel messages | Webhook URL |
| PagerDuty | PagerDuty incidents | Integration key |
| Microsoft Teams | Teams channel messages | Webhook URL |
| Webhook | Custom HTTP POST | Endpoint URL |

### Configuring Channels

1. Navigate to **Notification Settings** (gear icon)
2. Enable desired channels
3. Enter required configuration:
   - **Email:** Comma-separated email addresses
   - **Slack:** Incoming webhook URL
   - **PagerDuty:** Integration key (Events API v2)
   - **Teams:** Incoming webhook URL
   - **Webhook:** HTTPS endpoint URL
4. Click **"Test"** to verify configuration
5. Click **"Save Settings"**

### Site-Level Overrides

Override notification routing for specific sites:

1. Click site row to expand
2. Click **"Notification Overrides"**
3. Configure site-specific channels
4. Useful for routing critical clients to PagerDuty

---

## Escalation Tickets

L3 escalations from appliances that require human attention.

### Ticket List

| Column | Description |
|--------|-------------|
| Site | Affected client site |
| Category | Type of issue (security, compliance, system) |
| Severity | Critical, High, Medium, Low |
| Status | Open, Acknowledged, Resolved |
| Created | When the escalation was raised |
| SLA | Time remaining before SLA breach |

### Managing Tickets

**Acknowledge:**
1. Click ticket row
2. Click **"Acknowledge"**
3. Optionally add notes
4. Ticket moves to "Acknowledged" status

**Resolve:**
1. Click ticket row
2. Click **"Resolve"**
3. Enter resolution notes (required)
4. Select resolution category
5. Ticket moves to "Resolved" status

### SLA Definitions

Default SLA response/resolution times:

| Severity | Response SLA | Resolution SLA |
|----------|--------------|----------------|
| Critical | 15 minutes | 4 hours |
| High | 1 hour | 8 hours |
| Medium | 4 hours | 24 hours |
| Low | 8 hours | 72 hours |

---

## Revenue & Billing

Track your revenue share from OsirisCare deployments.

### Revenue Dashboard

| Metric | Description |
|--------|-------------|
| Active Sites | Sites currently generating revenue |
| Monthly Revenue | Your share of monthly recurring |
| Revenue Share % | Your configured percentage (default 40%) |
| Pending Payout | Amount due in next payout cycle |

### Invoice History

View past invoices and payment status.

---

## API Access

Access the Partner API programmatically using your API key.

### Authentication

```bash
curl -H "X-API-Key: osk_your_api_key_here" \
  https://api.osiriscare.net/api/partners/me
```

### Common Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/partners/me` | GET | Get partner info |
| `/api/partners/me/sites` | GET | List sites |
| `/api/partners/me/provisions` | GET | List provision codes |
| `/api/partners/me/provisions` | POST | Create provision code |
| `/api/partners/me/escalations` | GET | List escalation tickets |

See [Partner API Documentation](./README.md) for complete endpoint reference.

---

## Troubleshooting

### Cannot Log In

1. **OAuth:** Ensure you're using a business Google/Microsoft account
2. **API Key:** Verify key is correct (starts with `osk_`)
3. **New OAuth User:** Contact OsirisCare admin for approval
4. **Session Expired:** Refresh page and re-authenticate

### Site Shows Offline

1. Check appliance power and network at client site
2. Verify client firewall allows outbound HTTPS (443)
3. Check DNS resolution at client site
4. Review appliance logs: `journalctl -u appliance-daemon -n 100`

### Credential Validation Fails

1. Verify hostname/IP is correct
2. Check username format (DOMAIN\user vs user@domain.com)
3. Ensure password hasn't expired
4. Verify target system allows remote access
5. Check firewall rules (WinRM: 5985/5986, SSH: 22)

### Notifications Not Received

1. Test the notification channel in settings
2. Check webhook URL is accessible from internet
3. Verify email addresses are correct
4. Check spam/junk folders for email
5. Review Slack/Teams channel permissions

---

## Support

For partner support:

- **Email:** partners@osiriscare.net
- **Phone:** (570) 555-0100
- **Portal:** https://support.osiriscare.net

---

## Quick Reference

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Cmd+K` / `Ctrl+K` | Open command palette |
| `Esc` | Close modal/dialog |

### Status Colors

| Color | Meaning |
|-------|---------|
| Green | Healthy/Online/Pass |
| Yellow | Warning/Stale |
| Red | Critical/Offline/Fail |
| Gray | Pending/Unknown |
| Blue | Informational |

### URLs

| Resource | URL |
|----------|-----|
| Partner Login | https://dashboard.osiriscare.net/partner/login |
| Partner Dashboard | https://dashboard.osiriscare.net/partner/dashboard |
| API Base | https://api.osiriscare.net |

---

**Document Version:** 2.0
**Last Updated:** 2026-01-24
**Owner:** OsirisCare Partner Operations

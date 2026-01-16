# Central Command User Guide

## Malachor MSP Compliance Platform

**Version:** 1.2.0
**Last Updated:** January 16, 2026
**Agent Version:** v1.0.37
**ISO Version:** v37

---

## Table of Contents

1. [Getting Started](#getting-started)
2. [Dashboard Overview](#dashboard-overview)
3. [Fleet Management](#fleet-management)
4. [Runbook Library](#runbook-library)
5. [Go Agents (Workstation Compliance)](#go-agents-workstation-compliance)
6. [Zero-Friction Deployment](#zero-friction-deployment)
7. [Audit Logs](#audit-logs)
8. [User Administration](#user-administration)

---

## Getting Started

### Accessing Central Command

Central Command is accessed via web browser at your organization's designated URL (e.g., `http://your-server:3000`).

### Login

1. Navigate to the Central Command URL
2. Enter your credentials:
   - **Username:** Your assigned username
   - **Password:** Your assigned password
3. Click "Sign In"

**Default Administrator Account:**
- Username: `admin`
- Password: Set via environment variable `ADMIN_INITIAL_PASSWORD` or `admin123` for development

> **Important:** Change the default password immediately after first login. Use the Users page to invite additional administrators with secure email-based password setup.

### Dashboard Layout

The interface consists of:
- **Sidebar** (left): Navigation menu and client list
- **Header** (top): Page title, search, refresh, and user info
- **Main Content** (center): Current page content

---

## Dashboard Overview

The Dashboard provides a real-time overview of your MSP fleet health and compliance status.

### Key Metrics

| Metric | Description |
|--------|-------------|
| Total Clients | Number of active client sites |
| Avg Compliance | Average HIPAA compliance score across all clients |
| Incidents (24h) | Number of compliance incidents in the last 24 hours |
| L1 Resolution | Percentage of incidents resolved automatically (L1) |

### Health Scoring

Health scores are calculated using:
- **Connectivity (40%):** Check-in freshness, healing success, order execution
- **Compliance (60%):** Patching, antivirus, backup, logging, firewall, encryption

**Status Thresholds:**
- **Healthy (Green):** 80-100%
- **Warning (Orange):** 40-79%
- **Critical (Red):** 0-39%

### Fleet Overview

Displays all client sites as cards showing:
- Client name and appliance count
- Overall health gauge
- Recent incident count

Click any client card to view detailed information.

### Recent Incidents

Shows the latest compliance incidents with:
- Timestamp
- Client/Host information
- Check type (Patch, AV, Backup, etc.)
- Resolution level (L1, L2, L3)
- Status (Active/Resolved)

---

## Fleet Management

### Client List

The sidebar displays all clients with health status indicators:
- **Green dot:** Healthy
- **Orange dot:** Warning
- **Red dot:** Critical

Click a client name to navigate to their detail page.

### Client Detail Page

Shows comprehensive information for a single client:
- Appliance inventory with individual health scores
- Compliance breakdown by check type
- Recent incidents for this client
- Historical trends

---

## Runbook Library

Runbooks are automated remediation playbooks that resolve compliance issues.

### Viewing Runbooks

Navigate to **Runbooks** in the sidebar to see all available runbooks.

### Runbook Information

Each runbook card displays:
- **ID:** Unique identifier (e.g., RB-WIN-PATCH-001)
- **Name:** Descriptive name
- **Level:** L1 (Deterministic) or L2 (LLM-assisted)
- **HIPAA Controls:** Mapped compliance requirements
- **Execution Stats:** Count, success rate, average time
- **Disruptive Flag:** Whether execution may cause service interruption

### Filtering Runbooks

Use the filter controls to:
- Search by name, ID, or HIPAA control
- Filter by resolution level (All, L1, L2, L3)

### Runbook Details

Click any runbook card to see:
- Full description
- Execution steps with timeouts
- Configuration parameters
- Recent execution history

---

## Go Agents (Workstation Compliance)

Go Agents provide lightweight, push-based compliance monitoring for Windows workstations at scale.

### Overview

Traditional WinRM polling limits scalability to ~15 workstations per appliance. Go Agents use gRPC to push compliance data, enabling 25-50 workstations per appliance.

### Architecture

```
Windows Workstation         NixOS Appliance
┌─────────────────┐        ┌─────────────────┐
│  Go Agent       │ gRPC   │  Python Agent   │
│  osiris-agent.  │───────>│  Port 50051     │
│  exe            │        │                 │
└─────────────────┘        └─────────────────┘
```

### Compliance Checks

Go Agents perform 6 HIPAA compliance checks:

| Check | Description | HIPAA Control |
|-------|-------------|---------------|
| BitLocker | Volume encryption enabled | 164.312(a)(2)(iv) |
| Defender | Real-time protection active | 164.308(a)(5)(ii)(B) |
| Firewall | All profiles (Domain/Private/Public) enabled | 164.312(e)(1) |
| Patches | Windows Update compliance | 164.308(a)(1)(ii)(A) |
| ScreenLock | Screen timeout ≤ 600 seconds | 164.312(a)(2)(iii) |
| RMM | Third-party RMM detection | - |

### Viewing Go Agents

Navigate to a site's detail page to see connected Go Agents:
- Agent hostname
- Last heartbeat
- Compliance status
- Check results

### Deployment

Go Agents are deployed to workstations via:
1. WinRM push from appliance
2. Manual installation
3. Group Policy deployment

**Binary location:** `C:\OsirisCare\osiris-agent.exe`
**Config location:** `C:\ProgramData\OsirisCare\config.json`

### Current Limitations

- gRPC streaming not yet implemented (agents report via heartbeat)
- SQLite offline queue requires CGO (currently disabled)

---

## Zero-Friction Deployment

Zero-friction deployment automates the entire site onboarding process with a single credential entry.

### Overview

Traditional deployment requires manually entering each server and workstation. Zero-friction deployment:
1. Automatically discovers the AD domain
2. Enumerates all computers from Active Directory
3. Configures targets automatically
4. Starts compliance scanning immediately

**Human touchpoints:** 1 (domain credential entry only)

### Deployment Flow

```
1. Boot Appliance
   └─> Discovers AD domain via DNS SRV records
   └─> Reports to Central Command
   └─> Partner receives notification

2. Enter Credentials (Partner Dashboard)
   └─> Partner enters one domain admin credential
   └─> Triggers enumeration

3. Automatic Enumeration
   └─> Appliance runs Get-ADComputer
   └─> Discovers all servers and workstations
   └─> Updates windows_targets automatically

4. Compliance Scanning
   └─> First scan runs immediately
   └─> Evidence uploaded to Central Command
```

### Partner Actions

1. **Receive Notification:** When appliance discovers a domain, you'll receive an email/notification
2. **Enter Credentials:** Navigate to the site and enter domain admin credentials
3. **Monitor Progress:** Watch enumeration results populate automatically
4. **Review Targets:** Verify discovered servers/workstations are correct

### Viewing Discovery Results

Navigate to Site Detail → Discovered Domain to see:
- Domain name and controllers
- Discovered servers with online status
- Discovered workstations
- Enumeration timestamp

### Benefits

| Benefit | Description |
|---------|-------------|
| Zero manual entry | All targets discovered automatically |
| Single credential | One domain admin credential enables full deployment |
| Fast deployment | First compliance report within 1 hour |
| Non-destructive | Discovered targets merge with manual configs |

---

## Audit Logs

Audit logs track all user actions for accountability and compliance.

### Accessing Audit Logs

Navigate to **Audit Logs** in the sidebar (Admin only).

### Log Information

Each log entry includes:
- **Timestamp:** When the action occurred
- **User:** Who performed the action
- **Action:** Type of action (LOGIN, VIEW, REFRESH, etc.)
- **Target:** What was affected
- **Details:** Additional context

### Action Types

| Action | Description |
|--------|-------------|
| LOGIN | User signed into the system |
| LOGOUT | User signed out |
| VIEW | User viewed a page or resource |
| REFRESH | User manually refreshed data |
| CREATE | New resource created |
| UPDATE | Resource modified |
| DELETE | Resource removed |
| EXECUTE | Runbook or command executed |

### Filtering Logs

Use the filter controls to:
- Search by target or details
- Filter by action type
- Filter by user

### Exporting Logs

Administrators can export logs to CSV:
1. Click "Export CSV" button
2. File downloads with timestamp in filename
3. Use for compliance audits or analysis

---

## User Administration

### User Roles

Central Command uses a three-tier Role-Based Access Control (RBAC) system:

| Role | View | Execute Actions | Manage Users | Audit Logs |
|------|------|-----------------|--------------|------------|
| Admin | ✅ All | ✅ All | ✅ Yes | ✅ Yes |
| Operator | ✅ All | ✅ Yes | ❌ No | ❌ No |
| Readonly | ✅ All | ❌ No | ❌ No | ❌ No |

**Admin:** Full access to all features including user management, partners, and audit logs.

**Operator:** Can view dashboards, execute runbooks, acknowledge alerts, but cannot manage users or view audit logs.

**Readonly:** Can only view dashboards and data. Cannot execute any actions.

### Managing Users (Admin Only)

Navigate to **Users** in the sidebar to access user management.

#### Inviting New Users

1. Click the "Invite User" button
2. Enter the user's email address
3. Enter their display name
4. Select their role (Admin, Operator, or Readonly)
5. Click "Send Invite"

The user will receive an email with a link to set their password.

#### Pending Invites

The "Pending Invites" tab shows all outstanding invitations with options to:
- **Resend:** Send the invite email again
- **Revoke:** Cancel the invitation

#### Managing Existing Users

Click any user row to:
- Change their role
- Activate/deactivate their account
- Send a password reset email

### Password Management

#### Changing Your Password

1. Navigate to Users page
2. Find your account
3. Click "Reset Password"
4. You'll receive an email with a password reset link

#### Setting Password (New Users)

When you receive an invite email:
1. Click the link in the email
2. Enter your new password (minimum 8 characters)
3. Confirm your password
4. Click "Create Account"
5. You'll be redirected to log in

### Signing Out

1. Click the logout icon (arrow) in the bottom-left sidebar
2. You will be returned to the login screen

### Session Management

- Sessions persist across browser refreshes
- Sessions are stored in the database (not browser-only)
- Session tokens expire after 7 days of inactivity

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `/` | Focus search box |
| `Esc` | Close modals/dialogs |

---

## Troubleshooting

### Cannot Log In

1. Verify username and password are correct
2. Check caps lock is not enabled
3. Clear browser cache and try again
4. Contact administrator if issue persists

### Data Not Loading

1. Check network connectivity
2. Click the refresh button in the header
3. Wait 30 seconds for auto-refresh
4. Contact administrator if issue persists

### Slow Performance

1. Clear browser cache
2. Close unused browser tabs
3. Check network connection speed

---

## Support

For technical support, contact your system administrator or refer to the internal IT helpdesk.

---

*Document generated for Malachor MSP Compliance Platform*
*Central Command Dashboard v1.0.0*

# Client Portal User Guide

**Last Updated:** 2026-01-24 (Session 68)
**Version:** 1.0
**URL:** https://dashboard.osiriscare.net/client/login

---

## Overview

The OsirisCare Client Portal provides healthcare practices with direct access to their compliance data. View your compliance scores, download audit evidence, and manage your account settings.

---

## Getting Started

### Accessing the Portal

1. Visit https://dashboard.osiriscare.net/client/login
2. Enter your email address
3. Click **"Send Magic Link"**
4. Check your email for the login link (valid 60 minutes)
5. Click the link to access your dashboard

**Optional:** After first login, you can set a password in Settings for convenience.

---

## Dashboard

### Compliance Score

The main dashboard shows your organization's overall compliance score:

| Score Range | Status | Meaning |
|-------------|--------|---------|
| 90-100% | Excellent | Fully compliant |
| 75-89% | Good | Minor issues to address |
| 50-74% | Fair | Multiple compliance gaps |
| Below 50% | Critical | Immediate action required |

### Quick Stats

| Metric | Description |
|--------|-------------|
| Active Sites | Number of locations being monitored |
| Passing Controls | Number of HIPAA controls currently passing |
| Evidence Bundles | Total compliance evidence generated |
| Days Since Last Issue | Consecutive days without critical findings |

### Quick Links

| Link | Destination |
|------|-------------|
| Evidence Archive | Download compliance evidence bundles |
| Monthly Reports | Access monthly compliance reports |
| Notifications | View system alerts |
| Help & Docs | How-to guides and FAQs |

---

## Evidence Archive

### Understanding Evidence Bundles

Evidence bundles are cryptographically signed compliance records that prove your organization's security posture at a specific point in time.

**Each bundle contains:**
- Compliance check results
- System configuration snapshots
- Timestamp (timestamped by external authority)
- Digital signature (Ed25519)
- Hash chain link (blockchain-style integrity)

### Downloading Evidence

1. Navigate to **Evidence Archive**
2. Browse bundles by date or check type
3. Click **"Download"** on any bundle
4. Bundle downloads as signed JSON or PDF

### Evidence Chain Verification

Each evidence bundle is linked to previous bundles via cryptographic hash, creating an immutable chain:

```
Bundle 1 → Bundle 2 → Bundle 3 → ...
  hash₁  ←   prev    ←   prev
```

This proves evidence has not been tampered with since creation.

### What to Tell Your Auditor

When presenting evidence to HIPAA auditors:

1. **Explain the chain:** "Each evidence bundle contains a hash of the previous bundle, creating a verifiable chain that proves no tampering."

2. **Show the signature:** "Every bundle is signed with Ed25519 digital signatures, cryptographically proving authenticity."

3. **Demonstrate timestamps:** "External timestamp authorities verify when each bundle was created."

4. **Provide verification:** "Auditors can independently verify the hash chain and signatures using standard cryptographic tools."

---

## Monthly Reports

### Report Contents

Monthly compliance reports include:

| Section | Content |
|---------|---------|
| Executive Summary | Overall compliance status |
| Control Status | Per-control pass/fail rates |
| Incident History | Any compliance incidents |
| Remediation Log | Actions taken to fix issues |
| Trend Analysis | Month-over-month comparison |

### Downloading Reports

1. Navigate to **Monthly Reports**
2. Select the month
3. Click **"Download PDF"**
4. Report ready for auditor presentation

### Annual Reports

Annual summaries compile all monthly data for yearly audit preparation.

---

## Notifications

### Notification Types

| Type | Description |
|------|-------------|
| Compliance Alert | New compliance finding |
| Evidence Ready | New evidence bundle available |
| Report Ready | Monthly report generated |
| System Notice | Platform announcements |

### Managing Notifications

- Click any notification to view details
- Mark individual or all as read
- Notifications link to relevant dashboard sections

---

## Settings

### Password

Set an optional password for login convenience:

1. Navigate to **Settings**
2. Click **Password** tab
3. Enter new password (12+ characters)
4. Confirm password
5. Click **"Set Password"**

After setting a password, you can log in with email + password instead of magic links.

### Users (Organization Admins Only)

Manage who can access your portal:

**Roles:**

| Role | Permissions |
|------|-------------|
| Owner | Full access, user management |
| Admin | Full access, no user management |
| Viewer | Read-only access |

**Inviting Users:**

1. Navigate to **Settings → Users**
2. Click **"Invite User"**
3. Enter email address
4. Select role
5. Click **"Send Invite"**

### Transfer Provider (Owner Only)

If you need to switch MSP providers:

1. Navigate to **Settings → Transfer Provider**
2. Click **"Request Transfer"**
3. Enter new provider details
4. Submit request
5. OsirisCare will coordinate the transition

**Important:** Your compliance data follows you to any new provider.

---

## HIPAA Controls Reference

Your compliance monitoring covers these HIPAA Security Rule controls:

| Control | Description |
|---------|-------------|
| 164.312(a)(1) | Access Control |
| 164.312(a)(2)(i) | Unique User Identification |
| 164.312(a)(2)(ii) | Emergency Access Procedure |
| 164.312(a)(2)(iii) | Automatic Logoff |
| 164.312(a)(2)(iv) | Encryption and Decryption |
| 164.312(b) | Audit Controls |
| 164.312(c)(1) | Integrity Controls |
| 164.312(c)(2) | Authentication |
| 164.312(d) | Person or Entity Authentication |
| 164.312(e)(1) | Transmission Security |
| 164.312(e)(2)(i) | Integrity Controls for Transmission |
| 164.312(e)(2)(ii) | Encryption for Transmission |

---

## Getting Help

### In-Portal Help

Click **Help & Docs** in the dashboard for:
- Step-by-step guides
- FAQ answers
- Auditor preparation tips
- Contact information

### Support Contact

- **Email:** support@osiriscare.net
- **Phone:** (570) 555-0100
- **Hours:** Monday-Friday, 8am-6pm EST

### For Auditors

Direct auditor inquiries to: auditors@osiriscare.net

We can provide:
- Technical documentation
- Verification procedures
- Independent attestation

---

## Security & Privacy

### Your Data

- All data encrypted at rest (AES-256)
- All connections encrypted in transit (TLS 1.3)
- No PHI stored in evidence bundles
- Data retained for 7 years per HIPAA requirements

### Session Security

- Magic links expire after 60 minutes
- Sessions expire after 30 days
- All access logged for audit trail

### Compliance

OsirisCare is:
- HIPAA compliant
- SOC 2 Type II certified
- Business Associate Agreement available

---

## Frequently Asked Questions

**Q: How often is compliance checked?**
A: Continuous monitoring with checks running every 5-15 minutes.

**Q: Can I share reports with my auditor?**
A: Yes, all reports and evidence bundles are designed for auditor presentation.

**Q: What if I find an error in my compliance data?**
A: Contact support immediately. We'll investigate and correct any issues.

**Q: How long is evidence retained?**
A: 7 years, per HIPAA requirements.

**Q: Can I export all my data?**
A: Yes, contact support for a complete data export.

**Q: What happens if I change providers?**
A: Use the Transfer Provider feature. Your data follows you.

---

**Document Version:** 1.0
**Last Updated:** 2026-01-24
**Owner:** OsirisCare Client Success

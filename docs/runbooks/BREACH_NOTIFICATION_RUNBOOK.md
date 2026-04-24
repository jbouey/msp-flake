# OsirisCare Breach Notification Runbook

**Purpose:** Step-by-step operational guide for responding to a suspected or confirmed data breach. Written for the person on-call at 2am who just got paged.

**Last updated:** 2026-03-11
**Regulation:** HIPAA Breach Notification Rule, 45 CFR §§164.400-414

---

## 1. What Constitutes a Reportable Breach

### HIPAA Definition (45 CFR §164.402)

A breach is the acquisition, access, use, or disclosure of unsecured PHI in a manner not permitted by the Privacy Rule that compromises the security or privacy of the PHI. The burden is on OsirisCare to demonstrate that a breach did NOT occur (the presumption is that it did).

### OsirisCare's PHI Posture

OsirisCare manages **compliance metadata**, not PHI directly. We do not store patient records, clinical data, or insurance information. However, metadata about devices, incidents, and compliance status **may be considered ePHI-adjacent** because it reveals information about a healthcare provider's security posture. A covered entity's risk assessment could classify our data as ePHI if:

- It identifies a specific practice AND exposes their security deficiencies
- Credential data for their systems is exposed
- Compliance scores could be used to target a practice for attack

### Reportable Scenarios Specific to This System

| Scenario | Severity | Why It Matters |
|----------|----------|----------------|
| **Cross-tenant data access (RLS bypass)** | Critical | `tenant_middleware.py` SET LOCAL fails or `app.is_admin` defaults to `true` in an endpoint that should be tenant-scoped. Attacker sees another org's incidents, devices, compliance scores. |
| **IDOR exploit on compliance data** | Critical | Partner or client portal endpoint returns data for a `site_id` the authenticated user does not own. Check `partners.py`, `client_portal.py` ownership validation. |
| **`site_credentials` table exposure** | Critical | Contains WinRM/SSH credentials (some Fernet-encrypted in `password_encrypted`, some JSON in `encrypted_data`). Exposure = full domain admin access to client networks. |
| **Evidence bundle exposure to wrong tenant** | High | `evidence_bundles` table contains hash-chained compliance proof. Cross-tenant access reveals detailed security posture. |
| **Database dump or backup exposure** | Critical | PostgreSQL container `mcp-postgres` backup leaking to public. Contains all tenant data, credentials, audit logs. |
| **Session token theft** | High | `admin_sessions`, partner sessions, client sessions. Tokens are HMAC-SHA256 hashed in DB but raw token in cookie grants full access. |
| **Signing key compromise** | Critical | Ed25519 key at `/app/secrets/signing.key` in mcp-server container. Used for fleet order signing. Compromised key = arbitrary code execution on all appliances. |

### What Is NOT a Reportable Breach

- Failed login attempts (even brute force) that did not succeed
- Vulnerability discovery with no evidence of exploitation
- Internal access by authorized admin users acting within scope
- Appliance checkin data visible to the admin dashboard (that is its purpose)

---

## 2. Detection Triggers

These are the signals that should wake you up. If any of these fire, start this runbook.

### Database-Level Triggers

| Signal | Where to Look | What It Means |
|--------|---------------|---------------|
| `prevent_audit_modification` trigger fires | PostgreSQL logs in `mcp-postgres` container | Someone attempted UPDATE or DELETE on an audit table (`admin_audit_log`, `partner_activity_log`, `update_audit_log`, `exception_audit_log`, `portal_access_log`, `companion_activity_log`). This is blocked by migration 084 triggers. |
| RLS policy violation | PostgreSQL logs, look for `new row violates row-level security policy` | An INSERT/UPDATE tried to write data for a tenant the connection is not scoped to. |
| `app.current_tenant` mismatch | Application logs from `tenant_middleware.py` | `tenant_connection()` was called without a `site_id` or with a mismatched one. |

### Application-Level Triggers

| Signal | Where to Look | What It Means |
|--------|---------------|---------------|
| Auth rate limit exceeded | `admin_audit_log` table: `action = 'LOGIN_BLOCKED'`, `reason = 'account_locked'` | 5+ failed login attempts triggered 15-minute lockout (`auth.py` MAX_FAILED_ATTEMPTS). Could be brute force. |
| CSRF validation failure from unexpected origin | Application logs, `X-CSRF-Token` mismatch | Request origin does not match expected domain. Possible CSRF attack or misconfigured proxy. |
| Evidence chain hash mismatch | `evidence_chain.py` logs, `hmac.compare_digest()` returns false | Tampered evidence bundle. The SHA256 hash chain is broken. Either data corruption or deliberate modification. |
| Anomalous cross-site queries | Slow query log, `pg_stat_statements` | Queries joining across `site_id` boundaries without admin context. |
| Fleet order signature verification failure | Appliance daemon logs (`slog` JSON output) | Someone submitted a fleet order with an invalid Ed25519 signature. Could be a compromised or forged order. |
| Unusual `admin_audit_log` patterns | `SELECT * FROM admin_audit_log WHERE action NOT IN ('LOGIN_SUCCESS','LOGOUT','PAGE_VIEW') ORDER BY created_at DESC` | Look for: bulk data exports, user creation, role changes, credential access outside business hours. |

### Infrastructure-Level Triggers

| Signal | Where to Look |
|--------|---------------|
| Unauthorized SSH access to VPS (178.156.162.116) | `/var/log/auth.log` on VPS |
| Container escape | Docker logs, unexpected processes in `mcp-server` or `mcp-postgres` containers |
| DNS/TLS certificate changes | Certificate Transparency logs for `osiriscare.net` |
| Backup storage access | MinIO audit logs (if WORM storage is active) |

---

## 3. Immediate Steps (First 1 Hour)

**You are the Incident Commander until someone explicitly takes over.**

### Step 1: Confirm and Classify (0-10 minutes)

1. **Do NOT shut anything down yet.** Preserve the running state.
2. Open a new document or incident channel. Record the current UTC time: `date -u`
3. Identify what triggered the alert. Copy the raw alert/log entry verbatim.
4. Classify severity:
   - **Critical**: Active data exfiltration, credential exposure, RLS bypass confirmed
   - **High**: Unauthorized access confirmed but scope unknown
   - **Medium**: Suspicious activity, exploitation not confirmed
5. If Critical or High, proceed immediately to Step 2. If Medium, proceed but notify engineering lead within 1 hour.

### Step 2: Contain (10-20 minutes)

Choose containment actions based on the attack vector:

**If compromised admin/partner/client session:**
```sql
-- On VPS: docker exec -it mcp-postgres psql -U mcp mcp
-- Kill all sessions for the compromised user
DELETE FROM admin_sessions WHERE user_id = '<user_id>';
-- Or for partner sessions:
DELETE FROM partner_sessions WHERE partner_user_id = '<user_id>';
-- Lock the account
UPDATE admin_users SET status = 'disabled', locked_until = NOW() + INTERVAL '30 days' WHERE id = '<user_id>';
```

**If compromised API key:**
```sql
-- Rotate the affected site's API key
UPDATE sites SET api_key = encode(gen_random_bytes(32), 'hex') WHERE site_id = '<site_id>';
```

**If credential table exposure:**
```sql
-- Check what was accessed
SELECT site_id, credential_type, created_at, updated_at
FROM site_credentials ORDER BY updated_at DESC LIMIT 50;
-- Notify affected sites that their Windows/SSH credentials must be rotated
```

**If signing key compromise:**
```bash
# Generate new signing key IMMEDIATELY
ssh root@178.156.162.116
docker exec -it mcp-server python3 -c "
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
key = Ed25519PrivateKey.generate()
with open('/app/secrets/signing.key', 'wb') as f:
    f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
print('New key written. Restart container.')
"
docker restart mcp-server
# All appliances will need to re-sync their public key on next checkin
```

**If RLS bypass:**
```sql
-- Verify RLS is still enabled on all 27 tables
SELECT schemaname, tablename, rowsecurity
FROM pg_tables
WHERE schemaname = 'public' AND rowsecurity = true;
-- Should return 27 rows. If any are missing, re-run migration 080.
```

### Step 3: Preserve Evidence (20-40 minutes)

**Do all of this BEFORE any remediation changes.**

1. **Database snapshot:**
```bash
ssh root@178.156.162.116
docker exec mcp-postgres pg_dump -U mcp mcp | gzip > /root/breach-evidence/db-snapshot-$(date +%Y%m%d-%H%M%S).sql.gz
```

2. **Audit logs export:**
```sql
-- Admin audit log
COPY (SELECT * FROM admin_audit_log WHERE created_at > NOW() - INTERVAL '7 days' ORDER BY created_at) TO '/tmp/admin_audit.csv' CSV HEADER;
-- Partner activity log
COPY (SELECT * FROM partner_activity_log WHERE created_at > NOW() - INTERVAL '7 days' ORDER BY created_at) TO '/tmp/partner_audit.csv' CSV HEADER;
```

3. **Container logs:**
```bash
docker logs mcp-server --since 72h > /root/breach-evidence/mcp-server-$(date +%Y%m%d).log 2>&1
docker logs mcp-postgres --since 72h > /root/breach-evidence/mcp-postgres-$(date +%Y%m%d).log 2>&1
```

4. **Evidence chain integrity check:**
```sql
-- Verify no evidence bundles were tampered with
SELECT site_id, COUNT(*) as bundles,
       COUNT(*) FILTER (WHERE prev_hash IS NOT NULL) as chained
FROM evidence_bundles
GROUP BY site_id;
```

5. **OTS proofs** (if enterprise tier): Export all `.ots` proof files from MinIO WORM storage.

6. **Network artifacts:**
```bash
# Connection logs
ss -tnp > /root/breach-evidence/connections-$(date +%Y%m%d).txt
# Recent auth attempts
docker exec mcp-postgres psql -U mcp mcp -c "
  SELECT username, action, ip_address, created_at
  FROM admin_audit_log
  WHERE action IN ('LOGIN_FAILED','LOGIN_BLOCKED','LOGIN_SUCCESS')
  AND created_at > NOW() - INTERVAL '48 hours'
  ORDER BY created_at DESC;
"
```

### Step 4: Document (40-60 minutes)

Create a breach incident record with:
- **When** the breach was detected (UTC)
- **When** the breach likely started (based on log analysis)
- **What** data was accessed or exposed
- **How** the access occurred (attack vector)
- **Who** is affected (which sites/orgs)
- **What** containment actions were taken and when

---

## 4. Internal Escalation Chain

Escalate in this order. Do NOT skip levels unless someone is unreachable for 15+ minutes.

| Order | Role | Action | Timeframe |
|-------|------|--------|-----------|
| 1 | **On-call Engineer** (you) | Detect, contain, preserve evidence | Immediately |
| 2 | **Engineering Lead** | Confirm scope, authorize remediation | Within 30 min |
| 3 | **CEO / Privacy Officer** | Authorize external notifications, legal engagement | Within 2 hours |
| 4 | **Legal Counsel** | Advise on notification obligations, draft communications | Within 24 hours |
| 5 | **Covered Entity contacts** | Notify affected healthcare practices (they are the covered entities; we are their BA) | Within 48 hours |

### Key Principle

OsirisCare is a **Business Associate** under HIPAA. The covered entities (healthcare practices) are ultimately responsible for breach notification to individuals and HHS. Our obligation is to notify the covered entity **without unreasonable delay and no later than 60 days** from discovery (per our BAA). In practice, notify them within 48 hours.

---

## 5. OCR Notification (60-Day Deadline)

### When OsirisCare Must Notify HHS Directly

As a Business Associate, OsirisCare must notify **each affected covered entity** of a breach. The covered entities then notify HHS. However, if OsirisCare discovers a breach affecting its own systems that impacts PHI across multiple covered entities:

### Timeline

| Day | Action |
|-----|--------|
| Day 0 | Breach discovered. Clock starts. |
| Day 1-3 | Internal investigation, containment, evidence preservation. |
| Day 3-7 | Notify affected covered entities with all known details. |
| Day 7-30 | Complete forensic investigation. Provide updated details to covered entities. |
| Day 60 | **Hard deadline.** All covered entities must have been notified. |

### HHS Breach Portal (for covered entities, but know the process)

- URL: https://ocrportal.hhs.gov/ocr/breach/wizard_breach.jsf
- Form: HHS-700 (if the covered entity asks you to submit on their behalf)
- Required information:
  - Nature of the PHI involved (in our case: compliance metadata, device identifiers, credential data)
  - Who accessed it or to whom it was disclosed
  - Whether the PHI was actually viewed or only accessed
  - Mitigation steps taken
  - Contact information for affected individuals to ask questions

### The Four-Factor Risk Assessment

Before notifying, HIPAA requires a risk assessment considering:

1. **Nature and extent of PHI involved** — OsirisCare stores compliance metadata, site credentials, device inventories. Not clinical data, but credential exposure could enable downstream PHI access.
2. **Unauthorized person who accessed it** — External attacker vs. internal user vs. another tenant.
3. **Whether PHI was actually acquired or viewed** — Check audit logs. If RLS contained the access, the data may not have been viewable.
4. **Extent of mitigation** — Sessions revoked, credentials rotated, RLS verified, signing keys rotated.

If the risk assessment shows **low probability** that PHI was compromised, notification may not be required. Document the assessment thoroughly regardless.

---

## 6. Affected Individual Notification

### OsirisCare's Role

We notify **covered entities** (the healthcare practices). They decide whether to notify their patients/individuals. Our BAA should specify this responsibility chain.

### What to Communicate to Covered Entities

Provide a written notice including:

1. **What happened** — plain language description of the incident
2. **What data was involved** — be specific: "compliance scores for 12 devices at your site," "WinRM credentials for your domain controller," etc.
3. **What we did** — containment and remediation actions taken
4. **What they should do** — rotate any credentials we delivered to them, review their own audit logs, consider their own breach notification obligations
5. **Contact information** — who at OsirisCare they can reach for updates

### Thresholds

| Affected Individuals | Covered Entity Obligation |
|----------------------|---------------------------|
| < 500 | Notify HHS within 60 days of calendar year end |
| >= 500 | Notify HHS **within 60 days**, notify prominent media in the state |

### Template Opening

> Dear [Practice Name],
>
> We are writing to inform you of a security incident involving OsirisCare's compliance monitoring platform that may have affected data related to your organization. On [date], we discovered [brief description]. We have taken immediate steps to contain the incident and are providing this notice so you can assess any impact to your organization and patients.

---

## 7. Evidence Preservation Checklist

Check off each item. Missing evidence weakens your position with OCR.

### Database

- [ ] Full `pg_dump` snapshot taken before any remediation
- [ ] `admin_audit_log` exported (append-only, protected by `prevent_audit_modification` trigger)
- [ ] `partner_activity_log` exported
- [ ] `portal_access_log` exported
- [ ] `companion_activity_log` exported
- [ ] `update_audit_log` exported
- [ ] `exception_audit_log` exported
- [ ] `admin_sessions` snapshot (shows who was logged in)
- [ ] `partner_sessions` snapshot
- [ ] `evidence_bundles` hash chain verified (no broken `prev_hash` links)
- [ ] `l2_decisions` exported (shows what the LLM planner decided, if relevant)
- [ ] `escalation_tickets` exported (shows L3/L4 human decisions)

### OTS Proofs

- [ ] All OpenTimestamps `.ots` files from MinIO WORM storage
- [ ] Bitcoin attestation verification for any tampered evidence bundles
- [ ] Calendar server responses preserved

### Application Logs

- [ ] `mcp-server` container logs (72+ hours)
- [ ] `mcp-postgres` container logs (72+ hours)
- [ ] PgBouncer logs from port 6432
- [ ] Appliance daemon logs from affected sites (JSON structured via `slog`)
- [ ] Nginx/reverse proxy access logs

### Infrastructure

- [ ] Network connection state (`ss -tnp`, `netstat`)
- [ ] Docker container state (`docker inspect mcp-server mcp-postgres`)
- [ ] Firewall rules (`nft list ruleset` on appliances, `iptables -L` on VPS)
- [ ] SSH auth logs (`/var/log/auth.log` on VPS)
- [ ] DNS records snapshot for `osiriscare.net`
- [ ] TLS certificate chain verification

### Chain of Custody

- [ ] All evidence files checksummed (SHA256) at time of collection
- [ ] Evidence stored in a directory only accessible to incident commander
- [ ] Timestamps documented in UTC
- [ ] Each evidence collection step logged with who collected it and when

---

## 8. Post-Incident Requirements

### Corrective Action Plan (Required by OCR)

Within 30 days of incident closure, produce a corrective action plan addressing:

1. **Root cause analysis** — What specific code path, configuration, or process failed?
2. **Technical remediation** — What was fixed? Reference specific migrations, code changes, PRs.
3. **Process changes** — What operational procedures are new or updated?
4. **Monitoring improvements** — What new alerts or detection mechanisms were added?
5. **Training** — Were any personnel actions or training sessions conducted?
6. **Timeline for completion** — When will each remediation item be complete?

### Documentation for OCR

Maintain a breach investigation file containing:

- This runbook (proof that a response plan existed)
- The four-factor risk assessment (Section 5)
- All evidence from Section 7
- Covered entity notification letters with timestamps
- Corrective action plan
- Any BAA amendments resulting from the incident

### System-Specific Remediation Checklist

- [ ] RLS policies verified on all 27 tables (`pg_tables.rowsecurity = true`)
- [ ] `app.is_admin` default verified (should be `false` in production when Phase 4 P2 completes)
- [ ] All admin/partner/client sessions revoked and re-issued
- [ ] API keys rotated for affected sites
- [ ] Site credentials (`site_credentials` table) rotated for affected sites
- [ ] Ed25519 signing key rotated if fleet order integrity is in question
- [ ] `SESSION_TOKEN_SECRET` environment variable rotated
- [ ] bcrypt password hashes verified (no legacy SHA-256 hashes remain in `admin_users`)
- [ ] CSRF token validation confirmed on all non-exempt endpoints
- [ ] Rate limiting confirmed: 5 failed attempts = 15-minute lockout
- [ ] Audit log append-only triggers verified on all 6 tables (migration 084)
- [ ] Evidence chain hash integrity verified across all sites
- [ ] PgBouncer connection pooling verified (no leaked tenant context between transactions)

### Lessons Learned Meeting

Hold within 14 days of incident closure. Attendees: all engineers, CEO, legal counsel. Document:

- What went well in the response
- What was slow or confused
- What tools or access were missing at 2am
- Updates to this runbook based on the experience

---

## Appendix A: Key Tables Reference

| Table | Contains | RLS | Audit Trigger |
|-------|----------|-----|---------------|
| `admin_users` | Admin accounts, bcrypt hashes, MFA secrets | No (admin-only) | No |
| `admin_sessions` | HMAC-SHA256 hashed session tokens | No | No |
| `admin_audit_log` | All admin actions | No | `prevent_audit_modification` |
| `sites` | Site metadata, API keys, `client_org_id` | Yes | No |
| `site_credentials` | WinRM/SSH creds (Fernet or JSON encrypted) | Yes | No |
| `incidents` | Compliance drift incidents per device | Yes | No |
| `evidence_bundles` | Hash-chained compliance evidence | Yes | No |
| `discovered_devices` | Device inventory per site | Yes | No |
| `fleet_orders` | Signed remediation orders | Yes | No |
| `partner_activity_log` | Partner portal actions | No | `prevent_audit_modification` |
| `escalation_tickets` | L3/L4 human escalations | Yes | No |
| `l1_rules` | Deterministic healing rules | No | No |
| `l2_decisions` | LLM planner decisions | Yes | No |

## Appendix B: Emergency Contacts Template

Fill this in and keep it current. Print a copy and keep it with the on-call phone.

| Role | Name | Phone | Email | Backup |
|------|------|-------|-------|--------|
| On-call Engineer | | | | |
| Engineering Lead | | | | |
| CEO / Privacy Officer | | | | |
| Legal Counsel | | | | |
| Hosting Provider (VPS) | | | | |
| Domain Registrar | | | | |

---

*This runbook is a living document. Update it after every incident, drill, or architectural change. If you used it at 2am and something was wrong or missing, fix it the next morning.*

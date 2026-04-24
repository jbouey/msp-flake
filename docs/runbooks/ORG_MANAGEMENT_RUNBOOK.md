# Organization Management Runbook

**Scope:** Org lifecycle — provisioning, deprovisioning, data export, quota enforcement, BAA compliance.

**Owner:** Platform Engineering / Customer Success
**Compliance:** HIPAA §164.308, §164.312, §164.528 (disclosure accounting)

---

## 1. Architecture

Organizations are the billing/compliance boundary. An org contains:
- 1+ sites (physical locations)
- 0+ client_users (portal access)
- 1 partner (the MSP managing the org)
- 0-1 SSO configuration
- BAA + compliance framework assignment
- Quotas (max sites, users, incidents/day)

**RLS model:** `client_orgs` has RLS policies (admin bypass, self-read by org scope, partner-read by partner scope). All 25 dependent tables have `org_isolation` + `tenant_isolation` RLS.

**Retention:** HIPAA §164.530 requires 6 years of records. `data_retention_until` defaults to `now() + 6 years` on deprovisioning.

---

## 2. Monitoring

### Primary Metrics

| Metric | Healthy | Warning | Critical |
|--------|---------|---------|----------|
| `osiriscare_org_site_quota_pct{org}` | < 80 | 80-95 | > 95 |
| `osiriscare_org_baa_expiring_30d` | 0 | 1-2 | > 2 |
| `osiriscare_org_baa_expired` | 0 | — | > 0 (blocks ops) |
| `osiriscare_orgs_deprovisioned` | tracked | — | — |

### Alerts

```yaml
- alert: OrgBAAExpired
  expr: osiriscare_org_baa_expired > 0
  for: 1m
  labels:
    severity: critical
  annotations:
    summary: "{{ $value }} orgs with expired BAAs"
    runbook: docs/runbooks/ORG_MANAGEMENT_RUNBOOK.md#baa-expiration

- alert: OrgQuotaNearLimit
  expr: osiriscare_org_site_quota_pct > 90
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "Org {{ $labels.org }} at {{ $value }}% of site quota"
```

---

## 3. Provisioning a New Org

### API

```bash
curl -X POST https://api.osiriscare.net/api/dashboard/admin/orgs/provision \
  -H "Cookie: session=..." \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Sunset Medical Associates",
    "primary_email": "admin@sunsetmed.com",
    "primary_phone": "555-0100",
    "address_line1": "100 Main St",
    "city": "Scranton",
    "state": "PA",
    "postal_code": "18503",
    "npi_number": "1234567890",
    "practice_type": "primary_care",
    "provider_count": 5,
    "partner_id": "uuid-of-partner",
    "baa_effective_date": "2026-04-09",
    "baa_expiration_date": "2027-04-09",
    "max_sites": 20,
    "max_users": 10,
    "compliance_framework": "HIPAA",
    "mfa_required": true
  }'
```

Response includes `next_steps` checklist:
1. Upload signed BAA
2. Configure per-org SSO (optional)
3. Provision first site
4. Invite client users
5. First compliance packet auto-generates on 1st of next month

### Post-Provisioning Checklist

- [ ] BAA uploaded to secure storage (not the platform)
- [ ] SSO configured if enterprise client (via POST `/api/partners/me/orgs/{id}/sso`)
- [ ] At least one site provisioned and reporting checkins
- [ ] Primary contact verified (welcome email sent)
- [ ] Billing plan selected and Stripe customer linked
- [ ] Quotas set appropriately for expected scale

---

## 4. Deprovisioning an Org

### API

```bash
curl -X POST https://api.osiriscare.net/api/dashboard/admin/orgs/{org_id}/deprovision \
  -H "Cookie: session=..." \
  -d '{
    "reason": "Contract terminated 2026-04-09, partner request",
    "retention_days": 2190,
    "notify_users": true
  }'
```

### What happens

1. `client_orgs.deprovisioned_at` set to `NOW()`
2. `client_orgs.status` → `deprovisioned`
3. `data_retention_until` = today + retention_days (default 6 years)
4. All sites marked `status = 'archived'`
5. All client_sessions deleted (users forced to log in, auth will fail)
6. Audit event `org_deprovisioned` written to `org_audit_log`

### Reversing a Deprovisioning

Within the retention period, you can reprovision:

```bash
curl -X POST https://api.osiriscare.net/api/dashboard/admin/orgs/{org_id}/reprovision
```

After `data_retention_until` passes, the endpoint returns `410 Gone`.

### Hard Delete (after retention period)

Currently manual via SQL:

```sql
-- Verify retention expired
SELECT id, name, data_retention_until FROM client_orgs
WHERE deprovisioned_at IS NOT NULL
  AND data_retention_until < CURRENT_DATE;

-- Cascade delete (FK constraints will cascade)
DELETE FROM client_orgs WHERE id = '<uuid>';
```

**Warning:** This is irreversible. Always take a final export before deleting.

---

## 5. BAA Expiration Playbook

**Symptom:** `osiriscare_org_baa_expired > 0` OR `osiriscare_org_baa_expiring_30d > 0`

### Response

1. **30-day warning:** Email the org's primary_email and the managing partner
2. **Day of expiry:** Block all new data ingest for the org (TODO: enforce at checkin level)
3. **After expiry:** Treat as deprovisioning trigger if no renewal within 30 days

### Renewal

```sql
UPDATE client_orgs
SET baa_effective_date = '2027-04-09',
    baa_expiration_date = '2028-04-09'
WHERE id = '<uuid>';
```

Follow up with partner confirmation and add audit entry:

```sql
INSERT INTO org_audit_log (org_id, event_type, actor, actor_type, details)
VALUES ('<uuid>', 'baa_renewed', 'your-username', 'admin',
        '{"new_expiration": "2028-04-09", "renewal_signed_at": "2027-04-01"}'::jsonb);
```

---

## 6. Data Export for HIPAA Disclosure

### When

A patient or partner requests "all data about org X" under HIPAA §164.524 or §164.528.

### API

```bash
curl https://api.osiriscare.net/api/dashboard/admin/orgs/{org_id}/export \
  -H "Cookie: session=..." > org-export.json
```

### What's included

- Full org row (all metadata)
- All sites
- All client_users (sanitized — no password hashes)
- Incidents (last 10000)
- Compliance packet history
- org_audit_log (last 5000 events)
- Disclosure note with timestamp + acting user

### HIPAA Tracking

The export request itself is logged to `org_audit_log` as `data_exported` per §164.528 (accounting of disclosures). Partners can query their own disclosure log:

```sql
SELECT created_at, actor, details
FROM org_audit_log
WHERE org_id = '<uuid>' AND event_type = 'data_exported'
ORDER BY created_at DESC;
```

---

## 7. BAA Audit Bundle (for external auditors)

### API

```bash
curl "https://api.osiriscare.net/api/dashboard/admin/orgs/{org_id}/audit-bundle?start_date=2026-01-01&end_date=2026-03-31" \
  -H "Cookie: session=..." > audit-q1-2026.json
```

### Contents

- Org metadata + BAA status
- Per-period compliance summary (bundles, signed, blockchain anchored)
- Incident counts by tier (L1/L2/L3)
- Verification instructions for independent proof:
  - Hash chain integrity via `sha256sum`
  - Ed25519 signatures via appliance public keys
  - Blockchain anchoring via `ots verify` CLI
  - Full data export link

Self-contained — auditor needs only `sha256sum`, `ots verify`, and a Bitcoin block explorer.

---

## 8. Quota Management

### Check current usage

```bash
curl https://api.osiriscare.net/api/dashboard/admin/orgs/{org_id}/quota
```

### Update limits

```bash
curl -X PUT https://api.osiriscare.net/api/dashboard/admin/orgs/{org_id}/quota \
  -d '{
    "max_sites": 50,
    "max_users": 100,
    "max_incidents_per_day": 20000
  }'
```

### Enforcement

Currently quotas are tracked but not blocked at write time (soft limit). Partners get quota usage alerts via `osiriscare_org_site_quota_pct` metric. Hard enforcement is a future enhancement — for now, monitoring + manual intervention.

---

## 9. Cross-Org Search

### API

```bash
curl "https://api.osiriscare.net/api/dashboard/admin/orgs/search?q=sunset&include_deprovisioned=false"
```

### Response

Returns matching orgs with name, email, partner, site count, open incidents, BAA status. Filtered to the user's org_scope for non-admin users.

---

## 10. Incident Response: Org Data Corruption

**Symptom:** Org data references point to nonexistent rows, cross-org data visible to wrong users, or RLS bypass.

### Immediate Response

1. **Freeze writes:** Set `client_orgs.status = 'suspended'` for affected orgs
2. **Snapshot DB:** `pg_dump -t 'client_orgs*' -t sites -t org_audit_log > snapshot.sql`
3. **Check RLS is active:**
   ```sql
   SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'client_orgs';
   -- Both should be 't'
   ```
4. **Check audit log for suspicious writes:**
   ```sql
   SELECT * FROM org_audit_log
   WHERE created_at > NOW() - INTERVAL '24 hours'
     AND event_type IN ('quota_updated', 'org_deprovisioned', 'data_exported')
   ORDER BY created_at DESC;
   ```
5. **Escalate to CISO** if any unauthorized access detected

---

## 11. Known Gotchas

1. **RLS on `client_orgs`** was added in migration 146. Pre-146 deployments had no RLS on this table — verify `pg_class.relrowsecurity = true` on every deploy.
2. **Partner role required for `current_partner_id`** — updating `current_partner_id` directly without setting `partner_assigned_at` will fail downstream queries. Always update both.
3. **Deprovisioning does NOT delete evidence** — compliance_bundles and ots_proofs are retained for audit. Only sites + sessions are affected.
4. **`data_retention_until` is a DATE not a TIMESTAMP** — retention is granular to the day, not the second.
5. **Admin scope vs partner scope** — an admin with `org_scope = None` sees everything. An admin with `org_scope = [uuid1, uuid2]` sees only those orgs. Partners always see only their assigned orgs via `current_partner_id`.

---

## 12. Escalation Matrix

| Condition | Response Time | Action |
|-----------|---------------|--------|
| BAA expired (any org) | Immediate | Block writes, notify partner + primary_email |
| Quota > 95% | 1 hour | Alert partner, offer upgrade path |
| Unauthorized org access | Immediate | CISO + freeze account |
| Export requested | 24 hours | Review, execute, log disclosure |
| Deprovisioning request | 1 business day | Verify partner authority, execute |
| Org data corruption | Immediate | Section 10 playbook |

---

## 13. Reference

- **Migration:** `migrations/146_org_enterprise_hardening.sql`
- **API module:** `org_management.py`
- **Tests:** `tests/test_org_isolation.py` (29 tests)
- **Metrics:** `osiriscare_org_*` (section 2)
- **Tables:** `client_orgs`, `sites`, `client_users`, `client_org_sso`, `org_credentials`, `org_audit_log`, `admin_org_assignments`

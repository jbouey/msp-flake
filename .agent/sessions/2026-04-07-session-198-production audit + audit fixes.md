# Session 198 — Production Audit + Fixes + Multi-Framework + Data Cleanup

**Date:** 2026-04-07
**Commits:** ~12
**Tests:** 82 passing (11 test files)
**Migrations:** 135 deployed

---

## What Shipped

### Production Security Audit (9 findings fixed)
- C1: DRY — alert enqueue uses module function not inline reimplementation
- C2: HTML-escaped org_name in all email templates
- C3: partner_notifications RLS tenant policy (migration 135)
- I1-I6: idempotency, connection consolidation, logging, site name, validation

### Pre-Existing Production Errors (4 fixed, all verified 0)
- go_agents RLS: admin_connection must SET LOCAL app.is_admin=true (PgBouncer stale GUC)
- Mesh isolation: ::text cast on asyncpg LIKE parameter
- MinIO WORM: http:// prefix for boto3 endpoint_url
- PgBouncer DuplicatePreparedStatementError: retry decorator on SQLAlchemy hot path

### Multi-Framework Compliance (9 frameworks)
- compliance_packet.py routes through control_mappings.yaml crosswalk
- Email templates parameterized (framework-agnostic)
- Frontend copy.ts: "Compliance Monitoring Platform" not "HIPAA..."
- Checkin delivers compliance_framework to daemon

### SOC 2 + GLBA Assessment Templates
- soc2_templates.py: 30 questions (CC/A/PI/C/P) + 8 policies
- glba_templates.py: 25 questions (admin/tech/physical/privacy/disposal) + 6 policies
- framework_templates.py: router for get_assessment_questions(framework)
- 17 template tests

### MFA Enforcement + Audit Retention
- Per-org/user mfa_required flag, blocks login if not enrolled
- 3-year audit log retention with background purge
- Migration 134

### Data Quality Cleanup
- 3 home network workstations removed
- 1 appliance IP workstation removed
- 1 duplicate iMac entry removed
- 28 home network discovered_devices removed
- 19 stale incidents resolved (deploy + unreachable + old IPs)
- Residential subnet exclusion (192.168.0/1.x) prevents recontamination

### VPN Page Dedup
- Backend: DISTINCT ON (site_id) instead of per-appliance rows
- Frontend: shows appliance count per unique peer

---

## Production State After Session

| Metric | Before | After |
|--------|--------|-------|
| go_agents RLS errors | 120/2h | 0 |
| PgBouncer stmt errors | intermittent | 0 (retry) |
| MinIO WORM errors | every checkin | 0 |
| Mesh isolation errors | every 5min | 0 |
| Open incidents | 38 | 7 (real ones) |
| Workstations | 15 (polluted) | 10 (clean) |
| Go agents in DB | 0 | 3 (NVDC01, NVWS01, iMac) |

---

## Next Session

1. Add node to 88.x subnet — test mesh target distribution
2. Key rotation design (last P0 security gap)
3. Verify workstation compliance improves now that incidents are recording
4. iMac agent: configure drift checks (currently 0/0 monitor-only)
5. VPN page: verify dedup renders correctly

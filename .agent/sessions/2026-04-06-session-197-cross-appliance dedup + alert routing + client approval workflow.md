# Session 197 - Cross-Appliance Dedup + Alert Routing + Client Approval Workflow

**Date:** 2026-04-06
**Commits:** ~20
**Tests:** 53 passing (7 new test files, 0 regressions)
**Migrations:** 128-133 deployed to production

---

## Goals

- [x] Spec 1: Cross-appliance incident dedup
- [x] Spec 1: Alert routing with PHI-free digest emails
- [x] Spec 1: Per-site alert modes (self_service/informed/silent)
- [x] Spec 1: Client portal alerts page + approve/dismiss
- [x] End-to-end alert email test (verified delivery)
- [x] Layer 4: Expandable appliance cards + per-appliance incident filter
- [x] Spec 2: Non-engagement escalation (48h unacted → partner)
- [x] Spec 2: Guided credential entry modal
- [x] Spec 2: Partner notifications API
- [x] Spec 2: Compliance packet approval audit section
- [x] Deploy all to production

---

## Progress

### Spec 1 — Alert Routing Foundation
- Incident dedup by SHA256(site_id:incident_type:hostname) — eliminates cross-appliance duplicates
- alert_router.py: classify, enqueue, digest render, welcome email, background loop
- 3 alert modes: self_service (approve buttons), informed (default), silent
- Org sets default, site overrides. Partner-configurable via API.
- Client portal /client/alerts page with approve/dismiss actions
- Digest sender: 4h batch, critical/high immediate, PHI-free
- Migrations 128-132

### End-to-End Verification
- Set alert_email to jbouey@osiriscare.net on North Valley org
- Inserted critical test alert → digest sender sent immediately
- Email received: "[OsirisCare] CRITICAL alert — North Valley Family Practice"
- Found + fixed 2 production bugs: column name mismatches (alert_mode, sites.name)

### T640/T740 Investigation
- NOT a credential problem — org-level inheritance works (3 Win + 2 SSH inherited)
- Hash ring distribution issue: 4 targets on 88.x all hash to Physical appliance
- T640's 0.x subnet is home network (not clinical). T640/T740 are hot standbys.
- Fix: add more scan targets. Tomorrow: add node to 88.x to test distribution.

### Layer 4 — Dashboard Multi-Appliance UX
- Expandable appliance cards: click to show compliance grid + connectivity stats
- Per-appliance incident filter chips
- display_name + assigned_target_count in backend response
- Frontend-only (one backend query addition)

### Spec 2 — Client Self-Service Approval UX
- Non-engagement escalation: 48h unacted alerts → partner notified via email + partner_notifications table. 7-day dedup. 72h → admin L4 queue.
- Guided credential entry: CredentialEntryModal.tsx (4 types, 3-step wizard). POST /api/client/credentials. Fernet encrypted, rate-limited 10/hr, audit logged.
- Partner notifications: GET /api/partners/me/notifications + PUT mark-read
- Compliance packet: "Approval Log (Last 30 Days)" section with HIPAA refs
- Migration 133: partner_notifications table

### Production Bugs Fixed
1. `co.alert_mode` → `co.client_alert_mode` in digest sender queries
2. `sites.name` → `sites.clinic_name` + `uuid[]` → `text[]` in digest aggregation

---

## Files Changed

| Category | Files |
|----------|-------|
| Migrations | 128-133 (6 SQL files) |
| New modules | alert_router.py, CredentialEntryModal.tsx, ClientAlerts.tsx |
| Backend modified | agent_api.py, client_portal.py, partners.py, routes.py, models.py, sites.py, main.py, email_alerts.py, compliance_packet.py |
| Frontend modified | ClientDetail.tsx, App.tsx, types/index.ts, client/index.ts |
| Tests | test_cross_appliance_dedup.py, test_alert_router.py, test_partner_alert_config.py, test_client_alerts.py, test_client_credentials.py, test_non_engagement.py |

---

## Next Session

1. Add node to 88.x subnet — test mesh target distribution across 3 appliances
2. Verify non-engagement escalation fires after 48h (monitor over next 2 days)
3. Test credential entry flow end-to-end via client portal
4. Partner notification UI (bell icon, notification page) — future enhancement

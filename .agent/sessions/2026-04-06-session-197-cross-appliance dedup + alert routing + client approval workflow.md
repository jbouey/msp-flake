# Session 197 — Multi-Appliance Maturity + Multi-Framework Compliance

**Date:** 2026-04-06
**Commits:** ~28
**Tests:** 64 passing (9 test files, 0 regressions)
**Migrations:** 128-134 deployed to production (7 total)

---

## What Shipped

### Spec 1: Cross-Appliance Dedup + Alert Routing
- Incident dedup by SHA256(site_id:incident_type:hostname)
- PHI-free digest emails to org contacts (4h batch, critical/high immediate)
- Per-site alert modes: self_service / informed / silent (org default, site override)
- Client portal /client/alerts page with approve/dismiss
- End-to-end verified: email delivered to jbouey@osiriscare.net

### Layer 4: Dashboard Multi-Appliance UX
- Expandable appliance cards with compliance breakdown grid
- Per-appliance incident filter chips
- display_name + assigned_target_count in backend

### Spec 2: Client Self-Service
- Non-engagement escalation: 48h unacted → partner notified (7-day dedup)
- Guided credential entry modal (4 types, 3-step wizard, Fernet encrypted)
- Partner notifications API (GET + mark-read)
- Compliance packet approval audit section

### Maturity Fixes
- MFA enforcement: per-org/per-user `mfa_required` flag, blocks login if not enrolled
- Audit log retention: 3-year policy with background purge
- Notes field disclaimer: "Do not enter patient names or PHI"

### Multi-Framework Compliance (THE BIG ONE)
- `client_orgs.compliance_framework` column (hipaa/soc2/pci_dss/nist_csf/glba/sox/gdpr/cmmc/iso_27001)
- `get_control_for_check()` routes through control_mappings.yaml crosswalk
- Email templates parameterized: "Compliance Controls" not "HIPAA Controls"
- Frontend copy.ts: "Compliance Monitoring Platform" not "HIPAA Compliance..."
- Checkin delivers compliance_framework to daemon
- 130+ check types map to all 9 frameworks via existing YAML infrastructure

---

## Production State

- 7 migrations (128-134) all successful
- 64 tests across 9 new test files
- alert_digest background task running
- Health: OK
- 2 production bugs found and fixed (column name mismatches in digest sender)

## Key Decisions

- **Multi-framework NOW not later** — engineering is free (AI), post-activation migration is expensive. Ship framework-agnostic while zero paying customers.
- **Table names stay** — `hipaa_*` tables work for any framework, renaming is pure risk.
- **Key rotation deferred** — needs careful design (wrong implementation corrupts all credentials). P0 but separate session.

## Next Session

1. Add node to 88.x subnet — test mesh target distribution
2. Key rotation design + implementation
3. Test non-engagement escalation (fires after 48h)
4. Test credential entry flow end-to-end via client portal
5. SOC 2 / GLBA assessment template data files

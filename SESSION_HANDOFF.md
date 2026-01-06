# Session Handoff - MSP Compliance Platform

**Date:** 2026-01-06
**Phase:** Phase 12 - Launch Readiness
**Session:** 14 - Credential Management API
**Status:** Both appliances using credential-pull architecture, 27 runbooks deployed

---

## Quick Summary

HIPAA compliance automation platform for healthcare SMBs. NixOS appliances phone home to Central Command, auto-heal infrastructure, generate audit evidence.

**Production URLs:**
- Dashboard: https://dashboard.osiriscare.net
- API: https://api.osiriscare.net
- Portal: https://msp.osiriscare.net

**Deployed Appliances:**
| Site | Type | IP | Agent | Status |
|------|------|-----|-------|--------|
| North Valley Dental (physical-appliance-pilot-1aea78) | HP T640 | 192.168.88.246 | v1.0.19 | online |
| Main Street Virtualbox Medical (test-appliance-lab-b3c40c) | VM | 192.168.88.231 | v1.0.19 | online |

**Lab Environment:**
- DC: 192.168.88.250 (NVDC01.northvalley.local)
- Credentials: See `.agent/LAB_CREDENTIALS.md`

---

## Today's Session (2026-01-06 Session 14)

### Completed
1. **Fixed sites.py windows_targets transformation**
   - Was returning raw JSON from `site_credentials` table
   - Now properly formats: `hostname`, `username` (DOMAIN\user), `password`, `use_ssl`

2. **Fixed sites.py runbook query**
   - Changed `r.id` (UUID) to `r.runbook_id` (VARCHAR)
   - Query now returns correct enabled_runbooks list

3. **Database fixes on VPS**
   - Created missing `appliance_runbook_config` table
   - Fixed NULL `check_type` for 6 original runbooks (backup, cert, service, drift, firewall, patch)

4. **Credential Management API**
   - Site detail endpoint now queries `site_credentials` (was hardcoded `[]`)
   - Added `POST /api/sites/{site_id}/credentials` - Create credential
   - Added `DELETE /api/sites/{site_id}/credentials/{id}` - Delete credential

5. **Verified credential-pull architecture**
   - Both appliances have no hardcoded credentials on disk
   - Only config.yaml with site_id/api_key
   - Credentials pulled from Central Command on each 60s check-in

### Previous Session (13) - Windows Runbook Expansion
- 27 total runbooks (7 core + 20 new categories)
- RunbookConfig.tsx UI for partner-configurable enable/disable
- Backend API: GET/PUT /api/sites/{site_id}/runbooks
- 20 tests in test_runbook_filtering.py

---

## What's Complete

### Phase 12 - Launch Readiness
- Agent v1.0.19 with multi-NTP time verification
- 27 Windows runbooks (services, security, network, storage, updates, AD)
- Partner-configurable runbook enable/disable
- Credential Management API (CRUD via Central Command)
- Credential-pull architecture (RMM-style, no creds on disk)
- Email alerts for critical incidents
- Chaos probe integration for testing

### Phase 11 - Partner/Reseller Infrastructure
- Partner API with 20+ endpoints
- QR code provisioning
- Discovery module (70+ port mappings)
- Appliance provisioning module

### Phase 10 - Production Deployment
- Hetzner VPS (178.156.162.116) with Docker Compose
- Caddy reverse proxy with auto-TLS
- PostgreSQL, Redis, MinIO (WORM storage)
- Physical appliance deployed (HP T640)
- ISO v19 built and deployed

---

## What's Pending

### Immediate (Next Session)
1. **OpenTimestamps blockchain anchoring** - Enterprise tier feature
2. **Frontend credential management UI** - Wire up add/delete buttons in SiteDetail
3. **Evidence bundle MinIO upload** - Verify WORM storage working

### Short-term
- First compliance packet generation
- 30-day monitoring period
- Data flywheel integration (resolution tracking)

---

## Key Files

| Purpose | Location |
|---------|----------|
| Project context | `.agent/CONTEXT.md` |
| Current tasks | `.agent/TODO.md` |
| Network/VMs | `.agent/NETWORK.md` |
| Lab credentials | `.agent/LAB_CREDENTIALS.md` |
| Phase status | `IMPLEMENTATION-STATUS.md` |
| Master architecture | `CLAUDE.md` |
| Appliance ISO | `iso/` directory |
| Compliance agent | `packages/compliance-agent/` |
| Backend API | `mcp-server/central-command/backend/` |
| Frontend | `mcp-server/central-command/frontend/` |

---

## Commands

```bash
# Work on compliance agent
cd packages/compliance-agent && source venv/bin/activate
python -m pytest tests/ -v --tb=short

# SSH to appliances
ssh root@192.168.88.246   # Physical (North Valley)
ssh root@192.168.88.231   # VM (Main Street)

# VPS management
ssh root@178.156.162.116
cd /opt/mcp-server && docker compose logs -f mcp-server

# Check appliance status
curl -s https://api.osiriscare.net/api/sites | jq '.[] | {name, status}'

# Add credential via API
curl -X POST "https://api.osiriscare.net/api/sites/{site_id}/credentials" \
  -H "Content-Type: application/json" \
  -d '{"credential_type":"domain_admin","credential_name":"North Valley DC","host":"192.168.88.250","username":"Administrator","password":"...","domain":"NORTHVALLEY"}'
```

---

## Session History

| Date | Session | Focus | Status |
|------|---------|-------|--------|
| 2026-01-06 | 14 | Credential Management API | Complete |
| 2026-01-06 | 13 | Windows Runbook Expansion (27 total) | Complete |
| 2026-01-05 | 12 | Email Alerts, NTP Verification, Chaos Probe | Complete |
| 2026-01-05 | 11 | ISO v18 Build | Complete |
| 2026-01-05 | 10 | Healing System Integration | Complete |
| 2026-01-04 | 9 | Credential-Pull Architecture | Complete |
| 2026-01-04 | 8 | Partner API Backend | Complete |
| 2026-01-04 | 7 | Partner Admin Management | Complete |
| 2026-01-04 | 6 | Partner/Reseller Infrastructure | Complete |

See `.agent/sessions/` for detailed session logs.

---

## Architecture Overview

```
Central Command (VPS 178.156.162.116)
├── FastAPI Backend (:8000)
│   ├── /api/appliances/checkin - Returns windows_targets + enabled_runbooks
│   ├── /api/sites/{site_id}/credentials - CRUD for credentials
│   ├── /api/sites/{site_id}/runbooks - Runbook config per site
│   └── /api/evidence/... - Evidence bundle submission
├── React Frontend (:3000)
├── PostgreSQL (16-alpine)
├── MinIO (WORM storage)
└── Caddy (auto-TLS)

Appliances (NixOS)
├── compliance-agent-appliance (systemd service)
│   ├── Check-in every 60s → receives credentials + runbooks
│   ├── Drift detection → L1/L2/L3 auto-healing
│   └── Evidence bundle → signed + uploaded
└── config.yaml (site_id + api_key only, NO credentials)
```

---

## For Next Session

1. Read this file + `.agent/CONTEXT.md`
2. Check `.agent/TODO.md` for priorities
3. Consider:
   - OpenTimestamps blockchain anchoring (Enterprise feature)
   - Frontend credential management UI
   - Evidence bundle verification in MinIO

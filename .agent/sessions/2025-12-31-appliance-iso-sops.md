# Session: Appliance ISO + SOPs Documentation

**Date:** 2025-12-31
**Duration:** Extended session
**Phase:** Phase 10 - Production Deployment + Appliance Imaging

---

## Session Objectives

1. Build compliance appliance ISO image for HP T640 thin clients
2. Add SOPs to Central Command documentation page
3. Deploy updated frontend to production VPS

---

## Completed Work

### 1. Appliance ISO Infrastructure

Created complete bootable USB/ISO infrastructure for lean compliance appliances.

**Target Hardware:** HP T640 Thin Client (4-8GB RAM, ~300MB working set)

**Files Created:**
- `iso/appliance-image.nix` - Main NixOS ISO configuration
- `iso/configuration.nix` - Base system configuration
- `iso/local-status.nix` - Nginx status page with Python API
- `iso/provisioning/generate-config.py` - Site provisioning script
- `iso/provisioning/template-config.yaml` - Configuration template

**Updated:**
- `flake-compliance.nix` - Added ISO outputs, apps, and nixosConfigurations

**Key Features:**
- Pull-only architecture (phones home every 60s)
- No local MCP server or Redis (lean mode)
- Local nginx status page on :80
- 8 HIPAA controls in phone-home payload
- mTLS certificate generation for site provisioning
- First-boot setup script

**Build Commands (requires Linux):**
```bash
nix build .#appliance-iso -o result-iso
# Test in QEMU
nix run .#test-iso
# Provision new site
python iso/provisioning/generate-config.py --site-id "clinic-001" --site-name "Test Clinic"
```

### 2. SOPs Added to Documentation Page

Updated `mcp-server/central-command/frontend/src/pages/Documentation.tsx` with comprehensive Operations section.

**7 New SOPs:**
1. **SOP-OPS-001**: Daily Operations Checklist
2. **SOP-OPS-002**: Onboard New Clinic (End-to-End)
3. **SOP-OPS-003**: Image Compliance Appliance
4. **SOP-OPS-004**: Provision Site Credentials
5. **SOP-OPS-005**: Replace Failed Appliance
6. **SOP-OPS-006**: Offboard Clinic
7. **SOP-OPS-007**: L3 Incident Response

**Content includes:**
- Step-by-step procedures with commands
- Verification checklists
- Timing expectations
- Role assignments

### 3. Deployed to Production VPS

**VPS:** 178.156.162.116 (Hetzner)
**SSH:** `ssh root@178.156.162.116` (key auth)

**Deployment Steps:**
1. Built frontend locally (`npm run build`)
2. Copied dist/ to VPS
3. Rebuilt container: `docker compose build frontend --no-cache && docker compose up -d frontend`
4. Verified new JS file being served: `index-CYuhK7oi.js`

**URLs:**
- Dashboard: https://dashboard.osiriscare.net
- API: https://api.osiriscare.net
- MSP Portal: https://msp.osiriscare.net

---

## Files Modified/Created

### New Files
```
iso/
├── appliance-image.nix          # 6.7KB - Main ISO config
├── configuration.nix            # 4.7KB - Base system config
├── local-status.nix             # 17KB - Nginx + Python status API
└── provisioning/
    ├── generate-config.py       # 11KB - Site provisioning
    └── template-config.yaml     # 4.8KB - Config template
```

### Modified Files
```
flake-compliance.nix             # Added ISO outputs, apps, nixosConfigurations
mcp-server/central-command/frontend/src/pages/Documentation.tsx  # Added Operations section
```

---

## Pending Work

### Immediate
- [ ] Test ISO build on Linux system (requires x86_64-linux)
- [ ] Git commit all changes
- [ ] First pilot client enrollment

### Short-Term
- [ ] Configure TLS certificates for MCP server (currently using Caddy auto-TLS)
- [ ] MinIO Object Lock configuration (WORM evidence)
- [ ] Connect test appliance to production API

---

## Infrastructure State

### Production (Hetzner VPS)
| Service | URL | Port | Status |
|---------|-----|------|--------|
| API | api.osiriscare.net | 8000 | Running |
| Dashboard | dashboard.osiriscare.net | 3000 | Running |
| Portal | msp.osiriscare.net | 3000 | Running (alias) |
| PostgreSQL | (internal) | 5432 | Running |
| Redis | (internal) | 6379 | Running |
| MinIO | (internal) | 9000/9001 | Running |
| Caddy | (reverse proxy) | 443 | Running |

### Development (Mac Host 174.178.63.139)
| VM | IP | SSH Port | Status |
|----|----|----------|--------|
| Appliance | 192.168.56.103 | 4444 | Available |
| MCP Server | 10.0.3.4 | 4445 | Available |
| Windows DC | 192.168.56.102 | 55985 | Available |

---

## Key Decisions Made

1. **Lean Appliance Mode**: No local MCP/Redis, phones home to central server
2. **Pull-Only Architecture**: No inbound connections, HIPAA compliant
3. **60s Poll Interval**: Balance between responsiveness and API load
4. **mTLS for Site Auth**: Each site gets unique client certificate
5. **Nginx Status Page**: Simple local status on :80 for NOC monitoring

---

## Test Status

- 169 tests passing (compliance-agent)
- ISO build untested (requires Linux)
- Frontend deployed and verified

---

## Session Handoff Checklist

- [x] Appliance ISO infrastructure created
- [x] SOPs added to documentation
- [x] Frontend deployed to production
- [x] Session log created
- [ ] Git commit pending
- [ ] ISO build verification pending (Linux required)

# Session Handoff - MSP Compliance Platform

**Date:** 2026-01-10
**Phase:** Phase 12 - Launch Readiness
**Session:** 24 - Linux Sensor + TLS Hardening + Full Git Sync
**Status:** All work pushed to production (6 commits, +13,000 lines)

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
| North Valley Dental (physical-appliance-pilot-1aea78) | HP T640 | 192.168.88.246 | v1.0.22 | online |
| Main Street Virtualbox Medical (test-appliance-lab-b3c40c) | VM | 192.168.88.247 | v1.0.22 | online |

**Lab Environment:**
- DC: 192.168.88.250 (NVDC01.northvalley.local)
- iMac Gateway: 192.168.88.50
- Credentials: See `.agent/LAB_CREDENTIALS.md`

---

## Today's Session (2026-01-10 Session 24)

### Part 1: Linux Sensor Dual-Mode Architecture

Full implementation of lightweight bash-based sensors for Linux servers.

**Sensor Scripts** (`packages/compliance-agent/sensor/linux/`):
- `osiriscare-sensor.sh` - 12 drift detection checks, 10-second loop
- `install.sh` - Curl-based installation with `--ca-cert` option
- `uninstall.sh` - Cleanup with `--force` for automation
- `osiriscare-sensor.service` - systemd unit

**Appliance API** (`sensor_linux.py`):
- `/sensor/register`, `/sensor/heartbeat`, `/sensor/event`, `/sensor/status`
- Script download endpoints for curl-based install

**Central Command API** (`sensors.py`):
- Linux sensor deploy/remove endpoints
- Combined Windows+Linux dual-mode status

### Part 2: TLS Hardening (Encryption Grade C+ → B+)

Security audit and fixes for data transmission:

| Component | Before | After | Fix |
|-----------|--------|-------|-----|
| Windows WinRM | HTTP (5985) | HTTPS (5986) | Default port + use_ssl=True |
| Appliance Client | No TLS pin | TLS 1.2+ | `create_secure_ssl_context()` |
| WORM Uploader | Optional SSL | TLS 1.2+ | `minimum_version=TLSv1_2` |
| MCP Client | No version pin | TLS 1.2+ | Added to `_create_ssl_context()` |
| Linux Sensor Install | `--insecure` | `--ca-cert` option | Proper cert verification |

### Part 3: Git Sync (Backlog Commits)

Committed and pushed all uncommitted work from Sessions 17-22:

```
837266e test: Add tests for Linux, OTS, network posture (Sessions 18-21)
0c2768d feat: Compliance agent - Linux support + OpenTimestamps (Sessions 18-21)
fccb6d8 feat: Frontend - User management UI (Sessions 19-20)
df283d3 feat: Backend infrastructure (Sessions 17-22)
ef0827a security: TLS hardening + HTTPS enforcement (Grade C+ → B+)
b8eb124 feat: Linux Sensor Dual-Mode Architecture (Session 24)
```

**Total:** 48 files changed, +13,000 lines pushed to production

---

## What's Complete

### Phase 12 - Launch Readiness
- Agent v1.0.22 with OpenTimestamps, Linux support, asyncssh
- 43 total runbooks (27 Windows + 16 Linux)
- Learning flywheel infrastructure seeded and ready
- Partner-configurable runbook enable/disable
- Credential-pull architecture (RMM-style, no creds on disk)
- Email alerts for critical incidents
- OpenTimestamps blockchain anchoring (Enterprise tier)
- RBAC user management (Admin/Operator/Readonly)
- Windows sensor push architecture
- Linux sensor push architecture
- **TLS 1.2+ enforcement across all clients**
- **HTTPS default for Windows WinRM**

### Encryption Grade: B+

| Rail | Grade |
|------|-------|
| Appliance → Central Command | A- |
| Evidence Signing | A- |
| Windows Credentials | B |
| Linux Sensor Install | B |
| API Key Storage | C (plaintext YAML) |

**To reach A:** Certificate pinning + SOPS-encrypted configs

---

## What's Pending

### Immediate (Next Session)
1. **Run migration 012** - Apply Linux sensor schema on VPS
2. **Deploy VPS changes** - Docker rebuild with new backend code
3. **Test Linux sensor** - End-to-end on real Linux host
4. **Build ISO v21** - Include sensor scripts + TLS hardening

### Short-term
- First compliance packet generation
- 30-day monitoring period
- Evidence bundle verification in MinIO

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
| Linux sensor scripts | `packages/compliance-agent/sensor/linux/` |
| Backend API | `mcp-server/central-command/backend/` |
| Frontend | `mcp-server/central-command/frontend/` |

---

## Commands

```bash
# Work on compliance agent
cd packages/compliance-agent && source venv/bin/activate
python -m pytest tests/ -v --tb=short

# Run Linux sensor tests specifically
python -m pytest tests/test_linux_sensor.py -v

# SSH to appliances
ssh root@192.168.88.246   # Physical (North Valley)
ssh root@192.168.88.247   # VM (Main Street)

# VPS management
ssh root@178.156.162.116
cd /opt/mcp-server && docker compose logs -f mcp-server

# Run migration 012 on VPS
ssh root@178.156.162.116 "cd /opt/mcp-server && docker compose exec postgres psql -U msp -d msp -f /migrations/012_linux_sensors.sql"

# Deploy Linux sensor (with proper CA cert)
curl -sSL --cacert /path/to/ca.pem https://appliance:8080/sensor/install.sh | \
  bash -s -- --sensor-id lsens-test --api-key <key> --appliance-url https://appliance:8080
```

---

## Session History

| Date | Session | Focus | Status |
|------|---------|-------|--------|
| 2026-01-10 | 24 | Linux Sensor + TLS Hardening + Git Sync | Complete |
| 2026-01-10 | 23 | Runbook Config Page Fix + Flywheel Seeding | Complete |
| 2026-01-09 | 22 | ISO v20 Build + Physical Appliance Update | Complete |
| 2026-01-09 | 21 | OpenTimestamps Blockchain Anchoring | Complete |
| 2026-01-09 | 20 | Auth Fix + System Audit | Complete |
| 2026-01-08 | 19 | RBAC User Management | Complete |
| 2026-01-08 | 18 | Linux Drift Healing Module | Complete |
| 2026-01-08 | 17 | Dashboard Auth + 1Password Secrets | Complete |
| 2026-01-08 | 16 | Partner Dashboard + L3 Escalation | Complete |
| 2026-01-08 | 15 | Windows Sensor Architecture | Complete |

---

## Architecture Overview

```
Central Command (VPS 178.156.162.116)
├── FastAPI Backend (:8000)
│   ├── /api/appliances/checkin - Credentials + runbooks (TLS 1.2+)
│   ├── /api/sensors/... - Windows + Linux sensor management
│   ├── /api/evidence/... - OTS anchoring + hash chains
│   ├── /api/users/... - RBAC (Admin/Operator/Readonly)
│   └── All connections require TLS 1.2+
├── React Frontend (:3000)
├── PostgreSQL (16-alpine)
├── MinIO (WORM storage)
└── Caddy (auto-TLS)

Appliances (NixOS)
├── compliance-agent-appliance (systemd)
│   ├── Check-in every 60s (TLS 1.2+ enforced)
│   ├── L1/L2/L3 auto-healing
│   ├── Ed25519 signed + OTS anchored evidence
│   └── Sensor API (:8080) for Windows + Linux
└── config.yaml (site_id + api_key only)

Sensors (Windows PowerShell / Linux Bash)
├── Push drift events to appliance
├── Heartbeat every 60s
└── WinRM/SSH remediation (HTTPS/TLS required)
```

---

## For Next Session

1. Read this file + `.agent/CONTEXT.md`
2. Check `.agent/TODO.md` for priorities
3. Priority tasks:
   - Run migration 012 on VPS
   - Deploy updated backend to VPS
   - Test Linux sensor end-to-end
   - Build ISO v21

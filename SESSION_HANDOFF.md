# Session Handoff - MSP Compliance Platform

**Date:** 2026-01-10
**Phase:** Phase 12 - Launch Readiness
**Session:** 24 - Linux Sensor Dual-Mode Architecture
**Status:** Complete implementation of Linux sensor push architecture

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

### Completed: Linux Sensor Dual-Mode Architecture

Full implementation of lightweight bash-based sensors for Linux servers that push drift events to the NixOS appliance, with SSH fallback for remediation.

#### Phase A: Sensor Scripts
Created `packages/compliance-agent/sensor/linux/`:
- `osiriscare-sensor.sh` - Main bash script (12 drift detection checks)
- `install.sh` - One-liner curl installation
- `uninstall.sh` - Cleanup with `--force` for automation
- `osiriscare-sensor.service` - systemd unit
- `sensor.env.template` - Config template

#### Phase B: Appliance API
Created `packages/compliance-agent/src/compliance_agent/sensor_linux.py`:
- `/sensor/register` - Register new sensor, get credentials
- `/sensor/heartbeat` - Receive heartbeats (every 60s)
- `/sensor/event` - Receive drift events (real-time)
- `/sensor/status` - Get all sensor statuses
- Script download endpoints for curl-based install

#### Phase C: Agent Integration
Updated `appliance_agent.py`:
- Linux sensor router integrated with sensor API server
- `configure_linux_healing()` for SSH-based remediation
- `deploy_linux_sensor` and `remove_linux_sensor` order handlers
- Combined sensor status for Windows + Linux

#### Phase D: Central Command API
Updated `mcp-server/central-command/backend/sensors.py`:
- `POST /api/sensors/sites/{site_id}/linux/{hostname}/deploy`
- `DELETE /api/sensors/sites/{site_id}/linux/{hostname}`
- `POST /api/sensors/linux/heartbeat`
- `GET /api/sensors/sites/{site_id}/linux`
- Updated dual-mode status to show both platforms

Created `migrations/012_linux_sensors.sql`:
- Added `platform` column to sensor_registry
- Added `sensor_id` column for Linux sensors
- Extended command_type constraint for Linux commands

#### Phase E: Testing
Created `tests/test_linux_sensor.py`:
- 25 tests covering models, registry, and API
- All 697 tests pass (672 + 25 new)

### Linux Sensor Checks (12 total)
1. SSH Configuration (password auth, root login)
2. Firewall Status (iptables, ufw, firewalld)
3. Failed Login Attempts
4. Disk Space
5. Memory Usage
6. Unauthorized Users (baseline drift)
7. Critical Services (sshd, rsyslog, cron)
8. File Integrity (passwd, shadow, sudoers)
9. Open Ports (baseline drift)
10. Security Updates
11. Audit Logs
12. Cron Jobs (baseline drift)

### Files Created
| File | Purpose |
|------|---------|
| `packages/compliance-agent/sensor/linux/osiriscare-sensor.sh` | Main sensor script |
| `packages/compliance-agent/sensor/linux/install.sh` | Installation script |
| `packages/compliance-agent/sensor/linux/uninstall.sh` | Uninstall script |
| `packages/compliance-agent/sensor/linux/osiriscare-sensor.service` | systemd unit |
| `packages/compliance-agent/sensor/linux/sensor.env.template` | Config template |
| `packages/compliance-agent/src/compliance_agent/sensor_linux.py` | Appliance API |
| `packages/compliance-agent/tests/test_linux_sensor.py` | Tests |
| `mcp-server/central-command/backend/migrations/012_linux_sensors.sql` | DB migration |

### Files Modified
| File | Change |
|------|--------|
| `packages/compliance-agent/src/compliance_agent/appliance_agent.py` | Linux sensor integration |
| `mcp-server/central-command/backend/sensors.py` | Linux sensor endpoints |

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
- **Linux sensor push architecture (NEW)**

### Previous Sessions
- Session 23: Runbook config page fix, flywheel seeding
- Session 22: ISO v20 build, physical appliance update
- Session 21: OpenTimestamps blockchain anchoring
- Session 20: Auth fix, comprehensive system audit
- Session 19: RBAC user management
- Session 18: Linux drift healing module

---

## What's Pending

### Immediate (Next Session)
1. **Deploy Linux sensor to test server** - Test end-to-end on real Linux host
2. **Run migration 012** - Apply Linux sensor schema on production DB
3. **Build ISO v21** - Include Linux sensor scripts in appliance

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
| **Linux sensor scripts** | `packages/compliance-agent/sensor/linux/` |
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

# MCP Server local dev
cd mcp-server && source venv/bin/activate
python -m uvicorn main:app --host 0.0.0.0 --port 8443 --ssl-keyfile /tmp/key.pem --ssl-certfile /tmp/cert.pem

# SSH to appliances
ssh root@192.168.88.246   # Physical (North Valley)
ssh root@192.168.88.247   # VM (Main Street)

# VPS management
ssh root@178.156.162.116
cd /opt/mcp-server && docker compose logs -f mcp-server

# Deploy Linux sensor to a target (from appliance)
curl -sSL --insecure https://192.168.88.246:8080/sensor/install.sh | \
  bash -s -- --sensor-id lsens-test --api-key <key> --appliance-url https://192.168.88.246:8080

# Check sensor status
curl -s https://api.osiriscare.net/api/sensors/stats
```

---

## Session History

| Date | Session | Focus | Status |
|------|---------|-------|--------|
| 2026-01-10 | 24 | Linux Sensor Dual-Mode Architecture | Complete |
| 2026-01-10 | 23 | Runbook Config Page Fix + Flywheel Seeding | Complete |
| 2026-01-09 | 22 | ISO v20 Build + Physical Appliance Update | Complete |
| 2026-01-09 | 21 | OpenTimestamps Blockchain Anchoring | Complete |
| 2026-01-09 | 20 | Auth Fix + System Audit | Complete |
| 2026-01-08 | 19 | RBAC User Management | Complete |
| 2026-01-08 | 18 | Linux Drift Healing Module | Complete |
| 2026-01-08 | 17 | Dashboard Auth + 1Password Secrets | Complete |
| 2026-01-08 | 16 | Partner Dashboard + L3 Escalation | Complete |
| 2026-01-08 | 15 | Windows Sensor Architecture | Complete |

See `.agent/sessions/` for detailed session logs.

---

## Architecture Overview

```
Central Command (VPS 178.156.162.116)
├── FastAPI Backend (:8000)
│   ├── /api/appliances/checkin - Returns windows_targets + linux_targets + runbooks
│   ├── /api/sensors/... - Windows + Linux sensor management (NEW)
│   ├── /api/evidence/... - Evidence bundle submission + OTS anchoring
│   └── /api/users/... - RBAC user management
├── React Frontend (:3000)
├── PostgreSQL (16-alpine)
├── MinIO (WORM storage)
└── Caddy (auto-TLS)

Appliances (NixOS)
├── compliance-agent-appliance (systemd service)
│   ├── Check-in every 60s → receives credentials + runbooks
│   ├── Drift detection → L1/L2/L3 auto-healing
│   ├── Learning flywheel → L2 patterns promote to L1
│   ├── Evidence bundle → Ed25519 signed + OTS anchored + WORM stored
│   └── Sensor API (:8080) → Receives Windows + Linux sensor events (NEW)
└── config.yaml (site_id + api_key only, NO credentials)

Linux Sensors (bash, runs on target servers)
├── osiriscare-sensor.sh - 12 drift checks, 10-second loop
├── Push events to appliance → /sensor/event
├── Heartbeat to appliance → /sensor/heartbeat (every 60s)
└── SSH fallback for remediation (credential-pull from Central Command)
```

---

## For Next Session

1. Read this file + `.agent/CONTEXT.md`
2. Check `.agent/TODO.md` for priorities
3. Consider:
   - Deploy Linux sensor to real Linux server for end-to-end test
   - Run migration 012_linux_sensors.sql on production
   - Build ISO v21 with sensor scripts bundled

# Current Tasks & Priorities

**Last Updated:** 2026-01-14 (Session 32 - Network Compliance + Extended Check Types + Documentation Sweep)
**Sprint:** Phase 12 - Launch Readiness (Agent v1.0.30, ISO v29, 43 Runbooks, OTS Anchoring, Linux+Windows Support, Windows Sensors, Partner Escalations, RBAC, Multi-Framework, Cloud Integrations, L1 JSON Rule Loading, Chaos Lab Automated, **Network Compliance Check**, **Extended Check Types**)

---

## Session 32 (2026-01-14) - Network Compliance + Extended Check Types

### 1. Network Compliance Check Integration
**Status:** COMPLETE
**Details:** Added Network compliance check across full stack (Drata/Vanta style).

#### Changes Made
- Backend `models.py`: Added `NETWORK = "network"` to CheckType enum
- Backend `metrics.py`: Updated `calculate_compliance_score()` to include network (7 metrics avg)
- Agent `appliance_agent.py`: Changed check_type from "network_posture_{os_type}" to "network"
- Frontend `types/index.ts`: Added 'network' to CheckType union and ComplianceMetrics
- Frontend `IncidentRow.tsx`: Added 'Network' label

#### Agent Version
- Bumped to v1.0.30 for ISO compatibility

### 2. Extended Check Type Labels
**Status:** COMPLETE
**Details:** Added frontend labels for all chaos probe/monitoring check types.

| Check Type | Label |
|------------|-------|
| ntp_sync | NTP |
| disk_space | Disk |
| service_health | Services |
| windows_defender | Defender |
| memory_pressure | Memory |
| certificate_expiry | Cert |
| database_corruption | Database |
| prohibited_port | Port |

### 3. Learning Flywheel Pattern Endpoints
**Status:** COMPLETE (deployed)
**Details:** Pattern reporting endpoints fully deployed to VPS.
- `/agent/patterns` - Agent pattern reporting
- `/patterns` - Dashboard pattern reporting
- Tier count query fix (`resolution_tier IS NOT NULL`)

### 4. Infrastructure Fixes
**Status:** COMPLETE
- Sensor registry FK constraint fix (VARCHAR match instead of strict FK)
- FrameworkConfig API parsing fix (extract frameworks object from response)
- Dockerfile: Added asyncpg + cryptography dependencies

### 5. Chaos Lab Enhancement
**Status:** COMPLETE
**Details:** Added second daily execution at 2 PM for more system stress testing.

**New Schedule (iMac 192.168.88.50):**
| Time | Task |
|------|------|
| 6:00 AM | Execute chaos plan (morning) |
| 12:00 PM | Mid-day checkpoint |
| 2:00 PM | Execute chaos plan (afternoon) - **NEW** |
| 6:00 PM | End of day report |
| 8:00 PM | Generate next day's plan |

### 6. Git Push & VPS Deployment
**Status:** COMPLETE
**Commits pushed:**
1. `fdb99c6` - L1 legacy action mapping (Session 30)
2. `e90c52c` - L1 JSON rule loading + chaos lab fixes (Session 31)
3. `1b3e665` - Network compliance + extended check types (v1.0.30)
4. `14cac63` - Learning Flywheel pattern reporting + tier fix
5. `4bc85c2` - Sensor registry FK + FrameworkConfig API fix

**VPS Deployed & Verified:**
- Backend: models.py, metrics.py, routes.py, db_queries.py
- Frontend: Built and deployed (index-DUHCrfow.js)
- Container: Restarted, healthy

**Files Modified:**
| File | Change |
|------|--------|
| `mcp-server/central-command/backend/models.py` | Added NETWORK check type |
| `mcp-server/central-command/backend/metrics.py` | Added network to compliance score |
| `mcp-server/central-command/backend/routes.py` | Added pattern endpoints |
| `mcp-server/central-command/backend/db_queries.py` | Fixed tier count query |
| `mcp-server/central-command/frontend/src/types/index.ts` | Extended CheckType union |
| `mcp-server/central-command/frontend/src/components/incidents/IncidentRow.tsx` | Extended labels |
| `mcp-server/main.py` | Added /agent/patterns endpoint |
| `mcp-server/Dockerfile` | Added asyncpg, cryptography |
| `packages/compliance-agent/src/compliance_agent/appliance_agent.py` | v1.0.30, network check_type |
| `~/chaos-lab/` crontab (iMac) | Added 2 PM execution |

---

## Immediate (Next Session)

### 1. Build ISO v30 with Network Check Type
**Status:** PENDING
**Details:**
- Agent code at v1.0.30 with network check_type fix
- Update `iso/appliance-image.nix` version to 1.0.30
- Build ISO on VPS

### 2. Deploy ISO v30 to Appliances
**Status:** PENDING
**Details:**
- Deploy to VM first (192.168.88.247)
- User handles physical appliance (192.168.88.246)

### 3. Run Chaos Lab Cycle
**Status:** PENDING
**Details:**
- Verify extended check types display correctly
- Monitor Learning dashboard for pattern aggregation
- Check incidents page for proper labels

---

## Short-term

- First compliance packet generation
- 30-day monitoring period
- Evidence bundle verification in MinIO
- Test framework scoring with real appliance data

---

## Quick Reference

**Run tests:**
```bash
cd /Users/dad/Documents/Msp_Flakes/packages/compliance-agent
source venv/bin/activate
python -m pytest tests/ -v --tb=short
```

**SSH to VPS:**
```bash
ssh root@178.156.162.116
```

**SSH to Physical Appliance:**
```bash
ssh root@192.168.88.246
```

**SSH to iMac Gateway:**
```bash
ssh jrelly@192.168.88.50
```

**Check chaos lab cron:**
```bash
ssh jrelly@192.168.88.50 "crontab -l | grep -A 10 'Chaos Lab'"
```

**Rebuild ISO on VPS:**
```bash
cd /root/msp-iso-build && git pull && nix build .#appliance-iso -o result-iso-v30
```

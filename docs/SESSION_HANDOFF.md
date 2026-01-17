# Session Handoff - MSP Compliance Platform

**Last Updated:** 2026-01-17 (Session 50)
**Current State:** Active Healing Enabled, Chaos Lab v2 Multi-VM Ready

---

## Quick Status

| Component | Status | Version |
|-----------|--------|---------|
| Agent | v1.0.40 | Stable |
| ISO | v40 | **DEPLOYED** - gRPC working |
| Tests | 811 + 24 Go tests | Healthy |
| Go Agent | Deployed to NVWS01 | 16.6MB binary |
| gRPC | **WORKING** | Verified |
| Chaos Lab | **v2 Multi-VM** | Ready for first run |
| Active Healing | **ENABLED** | HEALING_DRY_RUN=false |
| L1 Rules | Platform-specific | 29+ rules |
| L2 Scenarios | 6 new categories | Learning data |

---

## Session 50 Summary (2026-01-17)

### Completed

#### 1. Chaos Lab v2 - Multi-VM Campaign Generator
- **Location (iMac):** `~/chaos-lab/scripts/generate_and_plan_v2.py`
- **Change:** Campaign-level restore instead of per-scenario (21 → 3 restores)
- **Targets:** DC (192.168.88.250) + Workstation NVWS01 (192.168.88.251)
- **Crontab:** Updated to use v2 script

#### 2. Active Healing Enabled
- **Root Cause:** `HEALING_DRY_RUN=true` was preventing learning data collection
- **Database Status:** Was showing 0 L1 resolutions, 0 L2 resolutions, 102 unresolved
- **Fix:** Set `healing_dry_run: false` in `/var/lib/msp/config.yaml` on appliance
- **Verification:** Logs show "Three-tier healing enabled (ACTIVE)"

#### 3. NixOS Module & ISO Updates
- **modules/compliance-agent.nix:** Added `healingDryRun` option
- **iso/appliance-image.nix:** Added `HEALING_DRY_RUN=false` environment block

#### 4. L1 Rules Updates
- **mcp-server/main.py:** Added L1-FIREWALL-002, L1-DEFENDER-001
- **L1-FIREWALL-001:** Updated to use `restore_firewall_baseline` action

#### 5. L2 Scenario Categories
Added 6 categories that bypass L1 rules for L2 LLM engagement:
- credential_policy
- scheduled_tasks
- smb_security
- local_accounts
- registry_persistence
- wmi_persistence

#### 6. Repository Cleanup
- **`.gitignore`:** Added build artifact patterns (*.iso, *.tar.gz, *.exe, dist/)
- **Removed from tracking:** `.DS_Store`, `__pycache__`, `.egg-info`
- **Commits pushed:**
  - Msp_Flakes: `a842dce`
  - auto-heal-daemon: `253474b`

### Files Modified This Session
| File | Change |
|------|--------|
| `modules/compliance-agent.nix` | Added healingDryRun option |
| `iso/appliance-image.nix` | Added HEALING_DRY_RUN=false environment |
| `mcp-server/main.py` | Added L1-FIREWALL-002, L1-DEFENDER-001 |
| `.gitignore` | Added build artifact patterns |
| `~/chaos-lab/scripts/generate_and_plan_v2.py` (iMac) | Multi-VM generator |
| `~/chaos-lab/config.env` (iMac) | DC_* and WS_* variables |
| `/var/lib/msp/config.yaml` (appliance) | healing_dry_run: false |

---

## Infrastructure State

### Physical Appliance (192.168.88.246)
- **Status:** Online, running ISO v40
- **Agent:** v1.0.40
- **Active Healing:** ENABLED

### VM Appliance (192.168.88.247)
- **Status:** Online, running ISO v40
- **gRPC:** Verified working

### Windows Infrastructure
| Machine | IP | Go Agent | Status |
|---------|-----|----------|--------|
| NVWS01 | 192.168.88.251 | Deployed | Dry-run tested |
| NVDC01 | 192.168.88.250 | - | Domain Controller |
| NVSRV01 | 192.168.88.244 | - | Server Core |

### Chaos Lab (iMac 192.168.88.50)
**Cron Schedule:**
```
6:00  - Morning chaos execution (v2 multi-VM)
10:00 - Workstation cadence verification
12:00 - Mid-day checkpoint
14:00 - Afternoon chaos execution (v2 multi-VM)
16:00 - Workstation cadence verification
18:00 - End of day report
20:00 - Next day planning
```

---

## Next Session Priorities

### 1. Monitor Chaos Lab v2
- Wait for next scheduled run (6:00 or 14:00)
- Check `~/chaos-lab/logs/` for campaign results
- Verify multi-VM attacks execute correctly

### 2. Verify Learning Pipeline
```bash
# Check incident database on appliance
ssh root@192.168.88.246 "sqlite3 /var/lib/msp/incidents.db 'SELECT resolution_tier, COUNT(*) FROM incidents GROUP BY resolution_tier'"
```

### 3. Check L2 Engagement
```bash
# Watch for L2 LLM decisions
ssh root@192.168.88.246 "journalctl -u compliance-agent -f | grep -i 'L2\|LLM\|level2'"
```

### 4. Flash ISO v40 to Physical Appliance
If needed, the ISO is available at:
- iMac: `~/osiriscare-v40.iso`
- VPS: `/root/msp-iso-build/result-iso-v40/iso/osiriscare-appliance.iso`

---

## Quick Commands

```bash
# SSH to appliances
ssh root@192.168.88.246   # Physical appliance
ssh root@192.168.88.247   # VM appliance

# SSH to iMac
ssh jrelly@192.168.88.50

# Check agent status
ssh root@192.168.88.246 "journalctl -u compliance-agent -n 50"

# Check healing mode
ssh root@192.168.88.246 "grep healing_dry_run /var/lib/msp/config.yaml"

# Check incident database
ssh root@192.168.88.246 "sqlite3 /var/lib/msp/incidents.db 'SELECT * FROM incidents ORDER BY created_at DESC LIMIT 10'"

# Run tests locally
cd packages/compliance-agent && source venv/bin/activate && python -m pytest tests/ -v
```

---

## Architecture Reference

```
Chaos Lab (iMac)              Appliances (ISO v40)
┌─────────────────┐           ┌─────────────────────┐
│ generate_and_   │           │  Compliance Agent   │
│ plan_v2.py      │──attacks──│  v1.0.40            │
│                 │           │  Active Healing ON  │
│ Campaign-level  │           │                     │
│ restore (3x)    │           │  L1: 29+ rules      │
│                 │           │  L2: 6 new cats     │
└─────────────────┘           │  L3: Escalation     │
        │                     └─────────────────────┘
        ▼                              │
    DC (NVDC01)                        ▼
    WS (NVWS01)               Learning Pipeline
                              (patterns → promotion)
```

---

**For new AI sessions:**
1. Read `.agent/CONTEXT.md` for full state
2. Read `.agent/TODO.md` for current priorities
3. Check this file for handoff details

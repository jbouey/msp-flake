# Zero-Friction Update System

**Phase 13 - Fleet Update Infrastructure**

**Status:** FULLY IMPLEMENTED (Session 55), ISO v44 Built with A/B Partition System

## Deployment Status

| Component | Status | Notes |
|-----------|--------|-------|
| **Central Command UI** | ✅ DEPLOYED | Fleet Updates page at /fleet-updates |
| **Fleet API** | ✅ DEPLOYED | Create releases, manage rollouts |
| **Database Schema** | ✅ DEPLOYED | update_releases, update_rollouts, appliance_updates |
| **Rollout Management** | ✅ TESTED | Pause, resume, advance stage working |
| **A/B Partition** | ✅ IMPLEMENTED | GRUB boot config, partition detection |
| **Boot Health Gate** | ✅ IMPLEMENTED | health_gate.py, auto-rollback after 3 failed boots |
| **Update Agent** | ✅ IMPLEMENTED | Download/verify/apply logic, update_iso handler |
| **ISO v44** | ✅ BUILT | 1.1GB, ready for deployment |

## Overview

A/B partition scheme enabling zero-touch remote updates for all physical appliances via Central Command.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                       Central Command                            │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────────┐ │
│  │ ISO Storage │  │ Fleet API    │  │ Rollout Controller      │ │
│  │ /updates/   │  │ /api/fleet/  │  │ - Canary (5%)           │ │
│  │ v43.iso     │  │ - push       │  │ - Staged (10/50/100%)   │ │
│  │ v44.iso     │  │ - status     │  │ - Health-gated          │ │
│  └─────────────┘  │ - rollback   │  │ - Auto-pause on failure │ │
│                   └──────────────┘  └─────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ mTLS
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Physical Appliance                           │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                    Disk Layout                               ││
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────┐  ││
│  │  │ EFI/Boot │  │ Part A   │  │ Part B   │  │ Data/State  │  ││
│  │  │ 512MB    │  │ 2GB ISO  │  │ 2GB ISO  │  │ Persistent  │  ││
│  │  │ Bootldr  │  │ (active) │  │ (standby)│  │ /var/lib/   │  ││
│  │  └──────────┘  └──────────┘  └──────────┘  └─────────────┘  ││
│  └─────────────────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                   Update Agent                               ││
│  │  - Poll Central Command for updates                          ││
│  │  - Download ISO to standby partition                         ││
│  │  - Verify SHA256 checksum                                    ││
│  │  - Update bootloader (set standby as next)                   ││
│  │  - Reboot in maintenance window                              ││
│  │  - Health check post-boot (60s)                              ││
│  │  - Auto-rollback if unhealthy                                ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

## Disk Partition Scheme

```
/dev/sda (Internal SSD - 476GB on HP T640)
├── /dev/sda1  512MB  EFI System Partition (ESP)
│   └── grub.cfg with A/B boot logic
├── /dev/sda2  2GB    Partition A (squashfs root)
├── /dev/sda3  2GB    Partition B (squashfs root)
└── /dev/sda4  *GB    Persistent data partition
    └── /var/lib/msp/ (config, state, credentials)
```

## Boot Flow

```
Power On
    │
    ▼
┌─────────────┐
│ GRUB/rEFInd │
│ Read: /boot │
│ /ab_state   │
└──────┬──────┘
       │
       ▼
   ┌───────┐
   │A or B?│
   └───┬───┘
       │
  ┌────┴────┐
  ▼         ▼
┌───┐     ┌───┐
│ A │     │ B │
└─┬─┘     └─┬─┘
  │         │
  ▼         ▼
Boot selected partition
       │
       ▼
┌─────────────────┐
│ Health Check    │
│ - Network OK?   │
│ - Agent running?│
│ - API reachable?│
└────────┬────────┘
         │
    ┌────┴────┐
    │ Healthy?│
    └────┬────┘
         │
   Yes   │   No (after 3 boots)
    ┌────┴────┐
    ▼         ▼
 Continue   Rollback
            to other
            partition
```

## Update Flow

### 1. Central Command Initiates Update

```python
POST /api/fleet/updates
{
    "version": "v44",
    "iso_url": "https://updates.osiriscare.net/v44.iso",
    "sha256": "abc123...",
    "rollout": {
        "strategy": "staged",
        "stages": [
            {"percent": 5, "delay_hours": 24},   # Canary
            {"percent": 25, "delay_hours": 24},
            {"percent": 100, "delay_hours": 0}
        ]
    },
    "maintenance_window": {
        "start": "02:00",
        "end": "05:00",
        "timezone": "America/New_York"
    }
}
```

### 2. Appliance Receives Update Command

```python
# During regular check-in
GET /api/appliances/checkin
Response:
{
    "update_available": true,
    "update": {
        "version": "v44",
        "iso_url": "...",
        "sha256": "...",
        "reboot_after": "02:00"
    }
}
```

### 3. Appliance Downloads & Prepares

```python
# update_agent.py
async def apply_update(update_info):
    # 1. Download to standby partition
    standby = get_standby_partition()  # /dev/sda3 if A is active
    await download_iso(update_info['iso_url'], standby)

    # 2. Verify checksum
    actual_sha = sha256sum(standby)
    if actual_sha != update_info['sha256']:
        raise UpdateError("Checksum mismatch")

    # 3. Mark standby as next boot
    set_next_boot(standby)

    # 4. Report ready
    await report_status("ready_to_reboot", version=update_info['version'])

    # 5. Wait for maintenance window
    await wait_until(update_info['reboot_after'])

    # 6. Reboot
    os.system("systemctl reboot")
```

### 4. Post-Boot Health Check

```python
# health_gate.py (runs early in boot)
def health_check():
    checks = [
        ("network", check_network_connectivity),
        ("agent", check_agent_running),
        ("api", check_central_command_reachable),
        ("time", check_ntp_sync),
    ]

    for name, check in checks:
        if not check():
            return False, name

    return True, None

def boot_health_gate():
    boot_count = get_boot_count()

    if boot_count > 3:
        # Too many failed boots, rollback
        rollback_to_previous()
        return

    increment_boot_count()

    healthy, failed_check = health_check()

    if healthy:
        clear_boot_count()
        mark_current_as_good()
        report_update_success()
    else:
        log(f"Health check failed: {failed_check}")
        # Will try again, or rollback after 3 attempts
```

## Implementation Files

### Appliance Side

| File | Purpose |
|------|---------|
| `iso/appliance-image.nix` | A/B partition layout |
| `packages/compliance-agent/src/compliance_agent/update_agent.py` | Update download/apply |
| `packages/compliance-agent/src/compliance_agent/health_gate.py` | Post-boot health check |
| `packages/compliance-agent/src/compliance_agent/ab_boot.py` | Bootloader management |

### Central Command Side

| File | Purpose |
|------|---------|
| `mcp-server/central-command/backend/fleet_updates.py` | Fleet update API |
| `mcp-server/central-command/backend/rollout.py` | Staged rollout logic |
| `mcp-server/central-command/backend/migrations/022_fleet_updates.sql` | DB schema |
| `mcp-server/central-command/frontend/src/pages/FleetUpdates.tsx` | UI |

## Database Schema

```sql
-- 022_fleet_updates.sql

CREATE TABLE update_releases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version VARCHAR(50) NOT NULL UNIQUE,
    iso_url TEXT NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    release_notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    is_active BOOLEAN DEFAULT true
);

CREATE TABLE update_rollouts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    release_id UUID REFERENCES update_releases(id),
    strategy VARCHAR(20) DEFAULT 'staged',  -- immediate, staged, canary
    current_stage INT DEFAULT 0,
    stages JSONB,  -- [{percent: 5, delay_hours: 24}, ...]
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    status VARCHAR(20) DEFAULT 'in_progress'  -- in_progress, paused, completed, failed
);

CREATE TABLE appliance_updates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    appliance_id UUID REFERENCES appliances(id),
    rollout_id UUID REFERENCES update_rollouts(id),
    status VARCHAR(20) DEFAULT 'pending',
    -- pending, downloading, ready, rebooting, succeeded, failed, rolled_back
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    error_message TEXT,
    previous_version VARCHAR(50),
    new_version VARCHAR(50)
);

CREATE INDEX idx_appliance_updates_status ON appliance_updates(status);
CREATE INDEX idx_appliance_updates_appliance ON appliance_updates(appliance_id);
```

## Rollout Stages

```
Stage 0: Canary (5%)
    │
    ├── Wait 24 hours
    ├── Check: All canary appliances healthy?
    │   ├── Yes → Proceed to Stage 1
    │   └── No → Pause rollout, alert ops
    │
Stage 1: Early Adopters (25%)
    │
    ├── Wait 24 hours
    ├── Check: All Stage 1 appliances healthy?
    │   ├── Yes → Proceed to Stage 2
    │   └── No → Pause rollout, alert ops
    │
Stage 2: Full Fleet (100%)
    │
    └── Monitor for 48 hours
        └── Mark rollout complete
```

## Rollback Scenarios

| Trigger | Action |
|---------|--------|
| Health check fails 3x | Auto-revert to previous partition |
| Central Command detects failure rate >10% | Pause rollout, alert |
| Ops manually triggers | Fleet-wide rollback command |
| Appliance loses connectivity >1hr post-update | Auto-revert |

## Security Considerations

1. **Signed ISOs** - Ed25519 signature verification before apply
2. **Secure download** - mTLS to update server
3. **Rollback protection** - Cannot rollback past N versions
4. **Audit trail** - All update events logged to Central Command

## Maintenance Window

```yaml
# Default maintenance window (per-site configurable)
maintenance_window:
  start: "02:00"
  end: "05:00"
  timezone: "America/New_York"
  days: ["sunday", "monday", "tuesday", "wednesday", "thursday"]
  # No updates Friday/Saturday (healthcare weekend coverage)
```

## CLI Commands

```bash
# Force update check
osiris-update --check

# Show current partition status
osiris-update --status

# Manual rollback
osiris-update --rollback

# Skip next maintenance window
osiris-update --skip-window
```

## Metrics

- `update_download_seconds` - Time to download ISO
- `update_apply_seconds` - Time to write to partition
- `update_reboot_seconds` - Time from reboot to healthy
- `update_rollback_count` - Number of auto-rollbacks
- `update_success_rate` - Fleet-wide success percentage

## Phase 13 Implementation Order

1. ~~**Week 1**: A/B partition scheme in appliance-image.nix~~ 🟡 PENDING
2. ~~**Week 1**: Boot health gate service~~ 🟡 PENDING
3. ~~**Week 2**: Update agent (download, verify, apply)~~ 🟡 PENDING
4. ~~**Week 2**: Central Command fleet API~~ ✅ COMPLETE (Session 54)
5. ~~**Week 3**: Staged rollout logic~~ ✅ COMPLETE (Session 54)
6. ~~**Week 3**: Fleet Updates UI~~ ✅ COMPLETE (Session 54)
7. **Week 4**: Testing with VM fleet
8. **Week 4**: Production rollout to physical appliances

### Session 54 Accomplishments
- Fleet Updates UI deployed at dashboard.osiriscare.net/fleet-updates
- Test release v44 created with staged rollout (5% → 25% → 100%)
- Rollout controls tested: pause, resume, advance stage all working
- Database tables verified with test data

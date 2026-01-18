# Session 55: A/B Partition Zero-Touch Update System

**Date:** 2026-01-18
**Agent Version:** 1.0.44
**Phase:** 13 - Launch Readiness

## Summary

Implemented the appliance-side A/B partition update system for zero-touch remote updates with automatic rollback on failure. The Central Command UI (Fleet Updates) was already deployed in Session 54 - this session focused on appliance-side implementation.

## Changes Made

### New Files
1. **`packages/compliance-agent/src/compliance_agent/health_gate.py`** (350 lines)
   - Standalone module for post-boot health verification
   - Detects active partition from kernel cmdline and ab_state file
   - Runs health checks (network, NTP, disk space)
   - Automatic rollback after 3 failed boot attempts
   - Reports status to Central Command

2. **`iso/grub-ab.cfg`** (65 lines)
   - GRUB configuration for A/B partition boot selection
   - Reads ab_state file to determine active partition
   - Passes `ab.partition=A|B` via kernel cmdline
   - Recovery menu entries for manual partition selection

3. **`packages/compliance-agent/tests/test_health_gate.py`** (350 lines)
   - 25 unit tests covering all health gate functionality
   - Tests for partition detection, boot state, health checks
   - Tests for rollback trigger conditions

### Modified Files
1. **`packages/compliance-agent/src/compliance_agent/update_agent.py`**
   - Updated `get_partition_info()` to detect partition from kernel cmdline first
   - Updated `set_next_boot()` to write GRUB-compatible source format
   - Updated `mark_current_as_good()` to use new format

2. **`packages/compliance-agent/setup.py`**
   - Added `health-gate` entry point
   - Added `osiris-update` entry point

3. **`iso/appliance-image.nix`**
   - Added `msp-health-gate` systemd service
   - Updated compliance-agent to depend on health-gate
   - Enabled `/var/lib/msp` data partition mount (partlabel)
   - Enabled `/boot` partition mount for ab_state
   - Added update directories to activation script
   - Updated version to 1.0.44

4. **`packages/compliance-agent/src/compliance_agent/appliance_agent.py`**
   - Added `update_iso` order handler
   - Implemented `_handle_update_iso()` for Fleet Updates integration
   - Added `_do_reboot()` helper method

## Disk Layout Reference

```
/dev/sda (HP T640 internal SSD)
├── /dev/sda1  512MB   ESP (FAT32) - GRUB, ab_state
├── /dev/sda2  2GB     Partition A (squashfs)
├── /dev/sda3  2GB     Partition B (squashfs)
└── /dev/sda4  *       Data (ext4) - /var/lib/msp
```

## Update Flow

1. Central Command creates release and starts rollout
2. Appliance receives `update_iso` order during checkin
3. Update agent downloads ISO with resume support
4. SHA256 verification before applying
5. Writes ISO to standby partition via dd
6. Sets `ab_state` to boot from standby
7. Waits for maintenance window (if configured)
8. Reboots to new partition
9. Health gate runs at boot, verifies system
10. If healthy: marks partition as good, reports success
11. If unhealthy after 3 boots: rolls back to previous partition

## Test Results

- **25 new health_gate tests pass**
- **834 total tests pass** (2 pre-existing failures unrelated to this change)

## Verification Commands

```bash
# Run health gate tests
python -m pytest tests/test_health_gate.py -v

# Manual health gate status check
health-gate --status

# Manual health check
health-gate --check

# Update agent status
osiris-update --status
```

## Next Steps

1. Build new ISO with A/B partition layout
2. Test full update cycle in VM
3. Deploy to physical appliance
4. Test rollback scenario

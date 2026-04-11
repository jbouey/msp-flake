# OsirisCare Installer Redesign — Enterprise Zero-Friction Deployment

**Date:** 2026-04-11
**Authors:** Round Table (Principal SWE, CCIE, DBA, PM)
**Status:** Draft — pending user approval

---

## 1. Customer Story (PM)

Sarah is a clinic office manager. She gets a FedEx box containing a small black box and a USB stick with a green "OsirisCare" label. A card says:

> 1. Plug the black box into power and ethernet
> 2. Insert the USB stick and turn it on
> 3. Wait for the green checkmark (about 3 minutes)
> 4. Remove the USB stick
> 5. You're done

Sarah does this during lunch. She doesn't touch a keyboard. Her MSP partner sees the appliance in their dashboard 4 minutes later. She never thinks about it again.

**Hard requirements from this story:**
- Zero keyboard interaction
- Zero network dependency during install
- Zero dialog boxes or prompts
- Visual progress the entire time (never a blank/frozen screen)
- Clear "done" state (not a countdown race)
- Works on USB 2.0 and USB 3.0 (just slower on 2.0)
- Works on SSD, NVMe, and eMMC (HP T640, T740, any thin client)
- Re-running the installer on the same hardware is safe (wipe + reinstall)
- The USB works on ANY supported hardware without per-device customization

---

## 2. Architecture (Principal SWE)

### What changes

**Before:** ISO boots live NixOS → `nixos-install --flake github:...` fetches ~5000 packages from GitHub → builds system on device → 10 minutes, requires internet.

**After:** ISO contains a pre-built raw disk image of the complete NixOS system. Installer boots minimal Linux → `dd` writes image to internal drive → writes config from USB → powers off. 2-3 minutes, zero internet.

### Build pipeline

```
VPS (nix build)
├── Builds complete NixOS system closure (appliance-disk-image.nix)
├── Produces raw disk image (~2GB) with:
│   ├── Partition 1: ESP (512MB, FAT32, systemd-boot)
│   ├── Partition 2: MSP-DATA (2GB, ext4, persistent config/evidence)
│   └── Partition 3: Root (remaining, ext4, NixOS system)
├── Wraps in bootable USB image:
│   ├── Minimal Linux kernel + initramfs (installer only)
│   ├── The pre-built disk image (compressed, ~1.5GB)
│   └── Config partition (FAT32, readable by admin to drop config.yaml)
└── Output: osiriscare-installer-v{VERSION}.img (~2GB USB image)
```

### Install flow (on device)

```
USB boot
├── Kernel + initramfs load (10-30s depending on USB speed)
├── Show splash: "OsirisCare — Installing..." with progress bar
├── Detect internal drive (SSD/NVMe/eMMC)
│   ├── Found → continue
│   └── Not found → show error, halt (no dialog, just red text + "attach drive and reboot")
├── Check for existing install
│   ├── Found → show "Reinstalling in 10s..." countdown (Ctrl+C to cancel)
│   └── Clean → continue immediately
├── Decompress + dd system image to internal drive
│   ├── Progress bar updates every 1% (real progress, not fake)
│   └── Takes 1-3 minutes depending on drive speed
├── Resize root partition to fill drive
├── Write config.yaml from USB (if present) to MSP-DATA partition
├── Write SSH authorized_keys from USB (if present)
├── Generate unique machine-id
├── Unmount USB cleanly (sync + umount -l)
├── Show green checkmark: "Installation complete. Remove USB and power on."
├── Halt (NOT reboot, NOT poweroff timer — just halt and wait)
└── User removes USB, presses power button
```

### First boot (from internal drive)

```
NixOS boots from internal drive
├── systemd starts all services
├── msp-auto-provision checks for config:
│   ├── /var/lib/msp/config.yaml exists (from USB) → use it, start daemon
│   ├── config.yaml missing → try network provisioning:
│   │   ├── Get MAC address (deterministic, sorted by interface name)
│   │   ├── Call /api/provision/{MAC} over HTTPS
│   │   ├── If site assigned → receive signed config → verify → write → start daemon
│   │   ├── If unclaimed → auto-register, poll every 30s, show on "unclaimed" list
│   │   └── If no network → retry indefinitely with exponential backoff
│   └── Config obtained → start appliance-daemon
├── Daemon checks in to Central Command
├── Appliance appears in dashboard
└── Done
```

---

## 3. Network Architecture (CCIE)

### Zero network during install

The installer NEVER touches the network. No DHCP, no DNS, no GitHub, no Central Command. The install is pure disk I/O.

Network is only needed on FIRST BOOT from the installed system, and ONLY if no config.yaml was provided on the USB.

### Provisioning priority (first boot)

1. **USB config** (highest priority) — config.yaml on USB's MSP-DATA partition or config partition. Zero network needed. Instant.
2. **Network auto-register** — MAC lookup via HTTPS. Requires DHCP + DNS + internet. Falls back with exponential backoff.
3. **Link-local mDNS** — If network provisioning fails, discover sibling appliances via mDNS and request config relay. Future enhancement.

### Failure recovery

| Failure | Behavior |
|---------|----------|
| No internal drive | Red error text, halt. User attaches drive, reboots. |
| USB read error | Retry 3x, then halt with error. |
| Image decompression fails | Halt with error + checksum info for debugging. |
| No network on first boot | Retry provisioning with exponential backoff (30s → 60s → 120s → 300s max). Show status on console. |
| No config + no network | Keep retrying. Console shows: "Waiting for network... Plug in ethernet or provide config.yaml on USB." |
| Central Command unreachable | Same backoff. Daemon starts in offline mode when config is present. |

---

## 4. Visual Design

### Boot splash (first 10-30s)

```
┌──────────────────────────────────────────────────────┐
│                                                      │
│              ╔═══════════════════════╗                │
│              ║     OsirisCare        ║                │
│              ╚═══════════════════════╝                │
│                                                      │
│         Loading installer...  ████░░░░░░  40%        │
│                                                      │
│         USB 3.0 detected — est. 2 minutes            │
│                                                      │
└──────────────────────────────────────────────────────┘
```

### Install progress (1-3 min)

```
┌──────────────────────────────────────────────────────┐
│                                                      │
│              OsirisCare Installer v9                  │
│                                                      │
│  Target: MMC 32GB (/dev/mmcblk0)                     │
│                                                      │
│  ████████████████████░░░░░░░░░░  67%                 │
│                                                      │
│  Writing system image...  1.1 GB / 1.6 GB            │
│  Speed: 45 MB/s — about 12 seconds remaining         │
│                                                      │
│  ────────────────────────────────────────────         │
│  Step 1: Detect hardware        ✓                    │
│  Step 2: Write system image     ▸ (in progress)      │
│  Step 3: Resize partitions      ○                    │
│  Step 4: Write configuration    ○                    │
│  Step 5: Finalize               ○                    │
│                                                      │
└──────────────────────────────────────────────────────┘
```

### Completion (halt + wait)

```
┌──────────────────────────────────────────────────────┐
│                                                      │
│              OsirisCare Installer v9                  │
│                                                      │
│         ✓  Installation Complete                     │
│                                                      │
│  ████████████████████████████████  100%               │
│                                                      │
│  ────────────────────────────────────────────         │
│  Step 1: Detect hardware        ✓                    │
│  Step 2: Write system image     ✓  (1.6 GB in 38s)  │
│  Step 3: Resize partitions      ✓                    │
│  Step 4: Write configuration    ✓  (config.yaml)     │
│  Step 5: Finalize               ✓                    │
│  ────────────────────────────────────────────         │
│                                                      │
│  ┌──────────────────────────────────────────┐        │
│  │                                          │        │
│  │   Remove the USB drive.                  │        │
│  │   Press the power button to start.       │        │
│  │                                          │        │
│  │   The appliance will connect to your     │        │
│  │   management dashboard automatically.    │        │
│  │                                          │        │
│  └──────────────────────────────────────────┘        │
│                                                      │
│  Debug: Alt+F3 | Log: /tmp/install.log               │
│                                                      │
└──────────────────────────────────────────────────────┘
```

### Error state (halt, no dialog)

```
┌──────────────────────────────────────────────────────┐
│                                                      │
│              OsirisCare Installer v9                  │
│                                                      │
│         ✗  Installation Failed                       │
│                                                      │
│  No internal drive found.                            │
│                                                      │
│  Available devices:                                  │
│    sda   32 GB  USB  SanDisk Cruzer (this USB)       │
│                                                      │
│  Attach an internal drive (SSD, NVMe, or eMMC)       │
│  and reboot to try again.                            │
│                                                      │
│  Debug: Alt+F3 | Log: /tmp/install.log               │
│                                                      │
└──────────────────────────────────────────────────────┘
```

---

## 5. USB Layout

The USB stick has 3 partitions:

```
/dev/sda
├── sda1: ESP (FAT32, 32MB) — EFI bootloader + kernel + initramfs
├── sda2: IMAGE (ext4/squashfs, ~1.6GB) — compressed system disk image
└── sda3: CONFIG (FAT32, 100MB) — user-writable, holds config.yaml + SSH keys
```

**Why FAT32 for CONFIG:** Any OS (Windows, Mac, Linux) can read/write FAT32. The admin generates a deployment pack, downloads config.yaml, plugs the USB into their laptop, drops the file on the CONFIG partition. No special tools.

**The CONFIG partition is OPTIONAL.** If empty, the appliance provisions via network on first boot. If populated, it provisions instantly from the USB config. Both paths work.

---

## 6. Deployment Pack (Central Command)

### Admin flow

1. Admin clicks "Deploy Appliance" on a site's page
2. Modal shows two options:
   - **Download config.yaml** — for USB provisioning (recommended)
   - **Skip — appliance will self-register** — for network provisioning
3. If downloading: config.yaml contains site_id, API key, SSH keys, signed by server
4. Admin copies config.yaml to the USB's CONFIG partition
5. USB is ready for any hardware — no MAC needed

### config.yaml format

```yaml
site_id: north-valley-branch-2
api_key: fxtpL-a1cuM0JsTxfXiXN5--5f3oNYMArcFN0HYKeyo
api_endpoint: https://api.osiriscare.net
ssh_authorized_keys:
  - ssh-ed25519 AAAA... admin@osiriscare
signature: 904b211d...  # Ed25519 signature of the config
```

---

## 7. Drive Detection (all platforms)

Single function, used by both installer and installed system:

```bash
detect_internal_drive() {
  for dev in /dev/nvme0n1 /dev/sda /dev/sdb /dev/vda /dev/mmcblk0 /dev/mmcblk1; do
    [ -b "$dev" ] || continue
    # Skip the USB we booted from
    BOOT_DEV=$(findmnt -n -o SOURCE / | sed 's/[0-9]*$//' | sed 's/p[0-9]*$//')
    echo "$dev" | grep -q "$(basename $BOOT_DEV)" && continue
    # Skip removable
    DEV_NAME=$(basename "$dev")
    case "$DEV_NAME" in mmcblk*) SYSDEV="$DEV_NAME" ;; *) SYSDEV=$(echo "$DEV_NAME" | sed 's/[0-9]*$//') ;; esac
    [ "$(cat /sys/block/$SYSDEV/removable 2>/dev/null)" = "1" ] && continue
    # Must be >16GB
    SIZE=$(blockdev --getsize64 "$dev" 2>/dev/null || echo "0")
    [ "$SIZE" -gt 16000000000 ] && echo "$dev" && return 0
  done
  return 1
}
```

Shared between `appliance-image.nix` (installer) and `appliance-disk-image.nix` (installed system). Single source of truth.

---

## 8. Re-install Safety

When the installer detects an existing NixOS installation:
- Show "Existing installation found. Reinstalling in 10 seconds..."
- Countdown is read-only (no dialog, no keypress required)
- Ctrl+C cancels (for technicians who want to abort)
- After 10s: wipe and reinstall automatically
- **No efibootmgr manipulation** — the BIOS boot order is never touched

Re-provisioning on Central Command side:
- Same MAC checks in → upsert updates the existing `site_appliances` row
- `deleted_at` is cleared (soft-delete recovery)
- `first_checkin` is preserved
- `display_name` is preserved or auto-generated

---

## 9. What's NOT in this spec (out of scope)

- QR code pairing (future enhancement)
- Batch deployment tool (future — generate N config.yamls at once)
- Custom branding on installer screen (future — partner logo)
- Automatic USB creation tool (future — admin downloads .img, we provide flasher)
- A/B partition updates (existing, unchanged)
- WireGuard configuration (handled by emergency access system, unchanged)

---

## 10. Success Criteria

- [ ] Fresh install on SSD: under 2 minutes, zero interaction
- [ ] Fresh install on eMMC: under 3 minutes, zero interaction
- [ ] Fresh install on USB 2.0: under 5 minutes, zero interaction
- [ ] Reinstall on same hardware: works without BIOS changes
- [ ] No network during install
- [ ] Config from USB: appliance online in dashboard within 60s of first boot
- [ ] No config on USB: appliance appears in "unclaimed" list within 60s
- [ ] Progress visible at all times (no blank/frozen screen)
- [ ] Error states halt cleanly with readable message
- [ ] Debug console available (Alt+F3) without blocking install
- [ ] Works on: HP T640, HP T740, generic x86_64 UEFI hardware

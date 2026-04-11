# Enterprise Installer Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the nixos-install-from-GitHub installer with a pre-built disk image writer that deploys in under 3 minutes with zero network dependency.

**Architecture:** VPS builds a complete NixOS system as a compressed raw disk image. The installer USB boots minimal Linux, streams the image to the internal drive via zstd+dd, writes optional config from a USB partition, and halts. First boot provisions from USB config or network.

**Tech Stack:** Nix (derivation for raw image), bash (installer script), zstd (compression), pv (progress), NixOS (installed system)

**Spec:** `docs/superpowers/specs/2026-04-11-installer-redesign.md`

---

## Phase 1: Raw Disk Image Build

### Task 1: Create the raw image Nix derivation

**Files:**
- Create: `iso/raw-image.nix`
- Modify: `flake.nix` (add new output)

- [ ] **Step 1: Create `iso/raw-image.nix` skeleton**

```nix
# iso/raw-image.nix
# Builds a raw partitioned disk image containing the complete NixOS
# appliance system. This is NOT a live ISO — it's the installed system
# ready to dd onto internal storage.
#
# Output: osiriscare-system.raw.zst (~1.5GB compressed)
# Layout:
#   Partition 1: ESP (512MB, FAT32, systemd-boot + kernel)
#   Partition 2: MSP-DATA (2GB, ext4, persistent config/evidence)
#   Partition 3: Root (4GB, ext4, NixOS system closure)
#
# The installer decompresses and dds this to the internal drive,
# then runs growpart to expand partition 3 to fill the drive.

{ pkgs, lib, nixosConfig }:

let
  # The fully evaluated NixOS system (toplevel = /nix/store/...-nixos-system-...)
  toplevel = nixosConfig.config.system.build.toplevel;

  # Sizes in MB
  espSize = 512;
  dataSize = 2048;
  rootSize = 4096;
  totalSize = espSize + dataSize + rootSize + 1; # +1 for GPT overhead

  partScript = pkgs.writeShellScript "create-partitions" ''
    set -euo pipefail
    IMG="$1"

    # Create sparse image file
    truncate -s ${toString totalSize}M "$IMG"

    # Partition with GPT
    ${pkgs.parted}/bin/parted -s "$IMG" -- \
      mklabel gpt \
      mkpart ESP fat32 1MiB ${toString (espSize + 1)}MiB \
      set 1 esp on \
      mkpart MSP-DATA ext4 ${toString (espSize + 1)}MiB ${toString (espSize + dataSize + 1)}MiB \
      mkpart nixos ext4 ${toString (espSize + dataSize + 1)}MiB 100%
  '';

  populateScript = pkgs.writeShellScript "populate-image" ''
    set -euo pipefail
    IMG="$1"
    TOPLEVEL="${toplevel}"

    # Set up loop device
    LOOP=$(${pkgs.util-linux}/bin/losetup --find --show --partscan "$IMG")
    cleanup() { ${pkgs.util-linux}/bin/losetup -d "$LOOP" 2>/dev/null || true; }
    trap cleanup EXIT

    # Wait for partition devices
    sleep 1
    ${pkgs.util-linux}/bin/partprobe "$LOOP"
    sleep 1

    # Format partitions
    ${pkgs.dosfstools}/bin/mkfs.fat -F32 -n ESP "''${LOOP}p1"
    ${pkgs.e2fsprogs}/bin/mkfs.ext4 -L MSP-DATA -F "''${LOOP}p2"
    ${pkgs.e2fsprogs}/bin/mkfs.ext4 -L nixos -F "''${LOOP}p3"

    # Mount
    MOUNTPOINT=$(mktemp -d)
    mount "''${LOOP}p3" "$MOUNTPOINT"
    mkdir -p "$MOUNTPOINT/boot" "$MOUNTPOINT/var/lib/msp"
    mount "''${LOOP}p1" "$MOUNTPOINT/boot"
    mount "''${LOOP}p2" "$MOUNTPOINT/var/lib/msp"

    # Copy NixOS system closure
    echo "Copying system closure to image..."
    mkdir -p "$MOUNTPOINT/nix/store"
    ${pkgs.rsync}/bin/rsync -a --info=progress2 \
      $(${pkgs.nix}/bin/nix-store -qR "$TOPLEVEL" | tr '\n' ' ') \
      "$MOUNTPOINT/nix/store/"

    # Create system profile
    mkdir -p "$MOUNTPOINT/nix/var/nix/profiles"
    ln -sfn "$TOPLEVEL" "$MOUNTPOINT/nix/var/nix/profiles/system"
    mkdir -p "$MOUNTPOINT/etc"
    ln -sfn /nix/var/nix/profiles/system/etc/NIXOS "$MOUNTPOINT/etc/NIXOS" 2>/dev/null || true

    # Activate system (creates /etc symlinks etc.)
    NIXOS_INSTALL_BOOTLOADER=1 \
      ${pkgs.nixos-install-tools}/bin/nixos-enter --root "$MOUNTPOINT" -- \
      /nix/var/nix/profiles/system/bin/switch-to-configuration boot 2>/dev/null || true

    # Install systemd-boot
    ${pkgs.systemd}/bin/bootctl install \
      --esp-path="$MOUNTPOINT/boot" \
      --root="$MOUNTPOINT" 2>/dev/null || true

    # Write boot entry
    mkdir -p "$MOUNTPOINT/boot/loader/entries"
    KERNEL=$(ls "$MOUNTPOINT/nix/var/nix/profiles/system/kernel" 2>/dev/null || echo "")
    INITRD=$(ls "$MOUNTPOINT/nix/var/nix/profiles/system/initrd" 2>/dev/null || echo "")
    INIT="$TOPLEVEL/init"
    cat > "$MOUNTPOINT/boot/loader/entries/nixos.conf" <<BOOT
    title OsirisCare Appliance
    linux /nix/var/nix/profiles/system/kernel
    initrd /nix/var/nix/profiles/system/initrd
    options init=$INIT root=LABEL=nixos
    BOOT

    cat > "$MOUNTPOINT/boot/loader/loader.conf" <<LOADER
    default nixos.conf
    timeout 0
    editor no
    LOADER

    # Generate machine-id
    ${pkgs.systemd}/bin/systemd-machine-id-setup --root="$MOUNTPOINT"

    # Ensure fstab uses labels
    cat > "$MOUNTPOINT/etc/fstab" <<FSTAB
    LABEL=nixos  /               ext4  defaults,noatime  0 1
    LABEL=ESP    /boot           vfat  defaults,nofail   0 2
    LABEL=MSP-DATA /var/lib/msp  ext4  defaults,noatime,nofail  0 2
    FSTAB

    # Unmount
    umount "$MOUNTPOINT/var/lib/msp"
    umount "$MOUNTPOINT/boot"
    umount "$MOUNTPOINT"
    rmdir "$MOUNTPOINT"
  '';

in pkgs.runCommand "osiriscare-system-image" {
  nativeBuildInputs = with pkgs; [
    parted dosfstools e2fsprogs util-linux
    rsync nix nixos-install-tools systemd
    zstd
  ];

  # Need /dev/loop* access — requires sandbox disabled or --option sandbox false
  __noChroot = true;
} ''
  echo "=== Building OsirisCare raw disk image ==="

  # Create and partition
  ${partScript} image.raw

  # Populate with NixOS system
  ${populateScript} image.raw

  # Compress with zstd (high compression, fast decompression)
  echo "Compressing image with zstd..."
  ${pkgs.zstd}/bin/zstd -19 -T0 image.raw -o osiriscare-system.raw.zst

  # Output
  mkdir -p $out
  mv osiriscare-system.raw.zst $out/
  echo "${toplevel}" > $out/system-closure-path
  echo "$(stat -c %s image.raw)" > $out/decompressed-size

  echo "=== Done: $out/osiriscare-system.raw.zst ==="
  echo "Compressed: $(du -h $out/osiriscare-system.raw.zst | cut -f1)"
  echo "Decompressed: $(du -h image.raw | cut -f1)"
''
```

- [ ] **Step 2: Register in flake.nix**

Add to `flake.nix` after the existing `appliance-iso` output:

```nix
# Pre-built raw disk image for zero-friction installer
packages.x86_64-linux.appliance-raw-image =
  pkgs.callPackage ./iso/raw-image.nix {
    nixosConfig = self.nixosConfigurations.osiriscare-appliance-disk;
  };
```

- [ ] **Step 3: Test build locally**

Run: `nix build .#appliance-raw-image --no-link --print-out-paths`

This will take 10-20 minutes on first build (copies entire NixOS closure).
Expected output: `/nix/store/...-osiriscare-system-image/osiriscare-system.raw.zst`

- [ ] **Step 4: Verify image contents**

```bash
RESULT=$(nix build .#appliance-raw-image --no-link --print-out-paths)
ls -lh "$RESULT/"
# Expected: osiriscare-system.raw.zst (~1.5GB), system-closure-path, decompressed-size
```

- [ ] **Step 5: Commit**

```bash
git add iso/raw-image.nix flake.nix
git commit -m "feat: raw disk image Nix derivation for zero-friction installer

Builds a complete NixOS appliance as a compressed raw disk image.
3 partitions: ESP (systemd-boot) + MSP-DATA (persistent) + root (NixOS).
Compressed with zstd-19. No nixos-install, no GitHub fetch at install time."
```

---

### Task 2: Rewrite installer script (appliance-image.nix)

**Files:**
- Modify: `iso/appliance-image.nix` (complete rewrite of msp-auto-install service)

- [ ] **Step 1: Replace the nixos-install script with dd-based installer**

The new installer:
1. Detects internal drive (shared function with appliance-disk-image.nix)
2. Measures USB read speed → estimates time
3. Streams zstd-compressed image to drive via dd with progress
4. Runs growpart + resize2fs on root partition
5. Copies config.yaml from USB CONFIG partition (if present)
6. Halts cleanly

Key changes:
- Remove `nixos-install --flake` call entirely
- Remove `dialog` dependency (all output via echo/printf to framebuffer)
- Remove `efibootmgr` (never touch BIOS boot order)
- Add `pv` and `zstd` to path
- Add `cloud-utils` (for `growpart`)

The full script is ~200 lines of bash. The visual output uses ANSI escape codes
(printf with colors) — no ncurses, no dialog.

- [ ] **Step 2: Update ISO to include the raw system image**

The ISO derivation must include the `.raw.zst` file so the installer can find it:

```nix
# In appliance-image.nix or a new installer-image.nix:
environment.etc."installer/osiriscare-system.raw.zst".source =
  "${appliance-raw-image}/osiriscare-system.raw.zst";
environment.etc."installer/decompressed-size".source =
  "${appliance-raw-image}/decompressed-size";
```

- [ ] **Step 3: Add three-partition USB layout**

The USB needs a CONFIG partition (FAT32) that admins can write to:

```nix
# Add to ISO configuration
fileSystems."/config" = {
  device = "/dev/disk/by-label/OSCONFIG";
  fsType = "vfat";
  options = [ "nofail" "ro" ];
};
```

- [ ] **Step 4: Test on VPS**

```bash
ssh root@VPS "cd /opt/msp-flake && git pull && nix build .#appliance-iso --no-link --print-out-paths"
```

- [ ] **Step 5: Test on hardware (HP T740)**

Flash to USB, boot, verify:
- Visual progress displayed throughout
- No blank/frozen screens
- Install completes in <3 minutes on eMMC
- Halts with green checkmark
- Reboots to working NixOS after USB removal

- [ ] **Step 6: Commit**

```bash
git add iso/appliance-image.nix
git commit -m "feat: rewrite installer — dd-based, zero-network, visual progress

Replaces nixos-install (fetches from GitHub) with pre-built image write.
Zero network dependency. Real progress bar with speed + time estimate.
Halts on completion (no reboot race). Supports SSD, NVMe, eMMC."
```

---

### Task 3: Config partition + deployment pack integration

**Files:**
- Modify: `iso/appliance-image.nix` (installer reads config from USB)
- Modify: `iso/appliance-disk-image.nix` (first-boot reads config)
- Already done: `routes.py` (deployment-pack endpoint)

- [ ] **Step 1: Installer writes config from USB to installed system**

After dd + resize, before halt:

```bash
# Check for config on USB CONFIG partition
CONFIG_SRC=""
for path in /config/config.yaml /config/osiriscare/config.yaml /config/msp/config.yaml; do
  [ -f "$path" ] && CONFIG_SRC="$path" && break
done

if [ -n "$CONFIG_SRC" ]; then
  # Mount the installed system's MSP-DATA partition
  mount "${ROOT_PART%3}2" /tmp/msp-data  # partition 2 = MSP-DATA
  cp "$CONFIG_SRC" /tmp/msp-data/config.yaml
  chmod 600 /tmp/msp-data/config.yaml

  # Copy SSH keys if present
  if [ -f "$(dirname $CONFIG_SRC)/authorized_keys" ]; then
    mkdir -p /tmp/msp-data/.ssh
    cp "$(dirname $CONFIG_SRC)/authorized_keys" /tmp/msp-data/.ssh/authorized_keys
  fi

  umount /tmp/msp-data
  show_step "Write configuration" "done" "config.yaml from USB"
else
  show_step "Write configuration" "skip" "no config on USB — will provision via network"
fi
```

- [ ] **Step 2: Verify first-boot provisioning reads from MSP-DATA**

The existing `msp-auto-provision` script (line 919 of appliance-disk-image.nix)
already checks `/var/lib/msp/config.yaml` first. Since MSP-DATA mounts to
`/var/lib/msp`, the config written by the installer is automatically found.
No changes needed to the first-boot provisioning script for this path.

- [ ] **Step 3: Test both paths**

Test 1: USB with config.yaml → install → first boot → daemon starts immediately
Test 2: USB without config.yaml → install → first boot → network provisioning

- [ ] **Step 4: Commit**

```bash
git add iso/appliance-image.nix
git commit -m "feat: installer copies config.yaml from USB CONFIG partition

If admin placed config.yaml on the USB, the installer copies it to the
installed system's MSP-DATA partition. First boot finds it immediately —
no network provisioning needed. Zero-friction offline deployment."
```

---

### Task 4: API key single-use rotation

**Files:**
- Modify: `mcp-server/central-command/backend/sites.py` (checkin handler)
- Modify: `appliance/internal/daemon/phonehome.go` (daemon key rotation)

- [ ] **Step 1: Server sends rotated key on first checkin**

In sites.py checkin handler, after successful registration of a new appliance:

```python
# If this is the appliance's first checkin (no previous last_checkin),
# rotate the API key and include the new one in the response.
if not last_checkin_time:
    new_key = secrets.token_urlsafe(32)
    new_hash = hashlib.sha256(new_key.encode()).hexdigest()
    await conn.execute("""
        UPDATE api_keys SET active = false
        WHERE site_id = $1 AND key_hash = $2
    """, checkin.site_id, current_key_hash)
    await conn.execute("""
        INSERT INTO api_keys (key_hash, site_id, active, created_at, description)
        VALUES ($1, $2, true, NOW(), 'Rotated on first checkin')
    """, new_hash, checkin.site_id)
    checkin_response["rotated_api_key"] = new_key
```

- [ ] **Step 2: Daemon detects and saves rotated key**

In phonehome.go, after successful checkin response:

```go
if newKey := resp.RotatedAPIKey; newKey != "" {
    slog.Info("API key rotated by server — updating config", "component", "daemon")
    d.config.APIKey = newKey
    if err := d.config.WriteToFile(); err != nil {
        slog.Error("Failed to write rotated API key", "error", err)
    }
}
```

- [ ] **Step 3: Test**

1. Create provision with API key
2. First checkin → verify old key deactivated, new key in config.yaml
3. Second checkin → verify uses new key, no rotation

- [ ] **Step 4: Commit**

```bash
git add mcp-server/central-command/backend/sites.py appliance/internal/daemon/phonehome.go
git commit -m "security: API key single-use rotation on first checkin

USB provisioning keys are single-use. On first successful checkin,
server deactivates the old key and returns a rotated replacement.
Daemon writes the new key to config.yaml. If the USB is lost before
deployment, the key is useless — revocable from Central Command."
```

---

### Task 5: Verify + ship

- [ ] **Step 1: Full end-to-end test**

1. Build ISO on VPS
2. Flash to USB on Mac
3. Generate deployment pack in Central Command
4. Copy config.yaml to USB CONFIG partition
5. Boot HP T740 from USB
6. Verify: visual progress, <3 min install, green checkmark, halt
7. Remove USB, power on
8. Verify: boots from eMMC, daemon starts, checks in within 60s
9. Verify: appears in Central Command dashboard
10. Verify: API key rotated on first checkin

- [ ] **Step 2: Test without config (network provisioning path)**

1. Boot fresh hardware from USB (no config.yaml on USB)
2. Verify: installs, boots, polls Central Command
3. MAC appears in "unclaimed" list
4. Admin claims → next poll receives config → daemon starts

- [ ] **Step 3: Test reinstall (same hardware)**

1. Boot installed appliance from USB again
2. Verify: "Reinstalling in 10s..." countdown (no dialog)
3. Wipes and reinstalls cleanly
4. USB boot works (no efibootmgr hijack)

- [ ] **Step 4: Final commit + push**

```bash
git add -A
git commit -m "feat: enterprise zero-friction installer — complete implementation"
git push origin main
```

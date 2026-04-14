# Installer ISO v13 Hardening (Session 206)

Production failure on 2026-04-13: HP t640 Thin Client with AMD Ryzen Embedded / Radeon Vega + prior Windows install, installer stuck for 23+ minutes. Screen showed warped framebuffer at "3%"; install log truncated at `STEP 4 Checking target disk…`. Root cause was two separate bugs stacked:

1. **`lsblk -rno NAME,LABEL` filesystem probe hung on a BitLocker / corrupted NTFS header** — no built-in timeout
2. **amdgpu incomplete init crashed the framebuffer** — display garbled, TTY1 unreadable while install script hung invisibly underneath

This document is the standing reference for the design choices that prevent this class of failure, with citations.

---

## Design principles

1. **Every external command in the install flow is bounded.** No naked `lsblk`, `blkid`, `sfdisk`, `wipefs`, `curl`, `dd`, `sgdisk`, `ntpdate`. Every one wrapped in `timeout --kill-after=N M <cmd>`.
2. **Non-fatal wherever possible.** The dd pass overwrites the target disk unconditionally; the operations before it are courtesy UX and MUST NOT block install on their failure.
3. **Pre-wipe before probe.** Neutralize GPT + MBR + FS signatures BEFORE anything downstream could read them. Eliminates the "probe a half-readable filesystem" hang surface entirely.
4. **Kernel params defensive on AMD graphics.** Ryzen Embedded with partial firmware support crashes amdgpu. Trade GPU for reliability in the installer environment.
5. **Drop-to-shell escape hatch on any multi-minute hang.** Operator always has a way to diagnose.
6. **Serial console fallback.** If GPU is broken, operators with IPMI / console server / USB-serial still see the install.

---

## v13 changes

### Kernel parameters (`appliance-image.nix:170`)

Before:
```nix
boot.kernelParams = [
  "quiet" "loglevel=1" "systemd.show_status=false"
  "console=tty1" "console=ttyS0,115200"
  "nosoftlockup" "audit=0"
];
```

After:
```nix
boot.kernelParams = [
  "quiet" "loglevel=1" "systemd.show_status=false"
  "console=tty1" "console=ttyS0,115200"
  "nosoftlockup" "audit=0"
  "nomodeset"                         # VESA fallback, bypass KMS
  "video=efifb:off"                   # stop efifb from claiming fb0
  "video=vesafb:off"                  # stop vesafb from fighting
  "initcall_blacklist=sysfb_init"     # stop sysfb init race with drm
  "fbcon=map:1"                       # fbcon on fb1, drm on fb0 if loaded
  "amdgpu.dc=0"                       # disable AMDGPU Display Core
  "iommu=pt"                          # passthrough IOMMU for Ryzen Embedded
];
```

### Pre-wipe step replaces the cosmetic reinstall-countdown (`appliance-image.nix:916`)

Before:
```bash
NIXOS_PART=$(lsblk -rno NAME,LABEL "$INTERNAL_DEV" | grep nixos | head -1)  # UNBOUNDED, HANGS ON BITLOCKER
if [ -n "$NIXOS_PART" ]; then
    # show 10s countdown
fi
```

After:
```bash
# Bounded neutralization. Non-fatal; dd overwrites unconditionally.
timeout --kill-after=3 10 sgdisk --zap-all --force "$INTERNAL_DEV"  \
  || log "sgdisk --zap-all returned non-zero (non-fatal)"
timeout --kill-after=3 10 wipefs --all --force "$INTERNAL_DEV"  \
  || log "wipefs --all returned non-zero (non-fatal)"
timeout --kill-after=3 5 partprobe "$INTERNAL_DEV"  \
  || log "partprobe returned non-zero (non-fatal)"
```

### Packages (`appliance-image.nix:265`)

Added `gptfdisk` for `sgdisk`. `util-linux` already provides `wipefs`.

---

## Industry-comparable patterns

### Talos Linux
Uses declarative disk wipe via `talos.experimental.wipe=system` kernel param. For new installs on disks with existing partition tables, Talos recommends `sgdisk --zap-all` via its `wipe` option — exact pattern we adopted.
Ref: [siderolabs/talos#10646](https://github.com/siderolabs/talos/issues/10646), [siderolabs/talos#9408](https://github.com/siderolabs/talos/issues/9408)

### Rook / Ceph disk preparation
Calls `sgdisk --zap-all` + `dd if=/dev/zero of=<dev> bs=1M count=100` to clear FS signatures before creating OSDs. Same approach: nuke first, probe never.
Ref: [rook/rook#717](https://github.com/rook/rook/issues/717)

### util-linux wipefs(8) doctrine
*"wipefs can erase filesystem, raid or partition-table signatures from the specified device to make the signatures invisible for libblkid."*
Ref: [util-linux/util-linux#3191](https://github.com/util-linux/util-linux/issues/3191)

### AMDGPU framebuffer crash
Ubuntu launchpad bug 2003524: *"if not all of IP blocks are supported or not all of the firmware is present then the framebuffer is destroyed and the screen freezes"*. Workaround per Arch Wiki + FreeBSD forums: `nomodeset` + `video=efifb:off,vesafb:off` + `fbcon=map:1`.
Ref: [ubuntu/launchpad #2003524](https://bugs.launchpad.net/ubuntu/+source/linux/+bug/2003524), [Arch Linux forum #273784](https://bbs.archlinux.org/viewtopic.php?id=273784)

### Bash bounded subprocess pattern
GNU `coreutils` `timeout` with `--kill-after=N` sends SIGTERM first, then SIGKILL after grace period. Exit codes: 124 on timeout, 137 if SIGKILL needed. Our install script now uses the unified wrapper on every external call.
Ref: [linuxbash.sh timeout guide](https://www.linuxbash.sh/post/use-timeout-to-send-sigkill-only-after-a-grace-period-sigterm-first)

---

## Backlog (not shipped in this commit)

1. **Per-partition `blkid -p -o export` with 2s timeout each** — for operators who WANT to know what's on the disk pre-wipe. Needs UI: print "Found prior Windows install at sda1" as informational before the zap.
2. **Wifi support.** `wpa_supplicant`, `iw`, `wireless-tools` in `systemPackages`; `networking.wireless.enable = true`; a simple `wifi-config.yaml` on a second FAT32 partition of the USB that auto-configures SSID/PSK. Would let an install succeed on a box whose ethernet is dead.
3. **Declarative DHCP wait gate.** Before `check_network` runs, `systemctl start systemd-networkd-wait-online.timeout=30` so the interface has actually completed DHCP before we probe. Eliminates the `dhcp=false` false-positive on slow switches.
4. **`nixos-anywhere` alternative path.** For lab / dev bootstrapping, skip the ISO entirely: SSH into any Linux, overwrite with NixOS. Useful when the appliance hardware is flaky but a workstation is nearby.
5. **Automated boot test in CI.** Docker-in-Docker + qemu + a virtual HP t640 (with a BitLocker-like pre-populated disk image) boots the ISO weekly. Today's 23-minute hang would have failed that test in 30 seconds.

---

## Testing

Before shipping to the field:

1. `nix build .#appliance-iso` — builds a fresh ISO
2. Test in VBox with `--paravirtprovider kvm`, 8GB RAM, 120GB disk pre-populated with Windows-style partitions (or any NTFS volume) — simulates the HP t640 failure
3. Observe: install should complete in <90s total (no 23-min hang). Log should show `pre-wipe: neutralizing …` then `pre-wipe complete` in <30s.
4. Boot from internal disk post-install. Hostname should be assigned by MAC lookup (not `osiriscare-installer`).
5. Confirm appliance shows up in Central Command at `/api/sites/<site_id>/appliances`.

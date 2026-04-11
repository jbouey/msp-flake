#!/usr/bin/env bash
# Shared drive detection function for OsirisCare installer + installed system.
# Single source of truth — used by both appliance-image.nix and appliance-disk-image.nix.
#
# Usage: source this file, then call detect_internal_drive
# Returns: sets INTERNAL_DEV, DEV_SIZE, DEV_MODEL or returns 1 if not found

detect_internal_drive() {
  local boot_dev="${1:-}"  # Optional: boot device to skip

  INTERNAL_DEV=""
  DEV_SIZE=""
  DEV_MODEL=""

  for dev in /dev/nvme0n1 /dev/sda /dev/sdb /dev/vda /dev/mmcblk0 /dev/mmcblk1; do
    [ -b "$dev" ] || continue

    # Extract sysfs device name
    # SATA/NVMe: /dev/sda → sda, /dev/nvme0n1 → nvme0n1
    # eMMC: /dev/mmcblk0 → mmcblk0 (keep trailing 0)
    local dev_name
    dev_name=$(basename "$dev")
    case "$dev_name" in
      mmcblk*) ;;  # eMMC: use full name
      *) dev_name=$(echo "$dev_name" | sed 's/[0-9]*$//') ;;
    esac

    # Skip the boot device (USB we booted from)
    if [ -n "$boot_dev" ]; then
      echo "$boot_dev" | grep -q "$dev_name" && continue
    fi

    # Skip removable drives
    local removable
    removable=$(cat "/sys/block/$dev_name/removable" 2>/dev/null || echo "1")
    [ "$removable" = "1" ] && continue

    # Must be >16GB
    local size
    size=$(blockdev --getsize64 "$dev" 2>/dev/null || echo "0")
    [ "$size" -gt 16000000000 ] || continue

    INTERNAL_DEV="$dev"
    DEV_SIZE=$(numfmt --to=iec "$size" 2>/dev/null || echo "${size}B")
    DEV_MODEL=$(lsblk -dno MODEL "$dev" 2>/dev/null | xargs)
    return 0
  done

  return 1
}

# Partition path helper: handles NVMe/eMMC (p1/p2/p3) vs SATA (1/2/3)
part_path() {
  local dev="$1"
  local num="$2"
  case "$dev" in
    *nvme*|*mmcblk*) echo "${dev}p${num}" ;;
    *) echo "${dev}${num}" ;;
  esac
}

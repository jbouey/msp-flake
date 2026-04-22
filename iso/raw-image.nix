# iso/raw-image.nix
# Builds a compressed raw disk image with the full NixOS appliance closure.
#
# Layout:
#   Partition 1: ESP   (512MB, FAT32, label "ESP")     — systemd-boot + kernel + initrd
#   Partition 2: Root  (auto,  ext4,  label "nixos")    — complete NixOS system closure
#   Partition 3: Data  (2GB,   ext4,  label "MSP-DATA") — persistent config/evidence
#
# Output: $out/osiriscare-system.raw.zst  (zstd -19 compressed)
#         $out/decompressed-size           (raw image byte count for progress reporting)
#
# Usage: nix build .#appliance-raw-image
# Then:  zstd -d osiriscare-system.raw.zst | dd of=/dev/sdX bs=4M status=progress
#
# The installer ISO (Task 2) automates the dd + first-boot provisioning.

{ nixpkgs, lanzaboote, ... }:

let
  system = "x86_64-linux";
  pkgs = import nixpkgs { inherit system; };

  # The NixOS appliance configuration — same one used by nixos-rebuild on deployed appliances
  applianceSystem = nixpkgs.lib.nixosSystem {
    inherit system;
    modules = [
      lanzaboote.nixosModules.lanzaboote
      ./appliance-disk-image.nix
    ];
  };

  config = applianceSystem.config;

  # Step 1: Build the base 2-partition EFI image using nixpkgs make-disk-image.
  # This produces ESP (part 1) + root ext4 with full NixOS closure (part 2).
  # make-disk-image handles the hard parts: closureInfo, nixos-install, bootloader setup.
  baseImage = import "${nixpkgs}/nixos/lib/make-disk-image.nix" {
    inherit pkgs config;
    inherit (pkgs) lib;
    name = "osiriscare-base-image";
    format = "raw";
    diskSize = "auto";
    partitionTableType = "efi";
    bootSize = "512M";
    # 2026-04-22 FIX-6: was "1G", catastrophically undersized. On
    # 84:3A:5B:1D:0F:E5 the root partition filled inside two generations
    # of the closure, leaving nix's db.sqlite unable to commit writes
    # (`database or disk is full (in '/nix/var/nix/db/db.sqlite')`) —
    # every nixos-rebuild attempt on that box failed for 59 days until
    # the 0.4.7 diagnostic surfaced the root cause. 8 GB is the minimum
    # that survives ~8-10 generations of day-to-day system drift plus
    # the eval-cache + nix store db working set. Does NOT protect
    # appliances that never garbage-collect; pair with the weekly
    # nix-gc timer already enabled on installed systems.
    additionalSpace = "8G";   # headroom for future nixos-rebuild on the appliance
    installBootLoader = true;
    copyChannel = false;
    label = "nixos";
    memSize = 2048;
  };

  # Step 2: Extend the base image with the MSP-DATA partition, then compress.
  # The base image has: [GPT header] [ESP 512M] [root ext4 auto-sized] [GPT backup]
  # We append 2GB, add partition 3, format it, and compress the whole thing.
  #
  # Key trick: mkfs.ext4 -E offset= works on a raw file without loop devices,
  # which is exactly how make-disk-image.nix itself formats partitions in the
  # pre-VM phase. This avoids needing /dev/loop inside the Nix sandbox.

in pkgs.runCommand "osiriscare-raw-image" {
  nativeBuildInputs = with pkgs; [
    util-linux    # sfdisk, partx
    gptfdisk      # sgdisk
    e2fsprogs     # mkfs.ext4
    jq            # JSON partition table parsing
    zstd
    coreutils
  ];
} ''
  set -euo pipefail

  echo "=== OsirisCare Raw Image Builder ==="
  echo "Base image: ${baseImage}"

  # Copy the base image so we can modify it
  cp ${baseImage}/nixos.img ./osiriscare-system.raw
  chmod u+w ./osiriscare-system.raw

  baseSize=$(stat -c%s ./osiriscare-system.raw)
  echo "Base image size: $baseSize bytes ($(( baseSize / 1024 / 1024 )) MB)"

  # Grow the image by 2GB + 1MB alignment overhead for the MSP-DATA partition
  dataPartBytes=$(( 2 * 1024 * 1024 * 1024 ))
  growBy=$(( dataPartBytes + 1024 * 1024 ))
  truncate -s +$growBy ./osiriscare-system.raw

  newSize=$(stat -c%s ./osiriscare-system.raw)
  echo "Grown image size: $newSize bytes ($(( newSize / 1024 / 1024 )) MB)"

  # Relocate the backup GPT to the new end of disk.
  # sgdisk -e ("expand") moves the backup GPT header to fill available space.
  sgdisk -e ./osiriscare-system.raw

  # Find where the root partition (part 2) ends — that is where MSP-DATA starts.
  # sfdisk --json gives us partition offsets and sizes in sectors (512 bytes each).
  rootEnd=$(sfdisk --json ./osiriscare-system.raw | \
    ${pkgs.jq}/bin/jq '.partitiontable.partitions[1].start + .partitiontable.partitions[1].size')
  echo "Root partition ends at sector: $rootEnd"

  # Compute MSP-DATA size in sectors (512-byte sectors)
  dataSectors=$(( dataPartBytes / 512 ))

  # Add partition 3 as a Linux filesystem partition starting right after root.
  # Use sfdisk --append to add without disturbing existing partitions.
  echo "$rootEnd $dataSectors L" | sfdisk --append --no-reread ./osiriscare-system.raw

  # Set the GPT partition name (partlabel) to MSP-DATA.
  # The appliance config uses /dev/disk/by-partlabel/MSP-DATA to mount this.
  sgdisk -c 3:MSP-DATA ./osiriscare-system.raw

  # Verify the partition table
  echo "=== Final partition layout ==="
  sfdisk --list ./osiriscare-system.raw

  # Format partition 3 as ext4.
  # mkfs.ext4 -E offset= lets us format a region of a raw file without loop devices.
  # Get the exact offset in bytes.
  dataStart=$(sfdisk --json ./osiriscare-system.raw | \
    ${pkgs.jq}/bin/jq '.partitiontable.partitions[2].start')
  dataStartBytes=$(( dataStart * 512 ))

  # Get the actual partition size from sfdisk (may differ slightly from requested)
  actualDataSectors=$(sfdisk --json ./osiriscare-system.raw | \
    ${pkgs.jq}/bin/jq '.partitiontable.partitions[2].size')
  actualDataKB=$(( actualDataSectors * 512 / 1024 ))

  echo "MSP-DATA partition: offset=$dataStartBytes bytes, size=$actualDataKB KB"

  mkfs.ext4 -F -L MSP-DATA \
    -E offset=$dataStartBytes \
    ./osiriscare-system.raw \
    ''${actualDataKB}K

  # Record decompressed size before compressing (for installer progress bar)
  mkdir -p $out
  stat -c%s ./osiriscare-system.raw > $out/decompressed-size

  decompSize=$(cat $out/decompressed-size)
  echo "Decompressed image size: $decompSize bytes ($(( decompSize / 1024 / 1024 )) MB)"

  # Compress with zstd level 19 (high compression, ~60% ratio on disk images)
  # This is a build-time cost; decompression is fast.
  echo "Compressing with zstd -19 (this takes a while)..."
  zstd -19 -T0 -o $out/osiriscare-system.raw.zst ./osiriscare-system.raw

  compSize=$(stat -c%s $out/osiriscare-system.raw.zst)
  ratio=$(( compSize * 100 / decompSize ))
  echo "=== Done ==="
  echo "Compressed: $compSize bytes ($(( compSize / 1024 / 1024 )) MB, ''${ratio}% of original)"
  echo "Output: $out/osiriscare-system.raw.zst"
  echo "        $out/decompressed-size"
''

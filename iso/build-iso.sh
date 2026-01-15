#!/usr/bin/env bash
# Build ISO on NixOS VM
# Usage: ./build-iso.sh [version]

set -e

VERSION="${1:-latest}"
REPO_DIR="/root/msp-iso-build"
OUTPUT_DIR="/mnt/shared"  # VirtualBox shared folder

echo "=== MSP Appliance ISO Builder ==="
echo "Version: $VERSION"
echo ""

# Clone or update repo
if [ ! -d "$REPO_DIR" ]; then
    echo "Cloning repository..."
    git clone https://github.com/jbouey/msp-flake.git "$REPO_DIR"
else
    echo "Updating repository..."
    cd "$REPO_DIR"
    git pull
fi

cd "$REPO_DIR"

# Build ISO
echo "Building ISO..."
nix build .#nixosConfigurations.osiriscare-appliance.config.system.build.isoImage -o "result-v$VERSION"

# Show result
ISO_PATH="$(readlink -f result-v$VERSION/iso/osiriscare-appliance.iso)"
ISO_SIZE="$(du -h "$ISO_PATH" | cut -f1)"

echo ""
echo "=== Build Complete ==="
echo "ISO: $ISO_PATH"
echo "Size: $ISO_SIZE"

# Copy to shared folder if available
if [ -d "$OUTPUT_DIR" ] && [ -w "$OUTPUT_DIR" ]; then
    echo "Copying to shared folder..."
    cp "$ISO_PATH" "$OUTPUT_DIR/osiriscare-appliance-v$VERSION.iso"
    echo "Copied to: $OUTPUT_DIR/osiriscare-appliance-v$VERSION.iso"
fi

echo ""
echo "Done!"

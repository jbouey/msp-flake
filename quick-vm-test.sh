#!/usr/bin/env bash
#
# Quick VM Test - Fastest path to running VM
#
set -euo pipefail

echo "════════════════════════════════════════════════════════════"
echo "  MSP Compliance VM - Quick Test"
echo "════════════════════════════════════════════════════════════"
echo ""

# Ensure we're in the right directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || {
    echo "❌ Failed to change to script directory"
    exit 1
}

# Check if deployment script exists
if [ ! -f "scripts/deploy-vbox-vms.sh" ]; then
    echo "❌ Deployment script not found: scripts/deploy-vbox-vms.sh"
    echo "   Are you in the MSP Flakes project directory?"
    exit 1
fi

# Check if VirtualBox is installed
if ! command -v VBoxManage &> /dev/null; then
    echo "❌ VirtualBox not installed"
    echo ""
    echo "Install with: brew install --cask virtualbox"
    echo "Or download from: https://www.virtualbox.org/wiki/Downloads"
    exit 1
fi

echo "✓ VirtualBox found"
echo "✓ Deployment script found"
echo ""

# Ask user what they want to do
echo "What would you like to do?"
echo ""
echo "  1. Quick test (build + run test VM locally)"
echo "  2. Build all VMs for transfer to main CPU"
echo "  3. Package source only (build on main CPU)"
echo "  4. Show manual instructions"
echo ""
read -p "Enter choice (1-4): " choice

case $choice in
    1)
        echo ""
        echo "Building and starting test VM..."
        echo "This will take ~15 minutes on first run."
        echo ""
        ./scripts/deploy-vbox-vms.sh --full test-client-wired
        echo ""
        echo "════════════════════════════════════════════════════════════"
        echo "  ✓ VM is now running!"
        echo "════════════════════════════════════════════════════════════"
        echo ""
        echo "Connect via SSH:"
        echo "  ssh -p 4444 root@localhost"
        echo ""
        echo "Check agent status:"
        echo "  ssh -p 4444 root@localhost systemctl status compliance-agent"
        echo ""
        echo "Stop VM:"
        echo "  VBoxManage controlvm test-client-wired poweroff"
        echo ""
        ;;

    2)
        echo ""
        echo "Building all VMs for transfer..."
        echo "This will take ~30-45 minutes."
        echo ""
        ./scripts/deploy-vbox-vms.sh --build-all
        echo ""
        echo "════════════════════════════════════════════════════════════"
        echo "  ✓ VMs built successfully!"
        echo "════════════════════════════════════════════════════════════"
        echo ""
        echo "Transfer to main CPU:"
        echo ""
        echo "  scp deploy/msp-flakes.tar.gz user@main-cpu:/tmp/"
        echo "  scp deploy/vms/*.ova user@main-cpu:/tmp/"
        echo "  scp deploy/setup-on-main-cpu.sh user@main-cpu:/tmp/"
        echo ""
        echo "Then on main CPU:"
        echo "  cd /tmp && bash setup-on-main-cpu.sh"
        echo ""
        ;;

    3)
        echo ""
        echo "Packaging source for transfer..."
        ./scripts/deploy-vbox-vms.sh --package
        echo ""
        echo "════════════════════════════════════════════════════════════"
        echo "  ✓ Source packaged!"
        echo "════════════════════════════════════════════════════════════"
        echo ""
        echo "Transfer to main CPU:"
        echo ""
        echo "  scp deploy/msp-flakes.tar.gz user@main-cpu:/tmp/"
        echo "  scp deploy/setup-on-main-cpu.sh user@main-cpu:/tmp/"
        echo ""
        echo "Then on main CPU:"
        echo "  cd /tmp && bash setup-on-main-cpu.sh"
        echo ""
        echo "Note: Main CPU will build VMs from source (~15 min per VM)"
        echo ""
        ;;

    4)
        echo ""
        cat << 'EOF'
════════════════════════════════════════════════════════════
  Manual VM Deployment Instructions
════════════════════════════════════════════════════════════

QUICK LOCAL TEST:
  ./scripts/deploy-vbox-vms.sh --full test-client-wired

BUILD ALL VMS:
  ./scripts/deploy-vbox-vms.sh --build-all

PACKAGE FOR TRANSFER:
  ./scripts/deploy-vbox-vms.sh --package

BUILD SPECIFIC VM:
  ./scripts/deploy-vbox-vms.sh --build test-client-wired
  ./scripts/deploy-vbox-vms.sh --build direct-config
  ./scripts/deploy-vbox-vms.sh --build reseller-config

IMPORT EXISTING VM:
  ./scripts/deploy-vbox-vms.sh --import test-client-wired

START VM:
  ./scripts/deploy-vbox-vms.sh --start test-client-wired

STOP VM:
  VBoxManage controlvm test-client-wired poweroff

DELETE VM:
  VBoxManage unregistervm test-client-wired --delete

FULL HELP:
  ./scripts/deploy-vbox-vms.sh --help

DETAILED GUIDE:
  cat VIRTUALBOX-DEPLOYMENT.md

════════════════════════════════════════════════════════════
EOF
        echo ""
        ;;

    *)
        echo "Invalid choice"
        exit 1
        ;;
esac

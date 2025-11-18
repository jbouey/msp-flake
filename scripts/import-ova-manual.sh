#!/usr/bin/env bash
#
# Import VirtualBox OVA (manually downloaded from GitHub Actions)
#
# Usage:
#   1. Download OVA artifact from GitHub Actions:
#      https://github.com/jbouey/msp-flake/actions/workflows/build-vm.yml
#   2. Extract the ZIP file
#   3. Run this script:
#      ./scripts/import-ova-manual.sh ~/Downloads/nixos-ova-*.ova test-client-wired
#
# Arguments:
#   $1 - Path to OVA file
#   $2 - VM name (optional, defaults to base name of OVA)
#

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# Check if running on macOS
if [[ "$(uname -s)" != "Darwin" ]]; then
    log_error "This script is for macOS only"
    exit 1
fi

# Parse arguments
OVA_PATH="${1:-}"
VM_NAME="${2:-}"

if [[ -z "$OVA_PATH" ]]; then
    log_error "Usage: $0 <path-to-ova> [vm-name]"
    log_info ""
    log_info "Example:"
    log_info "  $0 ~/Downloads/nixos-ova-*.ova test-client-wired"
    log_info ""
    log_info "Steps to get the OVA:"
    log_info "  1. Go to: https://github.com/jbouey/msp-flake/actions/workflows/build-vm.yml"
    log_info "  2. Click on a completed workflow run (green checkmark)"
    log_info "  3. Scroll to 'Artifacts' section"
    log_info "  4. Download 'test-client-wired-ova'"
    log_info "  5. Extract the ZIP file"
    log_info "  6. Run this script with the .ova file path"
    exit 1
fi

# Expand glob if needed
OVA_PATH=$(echo $OVA_PATH)

# Check if OVA exists
if [[ ! -f "$OVA_PATH" ]]; then
    log_error "OVA file not found: $OVA_PATH"
    exit 1
fi

# Default VM name from OVA filename
if [[ -z "$VM_NAME" ]]; then
    VM_NAME=$(basename "$OVA_PATH" .ova)
    log_info "Using VM name: $VM_NAME"
fi

# Check dependencies
log_info "Checking dependencies..."

if ! command -v VBoxManage &> /dev/null; then
    log_error "VirtualBox not found"
    log_info "Install from: https://www.virtualbox.org/wiki/Downloads"
    exit 1
fi

log_success "VirtualBox found: $(VBoxManage --version)"

# Show OVA details
log_info "OVA file: $OVA_PATH"
log_info "  Size: $(du -h "$OVA_PATH" | cut -f1)"

# Check if VM already exists
if VBoxManage list vms | grep -q "\"$VM_NAME\""; then
    log_warn "VM '$VM_NAME' already exists"
    read -p "Delete and re-import? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        log_info "Stopping VM if running..."
        VBoxManage controlvm "$VM_NAME" poweroff 2>/dev/null || true
        sleep 2

        log_info "Deleting existing VM..."
        VBoxManage unregistervm "$VM_NAME" --delete
    else
        log_info "Keeping existing VM, skipping import"
        exit 0
    fi
fi

# Import OVA
log_info "Importing to VirtualBox..."

if ! VBoxManage import "$OVA_PATH" \
    --vsys 0 \
    --vmname "$VM_NAME"; then
    log_error "Failed to import OVA"
    exit 1
fi

log_success "VM imported successfully"

# Configure port forwarding
log_info "Configuring port forwarding..."

VBoxManage modifyvm "$VM_NAME" \
    --natpf1 "ssh,tcp,127.0.0.1,4444,,22" 2>/dev/null || true

VBoxManage modifyvm "$VM_NAME" \
    --natpf1 "http,tcp,127.0.0.1,8080,,80" 2>/dev/null || true

log_success "Port forwarding configured (SSH: 4444, HTTP: 8080)"

# Start VM
log_info "Starting VM in headless mode..."

if ! VBoxManage startvm "$VM_NAME" --type headless; then
    log_error "Failed to start VM"
    log_info "Try manually: VBoxManage startvm $VM_NAME --type gui"
    exit 1
fi

log_success "VM started successfully"

# Wait for SSH
log_info "Waiting for SSH to be available..."

MAX_WAIT=60
for ((i=1; i<=MAX_WAIT; i++)); do
    if ssh -p 4444 \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        -o ConnectTimeout=2 \
        root@localhost "echo 'SSH ready'" &>/dev/null; then
        log_success "SSH is available"
        break
    fi

    if [[ $i -eq $MAX_WAIT ]]; then
        log_warn "SSH not available after ${MAX_WAIT}s"
        log_info "The VM may still be booting - wait a minute and try:"
        log_info "  ssh -p 4444 root@localhost"
    else
        echo -n "."
        sleep 1
    fi
done

# Summary
cat <<EOF

${GREEN}╔════════════════════════════════════════════════════════════╗
║                    VM IMPORT COMPLETE                      ║
╚════════════════════════════════════════════════════════════╝${NC}

${BLUE}VM Information:${NC}
  Name:       $VM_NAME
  Status:     Running (headless)
  OVA:        $(basename "$OVA_PATH")

${BLUE}Connection Details:${NC}
  SSH:        ssh -p 4444 root@localhost
  Password:   root (change immediately!)
  HTTP:       http://localhost:8080

${BLUE}VM Management:${NC}
  Stop:       VBoxManage controlvm $VM_NAME poweroff
  Restart:    VBoxManage controlvm $VM_NAME reset
  Console:    VBoxManage startvm $VM_NAME --type gui
  Delete:     VBoxManage unregistervm $VM_NAME --delete

${BLUE}Next Steps:${NC}
  1. SSH into the VM: ssh -p 4444 root@localhost
  2. Change root password: passwd
  3. Check compliance agent: systemctl status compliance-agent
  4. View logs: journalctl -u compliance-agent -f

EOF

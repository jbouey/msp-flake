#!/usr/bin/env bash
#
# Import VirtualBox OVA built by GitHub Actions
#
# Usage:
#   ./scripts/import-ova-from-ci.sh <config-name> [run-id]
#
# Examples:
#   ./scripts/import-ova-from-ci.sh test-client-wired
#   ./scripts/import-ova-from-ci.sh test-client-wired 12345678
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
    log_info "On Linux, use: ./scripts/deploy-vbox-vms.sh"
    exit 1
fi

# Parse arguments
CONFIG_NAME="${1:-}"
RUN_ID="${2:-}"

if [[ -z "$CONFIG_NAME" ]]; then
    log_error "Usage: $0 <config-name> [run-id]"
    log_info "Example: $0 test-client-wired"
    exit 1
fi

# Check dependencies
log_info "Checking dependencies..."

if ! command -v VBoxManage &> /dev/null; then
    log_error "VirtualBox not found"
    log_info "Install from: https://www.virtualbox.org/wiki/Downloads"
    exit 1
fi

if ! command -v gh &> /dev/null; then
    log_error "GitHub CLI not found"
    log_info "Install with: brew install gh"
    log_info "Then authenticate: gh auth login"
    exit 1
fi

log_success "All dependencies found"

# Create download directory
DOWNLOAD_DIR="${SCRIPT_DIR}/../deploy/vms"
mkdir -p "$DOWNLOAD_DIR"

# Get repository info
REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
log_info "Repository: $REPO"

# Find latest successful workflow run if not specified
if [[ -z "$RUN_ID" ]]; then
    log_info "Finding latest successful workflow run..."

    RUN_ID=$(gh run list \
        --workflow=build-vm.yml \
        --status=success \
        --limit=1 \
        --json databaseId \
        --jq '.[0].databaseId')

    if [[ -z "$RUN_ID" ]]; then
        log_error "No successful workflow runs found"
        log_info "Trigger a build at: https://github.com/$REPO/actions/workflows/build-vm.yml"
        log_info "Or run: gh workflow run build-vm.yml -f config_name=$CONFIG_NAME"
        exit 1
    fi

    log_info "Found run ID: $RUN_ID"
fi

# Download artifact
log_info "Downloading OVA from GitHub Actions (run $RUN_ID)..."

ARTIFACT_NAME="${CONFIG_NAME}-ova"

if ! gh run download "$RUN_ID" \
    --name "$ARTIFACT_NAME" \
    --dir "$DOWNLOAD_DIR"; then
    log_error "Failed to download artifact: $ARTIFACT_NAME"
    log_info "Available artifacts for run $RUN_ID:"
    gh run view "$RUN_ID" --json artifacts --jq '.artifacts[] | "  - \(.name)"'
    exit 1
fi

# Find the OVA file
OVA_FILE=$(find "$DOWNLOAD_DIR" -name "*.ova" -type f | head -n1)

if [[ -z "$OVA_FILE" ]]; then
    log_error "No OVA file found in download"
    log_info "Downloaded files:"
    ls -lh "$DOWNLOAD_DIR"
    exit 1
fi

log_success "Downloaded OVA: $(basename "$OVA_FILE")"
log_info "  Size: $(du -h "$OVA_FILE" | cut -f1)"

# Import to VirtualBox
log_info "Importing to VirtualBox..."

VM_NAME="$CONFIG_NAME"

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
if ! VBoxManage import "$OVA_FILE" \
    --vsys 0 \
    --vmname "$VM_NAME"; then
    log_error "Failed to import OVA"
    exit 1
fi

log_success "VM imported successfully"

# Configure port forwarding (SSH on 4444)
log_info "Configuring port forwarding..."

VBoxManage modifyvm "$VM_NAME" \
    --natpf1 "ssh,tcp,127.0.0.1,4444,,22" || true

VBoxManage modifyvm "$VM_NAME" \
    --natpf1 "http,tcp,127.0.0.1,8080,,80" || true

log_success "Port forwarding configured"

# Start VM
log_info "Starting VM in headless mode..."

if ! VBoxManage startvm "$VM_NAME" --type headless; then
    log_error "Failed to start VM"
    log_info "Try manually: VBoxManage startvm $VM_NAME --type gui"
    exit 1
fi

log_success "VM started successfully"

# Wait for SSH to be available
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
        log_info "The VM may still be booting"
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
  OVA:        $(basename "$OVA_FILE")

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

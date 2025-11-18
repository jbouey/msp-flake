#!/usr/bin/env bash
#
# VirtualBox VM Deployment Script
# Packages flakes and creates VirtualBox VMs for testing
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOY_DIR="$PROJECT_ROOT/deploy"
VM_DIR="$DEPLOY_DIR/vms"
LOG_FILE="$DEPLOY_DIR/deploy.log"

# SSH port configuration
SSH_PORT=4444

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Cleanup tracking
TEMP_FILES=()
CLEANUP_NEEDED=false

# Error handling
cleanup_on_exit() {
    local exit_code=$?

    if [ "$CLEANUP_NEEDED" = true ]; then
        log_warn "Cleaning up temporary files..."

        for temp_file in "${TEMP_FILES[@]}"; do
            if [ -f "$temp_file" ]; then
                rm -f "$temp_file" 2>/dev/null || true
                log_info "  Removed: $temp_file"
            fi
        done
    fi

    if [ $exit_code -ne 0 ]; then
        log_error "Script failed with exit code: $exit_code"
        log_error "Check log file: $LOG_FILE"
    fi

    return $exit_code
}

trap cleanup_on_exit EXIT
trap 'log_error "Script interrupted"; exit 130' INT TERM

log_info() {
    local msg="${GREEN}[INFO]${NC} $*"
    echo -e "$msg"
    if [ -d "$DEPLOY_DIR" ]; then
        echo -e "$msg" >> "$LOG_FILE"
    fi
}

log_warn() {
    local msg="${YELLOW}[WARN]${NC} $*"
    echo -e "$msg"
    if [ -d "$DEPLOY_DIR" ]; then
        echo -e "$msg" >> "$LOG_FILE"
    fi
}

log_error() {
    local msg="${RED}[ERROR]${NC} $*"
    echo -e "$msg" >&2
    if [ -d "$DEPLOY_DIR" ]; then
        echo -e "$msg" >> "$LOG_FILE"
    fi
}

log_debug() {
    if [ -d "$DEPLOY_DIR" ]; then
        echo -e "${BLUE}[DEBUG]${NC} $*" >> "$LOG_FILE"
    fi
}

fatal_error() {
    log_error "$1"
    exit 1
}

validate_environment() {
    # Create deploy directory FIRST (before any logging)
    mkdir -p "$DEPLOY_DIR" 2>/dev/null || {
        echo -e "${RED}[ERROR]${NC} Failed to create deploy directory: $DEPLOY_DIR" >&2
        exit 1
    }

    # Initialize log file
    echo "=== MSP VirtualBox Deployment Log ===" > "$LOG_FILE"
    echo "Started: $(date)" >> "$LOG_FILE"
    echo "Working directory: $PROJECT_ROOT" >> "$LOG_FILE"
    echo "" >> "$LOG_FILE"

    log_debug "Validating environment..."

    # Check we're in the right directory
    if [ ! -f "$PROJECT_ROOT/flake-compliance.nix" ]; then
        fatal_error "Not in MSP Flakes project directory. Expected: $PROJECT_ROOT/flake-compliance.nix"
    fi

    # Check disk space (need at least 10GB)
    local available_space=$(df -k "$PROJECT_ROOT" | awk 'NR==2 {print $4}')
    local required_space=$((10 * 1024 * 1024)) # 10GB in KB

    if [ "$available_space" -lt "$required_space" ]; then
        log_warn "Low disk space: $(($available_space / 1024 / 1024))GB available"
        log_warn "Recommended: At least 10GB free"
    fi

    log_debug "Environment validation complete"
}

check_dependencies() {
    log_info "Checking dependencies..."

    local missing_deps=()
    local dep_versions=()

    # Check Nix
    if ! command -v nix &> /dev/null; then
        missing_deps+=("nix")
    else
        local nix_version=$(nix --version 2>/dev/null | head -n1 || echo "unknown")
        dep_versions+=("nix: $nix_version")
        log_debug "Found: $nix_version"
    fi

    # Check VirtualBox
    if ! command -v VBoxManage &> /dev/null; then
        missing_deps+=("VirtualBox")
    else
        local vbox_version=$(VBoxManage --version 2>/dev/null || echo "unknown")
        dep_versions+=("VirtualBox: $vbox_version")
        log_debug "Found VirtualBox: $vbox_version"
    fi

    # Check tar
    if ! command -v tar &> /dev/null; then
        missing_deps+=("tar")
    fi

    if [ ${#missing_deps[@]} -ne 0 ]; then
        log_error "Missing dependencies: ${missing_deps[*]}"
        echo ""
        log_error "Installation instructions:"
        for dep in "${missing_deps[@]}"; do
            case $dep in
                nix)
                    log_error "  Nix: sh <(curl -L https://nixos.org/nix/install)"
                    ;;
                VirtualBox)
                    log_error "  VirtualBox: brew install --cask virtualbox"
                    log_error "  Or download: https://www.virtualbox.org/wiki/Downloads"
                    ;;
                tar)
                    log_error "  tar: Should be pre-installed on macOS"
                    ;;
            esac
        done
        exit 1
    fi

    log_info "✓ All dependencies found"
    for version in "${dep_versions[@]}"; do
        log_debug "  $version"
    done
}

check_port_available() {
    local port=$1

    log_debug "Checking if port $port is available..."

    if lsof -Pi ":$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
        log_error "Port $port is already in use"
        log_error "Process using port:"
        lsof -Pi ":$port" -sTCP:LISTEN | head -n2 | tee -a "$LOG_FILE"
        return 1
    fi

    log_debug "Port $port is available"
    return 0
}

package_flake() {
    log_info "Packaging flake for transfer..."

    # Validate required files exist
    local required_files=(
        "flake-compliance.nix"
        "modules/compliance-agent.nix"
        "packages/compliance-agent"
        "examples"
    )

    for file in "${required_files[@]}"; do
        if [ ! -e "$PROJECT_ROOT/$file" ]; then
            fatal_error "Required file/directory missing: $file"
        fi
    done

    # Create deploy directory
    mkdir -p "$DEPLOY_DIR" || fatal_error "Failed to create deploy directory: $DEPLOY_DIR"

    local tarball="$DEPLOY_DIR/msp-flakes.tar.gz"

    # Remove old tarball if exists
    if [ -f "$tarball" ]; then
        log_warn "Removing existing tarball: $tarball"
        rm -f "$tarball" || fatal_error "Failed to remove old tarball"
    fi

    # Create tarball of essential files
    log_debug "Creating tarball..."
    cd "$PROJECT_ROOT" || fatal_error "Failed to cd to project root"

    # Build list of directories to include (only if they exist)
    local tar_includes=()

    # Always include these files
    tar_includes+=("flake-compliance.nix")
    [ -f "flake.lock" ] && tar_includes+=("flake.lock")

    # Include directories only if they exist
    local optional_dirs=("modules" "packages" "examples" "nixosTests" "checks")
    for dir in "${optional_dirs[@]}"; do
        if [ -d "$dir" ]; then
            tar_includes+=("$dir/")
            log_debug "Including directory: $dir/"
        else
            log_warn "Directory not found, skipping: $dir/"
        fi
    done

    log_debug "Packaging ${#tar_includes[@]} items into tarball"

    # Create tarball with error capture (exclude patterns must come before files)
    local tar_error_log="$DEPLOY_DIR/tar-error.log"
    if ! tar czf "$tarball" \
        --exclude '*.pyc' \
        --exclude '__pycache__' \
        --exclude '.git' \
        --exclude 'result*' \
        "${tar_includes[@]}" 2>"$tar_error_log"; then

        log_error "Failed to create tarball"
        if [ -f "$tar_error_log" ] && [ -s "$tar_error_log" ]; then
            log_error "Tar error details:"
            cat "$tar_error_log" | while read line; do
                log_error "  $line"
            done
            cat "$tar_error_log" >> "$LOG_FILE"
        fi
        rm -f "$tar_error_log"
        fatal_error "Tarball creation failed - see errors above"
    fi
    rm -f "$tar_error_log"

    # Verify tarball was created and is not empty
    if [ ! -f "$tarball" ]; then
        fatal_error "Tarball was not created: $tarball"
    fi

    local tarball_size=$(stat -f%z "$tarball" 2>/dev/null || echo "0")
    if [ "$tarball_size" -lt 1024 ]; then
        fatal_error "Tarball is suspiciously small: $tarball_size bytes"
    fi

    log_info "✓ Flake packaged to: $tarball"
    log_info "  Size: $(du -h "$tarball" | cut -f1)"
    log_debug "  Actual size: $tarball_size bytes"

    # List tarball contents for verification
    log_debug "Tarball contents:"
    tar tzf "$tarball" | head -20 >> "$LOG_FILE" || log_warn "Could not list tarball contents"
}

build_vm_image() {
    local config_name=$1
    local config_path="$PROJECT_ROOT/examples/$config_name.nix"

    log_info "Building VirtualBox VM for: $config_name"

    # Validate configuration exists
    if [ ! -f "$config_path" ]; then
        log_error "Configuration not found: $config_path"
        log_error "Available configurations:"
        ls -1 "$PROJECT_ROOT/examples/"*.nix 2>/dev/null | sed 's/.*\//  - /' | sed 's/\.nix$//' || true
        return 1
    fi

    # Create VM directory
    mkdir -p "$VM_DIR" || fatal_error "Failed to create VM directory: $VM_DIR"

    # Create a temporary configuration that uses virtualbox
    local temp_config=$(mktemp)
    TEMP_FILES+=("$temp_config")
    CLEANUP_NEEDED=true

    log_debug "Creating temporary config: $temp_config"

    cat > "$temp_config" <<EOF
{ config, pkgs, modulesPath, ... }:
{
  imports = [
    $config_path
    "\${modulesPath}/virtualisation/virtualbox-image.nix"
  ];

  # VirtualBox-specific settings
  virtualbox.baseImageSize = 20480; # 20 GB

  # Basic system settings
  system.stateVersion = "24.05";

  # Enable guest additions
  virtualisation.virtualbox.guest.enable = true;

  # Network configuration
  networking.useDHCP = true;
  networking.firewall.enable = true;

  # SSH configuration with custom port
  services.openssh = {
    enable = true;
    settings = {
      PermitRootLogin = "yes";
      PasswordAuthentication = true;
    };
  };
}
EOF

    if [ ! -f "$temp_config" ] || [ ! -s "$temp_config" ]; then
        fatal_error "Failed to create temporary configuration"
    fi

    log_debug "Temporary config created successfully"

    # Build the VirtualBox image
    log_info "Building VirtualBox OVA (this may take 10-15 minutes)..."
    log_info "Progress will be logged to: $LOG_FILE"

    local result_link="$DEPLOY_DIR/result-$config_name"

    # Remove old result if exists
    if [ -L "$result_link" ]; then
        log_debug "Removing old result symlink: $result_link"
        rm -f "$result_link"
    fi

    # Build with error handling
    if ! nix-build '<nixpkgs/nixos>' \
        -A config.system.build.virtualBoxOVA \
        -I nixos-config="$temp_config" \
        --out-link "$result_link" \
        --show-trace 2>&1 | tee -a "$LOG_FILE"; then
        log_error "Failed to build VirtualBox image for $config_name"
        log_error "Check build log: $LOG_FILE"
        return 1
    fi

    # Verify result exists
    if [ ! -L "$result_link" ]; then
        fatal_error "Build completed but result link not created: $result_link"
    fi

    # Find and copy OVA
    local ova_source="$result_link/nixos.ova"
    local ova_dest="$VM_DIR/$config_name.ova"

    if [ ! -f "$ova_source" ]; then
        # Try alternative location
        ova_source=$(find "$result_link" -name "*.ova" -type f | head -n1)
        if [ -z "$ova_source" ]; then
            fatal_error "OVA file not found in build result"
        fi
        log_warn "OVA found at non-standard location: $ova_source"
    fi

    log_debug "Copying OVA from $ova_source to $ova_dest"

    if ! cp "$ova_source" "$ova_dest"; then
        fatal_error "Failed to copy OVA file"
    fi

    # Verify OVA
    if [ ! -f "$ova_dest" ]; then
        fatal_error "OVA not copied successfully"
    fi

    local ova_size=$(stat -f%z "$ova_dest" 2>/dev/null || echo "0")
    if [ "$ova_size" -lt 1048576 ]; then  # Less than 1MB is suspicious
        fatal_error "OVA file is suspiciously small: $ova_size bytes"
    fi

    log_info "✓ VM image created: $ova_dest"
    log_info "  Size: $(du -h "$ova_dest" | cut -f1)"
    log_debug "  Actual size: $ova_size bytes"

    return 0
}

import_vm_to_virtualbox() {
    local vm_name=$1
    local ova_path="$VM_DIR/$vm_name.ova"

    log_info "Importing $vm_name to VirtualBox..."

    # Validate OVA exists
    if [ ! -f "$ova_path" ]; then
        log_error "OVA not found: $ova_path"
        log_error "Available OVAs:"
        ls -1 "$VM_DIR"/*.ova 2>/dev/null | sed 's/.*\//  - /' || log_error "  (none)"
        return 1
    fi

    # Check if VM already exists
    if VBoxManage list vms 2>/dev/null | grep -q "\"$vm_name\""; then
        log_warn "VM '$vm_name' already exists. Removing..."

        # Check if VM is running
        if VBoxManage list runningvms 2>/dev/null | grep -q "\"$vm_name\""; then
            log_warn "Stopping running VM..."
            if ! VBoxManage controlvm "$vm_name" poweroff 2>>"$LOG_FILE"; then
                log_warn "Failed to stop VM gracefully, continuing anyway..."
                sleep 2
            fi
            sleep 2
        fi

        # Unregister and delete
        if ! VBoxManage unregistervm "$vm_name" --delete 2>>"$LOG_FILE"; then
            log_error "Failed to remove existing VM"
            return 1
        fi

        log_info "Old VM removed successfully"
        sleep 1
    fi

    # Check if SSH port is available
    if ! check_port_available "$SSH_PORT"; then
        log_error "Cannot import VM: SSH port $SSH_PORT is in use"
        return 1
    fi

    # Import OVA
    log_debug "Importing OVA: $ova_path"
    if ! VBoxManage import "$ova_path" \
        --vsys 0 \
        --vmname "$vm_name" 2>&1 | tee -a "$LOG_FILE"; then
        fatal_error "Failed to import OVA"
    fi

    # Verify import
    if ! VBoxManage list vms 2>/dev/null | grep -q "\"$vm_name\""; then
        fatal_error "VM was not imported successfully"
    fi

    # Configure VM settings
    log_info "Configuring VM settings..."

    if ! VBoxManage modifyvm "$vm_name" \
        --memory 2048 \
        --cpus 2 \
        --vram 16 \
        --graphicscontroller vmsvga \
        --nic1 nat \
        --natpf1 "ssh,tcp,,$SSH_PORT,,22" \
        --natpf1 "http,tcp,,8080,,80" \
        --natpf1 "https,tcp,,8443,,443" 2>&1 | tee -a "$LOG_FILE"; then
        log_error "Failed to configure VM settings"
        return 1
    fi

    log_info "✓ VM '$vm_name' imported and configured"
    log_info "  SSH: ssh -p $SSH_PORT root@localhost"
    log_info "  HTTP: http://localhost:8080"
    log_info "  HTTPS: https://localhost:8443"

    return 0
}

start_vm() {
    local vm_name=$1

    log_info "Starting VM: $vm_name"

    # Verify VM exists
    if ! VBoxManage list vms 2>/dev/null | grep -q "\"$vm_name\""; then
        log_error "VM '$vm_name' not found"
        log_error "Available VMs:"
        VBoxManage list vms 2>/dev/null | sed 's/^/  /' || log_error "  (none)"
        return 1
    fi

    # Check if already running
    if VBoxManage list runningvms 2>/dev/null | grep -q "\"$vm_name\""; then
        log_warn "VM '$vm_name' is already running"
        return 0
    fi

    # Check SSH port availability
    if ! check_port_available "$SSH_PORT"; then
        log_error "Cannot start VM: SSH port $SSH_PORT is in use"
        log_error "Stop the conflicting process or choose a different port"
        return 1
    fi

    # Start VM in headless mode
    log_debug "Starting VM in headless mode..."
    if ! VBoxManage startvm "$vm_name" --type headless 2>&1 | tee -a "$LOG_FILE"; then
        log_error "Failed to start VM"
        return 1
    fi

    # Wait for VM to start
    log_info "Waiting for VM to boot (up to 60 seconds)..."
    local wait_time=0
    local max_wait=60

    while [ $wait_time -lt $max_wait ]; do
        if VBoxManage list runningvms 2>/dev/null | grep -q "\"$vm_name\""; then
            log_info "✓ VM '$vm_name' started successfully (headless mode)"
            log_info ""
            log_info "Connection details:"
            log_info "  SSH: ssh -p $SSH_PORT root@localhost"
            log_info "  HTTP: http://localhost:8080"
            log_info "  HTTPS: https://localhost:8443"
            log_info ""
            log_info "Useful commands:"
            log_info "  View status: VBoxManage showvminfo $vm_name | head -20"
            log_info "  Stop VM: VBoxManage controlvm $vm_name poweroff"
            log_info "  Screenshot: VBoxManage controlvm $vm_name screenshotpng /tmp/$vm_name.png"
            return 0
        fi
        sleep 2
        wait_time=$((wait_time + 2))
        echo -n "." | tee -a "$LOG_FILE"
    done

    echo "" | tee -a "$LOG_FILE"
    log_error "VM did not start within $max_wait seconds"
    log_error "Check VM state: VBoxManage showvminfo $vm_name"
    return 1
}

generate_transfer_script() {
    log_info "Generating transfer script for main CPU..."

    local setup_script="$DEPLOY_DIR/setup-on-main-cpu.sh"

    cat > "$setup_script" <<EOFTRANSFER
#!/usr/bin/env bash
#
# Setup script for main CPU
# Run this after transferring msp-flakes.tar.gz
#
set -euo pipefail

# SSH port configuration
SSH_PORT=$SSH_PORT

echo "════════════════════════════════════════════════════════════"
echo "  MSP Compliance VMs - Setup on Main CPU"
echo "════════════════════════════════════════════════════════════"
echo ""

# Check if tarball exists
if [ ! -f "msp-flakes.tar.gz" ]; then
    echo "ERROR: msp-flakes.tar.gz not found in current directory"
    echo "Expected location: \$(pwd)/msp-flakes.tar.gz"
    exit 1
fi

# Extract flake
echo "Extracting flake..."
mkdir -p msp-flakes
if ! tar xzf msp-flakes.tar.gz -C msp-flakes; then
    echo "ERROR: Failed to extract tarball"
    exit 1
fi

cd msp-flakes || exit 1

# Check VirtualBox
echo "Checking dependencies..."
if ! command -v VBoxManage &> /dev/null; then
    echo "ERROR: VirtualBox not installed"
    echo "Install from: https://www.virtualbox.org/wiki/Downloads"
    exit 1
fi

echo "✓ VirtualBox found: \$(VBoxManage --version)"

# Check Nix
if ! command -v nix &> /dev/null; then
    echo "WARNING: Nix not installed"
    echo "Install with: sh <(curl -L https://nixos.org/nix/install)"
    echo ""
    read -p "Continue without Nix? (VMs must be pre-built) [y/N]: " confirm
    if [[ ! \$confirm =~ ^[Yy]\$ ]]; then
        exit 1
    fi
fi

# Check for pre-built VMs
if [ -d "../vms" ] && [ "\$(ls -A ../vms/*.ova 2>/dev/null)" ]; then
    echo ""
    echo "Pre-built VMs found:"
    ls -1 ../vms/*.ova | sed 's/.*\//  - /'
    echo ""
    read -p "Import pre-built VM? [y/N]: " use_prebuilt

    if [[ \$use_prebuilt =~ ^[Yy]\$ ]]; then
        echo ""
        echo "Available VMs:"
        select ova in ../vms/*.ova; do
            if [ -n "\$ova" ]; then
                vm_name=\$(basename "\$ova" .ova)
                echo "Importing \$vm_name..."

                # Check if VM exists
                if VBoxManage list vms | grep -q "\"\$vm_name\""; then
                    echo "VM exists, removing..."
                    VBoxManage controlvm "\$vm_name" poweroff 2>/dev/null || true
                    sleep 2
                    VBoxManage unregistervm "\$vm_name" --delete 2>/dev/null || true
                fi

                # Import
                if VBoxManage import "\$ova" --vsys 0 --vmname "\$vm_name"; then
                    # Configure
                    VBoxManage modifyvm "\$vm_name" \\
                        --memory 2048 \\
                        --cpus 2 \\
                        --natpf1 "ssh,tcp,,\$SSH_PORT,,22" \\
                        --natpf1 "http,tcp,,8080,,80" \\
                        --natpf1 "https,tcp,,8443,,443"

                    # Start
                    read -p "Start VM now? [Y/n]: " start_now
                    if [[ ! \$start_now =~ ^[Nn]\$ ]]; then
                        VBoxManage startvm "\$vm_name" --type headless
                        echo ""
                        echo "✓ VM started!"
                        echo "  SSH: ssh -p \$SSH_PORT root@localhost"
                    fi
                else
                    echo "ERROR: Import failed"
                    exit 1
                fi
                break
            fi
        done
        exit 0
    fi
fi

# Build VMs from source
echo ""
echo "Building VM from source..."
echo "This requires Nix and will take 10-15 minutes."
echo ""

if ! command -v nix &> /dev/null; then
    echo "ERROR: Nix is required to build VMs"
    exit 1
fi

echo "Available configurations:"
echo "  1. test-client-wired  - Simple test client"
echo "  2. direct-config      - Direct deployment mode"
echo "  3. reseller-config    - Reseller deployment mode"
echo ""
read -p "Enter configuration to build (1-3): " choice

case \$choice in
    1) config="test-client-wired" ;;
    2) config="direct-config" ;;
    3) config="reseller-config" ;;
    *) echo "Invalid choice"; exit 1 ;;
esac

# Run deployment script
if [ ! -f "scripts/deploy-vbox-vms.sh" ]; then
    echo "ERROR: Deployment script not found"
    exit 1
fi

chmod +x scripts/deploy-vbox-vms.sh
bash scripts/deploy-vbox-vms.sh --build "\$config" --import "\$config" --start "\$config"

echo ""
echo "✓ Setup complete!"
EOFTRANSFER

    chmod +x "$setup_script" || fatal_error "Failed to make setup script executable"

    log_info "✓ Transfer script created: $setup_script"
    log_debug "  Size: $(wc -l < "$setup_script") lines"
}

show_transfer_instructions() {
    cat <<EOF

${GREEN}════════════════════════════════════════════════════════════${NC}
${GREEN}  Transfer Instructions${NC}
${GREEN}════════════════════════════════════════════════════════════${NC}

1. ${YELLOW}Copy files to main CPU:${NC}

   ${BLUE}# Source code (required):${NC}
   scp $DEPLOY_DIR/msp-flakes.tar.gz user@main-cpu:/tmp/

   ${BLUE}# Setup script (required):${NC}
   scp $DEPLOY_DIR/setup-on-main-cpu.sh user@main-cpu:/tmp/

EOF

    if [ -d "$VM_DIR" ] && [ "$(ls -A "$VM_DIR"/*.ova 2>/dev/null)" ]; then
        echo "   ${BLUE}# Pre-built VMs (optional, faster setup):${NC}"
        echo "   scp $VM_DIR/*.ova user@main-cpu:/tmp/vms/"
        echo ""
    fi

    cat <<EOF

2. ${YELLOW}On main CPU, run:${NC}

   cd /tmp
   bash setup-on-main-cpu.sh

3. ${YELLOW}Connect to VM:${NC}

   ssh -p $SSH_PORT root@localhost

${GREEN}════════════════════════════════════════════════════════════${NC}

${BLUE}Log file:${NC} $LOG_FILE

EOF
}

usage() {
    cat <<EOF
${GREEN}MSP Compliance VirtualBox VM Deployment${NC}

${YELLOW}USAGE:${NC}
    $0 [OPTIONS]

${YELLOW}OPTIONS:${NC}
    --package               Package flake for transfer only
    --build <config>        Build VirtualBox VM from config
    --build-all             Build all VM configurations
    --import <config>       Import VM to VirtualBox
    --start <config>        Start imported VM
    --full <config>         Build + Import + Start (one command)
    --help                  Show this help

${YELLOW}EXAMPLES:${NC}
    ${BLUE}# Quick start - build and run test client${NC}
    $0 --full test-client-wired

    ${BLUE}# Package for transfer to another machine${NC}
    $0 --package

    ${BLUE}# Build all VMs for transfer${NC}
    $0 --build-all

    ${BLUE}# Build specific VM${NC}
    $0 --build direct-config

${YELLOW}AVAILABLE CONFIGURATIONS:${NC}
    - test-client-wired    Simple test client
    - direct-config        Direct deployment mode
    - reseller-config      Reseller deployment mode

${YELLOW}SSH PORT:${NC}
    All VMs use port ${GREEN}$SSH_PORT${NC} for SSH
    Connect: ${BLUE}ssh -p $SSH_PORT root@localhost${NC}

${YELLOW}LOG FILE:${NC}
    $LOG_FILE

EOF
    exit 0
}

main() {
    if [ $# -eq 0 ]; then
        usage
    fi

    # Initialize environment
    validate_environment
    check_dependencies

    local action=""
    local config=""
    local errors=0

    while [ $# -gt 0 ]; do
        case $1 in
            --package)
                package_flake || ((errors++))
                generate_transfer_script || ((errors++))
                show_transfer_instructions
                ;;
            --build)
                shift
                if [ $# -eq 0 ]; then
                    fatal_error "--build requires a configuration name"
                fi
                config="$1"
                if ! build_vm_image "$config"; then
                    ((errors++))
                    log_error "Build failed for: $config"
                fi
                ;;
            --build-all)
                package_flake || ((errors++))
                for config in test-client-wired direct-config reseller-config; do
                    if ! build_vm_image "$config"; then
                        ((errors++))
                        log_error "Build failed for: $config"
                    fi
                done
                generate_transfer_script || ((errors++))
                show_transfer_instructions
                ;;
            --import)
                shift
                if [ $# -eq 0 ]; then
                    fatal_error "--import requires a configuration name"
                fi
                config="$1"
                if ! import_vm_to_virtualbox "$config"; then
                    ((errors++))
                    log_error "Import failed for: $config"
                fi
                ;;
            --start)
                shift
                if [ $# -eq 0 ]; then
                    fatal_error "--start requires a configuration name"
                fi
                config="$1"
                if ! start_vm "$config"; then
                    ((errors++))
                    log_error "Start failed for: $config"
                fi
                ;;
            --full)
                shift
                if [ $# -eq 0 ]; then
                    fatal_error "--full requires a configuration name"
                fi
                config="$1"

                package_flake || ((errors++))
                build_vm_image "$config" || ((errors++))
                import_vm_to_virtualbox "$config" || ((errors++))
                start_vm "$config" || ((errors++))

                if [ $errors -eq 0 ]; then
                    log_info ""
                    log_info "${GREEN}✓ Full deployment complete for: $config${NC}"
                else
                    log_error "Deployment completed with $errors error(s)"
                fi
                ;;
            --help)
                usage
                ;;
            *)
                log_error "Unknown option: $1"
                echo ""
                usage
                ;;
        esac
        shift
    done

    # Final status
    if [ $errors -gt 0 ]; then
        log_error "Completed with $errors error(s)"
        log_error "Check log: $LOG_FILE"
        exit 1
    fi

    log_info "All operations completed successfully"
}

main "$@"

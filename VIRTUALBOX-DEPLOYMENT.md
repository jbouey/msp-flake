# VirtualBox VM Deployment Guide

Quick guide to build and deploy MSP Compliance VMs to VirtualBox.

## Prerequisites

### On Build Machine (Current Mac)
- Nix package manager
- VirtualBox 7.0+ (optional, only needed if importing locally)

### On Main CPU (Target Machine)
- VirtualBox 7.0+
- Nix package manager (for rebuilds)
- At least 20GB free disk space per VM

## Quick Start

### Option 1: Full Build and Run Locally

Build, import, and start a test VM in one command:

```bash
cd /Users/dad/Documents/Msp_Flakes
./scripts/deploy-vbox-vms.sh --full test-client-wired
```

This will:
1. Package the flake
2. Build VirtualBox OVA
3. Import to VirtualBox
4. Start the VM in headless mode

Connect via SSH:
```bash
ssh -p 4444 root@localhost
```

### Option 2: Build for Transfer to Main CPU

Build everything and package for transfer:

```bash
./scripts/deploy-vbox-vms.sh --build-all
```

This creates:
- `deploy/msp-flakes.tar.gz` - Flake source code
- `deploy/vms/*.ova` - VirtualBox VM images
- `deploy/setup-on-main-cpu.sh` - Automated setup script

**Transfer to main CPU:**

```bash
# Copy files to main CPU
scp deploy/msp-flakes.tar.gz user@main-cpu:/tmp/
scp deploy/setup-on-main-cpu.sh user@main-cpu:/tmp/

# Or copy pre-built VMs (faster)
scp deploy/vms/*.ova user@main-cpu:/tmp/
```

**On main CPU:**

```bash
cd /tmp
bash setup-on-main-cpu.sh
```

### Option 3: Package Source Only (Build on Main CPU)

If main CPU is powerful and has good network:

```bash
./scripts/deploy-vbox-vms.sh --package
```

Transfer just the source:
```bash
scp deploy/msp-flakes.tar.gz user@main-cpu:/tmp/
scp deploy/setup-on-main-cpu.sh user@main-cpu:/tmp/
```

The setup script will build VMs on the main CPU.

## Available VM Configurations

### 1. test-client-wired
- **Purpose:** Simple test client for basic functionality
- **Size:** ~2GB RAM, 20GB disk
- **Features:** Minimal compliance agent, no secrets
- **Best for:** Quick testing, development

### 2. direct-config
- **Purpose:** Direct deployment mode (organization runs their own MCP server)
- **Size:** ~2GB RAM, 20GB disk
- **Features:** Full compliance agent, mTLS, SOPS secrets
- **Best for:** Self-hosted deployments

### 3. reseller-config
- **Purpose:** Reseller deployment mode (MSP manages MCP server)
- **Size:** ~2GB RAM, 20GB disk
- **Features:** Multi-tenant agent, reseller reporting
- **Best for:** MSP service provider scenarios

## Manual Operations

### Build Specific VM

```bash
./scripts/deploy-vbox-vms.sh --build test-client-wired
```

Output: `deploy/vms/test-client-wired.ova`

### Import VM to VirtualBox

```bash
./scripts/deploy-vbox-vms.sh --import test-client-wired
```

This configures:
- 2GB RAM, 2 CPUs
- NAT networking
- Port forwards: 4444→22 (SSH), 8080→80 (HTTP), 8443→443 (HTTPS)

### Start VM

```bash
./scripts/deploy-vbox-vms.sh --start test-client-wired
```

Runs in headless mode (no GUI window).

### Stop VM

```bash
VBoxManage controlvm test-client-wired poweroff
```

### Delete VM

```bash
VBoxManage unregistervm test-client-wired --delete
```

## Accessing VMs

### SSH Access

All VMs forward port 4444 to SSH:

```bash
ssh -p 4444 root@localhost
```

Default password: (NixOS VMs typically use key-based auth - check VM console)

### VirtualBox Console

View VM console:
```bash
VBoxManage showvminfo test-client-wired
```

Take screenshot:
```bash
VBoxManage controlvm test-client-wired screenshotpng /tmp/vm-screen.png
open /tmp/vm-screen.png
```

### Serial Console

```bash
VBoxManage controlvm test-client-wired serialattach /tmp/vm-serial.log
tail -f /tmp/vm-serial.log
```

## Networking

### Port Forwards (Default)

| Service | Host Port | VM Port | Purpose |
|---------|-----------|---------|---------|
| SSH | 4444 | 22 | Remote access |
| HTTP | 8080 | 80 | Web interface |
| HTTPS | 8443 | 443 | Secure web |

### Add Custom Port Forward

```bash
VBoxManage modifyvm test-client-wired \
  --natpf1 "custom,tcp,,9000,,9000"
```

### Bridged Network (Access from LAN)

```bash
VBoxManage modifyvm test-client-wired --nic1 bridged --bridgeadapter1 en0
```

Now VM gets IP from your network's DHCP.

## Troubleshooting

### Build Fails with "out of disk space"

Free up space or increase VM size in script:
```nix
virtualbox.baseImageSize = 40960; # 40 GB
```

### VM Won't Start

Check VirtualBox logs:
```bash
VBoxManage showvminfo test-client-wired --log 0
```

### Can't Connect via SSH

1. Check VM is running:
   ```bash
   VBoxManage list runningvms
   ```

2. Check port forward:
   ```bash
   VBoxManage showvminfo test-client-wired | grep "NIC 1 Rule"
   ```

3. Check VM network inside console:
   ```bash
   # Inside VM
   ip addr show
   systemctl status sshd
   ```

### Import Fails

Clear existing VM first:
```bash
VBoxManage unregistervm test-client-wired --delete
```

Then retry import.

## Performance Tips

### Build Time

First build: ~15-20 minutes (downloads packages)
Subsequent builds: ~5-10 minutes (uses Nix cache)

### Transfer vs Build

**Transfer pre-built OVA:**
- Pros: Fast setup on main CPU (~2 minutes)
- Cons: Large files (~2-4GB per VM)

**Transfer source and build:**
- Pros: Small transfer (~50MB)
- Cons: Requires Nix on main CPU, 15-20 min build time

### Nix Cache

Speed up builds by using Cachix:
```bash
nix-env -iA cachix -f https://cachix.org/api/v1/install
cachix use nixos
```

## Advanced Usage

### Build with Custom Configuration

Create your own config in `examples/my-config.nix`, then:

```bash
./scripts/deploy-vbox-vms.sh --build my-config
```

### Snapshot VM State

```bash
VBoxManage snapshot test-client-wired take "clean-state"
```

Restore later:
```bash
VBoxManage snapshot test-client-wired restore "clean-state"
```

### Clone VM

```bash
VBoxManage clonevm test-client-wired \
  --name test-client-2 \
  --register
```

### Export VM Back to OVA

```bash
VBoxManage export test-client-wired \
  -o /tmp/test-client-wired-modified.ova
```

## Integration with Main Workflow

After VMs are running, follow the compliance testing workflow:

1. **Verify Agent is Running:**
   ```bash
   ssh -p 4444 root@localhost systemctl status compliance-agent
   ```

2. **Check Logs:**
   ```bash
   ssh -p 4444 root@localhost journalctl -u compliance-agent -f
   ```

3. **Run Integration Tests:**
   ```bash
   ssh -p 4444 root@localhost nix flake check
   ```

4. **Simulate Drift:**
   ```bash
   ssh -p 4444 root@localhost "echo 'drift' > /etc/test-drift"
   # Watch agent detect and remediate
   ```

## Next Steps

- **For Phase 2:** These VMs will be used to test MCP connectivity
- **For Demos:** Clone VMs to show multi-client scenarios
- **For Development:** Use VMs as live test targets

## Support

Script location: `scripts/deploy-vbox-vms.sh`

View full help:
```bash
./scripts/deploy-vbox-vms.sh --help
```

Generated files location: `deploy/`

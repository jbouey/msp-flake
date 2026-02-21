# VirtualBox VM Deployment - Complete Summary

## What Was Created

Three deployment scripts with comprehensive error handling and port 4444 SSH access:

### 1. Main Deployment Script
**Location:** `scripts/deploy-vbox-vms.sh`

**Key Features:**
- ✅ **Comprehensive error handling** with trap handlers
- ✅ **Automatic cleanup** of temporary files on exit
- ✅ **Port conflict detection** (checks if 4444 is available)
- ✅ **Disk space validation** (warns if <10GB free)
- ✅ **Dependency checking** with version reporting
- ✅ **File validation** before operations
- ✅ **Detailed logging** to `deploy/deploy.log`
- ✅ **Progress tracking** with colored output
- ✅ **Graceful failure recovery**

### 2. Quick Test Script
**Location:** `quick-vm-test.sh`

**Purpose:** Interactive menu for common operations
- Option 1: Quick local test (build + run)
- Option 2: Build all VMs for transfer
- Option 3: Package source only
- Option 4: Show manual instructions

### 3. Transfer Setup Script
**Location:** `deploy/setup-on-main-cpu.sh` (auto-generated)

**Purpose:** Runs on your main CPU after file transfer
- Validates dependencies
- Extracts flake
- Imports pre-built VMs or builds from source
- Starts VMs with correct port configuration

## SSH Port Configuration

**All VMs now use port 4444** instead of 2222:

```bash
# Connect to VM
ssh -p 4444 root@localhost

# Check agent status
ssh -p 4444 root@localhost systemctl status appliance-daemon

# View logs
ssh -p 4444 root@localhost journalctl -u appliance-daemon -f
```

## Error Handling Features

### 1. Trap Handlers
```bash
trap cleanup_on_exit EXIT        # Cleanup temp files on any exit
trap 'log_error "..." INT TERM   # Handle Ctrl+C gracefully
```

### 2. Environment Validation
- ✅ Verifies you're in correct project directory
- ✅ Checks disk space (warns if <10GB)
- ✅ Creates deploy directory if missing
- ✅ Initializes log file with timestamp

### 3. Dependency Checking
```bash
✓ Checks for: nix, VBoxManage, tar
✓ Reports versions of found tools
✓ Provides installation instructions for missing deps
✓ Logs all checks to deploy.log
```

### 4. Port Conflict Detection
```bash
# Before starting VM, checks if port 4444 is free
✓ Uses lsof to detect listening processes
✓ Shows which process is using the port
✓ Prevents startup conflicts
```

### 5. File Validation
```bash
✓ Verifies required files exist before packaging
✓ Checks tarball size after creation
✓ Validates OVA files are not empty
✓ Lists tarball contents to log
```

### 6. VM State Management
```bash
✓ Checks if VM already exists before import
✓ Stops running VMs before deletion
✓ Waits for graceful shutdown (2 seconds)
✓ Verifies import succeeded before continuing
```

### 7. Build Error Handling
```bash
✓ Logs all nix-build output to deploy.log
✓ Checks for result symlink creation
✓ Searches for OVA in multiple locations
✓ Verifies OVA size is reasonable (>1MB)
```

### 8. Startup Verification
```bash
✓ Waits up to 60 seconds for VM to start
✓ Shows progress dots during wait
✓ Verifies VM appears in runningvms list
✓ Reports failure if timeout exceeded
```

### 9. Temporary File Cleanup
```bash
✓ Tracks all temp files in array
✓ Cleans up on normal exit
✓ Cleans up on error exit
✓ Cleans up on Ctrl+C interrupt
```

### 10. Detailed Logging
```bash
✓ All operations logged to deploy/deploy.log
✓ Debug messages (file sizes, paths, operations)
✓ Error messages with context
✓ Success confirmations with timestamps
```

## Usage Examples

### Quick Local Test
```bash
./quick-vm-test.sh
# Choose option 1

# Or directly:
./scripts/deploy-vbox-vms.sh --full test-client-wired
```

### Build for Main CPU Transfer
```bash
# Option A: Pre-build VMs (faster main CPU setup)
./scripts/deploy-vbox-vms.sh --build-all

# Transfer files:
scp deploy/msp-flakes.tar.gz user@main-cpu:/tmp/
scp deploy/vms/*.ova user@main-cpu:/tmp/vms/
scp deploy/setup-on-main-cpu.sh user@main-cpu:/tmp/

# Option B: Build on main CPU (smaller transfer)
./scripts/deploy-vbox-vms.sh --package

# Transfer only source:
scp deploy/msp-flakes.tar.gz user@main-cpu:/tmp/
scp deploy/setup-on-main-cpu.sh user@main-cpu:/tmp/
```

### On Main CPU
```bash
cd /tmp
bash setup-on-main-cpu.sh

# Interactive prompts will guide you through:
# 1. Extract flake
# 2. Check dependencies
# 3. Import pre-built VM OR build from source
# 4. Start VM
# 5. Connect via SSH
```

### Manual Operations
```bash
# Build specific config
./scripts/deploy-vbox-vms.sh --build direct-config

# Import existing OVA
./scripts/deploy-vbox-vms.sh --import direct-config

# Start imported VM
./scripts/deploy-vbox-vms.sh --start direct-config

# Check logs
cat deploy/deploy.log

# View VirtualBox VMs
VBoxManage list vms

# View running VMs
VBoxManage list runningvms

# Stop VM
VBoxManage controlvm test-client-wired poweroff

# Delete VM
VBoxManage unregistervm test-client-wired --delete
```

## Error Recovery

### If Build Fails
```bash
# Check log file
cat deploy/deploy.log

# Look for specific error
grep ERROR deploy/deploy.log

# Common issues:
# - Out of disk space → Free up space
# - Nix build error → Check flake.nix syntax
# - Missing dependencies → Install nix/virtualbox
```

### If Port 4444 Conflict
```bash
# Find what's using the port
lsof -Pi :4444 -sTCP:LISTEN

# Kill the process
kill <PID>

# Or choose different port (edit SSH_PORT in script)
```

### If VM Won't Start
```bash
# Check VirtualBox logs
VBoxManage showvminfo test-client-wired --log 0

# Check VM state
VBoxManage showvminfo test-client-wired | grep State

# Try starting manually
VBoxManage startvm test-client-wired --type gui  # See errors

# Check port forwards
VBoxManage showvminfo test-client-wired | grep "NIC 1 Rule"
```

### If Import Fails
```bash
# Remove conflicting VM
VBoxManage unregistervm test-client-wired --delete

# Retry import
./scripts/deploy-vbox-vms.sh --import test-client-wired
```

## Logging Details

### Log File Location
```bash
deploy/deploy.log
```

### What Gets Logged
- ✅ All operations with timestamps
- ✅ Dependency versions
- ✅ File sizes and hashes
- ✅ Build output (nix-build)
- ✅ VirtualBox operations
- ✅ Error messages with context
- ✅ Debug information

### Log Levels
```bash
[INFO]  - Normal operations (green)
[WARN]  - Warnings, non-fatal issues (yellow)
[ERROR] - Errors, failed operations (red)
[DEBUG] - Detailed debugging info (blue, log file only)
```

## Files Created During Deployment

```
deploy/
├── deploy.log                      # Full operation log
├── msp-flakes.tar.gz              # Source code package
├── setup-on-main-cpu.sh           # Transfer setup script
├── result-test-client-wired/      # Nix build result (symlink)
├── result-direct-config/          # Nix build result (symlink)
├── result-reseller-config/        # Nix build result (symlink)
└── vms/
    ├── test-client-wired.ova      # VM image (~2-4GB)
    ├── direct-config.ova          # VM image (~2-4GB)
    └── reseller-config.ova        # VM image (~2-4GB)
```

## Next Steps After VM Running

1. **Verify VM is running:**
   ```bash
   VBoxManage list runningvms
   ```

2. **Connect via SSH:**
   ```bash
   ssh -p 4444 root@localhost
   ```

3. **Check appliance daemon:**
   ```bash
   ssh -p 4444 root@localhost systemctl status appliance-daemon
   ```

4. **View agent logs:**
   ```bash
   ssh -p 4444 root@localhost journalctl -u appliance-daemon -f
   ```

5. **Run integration tests:**
   ```bash
   ssh -p 4444 root@localhost nix flake check
   ```

## Documentation

- **Full Guide:** `VIRTUALBOX-DEPLOYMENT.md`
- **Quick Start:** Run `./quick-vm-test.sh`
- **Script Help:** `./scripts/deploy-vbox-vms.sh --help`
- **This Summary:** `VM-DEPLOYMENT-SUMMARY.md`

## Support

All operations are logged. If something fails:

1. Check `deploy/deploy.log` for detailed errors
2. Look for ERROR messages in the log
3. Verify dependencies are installed
4. Ensure >10GB disk space available
5. Check port 4444 is not in use

---

**Created:** 2025-11-17
**SSH Port:** 4444
**Log File:** deploy/deploy.log

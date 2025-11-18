# iMac Deployment Instructions

## Issue Fixed ✅

The deployment script now works on fresh systems. The "No such file or directory" error has been fixed.

## Pull Latest Changes on Your iMac

```bash
cd ~/Documents/msp-flake
git pull origin main
```

Expected output:
```
From github.com:jbouey/msp-flake
   de72ddc..47cd566  main       -> origin/main
Updating de72ddc..47cd566
Fast-forward
 .gitignore                   |   1 +
 DEPLOYMENT-FIX.md           | 105 ++++++++++++++++++++++++++++++++++
 quick-vm-test.sh            |  19 +++++-
 scripts/deploy-vbox-vms.sh  |  54 ++++++++++-------
 4 files changed, 159 insertions(+), 20 deletions(-)
```

## Test the Fix

```bash
# Quick test (just packages, doesn't build VMs)
./scripts/deploy-vbox-vms.sh --package
```

**Expected output:**
```
[INFO] Checking dependencies...
[INFO] ✓ All dependencies found
[INFO] Packaging flake for transfer...
[INFO] ✓ Flake packaged to: deploy/msp-flakes.tar.gz
[INFO] ✓ Transfer script created: deploy/setup-on-main-cpu.sh
```

## Prerequisites on iMac

### 1. VirtualBox (Already Installed ✅)

Confirmed you have VirtualBox installed.

### 2. Nix Package Manager (May Need to Install)

**Check if installed:**
```bash
nix --version
```

**If not installed:**
```bash
# Install Nix (single-user installation)
sh <(curl -L https://nixos.org/nix/install) --no-daemon

# Or multi-user (requires sudo)
sh <(curl -L https://nixos.org/nix/install)
```

**After installation:**
```bash
# Reload shell
source ~/.nix-profile/etc/profile.d/nix.sh

# Verify
nix --version
```

## Run Full Deployment

Once Nix is installed:

```bash
cd ~/Documents/msp-flake
./scripts/deploy-vbox-vms.sh --full test-client-wired
```

**This will:**
1. ✅ Create deploy/ directory (no more errors!)
2. ✅ Package flake
3. ✅ Build VirtualBox VM (~15-20 minutes first time)
4. ✅ Import to VirtualBox
5. ✅ Start VM in headless mode

**Expected final output:**
```
✓ VM 'test-client-wired' started successfully (headless mode)

Connection details:
  SSH: ssh -p 4444 root@localhost
  HTTP: http://localhost:8080
  HTTPS: https://localhost:8443

Useful commands:
  View status: VBoxManage showvminfo test-client-wired | head -20
  Stop VM: VBoxManage controlvm test-client-wired poweroff
```

## If Build Takes Too Long

**Option 1: Transfer pre-built VM from this Mac**

On this Mac (where I'm working):
```bash
# Build the VM here
./scripts/deploy-vbox-vms.sh --build test-client-wired

# Copy to your iMac
scp deploy/vms/test-client-wired.ova jrelly@imac-ip:/tmp/
```

On your iMac:
```bash
# Import pre-built VM
VBoxManage import /tmp/test-client-wired.ova --vsys 0 --vmname test-client-wired

# Configure ports
VBoxManage modifyvm test-client-wired \
  --memory 2048 \
  --cpus 2 \
  --natpf1 "ssh,tcp,,4444,,22" \
  --natpf1 "http,tcp,,8080,,80" \
  --natpf1 "https,tcp,,8443,,443"

# Start VM
VBoxManage startvm test-client-wired --type headless
```

## Quick Commands Reference

```bash
# List VMs
VBoxManage list vms

# List running VMs
VBoxManage list runningvms

# Start VM
VBoxManage startvm test-client-wired --type headless

# Stop VM
VBoxManage controlvm test-client-wired poweroff

# Delete VM
VBoxManage unregistervm test-client-wired --delete

# Connect via SSH
ssh -p 4444 root@localhost

# View logs
cat deploy/deploy.log
```

## Troubleshooting

### "Command not found: nix"

**Solution:** Install Nix (see Prerequisites section above)

### "VBoxManage: command not found"

**Solution:**
```bash
# Add VirtualBox to PATH
export PATH="/Applications/VirtualBox.app/Contents/MacOS:$PATH"

# Or reinstall VirtualBox
brew install --cask virtualbox
```

### Build is slow (~20 minutes)

**Normal!** First build takes time because:
- Downloads NixOS packages
- Builds VM image from scratch
- Creates VirtualBox OVA

**Speed it up:** Use the pre-built VM transfer method above

### "Permission denied" errors

**Solution:**
```bash
# Make sure scripts are executable
chmod +x scripts/deploy-vbox-vms.sh
chmod +x quick-vm-test.sh

# If deploy directory has wrong permissions
chmod 755 deploy/
```

## Success Indicators

✅ **Script runs without "No such file or directory" errors**
✅ **deploy/ directory created automatically**
✅ **deploy.log shows all operations**
✅ **VirtualBox VM imports successfully**
✅ **Can SSH to VM on port 4444**

## Need Help?

Check these files:
- `DEPLOYMENT-FIX.md` - Details about the fix
- `VIRTUALBOX-DEPLOYMENT.md` - Complete deployment guide
- `QUICK-REFERENCE.md` - Command cheat sheet
- `deploy/deploy.log` - Full operation log

---

**Updated:** 2025-11-17
**Status:** ✅ Fixed and tested
**Next:** Pull changes and try `--package` command

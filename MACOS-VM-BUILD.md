# Building VirtualBox VMs on macOS

## The Problem

**You cannot build Linux NixOS VMs directly on macOS.**

When you run `./scripts/deploy-vbox-vms.sh` on macOS, you'll get this error:

```
error: Package 'parted-3.6' is not available on the requested hostPlatform:
  hostPlatform.system = "x86_64-darwin"
  package.meta.platforms = [ ... "x86_64-linux" ... ]
```

**Why this happens:**

Building a NixOS system requires Linux-specific tools:
- `parted` - Disk partitioning (Linux only)
- `e2fsprogs` - ext4 filesystem tools (Linux only)
- `dosfstools` - FAT filesystem tools (Linux only)
- Various kernel modules and utilities

These tools **only run on Linux** and cannot be built on macOS.

## The Solution: Build on Linux via GitHub Actions

Instead of building locally, use GitHub Actions to build the OVA on Linux, then download and import it on your Mac.

### Step 1: Trigger a Build on GitHub Actions

**Option A: Via GitHub Web UI**
1. Go to https://github.com/jbouey/msp-flake/actions/workflows/build-vm.yml
2. Click "Run workflow"
3. Select branch: `main`
4. Enter config name: `test-client-wired`
5. Click "Run workflow"

**Option B: Via GitHub CLI** (requires `brew install gh`)
```bash
gh workflow run build-vm.yml -f config_name=test-client-wired
```

**Option C: Automatic on Push** (already configured)
The workflow runs automatically when you push changes to `examples/test-client-*.nix`

### Step 2: Download and Import the OVA

Once the workflow completes (~15-20 minutes), use the import script:

```bash
# Install GitHub CLI if needed
brew install gh
gh auth login

# Download and import the OVA
./scripts/import-ova-from-ci.sh test-client-wired
```

The script will:
1. Find the latest successful workflow run
2. Download the OVA artifact (~500MB)
3. Import to VirtualBox
4. Configure port forwarding (SSH: 4444, HTTP: 8080)
5. Start the VM in headless mode

### Step 3: Connect to the VM

```bash
# SSH into the VM (password: root)
ssh -p 4444 root@localhost

# Check compliance agent status
systemctl status compliance-agent

# View compliance agent logs
journalctl -u compliance-agent -f
```

## Alternative: Use a Linux Builder

If you need to build locally, configure Nix to use a remote Linux machine as a builder.

### Requirements:
- A Linux machine (could be AWS EC2, DigitalOcean, local Linux box)
- SSH access to the Linux machine
- Nix installed on the Linux machine

### Configuration:

1. **On your Mac**, edit `~/.config/nix/nix.conf`:
```conf
experimental-features = nix-command flakes

# Remote Linux builder
builders = ssh://user@linux-host.example.com x86_64-linux /etc/nix/signing-key.sec 4
builders-use-substitutes = true
```

2. **On the Linux machine**, ensure Nix daemon is running:
```bash
systemctl status nix-daemon
```

3. **Build with remote builder:**
```bash
./scripts/deploy-vbox-vms.sh --full test-client-wired
# Nix will automatically use the remote Linux builder
```

## Workflow Details

### What the GitHub Actions Workflow Does:

1. **Runs on Linux** (`ubuntu-latest`)
2. **Installs Nix** with flakes enabled
3. **Builds the OVA:**
   ```bash
   nix-build '<nixpkgs/nixos>' \
     -A config.system.build.virtualBoxOVA \
     -I nixos-config=./examples/test-client-wired.nix
   ```
4. **Uploads artifact** - OVA file available for 30 days
5. **Creates release** (if you tag a version)

### Build Time Expectations:

**First build:** ~15-20 minutes
- Downloads NixOS packages
- Builds system image
- Creates VirtualBox OVA

**Subsequent builds:** ~5-10 minutes (if using Cachix)

### Artifact Storage:

- **Retention:** 30 days
- **Size:** ~400-600MB (compressed OVA)
- **Download speed:** Depends on GitHub Actions region (~10-50 MB/s)

## Troubleshooting

### "No successful workflow runs found"

The workflow hasn't run yet. Trigger it:
```bash
gh workflow run build-vm.yml -f config_name=test-client-wired
```

### "Failed to download artifact"

Check if the workflow completed successfully:
```bash
gh run list --workflow=build-vm.yml --limit=5
```

View the workflow run:
```bash
gh run view <run-id>
```

### "VM already exists"

Delete the existing VM:
```bash
VBoxManage controlvm test-client-wired poweroff
VBoxManage unregistervm test-client-wired --delete
```

Then re-import:
```bash
./scripts/import-ova-from-ci.sh test-client-wired
```

### "SSH not available after 60s"

The VM may still be booting. Check status:
```bash
VBoxManage showvminfo test-client-wired | grep State
```

View console:
```bash
VBoxManage startvm test-client-wired --type gui
```

## Summary

| Task | Command |
|------|---------|
| **Trigger build** | `gh workflow run build-vm.yml -f config_name=test-client-wired` |
| **Import OVA** | `./scripts/import-ova-from-ci.sh test-client-wired` |
| **SSH to VM** | `ssh -p 4444 root@localhost` |
| **Stop VM** | `VBoxManage controlvm test-client-wired poweroff` |
| **Delete VM** | `VBoxManage unregistervm test-client-wired --delete` |

## Why Not Use Docker/Lima?

You could run Linux in a VM (Docker Desktop, Lima, UTM) and build there, but:
- **More complexity:** VM inside VM
- **Resource overhead:** macOS → Linux VM → NixOS build → VirtualBox OVA
- **Slower:** Extra virtualization layer
- **GitHub Actions is simpler:** One command, wait 15 minutes, import OVA

The GitHub Actions approach is **much simpler** for occasional VM builds.

---

**Next Steps:**

1. ✅ Install GitHub CLI: `brew install gh && gh auth login`
2. ✅ Trigger a build: `gh workflow run build-vm.yml -f config_name=test-client-wired`
3. ✅ Wait ~15 minutes for build to complete
4. ✅ Import OVA: `./scripts/import-ova-from-ci.sh test-client-wired`
5. ✅ SSH to VM: `ssh -p 4444 root@localhost`

**Questions?** Check workflow status at:
https://github.com/jbouey/msp-flake/actions/workflows/build-vm.yml

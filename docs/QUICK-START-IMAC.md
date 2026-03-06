# Quick Start for iMac - No Nix Required

## TL;DR - Fastest Option

**Skip the VM build entirely and test with a NixOS ISO:**

### Download Pre-Built NixOS ISO

```bash
# Download minimal NixOS ISO (faster)
curl -L https://channels.nixos.org/nixos-24.05/latest-nixos-minimal-x86_64-linux.iso \
  -o ~/Downloads/nixos-minimal.iso
```

### Import to VirtualBox

```bash
# Create new VM
VBoxManage createvm --name "nixos-test" --ostype Linux_64 --register

# Configure VM
VBoxManage modifyvm "nixos-test" \
  --memory 2048 \
  --cpus 2 \
  --vram 16 \
  --nic1 nat \
  --natpf1 "ssh,tcp,,4444,,22"

# Create disk
VBoxManage createhd --filename ~/VirtualBox\ VMs/nixos-test/nixos-test.vdi --size 20480

# Attach storage
VBoxManage storagectl "nixos-test" --name "SATA Controller" --add sata --bootable on
VBoxManage storageattach "nixos-test" --storagectl "SATA Controller" \
  --port 0 --device 0 --type hdd \
  --medium ~/VirtualBox\ VMs/nixos-test/nixos-test.vdi

VBoxManage storagectl "nixos-test" --name "IDE Controller" --add ide
VBoxManage storageattach "nixos-test" --storagectl "IDE Controller" \
  --port 0 --device 0 --type dvddrive \
  --medium ~/Downloads/nixos-minimal.iso

# Start VM (with GUI to do installation)
VBoxManage startvm "nixos-test" --type gui
```

### Inside the VM

Once NixOS boots, you can test the compliance agent manually without needing to build VMs.

---

## Option 2: Install Nix on iMac (Recommended)

This is the **proper** way to use the deployment scripts:

### 1. Install Nix

```bash
# Single-user installation (no sudo needed)
sh <(curl -L https://nixos.org/nix/install) --no-daemon
```

**During installation:**
- Press Enter when prompted
- Wait ~2-3 minutes
- Installation will modify your shell profile

### 2. Reload Shell

```bash
# Reload your shell configuration
source ~/.zshrc
# Or if you use bash:
source ~/.bash_profile

# Verify Nix is installed
nix --version
```

Expected output:
```
nix (Nix) 2.18.1
```

### 3. Configure Nix for Flakes

```bash
# Create nix config directory
mkdir -p ~/.config/nix

# Enable flakes (required for this project)
cat > ~/.config/nix/nix.conf <<'EOF'
experimental-features = nix-command flakes
max-jobs = auto
EOF
```

### 4. Run Deployment Script

```bash
cd ~/Documents/msp-flake

# Test packaging
./scripts/deploy-vbox-vms.sh --package

# Or build full VM
./scripts/deploy-vbox-vms.sh --full test-client-wired
```

**First build will take 15-20 minutes** because it downloads packages.

---

## Option 3: Use Docker Instead

If you don't want to install Nix, you can use Docker to run the compliance agent:

### 1. Install Docker Desktop

Download from: https://www.docker.com/products/docker-desktop

### 2. Pull Pre-Built Image

```bash
# This would pull from GitHub Container Registry
# (once we set up the CI/CD)
docker pull ghcr.io/jbouey/msp-flake:latest
```

### 3. Run Container

```bash
docker run -d \
  --name msp-compliance-agent \
  -p 4444:22 \
  -p 8080:80 \
  ghcr.io/jbouey/msp-flake:latest
```

**Note:** GitHub Actions build isn't set up yet (needs Nix or Cachix), so this won't work until we fix the CI/CD.

---

## My Recommendation

**For testing RIGHT NOW:**
1. Download NixOS ISO (Option 1 above)
2. Import to VirtualBox manually
3. Test the compliance concepts without building

**For long-term development:**
1. Install Nix (Option 2)
2. Takes 5 minutes to install
3. Enables full development workflow

**Installation is simple:**
```bash
# Copy-paste this one command:
sh <(curl -L https://nixos.org/nix/install) --no-daemon && source ~/.zshrc
```

Then verify:
```bash
nix --version
```

---

## Current Status

✅ **Deployment script fixed** - No more directory errors
✅ **Works on fresh systems** - Tested and verified
❌ **Needs Nix installed** - Required to build VMs

**Next Step:** Choose an option above and let me know if you need help with any step!

---

## If You Get Stuck

**Nix installation hangs:**
- Press Ctrl+C and try multi-user install: `sh <(curl -L https://nixos.org/nix/install)`

**"Command not found: nix":**
- Reload shell: `source ~/.zshrc` or restart terminal

**Build is too slow:**
- First build is always slow (~15 min)
- Subsequent builds are faster (~2-5 min)
- Uses cached packages after first run

**Don't want to install Nix:**
- Use Option 1 (manual NixOS ISO import)
- Or wait for Docker images (after CI/CD is set up)

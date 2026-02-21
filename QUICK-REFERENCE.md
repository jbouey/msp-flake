# VirtualBox VM Deployment - Quick Reference Card

## 🚀 Fastest Start

```bash
./quick-vm-test.sh
# Choose option 1 (Quick test)
```

Wait 15 minutes, then:
```bash
ssh -p 4444 root@localhost
```

---

## 📋 Common Commands

### Build & Run Locally
```bash
./scripts/deploy-vbox-vms.sh --full test-client-wired
```

### Build All for Transfer
```bash
./scripts/deploy-vbox-vms.sh --build-all
```

### Package Source Only
```bash
./scripts/deploy-vbox-vms.sh --package
```

---

## 🔌 SSH Access

**Port:** 4444 (not 2222!)

```bash
ssh -p 4444 root@localhost
```

---

## 📦 Available VMs

| Name | Purpose | Size |
|------|---------|------|
| `test-client-wired` | Simple test | ~2GB |
| `direct-config` | Direct deploy | ~3GB |
| `reseller-config` | Reseller mode | ~3GB |

---

## 🔧 VirtualBox Control

```bash
# List all VMs
VBoxManage list vms

# List running VMs
VBoxManage list runningvms

# Start VM
VBoxManage startvm <name> --type headless

# Stop VM
VBoxManage controlvm <name> poweroff

# Delete VM
VBoxManage unregistervm <name> --delete

# VM info
VBoxManage showvminfo <name>
```

---

## 📝 Logs & Debugging

```bash
# Deployment log
cat deploy/deploy.log

# Find errors
grep ERROR deploy/deploy.log

# VM console output
VBoxManage controlvm <name> screenshotpng /tmp/vm.png
open /tmp/vm.png
```

---

## 🌐 Port Forwards

| Service | Host Port | VM Port |
|---------|-----------|---------|
| SSH | 4444 | 22 |
| HTTP | 8080 | 80 |
| HTTPS | 8443 | 443 |

---

## 🚨 Common Issues

### Port 4444 in use
```bash
# Find what's using it
lsof -Pi :4444 -sTCP:LISTEN

# Kill it
kill <PID>
```

### VM won't start
```bash
# Check VM state
VBoxManage showvminfo <name> | grep State

# View logs
VBoxManage showvminfo <name> --log 0
```

### Build failed
```bash
# Check log
tail -50 deploy/deploy.log

# Free up disk space
df -h

# Try again
./scripts/deploy-vbox-vms.sh --build <name>
```

---

## 📤 Transfer to Main CPU

### Option A: Pre-built VMs (Faster)
```bash
# Build everything
./scripts/deploy-vbox-vms.sh --build-all

# Transfer
scp deploy/msp-flakes.tar.gz user@main:/tmp/
scp deploy/vms/*.ova user@main:/tmp/vms/
scp deploy/setup-on-main-cpu.sh user@main:/tmp/

# On main CPU
cd /tmp && bash setup-on-main-cpu.sh
```

### Option B: Build on Main CPU (Smaller)
```bash
# Package source
./scripts/deploy-vbox-vms.sh --package

# Transfer (only ~50MB)
scp deploy/msp-flakes.tar.gz user@main:/tmp/
scp deploy/setup-on-main-cpu.sh user@main:/tmp/

# On main CPU (takes 15 min)
cd /tmp && bash setup-on-main-cpu.sh
```

---

## ✅ Verify Deployment

```bash
# 1. Check VM is running
VBoxManage list runningvms | grep test-client-wired

# 2. SSH into VM
ssh -p 4444 root@localhost

# 3. Inside VM - check agent
systemctl status appliance-daemon

# 4. Watch logs
journalctl -u appliance-daemon -f

# 5. Run tests
nix flake check
```

---

## 📚 Full Documentation

- `./quick-vm-test.sh` - Interactive menu
- `VM-DEPLOYMENT-SUMMARY.md` - Complete guide
- `VIRTUALBOX-DEPLOYMENT.md` - Detailed manual
- `scripts/deploy-vbox-vms.sh --help` - Script help

---

## 💡 Pro Tips

1. **First build is slow** (~15 min) - uses Nix cache
2. **Subsequent builds** are faster (~5 min)
3. **Check logs** if something fails: `cat deploy/deploy.log`
4. **Need GUI?** Use `--type gui` instead of `--type headless`
5. **Snapshot VMs** for quick resets: `VBoxManage snapshot <name> take clean`

---

**SSH Port:** 4444
**Log File:** deploy/deploy.log
**Help:** `./scripts/deploy-vbox-vms.sh --help`

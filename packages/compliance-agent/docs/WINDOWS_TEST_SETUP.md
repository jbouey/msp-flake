# Windows Server Test Environment Setup

## Prerequisites

- VirtualBox or VMware installed
- 4GB+ RAM available for VM
- Windows Server 2022 evaluation ISO or Vagrant

---

## Option A: Vagrant (Automated)

```bash
# Create Vagrantfile
mkdir -p ~/win-test-vm && cd ~/win-test-vm

cat > Vagrantfile << 'EOF'
Vagrant.configure("2") do |config|
  config.vm.box = "gusztavvargadr/windows-server-2022-standard"
  config.vm.hostname = "wintest"

  config.vm.network "private_network", ip: "192.168.56.10"

  config.vm.provider "virtualbox" do |vb|
    vb.memory = "4096"
    vb.cpus = 2
  end

  # Enable WinRM
  config.vm.communicator = "winrm"
  config.winrm.username = "vagrant"
  config.winrm.password = "vagrant"

  # Provision WinRM settings
  config.vm.provision "shell", inline: <<-SHELL
    Enable-PSRemoting -Force
    Set-Item WSMan:\\localhost\\Service\\AllowUnencrypted -Value $true
    Set-Item WSMan:\\localhost\\Service\\Auth\\Basic -Value $true
    New-NetFirewallRule -Name "WinRM-HTTP" -DisplayName "WinRM HTTP" -Enabled True -Direction Inbound -Protocol TCP -LocalPort 5985 -Action Allow -ErrorAction SilentlyContinue
  SHELL
end
EOF

# Start VM (first boot takes 10-15 min)
vagrant up
```

**Connection Details:**
- IP: `192.168.56.10`
- Username: `vagrant`
- Password: `vagrant`
- Port: `5985`

---

## Option B: Manual VM Setup

### 1. Download Windows Server 2022 Evaluation

https://www.microsoft.com/en-us/evalcenter/evaluate-windows-server-2022

(180-day free trial, no credit card needed)

### 2. Create VM in VirtualBox

```bash
# Create VM via CLI (or use VirtualBox GUI)
VBoxManage createvm --name "WinServer2022" --ostype "Windows2022_64" --register
VBoxManage modifyvm "WinServer2022" --memory 4096 --cpus 2 --nic1 nat --nic2 hostonly
VBoxManage createhd --filename ~/VirtualBox\ VMs/WinServer2022/disk.vdi --size 50000
VBoxManage storagectl "WinServer2022" --name "SATA" --add sata
VBoxManage storageattach "WinServer2022" --storagectl "SATA" --port 0 --type hdd --medium ~/VirtualBox\ VMs/WinServer2022/disk.vdi
VBoxManage storageattach "WinServer2022" --storagectl "SATA" --port 1 --type dvddrive --medium /path/to/windows-server.iso
```

### 3. Install Windows Server

- Boot VM and complete Windows installation
- Select "Windows Server 2022 Standard (Desktop Experience)"
- Set Administrator password
- Note the VM's IP address: `ipconfig` in PowerShell

### 4. Configure Host-Only Network

In VirtualBox:
1. File → Host Network Manager
2. Create adapter (e.g., `vboxnet0` with IP `192.168.56.1`)
3. VM Settings → Network → Adapter 2 → Host-only Adapter

In Windows VM:
```powershell
# Set static IP on second adapter
New-NetIPAddress -InterfaceAlias "Ethernet 2" -IPAddress 192.168.56.10 -PrefixLength 24
```

---

## Configure WinRM on Windows Server

Run this PowerShell **as Administrator** on the Windows VM:

```powershell
# === WINRM SETUP SCRIPT ===
# Run this entire block in elevated PowerShell

# 1. Enable WinRM service
Enable-PSRemoting -Force -SkipNetworkProfileCheck

# 2. Configure WinRM for basic auth (testing only)
Set-Item WSMan:\localhost\Service\AllowUnencrypted -Value $true
Set-Item WSMan:\localhost\Service\Auth\Basic -Value $true

# 3. Configure trusted hosts (allow any for testing)
Set-Item WSMan:\localhost\Client\TrustedHosts -Value "*" -Force

# 4. Create HTTP listener if not exists
$listeners = Get-WSManInstance -ResourceURI winrm/config/listener -Enumerate
if (-not ($listeners | Where-Object { $_.Transport -eq "HTTP" })) {
    New-WSManInstance -ResourceURI winrm/config/listener -SelectorSet @{Address="*";Transport="HTTP"}
}

# 5. Open firewall
New-NetFirewallRule -Name "WinRM-HTTP-In" -DisplayName "WinRM HTTP Inbound" `
    -Enabled True -Direction Inbound -Protocol TCP -LocalPort 5985 -Action Allow -ErrorAction SilentlyContinue

# 6. Restart WinRM
Restart-Service WinRM

# 7. Verify
Write-Host "`n=== WinRM Configuration ===" -ForegroundColor Green
winrm get winrm/config/service
Write-Host "`n=== Listeners ===" -ForegroundColor Green
winrm enumerate winrm/config/listener
Write-Host "`n=== Test Connection ===" -ForegroundColor Green
Test-WSMan -ComputerName localhost
```

---

## Test Connection from macOS/Linux

### Install pywinrm

```bash
cd /Users/dad/Documents/Msp_Flakes/packages/compliance-agent
source venv/bin/activate
pip install pywinrm
```

### Quick Connection Test

```bash
python3 << 'EOF'
import winrm

session = winrm.Session(
    'http://192.168.56.10:5985/wsman',
    auth=('Administrator', 'YOUR_PASSWORD'),
    transport='ntlm'
)

result = session.run_ps('$env:COMPUTERNAME')
print(f"Connected to: {result.std_out.decode().strip()}")
print(f"Status: {result.status_code}")
EOF
```

---

## Run Compliance Agent Tests

```bash
cd /Users/dad/Documents/Msp_Flakes/packages/compliance-agent
source venv/bin/activate

# Set environment variables
export WIN_TEST_HOST="192.168.56.10"
export WIN_TEST_USER="Administrator"
export WIN_TEST_PASS="YOUR_PASSWORD"

# Run manual test
python tests/test_windows_integration.py
```

---

## Troubleshooting

### "Connection refused"
- Check VM firewall: `Get-NetFirewallRule -Name "WinRM*"`
- Check WinRM service: `Get-Service WinRM`
- Check listener: `winrm enumerate winrm/config/listener`

### "Access denied"
- Verify username/password
- Check authentication: `Get-Item WSMan:\localhost\Service\Auth\*`
- Try NTLM transport instead of basic

### "Network unreachable"
- Verify host-only network: `ping 192.168.56.10`
- Check VM network adapter settings
- Verify IP assignment in VM: `ipconfig`

### WinRM not responding
```powershell
# Reset WinRM to defaults
winrm invoke Restore winrm/config @{}
# Then re-run setup script
```

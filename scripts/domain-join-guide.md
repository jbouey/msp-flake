# Windows Server Domain Join Guide

## Quick Start

If you have a Windows Server VM that needs to join the `northvalley.local` domain, follow these steps:

### Prerequisites

- Windows Server VM is running
- VM can reach the Domain Controller at `192.168.88.250`
- You have local Administrator access to the Windows Server
- Domain Controller (NVDC01) is running

### Domain Information

| Property | Value |
|----------|-------|
| **Domain FQDN** | `northvalley.local` |
| **Domain NetBIOS** | `NORTHVALLEY` |
| **Domain Controller** | `NVDC01.northvalley.local` |
| **DC IP Address** | `192.168.88.250` |
| **Domain Admin** | `NORTHVALLEY\Administrator` |
| **Domain Admin Password** | `NorthValley2024!` |

---

## Method 1: PowerShell Script (Recommended)

### Step 1: Copy the Script to Windows Server

From your Mac, you can copy the script to the Windows Server via RDP or network share:

```bash
# If you have RDP access, copy the script
scp scripts/join-windows-server-to-domain.ps1 Administrator@<WINDOWS_SERVER_IP>:C:\temp\
```

Or manually create the script on the Windows Server.

### Step 2: Run the Script

On the Windows Server, open **PowerShell as Administrator** and run:

```powershell
# Navigate to script location
cd C:\temp

# Run the script
.\join-windows-server-to-domain.ps1
```

The script will:
1. Configure DNS to point to the Domain Controller
2. Test connectivity
3. Join the domain
4. Restart the computer

### Step 3: Verify Domain Join

After restart, log in with domain credentials:
- Username: `NORTHVALLEY\Administrator`
- Password: `NorthValley2024!`

Then verify:
```powershell
# Check domain membership
(Get-WmiObject Win32_ComputerSystem).Domain
# Should return: northvalley.local

# Check secure channel
Test-ComputerSecureChannel
# Should return: True
```

---

## Method 2: Manual Domain Join

### Step 1: Configure DNS

Open PowerShell as Administrator:

```powershell
# Get network adapters
Get-NetAdapter | Where-Object { $_.Status -eq "Up" }

# Set DNS to Domain Controller (replace "Ethernet" with your adapter name)
Set-DnsClientServerAddress -InterfaceAlias "Ethernet" -ServerAddresses "192.168.88.250"

# Verify DNS
Get-DnsClientServerAddress -InterfaceAlias "Ethernet"
```

### Step 2: Test Connectivity

```powershell
# Ping Domain Controller
Test-Connection -ComputerName 192.168.88.250 -Count 2

# Test DNS resolution
Resolve-DnsName -Name NVDC01.northvalley.local
```

### Step 3: Join Domain

```powershell
# Create credential object
$DomainAdmin = "NORTHVALLEY\Administrator"
$Password = ConvertTo-SecureString "NorthValley2024!" -AsPlainText -Force
$Credential = New-Object System.Management.Automation.PSCredential($DomainAdmin, $Password)

# Join domain
Add-Computer -DomainName "northvalley.local" -Credential $Credential -Restart
```

The computer will restart automatically.

---

## Method 3: Via WinRM (Remote)

If WinRM is already configured on the Windows Server, you can join it remotely:

### From Mac/Linux:

```python
import winrm

# Connect to Windows Server (replace with actual IP)
session = winrm.Session(
    'http://<WINDOWS_SERVER_IP>:5985/wsman',
    auth=('Administrator', '<LOCAL_ADMIN_PASSWORD>'),
    transport='ntlm'
)

# Run domain join script
ps_script = '''
$DomainAdmin = "NORTHVALLEY\Administrator"
$Password = ConvertTo-SecureString "NorthValley2024!" -AsPlainText -Force
$Credential = New-Object System.Management.Automation.PSCredential($DomainAdmin, $Password)

# Configure DNS
$Adapter = Get-NetAdapter | Where-Object { $_.Status -eq "Up" } | Select-Object -First 1
Set-DnsClientServerAddress -InterfaceIndex $Adapter.ifIndex -ServerAddresses "192.168.88.250"

# Join domain
Add-Computer -DomainName "northvalley.local" -Credential $Credential -Restart
'''

result = session.run_ps(ps_script)
print(result.std_out.decode())
print(result.std_err.decode())
```

---

## Troubleshooting

### Cannot Reach Domain Controller

**Symptom:** `Add-Computer` fails with "The specified domain either does not exist or could not be contacted"

**Solutions:**
1. Verify DC is running:
   ```bash
   ping 192.168.88.250
   ```

2. Check DNS configuration:
   ```powershell
   Get-DnsClientServerAddress
   # Should show 192.168.88.250
   ```

3. Test DNS resolution:
   ```powershell
   Resolve-DnsName -Name NVDC01.northvalley.local
   ```

4. Check firewall on Windows Server:
   ```powershell
   # Allow domain join traffic
   New-NetFirewallRule -Name "DomainJoin" `
       -DisplayName "Domain Join Traffic" `
       -Enabled True `
       -Direction Inbound `
       -Protocol TCP `
       -RemoteAddress 192.168.88.250 `
       -Action Allow
   ```

### Invalid Credentials

**Symptom:** "The user name or password is incorrect"

**Solutions:**
1. Verify domain admin credentials:
   - Username: `NORTHVALLEY\Administrator`
   - Password: `NorthValley2024!`

2. Test credentials on DC:
   ```powershell
   # From Windows Server, test authentication
   Test-ComputerSecureChannel -Server NVDC01.northvalley.local -Credential $Credential
   ```

### Computer Name Conflict

**Symptom:** "The computer account could not be created because the name is already in use"

**Solutions:**
1. Use a different computer name:
   ```powershell
   Add-Computer -DomainName "northvalley.local" `
       -Credential $Credential `
       -NewName "NEW-SERVER-NAME" `
       -Restart
   ```

2. Or remove old computer account from AD (requires DC access):
   ```powershell
   # On Domain Controller
   Remove-ADComputer -Identity "OLD-COMPUTER-NAME" -Confirm:$false
   ```

### Secure Channel Broken After Join

**Symptom:** After joining, `Test-ComputerSecureChannel` returns `False`

**Solution:**
```powershell
# Repair secure channel
Test-ComputerSecureChannel -Repair -Credential $Credential
```

---

## Post-Join Configuration

### Enable WinRM (if not already enabled)

```powershell
Enable-PSRemoting -Force -SkipNetworkProfileCheck
Set-Item WSMan:\localhost\Service\AllowUnencrypted -Value $true
Set-Item WSMan:\localhost\Service\Auth\Basic -Value $true
New-NetFirewallRule -Name "WinRM-HTTP-In" `
    -DisplayName "WinRM HTTP Inbound" `
    -Enabled True `
    -Direction Inbound `
    -Protocol TCP `
    -LocalPort 5985 `
    -Action Allow
Restart-Service WinRM
```

### Verify Domain Membership

```powershell
# Check domain
(Get-WmiObject Win32_ComputerSystem).Domain
# Should return: northvalley.local

# Check secure channel
Test-ComputerSecureChannel
# Should return: True

# Check domain controller
[System.DirectoryServices.ActiveDirectory.Domain]::GetCurrentDomain().DomainControllers[0].Name
# Should return: NVDC01.northvalley.local
```

---

## Accessing the Domain-Joined Server

### Via RDP

- **Username:** `NORTHVALLEY\Administrator` or `NORTHVALLEY\<username>`
- **Password:** Domain password
- **Computer:** `<server-ip>` or `<server-name>.northvalley.local`

### Via WinRM

```python
import winrm

session = winrm.Session(
    'http://<server-ip>:5985/wsman',
    auth=('NORTHVALLEY\\Administrator', 'NorthValley2024!'),
    transport='ntlm'
)

result = session.run_ps('hostname')
print(result.std_out.decode())
```

---

## Related Documentation

- **Network Topology:** `.agent/NETWORK.md`
- **Lab Credentials:** `.agent/LAB_CREDENTIALS.md`
- **Windows Test Setup:** `packages/compliance-agent/docs/WINDOWS_TEST_SETUP.md`

---

**Last Updated:** 2026-01-09

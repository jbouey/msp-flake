# WS01 Manual Fix — 2 Minutes

WS01 is at the Windows lock screen. WinRM is running but only accepts Kerberos (not NTLM). We need to type 3 commands at the console.

## Steps

### 1. Wake the screen
- Open the VirtualBox window for **northvalley-ws01** on the iMac
- Click inside the VM window, press any key or move mouse to wake from lock screen
- It should auto-logon to the desktop (GPO auto-logon is set). If it asks for a password: `NorthValley2024!`

### 2. Open PowerShell as Admin
- Right-click the Start button (bottom-left) → **Windows PowerShell (Admin)**
- Or press `Win+X` then `A`

### 3. Paste these 4 commands (one at a time)

```
winrm set winrm/config/service @{AllowUnencrypted="true"}
```

```
winrm set winrm/config/service/auth @{Basic="true"}
```

```
Set-Item WSMan:\localhost\Client\TrustedHosts '*' -Force
```

```
Restart-Service WinRM
```

### 4. Install Guest Additions
- In VirtualBox menu bar: **Devices → Insert Guest Additions CD image**
  (it may already be mounted — if so skip this)
- In the PowerShell window, paste:

```
D:\VBoxWindowsAdditions.exe /S
```

- Wait ~60 seconds for it to finish. If it says reboot needed, type:

```
Restart-Computer -Force
```

### 5. Done

After this, WinRM will accept NTLM from the appliance and Guest Additions will be installed. The daemon will auto-discover and deploy to WS01 on its next cycle.

---

**That's it — 4 commands + 1 installer. Should take 2 minutes.**

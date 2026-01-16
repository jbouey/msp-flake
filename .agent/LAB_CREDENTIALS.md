# Lab Credentials

**Last Updated:** 2026-01-02
**Environment:** North Valley Clinic Test Lab + Physical Appliance

> **WARNING:** These are LAB/TEST credentials only. Never use in production.

---

## North Valley Clinic Lab (192.168.88.x)

### Infrastructure Access

| System | Host | IP | Username | Password |
|--------|------|-----|----------|----------|
| iMac Lab Host | - | 192.168.88.50 | jrelly | (SSH key) |
| Windows DC | NVDC01 | 192.168.88.250 | NORTHVALLEY\Administrator | NorthValley2024! |
| Windows DC (local) | NVDC01 | 192.168.88.250 | .\Administrator | NorthValley2024! |
| Windows DC (DSRM) | NVDC01 | 192.168.88.250 | (Safe Mode) | NorthValley2024! |
| Windows Workstation | NVWS01 | 192.168.88.251 | localadmin | NorthValley2024! |
| Windows Workstation | NVWS01 | 192.168.88.251 | NORTHVALLEY\adminit | ClinicAdmin2024! |

### Domain Information

| Property | Value |
|----------|-------|
| Domain FQDN | northvalley.local |
| NetBIOS Name | NORTHVALLEY |
| Domain Controller | NVDC01.northvalley.local |
| DC IP | 192.168.88.250 |
| DNS Server | 192.168.88.250 |

### AD Domain Users

| Name | Username | Password | Role | Groups |
|------|----------|----------|------|--------|
| Dr. Sarah Smith | ssmith | ClinicUser2024! | Provider | Providers, PHI-Access |
| Dr. Michael Chen | mchen | ClinicUser2024! | Provider | Providers, PHI-Access |
| Lisa Johnson | ljohnson | ClinicUser2024! | Nurse | Nurses, PHI-Access |
| Maria Garcia | mgarcia | ClinicUser2024! | Front Desk | FrontDesk |
| Tom Wilson | twilson | ClinicUser2024! | Billing | Billing |
| Admin IT | adminit | ClinicAdmin2024! | IT Admin | IT-Admins, PHI-Access, Audit-Reviewers |

### AD Service Accounts

| Name | Username | Password | Purpose |
|------|----------|----------|---------|
| SVC Backup | svc.backup | SvcAccount2024! | Backup operations |
| SVC Monitoring | svc.monitoring | SvcAccount2024! | Compliance agent |

### AD Security Groups

| Group | Purpose |
|-------|---------|
| Providers | Doctors and NPs |
| Nurses | Nursing staff |
| FrontDesk | Reception/scheduling |
| Billing | Billing department |
| IT-Admins | IT administrators |
| PHI-Access | Access to patient data |
| Audit-Reviewers | Compliance audit access |

---

## SMB Shares

| Share | Path | Access |
|-------|------|--------|
| PatientFiles | C:\Shares\PatientFiles | PHI-Access group |
| ClinicDocs | C:\Shares\ClinicDocs | All staff |
| Backups$ | C:\Shares\Backups | Hidden, svc.backup only |
| Scans | C:\Shares\Scans | All staff |
| Templates | C:\Shares\Templates | All staff (read) |

**Access from workstation:**
```
\\NVDC01\PatientFiles
\\NVDC01\ClinicDocs
\\192.168.88.250\Scans
```

---

## Appliances

### Physical Appliance (HP T640)

| Property | Value |
|----------|-------|
| Hostname | osiriscare-appliance |
| mDNS | osiriscare-appliance.local |
| IP | 192.168.88.246 |
| MAC | 84:3A:5B:91:B6:61 |
| SSH | root@192.168.88.246 (SSH key) |
| Site ID | physical-appliance-pilot-1aea78 |
| API Key | q5VihYAYhKMH-vtX-DXuzLrjqbhgM61S5KjgPM4UG4A |

### Lab Appliance (VirtualBox VM)

| Property | Value |
|----------|-------|
| Hostname | osiriscare-appliance |
| IP | 192.168.88.247 |
| MAC | 08:00:27:98:FD:84 |
| SSH | root@192.168.88.247 (SSH key) |
| Site ID | test-appliance-lab-b3c40c |
| API Key | n0hGyslQnVlXg6YHbDoWDk3bVqU9GzNvADTBG3M1WME |
| Config | /var/lib/msp/config.yaml |

---

## API Keys (CANONICAL SOURCE)

> **IMPORTANT:** Always use these keys. Do NOT copy keys from other sources.

| Key | Value | Purpose |
|-----|-------|---------|
| **Anthropic API Key (L2 LLM)** | `(see 1Password - Anthropic L2 Key)` | L2 healing on appliances |
| Physical Appliance Site API | `q5VihYAYhKMH-vtX-DXuzLrjqbhgM61S5KjgPM4UG4A` | Central Command auth |
| Lab Appliance Site API | `n0hGyslQnVlXg6YHbDoWDk3bVqU9GzNvADTBG3M1WME` | Central Command auth |

### Appliance Config Template

```yaml
# /var/lib/msp/config.yaml
site_id: <site-id>
api_key: <site-api-key>
api_endpoint: https://api.osiriscare.net
healing_dry_run: false
l2_enabled: true
l2_api_key: <ANTHROPIC_API_KEY_FROM_1PASSWORD>
l2_api_provider: anthropic
l2_api_model: claude-3-5-haiku-latest
```

---

## Chaos Lab (iMac)

| Property | Value |
|----------|-------|
| Location | /Users/jrelly/chaos-lab |
| Config | /Users/jrelly/chaos-lab/config.env |
| VM Name | northvalley-dc |
| Snapshot | pre-chaos-clean |
| Target DC | 192.168.88.250 (NORTHVALLEY\Administrator) |

**Crontab Schedule:**
- 20:00 - generate_and_plan.py (creates tomorrow's campaigns)
- 06:00 - EXECUTION_PLAN.sh (runs attacks, no LLM)
- 12:00 - mid_day_checkpoint.py (assesses progress)
- 18:00 - end_of_day_report.py (analysis + Slack)

---

## Production Central Command

| System | URL/Host | Username | Password |
|--------|----------|----------|----------|
| Dashboard | https://dashboard.osiriscare.net | admin | Admin123 |
| Dashboard | https://dashboard.osiriscare.net | operator | operator |
| API | https://api.osiriscare.net | (API key auth) | - |
| VPS SSH | root@178.156.162.116 | root | (SSH key) |

### Registered Sites

| Site ID | Clinic Name | API Key | Status |
|---------|-------------|---------|--------|
| physical-appliance-pilot-1aea78 | Physical Appliance Pilot | q5VihYAYhKMH-vtX-DXuzLrjqbhgM61S5KjgPM4UG4A | online |
| test-appliance-lab-b3c40c | Test Appliance Lab | (in config.yaml on VM) | online |

### Ed25519 Signing Key

| Property | Value |
|----------|-------|
| Location | `/opt/mcp-server/secrets/signing.key` (on VPS) |
| Public Key | `904b211dba3786764c3a3ab3723db8640295f390c196b8f3bc47ae0a47a0b0db` |
| Endpoint | `GET https://api.osiriscare.net/api/evidence/public-key` |

---

## WinRM Connection Examples

### PowerShell (from Windows)
```powershell
$cred = Get-Credential  # NORTHVALLEY\Administrator
Enter-PSSession -ComputerName 192.168.88.250 -Credential $cred
```

### Python (from Mac/Linux)
```python
import winrm

# Connect to DC
s = winrm.Session('http://192.168.88.250:5985/wsman',
                  auth=('NORTHVALLEY\\Administrator', 'NorthValley2024!'),
                  transport='ntlm')
result = s.run_ps('hostname')
print(result.std_out.decode())

# Connect as domain user
s = winrm.Session('http://192.168.88.250:5985/wsman',
                  auth=('NORTHVALLEY\\ssmith', 'ClinicUser2024!'),
                  transport='ntlm')
```

---

## SSH Quick Access

```bash
# iMac lab host
ssh jrelly@192.168.88.50

# Check running VMs
ssh jrelly@192.168.88.50 '/Applications/VirtualBox.app/Contents/MacOS/VBoxManage list runningvms'

# Start VMs
ssh jrelly@192.168.88.50 '/Applications/VirtualBox.app/Contents/MacOS/VBoxManage startvm "northvalley-dc" --type headless'
ssh jrelly@192.168.88.50 '/Applications/VirtualBox.app/Contents/MacOS/VBoxManage startvm "northvalley-ws01" --type headless'

# Stop VMs gracefully
ssh jrelly@192.168.88.50 '/Applications/VirtualBox.app/Contents/MacOS/VBoxManage controlvm "northvalley-dc" acpipowerbutton'
```

---

## Password Policy (Domain)

| Setting | Value |
|---------|-------|
| Minimum Length | 12 characters |
| Password History | 24 passwords |
| Maximum Age | 90 days |
| Complexity | Required |
| Lockout Threshold | 5 attempts |
| Lockout Duration | 30 minutes |

---

## Network Summary

```
192.168.88.0/24 - North Valley Lab Network
├── 192.168.88.1   - Gateway/Router
├── 192.168.88.50  - iMac (VirtualBox host)
├── 192.168.88.244 - NVSRV01 (Windows Server 2022 Core, domain member)
├── 192.168.88.246 - HP T640 Physical Appliance (physical-appliance-pilot-1aea78)
├── 192.168.88.247 - Lab Appliance VM (test-appliance-lab-b3c40c)
├── 192.168.88.250 - NVDC01 (Windows Server 2019 DC)
└── 192.168.88.251 - NVWS01 (Windows 10 Workstation)

Production:
└── 178.156.162.116 - Hetzner VPS (api/dashboard/msp.osiriscare.net)
```

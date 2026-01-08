# MSP Compliance Platform - Infrastructure Inventory

**Last Updated:** 2026-01-05 (Session 11 - ISO v18 Deployed)
**Status:** Production + North Valley Lab

---

## Production Environment

### Central Command (Hetzner VPS)
| Property | Value |
|----------|-------|
| **IP** | 178.156.162.116 |
| **SSH** | `ssh root@178.156.162.116` |
| **API** | https://api.osiriscare.net |
| **Dashboard** | https://dashboard.osiriscare.net |
| **Portal** | https://msp.osiriscare.net |
| **Stack** | Docker: FastAPI + PostgreSQL + Redis + MinIO |

### Physical Appliance (HP T640)
| Property | Value |
|----------|-------|
| **Site ID** | physical-appliance-pilot-1aea78 |
| **Name** | North Valley Dental |
| **IP** | 192.168.88.246 |
| **MAC** | 84:3A:5B:91:B6:61 |
| **ISO Version** | v18 (with healing fixes) |
| **Agent Version** | 1.0.9 |
| **Check-in** | Every 60s |
| **Evidence Bundles** | 17,000+ |

```bash
# SSH access requires key setup after ISO flash
# Via iMac gateway:
ssh jrelly@192.168.88.50 "ssh root@192.168.88.246"
```

### iMac Gateway (Lab Network Access)
| Property | Value |
|----------|-------|
| **IP** | 192.168.88.50 |
| **User** | jrelly (SSH key auth) |
| **Role** | VirtualBox host for North Valley lab |

---

## North Valley Lab (Pilot Client)

### Windows Server 2019 DC (NVDC01)
| Property | Value |
|----------|-------|
| **Hostname** | NVDC01 |
| **Domain** | northvalley.local |
| **IP** | 192.168.88.250 |
| **WinRM Port** | 5985 |
| **Credentials** | *via credential-pull from Central Command* |
| **VM Host** | iMac VirtualBox |

**Domain Users:**
| User | Role | Password |
|------|------|----------|
| NORTHVALLEY\\Administrator | Domain Admin | *stored in site_credentials* |
| NORTHVALLEY\\adminit | IT Admin | ClinicAdmin2024! |
| NORTHVALLEY\\ssmith | Provider | ClinicUser2024! |

### Windows 10 Workstation (NVWS01)
| Property | Value |
|----------|-------|
| **Hostname** | NVWS01 |
| **IP** | 192.168.88.251 |
| **Domain** | northvalley.local |
| **WinRM Port** | 5985 |

---

## Database Stats (Production)

| Table | Count | Notes |
|-------|-------|-------|
| sites | 2 | North Valley Dental, Main Street Medical |
| appliances | 1 | HP T640 physical |
| compliance_bundles | 17,165+ | Ed25519 signed, hash-chained |
| incidents | 1 | Test incident |
| runbooks | 6 | Loaded from DB |
| partners | 3 | Partner infrastructure |
| admin_orders | 12 | Various order types |
| site_credentials | 1 | North Valley DC |

---

## Network Topology

```
                    Internet
                        │
                        ▼
            ┌───────────────────────┐
            │  Hetzner VPS          │
            │  178.156.162.116      │
            │  - Central Command    │
            │  - PostgreSQL         │
            │  - MinIO (WORM)       │
            └───────────────────────┘
                        │
                        │ HTTPS (pull-only)
                        │
        ┌───────────────┼───────────────┐
        │               │               │
        ▼               ▼               ▼
┌─────────────┐ ┌─────────────┐ ┌─────────────┐
│ HP T640     │ │ VirtualBox  │ │ Future      │
│ Appliance   │ │ VM Appliance│ │ Clients     │
│ 192.168.88  │ │ 192.168.88  │ │             │
│ .246        │ │ .247        │ │             │
└─────────────┘ └─────────────┘ └─────────────┘
        │
        │ WinRM (credential-pull)
        ▼
┌─────────────────────────────┐
│ North Valley Lab            │
│ ┌─────────┐  ┌─────────┐   │
│ │ NVDC01  │  │ NVWS01  │   │
│ │ .250    │  │ .251    │   │
│ │ DC/FS   │  │ Win10   │   │
│ └─────────┘  └─────────┘   │
└─────────────────────────────┘
```

---

## Quick Commands

```bash
# VPS
ssh root@178.156.162.116
docker ps  # Check services
docker logs mcp-server -f  # Server logs

# iMac Gateway
ssh jrelly@192.168.88.50
VBoxManage list runningvms

# Start North Valley VMs
ssh jrelly@192.168.88.50 '/Applications/VirtualBox.app/Contents/MacOS/VBoxManage startvm "northvalley-dc" --type headless'

# Physical Appliance (after key setup)
ssh root@192.168.88.246
journalctl -u compliance-agent -f

# WinRM to DC (from appliance - credentials pulled automatically)
# Or manually test:
python3 -c "
import winrm
s = winrm.Session('http://192.168.88.250:5985/wsman',
                  auth=('NORTHVALLEY\\\\Administrator', '...'),
                  transport='ntlm')
print(s.run_ps('hostname').std_out.decode())
"
```

---

## Legacy Development Environment (Deprecated)

> **⚠️ DEPRECATED:** The VirtualBox setup on Mac 174.178.63.139 has been decommissioned.
> All development now uses the VPS and physical appliances.

## Quick Access (from your Mac desktop)

### Compliance Dashboard
```bash
# Create SSH tunnel (run once)
# NOTE: Use VM's host-only IP (192.168.56.103), NOT localhost
# (localhost:8080 on Mac has Jenkins running)
ssh -f -N -L 9080:192.168.56.103:8080 jrelly@174.178.63.139

# Then open in browser:
open http://localhost:9080

# Or test API directly:
curl http://localhost:9080/api/status
```

**Troubleshooting:** If you see Jenkins instead of the compliance dashboard:
1. Kill existing tunnel: `lsof -ti:9080 | xargs kill -9`
2. Ensure tunnel uses `192.168.56.103:8080` (VM IP), not `localhost:8080`
3. Verify firewall rule: `ssh jrelly@174.178.63.139 "ssh root@192.168.56.103 'nft list chain inet nixos-fw input-allow'"` should show port 8080

### SSH Access
```bash
# Compliance Appliance
ssh -p 4444 root@174.178.63.139

# MCP Server
ssh -p 4445 root@174.178.63.139
```

---

## VirtualBox Host
- **Location**: Mac at 174.178.63.139
- **Networks**:
  - NAT Network: `msp-network` (10.0.3.0/24) - Internet access
  - Host-Only Network: `Legacy vboxnet0 Network` (192.168.56.0/24) - VM-to-VM

---

## Virtual Machines

### 1. Compliance Appliance (Central Black Box)
| Property | Value |
|----------|-------|
| **VM Name** | `nixos-24.05.7376.b134951a4c9f-x86_64-linux.ovf` |
| **Hostname** | `test-client-001` |
| **NAT IP** | 10.0.3.5 |
| **Host-Only IP** | 192.168.56.103 |
| **SSH Port** | 4444 (Mac) → 22 (VM) |
| **Web UI** | Port 8080 (internal) |
| **OS** | NixOS 24.05, Kernel 6.6.68 |

**Running Services:**
- `compliance-agent` - Core drift detection & self-healing
- `uvicorn` (web UI) - Dashboard on port 8080
- `windows_collector_daemon` - Scans Windows every 5 minutes

**Web UI Access:**
```bash
# From Mac - create tunnel then browse to localhost:9080
ssh -f -N -L 9080:localhost:8080 -p 4444 root@174.178.63.139
open http://localhost:9080

# Direct on appliance
curl http://localhost:8080/health
```

### 2. MCP Server
| Property | Value |
|----------|-------|
| **VM Name** | `mcp-server` |
| **Hostname** | `mcp-server` |
| **NAT IP** | 10.0.3.4 |
| **SSH Port** | 4445 (Mac) → 22 (VM) |
| **API Port** | 8001 (Mac) → 8000 (VM) |
| **OS** | NixOS 24.05 |

**Role:** LLM-driven automation and runbook execution

### 3. Windows Server (Test Target)
| Property | Value |
|----------|-------|
| **VM Name** | `win-test-vm_default_1763941055603_9826` |
| **Hostname** | `wintest` |
| **Host-Only IP** | 192.168.56.102 |
| **WinRM Port** | 5985 (direct from appliance) |
| **OS** | Windows Server |
| **Credentials** | vagrant / vagrant |

**Role:** Target for compliance monitoring (patches, AV, backups, etc.)

---

## Network Topology

```
┌─────────────────────────────────────────────────────────────────┐
│  Mac Host (174.178.63.139)                                      │
│  Interface: bridge100 = 192.168.56.101                          │
│                                                                 │
│  Port Forwards (NAT):                                           │
│    :4444 → Appliance:22 (SSH)                                   │
│    :4445 → MCP Server:22 (SSH)                                  │
│    :8001 → MCP Server:8000 (API)                                │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Host-Only Network: 192.168.56.0/24                     │   │
│  │  (Direct VM-to-VM communication)                        │   │
│  │                                                         │   │
│  │  ┌─────────────────────┐    ┌─────────────────────┐    │   │
│  │  │ Compliance          │    │ Windows Server      │    │   │
│  │  │ Appliance           │    │                     │    │   │
│  │  │                     │    │                     │    │   │
│  │  │ 192.168.56.103      │───▶│ 192.168.56.102      │    │   │
│  │  │                     │    │                     │    │   │
│  │  │ Web UI :8080        │    │ WinRM :5985         │    │   │
│  │  │ SSH :22             │    │ RDP :3389           │    │   │
│  │  └─────────────────────┘    └─────────────────────┘    │   │
│  │           │                                             │   │
│  │           │ NAT (10.0.3.0/24)                          │   │
│  │           ▼                                             │   │
│  │  ┌─────────────────────┐                               │   │
│  │  │ MCP Server          │                               │   │
│  │  │ 10.0.3.4            │                               │   │
│  │  │ API :8000           │                               │   │
│  │  └─────────────────────┘                               │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Data Flow

```
Your Desktop Browser
        │
        │ http://localhost:9080
        ▼
   SSH Tunnel (port 9080 → appliance:8080)
        │
        ▼
┌───────────────────────────────────────┐
│  Compliance Appliance                 │
│  ┌─────────────────────────────────┐  │
│  │  Web UI (FastAPI/Uvicorn)       │  │
│  │  - Dashboard                    │  │
│  │  - HIPAA Controls               │  │
│  │  - Evidence Browser             │  │
│  └─────────────────────────────────┘  │
│              │                        │
│              ▼                        │
│  ┌─────────────────────────────────┐  │
│  │  Windows Collector Daemon       │  │
│  │  - Runs every 5 minutes         │  │
│  │  - WinRM to 192.168.56.102      │  │
│  │  - Stores results in SQLite     │  │
│  │  - Generates evidence bundles   │  │
│  └─────────────────────────────────┘  │
│              │                        │
│              │ Host-Only Network      │
└──────────────┼────────────────────────┘
               │
               ▼
┌───────────────────────────────────────┐
│  Windows Server (192.168.56.102)      │
│  - Patch status                       │
│  - Windows Defender                   │
│  - Event logging                      │
│  - BitLocker encryption               │
│  - Active Directory                   │
└───────────────────────────────────────┘
```

---

## Current Compliance Status

**Last Check:** Running every 5 minutes

| Check | Status | HIPAA Control |
|-------|--------|---------------|
| Patch Compliance | FAIL | 164.308(a)(5)(ii)(B) |
| Windows Defender | FAIL | 164.308(a)(5)(ii)(B) |
| Backup Verification | FAIL | 164.308(a)(7)(ii)(A) |
| Event Logging | FAIL | 164.312(b) |
| Firewall Status | FAIL | 164.312(a)(1) |
| BitLocker Encryption | PASS | 164.312(a)(2)(iv) |
| Active Directory | PASS | 164.308(a)(3)(ii)(C) |

**Score:** 28.6% (Critical)

---

## Useful Commands

### From Mac Desktop
```bash
# Check if tunnel exists
lsof -i :9080

# Create tunnel if needed
ssh -f -N -L 9080:localhost:8080 -p 4444 root@174.178.63.139

# SSH to appliance
ssh -p 4444 root@174.178.63.139

# Check appliance services
ssh -p 4444 root@174.178.63.139 "systemctl status compliance-agent"
```

### From Compliance Appliance
```bash
# Check web UI
curl http://localhost:8080/health

# Trigger Windows scan manually
curl -X POST http://localhost:8080/api/windows/collect

# View collector logs
tail -f /var/log/windows_collector.log

# Check latest Windows results
cat /var/lib/msp-compliance-agent/windows_latest.json | python3 -m json.tool

# View incidents database
sqlite3 /var/lib/msp-compliance-agent/incidents.db "SELECT * FROM incidents ORDER BY created_at DESC LIMIT 5;"

# List evidence bundles
ls -la /var/lib/msp-compliance-agent/evidence/
```

### From Windows Server
```powershell
# Check WinRM is running
Get-Service WinRM

# Test WinRM locally
winrm enumerate winrm/config/listener

# Check firewall status
Get-NetFirewallProfile | Select Name, Enabled
```

---

## Troubleshooting

### Can't access web UI?
1. Check tunnel: `lsof -i :9080`
2. Recreate: `ssh -f -N -L 9080:localhost:8080 -p 4444 root@174.178.63.139`
3. Check uvicorn: `ssh -p 4444 root@174.178.63.139 "ps aux | grep uvicorn"`

### Windows collection failing?
1. Check connectivity: `ssh -p 4444 root@174.178.63.139 "ping -c 2 192.168.56.102"`
2. Check WinRM: `ssh -p 4444 root@174.178.63.139 "nc -zv 192.168.56.102 5985"`
3. Check collector log: `ssh -p 4444 root@174.178.63.139 "tail -50 /var/log/windows_collector.log"`

**Known Issue (2025-11-28):** Windows Firewall blocks VM-to-VM traffic by default. WinRM works from Mac host (192.168.56.1) but times out from NixOS appliance (192.168.56.103). See `WINDOWS_TEST_SETUP.md` section "Windows Firewall Blocking VM-to-VM Traffic" for fix.

### VMs not starting?
```bash
# On Mac - list VMs
VBoxManage list vms

# Start specific VM
VBoxManage startvm "nixos-24.05..." --type headless
VBoxManage startvm "win-test-vm..." --type headless
```

---

**Last Updated:** 2025-11-28

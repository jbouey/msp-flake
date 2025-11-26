# MSP Compliance Platform - VM Inventory

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

### VMs not starting?
```bash
# On Mac - list VMs
VBoxManage list vms

# Start specific VM
VBoxManage startvm "nixos-24.05..." --type headless
VBoxManage startvm "win-test-vm..." --type headless
```

---

**Last Updated:** 2025-11-24

# Network Topology & VM Inventory

**Last Updated:** 2025-12-31
**Environment:** Production + Development/Test Lab

---

## Production Infrastructure

### Central Command (Hetzner VPS)

```
                         ┌─────────────────┐
                         │    INTERNET     │
                         └────────┬────────┘
                                  │
                   ┌──────────────┼──────────────┐
                   │              │              │
        ┌──────────▼──────┐ ┌────▼────┐ ┌───────▼───────┐
        │ api.osiriscare  │ │dashboard│ │msp.osiriscare │
        │      .net       │ │.osiris  │ │    .net       │
        │                 │ │care.net │ │               │
        └─────────────────┘ └─────────┘ └───────────────┘
                   │              │              │
                   └──────────────┼──────────────┘
                                  │ HTTPS (443)
                         ┌────────▼────────┐
                         │  Caddy Reverse  │
                         │     Proxy       │
                         │  (Auto TLS)     │
                         └────────┬────────┘
                   ┌──────────────┼──────────────┐
                   │              │              │
          ┌────────▼────┐  ┌─────▼─────┐  ┌─────▼─────┐
          │ MCP Server  │  │ Dashboard │  │ PostgreSQL│
          │ :8000       │  │ :3000     │  │ Redis     │
          │ FastAPI     │  │ React     │  │ MinIO     │
          └─────────────┘  └───────────┘  └───────────┘

                    Hetzner VPS: 178.156.162.116
```

| Service | Internal | External URL | Purpose |
|---------|----------|--------------|---------|
| MCP API | :8000 | https://api.osiriscare.net | REST API, phone-home |
| Dashboard | :3000 | https://dashboard.osiriscare.net | Central Command UI |
| Dashboard | :3000 | https://msp.osiriscare.net | Alias for dashboard |
| PostgreSQL | :5432 | (internal only) | Database |
| Redis | :6379 | (internal only) | Caching, queues |
| MinIO | :9000-9001 | (internal only) | Evidence storage |

**DNS Records:**
```
api.osiriscare.net       A    178.156.162.116
dashboard.osiriscare.net A    178.156.162.116
msp.osiriscare.net       A    178.156.162.116
```

**SSH Access:**
```bash
ssh root@178.156.162.116
```

**Docker Services:**
```bash
# Check status
ssh root@178.156.162.116 "docker ps"

# View logs
ssh root@178.156.162.116 "docker logs mcp-server"
ssh root@178.156.162.116 "docker logs caddy"
```

---

## Development/Test Lab (Mac Host)

```
                         ┌─────────────────┐
                         │    INTERNET     │
                         └────────┬────────┘
                                  │
                         ┌────────▼────────┐
                         │  Mac Host       │
                         │ 174.178.63.139  │
                         │ (Remote Access) │
                         └────────┬────────┘
                                  │
            ┌─────────────────────┼─────────────────────┐
            │                     │                     │
   ┌────────▼────────┐   ┌───────▼────────┐   ┌───────▼────────┐
   │  NAT Network    │   │ Host-Only Net  │   │  Port Forwards │
   │  10.0.3.0/24    │   │ 192.168.56.0/24│   │  (SSH/WinRM)   │
   │  (Internet)     │   │ (VM-to-VM)     │   │                │
   └────────┬────────┘   └───────┬────────┘   └────────────────┘
            │                    │
   ┌────────▼────────┐   ┌───────▼────────┐
   │  MCP Server     │   │  Compliance    │
   │  10.0.3.4       │   │  Appliance     │
   │  NixOS          │   │  192.168.56.103│
   │  Port 8000      │   │  NixOS         │
   └─────────────────┘   │  Web UI: 8080  │
                         └───────┬────────┘
                                 │ WinRM (5985)
                         ┌───────▼────────┐
                         │  Windows DC    │
                         │  192.168.56.102│
                         │  "wintest"     │
                         │  msp.local     │
                         └────────────────┘
```

---

## VM Inventory

### Compliance Appliance (Primary Work Target)

| Property | Value |
|----------|-------|
| **Role** | Compliance monitoring agent |
| **OS** | NixOS 24.05 |
| **Host-Only IP** | 192.168.56.103 |
| **NAT IP** | 10.0.3.5 (when connected) |
| **SSH Port** | 22 (internal), 4444 (forwarded to Mac) |
| **Web UI** | http://192.168.56.103:8080 |
| **Username** | root |
| **Auth** | SSH key (Mac's ~/.ssh/id_ed25519) |

**Access from Mac:**
```bash
ssh -p 4444 root@174.178.63.139
```

**Access Web UI:**
```bash
ssh -f -N -L 9080:192.168.56.103:8080 jrelly@174.178.63.139
open http://localhost:9080
```

---

### MCP Server

| Property | Value |
|----------|-------|
| **Role** | Central control plane |
| **OS** | NixOS 24.05 |
| **NAT IP** | 10.0.3.4 |
| **SSH Port** | 22 (internal), 4445 (forwarded to Mac) |
| **API Port** | 8000 |
| **Username** | root |
| **Auth** | SSH key |

**Access from Mac:**
```bash
ssh -p 4445 root@174.178.63.139
```

---

### Windows Domain Controller

| Property | Value |
|----------|-------|
| **Role** | Test Windows Server + AD |
| **OS** | Windows Server 2016 |
| **Hostname** | wintest |
| **Domain** | msp.local |
| **Host-Only IP** | 192.168.56.102 |
| **WinRM Port** | 5985 (internal), 55985 (forwarded to Mac) |

**Credentials:**
| Account | Username | Password |
|---------|----------|----------|
| Domain Admin | MSP\vagrant | vagrant |
| Local Admin | .\Administrator | Vagrant123! |

**Access from Mac (WinRM):**
```python
import winrm
s = winrm.Session('http://127.0.0.1:55985/wsman', 
                  auth=('MSP\\vagrant','vagrant'), 
                  transport='ntlm')
print(s.run_ps('whoami').std_out.decode())
```

**Known Issue:** Windows Firewall blocks WinRM from other VMs (192.168.56.0/24). Works from Mac host (192.168.56.1).

**Fix (run on Windows):**
```powershell
New-NetFirewallRule -Name "WinRM_HostOnly" `
  -DisplayName "WinRM from Host-Only Network" `
  -Enabled True -Direction Inbound -Protocol TCP `
  -LocalPort 5985 -RemoteAddress 192.168.56.0/24 -Action Allow
```

---

## Port Forwarding Summary

| Service | Guest Port | Mac Port | Target VM |
|---------|------------|----------|-----------|
| SSH (Appliance) | 22 | 4444 | 192.168.56.103 |
| SSH (MCP) | 22 | 4445 | 10.0.3.4 |
| WinRM | 5985 | 55985 | 192.168.56.102 |
| Web UI | 8080 | 9080 (tunnel) | 192.168.56.103 |

---

## Network Segments

### NAT Network (msp-network)
- **Subnet:** 10.0.3.0/24
- **Gateway:** 10.0.3.1
- **Purpose:** Internet access for VMs
- **Connected:** MCP Server, Appliance (secondary NIC)

### Host-Only Network (vboxnet0)
- **Subnet:** 192.168.56.0/24
- **Host IP:** 192.168.56.1
- **Purpose:** VM-to-VM and host-to-VM communication
- **Connected:** Appliance, Windows DC

---

## Connectivity Matrix

| From → To | Appliance | MCP Server | Windows DC | Mac Host |
|-----------|-----------|------------|------------|----------|
| **Appliance** | - | ✅ (10.0.3.x) | ⚠️ (needs FW fix) | ✅ |
| **MCP Server** | ✅ | - | ❌ (no route) | ✅ |
| **Windows DC** | ⚠️ | ❌ | - | ✅ |
| **Mac Host** | ✅ | ✅ | ✅ | - |

⚠️ = Requires Windows Firewall rule

---

## Quick Diagnostics

**Test Appliance connectivity:**
```bash
# From Mac
ssh -p 4444 root@174.178.63.139 'ping -c 1 192.168.56.102'
```

**Test WinRM from Mac:**
```bash
curl -u 'MSP\vagrant:vagrant' --ntlm \
  http://127.0.0.1:55985/wsman -H "Content-Type: application/soap+xml"
```

**Check VM status (on Mac host):**
```bash
ssh jrelly@174.178.63.139 'VBoxManage list runningvms'
```

---

## Troubleshooting

### "Connection timed out" to Windows from Appliance
1. Windows Firewall blocking - add rule above
2. Wrong network adapter - ensure both VMs on vboxnet0
3. Windows VM not running - check VirtualBox

### Web UI not loading
1. Ensure tunnel is active: `ssh -f -N -L 9080:192.168.56.103:8080 jrelly@174.178.63.139`
2. Check service: `ssh -p 4444 root@174.178.63.139 'systemctl status msp-web-ui'`
3. Don't use localhost:8080 on Mac (Jenkins runs there)

### MCP Server unreachable from Appliance
1. Check NAT network connectivity
2. Verify MCP service: `ssh -p 4445 root@174.178.63.139 'systemctl status msp-server'`

---

## Production API Endpoints

### Central Command API (api.osiriscare.net)

**Base URL:** `https://api.osiriscare.net`

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Health check (status, DB, Redis, MinIO) |
| `/api/sites` | GET | List all sites with status |
| `/api/sites` | POST | Create new site |
| `/api/sites/{site_id}` | GET | Get site details |
| `/api/sites/{site_id}` | PUT | Update site |
| `/api/appliances/checkin` | POST | Appliance phone-home endpoint |
| `/api/sites/{site_id}/credentials` | POST | Store encrypted credentials |
| `/api/webhooks/n8n/intake-received` | POST | n8n webhook for lead intake |
| `/api/dashboard/fleet` | GET | Fleet overview for dashboard |
| `/api/dashboard/incidents` | GET | Active incidents |
| `/api/dashboard/learning` | GET | Learning loop status |

### Appliance Phone-Home

Appliances call the checkin endpoint every 60 seconds:

```bash
curl -X POST https://api.osiriscare.net/api/appliances/checkin \
  -H "Content-Type: application/json" \
  -d '{
    "site_id": "clinic-name-abc123",
    "mac_address": "aa:bb:cc:dd:ee:ff",
    "hostname": "msp-appliance-01",
    "ip_addresses": ["192.168.1.100"],
    "agent_version": "1.0.0",
    "nixos_version": "24.05",
    "uptime_seconds": 86400
  }'
```

**Status Calculation:**
- `online`: Last checkin < 5 minutes ago
- `stale`: Last checkin 5-15 minutes ago
- `offline`: Last checkin > 15 minutes ago
- `pending`: Never checked in

### Sites Management

**Create a new site:**
```bash
curl -X POST https://api.osiriscare.net/api/sites \
  -H "Content-Type: application/json" \
  -d '{
    "clinic_name": "Acme Dental",
    "contact_name": "Dr. Smith",
    "contact_email": "smith@acmedental.com",
    "tier": "mid"
  }'
# Returns: {"site_id": "acme-dental-a1b2c3", ...}
```

**List sites with status filter:**
```bash
curl "https://api.osiriscare.net/api/sites?status=online"
```

**Store encrypted credentials:**
```bash
curl -X POST https://api.osiriscare.net/api/sites/acme-dental-a1b2c3/credentials \
  -H "Content-Type: application/json" \
  -d '{
    "credential_type": "router",
    "credential_name": "Main Router",
    "username": "admin",
    "password": "secret123",
    "host": "192.168.1.1"
  }'
```

---

## Central Command Dashboard

**URL:** https://dashboard.osiriscare.net

### Main Pages

| Page | Path | Purpose |
|------|------|---------|
| Dashboard | `/` | Fleet overview, incidents, stats |
| Sites | `/sites` | All client sites with status |
| Site Detail | `/sites/{site_id}` | Site info, appliances, credentials |
| Onboarding | `/onboarding` | Pipeline with stages |
| Runbooks | `/runbooks` | Runbook library |
| Learning | `/learning` | Data flywheel, pattern promotion |
| Audit Logs | `/audit-logs` | Admin activity log |

### Login Credentials

| Role | Username | Password |
|------|----------|----------|
| Admin | admin | (configured in .env) |
| Operator | operator | (configured in .env) |

---

## Onboarding Pipeline Stages

Sites progress through these stages:

```
Lead → Discovery → Proposal → Contract → Intake → Credentials →
Shipped → Received → Connectivity → Scanning → Baseline → Active
```

**Stage Triggers:**
- `lead`: n8n webhook or manual creation
- `intake_received`: Form submission
- `connectivity`: First appliance checkin
- `scanning`: Network discovery complete
- `baseline`: Baseline enforcement applied
- `active`: Validation period complete

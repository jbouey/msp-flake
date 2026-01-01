# Network Topology & VM Inventory

**Last Updated:** 2026-01-01
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

## Development/Test Lab (Local iMac)

```
                         ┌─────────────────┐
                         │    INTERNET     │
                         └────────┬────────┘
                                  │
                         ┌────────▼────────┐
                         │  Router/Gateway │
                         │ 174.178.63.139  │
                         │ (External IP)   │
                         └────────┬────────┘
                                  │
                         ┌────────▼────────┐
                         │  Local Network  │
                         │ 192.168.88.0/24 │
                         └────────┬────────┘
                                  │
            ┌─────────────────────┼─────────────────────┐
            │                     │                     │
   ┌────────▼────────┐   ┌───────▼────────┐            │
   │  MacBook Pro    │   │  iMac (Lab)    │            │
   │  (Dev Machine)  │   │  192.168.88.50 │            │
   │                 │   │  VirtualBox    │            │
   └─────────────────┘   └───────┬────────┘            │
                                 │                     │
                         ┌───────▼────────┐            │
                         │  NVDC01        │            │
                         │  192.168.88.250│            │
                         │  northvalley   │            │
                         │  .local        │            │
                         └────────────────┘            │
```

### iMac Lab Host

| Property | Value |
|----------|-------|
| **Role** | VirtualBox host for test VMs |
| **IP** | 192.168.88.50 |
| **Username** | jrelly |
| **Auth** | SSH key |

**Access from MacBook:**
```bash
ssh jrelly@192.168.88.50
```

**Check VM status:**
```bash
ssh jrelly@192.168.88.50 'VBoxManage list vms'
ssh jrelly@192.168.88.50 'VBoxManage list runningvms'
```

---

## VM Inventory

### North Valley Clinic - Windows AD DC

| Property | Value |
|----------|-------|
| **VM Name** | northvalley-dc |
| **Role** | Windows Server AD Domain Controller |
| **OS** | Windows Server 2019 Standard |
| **Hostname** | NVDC01 |
| **Domain** | northvalley.local |
| **NetBIOS** | NORTHVALLEY |
| **IP** | 192.168.88.250 |
| **Gateway** | 192.168.88.1 |
| **DNS** | 127.0.0.1, 8.8.8.8 |
| **WinRM** | http://192.168.88.250:5985 |
| **RAM** | 4 GB |
| **CPU** | 2 cores |
| **Disk** | 60 GB |
| **Network** | Bridged (en1: Wi-Fi) |
| **Status** | RUNNING |

**Credentials:**
| Account | Username | Password |
|---------|----------|----------|
| Domain Admin | NORTHVALLEY\Administrator | NorthValley2024! |
| Local Admin | .\Administrator | NorthValley2024! |
| DSRM | (Safe Mode) | NorthValley2024! |

**Access via WinRM (Python):**
```python
import winrm
s = winrm.Session('http://192.168.88.250:5985/wsman',
                  auth=('NORTHVALLEY\\Administrator', 'NorthValley2024!'),
                  transport='ntlm')
result = s.run_ps('hostname')
print(result.std_out.decode())
```

**AD DS Services:**
- DNS: Running (Automatic)
- NTDS: Running (Automatic)
- Netlogon: Running (Automatic)

---

## Network Segments

### Local Network (192.168.88.0/24)
- **Gateway:** 192.168.88.1 (router)
- **iMac Lab Host:** 192.168.88.50
- **NVDC01 (Windows DC):** 192.168.88.250
- **Purpose:** Development/test environment
- **External Access:** Via 174.178.63.139

---

## Quick Commands

**Check VM status:**
```bash
ssh jrelly@192.168.88.50 '/Applications/VirtualBox.app/Contents/MacOS/VBoxManage list runningvms'
```

**Start/Stop VM:**
```bash
# Start
ssh jrelly@192.168.88.50 '/Applications/VirtualBox.app/Contents/MacOS/VBoxManage startvm "northvalley-dc" --type headless'

# Stop gracefully
ssh jrelly@192.168.88.50 '/Applications/VirtualBox.app/Contents/MacOS/VBoxManage controlvm "northvalley-dc" acpipowerbutton'

# Force stop
ssh jrelly@192.168.88.50 '/Applications/VirtualBox.app/Contents/MacOS/VBoxManage controlvm "northvalley-dc" poweroff'
```

**Test WinRM:**
```bash
nc -zv 192.168.88.250 5985
```

**Ping DC:**
```bash
ping 192.168.88.250
```

---

## Central Command Server API & Dashboard

> **NOTE:** The following sections document the production Central Command server
> running on the Hetzner VPS at **178.156.162.116** (msp.osiriscare.net).
> This is NOT part of the local development lab.

---

### Production API Endpoints (api.osiriscare.net)

**Server:** Hetzner VPS 178.156.162.116
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

### Central Command Dashboard (dashboard.osiriscare.net)

**Server:** Hetzner VPS 178.156.162.116
**URL:** https://dashboard.osiriscare.net (alias: https://msp.osiriscare.net)

#### Main Pages

| Page | Path | Purpose |
|------|------|---------|
| Dashboard | `/` | Fleet overview, incidents, stats |
| Sites | `/sites` | All client sites with status |
| Site Detail | `/sites/{site_id}` | Site info, appliances, credentials |
| Onboarding | `/onboarding` | Pipeline with stages |
| Runbooks | `/runbooks` | Runbook library |
| Learning | `/learning` | Data flywheel, pattern promotion |
| Audit Logs | `/audit-logs` | Admin activity log |

#### Login Credentials to msp.osiriscare.net

| Role | Username | Password |
|------|----------|----------|
| Admin | admin | admin |
| Operator | operator | operator |

---

### Onboarding Pipeline Stages

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

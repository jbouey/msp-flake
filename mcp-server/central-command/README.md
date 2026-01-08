# Central Command Dashboard

**Last Updated:** 2026-01-04 (Session 9 - Credential-Pull Architecture)

Web-based dashboard for OsirisCare MSP compliance platform. Provides fleet overview, incident tracking, runbook management, learning loop visibility, partner/reseller infrastructure, and onboarding pipeline.

## Design Language

iOS/Apple glassmorphism: white backgrounds, frosted glass cards, subtle shadows, SF Pro-inspired typography.

## Directory Structure

```
central-command/
├── README.md                     # This file
├── CHANGELOG.md                  # Track all changes
│
├── backend/
│   ├── __init__.py
│   ├── models.py                 # Pydantic models for API
│   ├── metrics.py                # Health/compliance scoring
│   ├── fleet.py                  # Multi-tenant fleet aggregation
│   ├── runbooks.py               # Runbook library management
│   ├── learning.py               # L2→L1 promotion status
│   ├── onboarding.py             # Onboarding pipeline logic
│   ├── chat.py                   # Command interface handler
│   ├── routes.py                 # FastAPI routes
│   ├── partners.py               # Partner/reseller API
│   ├── discovery.py              # Network discovery API
│   ├── provisioning.py           # Appliance provisioning API
│   ├── portal.py                 # Client portal (magic link auth)
│   ├── sites.py                  # Site management
│   └── db_queries.py             # PostgreSQL queries
│
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── tokens/               # Design system
│       ├── components/           # UI components
│       ├── pages/                # Route pages
│       ├── hooks/                # React hooks
│       ├── utils/                # Utilities
│       └── types/                # TypeScript types
│
└── docs/
    ├── metrics-spec.md           # Scoring model documentation
    ├── api-spec.md               # API endpoint docs
    └── design-system.md          # Component documentation
```

## Health Scoring Model

### Overall Health
```
overall = (connectivity.score * 0.4) + (compliance.score * 0.6)
```

### Connectivity Score (40% weight)
- Check-in freshness (0-100 based on last_checkin age)
- Healing success rate (successful_heals / total_incidents * 100)
- Order execution rate (executed_orders / total_orders * 100)

### Compliance Score (60% weight)
- Patching (0 or 100)
- Antivirus (0 or 100)
- Backup (0 or 100)
- Logging (0 or 100)
- Firewall (0 or 100)
- Encryption (0 or 100)

### Health Thresholds
| Score | Color | Label |
|-------|-------|-------|
| 0-39 | Red | Critical |
| 40-79 | Yellow | Warning |
| 80-100 | Green | Healthy |

## API Endpoints

### Fleet
- `GET /api/dashboard/fleet` - All clients with health scores
- `GET /api/dashboard/fleet/{site_id}` - Client detail
- `GET /api/dashboard/fleet/{site_id}/appliances` - Client appliances

### Incidents
- `GET /api/dashboard/incidents` - Recent incidents
- `GET /api/dashboard/incidents/{incident_id}` - Incident detail

### Runbooks
- `GET /api/dashboard/runbooks` - All runbooks
- `GET /api/dashboard/runbooks/{runbook_id}` - Runbook detail
- `GET /api/dashboard/runbooks/{runbook_id}/executions` - Execution history

### Learning Loop
- `GET /api/dashboard/learning/status` - Learning loop status
- `GET /api/dashboard/learning/candidates` - Promotion candidates
- `GET /api/dashboard/learning/history` - Promotion history
- `POST /api/dashboard/learning/promote/{pattern_id}` - Promote pattern

### Onboarding
- `GET /api/dashboard/onboarding` - Pipeline overview
- `GET /api/dashboard/onboarding/metrics` - Pipeline metrics
- `GET /api/dashboard/onboarding/{client_id}` - Client detail
- `POST /api/dashboard/onboarding` - Create prospect
- `PATCH /api/dashboard/onboarding/{client_id}/stage` - Advance stage

### Stats & Command
- `GET /api/dashboard/stats` - Global statistics
- `POST /api/dashboard/command` - Command interface

### Partner/Reseller (X-API-Key auth)
- `GET /api/partners/me` - Current partner info
- `GET /api/partners/me/sites` - Partner's sites
- `GET /api/partners/me/provisions` - List provision codes
- `POST /api/partners/me/provisions` - Create provision code
- `GET /api/partners/me/provisions/{id}/qr` - Generate QR code image
- `POST /api/partners/me/sites/{site_id}/credentials` - Add site credentials
- `POST /api/partners/me/sites/{site_id}/discovery/trigger` - Trigger network scan

### Discovery
- `POST /api/discovery/report` - Receive discovery results from appliance
- `POST /api/discovery/status` - Update scan status
- `GET /api/discovery/pending/{site_id}` - Get pending scans
- `GET /api/discovery/assets/{site_id}/summary` - Asset summary

### Provisioning
- `POST /api/provision/claim` - Claim provision code
- `GET /api/provision/validate/{code}` - Validate code
- `POST /api/provision/status` - Update provisioning progress
- `POST /api/provision/heartbeat` - Heartbeat from appliance
- `GET /api/provision/config/{appliance_id}` - Get appliance config

### Client Portal
- `GET /api/portal/auth/validate` - Validate magic link token
- `GET /api/portal/site/{site_id}` - Get site data for portal
- `GET /api/portal/site/{site_id}/compliance` - Compliance status

### Appliance Check-in (Credential-Pull)
- `POST /api/appliances/checkin` - Phone-home with credential-pull

The check-in endpoint implements **RMM-style credential-pull** (like Datto, ConnectWise, NinjaRMM). Appliances receive Windows target credentials on each check-in cycle, eliminating local credential storage.

**Request:**
```json
{
  "site_id": "physical-appliance-pilot-1aea78",
  "hostname": "osiriscare-appliance",
  "mac_address": "84:3A:5B:91:B6:61",
  "ip_addresses": ["192.168.88.246"],
  "uptime_seconds": 3600,
  "agent_version": "1.0.8",
  "nixos_version": "24.05"
}
```

**Response:**
```json
{
  "status": "ok",
  "appliance_id": "physical-appliance-pilot-1aea78-84:3A:5B:91:B6:61",
  "server_time": "2026-01-04T12:00:00Z",
  "windows_targets": [
    {
      "hostname": "192.168.88.250",
      "username": "NORTHVALLEY\\Administrator",
      "password": "...",
      "use_ssl": false
    }
  ]
}
```

**Credential-Pull Benefits:**
- **No local credential storage** - Credentials never touch disk on appliance
- **Automatic rotation** - Credential changes propagate in ~60s (next check-in)
- **Stolen device safety** - Compromised appliance doesn't expose credentials
- **Consistent with industry pattern** - Same model as enterprise RMM tools

**Credentials Source:** `site_credentials` table (types: `winrm`, `domain_admin`, `service_account`, `local_admin`)

## Production Deployment

**IMPORTANT:** The VPS has two separate code locations. Only `/opt/mcp-server/` is production:

| Path | Purpose | Docker Mount |
|------|---------|--------------|
| `/opt/mcp-server/` | **PRODUCTION** - Docker container mounts this | Yes - volume mount |
| `/root/msp-iso-build/mcp-server/` | Build source - NOT used at runtime | No |

### Production File Structure (VPS)
```
/opt/mcp-server/
├── docker-compose.yml          # Production docker-compose
├── Caddyfile                   # Reverse proxy config
├── .env                        # Environment variables
├── app/
│   ├── main.py                 # Main FastAPI app (container entrypoint)
│   └── dashboard_api/          # API routes (mapped from central-command/backend)
│       ├── routes.py
│       ├── models.py
│       ├── sites.py
│       ├── portal.py
│       ├── partners.py
│       ├── discovery.py
│       ├── provisioning.py
│       └── ...
├── agent-packages/             # Agent tarballs for OTA updates
├── runbooks/                   # YAML runbooks
└── secrets/                    # Signing keys
```

### Syncing Backend Changes
```bash
# From local machine - sync to /opt/mcp-server/app/dashboard_api/
rsync -avz mcp-server/central-command/backend/*.py root@VPS:/opt/mcp-server/app/dashboard_api/

# Fix permissions (container runs as uid 1000)
ssh root@VPS "chmod 644 /opt/mcp-server/app/dashboard_api/*.py && chown -R 1000:1000 /opt/mcp-server/app/dashboard_api/"

# Restart container
ssh root@VPS "cd /opt/mcp-server && docker-compose restart mcp-server"
```

### Common Mistakes
- **DO NOT** sync to `/root/msp-iso-build/` - that's for ISO builds only
- **DO NOT** create .bak files in production - they cause import errors
- **ALWAYS** fix permissions after rsync (container runs as non-root)

## Development

```bash
# Backend (runs with existing MCP server)
cd mcp-server
python server.py

# Frontend
cd central-command/frontend
npm install
npm run dev
```

## Build Phases

1. Backend Foundation - Models, metrics, routes
2. Frontend Foundation - React + Vite + Tailwind + design tokens
3. Fleet Dashboard - Client grid with health gauges
4. Runbook Library - HIPAA mappings, execution stats
5. Learning Loop Dashboard - L2→L1 promotion UI
6. Command Bar - Natural language interface
7. Client Detail & Polish - Deep dive views
8. Onboarding Pipeline - Prospect tracking

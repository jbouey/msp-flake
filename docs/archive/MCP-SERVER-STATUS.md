# MCP Server Status

**Last Updated:** 2026-01-02
**Server Location:** Hetzner VPS (178.156.162.116)
**API Endpoint:** https://api.osiriscare.net

---

## Current Status

| Component | Status | Notes |
|-----------|--------|-------|
| MCP Server | Running | Docker container `mcp-server` |
| PostgreSQL | Running | 16-alpine, persistent volume |
| Redis | Running | Caching and queues |
| MinIO | Running | WORM evidence storage |
| Caddy | Running | Auto-TLS for all domains |
| Frontend | Running | React dashboard |

---

## Production URLs

| Service | URL | Purpose |
|---------|-----|---------|
| API | https://api.osiriscare.net | REST API, phone-home |
| Dashboard | https://dashboard.osiriscare.net | Central Command UI |
| MSP Portal | https://msp.osiriscare.net | Dashboard alias |
| Client Portal | https://dashboard.osiriscare.net/portal | Client login |

---

## Server Access

```bash
# SSH to VPS
ssh root@178.156.162.116

# View container status
docker ps

# View logs
docker logs -f mcp-server
docker logs -f caddy

# Restart services
cd /opt/mcp-server && docker compose restart
```

---

## API Endpoints

### Core Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check (DB, Redis, MinIO status) |
| `/api/sites` | GET/POST | Site management |
| `/api/sites/{id}` | GET/PUT | Site details |
| `/api/appliances/checkin` | POST | Appliance phone-home |
| `/runbooks` | GET | List loaded runbooks |
| `/stats` | GET | Server statistics |

### Evidence Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/evidence/sites/{id}/submit` | POST | Submit evidence bundle (Ed25519 signed) |
| `/api/evidence/sites/{id}/verify` | GET | Verify hash chain + signatures |
| `/api/evidence/sites/{id}/bundles` | GET | List evidence bundles |
| `/api/evidence/public-key` | GET | Get Ed25519 public key |

### Provisioning Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/provision/{mac}` | GET | Get config for MAC address |
| `/api/provision` | POST | Register MAC for provisioning |
| `/api/provision/{mac}` | DELETE | Remove provisioning entry |

### Learning Loop Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/learning/status` | GET | Learning loop status |
| `/learning/candidates` | GET | Promotion candidates |
| `/agent/sync` | GET | Get L1 rules for agent |
| `/agent/checkin` | POST | Agent checkin |

---

## File Locations (on VPS)

```
/opt/mcp-server/
├── docker-compose.yml     # Service definitions
├── Caddyfile             # Reverse proxy config
├── app/                  # FastAPI application
│   ├── main.py
│   └── dashboard_api/
│       ├── sites.py
│       ├── evidence_chain.py
│       ├── provisioning.py
│       └── ...
├── frontend/dist/        # Built React app
├── secrets/
│   └── signing.key       # Ed25519 private key
├── migrations/           # SQL migrations
└── init.sql              # Database initialization
```

---

## Database Schema (Key Tables)

| Table | Purpose |
|-------|---------|
| `sites` | Registered client sites |
| `compliance_bundles` | Evidence bundles with hash chain |
| `patterns` | Learning loop patterns |
| `appliance_provisioning` | MAC-based provisioning |
| `incidents` | Incident tracking |

---

## Deployed Appliances

| Site ID | Type | IP | Status |
|---------|------|-----|--------|
| physical-appliance-pilot-1aea78 | HP T640 | 192.168.88.246 | online |
| test-appliance-lab-b3c40c | VM | 192.168.88.247 | online |

---

## Ed25519 Signing

| Property | Value |
|----------|-------|
| Algorithm | Ed25519 |
| Key Location | `/opt/mcp-server/secrets/signing.key` |
| Public Key | `904b211dba3786764c3a3ab3723db8640295f390c196b8f3bc47ae0a47a0b0db` |
| Verify Endpoint | `GET /api/evidence/public-key` |

---

## Quick Health Check

```bash
# API health
curl https://api.osiriscare.net/health | jq .

# Check sites
curl https://api.osiriscare.net/api/sites | jq '.[] | {site_id, status}'

# Check physical appliance
curl https://api.osiriscare.net/api/sites/physical-appliance-pilot-1aea78 | jq .

# Verify evidence chain
curl https://api.osiriscare.net/api/evidence/sites/test-appliance-lab-b3c40c/verify | jq .
```

---

## Maintenance Commands

```bash
# SSH to VPS
ssh root@178.156.162.116

# View all logs
cd /opt/mcp-server && docker compose logs -f

# Restart all services
cd /opt/mcp-server && docker compose restart

# Rebuild and restart
cd /opt/mcp-server && docker compose up -d --build

# Database shell
docker exec -it mcp-postgres psql -U mcp -d mcp

# View evidence bundles
docker exec -it mcp-postgres psql -U mcp -d mcp -c "SELECT bundle_id, site_id, created_at FROM compliance_bundles ORDER BY created_at DESC LIMIT 10"
```

---

## Next Steps

1. **Deploy full compliance-agent** - Replace phone-home with full agent on appliances
2. **L1 rules syncing** - Agent downloads rules from Central Command
3. **Evidence upload to MinIO** - Agent uploads bundles to WORM storage
4. **OpenTimestamps** - Blockchain anchoring for enterprise tier
5. **Multi-NTP verification** - Time source validation before signing

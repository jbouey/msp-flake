# Session: Production MCP Server Deployment

**Date:** 2025-12-28
**Duration:** ~1 hour
**Focus:** Deploy production MCP server to Hetzner VPS + create architecture diagrams

---

## Summary

Deployed the production MCP Server stack to a Hetzner VPS and created comprehensive Mermaid architecture diagrams for the platform.

---

## Work Completed

### 1. Architecture Diagrams Created

Created `docs/diagrams/` with:

| File | Description |
|------|-------------|
| `system-architecture.mermaid` | Component relationships (NixOS, MCP, Agent, Backup) |
| `data-flow.mermaid` | Compliance checks → Healing → Evidence → Reports |
| `deployment-topology.mermaid` | Network boundaries, HIPAA zones, encrypted paths |
| `README.md` | Documentation for viewing and updating diagrams |

### 2. Production MCP Server Deployed

**Server:** Hetzner VPS at `178.156.162.116`

**Stack Components:**
- FastAPI application (:8000)
- PostgreSQL 16 (8 tables)
- Redis 7 (rate limiting + caching)
- MinIO (WORM evidence storage, :9000/:9001)

**Features Implemented:**
- Ed25519 signed orders with 15-minute TTL
- Rate limiting: 10 requests / 5 minutes / site_id
- L1 deterministic runbook selection
- 6 default HIPAA runbooks in database
- Pull-only architecture (appliances poll server)

**Server Location:** `/opt/mcp-server/`
```
/opt/mcp-server/
├── docker-compose.yml
├── .env (secrets)
├── init.sql (8-table schema)
├── app/
│   ├── main.py (FastAPI app)
│   ├── requirements.txt
│   └── Dockerfile
├── secrets/
│   └── signing.key (Ed25519)
└── runbooks/
    └── backup-verify.yaml
```

### 3. API Endpoints Verified

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Service health check |
| `/checkin` | POST | Appliance registration/update |
| `/orders/{site_id}` | GET | Get pending orders |
| `/orders/acknowledge` | POST | Acknowledge order receipt |
| `/incidents` | POST | Report incident → get signed order |
| `/drift` | POST | Report drift detection |
| `/evidence` | POST | Submit evidence bundle |
| `/runbooks` | GET | List available runbooks |
| `/stats` | GET | Server statistics |

### 4. NixOS Configuration Updated

Updated Hetzner VPS NixOS config:
- Enabled Docker
- Installed docker-compose, git, htop, vim, curl, jq
- Opened firewall ports: 22, 80, 443, 8000

---

## Files Changed

### New Files
- `docs/diagrams/system-architecture.mermaid`
- `docs/diagrams/data-flow.mermaid`
- `docs/diagrams/deployment-topology.mermaid`
- `docs/diagrams/README.md`
- `.agent/sessions/2025-12-28-mcp-server-deploy.md`

### Updated Files
- `.agent/CONTEXT.md` - Added MCP server architecture + commands
- `.agent/TODO.md` - Marked deployment complete, added new tasks

### Remote Server Files (178.156.162.116)
- `/opt/mcp-server/docker-compose.yml`
- `/opt/mcp-server/.env`
- `/opt/mcp-server/init.sql`
- `/opt/mcp-server/app/main.py`
- `/opt/mcp-server/app/requirements.txt`
- `/opt/mcp-server/app/Dockerfile`
- `/opt/mcp-server/secrets/signing.key`
- `/opt/mcp-server/runbooks/backup-verify.yaml`
- `/etc/nixos/configuration.nix`

---

## Test Results

```bash
# Health check
$ curl http://178.156.162.116:8000/health
{
  "status": "ok",
  "redis": "connected",
  "database": "connected",
  "minio": "connected",
  "runbooks_loaded": 1
}

# Test checkin
$ curl -X POST http://178.156.162.116:8000/checkin \
    -d '{"site_id":"test-clinic-001","host_id":"nixos-01","deployment_mode":"reseller"}'
{
  "status": "ok",
  "action": "registered",
  "server_public_key": "904b211d...",
  "pending_orders": []
}

# Test incident → gets signed order
$ curl -X POST http://178.156.162.116:8000/incidents \
    -d '{"site_id":"test-clinic-001","host_id":"nixos-01","incident_type":"backup_failed","severity":"high"}'
{
  "status": "received",
  "resolution_tier": "L1",
  "order_id": "c9bd692aa07f2a28",
  "runbook_id": "RB-BACKUP-001"
}
```

---

## Next Steps

1. **Connect compliance agent to production MCP** - Update mcpUrl in NixOS module
2. **Configure TLS** - Set up HTTPS with Let's Encrypt
3. **MinIO Object Lock** - Enable WORM retention policy
4. **Deploy first T640 appliance** - Image and configure hardware

---

## Access Credentials

| Service | URL | Notes |
|---------|-----|-------|
| MCP API | http://178.156.162.116:8000 | Public |
| MinIO Console | http://178.156.162.116:9001 | minio / minio-hipaa-2024-secure |
| SSH | root@178.156.162.116 | Ed25519 key auth |

---

## Notes

- The init.sql needed careful escaping for PostgreSQL string literals in heredocs
- Docker was not pre-installed on the NixOS image; had to enable via configuration.nix
- Signing key permissions required adjustment (644 for container access)
- All passwords stored in `/opt/mcp-server/.env` with 600 permissions

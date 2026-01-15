# VPS Deployment Guide

## CRITICAL: Two Separate Directories

There are TWO different deployments on the VPS - **always use production**:

| Directory | Purpose | Container | USE THIS? |
|-----------|---------|-----------|-----------|
| `/opt/mcp-server/` | **PRODUCTION** | `mcp-server` | ✅ YES |
| `/root/msp-iso-build/` | Git repo / ISO builds | `msp-server` | ❌ NO (for ISO builds only) |

## Quick Deploy

After pushing changes to GitHub:

```bash
ssh root@api.osiriscare.net "/opt/mcp-server/deploy.sh"
```

This script:
1. Pulls latest from git
2. Syncs backend code to production
3. Builds frontend
4. Deploys frontend to container
5. Restarts backend

## Manual Deployment Steps

If the script doesn't exist or you need manual control:

```bash
# SSH to VPS
ssh root@api.osiriscare.net

# Pull latest code
cd /root/msp-iso-build
git pull origin main

# Sync backend
rsync -av --delete /root/msp-iso-build/mcp-server/central-command/backend/integrations/ /opt/mcp-server/app/dashboard_api/integrations/

# Build frontend
cd /root/msp-iso-build/mcp-server/central-command/frontend
npm run build

# Deploy frontend
docker cp dist/. central-command:/usr/share/nginx/html/

# Restart backend
cd /opt/mcp-server
docker compose restart mcp-server

# Verify
curl https://api.osiriscare.net/health
```

## Container Names

| Service | Container Name | Port |
|---------|---------------|------|
| Backend API | `mcp-server` | 8000 |
| Frontend | `central-command` | 80 (internal) |
| Postgres | `mcp-postgres` | 5432 |
| Redis | `mcp-redis` | 6379 |
| MinIO | `mcp-minio` | 9000-9001 |
| Caddy | `caddy` | 80, 443 |

## Caddy Routing

The Caddyfile at `/opt/mcp-server/Caddyfile` routes:
- `api.osiriscare.net` → `mcp-server:8000`
- `dashboard.osiriscare.net` → `central-command:80` (with `/api/*` → `mcp-server:8000`)

## Database Migrations

Migrations are in `/opt/mcp-server/migrations/`. To run:

```bash
ssh root@api.osiriscare.net
docker exec mcp-postgres psql -U mcp -d mcp -f /path/to/migration.sql
```

## Common Issues

### 502 Bad Gateway
- Check Caddy logs: `docker logs caddy --tail 20`
- Verify container name in Caddyfile matches running container
- Restart Caddy: `cd /opt/mcp-server && docker compose restart caddy`

### Database constraint errors
- Check if new providers/types need to be added to CHECK constraints
- Example: `ALTER TABLE integrations DROP CONSTRAINT valid_provider; ALTER TABLE integrations ADD CONSTRAINT valid_provider CHECK (...);`

### Frontend not updating
- Hard refresh browser (Cmd+Shift+R)
- Verify correct bundle: `curl -s https://dashboard.osiriscare.net/ | grep -o 'index-[^"]*\.js'`
- Check container has new files: `docker exec central-command cat /usr/share/nginx/html/index.html | grep index-`

# MSP Compliance Platform - Demo Stack

```
 ██████╗ ███████╗██╗   ██╗     ██████╗ ███╗   ██╗██╗  ██╗   ██╗
 ██╔══██╗██╔════╝██║   ██║    ██╔═══██╗████╗  ██║██║  ╚██╗ ██╔╝
 ██║  ██║█████╗  ██║   ██║    ██║   ██║██╔██╗ ██║██║   ╚████╔╝
 ██║  ██║██╔══╝  ╚██╗ ██╔╝    ██║   ██║██║╚██╗██║██║    ╚██╔╝
 ██████╔╝███████╗ ╚████╔╝     ╚██████╔╝██║ ╚████║███████╗██║
 ╚═════╝ ╚══════╝  ╚═══╝       ╚═════╝ ╚═╝  ╚═══╝╚══════╝╚═╝

 THIS IS FOR LOCAL DEVELOPMENT AND TESTING ONLY
 DO NOT USE IN PRODUCTION - NO SECURITY HARDENING
```

## Overview

This demo stack provides a complete local environment for testing the MSP Compliance Platform without needing the full NixOS infrastructure.

## Components

| Service | Port | Description |
|---------|------|-------------|
| **mcp-server** | 8001 | FastAPI MCP server stub |
| **redis** | 6379 | Event queue and state storage |
| **minio** | 9000/9001 | S3-compatible WORM storage |
| **agent** | - | Demo compliance agent |

## Quick Start

```bash
# Start all services
docker compose up -d

# View logs
docker compose logs -f

# View just agent logs
docker compose logs -f agent

# Stop everything
docker compose down -v
```

## Testing

### Health Check

```bash
curl http://localhost:8001/health
```

### Inject Test Order

```bash
# Inject a backup runbook order
curl -X POST "http://localhost:8001/demo/inject-order?runbook_id=RB-BACKUP-001"

# Inject a certificate renewal order
curl -X POST "http://localhost:8001/demo/inject-order?runbook_id=RB-CERT-001"
```

### View Evidence

```bash
# List all evidence bundles
curl http://localhost:8001/evidence

# Get specific bundle
curl http://localhost:8001/evidence/{bundle_id}
```

### Demo Stats

```bash
curl http://localhost:8001/demo/stats
```

### Reset Demo Data

```bash
curl -X DELETE http://localhost:8001/demo/reset
```

## API Endpoints

### Orders

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/orders?site_id=X` | Get pending orders |
| POST | `/orders` | Create new order |
| GET | `/orders/{id}` | Get order status |
| PATCH | `/orders/{id}` | Update order status |

### Evidence

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/evidence` | Upload evidence bundle |
| GET | `/evidence` | List evidence bundles |
| GET | `/evidence/{id}` | Get specific bundle |

### Runbooks

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/runbooks` | List available runbooks |
| GET | `/runbooks/{id}` | Get runbook definition |

## MinIO Console

Access the MinIO console at http://localhost:9001

- **Username:** minioadmin
- **Password:** minioadmin123

Buckets:
- `evidence` - Evidence bundles
- `compliance-packets` - Generated compliance reports

## Architecture

```
┌─────────────┐         ┌─────────────┐
│   Agent     │ ──poll──▶│ MCP Server  │
│   (stub)    │◀─orders─ │   (stub)    │
└──────┬──────┘         └──────┬──────┘
       │                       │
       │ evidence              │ state
       │                       │
       ▼                       ▼
┌─────────────┐         ┌─────────────┐
│   MinIO     │         │   Redis     │
│   (WORM)    │         │   (queue)   │
└─────────────┘         └─────────────┘
```

## Configuration

Environment variables for the agent:

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_URL` | http://mcp-server:8001 | MCP server URL |
| `SITE_ID` | demo-site-001 | Site identifier |
| `HOST_ID` | demo-host-001 | Host identifier |
| `POLL_INTERVAL` | 30 | Seconds between polls |
| `LOG_LEVEL` | DEBUG | Logging level |

## Runbooks Included

- `RB-BACKUP-001` - Backup failure remediation
- `RB-CERT-001` - Certificate expiry renewal
- `RB-CPU-001` - High CPU remediation
- `RB-DISK-001` - Disk space cleanup
- `RB-DRIFT-001` - Configuration drift fix
- `RB-RESTORE-001` - Backup restore test
- `RB-SERVICE-001` - Service restart

## Troubleshooting

### Services not starting

```bash
# Check service status
docker compose ps

# View specific service logs
docker compose logs mcp-server
```

### Redis connection issues

```bash
# Test Redis connection
docker compose exec redis redis-cli ping
```

### MinIO not accessible

```bash
# Check MinIO health
docker compose exec minio mc ready local
```

## Development

### Rebuild after changes

```bash
docker compose build --no-cache
docker compose up -d
```

### Run specific service

```bash
docker compose up -d mcp-server redis
```

### Shell into container

```bash
docker compose exec mcp-server /bin/sh
docker compose exec agent /bin/sh
```

---

**WARNING:** This demo stack has NO security hardening. Do not expose to the internet or use with real data.

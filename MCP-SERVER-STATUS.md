# MCP Server Status

**Last Updated:** 2025-11-21
**Server Location:** mcp-server VM (port 4445)
**API Endpoint:** http://localhost:8001 (from Mac host)

---

## Current Status

| Component | Status | Notes |
|-----------|--------|-------|
| MCP Server | Running | Manually started with nohup |
| Redis | Running | Port 6379, default config |
| API Health | Healthy | `/health` returns OK |
| Runbooks | 5/7 Loaded | 2 have YAML parse errors |

---

## Server Details

### Access
```bash
# SSH to MCP server VM
ssh -p 4445 root@localhost  # from Mac host

# API health check
curl http://localhost:8001/health
```

### Running Process
```bash
# Server running as:
python3 /var/lib/mcp-server/server.py

# Started with:
cd /var/lib/mcp-server && nohup python3 server.py > server.log 2>&1 &
```

### Configuration
- **Host:** 0.0.0.0
- **Port:** 8000 (forwarded to Mac host port 8001)
- **Redis Host:** 127.0.0.1
- **Redis Port:** 6379

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/runbooks` | GET | List loaded runbooks |
| `/execute` | POST | Execute a tool |
| `/incident` | POST | Report an incident |

### Health Check Response
```json
{
  "status": "healthy",
  "redis": "connected",
  "runbooks_loaded": 5,
  "uptime_seconds": 3600
}
```

---

## Runbooks Status

### Loaded Successfully (5)
| Runbook ID | Name | Status |
|------------|------|--------|
| RB-SERVICE-001 | Service Restart | Loaded |
| RB-CERT-001 | Certificate Renewal | Loaded |
| RB-CPU-001 | CPU High Usage | Loaded |
| RB-DRIFT-001 | Configuration Drift | Loaded |
| RB-BACKUP-001 | Backup Failure | Loaded |

### Failed to Load (2)
| Runbook ID | Error | Issue |
|------------|-------|-------|
| RB-DISK-001 | YAML parse error (line 22) | Unknown escape character ';' |
| RB-RESTORE-001 | YAML parse error (line 37) | Unknown escape character ';' |

**Fix Required:** Edit these files to properly escape special characters or quote strings containing `;`.

---

## File Locations (on mcp-server VM)

```
/var/lib/mcp-server/
├── server.py           # Main FastAPI application
├── server.log          # Application logs
├── runbooks/
│   ├── RB-SERVICE-001.yaml
│   ├── RB-CERT-001.yaml
│   ├── RB-CPU-001.yaml
│   ├── RB-DISK-001.yaml      # YAML error
│   ├── RB-DRIFT-001.yaml
│   ├── RB-RESTORE-001.yaml   # YAML error
│   └── RB-BACKUP-001.yaml
└── evidence/           # Generated evidence bundles
```

---

## Dependencies Installed

Python 3.11.10 with:
- fastapi
- uvicorn
- pydantic
- redis (async)
- aiohttp
- PyYAML

---

## Known Issues

### 1. Manual Start Required
- NixOS read-only filesystem prevents systemd override
- Server must be started manually after VM reboot
- **Workaround:** Run `nohup python3 server.py &` after boot

### 2. Two Runbooks Have YAML Errors
- RB-DISK-001.yaml and RB-RESTORE-001.yaml fail to parse
- Contains unescaped special characters
- **Impact:** 5 of 7 runbooks functional

### 3. Server Runs as Root
- Currently runs in root context
- Should be moved to dedicated service user

---

## Restart Procedure

If the MCP server needs to be restarted:

```bash
# SSH to VM
ssh -p 4445 root@localhost

# Kill existing process
pkill -f "python.*server.py"

# Start fresh
cd /var/lib/mcp-server
nohup python3 server.py > server.log 2>&1 &

# Verify
curl http://localhost:8000/health
```

---

## Next Steps

1. **Fix YAML Runbooks** - Escape special characters in RB-DISK-001 and RB-RESTORE-001
2. **Proper NixOS Integration** - Create proper NixOS configuration for auto-start
3. **Add Authentication** - Currently no auth on API endpoints
4. **Connect Client VM** - Wire test-client-001 to report to MCP server

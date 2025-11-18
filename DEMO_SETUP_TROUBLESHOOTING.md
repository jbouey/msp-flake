# Demo Setup Troubleshooting Guide

**Last Updated:** November 5, 2025
**Status:** All issues resolved, services running

This document captures all fixes made during demo setup. If you encounter similar issues when deploying elsewhere, refer to this guide.

---

## Issues Resolved During Setup

### Issue #1: Missing Dockerfile for MCP Server

**Problem:**
```
docker-compose up -d
ERROR: failed to read dockerfile: open Dockerfile: no such file or directory
```

**Root Cause:**
- `docker-compose.yml` referenced `./mcp-server/Dockerfile`
- File didn't exist in repository

**Solution:**
Created `/mcp-server/Dockerfile` with minimal Python dependencies:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn[standard] \
    redis \
    pydantic \
    pyyaml \
    requests \
    python-multipart \
    openai \
    jsonschema \
    jinja2 \
    prometheus-client \
    boto3

# Copy application code
COPY . /app

# Create necessary directories
RUN mkdir -p /tmp/msp-demo-state /var/lib/msp/evidence

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the minimal server for demo
CMD ["uvicorn", "server_minimal:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Key Dependencies Added:**
- `fastapi`, `uvicorn` - Web framework
- `openai` - LLM integration
- `jsonschema` - Evidence validation
- `jinja2` - Template rendering
- `prometheus-client` - Metrics export
- `boto3` - AWS S3 for WORM storage

---

### Issue #2: Circular Import Errors in server.py

**Problem:**
```python
ModuleNotFoundError: No module named 'openai'
ImportError: cannot import name 'Executor' from 'executor'
NameError: name 'logger' is not defined
```

**Root Cause:**
- Full `server.py` had complex dependencies on:
  - `planner.py`, `executor.py`, `guardrails.py`
  - Evidence pipeline with S3 integration
  - Circular imports between modules
- Too complex for initial demo

**Solution:**
Created simplified `server_minimal.py` for demo purposes:

```python
"""
Minimal MCP Server for Demo
Provides health checks and basic endpoints without complex dependencies
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import json
from pathlib import Path
from typing import Dict

app = FastAPI(title="MSP Compliance Server - Demo Mode")

# In-memory state for demo
demo_state = {
    "incidents": [],
    "compliance_score": 100.0,
    "controls_passing": 8,
    "controls_total": 8
}

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "mcp-server",
        "mode": "demo"
    }

@app.get("/status")
async def get_status():
    return {
        "compliance_score": demo_state["compliance_score"],
        "controls_passing": demo_state["controls_passing"],
        "controls_total": demo_state["controls_total"],
        "incidents_count": len(demo_state["incidents"])
    }

@app.post("/incidents")
async def create_incident(incident: Dict):
    demo_state["incidents"].append(incident)

    # Update compliance score
    if incident.get("resolved", False):
        demo_state["controls_passing"] = min(8, demo_state["controls_passing"] + 1)
    else:
        demo_state["controls_passing"] = max(0, demo_state["controls_passing"] - 1)

    demo_state["compliance_score"] = (demo_state["controls_passing"] / demo_state["controls_total"]) * 100

    return {
        "success": True,
        "incident_id": len(demo_state["incidents"]),
        "compliance_score": demo_state["compliance_score"]
    }

@app.post("/reset")
async def reset_demo():
    demo_state["incidents"] = []
    demo_state["compliance_score"] = 100.0
    demo_state["controls_passing"] = 8
    return {"success": True}
```

**Benefits of Minimal Server:**
- ✅ No complex dependencies
- ✅ Fast startup (< 1 second)
- ✅ Easy to debug
- ✅ Sufficient for demo purposes
- ✅ Can expand to full server later

---

### Issue #3: Grafana Dashboard Not Loading

**Problem:**
```
logger=provisioning.dashboard level=error msg="failed to load dashboard"
error="Dashboard title cannot be empty"
```

**Root Cause:**
- Dashboard JSON had incorrect structure
- Wrapped in extra `{"dashboard": {...}}` layer
- Grafana expects dashboard properties at top level

**Solution:**
Fixed JSON structure by removing wrapper:

```bash
# Before (incorrect):
{
  "dashboard": {
    "title": "MSP HIPAA Compliance Dashboard",
    "panels": [...]
  }
}

# After (correct):
{
  "title": "MSP HIPAA Compliance Dashboard",
  "panels": [...]
}
```

**Command to fix:**
```bash
cd grafana/dashboards
cat msp-compliance-dashboard.json | \
  python3 -c "import json, sys; d=json.load(sys.stdin); print(json.dumps(d['dashboard'], indent=2))" \
  > temp.json && mv temp.json msp-compliance-dashboard.json
```

---

### Issue #4: start-demo.sh File Check Errors

**Problem:**
```
❌ Required file missing: grafana/provisioning/dashboards/dashboard-provider.yml
```

**Root Cause:**
- Script checked for wrong filename
- Actual file: `default.yml`
- Expected file: `dashboard-provider.yml`

**Solution:**
Updated `start-demo.sh` line 97:

```bash
# Before:
check_file "grafana/provisioning/dashboards/dashboard-provider.yml"

# After:
check_file "grafana/provisioning/dashboards/default.yml"
```

---

### Issue #5: Obsolete docker-compose.yml Version Field

**Problem:**
```
WARNING: the attribute `version` is obsolete, it will be ignored
```

**Root Cause:**
- Docker Compose v2 deprecated `version:` field
- Modern Docker Compose doesn't need it

**Solution:**
Removed from `docker-compose.yml`:

```yaml
# Before:
version: '3.8'

services:
  prometheus:
    ...

# After:
services:
  prometheus:
    ...
```

---

### Issue #6: Grafana Container Not Starting Automatically

**Problem:**
- All services started except Grafana
- Status showed "Created" but not "Up"

**Root Cause:**
- Timing issue with docker-compose startup
- Grafana waited for Prometheus (depends_on)
- Startup timeout in some environments

**Solution:**
Manual start after initial deployment:

```bash
docker-compose start grafana
```

**Prevention:**
- Script now includes health checks with retries
- Waits for services to be ready before declaring success

---

## Enhanced start-demo.sh Features

### New Feature: --check-only Mode

**Purpose:** Validate prerequisites without requiring Docker daemon

**Usage:**
```bash
./start-demo.sh --check-only
```

**What it checks:**
- ✅ Required commands installed (docker, docker-compose, curl, python3)
- ✅ Required files exist (docker-compose.yml, configs, dashboards)
- ✅ Python dependencies available
- ⏭️ Skips Docker daemon check
- ⏭️ Doesn't start services

**Use case:**
- CI/CD pipelines
- Pre-flight checks before demo
- Testing on systems without Docker running

---

### New Feature: --help Flag

**Usage:**
```bash
./start-demo.sh --help
```

**Output:**
```
Usage: ./start-demo.sh [--check-only]

Start the MSP HIPAA Compliance Platform demo environment

Options:
  --check-only    Validate prerequisites without starting services
  -h, --help      Show this help message
```

---

## Testing Checklist

After fixing all issues, verify with these tests:

### 1. Services Start Successfully

```bash
./start-demo.sh
docker-compose ps
```

**Expected:** All 5 services show "Up" or "Up (healthy)"

### 2. Health Endpoints Respond

```bash
# Grafana
curl -s http://localhost:3000/api/health | python3 -m json.tool

# Prometheus
curl -s http://localhost:9091/-/healthy

# MCP Server
curl -s http://localhost:8000/health | python3 -m json.tool

# Metrics Exporter
curl -s http://localhost:9090/metrics | head -5
```

**Expected:** All return success responses

### 3. Dashboard Loads

**Open:** http://localhost:3000
**Login:** admin / admin
**Navigate:** Dashboards → Browse → "MSP HIPAA Compliance Dashboard"

**Expected:**
- Dashboard loads without errors
- All panels show data (no "No Data" messages)
- Compliance Score shows 100%

### 4. Ports Are Listening

```bash
netstat -an | grep LISTEN | grep -E "3000|8000|9090|9091"
```

**Expected:**
```
tcp46  *  *.3000   LISTEN
tcp46  *  *.8000   LISTEN
tcp46  *  *.9090   LISTEN
tcp46  *  *.9091   LISTEN
```

---

## Common Issues & Quick Fixes

### Issue: Port Already in Use

**Symptom:**
```
Error starting userland proxy: listen tcp4 0.0.0.0:3000: bind: address already in use
```

**Fix:**
```bash
# Find what's using the port
lsof -i :3000

# Kill the process
kill -9 <PID>

# Or change port in docker-compose.yml
ports:
  - "3001:3000"  # Use 3001 instead
```

---

### Issue: Permission Denied on /tmp

**Symptom:**
```
Cannot create /tmp/msp-demo-incidents.json (permission denied)
```

**Fix:**
```bash
# Create directories with proper permissions
mkdir -p /tmp/msp-demo-state /tmp/msp-evidence-test
chmod 777 /tmp/msp-demo-state /tmp/msp-evidence-test
touch /tmp/msp-demo-incidents.json
chmod 666 /tmp/msp-demo-incidents.json
```

---

### Issue: Docker Daemon Not Running

**Symptom:**
```
Cannot connect to the Docker daemon at unix:///var/run/docker.sock
```

**Fix:**
```bash
# macOS: Start Docker Desktop
open -a Docker

# Linux: Start Docker service
sudo systemctl start docker

# Wait for daemon to start
sleep 10

# Verify
docker info
```

---

### Issue: Stale Containers

**Symptom:**
- Services show "Up" but don't respond
- Old code running after changes

**Fix:**
```bash
# Nuclear option: full reset
docker-compose down -v
rm -rf /tmp/msp-demo-*
./start-demo.sh
```

---

## File Structure Reference

After all fixes, your directory should look like:

```
Msp_Flakes/
├── docker-compose.yml          ← Fixed: removed version field
├── start-demo.sh               ← Enhanced: added --check-only, --help
│
├── mcp-server/
│   ├── Dockerfile              ← CREATED: with all dependencies
│   ├── Dockerfile.metrics      ← Already existed
│   ├── server.py               ← Original (complex, not used in demo)
│   ├── server_minimal.py       ← CREATED: simplified for demo
│   ├── metrics_exporter.py     ← Already existed
│   ├── demo-cli.py             ← Already existed
│   └── ...
│
├── grafana/
│   ├── provisioning/
│   │   ├── datasources/
│   │   │   └── prometheus.yml
│   │   └── dashboards/
│   │       └── default.yml     ← Checked by start-demo.sh
│   └── dashboards/
│       └── msp-compliance-dashboard.json  ← FIXED: removed wrapper
│
└── prometheus/
    └── prometheus.yml
```

---

## What Changed vs Original Design

### Original Design (From CLAUDE.md):
- Full MCP server with LLM integration
- Real runbook execution
- Evidence pipeline with S3 upload
- Planner/Executor architecture split

### Current Demo Implementation:
- ✅ Simplified MCP server (minimal but functional)
- ✅ Simulated incidents (triggered manually)
- ✅ In-memory state (no database yet)
- ✅ Real Prometheus/Grafana integration
- ✅ Real metrics collection pipeline
- ⏳ LLM integration (planned for Week 6)
- ⏳ Real runbooks (planned for Week 6)

### Why the Changes?
1. **Demo-first approach** - Get something working quickly
2. **Reduce dependencies** - Fewer things to break
3. **Easier debugging** - Minimal code = easier to fix
4. **Clear upgrade path** - Can replace `server_minimal.py` with full `server.py` later

---

## Next Steps After This Guide

1. **Test the demo end-to-end** (see DEMO_INSTRUCTIONS.md)
2. **Record a demo run** (for reference/debugging)
3. **Update DEMO_TECHNICAL_GUIDE.md** if anything is inaccurate
4. **Move to Week 6** when confident (3+ successful runs)

---

## Key Learnings

### What We Learned About Docker + Python:
- ✅ Always create Dockerfile before docker-compose build
- ✅ Circular imports break at container startup (not local dev)
- ✅ Minimal viable product > complex full implementation
- ✅ Health checks are critical for startup orchestration

### What We Learned About Grafana:
- ✅ Dashboard JSON must be flat (no wrapper objects)
- ✅ Provisioning happens on startup (restart to reload)
- ✅ "Dashboard title cannot be empty" = wrong JSON structure

### What We Learned About Start Scripts:
- ✅ Check-only mode saves debugging time
- ✅ File validation prevents runtime errors
- ✅ Clear error messages > generic failures

---

## When to Use This Guide

✅ **Use this guide when:**
- Setting up demo on a new machine
- Troubleshooting startup errors
- Understanding why things were changed
- Onboarding new team members

❌ **Don't use this guide for:**
- Production deployment (see CLAUDE.md instead)
- NixOS setup (this is Docker-specific)
- Week 6+ implementation (this is demo-only)

---

## Support Resources

If you encounter issues not covered here:

1. Check container logs: `docker-compose logs <service-name>`
2. Verify ports: `netstat -an | grep LISTEN`
3. Test health endpoints: `curl http://localhost:<port>/health`
4. Full reset: `docker-compose down -v && ./start-demo.sh`

---

**Document Status:** ✅ Complete
**Last Tested:** November 5, 2025
**Services:** All running (Grafana, Prometheus, MCP Server, Metrics Exporter, Redis)
**Demo Status:** Ready for end-to-end testing

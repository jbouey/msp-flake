# Demo Technical Guide: Architecture, Tools & Success Metrics

**Purpose:** Understand what's running, why it exists, and when you're ready to move to Week 6

---

## Architecture Overview: Why Docker?

### The Problem Docker Solves Here

Your production system will run on **NixOS** (deterministic, immutable infrastructure). But for a **quick demo**, you need:

1. **Fast setup** - Reviewers/stakeholders may not have NixOS
2. **Portability** - Run on any Mac/Linux/Windows machine
3. **Isolation** - Don't pollute host system with dependencies
4. **Reset-ability** - Clean slate between demo runs

**Docker = Demo Environment**
**NixOS = Production Deployment** (Week 6+)

Think of this as: "Docker is your sales demo, NixOS is the product you ship."

---

## The 5 Services Explained

Your `docker-compose.yml` orchestrates 5 interconnected services. Here's what each does and why:

### 1. **Redis** (Event Queue)
```yaml
redis:
  image: redis:alpine
  ports: 6379:6379
  command: redis-server --appendonly yes
```

**What it does:**
- In-memory database used as event queue
- Stores incident data temporarily
- Enables rate limiting (cooldown keys)

**Why you need it:**
- MCP server needs a queue to track incidents
- Rate limiting prevents runbook thrashing (same fix can't run twice in 5 min)
- In production: NATS JetStream or Redis with AOF (append-only file for durability)

**How it's used in demo:**
- Not heavily used yet (placeholder for future work)
- In full implementation, stores incident state, rate limit keys, evidence metadata

**Port 6379:** Standard Redis port

---

### 2. **MCP Server** (FastAPI Backend)
```yaml
mcp-server:
  build: ./mcp-server
  ports: 8000:8000
  environment:
    REDIS_URL: redis://redis:6379
```

**What it does:**
- FastAPI web server (Python)
- Receives incidents via HTTP POST
- Would select runbooks (not fully implemented yet)
- Would execute remediation actions (simulated in demo)

**Why you need it:**
- This is the "brain" - receives problems, decides solutions
- In production: calls LLM to select runbook, executes steps, generates evidence
- In demo: receives incidents from `demo-cli.py`, logs them

**How it's used in demo:**
- Listens on http://localhost:8000
- Accepts POST requests with incident data
- Logs incidents to `/tmp/msp-demo-incidents.json`
- Simulates remediation (marks incidents as resolved after 60s)

**Port 8000:** HTTP API endpoint

---

### 3. **Metrics Exporter** (Prometheus Exporter)
```yaml
metrics-exporter:
  build: ./mcp-server (using Dockerfile.metrics)
  ports: 9090:9090
```

**What it does:**
- Python script that exposes metrics in Prometheus format
- Reads incident log file (`/tmp/msp-demo-incidents.json`)
- Reads demo state files (backup status, disk usage, etc.)
- Calculates compliance score, MTTR, success rate
- Exposes all data via HTTP endpoint `/metrics`

**Why you need it:**
- Prometheus can't read JSON files - it needs metrics in its format
- This is the "translator" between your incident data and Prometheus
- Updates every 10 seconds with fresh data

**How it works:**
```python
# Simplified version of what it does:
compliance_score = (passing_controls / total_controls) * 100
msp_compliance_score.set(compliance_score)

for incident in incidents:
    msp_incidents_total.labels(
        type=incident['type'],
        severity=incident['severity']
    ).inc()
```

**Port 9090:** Prometheus scrape endpoint (http://localhost:9090/metrics)

---

### 4. **Prometheus** (Metrics Storage & Query)
```yaml
prometheus:
  image: prom/prometheus
  ports: 9091:9090
  volumes:
    - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml
```

**What it does:**
- Time-series database for metrics
- Scrapes metrics from exporter every 30 seconds
- Stores historical data (last 15 days by default)
- Provides query API for Grafana

**Why you need it:**
- Grafana needs a data source - Prometheus is that source
- Stores metrics over time (so you can see incident timeline)
- Enables queries like "show incidents in last 24h"

**Config file:** `prometheus/prometheus.yml`
```yaml
scrape_configs:
  - job_name: 'msp-metrics'
    scrape_interval: 30s
    static_configs:
      - targets: ['metrics-exporter:9090']
```

**Translation:** "Every 30 seconds, fetch metrics from metrics-exporter"

**Port 9091:** Prometheus UI (http://localhost:9091)
- Why 9091? To avoid conflict if you're running another Prometheus
- Inside Docker network, services talk to each other on standard ports

---

### 5. **Grafana** (Dashboard UI)
```yaml
grafana:
  image: grafana/grafana
  ports: 3000:3000
  environment:
    GF_SECURITY_ADMIN_USER: admin
    GF_SECURITY_ADMIN_PASSWORD: admin
```

**What it does:**
- Web-based dashboard tool (the visual part you see)
- Queries Prometheus for data
- Renders 10 panels (compliance score, incident timeline, etc.)
- Auto-refreshes every 30 seconds

**Why you need it:**
- This is what stakeholders see during the demo
- Visualizes your compliance status in real-time
- Shows auto-remediation happening live

**Auto-provisioning:**
- `grafana/provisioning/datasources/prometheus.yml` - Tells Grafana where Prometheus is
- `grafana/provisioning/dashboards/default.yml` - Auto-loads dashboard on startup
- `grafana/dashboards/msp-compliance-dashboard.json` - The dashboard configuration

**Port 3000:** Grafana web UI (http://localhost:3000)

---

## Data Flow: How Everything Connects

```
┌─────────────────┐
│   demo-cli.py   │  ← You run this to trigger incidents
└────────┬────────┘
         │ POST http://localhost:8000/incident
         ↓
┌─────────────────┐
│   MCP Server    │  ← Receives incident, logs to file
└────────┬────────┘
         │ Writes to /tmp/msp-demo-incidents.json
         ↓
┌─────────────────┐
│ Metrics Exporter│  ← Reads incident log every 10s
└────────┬────────┘
         │ Exposes metrics at :9090/metrics
         ↓
┌─────────────────┐
│   Prometheus    │  ← Scrapes metrics every 30s
└────────┬────────┘
         │ Stores time-series data
         ↓
┌─────────────────┐
│    Grafana      │  ← Queries Prometheus, renders dashboard
└─────────────────┘
         ↓
  [Your Browser]  ← See live updates at http://localhost:3000
```

**Key Files:**
- `/tmp/msp-demo-incidents.json` - Incident log (JSON)
- `/tmp/msp-demo-state/` - State files (backup status, disk usage, etc.)
- `/var/lib/msp/evidence/` - Evidence bundles (signed JSON files)

---

## What Each Tool Does (Simplified)

| Tool | Role | Analogy |
|------|------|---------|
| **demo-cli.py** | Incident generator | "Break things on command" |
| **MCP Server** | Incident receiver | "Log the problem, simulate fix" |
| **Metrics Exporter** | Data translator | "Convert JSON → Prometheus format" |
| **Prometheus** | Time-series DB | "Remember what happened when" |
| **Grafana** | Dashboard | "Pretty graphs for humans" |
| **Redis** | Event queue | "Traffic cop for incidents" (minimal use in demo) |

---

## What's SIMULATED vs REAL

### ✅ REAL (Production-Ready)
- Docker orchestration
- Service discovery and networking
- Prometheus metrics collection
- Grafana dashboard rendering
- Incident logging structure
- Evidence bundle format
- Rate limiting architecture
- Multi-service coordination

### 🎭 SIMULATED (Demo Only)
- Actual incidents (you trigger them manually)
- Runbook execution (just marks resolved after 60s)
- LLM decision-making (not integrated yet)
- Real backup systems (uses dummy state files)
- Real service management (doesn't actually stop/start nginx)

**Important:** The *architecture* is real. The *data* is simulated. In production, real incidents would flow through this same pipeline.

---

## Success Metrics: When Is Demo "Working"?

### Minimum Success Criteria (Must Pass)

#### ✅ 1. Services Start Successfully
```bash
./start-demo.sh
```
**Expected:**
- All 5 containers start (green "healthy" status)
- No error messages in docker logs
- Ports accessible: 3000, 8000, 9090, 9091, 6379

**Test:**
```bash
docker-compose ps
# Should show all services "Up"

curl http://localhost:9090/metrics | grep msp_
# Should show metrics with values
```

---

#### ✅ 2. Dashboard Loads with Real Data
**Open:** http://localhost:3000 (admin/admin)

**Expected:**
- Dashboard loads without errors
- Compliance Score shows **100%** (green)
- 8 Core Controls table shows all **PASS** (green checkmarks)
- System Health shows all services **Up**
- No "No Data" messages

**Test:** Take a screenshot - all panels should have data

---

#### ✅ 3. Incident Triggering Works
```bash
./mcp-server/demo-cli.py break backup
```

**Expected (within 60 seconds):**
- Dashboard refreshes automatically
- Compliance Score drops to **87.5%**
- Recent Incidents panel shows "backup_failure"
- Incident Timeline graph shows spike
- Auto-Fixes counter increments by 1
- Compliance Score returns to **100%**

**Test:** Watch dashboard for 90 seconds - should go red, then green

---

#### ✅ 4. Multiple Incident Types Work
```bash
./mcp-server/demo-cli.py break backup
./mcp-server/demo-cli.py break disk
./mcp-server/demo-cli.py break service nginx
```

**Expected:**
- Dashboard shows 3 different incident types
- Each has different color in timeline
- All resolve within 60-120 seconds
- Compliance score fluctuates then stabilizes

**Test:** Incident Timeline should show 3 distinct colors

---

#### ✅ 5. Evidence Bundles Visible
**Dashboard Panel:** "Evidence Bundles"

**Expected:**
- Table shows at least 1 row
- Timestamp is recent (within last 5 minutes)
- Signed column shows checkmark or hash
- Download link exists (even if simulated)

**Test:** Panel should not say "No evidence bundles"

---

### Advanced Success Criteria (Nice to Have)

#### 🎯 6. Reset Functionality Works
```bash
./mcp-server/demo-cli.py reset
```

**Expected:**
- Compliance Score returns to 100%
- Incident count resets to 0
- Timeline clears recent spikes
- Ready for another demo run

---

#### 🎯 7. Metrics Endpoint Accessible
```bash
curl http://localhost:9090/metrics
```

**Expected:**
- Prometheus-format metrics output
- Contains lines like:
  ```
  msp_compliance_score 100.0
  msp_incidents_total{type="backup_failure"} 1
  msp_remediations_total{status="success"} 1
  ```

---

#### 🎯 8. Logs Are Clean
```bash
docker-compose logs --tail=50 mcp-server
docker-compose logs --tail=50 metrics-exporter
```

**Expected:**
- No Python tracebacks
- No "connection refused" errors
- Occasional INFO messages (normal)

---

## When to Move to Week 6: Checklist

### Prerequisites for Production Work

You're ready for Week 6 when you can:

- [ ] **Demo runs end-to-end 3 times in a row without failure**
  - Start services → Load dashboard → Trigger incidents → See remediation → Reset

- [ ] **You can explain the architecture to someone else**
  - "Prometheus scrapes metrics from the exporter, which reads incident logs generated by the MCP server"

- [ ] **All 5 minimum success criteria pass consistently**
  - Services start, dashboard loads, incidents trigger, multiple types work, evidence visible

- [ ] **You understand what's simulated vs. real**
  - Know which parts need to be built for production (runbooks, LLM integration, real system monitoring)

- [ ] **Documentation is accurate**
  - DEMO_INSTRUCTIONS.md reflects actual behavior
  - You've updated any wrong assumptions

- [ ] **You have a recorded demo (optional but recommended)**
  - Screen recording showing full 15-minute demo flow
  - Serves as reference if you need to debug later

---

## What Week 6 Actually Means

From CLAUDE.md, Week 6 is: **"In-house demo preparation"**

But based on your progress, Week 6 should be:

### **Week 6: Production Foundations**

1. **Replace Simulated Runbooks with Real Scripts**
   - RB-BACKUP-001: Actually check backup status
   - RB-DISK-001: Actually clear temp files
   - RB-SERVICE-001: Actually restart services (systemd integration)

2. **Integrate Real Monitoring Sources**
   - Instead of `/tmp/msp-demo-incidents.json`, read from:
     - Journald logs
     - Systemd service status
     - Actual disk usage (`df`)
     - Real backup logs (restic, etc.)

3. **Deploy to a Real Test VM (Not Docker)**
   - Spin up a NixOS VM (DigitalOcean, AWS, local VirtualBox)
   - Install client flake with actual monitoring
   - Trigger real incidents (fill disk, stop service)
   - Verify auto-remediation works

4. **Add LLM Integration**
   - Connect MCP server to GPT-4o
   - Implement planner/executor split
   - Test runbook selection with real incidents

5. **Evidence Pipeline**
   - Generate real evidence bundles (JSON + signature)
   - Upload to WORM storage (S3 with object lock)
   - Test evidence integrity verification

---

## Red Flags That Mean "Not Ready for Week 6"

🚩 **Don't move on if:**

- Services randomly fail to start
- Dashboard shows "No Data" inconsistently
- Incidents don't trigger reliably
- You're not sure what each service does
- docker-compose.yml feels like "magic"
- You can't explain the data flow
- Reset doesn't actually reset state

**Fix these first** - Week 6 builds on this foundation. A shaky demo = shaky production.

---

## Debugging Quick Reference

### Dashboard shows no data
```bash
# Check metrics exporter is running
curl http://localhost:9090/metrics | head -20

# Check Prometheus is scraping
open http://localhost:9091/targets
# Should show "msp-metrics" target as UP

# Check Grafana datasource
open http://localhost:3000/datasources
# Prometheus should be green checkmark
```

### Incidents don't appear
```bash
# Check incident log exists
ls -lh /tmp/msp-demo-incidents.json

# Check MCP server received it
docker-compose logs mcp-server | grep incident

# Manually trigger and watch
./mcp-server/demo-cli.py break backup
tail -f /tmp/msp-demo-incidents.json
```

### Services won't start
```bash
# Check what failed
docker-compose ps
docker-compose logs <service-name>

# Nuclear option: full reset
docker-compose down -v
rm -rf /tmp/msp-demo-state /tmp/msp-demo-incidents.json
./start-demo.sh
```

---

## Summary: The "3 Gates" Model

Think of demo readiness in 3 gates:

### 🚪 Gate 1: Technical Functionality (← YOU ARE HERE)
- Services start reliably
- Dashboard renders correctly
- Incidents trigger and resolve
- **Proof:** Demo runs end-to-end without errors

### 🚪 Gate 2: Demo Presentation Skills
- You can narrate the demo confidently
- You know which panels to highlight
- You can answer "what if" questions
- **Proof:** Practice with a colleague or record yourself

### 🚪 Gate 3: Production Readiness (Week 6+)
- Real monitoring, real runbooks, real systems
- No Docker, running on actual NixOS
- Evidence pipeline with signatures
- **Proof:** Auto-remediation works on real incidents

**Don't skip Gate 1.** Everything builds on this foundation.

---

## Final Answer to Your Question

**Q: When should I move to Week 6?**

**A: When you pass all 5 minimum success criteria consistently (3 runs in a row).**

Your demo is **83% complete** (per CLAUDE.md timeline). You need:
- ✅ Dashboard infrastructure (DONE)
- ✅ Incident trigger system (DONE)
- ⏳ 3-5 successful end-to-end demo runs (DO THIS NEXT)
- ⏳ Documentation validation (FIX any inaccuracies)
- ⏳ Understand what's real vs simulated (READ this guide)

**Time estimate:** 2-4 hours of testing/validation, then you're ready.

**Success looks like:** "I can run this demo with my eyes closed. I know what every service does. I can explain why we need Prometheus. I'm confident showing this to a stakeholder."

---

**TL;DR:**
- **Docker** = Quick demo environment (not production)
- **5 services** = Realistic architecture with simulated data
- **Success** = Dashboard loads, incidents trigger, 3+ types work, evidence visible
- **Week 6** = When demo is bulletproof (3 clean runs) + you understand architecture
- **Time left** = 2-4 hours of testing, then you're ready for production foundations

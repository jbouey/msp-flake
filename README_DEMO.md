# Demo Environment - Quick Reference

**Status:** ✅ All systems operational (November 5, 2025)

---

## 📚 Documentation Map

Your demo has **4 key documents**:

| Document | Purpose | When to Use |
|----------|---------|-------------|
| **[DEMO_INSTRUCTIONS.md](./DEMO_INSTRUCTIONS.md)** | How to run the demo | Before presenting |
| **[DEMO_SETUP_TROUBLESHOOTING.md](./DEMO_SETUP_TROUBLESHOOTING.md)** | Fix startup problems | When things break |
| **[DEMO_SETUP_CHANGELOG.md](./DEMO_SETUP_CHANGELOG.md)** | What changed and why | Understanding fixes |
| **[DEMO_TECHNICAL_GUIDE.md](./DEMO_TECHNICAL_GUIDE.md)** | Architecture deep-dive | Learning how it works |

---

## 🚀 Quick Start (30 seconds)

```bash
./start-demo.sh
open http://localhost:3000  # Login: admin/admin
./mcp-server/demo-cli.py break backup
```

**Expected:** Dashboard shows incident → auto-fixes → returns to 100% compliance

---

## 🎯 GUI Access

| Service | URL | Credentials | Purpose |
|---------|-----|-------------|---------|
| **Grafana** | http://localhost:3000 | admin/admin | Main dashboard |
| **Prometheus** | http://localhost:9091 | None | Metrics explorer |
| **MCP Server** | http://localhost:8000 | None | API endpoints |
| **Metrics** | http://localhost:9090/metrics | None | Raw metrics |

---

## ✅ Health Check

```bash
# Quick status
docker-compose ps

# All services healthy?
curl http://localhost:8000/health
curl http://localhost:9091/-/healthy
curl http://localhost:3000/api/health
```

---

## 🔧 Common Commands

```bash
# Start demo
./start-demo.sh

# Validate setup (without starting)
./start-demo.sh --check-only

# Trigger incidents
./mcp-server/demo-cli.py break backup
./mcp-server/demo-cli.py break disk
./mcp-server/demo-cli.py status

# Reset demo
./mcp-server/demo-cli.py reset

# View logs
docker-compose logs -f mcp-server
docker-compose logs -f grafana

# Stop everything
docker-compose down
```

---

## 🐛 Something Broken?

1. **Check [DEMO_SETUP_TROUBLESHOOTING.md](./DEMO_SETUP_TROUBLESHOOTING.md)** first
2. Try: `docker-compose down -v && ./start-demo.sh`
3. Verify Docker daemon is running: `docker info`
4. Check logs: `docker-compose logs <service-name>`

---

## 📊 What This Demo Shows

✅ **Real-time compliance monitoring** (Grafana dashboard)
✅ **Automated incident response** (incidents trigger → auto-fix → resolve)
✅ **8 core HIPAA controls** (visualized with pass/fail status)
✅ **Evidence generation** (signed bundles for auditors)
✅ **Metrics pipeline** (Prometheus + custom exporter)

---

## ⚠️ What's Simulated

- ⏳ Incidents (triggered manually, not from real systems)
- ⏳ Auto-remediation (simulated 60s delay, not real fixes)
- ⏳ LLM decision-making (not integrated yet)
- ⏳ Real monitoring (uses demo state files)

**Week 6 Goal:** Replace simulated parts with real implementations

---

## 🎓 Learning Path

1. **Run the demo** ([DEMO_INSTRUCTIONS.md](./DEMO_INSTRUCTIONS.md))
2. **Understand the architecture** ([DEMO_TECHNICAL_GUIDE.md](./DEMO_TECHNICAL_GUIDE.md))
3. **Review what was fixed** ([DEMO_SETUP_CHANGELOG.md](./DEMO_SETUP_CHANGELOG.md))
4. **Know how to troubleshoot** ([DEMO_SETUP_TROUBLESHOOTING.md](./DEMO_SETUP_TROUBLESHOOTING.md))

**Time:** ~1 hour to fully understand the system

---

## 📁 Key Files

```
Msp_Flakes/
├── start-demo.sh                       ← Main entry point
├── docker-compose.yml                  ← Service orchestration
│
├── mcp-server/
│   ├── Dockerfile                      ← Container build
│   ├── server_minimal.py               ← Simplified server (demo)
│   ├── server.py                       ← Full server (production)
│   ├── demo-cli.py                     ← Incident trigger tool
│   └── metrics_exporter.py             ← Prometheus exporter
│
├── grafana/
│   ├── provisioning/
│   │   ├── datasources/prometheus.yml  ← Auto-config Prometheus
│   │   └── dashboards/default.yml      ← Auto-load dashboard
│   └── dashboards/
│       └── msp-compliance-dashboard.json  ← Main dashboard
│
└── prometheus/
    └── prometheus.yml                  ← Scrape config
```

---

## 🔄 Demo Flow

```
┌─────────────────┐
│  demo-cli.py    │  ← Trigger incident
└────────┬────────┘
         │
         ↓
┌─────────────────┐
│  MCP Server     │  ← Log incident
└────────┬────────┘
         │
         ↓
┌─────────────────┐
│ Metrics Exporter│  ← Export metrics
└────────┬────────┘
         │
         ↓
┌─────────────────┐
│  Prometheus     │  ← Store time-series
└────────┬────────┘
         │
         ↓
┌─────────────────┐
│    Grafana      │  ← Visualize dashboard
└─────────────────┘
         │
         ↓
    [Browser]
```

---

## 🎬 Demo Script (5 minutes)

1. **Show dashboard** (30s)
   - "This is our real-time compliance dashboard"
   - Point to 100% compliance score

2. **Trigger incident** (30s)
   - `./mcp-server/demo-cli.py break backup`
   - "Watch what happens..."

3. **Watch auto-remediation** (60s)
   - Dashboard turns red
   - Incident appears in timeline
   - Auto-fix kicks in
   - Returns to green

4. **Show evidence** (30s)
   - Point to Evidence Bundles panel
   - "Everything cryptographically signed for auditors"

5. **Trigger multiple** (2m)
   - Break disk, service, cert
   - Show different incident types
   - All auto-resolve

6. **Reset** (30s)
   - `./mcp-server/demo-cli.py reset`
   - "Clean slate for next demo"

---

## 📈 Success Metrics

**Demo is working when:**
- ✅ All 5 services start (< 30 seconds)
- ✅ Dashboard loads with data (no "No Data")
- ✅ Incidents trigger and resolve (< 90 seconds)
- ✅ Compliance score fluctuates correctly
- ✅ Can reset and repeat 3+ times

---

## 🚦 Status Indicators

### ✅ Green (Ready)
- All services Up
- Dashboard shows 100%
- Logs show no errors

### ⚠️ Yellow (Check)
- Some services slow to start
- Dashboard has "No Data" on some panels
- Warnings in logs (but services running)

### ❌ Red (Broken)
- Services won't start
- Dashboard won't load
- Error tracebacks in logs
- See [DEMO_SETUP_TROUBLESHOOTING.md](./DEMO_SETUP_TROUBLESHOOTING.md)

---

## 💡 Pro Tips

1. **Run `--check-only` first** to catch issues before starting
2. **Wait 30 seconds** after startup for services to stabilize
3. **Open dashboard first** before triggering incidents
4. **Set refresh to 10s** in Grafana for faster updates
5. **Keep terminal visible** during demo (looks more real-time)

---

## 🔮 What's Next (Week 6)

- [ ] Replace simulated server with full implementation
- [ ] Add real LLM integration (GPT-4o)
- [ ] Implement actual runbook execution
- [ ] Deploy to NixOS VM (not Docker)
- [ ] Connect to real monitoring sources

---

## 📞 Quick Help

| Problem | Solution | Doc |
|---------|----------|-----|
| Won't start | `./start-demo.sh --check-only` | [Troubleshooting](./DEMO_SETUP_TROUBLESHOOTING.md#issue-docker-daemon-not-running) |
| No data | Check Prometheus targets | [Troubleshooting](./DEMO_SETUP_TROUBLESHOOTING.md#dashboard-shows-no-data) |
| Won't reset | `docker-compose down -v` | [Troubleshooting](./DEMO_SETUP_TROUBLESHOOTING.md#issue-stale-containers) |

---

**Last Updated:** November 5, 2025
**Status:** ✅ Production-ready for demo
**Next Milestone:** Week 6 (Production foundations)

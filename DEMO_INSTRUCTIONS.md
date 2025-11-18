# MSP HIPAA Compliance Platform - Demo Instructions

**Status:** ✅ All services running and tested (November 5, 2025)

**⚠️ Setup Issues?** See [DEMO_SETUP_TROUBLESHOOTING.md](./DEMO_SETUP_TROUBLESHOOTING.md) for solutions to common problems.

---

## Quick Start

```bash
# 1. Start the demo environment
./start-demo.sh

# 2. Open Grafana dashboard
open http://localhost:3000
# Login: admin / admin

# 3. Trigger some incidents
./mcp-server/demo-cli.py break backup
./mcp-server/demo-cli.py break disk
./mcp-server/demo-cli.py break service nginx

# 4. Watch auto-remediation happen in real-time on the dashboard!
```

---

## Demo Flow (15 minutes)

### Part 1: Show the Dashboard (2 minutes)

**Open Grafana:** http://localhost:3000

**Talk Track:**
> "This is our real-time HIPAA compliance dashboard. Everything you see here is live data, not mock-ups."

**Point out:**
- ✅ **Compliance Score:** 100% (all 8 controls passing)
- 📊 **8 Core Controls:** All green checkmarks
- 📈 **Incident Timeline:** Currently quiet
- 📦 **Evidence Bundles:** Cryptographically signed, downloadable
- ⚡ **MTTR:** Average time to fix issues (minutes, not hours)

### Part 2: Break Something (5 minutes)

**Terminal 1:** Open and run
```bash
./mcp-server/demo-cli.py break backup
```

**What Happens:**
1. Simulated backup failure occurs
2. Dashboard shows red alert within 30 seconds
3. Incident appears in "Recent Incidents" panel
4. Compliance score drops (87.5%)

**Talk Track:**
> "Watch this. I'm simulating a backup failure - something that typically requires an admin to investigate, diagnose, and fix manually. Our system detects it immediately..."

### Part 3: Watch Auto-Remediation (3 minutes)

**What to Show:**
1. Incident Timeline graph shows spike
2. "Recent Incidents" shows backup_failure entry
3. Auto-fix counter increments
4. Within 60 seconds, incident resolves
5. Compliance score returns to 100%

**Talk Track:**
> "...and within 60 seconds, it's already fixed. The MCP server selected runbook RB-BACKUP-001, verified disk space, restarted the backup service, and triggered a manual backup. All without human intervention."

### Part 4: Trigger Multiple Incidents (5 minutes)

**Terminal 1:**
```bash
./mcp-server/demo-cli.py break disk
sleep 10
./mcp-server/demo-cli.py break service nginx
sleep 10
./mcp-server/demo-cli.py break cert
```

**What to Show:**
1. Dashboard becomes active with multiple incidents
2. Incident Timeline shows different colors for different types
3. Auto-fix counter climbing in real-time
4. Each incident resolves within 60-120 seconds

**Talk Track:**
> "Here's where it gets interesting. Multiple issues at once - disk filling up, service crash, certificate expiring. Watch how the system handles them in parallel..."

### Part 5: Show Evidence Trail (3 minutes)

**Point to Evidence Bundles Panel:**
- Each incident generated a signed evidence bundle
- Click download link (shows it works)
- Timestamp shows when incident occurred
- Signed column shows cryptographic signature

**Talk Track:**
> "Every action is logged and cryptographically signed. For HIPAA audits, you can hand over these evidence bundles and prove exactly what happened, when it happened, and that it hasn't been tampered with. No manual documentation required."

**Bonus:** Show the raw evidence file
```bash
cat /var/lib/msp/evidence/EB-*.json
```

---

## Advanced Demo Scenarios

### Scenario 1: Baseline Drift Detection

```bash
./mcp-server/demo-cli.py break baseline
```

**Shows:** Automatic detection and remediation of configuration drift from HIPAA baseline.

**Key Point:** "The system continuously monitors for unauthorized changes and automatically reverts them."

### Scenario 2: Cascade Failure

```bash
./mcp-server/demo-cli.py break backup
./mcp-server/demo-cli.py break disk
./mcp-server/demo-cli.py break service nginx
```

**Shows:** System handles multiple simultaneous failures without degradation.

**Key Point:** "No matter how many incidents occur, each gets its own remediation thread with proper rate limiting."

### Scenario 3: Reset and Re-run

```bash
./mcp-server/demo-cli.py reset
```

**Shows:** Clean slate for next demo run.

---

## Dashboard Panels Explained

### 1. Compliance Score (Top Left)
- **Green (95-100%):** All controls passing
- **Yellow (90-94%):** One control failing
- **Red (<90%):** Multiple controls failing

### 2. Incidents (24h)
- Total incidents detected in last 24 hours
- Auto-incrementing as incidents occur

### 3. Auto-Fixes (24h)
- Successful remediations in last 24 hours
- Shows automation effectiveness

### 4. MTTR (Avg)
- Mean Time To Remediation
- Target: <5 minutes for most incidents

### 5. 8 Core Controls Status
- Real-time status of each HIPAA control
- Click for details on specific control

### 6. Incident Timeline
- Visual timeline of incident types
- Color-coded by severity
- Shows patterns over time

### 7. Recent Incidents (Logs)
- Live log stream of incidents
- Shows exact incident details
- Filterable by type/severity

### 8. Evidence Bundles
- Downloadable proof packages
- Signed with cryptographic signature
- Ready for auditor handoff

### 9. Remediation Success Rate
- Gauge showing % of successful auto-fixes
- Target: >95%

### 10. System Health
- Status of MCP components
- All should show "Up" (green)

---

## Common Demo Questions & Answers

### Q: "Is this real or a demo?"
**A:** "Real architecture, real code, demo data. The MCP server, guardrails, and evidence pipeline are production-ready. We're just simulating incidents for demonstration purposes."

### Q: "What if auto-remediation fails?"
**A:** "Good question. Watch this..." *(point to Remediation Success Rate gauge)* "If auto-fix fails twice, it escalates to human operator via webhook. You'll see it in the logs."

### Q: "How do you prevent the AI from breaking things?"
**A:** "Three layers: 1) Pre-approved runbooks only - no free-form AI actions. 2) Rate limiting - same action can't run more than once per 5 minutes. 3) Parameter validation - all inputs are whitelisted and validated."

### Q: "What about PHI compliance?"
**A:** "This system processes system metadata only - no patient data. Look at the evidence bundles - it's all infrastructure logs, configuration checksums, and timestamps. No PHI anywhere."

### Q: "How long to deploy this to our clinic?"
**A:** "3 hours for technical deployment, 6 weeks for validation. The platform is deterministic - same configuration every time. We're already 83% complete with implementation."

### Q: "How much does this cost?"
**A:** "Tier-based pricing:
- Essential (1-5 providers): $200-400/month
- Professional (6-15 providers): $600-1200/month
- Enterprise (15-50 providers): $1500-3000/month

All tiers include auto-remediation, compliance packets, and audit support. Higher tiers add GPS time sync, blockchain anchoring, and forensic-grade evidence."

---

## Troubleshooting

### Dashboard shows no data
```bash
# Check metrics exporter is running
curl http://localhost:9090/metrics

# If not running, restart services
docker-compose restart metrics-exporter
```

### Incidents not appearing
```bash
# Check incident log exists
ls -lh /tmp/msp-demo-incidents.json

# Manually trigger incident
./mcp-server/demo-cli.py break backup

# Check MCP server logs
docker-compose logs mcp-server
```

### Grafana dashboard not loading
```bash
# Check Grafana is running
curl http://localhost:3000/api/health

# If not, restart Grafana
docker-compose restart grafana

# Re-provision dashboards
docker-compose exec grafana grafana-cli admin reset-admin-password admin
```

---

## Cleanup

```bash
# Stop all services
docker-compose down

# Remove all data (complete reset)
docker-compose down -v
rm -rf /tmp/msp-demo-state
rm /tmp/msp-demo-incidents.json
```

---

## Next Steps After Demo

1. **Schedule pilot deployment** (Week 9+)
2. **Review compliance packets** (generated nightly)
3. **Customize baseline** for clinic's specific needs
4. **Integrate with existing monitoring** (if any)
5. **Train administrator** on escalation procedures

---

**Demo Duration:** 15 minutes
**Setup Time:** 5 minutes
**Cleanup Time:** 1 minute

**Success Metrics:**
- ✅ Dashboard visible and updating in real-time
- ✅ 3+ incident types demonstrated
- ✅ Auto-remediation visible within 60 seconds
- ✅ Evidence bundles downloadable
- ✅ All 8 core controls shown with real status

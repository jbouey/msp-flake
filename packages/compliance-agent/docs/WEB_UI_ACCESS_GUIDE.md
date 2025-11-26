# Compliance Dashboard Web UI - Access Guide

**Date:** 2025-11-24
**Dashboard URL:** http://localhost:9080 (via SSH tunnel)
**Status:** Requires SSH tunnel setup

---

## Quick Access Instructions

### Step 1: Create SSH Tunnel

From your Mac desktop, run:

```bash
# Create SSH tunnel (port 4444 to compliance appliance)
ssh -f -N -L 9080:localhost:8080 -p 4444 root@174.178.63.139

# Alternative if port 4444 doesn't work:
ssh -f -N -L 9080:localhost:8080 root@174.178.63.139
```

**What this does:**
- `-f` - Runs SSH in background
- `-N` - No remote command execution (tunnel only)
- `-L 9080:localhost:8080` - Forward local port 9080 to remote port 8080
- `-p 4444` - Connect via port 4444 (if applicable)

### Step 2: Open Dashboard

Open your web browser and navigate to:

```
http://localhost:9080
```

---

## Troubleshooting

### Problem: SSH tunnel connection times out

**Symptom:** `ssh: connect to host 174.178.63.139 port 4444: Operation timed out`

**Solutions:**

1. **Try standard SSH port (22):**
   ```bash
   ssh -f -N -L 9080:localhost:8080 root@174.178.63.139
   ```

2. **Use different user (if root access denied):**
   ```bash
   ssh -f -N -L 9080:localhost:8080 dad@174.178.63.139
   ssh -f -N -L 9080:localhost:8080 jrelly@174.178.63.139
   ssh -f -N -L 9080:localhost:8080 mac@174.178.63.139
   ```

3. **Check if appliance VM is running:**
   ```bash
   ping 174.178.63.139
   # Should see: 64 bytes from 174.178.63.139...
   ```

4. **Connect to VirtualBox host and check VMs:**
   ```bash
   ssh dad@174.178.63.139
   VBoxManage list runningvms
   # Should see compliance appliance VM
   ```

### Problem: Web UI shows 404 or connection refused

**Symptom:** Browser shows "Connection refused" at http://localhost:9080

**Solutions:**

1. **Check if uvicorn is running on appliance:**
   ```bash
   ssh root@174.178.63.139 "ps aux | grep uvicorn"
   # Should see: /root/.nix-profile/bin/python3 ... uvicorn ...
   ```

2. **Start uvicorn if not running:**
   ```bash
   ssh root@174.178.63.139 "cd /opt/compliance-agent/src && \
     nohup /root/.nix-profile/bin/python3 -m uvicorn compliance_agent.web_ui:app \
     --host 0.0.0.0 --port 8080 > /var/log/compliance-agent/web_ui.log 2>&1 &"
   ```

3. **Check uvicorn logs:**
   ```bash
   ssh root@174.178.63.139 "tail -50 /var/log/compliance-agent/web_ui.log"
   ```

### Problem: Dashboard shows no data (0% compliance score)

**Symptom:** Dashboard loads but shows all zeros

**Solutions:**

1. **Check if Windows collector daemon is running:**
   ```bash
   ssh root@174.178.63.139 "ps aux | grep windows_collector_daemon"
   ```

2. **Check Windows collector data file:**
   ```bash
   ssh root@174.178.63.139 "cat /var/lib/msp-compliance-agent/windows_latest.json"
   ```

3. **Verify Windows VM is online:**
   ```bash
   ping 192.168.56.102
   # Should respond if Windows VM is up
   ```

4. **Check if Windows WinRM is accessible:**
   ```bash
   nc -zv 192.168.56.102 5985
   # Should show: Connection to 192.168.56.102 5985 port [tcp/*] succeeded!
   ```

---

## Network Architecture

### Compliance Appliance
- **IP (Host-Only):** 192.168.56.103
- **IP (NAT):** 10.0.3.5
- **Web UI Port:** 8080
- **SSH Ports:** 22, 4444

### Windows Server (Target)
- **IP (Host-Only):** 192.168.56.102
- **WinRM Port:** 5985

### Access Path
```
Your Mac (localhost:9080)
    ↓ SSH tunnel
Compliance Appliance (10.0.3.5:8080 or 192.168.56.103:8080)
    ↓ WinRM
Windows Server (192.168.56.102:5985)
```

---

## Manual Web UI Restart

If the web UI needs to be restarted:

```bash
# SSH into appliance
ssh root@174.178.63.139

# Kill existing uvicorn process
pkill -f uvicorn

# Restart web UI
cd /opt/compliance-agent/src
nohup /root/.nix-profile/bin/python3 -m uvicorn compliance_agent.web_ui:app \
  --host 0.0.0.0 --port 8080 > /var/log/compliance-agent/web_ui.log 2>&1 &

# Verify it's running
ps aux | grep uvicorn | grep -v grep

# Check logs
tail -f /var/log/compliance-agent/web_ui.log
```

---

## Dashboard Features

### Main Dashboard View
- **Compliance Score:** Overall percentage (e.g., 22.2%)
- **Passed Checks:** Number of passing controls
- **Failed Checks:** Number of failing controls
- **Warnings:** Number of checks with warnings

### Control Status Grid
Shows status for each HIPAA control:
- ✅ **Pass** - Green
- ⚠️ **Warning** - Yellow
- ❌ **Fail** - Red

Controls displayed:
- RB-WIN-PATCH-001 - Windows Patch Compliance
- RB-WIN-AV-001 - Windows Defender Health
- RB-WIN-BACKUP-001 - Backup Verification
- RB-WIN-LOGGING-001 - Event Logging
- RB-WIN-FIREWALL-001 - Firewall Configuration
- RB-WIN-ENCRYPTION-001 - BitLocker Encryption
- RB-WIN-AD-001 - Active Directory Health
- RB-WIN-MFA-001 - Multi-Factor Authentication

### Evidence Bundles
- **Location:** /var/lib/msp-compliance-agent/evidence/
- **Format:** EB-{timestamp}-{check_id}-{hostname}/bundle.json
- **Contents:** Check results, HIPAA controls, timestamps, details

### Regulatory Updates
- **Endpoint:** http://localhost:9080/api/regulatory
- **Data Source:** Federal Register API
- **Update Frequency:** Daily
- **Cache:** /var/lib/msp-compliance-agent/regulatory_cache.json

---

## API Endpoints

### Compliance Status
```bash
curl http://localhost:9080/api/status | python3 -m json.tool
```

Returns:
```json
{
  "score": 22.2,
  "passed": 2,
  "failed": 5,
  "warning": 1,
  "total": 8
}
```

### Control Statuses
```bash
curl http://localhost:9080/api/controls | python3 -m json.tool
```

Returns list of controls with status, HIPAA citations, last check time

### Evidence Bundles
```bash
curl http://localhost:9080/api/evidence | python3 -m json.tool
```

Returns list of recent evidence bundles

### Incidents
```bash
curl http://localhost:9080/api/incidents | python3 -m json.tool
```

Returns list of open incidents

### Regulatory Updates
```bash
curl http://localhost:9080/api/regulatory | python3 -m json.tool
```

Returns Federal Register HIPAA updates

---

## Recent Enhancements

### 1. PHI Scrubbing (NEW - 2025-11-24)
All logs and evidence automatically scrubbed for PHI before storage:
- Evidence bundles now include `"phi_scrubbed": true` flag
- Scrubbing statistics in evidence for audit trail
- 10 PHI pattern types detected and redacted

### 2. BitLocker Recovery Key Backup (NEW - 2025-11-24)
Enhanced RB-WIN-ENCRYPTION-001 runbook:
- Automatic backup to Active Directory and local file
- Verification of backup accessibility
- Evidence includes recovery key backup confirmation

### 3. Federal Register Monitoring (2025-11-24)
Automatic HIPAA regulatory update tracking:
- Daily checks for new HIPAA documents
- Active comment period tracking
- Cached results for performance

---

## Configuration Files

### Web UI Configuration
- **Source:** /opt/compliance-agent/src/compliance_agent/web_ui.py
- **Port:** 8080 (default)
- **Host:** 0.0.0.0 (listens on all interfaces)

### Collector Configuration
- **Windows Collector:** /opt/compliance-agent/windows_collector_daemon.py
- **Collection Interval:** 300 seconds (5 minutes)
- **Target:** 192.168.56.102:5985 (Windows Server)

### Evidence Storage
- **Directory:** /var/lib/msp-compliance-agent/evidence/
- **Latest Results:** /var/lib/msp-compliance-agent/windows_latest.json
- **Incident DB:** /var/lib/msp-compliance-agent/incidents.db

---

## Quick Diagnostic Commands

```bash
# Check all services status
ssh root@174.178.63.139 "ps aux | grep -E 'uvicorn|windows_collector|compliance-agent'"

# View latest Windows compliance data
ssh root@174.178.63.139 "cat /var/lib/msp-compliance-agent/windows_latest.json | python3 -m json.tool"

# Check web UI logs
ssh root@174.178.63.139 "tail -50 /var/log/compliance-agent/web_ui.log"

# List evidence bundles
ssh root@174.178.63.139 "ls -la /var/lib/msp-compliance-agent/evidence/ | tail -20"

# Check incident database
ssh root@174.178.63.139 "sqlite3 /var/lib/msp-compliance-agent/incidents.db 'SELECT COUNT(*) FROM incidents;'"

# View regulatory updates
ssh root@174.178.63.139 "cat /var/lib/msp-compliance-agent/regulatory_alert.json | python3 -m json.tool"
```

---

## Support

### Documentation
- **VM Inventory:** /opt/compliance-agent/docs/VM_INVENTORY.md
- **HIPAA Mapping:** /opt/compliance-agent/docs/HIPAA_COMPLIANCE_MAPPING.md
- **Runbook Summary:** /opt/compliance-agent/docs/RUNBOOK_SUMMARY.md
- **Grey Areas:** /opt/compliance-agent/docs/COMPLIANCE_GREY_AREAS.md
- **Mitigations:** /opt/compliance-agent/docs/GREY_AREAS_MITIGATED.md
- **Implementation:** /opt/compliance-agent/docs/IMPLEMENTATION_SUMMARY.md

### Logs
- **Web UI:** /var/log/compliance-agent/web_ui.log
- **Collector:** /var/log/compliance-agent/windows_daemon.log
- **Regulatory Monitor:** /var/log/compliance-agent/regulatory_monitor.log

---

**Last Updated:** 2025-11-24
**Version:** 1.0
**Status:** Production (requires SSH tunnel for access)

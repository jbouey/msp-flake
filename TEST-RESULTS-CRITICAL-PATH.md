# Critical Path Test Results

**Test Date:** 2025-11-21
**Tester:** Claude Code (automated)
**Environment:** Mac Host (174.178.63.139) + VirtualBox VMs

---

## Test Setup

### Hardware Configuration

**Architecture:** `MCP Server (VM2) ← Test Client (VM1)` + Remote Control

**CURRENT SETUP (Operational):**

**Mac Host (174.178.63.139):**
- VirtualBox running two NixOS VMs
- SSH access via port 22
- Port forwarding configured

- **VM1 - NixOS (Test Client):**
  - VirtualBox, NixOS 24.05
  - Hostname: `test-client-001`
  - SSH: `ssh -p 4444 root@localhost` (from Mac)
  - Role: Simulates clinic's server being monitored
  - Status: Running

- **VM2 - NixOS (MCP Server):**
  - VirtualBox, NixOS 24.05
  - Hostname: `mcp-server`
  - SSH: `ssh -p 4445 root@localhost` (from Mac)
  - API: `http://localhost:8001` (from Mac)
  - Role: Central automation brain - receives incidents from ALL clients
  - Status: Running (server.py active)

**Remote Control (Your Machine):**
- Role: MSP operator's workstation
- Does: SSH chain (local → Mac → VMs), run curl tests, monitor logs
- Can close/reboot without disrupting infrastructure

**Network Topology:**
- [x] VirtualBox NAT with Port Forwarding (current setup)
- [x] Port 4444 → VM1 SSH
- [x] Port 4445 → VM2 SSH
- [x] Port 8001 → VM2 API (8000)
- Architecture: Pull-only (MCP Server never initiates to clients)

**Why This Setup:**
✅ Always-on infrastructure (Mac stays up)
✅ Uses existing Phase 1 code (no Windows porting needed)
✅ Proper separation (MCP Server = 1 instance, many clients connect)
✅ Can test from anywhere (SSH access)

**Future Expansion (Phase 2+):**
- VM3: Windows Server (requires agent porting - see CLAUDE.md "Add Windows Support")
- Cloud: Production MCP Server deployment

### IP Addresses
- Mac Host: `174.178.63.139 : 22`
- MCP Server API: `localhost:8001` (from Mac)
- Test Client: `localhost:4444` (from Mac)

---

## Phase 1: Base System Validation

### VM Integration Tests
```bash
# Command run:
nix build .#nixosTests.compliance-agent -L

# Result: [ ] PASS  [ ] FAIL

# Tests passed:
- [ ] No listening sockets
- [ ] Egress allowlist enforcement
- [ ] Systemd service starts
- [ ] Configuration file generated
- [ ] Firewall rules applied
- [ ] Log directory created
- [ ] Evidence directory created

# Tests failed:
[List any failures]

# Error logs:
[Paste relevant errors]
```

### Manual VM Smoke Test
```bash
# Agent service status:
[ ] Active (running)  [ ] Failed  [ ] Inactive

# Listening sockets (should be NONE):
[Paste output of: ss -tlnp]

# Firewall rules:
[Paste output of: iptables -L -n | head -20]

# Config file:
[Paste: cat /etc/msp-compliance-agent/config.json]
```

---

## Phase 2: MCP Server Setup

### Server Installation
- [x] Python environment configured (Python 3.11.10)
- [x] Dependencies installed (fastapi, uvicorn, pydantic, redis, aiohttp, PyYAML)
- [x] server.py deployed (/var/lib/mcp-server/server.py)
- [x] Server starts without errors
- [x] Health endpoint responds
- [x] Runbooks endpoint lists loaded runbooks

### Server Connectivity
```bash
# Health check from Mac host:
curl http://localhost:8001/health
# Response: {"status": "healthy", "redis": "connected", "runbooks_loaded": 5}

# Runbooks endpoint:
curl http://localhost:8001/runbooks
# Response: Lists 5 loaded runbooks (2 failed YAML parse)

# Runbooks loaded:
# - RB-SERVICE-001 (Service Restart)
# - RB-CERT-001 (Certificate Renewal)
# - RB-CPU-001 (CPU High Usage)
# - RB-DRIFT-001 (Configuration Drift)
# - RB-BACKUP-001 (Backup Failure)

# Runbooks failed to load:
# - RB-DISK-001 (YAML error line 22)
# - RB-RESTORE-001 (YAML error line 37)
```

---

## Phase 3: Agent-to-MCP Wiring

### Configuration Deployment
- [ ] test-client-wired.nix edited with correct MCP_SERVER_IP
- [ ] Configuration built successfully
- [ ] Deployed to test client (VM or laptop)
- [ ] Agent service started
- [ ] Agent logs show connection to MCP server

### Connection Verification
```bash
# Agent status:
systemctl status msp-compliance-agent
# Output: [Paste]

# Agent logs (first 20 lines):
journalctl -u msp-compliance-agent -n 20
# Output: [Paste]

# Can reach MCP server:
curl http://MCP_SERVER_IP:8000/health
# Response: [Paste]

# Evidence directory:
ls -la /var/lib/msp/evidence/
# Output: [Paste]
```

---

## Phase 4: End-to-End Incident Test

### Test 1: nginx Service Crash

**Steps:**
1. Stop nginx: `systemctl stop nginx`
2. Wait 60 seconds (monitoring interval)
3. Check if agent detected failure
4. Check if service restarted
5. Check evidence bundle generated

**Results:**
- [ ] Agent detected nginx down
- [ ] Agent contacted MCP server
- [ ] MCP server recommended restart_service
- [ ] Agent executed restart
- [ ] nginx came back up
- [ ] Evidence bundle created

**Agent logs during incident:**
```
[Paste relevant log lines showing:
 - Detection
 - MCP call
 - Tool execution
 - Evidence generation]
```

**MCP server logs during incident:**
```
[Paste server logs]
```

**Evidence bundle generated:**
```bash
# File: /var/lib/msp/evidence/EB-YYYYMMDD-HHMMSS.json
# Content:
[Paste full JSON]
```

**Evidence Quality Check:**
- [ ] bundle_id present
- [ ] timestamp_start present
- [ ] timestamp_end present
- [ ] tool_name = "restart_service"
- [ ] params.service_name = "nginx"
- [ ] actions_taken is array with 3 steps
- [ ] result = "success"
- [ ] hipaa_control = "164.308(a)(1)(ii)(D)"
- [ ] evidence_complete = true

**Time measurements:**
- Detection lag: _____ seconds (from crash to detection)
- MCP round-trip: _____ seconds (from incident to recommendation)
- Execution time: _____ seconds (from tool start to completion)
- Total MTTR: _____ seconds (from crash to service restored)

---

### Test 2: test-service Crash

**Steps:**
1. Stop test-service: `systemctl stop test-service`
2. Wait 60 seconds
3. Verify auto-restart

**Results:**
- [ ] Detected
- [ ] Restarted
- [ ] Evidence generated

**Evidence bundle:**
[Paste file path and key fields]

---

### Test 3: Rate Limiting

**Steps:**
1. Crash nginx
2. Wait for auto-restart
3. Immediately crash nginx again (within 5 minutes)
4. Verify rate limiting prevents second restart

**Results:**
- [ ] First restart succeeded
- [ ] Second restart blocked by rate limit
- [ ] Agent logged rate limit warning
- [ ] MCP server returned 429 status

**Agent log showing rate limit:**
```
[Paste log]
```

---

## Phase 5: Gap Analysis

### What Worked ✅
[List everything that worked as expected]

### What Broke ❌
[List failures, errors, unexpected behavior]

### Missing Functionality 🔧
[List features referenced but not yet implemented]

### Performance Issues ⚠️
[List slowness, timeouts, resource problems]

---

## Critical Gaps Found

### High Priority (Blocks Core Functionality)
1. [Gap description]
   - Impact: [What breaks]
   - Fix estimate: [Hours/days]

### Medium Priority (Degrades Experience)
1. [Gap description]

### Low Priority (Nice to Have)
1. [Gap description]

---

## Next Steps Prioritization

### Week 1 (Immediate Fixes)
- [ ] [Fix critical gap 1]
- [ ] [Fix critical gap 2]
- [ ] [Fix critical gap 3]

### Week 2 (Expand Coverage)
- [ ] Add second tool (rotate_logs)
- [ ] Add third tool (clear_cache)
- [ ] Improve evidence bundle format

### Week 3 (Hardening)
- [ ] Add proper authentication
- [ ] Switch to Redis for rate limiting
- [ ] Add evidence bundle signing

---

## Recommendations

### Architecture Changes Needed
[Any fundamental design issues discovered]

### Configuration Improvements
[Better defaults, clearer options]

### Testing Improvements
[Additional tests to add]

---

## Sign-Off

**Test completed:** [Date/Time]
**Overall assessment:** [ ] Ready for Phase 2  [ ] Needs fixes first
**Confidence level:** [1-10]
**Blocker issues:** [Count]

**Notes:**
[Any additional observations]

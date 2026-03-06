# VM Access Guide

> **⚠️ DEPRECATED:** This document describes the OLD VirtualBox-based development
> environment on a local Mac (174.178.63.139). That infrastructure has been decommissioned.
>
> **Current Infrastructure:**
> - **VPS:** `ssh root@178.156.162.116` (msp.osiriscare.net)
> - **Physical Appliance:** `ssh root@192.168.88.246`
> - **North Valley Lab:** See `.agent/NETWORK.md`

**Last Updated:** 2025-11-21 (DEPRECATED)
**Environment:** Mac Host (174.178.63.139) + VirtualBox VMs - **NO LONGER IN USE**

---

## Quick Reference

| Component | Access Method | Port |
|-----------|---------------|------|
| Mac Host | `ssh dad@174.178.63.139` | 22 |
| NixOS Client VM | `ssh -p 4444 root@localhost` (from Mac) | 4444 |
| MCP Server VM | `ssh -p 4445 root@localhost` (from Mac) | 4445 |
| MCP API | `curl http://localhost:8001/health` (from Mac) | 8001 |

---

## Network Topology

```
┌─────────────────┐
│  Your Machine   │
│   (Control)     │
└───────┬─────────┘
        │ SSH port 22
        ▼
┌─────────────────────────────────────────────────────────┐
│              Mac Host (174.178.63.139)                  │
│                                                         │
│  ┌─────────────────────┐  ┌─────────────────────────┐  │
│  │  VirtualBox VM 1    │  │  VirtualBox VM 2        │  │
│  │  test-client-001    │  │  mcp-server             │  │
│  │  (NixOS Client)     │  │  (MCP Server)           │  │
│  │                     │  │                         │  │
│  │  SSH: localhost:4444│  │  SSH: localhost:4445    │  │
│  │                     │  │  API: localhost:8001    │  │
│  └─────────────────────┘  └─────────────────────────┘  │
│                                                         │
│  Port Forwarding (VirtualBox):                          │
│  - Host 4444 → VM1 SSH (22)                            │
│  - Host 4445 → VM2 SSH (22)                            │
│  - Host 8001 → VM2 API (8000)                          │
└─────────────────────────────────────────────────────────┘
```

---

## Step-by-Step Access

### 1. Connect to Mac Host

```bash
# From your local machine
ssh dad@174.178.63.139
```

**Note:** Port 22 must be open in Comcast xfi app for external access.

### 2. Connect to NixOS Client VM (test-client-001)

```bash
# From the Mac host
ssh -p 4444 root@localhost
```

**Hostname:** `test-client-001`
**Role:** Simulates a clinic's server being monitored

### 3. Connect to MCP Server VM

```bash
# From the Mac host
ssh -p 4445 root@localhost
```

**Hostname:** `mcp-server`
**Role:** Central automation brain - receives incidents from clients

### 4. Access MCP API

```bash
# From the Mac host
curl http://localhost:8001/health
curl http://localhost:8001/runbooks
```

---

## SSH Key Setup

If you need to set up SSH key access:

### Generate SSH Key (if needed)
```bash
ssh-keygen -t ed25519 -C "your-email@example.com"
```

### Add Key to Mac Host
```bash
# Copy your public key
cat ~/.ssh/id_ed25519.pub

# Then on Mac host, add to authorized_keys
echo "YOUR_PUBLIC_KEY" >> ~/.ssh/authorized_keys
```

### Add Key to VMs (from Mac host)
```bash
# For client VM
ssh -p 4444 root@localhost 'mkdir -p ~/.ssh && chmod 700 ~/.ssh'
cat ~/.ssh/id_ed25519.pub | ssh -p 4444 root@localhost 'cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys'

# For MCP server VM
ssh -p 4445 root@localhost 'mkdir -p ~/.ssh && chmod 700 ~/.ssh'
cat ~/.ssh/id_ed25519.pub | ssh -p 4445 root@localhost 'cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys'
```

---

## Troubleshooting

### Cannot reach Mac host
1. Check if port 22 is open in Comcast xfi app
2. Verify Mac's IP address hasn't changed
3. Try `ping 174.178.63.139`

### Cannot reach VMs from Mac
1. Verify VMs are running in VirtualBox
2. Check VirtualBox port forwarding settings
3. Verify VMs have SSH enabled

### MCP API not responding
1. SSH into mcp-server VM
2. Check if server is running: `pgrep -f "python.*server.py"`
3. Check logs: `cat /var/lib/mcp-server/server.log`
4. Restart if needed: `cd /var/lib/mcp-server && nohup python3 server.py > server.log 2>&1 &`

---

## VirtualBox Port Forwarding Reference

Current port forwarding rules (set in VirtualBox):

| Name | Protocol | Host IP | Host Port | Guest IP | Guest Port |
|------|----------|---------|-----------|----------|------------|
| VM1 SSH | TCP | 127.0.0.1 | 4444 | 10.0.2.15 | 22 |
| VM2 SSH | TCP | 127.0.0.1 | 4445 | 10.0.2.15 | 22 |
| VM2 API | TCP | 127.0.0.1 | 8001 | 10.0.2.15 | 8000 |

---

## Quick Commands Reference

```bash
# Full chain: local → Mac → VM
ssh -t dad@174.178.63.139 'ssh -p 4445 root@localhost'

# Check MCP server health from your machine (via SSH tunnel)
ssh dad@174.178.63.139 'curl -s http://localhost:8001/health'

# Check what runbooks are loaded
ssh dad@174.178.63.139 'curl -s http://localhost:8001/runbooks'

# View MCP server logs
ssh dad@174.178.63.139 'ssh -p 4445 root@localhost "cat /var/lib/mcp-server/server.log"'
```

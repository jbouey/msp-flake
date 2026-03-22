# Agent Deployment Guide

## Architecture

Push-first, pull-fallback. Once an agent is running on any platform, WinRM/SSH polling is unnecessary for that host.

```
Workstation (any OS)
  └── osiris-agent (persistent process)
        └── gRPC bidirectional stream → Appliance:50051
              ├── Push: drift events, heartbeats, check results
              ├── Pull: heal commands, config updates, self-update
              └── mTLS: auto-enrolled on first connect
```

## Platform Deploy Methods

### macOS (Zero Friction)

**One-liner via SSH:**
```bash
./packaging/deploy.sh jrelly@192.168.88.50 192.168.88.241:50051
```

**Or manual:**
```bash
scp bin/osiris-agent-darwin-amd64 user@host:/tmp/osiris-agent
ssh user@host 'sudo mkdir -p /Library/OsirisCare && sudo mv /tmp/osiris-agent /Library/OsirisCare/osiris-agent && sudo chmod +x /Library/OsirisCare/osiris-agent'
# Write config with appliance address
echo '{"appliance_addr":"APPLIANCE_IP:50051","check_interval":300}' | ssh user@host 'sudo tee /Library/OsirisCare/config.json'
# Install launchd plist
scp packaging/macos/com.osiriscare.agent.plist user@host:/tmp/
ssh user@host 'sudo mv /tmp/com.osiriscare.agent.plist /Library/LaunchDaemons/ && sudo launchctl load /Library/LaunchDaemons/com.osiriscare.agent.plist'
```

**Via .pkg installer:**
```bash
make build-pkg
# Distribute via MDM (Jamf/Mosyle/Kandji) or manual install:
sudo installer -pkg bin/osiris-agent-0.3.26.pkg -target /
```

**Build note:** macOS binaries MUST be built with Go 1.22 (`make build-darwin` handles this). Go 1.26+ links against `SecTrustCopyCertificateChain` which requires macOS 12. Healthcare Macs commonly run macOS 11 (Big Sur).

### Linux (Zero Friction)

**One-liner via SSH:**
```bash
./packaging/deploy.sh root@10.0.0.5 10.0.0.1:50051
```

**Or manual:**
```bash
scp bin/osiris-agent-linux root@host:/opt/osiriscare/osiris-agent
scp packaging/linux/osiriscare-agent.service root@host:/etc/systemd/system/
ssh root@host 'echo "{\"appliance_addr\":\"APPLIANCE_IP:50051\",\"check_interval\":300}" > /opt/osiriscare/config.json && systemctl daemon-reload && systemctl enable --now osiriscare-agent'
```

### Windows (GPO — Automatic on Boot)

No manual deploy needed. The appliance auto-configures:

1. **GPO startup script** (configured automatically by appliance autodeploy):
   - Runs `Enable-PSRemoting -Force` on every boot
   - Copies agent from `\\DC\NETLOGON\osiris-agent.exe`
   - Writes config with appliance gRPC address
   - Installs as Windows service `OsirisCareAgent`

2. **Workstations receive the agent on next boot/policy refresh:**
   ```powershell
   gpupdate /force   # Or just reboot
   ```

3. **Verify:**
   ```powershell
   Get-Service OsirisCareAgent
   # Should show Running
   ```

**Fallback chain for Windows binary delivery:**
1. HTTP GET from appliance:8090 (fastest, ~1s)
2. NETLOGON UNC copy via DC IP (fast, ~3s)
3. WinRM base64 chunked transfer (slow but reliable, ~2min)

## After Deploy

Once the agent connects to the appliance:

- **Auto-enrollment**: mTLS certificates issued on first connect
- **Registration**: Agent appears in `go_agents` table on Central Command
- **Dashboard**: Visible at `/sites/{siteId}/agents` (Go Agents page)
- **Self-update**: Appliance can push new binary via gRPC response
- **WinRM skip**: `driftscan.go` automatically skips WinRM for hosts with agents

## Scan Priority Order

```
For each workstation:
  1. If Go agent connected → push covers it (skip WinRM)
  2. Else → WinRM pull scan (fallback)
```

## Verifying Agent Status

**On appliance:**
```bash
journalctl -u appliance-daemon | grep "agents="
# agents=N shows connected Go agents
```

**On Central Command:**
```sql
SELECT agent_id, hostname, status, last_heartbeat FROM go_agents;
```

**On workstation (macOS):**
```bash
launchctl list | grep osiriscare
cat /Library/OsirisCare/agent.log | tail -20
```

**On workstation (Linux):**
```bash
systemctl status osiriscare-agent
journalctl -u osiriscare-agent -f
```

**On workstation (Windows):**
```powershell
Get-Service OsirisCareAgent
Get-Content C:\OsirisCare\agent.log -Tail 20
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Agent not connecting | Firewall blocking port 50051 outbound | Open TCP 50051 to appliance IP |
| macOS `dyld: Symbol not found` | Built with Go 1.26+ on macOS <12 | Rebuild with Go 1.22 (`make build-darwin`) |
| Windows service stops immediately | PowerShell execution policy | Run `Set-ExecutionPolicy RemoteSigned -Force` |
| NETLOGON copy gets wrong size | SYSVOL DFS replication lag | Use DC IP directly or wait for replication |
| Agent registered but no checks | Platform checks not compiled in | Verify build tags match target OS |

# Session 149: Fleet Order Error Handling + ws01 GPO

**Date:** 2026-03-03
**Focus:** Fleet order signing fixes, update_daemon handler, ws01 GPO verification

## Completed

### 1. Fixed NixOS Self-Scan False Positives (v0.3.12)
- **Root cause:** Both appliances scanning themselves (NixOS) — produced 12 false positives per scan cycle across 6 Linux check types
- Removed `scanLinuxSelf()` from `linuxscan.go` — remote Linux scanning via SSH still works
- Added port 80 (`http-file-server`) to `expectedPorts` in `netscan.go`
- Deployed v0.3.12 to both physical (192.168.88.241) and VM (192.168.88.254) appliances via SCP

### 2. Fleet Order Error Handling (v0.3.13)
Three improvements to prevent the base64-vs-hex signing bug from recurring:

**a) Better signature error messages** (`appliance/internal/crypto/verify.go`)
- Detects base64-encoded signatures and provides clear diagnostic: "use signature.hex() in Python, not base64.b64encode()"
- Shows expected vs actual character count in all error paths

**b) `update_daemon` order handler** (`appliance/internal/orders/processor.go`)
- 18th registered order type
- Downloads binary from URL (validated against domain allowlist, HTTPS required)
- Verifies SHA256 hash before writing
- Writes to `/var/lib/msp/appliance-daemon` via atomic tmp+rename
- Creates systemd override (persistent or runtime fallback)
- Runs `systemctl daemon-reload` + schedules restart in 10s
- Parameters: `binary_url`, `binary_sha256`, `version`

**c) Hex format validation** (`mcp-server/central-command/backend/order_signing.py`)
- `_validate_signature_hex()` asserts exactly 128 lowercase hex chars
- Applied to both `sign_admin_order()` and `sign_fleet_order()`
- Catches misconfiguration immediately on the Python side

### 3. DC Recovery
- Fixed EventLog service (Stopped/Disabled by chaos lab) → enabled ADWS
- Created GPO "OsirisCare-WinRM" linked to domain root
- Startup scripts: Setup-WinRM.ps1, Deploy-Agent.ps1, psscripts.ini, scripts.ini
- Staged osiris-agent.exe (12MB) + osiris-config.json to NETLOGON
- Reset ws01 machine account password in AD

### 4. ws01 Status
- ws01 rebooted, pingable at 192.168.88.251
- **BLOCKER:** Trust relationship broken — ws01 can't authenticate to domain
- GPO scripts in SYSVOL ready but won't apply until trust restored
- Admin share accessible from DC (C:\OsirisCare does not exist yet)

## Files Changed
- `appliance/internal/daemon/linuxscan.go` — removed scanLinuxSelf(), findBash, bashCandidates
- `appliance/internal/daemon/netscan.go` — port 80 in expectedPorts
- `appliance/internal/daemon/daemon.go` — version 0.3.11 → 0.3.13
- `appliance/internal/crypto/verify.go` — base64 detection + better error messages
- `appliance/internal/orders/processor.go` — update_daemon handler + imports
- `appliance/internal/orders/processor_test.go` — 17 → 18 handlers
- `mcp-server/central-command/backend/order_signing.py` — hex validation

## Next Priorities
1. **Fix ws01 trust** — rejoin domain from ws01 console, then gpupdate /force
2. **Deploy v0.3.13** — build + fleet order with new update_daemon handler
3. **Verify GPO** — after trust fix, check agent deployment on ws01
4. **Git commit** — all changes uncommitted

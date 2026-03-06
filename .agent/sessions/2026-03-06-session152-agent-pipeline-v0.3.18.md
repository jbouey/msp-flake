# Session 152: Agent Pipeline Fix + v0.3.18 Deploy

**Date:** 2026-03-06
**Focus:** Fix full agent enrollment pipeline, deploy v0.3.18 to both appliances

## Completed

### Agent Reconnect Logic
- Added `reconnectLoop()` with exponential backoff (30s → 5min) in `agent/cmd/osiris-agent/main.go`
- Added `tryRegisterAndSetup()` helper for registration flow
- Agent no longer runs offline forever if initial gRPC connect fails

### go_agents VPS Sync
- Fixed timestamp parsing in `sites.py` — asyncpg requires naive datetime, not offset-aware
- Added `_parse_ts()` helper: strips timezone info from ISO strings for `timestamp without time zone` columns
- ws01 agent `go-NVWS01-47d98ba3` now visible in go_agents table on VPS

### Autodeploy Version-Aware Probe
- `autodeploy.go` probe script now checks binary version + config correctness, not just service status
- Previously, `RUNNING` status caused skip even with stale binary/config
- New probe returns `STALE|ver=X|addr=Y` when version or config mismatch

### Pure-Go SQLite
- Replaced `github.com/mattn/go-sqlite3` (requires CGO) with `modernc.org/sqlite` (pure Go)
- Agent offline queue now cross-compiles for Windows without CGO toolchain
- Driver name changed from `"sqlite3"` to `"sqlite"`

### Driftscan Credential Fix
- Fixed hostname/IP mismatch in `driftscan.go` workstation target building
- Credentials stored by IP but lookups by AD hostname — added DNS resolution fallback
- `net.LookupHost()` resolves hostname → IP, then retries `LookupWinTarget()` with IP

### Agent Logging Fix
- Reordered `io.MultiWriter` args: `logFile` first, `os.Stderr` second
- Windows services have no valid stderr handle — first writer failing killed all logging
- Agent.log was 0 bytes on ws01 due to this

### Config BOM Encoding Fix
- PowerShell `ConvertTo-Json | Set-Content -Encoding UTF8` adds BOM
- Go JSON parser fails on BOM: `invalid character '�'`
- Fixed autodeploy to use `[System.IO.File]::WriteAllText()` with `UTF8Encoding($false)`

### Deployment
- Both appliances updated to v0.3.18 (daemon + driftscan fixes)
- VM appliance updated via SCP + systemd override
- Physical appliance updated via fleet order
- All changes pushed to main (commit 7b9fe91), CI/CD deployed to VPS

## Key Bugs Found
1. Agent had zero reconnect logic — offline forever on initial failure
2. Autodeploy probe only checked "is service running", not version/config
3. CGO SQLite dependency broke Windows cross-compilation
4. Driftscan used hostname for credential lookup but credentials stored by IP
5. MultiWriter order caused total log loss on Windows services
6. PowerShell BOM in config files broke Go JSON parsing
7. asyncpg rejected offset-aware datetimes for `timestamp without time zone` columns

## Verified
- ws01 agent enrolled: `go-NVWS01-47d98ba3` in go_agents table
- Full pipeline: deploy → enroll → mTLS → drift stream → heal commands
- Both appliances reporting v0.3.18 in checkins

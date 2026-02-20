# Session 118: Go Daemon — Full Production Deployment

**Date:** 2026-02-17/18
**Duration:** ~45 minutes
**Context:** Deploying Go daemon to production, wiring remaining subsystems

## Summary

Completed all remaining Go daemon tasks: L2 executor wiring, order completion POST, backend OrderType enum, Go 1.22 compatibility, NixOS rebuild, and production activation. The Go daemon is now **running in production** on the physical HP T640 appliance.

## What Was Done

### 1. Backend OrderType Enum (sites.py)
Added 7 missing order types: `nixos_rebuild`, `update_iso`, `diagnostic`, `deploy_sensor`, `remove_sensor`, `update_credentials`, `restart_agent`. Dashboard can now create rebuild orders via API.

### 2. L2 Executor Wiring (daemon.go)
- Added `winrmExec *winrm.Executor` and `sshExec *sshexec.Executor` to Daemon struct
- `executeL2Action()` dispatches L2 decisions to WinRM (Windows) or SSH (Linux) based on platform
- `buildWinRMTarget()` / `buildSSHTarget()` extract credentials from heal request metadata
- Falls back to L3 escalation if no credentials available

### 3. Order Completion POST (daemon.go)
Replaced stub `completeOrder()` with real HTTP POST to `/api/orders/{order_id}/complete`:
- Sends `{success, result, error_message}` JSON payload
- Uses existing PhoneHomeClient's HTTP client (with TLS, timeout)
- Bearer token auth from config

### 4. Go 1.22 Compatibility
- NixOS 24.05 ships Go 1.22, but deps required Go 1.24
- Downgraded: `pgx/v5` v5.8.0→v5.5.5, `x/crypto` v0.48.0→v0.24.0, `rogpeppe/go-internal` v1.14.1→v1.12.0
- Regenerated vendor hash: `sha256-UUQ3KKz2l1U77lJ16L/K7Zzo/gkSuwVLrzO/I/f4FUM=`

### 5. NixOS Rebuild & Activation
- First rebuild failed: `go.mod requires go >= 1.24.0 (running go 1.22.8)`
- Fixed deps, pushed, rebuilt successfully
- `touch /var/lib/msp/.use-go-daemon` + `systemctl start appliance-daemon`
- `nixos-rebuild switch` to persist

### 6. Production Verification
- Go daemon running since 01:09 UTC, PID 569492
- **Memory: 6.6MB** (vs Python's 112MB) — **17x reduction**
- **CPU: 102ms** total after 2 cycles
- **Checkin cycle: 52ms**
- L1 engine: 82 rules loaded (38 builtin + 44 synced)
- CA initialized from /var/lib/msp/ca
- gRPC server listening on :50051
- Order completion POST to Central Command working

## Test Count
- **150 tests** across 10 packages (up from 141)
- New tests: CompleteOrderHTTP, CompleteOrderHTTPFailure, BuildWinRMTarget, BuildSSHTarget, ExecuteL2ActionNoCredentials, ExecuteL2ActionLinuxPlatform, executor init checks

## Commits
- `ecec526` — feat: L2 executor wiring + order completion POST + Go 1.22 compat

## Resource Comparison (Python vs Go)

| Metric | Python Agent | Go Daemon | Improvement |
|--------|-------------|-----------|-------------|
| Memory | 112.5MB | 6.6MB | 17x less |
| CPU (startup) | ~10min | 102ms | ~6000x less |
| Checkin cycle | ~50-100ms | 52ms | Comparable |
| L1 rules | 38 builtin | 82 (38+44 synced) | Auto-loads synced |
| Binary size | ~1.1GB (Python+deps) | ~15MB | 73x smaller |

## Monitoring Commands
```bash
# Watch logs
ssh root@192.168.88.241 'journalctl -u appliance-daemon -f'

# Check status
ssh root@192.168.88.241 'systemctl status appliance-daemon'

# Rollback to Python
ssh root@192.168.88.241 'rm /var/lib/msp/.use-go-daemon && systemctl stop appliance-daemon && systemctl start compliance-agent'

# Check VPS order delivery
ssh root@178.156.162.116 'docker exec mcp-postgres psql -U mcp -d mcp -c "SELECT last_checkin FROM site_appliances WHERE appliance_id LIKE '"'"'physical%'"'"';"'
```

## Remaining After Soak Test
- Persist rebuild with `nixos-rebuild switch` on boot (done)
- Monitor for 72 hours (started 2026-02-18 01:09 UTC)
- Python cleanup after soak test passes
- Wire `nixos-rebuild` order handler to use `systemd-run` (sandbox escape)

# Current State

**Updated:** 2026-02-27T18:30:00Z
**Session:** 142
**Branch:** main

## System Health

| Component | Status |
|-----------|--------|
| VPS API | healthy |
| Dashboard | healthy |
| Physical Appliance | go_daemon_v0.3.6, fleet order pending (security hardening) |
| VM Appliance | go_daemon_v0.3.6, fleet order pending (security hardening) |
| L2 Planner | enabled (L2Enabled=true default, confidence >= 0.6 auto-exec) |
| Learning Flywheel | active, aggregation bridge deployed, 2 patterns promoted |
| Evidence Pipeline | 216k+ bundles, Ed25519 signed, real drift data |
| Compliance Packets | 56% compliance, 15 HIPAA controls |
| OTS Proofs | 2705 anchored, 251 pending |
| Fleet Updates | fleet_orders live, v0.3.6 deployed, security hardened |

## Latest Commit

```
a68fe2d security: harden fleet orders — auth, signatures, nonce replay, WinRM SSL
```

## Session 142 Changes — Security Hardening (7 fixes)

### CRITICAL (4)
1. **Appliance checkin auth** — `require_appliance_auth()` validates Bearer token against `api_keys` table with graceful migration fallback; applied to checkin, acknowledge, complete
2. **Signature delivery** — admin_orders, healing orders, fleet_orders now include `nonce, signature, signed_payload` in SELECT and response
3. **server_public_key** — checkin response now returns `get_public_key_hex()` for Go daemon signature verification
4. **Fleet order signatures** — `get_fleet_orders_for_appliance()` includes signature columns

### HIGH (3)
5. **Nonce replay protection** — Go `Processor` tracks used nonces in-memory + persists to `used_nonces.json`, 24h eviction
6. **Hostname validation** — `isKnownTarget()` validates healing order hostnames against DC, deployed workstations, linux targets
7. **WinRM SSL** — All 10 WinRM connection sites switched to port 5986 + `UseSSL: true` + `VerifySSL: false` (self-signed tolerance)

### Files Changed
- `mcp-server/central-command/backend/sites.py` — auth, signatures, server_public_key
- `mcp-server/central-command/backend/fleet_updates.py` — fleet order signatures
- `appliance/internal/orders/processor.go` — nonce replay tracking
- `appliance/internal/daemon/healing_executor.go` — hostname validation, WinRM SSL
- `appliance/internal/daemon/daemon.go` — WinRM SSL (3 locations)
- `appliance/internal/daemon/driftscan.go` — WinRM SSL (3 locations)
- `appliance/internal/daemon/autodeploy.go` — WinRM SSL (3 locations)

## Current Blockers

- **WinRM HTTPS prerequisite** — Windows targets need WinRM HTTPS listener configured with cert before SSL connections will work
- **HIPAA compliance at 56%** — needs more check coverage
- **Deploy required** — push to main triggers CI/CD for backend; appliances need fleet order for Go daemon rebuild

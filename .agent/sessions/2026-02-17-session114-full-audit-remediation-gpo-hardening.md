# Session 114: Full Audit Remediation + GPO Pipeline Hardening

**Date:** 2026-02-17
**Duration:** ~3 hours (spans sessions 112-114)
**Status:** COMPLETE

## Summary

Executed a comprehensive 4-track plan to remediate all audit findings from Go agent, Central Command, NixOS infrastructure, and Python compliance agent audits. Also hardened the GPO deployment pipeline and added 43 new tests.

## Commits

| Hash | Description |
|------|-------------|
| `dd83883` | fix: Go agent — GC pinning, error logging, backpressure, timeout validation |
| `e9de57a` | fix: Central Command — real onboarding endpoints, pagination, metrics, indexes |
| `cc135f1` | fix: NixOS hardening — SSH, resource limits, firewall, service ordering |
| `a67c079` | feat: GPO pipeline hardening — cert warnings, rollback, 43 new tests |

## Track A: Go Agent Audit Fixes (7 items)

1. **EventLog GC pinning** — `callbackPins` field prevents GC from collecting Windows callback data passed via unsafe.Pointer
2. **EventLog error logging** — Capture `lastErr` from `procEvtRender.Call` instead of discarding
3. **OfflineQueue enforceLimit** — Log error from DELETE instead of silent discard
4. **HealCmds backpressure** — Buffer 32→128, capacity warnings at 75% at all 3 send sites
5. **WMI context deadline** — `ctx.Err()` checks before ConnectServer, ExecQuery, and 3 registry functions
6. **RMM sanitization** — `strings.NewReplacer` replaces 3 chained `strings.ReplaceAll`
7. **Healing timeout validation** — Validate 0-600s range before `time.Duration` conversion

## Track B: Central Command Fixes (7 items)

1. **Real onboarding endpoints** — advance_stage, update_blockers, add_note now use SQL + `Depends(get_db)`
2. **Pagination offset** — Added `offset` param to incidents, events, runbook_executions (routes.py + db_queries.py)
3. **Onboarding metrics** — Replaced hardcoded zeros with real SQL aggregates
4. **Notification log level** — `logger.debug` → `logger.warning` for broadcast failures
5. **Cache TTL** — Configurable via `CACHE_TTL_SCORES`/`CACHE_TTL_METRICS` env vars
6. **Vite proxy** — `VITE_API_URL` env var instead of hardcoded IP
7. **Migration 047** — Composite index on `compliance_bundles(appliance_id, reported_at DESC, check_type)`

## Track C: NixOS Infrastructure Hardening (4 items)

1. **SSH** — `PermitRootLogin=prohibit-password`, `PasswordAuthentication=false`, `mkDefault` on root password
2. **Resource limits** — `MemoryMax`, `CPUQuota`, `LimitNOFILE`, `StartLimitIntervalSec/Burst` on 3 services
3. **Service ordering** — `requires = [ "msp-auto-provision.service" ]` for compliance-agent + scanner
4. **Firewall** — Reduced TCP ports from 7 to 5 (8081/8082 bind localhost only)

## Track D: GPO Pipeline Hardening + Tests (4 items)

1. **Cert enrollment warning** — `elif request.needs_certificates and not self.agent_ca` logs warning in Register handler; startup warnings in `serve_sync()` and `serve()`
2. **GPO rollback** — `_rollback(artifacts)` cleans up SYSVOL dir + GPO on partial deployment failure
3. **GPO flag logging** — `except Exception: pass` → `logger.warning(...)` on flag write failure
4. **43 new tests** across 4 files:
   - `test_agent_ca.py` (11 tests) — real crypto, no mocking
   - `test_gpo_deployment.py` (14 tests) — pipeline, rollback, verify
   - `test_dns_registration.py` (8 tests) — SRV create, verify, failure
   - `test_agent_deployment.py` (10 tests) — WinRM pipeline, concurrent, status

## Post-Deploy: Service Fixes (3 iterations)

After pushing audit tracks, kicked off NixOS rebuilds on both appliances. Two services failed: `msp-console-branding` and `msp-rebuild-watchdog`. Fixed across 3 commits:

| Hash | Description |
|------|-------------|
| `ac549c2` | fix: console-branding rm symlink before write + watchdog add util-linux |
| `daf82a8` | fix: local-portal port 8083→8084 (conflict with scanner) + getty --no-block |
| `356d229` | fix: console-branding timeout on tty1 write (blocks without physical console) |

### Issues Found & Fixed

1. **`/etc/issue` read-only symlink** — NixOS manages `/etc/issue` as a symlink to the nix store. `cat > /etc/issue` fails silently. Fix: `rm -f /etc/issue` before writing.

2. **`logger: command not found`** — `msp-rebuild-watchdog` used `logger` but didn't have `util-linux` in its PATH. Fix: added to `path` list.

3. **Port 8083 conflict** — `network-scanner` binds `0.0.0.0:8083` for Go agent check-ins. `local-portal` also defaulted to 8083. Fix: moved local-portal to port 8084. Updated all references (firewall, banner, MOTD).

4. **`systemctl restart getty@tty1` deadlock** — During `nixos-rebuild test` activation, systemd holds a transaction lock. `systemctl restart` inside a oneshot blocks forever waiting for the lock. Fix: `systemctl --no-block restart`.

5. **`printf '\033c' > /dev/tty1` blocking** — Writing to `/dev/tty1` blocks indefinitely when no physical console is attached (VM or headless). Fix: `timeout 2 bash -c 'printf "\033c" > /dev/tty1'`.

### Rebuild Results

- **VM (.254):** Clean exit 0 after commit ac549c2
- **Physical (.241):** Clean exit 0 after commit 356d229 (required all 3 iterations)
- Both appliances: all services running, zero failures

### Overlay Deploy

Built and deployed v1.0.73 overlay tarball to VPS (`/var/www/updates/agent-overlay.tar.gz`).

## Test Results

- **1037 passed**, 13 skipped, 0 failures
- Go agent: `go vet` clean
- All pushed to main, CI/CD deploying

## Files Modified

### Go Agent (6 files)
- `agent/internal/eventlog/eventlog_windows.go`
- `agent/internal/transport/grpc.go`
- `agent/internal/transport/offline.go`
- `agent/internal/wmi/wmi_windows.go`
- `agent/internal/checks/rmm.go`
- `agent/internal/healing/executor.go`

### Central Command (4 files)
- `mcp-server/central-command/backend/routes.py`
- `mcp-server/central-command/backend/db_queries.py`
- `mcp-server/central-command/frontend/vite.config.ts`
- `mcp-server/central-command/backend/migrations/047_audit_indexes.sql` (NEW)

### NixOS (3 files)
- `iso/appliance-disk-image.nix`
- `iso/appliance-image.nix`
- `modules/compliance-agent.nix`

### Python Agent (3 source + 4 test files)
- `packages/compliance-agent/src/compliance_agent/grpc_server.py`
- `packages/compliance-agent/src/compliance_agent/gpo_deployment.py`
- `packages/compliance-agent/src/compliance_agent/appliance_agent.py`
- `packages/compliance-agent/tests/test_agent_ca.py` (NEW)
- `packages/compliance-agent/tests/test_gpo_deployment.py` (NEW)
- `packages/compliance-agent/tests/test_dns_registration.py` (NEW)
- `packages/compliance-agent/tests/test_agent_deployment.py` (NEW)

### Service Fixes (3 files)
- `iso/appliance-disk-image.nix` (console-branding script, firewall ports, banner/MOTD port refs)
- `iso/configuration.nix` (rebuild-watchdog util-linux path)
- `modules/local-portal.nix` (default port 8083→8084)

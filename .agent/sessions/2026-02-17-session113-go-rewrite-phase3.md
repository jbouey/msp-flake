# Session 113: Go Rewrite — Phases 3A-3F + Phase 4 Wiring Complete

**Date:** 2026-02-17
**Duration:** ~2 hours (across multiple context windows)
**Context:** Continuation of Go rewrite from session that ran out of context

## Summary

Completed all phases of the Go appliance daemon rewrite: Phases 3A through 3F plus Phase 4 wiring. The entire `appliance/` Go module now has 10 packages with 141 tests, all passing with zero vet issues.

## What Was Done

### Phase 3A: Daemon Config + Phone-Home (completed in prior session, tests run here)
- 7 config tests verified passing

### Phase 3B: L1 Deterministic Healing Engine
- **`internal/healing/l1_engine.go`** (~450 lines) — Full L1 engine with:
  - 9 match operators: eq, ne, contains, regex, gt, lt, in, not_in, exists
  - Dot-notation nested field access
  - Rule loading: builtin, YAML, synced JSON, promoted
  - Cooldown tracking (per rule+host)
  - Action execution with dry-run support
  - Stats and rule listing
- **`internal/healing/builtin_rules.go`** (~500 lines) — All 38 builtin rules ported from Python:
  - 12 generic/NixOS rules (patching, AV, backup, logging, firewall, encryption, cert, disk, service crash)
  - 13 Linux rules (SSH, kernel, cron, SUID, firewall, audit, services, logging, permissions, network, banner, crypto, IR)
  - 13 Windows rules (DNS, SMB, WUAU, network profile, screen lock, BitLocker, NetLogon, DNS hijack, Defender exclusions, scheduled task/registry/WMI persistence, SMBv1)
- **22 tests** (56 assertions with subtests): all operators, Linux rules, Windows rules, synced JSON override, cooldown, severity filter, YAML loading, reload

### Phase 3C: L2 Bridge (Go→Python Unix Socket)
- **`internal/l2bridge/client.go`** (~200 lines) — JSON-RPC 2.0 client over Unix socket:
  - `Plan(incident)` → `LLMDecision` with confidence + escalation flags
  - `Health()` liveness check
  - `PlanWithRetry()` with auto-reconnection
  - `ShouldExecute()` — checks confidence >= 0.6, no escalation flags
- **11 tests** (16 with subtests): plan, health, escalation, reconnection, RPC errors, multiple requests, ShouldExecute decisions

### Phase 3D: WinRM + SSH Executors
- **`internal/winrm/executor.go`** (~340 lines) — WinRM executor:
  - Session caching with 300s refresh
  - Inline execution for scripts ≤2000 chars
  - Temp file execution for longer scripts (cmd.exe 8191 char limit workaround)
  - Base64 chunking (6000-char chunks via cmd.exe echo)
  - UTF-16LE PowerShell encoding
  - Retry with exponential backoff
  - SHA256 output hashing for evidence
- **`internal/sshexec/executor.go`** (~310 lines) — SSH executor:
  - Connection caching with staleness detection
  - Base64 script encoding (avoids shell quoting)
  - sudo support (with/without password)
  - Distro detection (ubuntu, rhel, debian, etc.)
  - Auth failure detection (no retry on PermissionDenied)
  - IPv6-safe host:port formatting
- **19 tests** combined (8 WinRM + 11 SSH): encoding, splitting, hashing, auth config, error handling

### Phase 3E: Order Processing
- **`internal/orders/processor.go`** (~400 lines) — 17 order type handlers:
  - force_checkin, run_drift, sync_rules, restart_agent
  - nixos_rebuild (two-phase with rollback safety, restart-safe)
  - update_agent, update_iso
  - view_logs, diagnostic (whitelisted commands only)
  - deploy/remove sensor (Windows + Linux)
  - sensor_status, sync_promoted_rule
  - healing, update_credentials
  - Completion callback pattern
  - Deferred rebuild completion on startup
  - Custom handler registration
- **22 tests**: all order types, missing fields, whitelist enforcement, batch processing, cancellation, deferred rebuild

### Phase 3F: AD Enumeration + Discovery
- **`internal/discovery/domain.go`** (~350 lines) — Domain discovery via 4 methods:
  - DNS SRV records, resolv.conf, DHCP lease files, LDAP rootDSE
  - Raw BER encoding for LDAP (no external library)
  - DN→domain conversion, NetBIOS extraction
- **`internal/discovery/ad.go`** (~250 lines) — AD enumeration:
  - PowerShell Get-ADComputer, JSON parsing, classification
  - Connectivity testing, IP resolution
- **19 tests**: DN conversion, BER encoding, enumeration, classification, connectivity

### Phase 4: Daemon Wiring (L1→L2→L3 Pipeline)
- **`internal/daemon/daemon.go`** — Fully wired subsystem integration:
  - Added `healing.Engine`, `l2bridge.Client`, `orders.Processor` fields to Daemon struct
  - `New()` initializes L1 engine (loads 38 rules), L2 bridge (conditional on config), order processor (with completion callback)
  - `processOrders()` converts raw checkin order maps → `orders.Order` structs → dispatches via processor
  - `healIncident()` implements full L1→L2→L3 pipeline:
    - L1: Match incident against rules, execute (or dry-run)
    - L2: If no L1 match and L2 enabled, send to Python sidecar via Unix socket
    - L3: Escalate if L2 unavailable, low confidence, or requires approval
  - `completeOrder()` callback logs order results
  - `escalateToL3()` logs escalation for human review
  - Clean shutdown: closes L2 bridge, stops gRPC gracefully
  - Deferred NixOS rebuild completion on startup
- **`internal/daemon/daemon_test.go`** — 11 new integration tests:
  - Daemon creation (with/without L2, dry-run mode)
  - L1 match + dry-run execution (verified L1-FW-001 matches firewall drift)
  - L1 no-match → L3 escalation
  - Order processing (normal, with params, unknown type)
  - Completion callback
  - L3 escalation
  - Daemon shutdown (200ms timeout)

## Test Summary

| Package | Tests | Description |
|---------|-------|-------------|
| `internal/ca` | 7 | ECDSA P-256 CA |
| `internal/checkin` | 10 | Checkin models + handler |
| `internal/daemon` | 18 | Config + phone-home + wiring |
| `internal/grpcserver` | 13 | 5 RPCs via bufconn |
| `internal/healing` | 22 | L1 engine + all operators |
| `internal/l2bridge` | 11 | JSON-RPC client |
| `internal/orders` | 22 | 17 order handlers |
| `internal/sshexec` | 11 | SSH executor |
| `internal/winrm` | 8 | WinRM executor |
| `internal/discovery` | 19 | AD discovery + enumeration |
| **Total** | **141** | All passing, zero vet issues |

## Dependencies
- `github.com/masterzen/winrm` — WinRM client (NTLM auth)
- `golang.org/x/crypto/ssh` — SSH client
- `google.golang.org/grpc` + `google.golang.org/protobuf` — gRPC
- `github.com/jackc/pgx/v5` — PostgreSQL
- `gopkg.in/yaml.v3` — YAML config/rules

## Architecture

```
appliance/
  cmd/
    appliance-daemon/main.go     # Main daemon binary
    checkin-receiver/main.go     # VPS checkin service
    grpc-server/main.go          # Standalone gRPC server
  internal/
    ca/          # ECDSA P-256 cert authority
    checkin/     # HTTP checkin handler (pgx)
    daemon/      # Config, phone-home, main loop, L1→L2→L3 wiring
    discovery/   # AD domain discovery + computer enumeration
    grpcserver/  # 5 RPC server + agent registry
    healing/     # L1 deterministic engine (38 rules)
    l2bridge/    # Unix socket JSON-RPC to Python L2
    orders/      # 17 order type handlers
    sshexec/     # SSH executor (Linux targets)
    winrm/       # WinRM executor (Windows targets)
  proto/         # Generated Go code
  go.mod         # github.com/osiriscare/appliance
```

## Remaining Work
- **NixOS packaging:** `pkgs.buildGoModule` in flake.nix
- **Feature flag:** `use_go_daemon` config for per-appliance rollout
- **L2 action execution:** Wire WinRM/SSH executors into L2 decision execution
- **Order completion API:** POST to Central Command `/api/appliances/orders/<id>/complete`
- **Python cleanup:** Remove replaced Python components after soak testing
- **Soak test:** 72-hour run on physical HP T640

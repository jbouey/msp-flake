# Session 193b — Chaos Lab Fix + iMac Agent Recovery

**Date:** 2026-04-03
**Agent Version:** 0.4.2 (workstation), 0.3.76 (appliance daemon)

## Chaos Lab Fixes

All chaos lab scripts on iMac (.50) were broken due to stale IPs and service names from the Python agent era.

1. **IP updates**: `.246→.235` (appliance), `.242→.233` (linux) in `chaos_workstation_cadence.py`, `chaos_lab.sh`, `linux_chaos_lab.sh`
2. **Service name**: `compliance-agent→appliance-daemon` in cadence script + chaos_lab.sh
3. **SSH host key**: Cleared stale `.246` key on iMac known_hosts
4. **Cadence script rewrite**: Complete rewrite to match Go daemon log patterns — 8 regex patterns covering drift_scan, win_scan, netscan, checkin, adaptive interval, l1_healed, evidence, healing_rate. Replaces Python-era patterns (enumerate_from_ad, run_all_checks).

## iMac Agent Root Cause Chain

The iMac agent was dead since March 26 (8 days). Five cascading failures:

1. **Wrong platform binary**: Appliance update endpoint served `osiris-agent.exe` (Windows PE32+). Updater downloaded it, replaced the macOS Mach-O binary. LaunchDaemon got exit code 126 (cannot execute binary).
2. **updater.go chmod bug**: `downloadBinary()` uses `os.Create()` which defaults to 0666/umask (0644). No `os.Chmod(0755)` after download. Even if the right binary was downloaded, it wouldn't be executable.
3. **Config pointed to .241**: After DHCP reassigned the appliance to .235, the agent config/plist still had .241:50051.
4. **Stale TLS certs**: ca.crt, agent.crt, agent.key from old appliance CA identity. Even after fixing the IP, TLS handshake fails with `x509: ECDSA verification failure`.
5. **Go 1.26 incompatibility**: macOS 11.7 (Big Sur) doesn't have `SecTrustCopyCertificateChain` (requires macOS 12+). Go 1.26 uses this API. The amd64 build needed Go 1.24.

## Fixes Applied

| Layer | Fix |
|-------|-----|
| `updater.go` | Added `os.Chmod(destPath, 0755)` after download |
| `updater_test.go` | 12 new tests: chmod, content, HTTP errors, SHA256, concurrency, backoff |
| `Makefile` | Split Go toolchains: Go 1.24 for amd64 (macOS 11+), Go 1.26 for arm64 (M-series) |
| iMac config.json | `appliance_address` → `192.168.88.235:50051` |
| iMac plist | `--appliance 192.168.88.235:50051` |
| iMac TLS | Cleared ca.crt, agent.crt, agent.key, appliance_cert_pin.hex → TOFU re-enrolled |
| iMac binary | Installed Go 1.24-built amd64 binary, v0.4.2 |
| Appliance `/var/lib/msp/bin/` | Both darwin-amd64 + darwin-arm64 binaries + VERSION file |
| Appliance manifest | Auto-scanned on restart: `darwin/amd64` + `darwin/arm64` registered |

## Verified

- Chaos lab cadence: **6/6 PASSED** (daemon, targets, scan cadence, checkin cadence, healing, evidence)
- iMac agent: **v0.4.2 running**, registered as `go-MaCs-iMac.local-94967874`, mTLS connected
- iMac compliance: 9 pass, 3 fail (screen_lock, firewall, time_machine)
- Appliance sees agent: `agents=1` in cycle, drift streaming active
- Go tests: 4 packages, 0 failures (checks, transport, updater, wmi)

## Phase 2: Runbook Coverage + Agent Hardening

**4 commits, agent v0.4.4, daemon v0.3.77**

### Sub-project A: Linux L1 Executor (0% → 100%)
- `executor_linux.go`: 7 heal functions (SSH, firewall, upgrades, SUID, audit, users, NTP)
- Cross-distro: apt/dnf/yum for upgrades, ufw/firewalld/nftables for firewall
- Idempotent scripts, HIPAA audit rules, safe SUID allowlist
- `executor_linux_test.go`: 9 tests

### Sub-project B: Agent Updater Hardening
- `validateBinaryPlatform()`: magic number check (PE/Mach-O/ELF) before swap
- 4 new platform validation tests
- `MarkDisconnected()` + heartbeat loop exits after 3 consecutive failures
- reconnectLoop detects disconnect → full reconnect cycle (fixes stuck agent after daemon restart)

### Sub-project C: macOS/Windows Healing Additions
- macOS FileVault deferred enablement (queues for next login)
- macOS Time Machine auto-enable (if backup destination exists)
- Windows Update L1 healing (COM API, critical/important only)
- `executor_darwin_test.go` + `executor_windows_test.go`

### Infrastructure Fixes
- healMap expanded: 6 Windows + 8 macOS + 6 Linux check types in gRPC drift ACKs
- LIN-PATCH-001 runbook fix: `apt-daily-upgrade.timer` (was `unattended-upgrades.timer`)
- Windows exe deployed to `/var/lib/msp/agent/` (fixes autodeploy 404)
- Appliance manifest: darwin-amd64, darwin-arm64, linux-amd64 all v0.4.3

### Verified
- `[gRPC] Immediate heal for MaCs-iMac.local: macos_time_machine/enable` → `success=true`
- Linux unattended_upgrades: **healed in 3.6s** (was failing before timer name fix)
- Healing rate: **88% (30/34 in 24h)**
- Agent tests: 5 packages, 0 failures
- Appliance tests: 18 packages, 0 failures
- Coverage: **22/27 (81%)** across all platforms (was 12/27 = 44%)

## Open Items

- MikroTik DHCP reservations need admin creds (appliance .235 + linux .233)
- iMac SSH port 2222 still broken (use reverse tunnel: VPS:2250)
- NVDC01 Windows agent config still points to .241:50051 (low priority — WinRM scanning works)

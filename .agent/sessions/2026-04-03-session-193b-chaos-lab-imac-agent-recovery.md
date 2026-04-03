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

## Open Items

- iMac SSH port 2222 still broken (use reverse tunnel: VPS:2250)
- MikroTik DHCP reservations need admin creds
- macos_firewall check failing — firewall disabled on iMac
- macos_screen_lock — askForPassword not set
- macos_time_machine — no backup disk configured

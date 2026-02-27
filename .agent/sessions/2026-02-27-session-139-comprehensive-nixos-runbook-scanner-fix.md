# Session 139 — Comprehensive NixOS Runbook & Scanner Fix (v0.3.6)

**Date:** 2026-02-27
**Focus:** Fix all remaining NixOS-incompatible runbooks and scanner false positives

## Continuation of Session 138

After v0.3.5 fixed LIN-SVC-001, LIN-PATCH-001, LIN-LOG-001, 40+ new notifications revealed more broken runbooks and scanner issues.

## Comprehensive Audit Results

15 scanner checks audited. 7 issues found, 4 critical:

## Fixes

### 1. configuration.nix — HIPAA kernel sysctl params
Added 7 HIPAA-required kernel hardening params to `boot.kernel.sysctl`:
- `net.ipv4.ip_forward = 0`
- `net.ipv4.tcp_syncookies = 1`
- `net.ipv4.conf.all.send_redirects = 0`
- `net.ipv4.conf.all.accept_redirects = 0`
- `net.ipv4.conf.all.rp_filter = 1`
- `kernel.randomize_va_space = 2`
- `kernel.suid_dumpable = 0`

### 2. LIN-FW-001 — Firewall (was empty)
- **remediate_script**: NixOS-aware — detects /etc/NIXOS, reloads nftables service; standard Linux falls through to ufw/firewalld
- **verify_script**: Checks nft ruleset count, ufw status, firewalld state

### 3. LIN-SSH-001 — SSH Root Login (was sed on read-only)
- **detect_script**: Now accepts both `no` and `prohibit-password` as compliant
- **remediate_script**: NixOS-aware — detects /etc/NIXOS, verifies config is already correct declaratively, skips sed
- **verify_script**: Accepts both `no` and `prohibit-password`

### 4. LIN-KERN-001 — Kernel Params (cat > /etc/sysctl.d/ fails on NixOS)
- **remediate_script**: `sysctl -w` for immediate effect on all Linux; skips file persistence on NixOS (managed by configuration.nix)

### 5. SUID Scanner — False positives on NixOS
- linuxscan.go SUID check: Added `case "$f" in /nix/store/*) continue ;;` to skip declaratively-managed NixOS store paths

### 6. Version bump 0.3.5 → 0.3.6

## Files Changed
- `iso/configuration.nix` — 7 HIPAA kernel sysctl params
- `appliance/internal/daemon/runbooks.json` — LIN-FW-001, LIN-SSH-001, LIN-KERN-001 NixOS-aware
- `appliance/internal/daemon/linuxscan.go` — SUID /nix/store filter
- `appliance/internal/daemon/daemon.go` — version 0.3.5 → 0.3.6

## Test Results
- `go build ./...` — clean
- `go test ./internal/orders/` — 41/41 pass
- `go test ./internal/healing/` — pre-existing smb_signing failure (unrelated)
- `runbooks.json` — valid JSON

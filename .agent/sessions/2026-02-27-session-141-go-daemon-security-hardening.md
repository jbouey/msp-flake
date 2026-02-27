# Session 141: Go Daemon Security Hardening

**Date:** 2026-02-27
**Scope:** Comprehensive audit + 4-phase hardening of the Go appliance daemon

## Summary

Three parallel audit agents identified ~50 issues across security, correctness, resource management, and code quality in `appliance/`. Implemented 13 fixes across 4 phases.

## Phase 1: Security Hardening

| Fix | File | Change |
|-----|------|--------|
| SSH TOFU host key verification | `sshexec/executor.go` | Replaced `InsecureIgnoreHostKey()` with TOFU: persist keys to `/var/lib/msp/ssh_known_hosts`, reject changed keys |
| WinRM full SHA256 hash | `winrm/executor.go:204` | Changed `[:8]` truncation to full 64-char SHA256 hex for temp file names |
| UTF-16LE encoding fix | `winrm/executor.go:328-334` | Replaced byte-iteration with `unicode/utf16.Encode()` for correct multi-byte char handling |
| Reject unsigned orders | `orders/processor.go:301-306` | Changed from warn-and-allow to reject unsigned orders when server public key is present |
| Tighten file permissions | `orders/processor.go:723` | Changed promoted rule files from `0644` to `0600` |

## Phase 2: Correctness Fixes

| Fix | File | Change |
|-----|------|--------|
| io.ReadAll error handling | `daemon.go:524` | Check and return error instead of discarding with `_` |
| Context propagation | `healing_executor.go` | Added `executeRunbookCtx()`, `executeLocalCtx()`, `executeInlineScriptCtx()` — healing order path now propagates parent context to SSH/local execution |
| Incident reporter context | `incident_reporter.go:86,131` | Added 30s timeout context via `http.NewRequestWithContext()` |

## Phase 3: Resource Management

| Fix | File | Change |
|-----|------|--------|
| SSH LRU cache | `sshexec/executor.go` | Max 50 cached connections with LRU eviction via `connOrder` slice |
| Distro cache TTL | `sshexec/executor.go` | 24h TTL on distro detection cache via `distroCacheEntry` struct |
| WaitGroup drain | `daemon.go` | `sync.WaitGroup` on key goroutines; 30s timeout drain on shutdown |

## Phase 4: Code Quality

| Fix | File | Change |
|-----|------|--------|
| Atomic DriftCount | `grpcserver/registry.go`, `server.go` | Changed `DriftCount int64` to `atomic.Int64` for race-free concurrent access |
| gpoFixDone to struct | `daemon.go` | Moved package-level `var gpoFixDone sync.Map` to `Daemon` struct field |

## Not Fixed (already correct)
- Cooldown key separator collision (4C) — already uses `:` separator at `daemon.go:591`

## Test Results
- All Go packages pass (except pre-existing `TestWindowsRulesMatch/smb_signing`)
- `go vet ./...` clean
- `go build ./...` clean

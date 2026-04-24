# AGENTS.md — appliance/ (Go daemon)

Scoped to the Go appliance daemon — the active on-box agent. Root invariants live in [`/AGENTS.md`](../AGENTS.md) and [`/CLAUDE.md`](../CLAUDE.md) — read those first.

## Entry points

| You're about to work on... | Read first |
|---|---|
| Core daemon lifecycle, StateManager, interfaces | `internal/daemon/` |
| Fleet order handling + signature verification | `internal/orders/processor.go` |
| PHI scrubbing before checkin | `internal/phiscrub/` (14 patterns, 21 tests) |
| Evidence bundle signing + submission | `internal/evidence/` |
| gRPC agent registry + TLS enrollment | `internal/grpcserver/` |
| Phonehome / TOFU cert pinning | `internal/daemon/phonehome.go` |
| Credential access for sub-components | `internal/daemon/credentials.go` — use `CredentialProvider`, not captured copies |

## Local invariants (non-negotiable in this directory)

- **`CredentialProvider` is the only valid auth-state holder.** Sub-components (`incident_reporter`, `telemetry`, `logshipper`, `evidence_submit`, …) MUST call `creds.APIKey()` per-request. Captured-by-value copies of `cfg.APIKey` at construction time are banned — they silently go stale on auto-rekey and break the evidence chain. See [ADR 2026-04-24](../docs/adr/2026-04-24-source-of-truth-hygiene.md) §Split #3 for the failure class this closes.
- **Dangerous orders require Ed25519 verification BEFORE apply.** `dangerousOrderTypes` in `internal/orders/processor.go` covers: `update_daemon`, `nixos_rebuild`, `healing`, `diagnostic`, `sync_promoted_rule`. Only `force_checkin`, `run_drift`, `restart_agent` are allowed pre-server-key. Adding a new order type = decide dangerousness at design time.
- **TOFU cert pinning is the fallback, not the goal.** `phonehome.go` pins the server leaf cert SHA256 on first successful TLS handshake and refuses anything else afterwards. A server cert rotation requires operator-acknowledged pin reset. Don't relax `VerifySSL: true` anywhere — the `driftscan.go` sites all depend on it.
- **`slog` is the only logger.** 15 files migrated off `log.Printf` in Session 202. New Go code uses `slog.Info/Warn/Error` with a `"component"` key.
- **Egress strings go through `phiscrub.Scrub()`.** Device hostnames (`netscan.go`), deploy errors (`daemon.go`), anything with potentially-patient-named data must scrub before submit. Central Command is PHI-free.

## Build

```bash
cd appliance
make build-linux VERSION=0.4.9   # amd64 linux/static, CGO disabled
make test                         # go test ./...
make vet lint                     # pre-push hooks
```

- **Toolchain:** Go 1.24 for amd64 targets (appliance Linux, macOS 11+ workstation agent). Go 1.26 for arm64 (Apple M-series workstation agent).
- **ldflags version target:** `internal/daemon.Version` — NOT `main.Version`. The Makefile wires `-X github.com/osiriscare/appliance/internal/daemon.Version=$(VERSION)`.
- **Nix flake + `git add -A`:** `buildGoModule` with `src = ../appliance` filters sources via the git index. Changes to untracked files are invisible to the build. After editing, `git add -A` before `nix build`.

## Version alignment

`iso/appliance-disk-image.nix` pins `daemonVersion = "x.y.z"`. If you bump the daemon, bump that string in the same PR — the ISO's build of the daemon MUST match what's deployed through fleet orders.

## Three invariant documents (never stale)

1. [ADR 2026-04-24 — Source-of-Truth Hygiene](../docs/adr/2026-04-24-source-of-truth-hygiene.md)
2. [Post-mortem PROCESS.md](../docs/postmortems/PROCESS.md)
3. [Root CLAUDE.md](../CLAUDE.md) — privileged-access chain, three-list lockstep, fleet-order auth.

## Deploy discipline

- **Never scp a binary to an appliance.** Use fleet orders (`update_daemon`) — that path has signature verification + rollback detection (daemon 0.4.3+).
- Binary served from `https://api.osiriscare.net/updates/appliance-daemon-<version>` — NOT `release.osiriscare.net` (no DNS A record).

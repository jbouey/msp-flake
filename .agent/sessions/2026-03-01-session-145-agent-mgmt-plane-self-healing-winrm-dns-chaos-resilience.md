# Session 145 - Agent Management Plane Self-Healing + Chaos Resilience

**Date:** 2026-03-01
**Started:** 12:42
**Previous Session:** 144

---

## Goals

- [x] Complete 4 remaining gaps (agent sync, healing order signing, AD trust, version stamping)
- [x] Add management plane self-healing to Go agent (WinRM + DNS checks)
- [x] Rebuild and stage agent binary with new checks
- [ ] Deploy updated agent to ws01 (blocked: WinRM auth broken by chaos lab)

---

## Progress

### Completed

1. **Agent sync to VPS go_agents table** — Daemon includes connected agents in checkin, backend upserts into go_agents (ea35e77)
2. **Healing order host scope fix** — Orders now signed with canonical appliance_id (site_id + MAC) instead of UUID (ea35e77)
3. **Version stamping** — Makefile uses `git describe`, agent shows commit hash in logs (ea35e77)
4. **Management plane self-healing** — New `winrm` and `dns_service` checks + healing handlers (31a33b2)
5. **Agent binary staged** — v31a33b2 at `/var/lib/msp/agent/osiris-agent.exe` on physical appliance

### Blocked

- **ws01 agent down** — Self-update swapped binary but restart script failed (WinRM auth chaos)
- **WinRM NTLM auth** — Both DC and ws01 rejecting NTLM, Negotiate-only after reboot
- **iMac intermittent** — Can't check/fix chaos crons when unreachable

---

## Files Changed

| File | Change |
|------|--------|
| `appliance/internal/daemon/phonehome.go` | ConnectedAgent struct + CheckinRequest field |
| `appliance/internal/daemon/daemon.go` | Populate agent data from registry in runCheckin |
| `mcp-server/central-command/backend/sites.py` | ConnectedAgentInfo model + Step 3.7 upsert |
| `mcp-server/main.py` | Canonical appliance_id for healing order signing |
| `agent/Makefile` | Git-based VERSION + GIT_COMMIT ldflags |
| `agent/cmd/osiris-agent/main.go` | Version="dev", GitCommit var |
| `agent/internal/checks/winrm.go` | **NEW** WinRM compliance check |
| `agent/internal/checks/dns.go` | **NEW** DNS service compliance check |
| `agent/internal/checks/checks.go` | Register WinRMCheck + DNSCheck |
| `agent/internal/healing/executor.go` | healWinRM + healDNS handlers |
| `appliance/internal/grpcserver/server.go` | healMap + EnabledChecks for winrm, dns_service |

## Commits
- `ea35e77` — agent sync, healing order fix, version stamping
- `31a33b2` — management plane self-healing (WinRM + DNS)

---

## Next Session

1. Get ws01 agent running via VBoxManage console or WinRM auth fix
2. Verify WinRM self-healing loop works end-to-end
3. Fix chaos lab crons to exclude WinRM/SSH from disruption targets
4. Deploy updated daemon to appliance (new healMap entries)

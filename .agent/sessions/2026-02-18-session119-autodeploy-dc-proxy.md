# Session: 2026-02-18 - Auto-Deploy to AD Workstations + DC Proxy Fallback

**Duration:** ~6 hours
**Focus Area:** Go appliance daemon — zero-friction agent deployment to AD workstations

---

## What Was Done

### Completed
- [x] Fixed client portal compliance report SQL bug (commit 65f86f7) — `client_org_sites` table didn't exist, changed to join via `sites.client_org_id` directly
- [x] Implemented auto-deploy pipeline in `appliance/internal/daemon/autodeploy.go` (~900 lines)
- [x] AD enumeration → connectivity check → Direct WinRM deploy (5-step pipeline)
- [x] NTLM auth fix in `winrm/executor.go` (Basic → ClientNTLM)
- [x] Added WinRM GPO configuration via Default Domain Policy
- [x] Added concurrency guard (atomic CAS) to prevent overlapping deploy cycles
- [x] Added fallback chain: Direct WinRM → DC Proxy (Invoke-Command via Kerberos) → retry next cycle
- [x] Integrated autoDeployer into daemon.go
- [x] Added GRPCListenAddr() to config.go

### Partially Done
- [ ] v7 DC proxy + NETLOGON approach — code compiles clean but NOT yet deployed/tested

### Not Started (planned but deferred)
- [ ] End-to-end test of NETLOGON binary distribution
- [ ] SPN registration automation

---

## Key Decisions Made

| Decision | Rationale | Impact |
|----------|-----------|--------|
| Direct WinRM NTLM first, DC proxy fallback | Simplest path for domain-joined machines; DC proxy covers non-direct cases | Two-stage fallback handles both domain and edge cases |
| NETLOGON share for binary distribution | Universal AD share, already replicated to all DCs, no extra infra | Agent binary accessible from any domain-joined workstation |
| Atomic CAS concurrency guard | Prevent overlapping deploy cycles from timer + manual trigger | Thread-safe without mutexes |
| Negotiate auth instead of strict NTLM | Kerberos preferred when available, NTLM fallback automatic | Covers both domain and workgroup scenarios |
| GPO-based WinRM config | Ensures future workstations automatically have WinRM enabled | No per-machine setup needed |

---

## Files Modified

| File | Change |
|------|--------|
| `appliance/internal/daemon/autodeploy.go` | NEW ~900 lines — full auto-deploy with fallback chain |
| `appliance/internal/daemon/daemon.go` | Integrated autoDeployer into daemon lifecycle |
| `appliance/internal/daemon/config.go` | Added GRPCListenAddr() method |
| `appliance/internal/winrm/executor.go` | Switched from Basic to ClientNTLM auth |
| `mcp-server/central-command/backend/client_portal.py` | SQL fix — use sites.client_org_id instead of nonexistent client_org_sites table |

---

## Tests Status

```
Go: compiles clean (go build ./...)
Python: not re-run (no Python changes beyond SQL fix)
New tests added: none yet for autodeploy
```

---

## Blockers Encountered

| Blocker | Status | Resolution |
|---------|--------|------------|
| NVWS01 returns 401 on direct WinRM NTLM from Linux | Expected | Non-domain Linux can't do Kerberos; added DC proxy fallback |
| DC Kerberos also failed (0x80090322) | Open | Missing HTTP SPNs + TrustedHosts — v7 adds SPN registration + TrustedHosts config |
| WinRM not enabled on workstations by default | Resolved | Added GPO configuration via Default Domain Policy |

---

## Next Session Should

### Immediate Priority
1. Deploy v7 autodeploy.go to physical appliance and test NETLOGON approach
2. Register HTTP SPNs on DC for WinRM Kerberos
3. Configure TrustedHosts on DC
4. Verify end-to-end: appliance → NETLOGON copy → DC proxy → workstation install

### Context Needed
- v7 code is uncommitted in autodeploy.go — compile-tested only
- The fallback chain order: Direct WinRM NTLM → DC Proxy (Invoke-Command) → retry next cycle
- NETLOGON share path: `\\<DC>\NETLOGON\osiris-agent\`
- Physical appliance is at 192.168.88.241, DC is YOURDC in the AD domain

### Commands to Run First
```bash
cd /Users/dad/Documents/Msp_Flakes/appliance
go build ./...  # Verify clean compile
ssh root@192.168.88.241  # Check appliance state
```

---

## Environment State

**VMs Running:** Physical appliance online
**Tests Passing:** Go compiles clean
**Web UI Status:** Working (SQL fix deployed)
**Last Commit:** aa54a7a feat: zero-friction auto-deploy (v7 changes uncommitted)

---

## Commits

- `65f86f7` fix: client portal compliance report — use sites.client_org_id directly
- `aa54a7a` feat: zero-friction auto-deploy — spread agent to all AD workstations

---

## Notes for Future Self

- The core problem: Linux appliance (non-domain) cannot do Kerberos to Windows workstations. Direct NTLM works for some scenarios but 401s are expected for hardened workstations.
- The v7 solution uses the DC as a trusted proxy — the appliance authenticates to the DC (which trusts it via Negotiate), and the DC uses Invoke-Command with Kerberos to reach workstations.
- NETLOGON share avoids needing to push the binary over WinRM (size limits, reliability).
- SPN registration (`setspn -A HTTP/dc-hostname DC$`) is required for Kerberos to work with WinRM on the DC.

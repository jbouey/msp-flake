# Session 113 - GPO Deployment Pipeline Complete + Healing Gap Investigation

**Date:** 2026-02-17
**Started:** 02:50
**Previous Session:** 112

---

## Goals

- [x] Complete Phase 3: Proto update + certificate auto-enrollment (Go + Python)
- [x] Complete Phases 4-6: Wire CA, DNS SRV, GPO into appliance boot sequence
- [x] Run Linux chaos attacks on Ubuntu VM (.242)
- [x] Investigate Windows healing gaps (firewall, network profile, registry persistence)
- [x] Clear flap suppressions
- [x] Update technical documentation (.md skills docs)

---

## Progress

### Completed

1. **Proto + Cert Enrollment (Phase 3)** — Added needs_certificates/cert PEM fields to proto, regenerated Go+Python stubs, implemented cert enrollment in grpc.go and grpc_server.py
2. **Orchestration Wiring (Phases 4-6)** — CA init, DNS SRV registration, GPO deployment all wired into appliance_agent.py boot sequence
3. **Linux Chaos Attacks** — 6/6 injected on .242, 5/6 auto-healed at L1 (crypto verify failed)
4. **Healing Gap Root Cause** — Identified: appliance firewall escalation is correct (NixOS); Windows firewall_status IS healing (DB confirms success on .244/.251); service flapping is GPO conflict (correct flap detection)
5. **Flap Suppressions** — Cleared 6, but they regenerate due to GPO conflicts (by design)
6. **Docs Updated** — api.md and backend.md updated with gRPC v0.3.0, CA, DNS SRV, GPO

### Blocked

- WinRM session exhaustion on .251 (HTTP 400 after ~15 sequential PS commands)
- LIN-CRYPTO-001 verify phase fails after successful remediate
- GPO in lab overrides healed settings (services, screen lock) causing flap loops

---

## Files Changed

| File | Change |
|------|--------|
| `agent/proto/compliance.proto` | Added cert enrollment fields |
| `agent/proto/compliance.pb.go` | Regenerated |
| `agent/proto/compliance_grpc.pb.go` | Regenerated |
| `agent/internal/transport/grpc.go` | Cert enrollment flow, v0.3.0 |
| `agent/Makefile` | VERSION 0.3.0 |
| `packages/.../compliance_pb2.py` | Regenerated |
| `packages/.../compliance_pb2_grpc.py` | Regenerated |
| `packages/.../grpc_server.py` | agent_ca parameter, cert issuance |
| `packages/.../appliance_agent.py` | CA/DNS/GPO wiring |
| `.claude/skills/docs/api/api.md` | gRPC v0.3.0, cert enrollment, GPO |
| `.claude/skills/docs/backend/backend.md` | New modules, deployment pipeline |

---

## Next Session

1. Fix WinRM session exhaustion — batch checks or reuse sessions more aggressively
2. Fix LIN-CRYPTO-001 verify phase — align verify check with remediation output
3. Address GPO conflicts — either modify lab GPO or teach flap detector to ignore known GPO-managed settings
4. Deploy Go agent v0.3.0 .exe to Windows VMs and test SCM integration
5. Run full chaos test with all fixes
6. Commit and push to main

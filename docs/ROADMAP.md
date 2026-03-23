# Implementation Roadmap

## Zero-to-MVP Build Plan

**Timeline:** ~5-6 full-time weeks to functioning pilot
**Engineer:** Solo operator
**Outcome:** Each new client = Terraform run + DNS entry

| Step | Goal | Time |
|------|------|------|
| 0. Scope lock | Service catalog, "not included" list | ½ day |
| 1. Repo skeleton | Top-level dirs, empty flake.nix/main.tf | ½ day |
| 2. Log watcher | fluent-bit + Python tailer, health endpoint | 3 days |
| 2.5 Baseline profile | NixOS-HIPAA v1 + controls mapping | 1 day |
| 3. Central queue | Redis Streams or NATS JetStream | 1 day |
| 4. Basic MCP server | FastAPI + 5 tools | 4 days |
| 5. Guardrails | Whitelist, cooldown, validation | 2 days |
| 6. LLM prompt | Incident → runbook selection | 2 days |
| 7. Closed loop | Verify fix or escalate | 1 day |
| 8. Client deploy | Terraform module | 3 days |
| 9. CI/CD | GitHub Actions | ½ day |
| 10. Security hardening | mTLS, Vault/SOPS, audit logs | 2 days |
| 11. Lab burn-in | Deploy to test VMs | 1 week |
| 12. Test-restore | Weekly backup verification runbook | 1 day |
| 13. Compliance packet | Automated report generation | 1 day |
| 14. First pilot | Real client, 30-day monitoring | 2 days + 30 days |

**Scale:** Each new client adds ~2 hours setup time

## Implementation Phases

### Phase 1: Foundation (Week 0-2)
- [ ] baseline/hipaa-v1.yaml - ~30 toggles (SSH, users, crypto, logging)
- [ ] controls-map.csv - HIPAA Rule → NixOS module mapping
- [ ] exceptions/ directory - Per-client exceptions with risk/expiry
- [ ] Event queue with tenant namespacing

### Phase 2: MCP Architecture (Week 3-4)
- [ ] Planner (LLM selects runbook ID only)
- [ ] Executor (runs pre-approved runbook steps)
- [ ] runbooks/ directory with HIPAA citations
- [ ] Evidence writer (hash-chain + WORM)

### Phase 3: Compliance Pipeline (Week 5-6)
- [ ] SBOM + image signing in CI
- [ ] LUKS + SSH-certs in client flake
- [ ] Weekly test-restore runbook
- [ ] Monthly compliance packet (Markdown → PDF)

## Runbook Directory Structure

```
mcp-server/
├── planner.py        # LLM selects runbook ID only
├── executor.py       # Runs pre-approved steps
└── runbooks/
    ├── RB-BACKUP-001-failure.yaml
    ├── RB-CERT-001-expiry.yaml
    ├── RB-DISK-001-full.yaml
    ├── RB-SERVICE-001-crash.yaml
    ├── RB-CPU-001-high.yaml
    └── RB-RESTORE-001-test.yaml
```

## Current Implementation Status

**Phase:** Production — Push-First Architecture
**Go Daemon:** v0.3.26 (appliance) | 3 Go Agents deployed (NVDC01, NVWS01, iMac)
**ISO Version:** installer-v8-dialog-tui (deployed to physical appliance)
**Updated:** 2026-03-22 (Session 183)

**Deliverables:**
- ✅ NixOS Compliance Agent - Production flake with 27 options
- ✅ Pull-Only Architecture - No listening sockets, outbound mTLS
- ✅ Go Daemon - Production agent (replaced Python agent), full L1/L2/L3 healing
- ✅ Go Agents - Push-first per-host compliance (Windows, macOS, Linux)
- ✅ Fleet Orders - Ed25519-signed, auto-recovery, configure_workstation_agent lifecycle
- ✅ Three-Tier Healing - L1 deterministic (112 rules) → L2 LLM → L3 human → L4 admin
- ✅ Partner Portal - OAuth + email/password + TOTP 2FA
- ✅ Client Portal - Email/password + magic link + TOTP 2FA
- ✅ Learning System - Data flywheel with post-promotion monitoring + rollback
- ✅ Evidence Pipeline - WORM storage, OTS proofs (2705 anchored), compliance packets
- ✅ WinRM + macOS Scanning - 3-tier WinRM fallback, 14 macOS checks, event log analysis
- ✅ Site Decommission - Export + deactivate + audit trail + frontend modal
- ✅ Org Consolidation - Health endpoint, PHI boundary, partner org view
- ✅ RLS Tenant Isolation - 28 tables, GUC-based, PgBouncer transaction pooling
- ✅ Prometheus Metrics - /metrics endpoint with 7 gauges/counters

**Key Files:**
- `appliance/appliance-daemon/` - Go daemon (production agent)
- `agent/osiris-agent/` - Go per-host agent
- `iso/appliance-disk-image.nix` - Installed system config (what nixos-rebuild uses)
- `mcp-server/app/main.py` - FastAPI application entry point
- `mcp-server/app/dashboard_api/` - Routes, models, services

**Next Priorities:**
- First paying pilot client onboarding
- Compliance packet PDF generation improvements
- Partner white-label customization
- Multi-site fleet scaling (5+ sites)

## Quick Checklist: This Week

- [ ] baseline/hipaa-v1.yaml with ~30 toggles
- [ ] 6 runbook files with HIPAA refs + evidence fields
- [ ] Evidence writer: hash-chain + WORM push
- [ ] SBOM + image signing in CI
- [ ] LUKS + SSH-certs in client flake
- [ ] One compliance packet prototype

## Target Market

| Size | Providers | Staff | Device | Price |
|------|-----------|-------|--------|-------|
| Small | 1-5 | <10 | Intel NUC, 4GB | $200-400/mo |
| Mid | 6-15 | 10-50 | Server, 16GB | $600-1200/mo |
| Large | 15-50 | 50-200 | Dedicated, 32GB | $1500-3000/mo |

## Competitive Positioning

**What You Won't Have:**
- DoD STIG certification
- Device attestation for classified systems

**What You WILL Have:**
- Named baseline (NixOS-HIPAA v1) with control mapping
- Evidence artifacts for every action
- Auditor-ready compliance packets
- Deterministic builds via Nix flakes
- Append-only audit trail via MCP
- WORM storage for tamper-evident evidence
- Cost advantage at SMB scale

**Market Position:** "Anduril-style compliance rigor, tailored for healthcare SMBs"

## Session Tracking

When done working, update:
1. `.agent/TODO.md` - Mark completed items
2. `.agent/sessions/YYYY-MM-DD-description.md` - Create session log

See: `.agent/CONTEXT.md` for current session state

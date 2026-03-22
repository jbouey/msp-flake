# Session 181 — Production Stability, Checkin Fix, Flywheel Audit, 10 Features, v0.3.24

**Date:** 2026-03-19
**Duration:** ~5 hours
**Previous Session:** 180

---

## Goals
- [x] Diagnose VM appliance "offline" on dashboard
- [x] Fix checkin consistency
- [x] Audit flywheel system
- [x] Audit runbook system
- [x] Run full unit tests
- [x] Implement 10 production stability features
- [x] Fix macOS scanning pipeline
- [x] Deploy v0.3.24 to both appliances
- [x] Clean up stale data

---

## Progress

### Completed

**Bugs Fixed (13 commits):**
- Checkin heartbeats from device_sync + evidence submission
- CSRF exemption for log ingestion
- Gzip auth decompression for log shipper
- Credential delivery always-on (removed has_local_credentials gate)
- Linux/macOS scan timer race (don't burn interval on empty targets)
- TLS TOFU pin auto-clear on cert rotation
- Checkin resilience (client recreation after 3 failures)
- Fleet order fallback endpoint
- ARP MAC parser for correct device MACs
- Wake-on-LAN for sleeping macOS targets
- nix-collect-garbage safety (7d retention)

**10 Production Features:**
1. Offline alerting (partner email)
2. Checkin resilience (Go)
3. Flywheel validation (runbook check)
4. Fleet order fallback (GET endpoint)
5. Structured request logging
6. Stale data reconciliation (5min job)
7. Runbook category cleanup (migration 094)
8. Rule signature enforcement (configurable)
9. Prometheus metrics (/metrics)
10. Flywheel rate limit (5/cycle)

**Flywheel Hardening:**
- L2-only promotion eligibility
- Post-promotion monitoring (auto-disable <70%)
- Admin rollback endpoints

**Data Cleanup:**
- 514 stale incidents resolved
- 71 bad pattern stats deleted
- 20 orphan L1 rules disabled
- 34 home network devices purged
- Runbooks re-categorized

**Tests:** 203 Python + 17 Go packages, 0 failures

### Blocked
- iMac SSH intermittent (works briefly then drops)
- macOS scan never completed (iMac unreachable at scan time)

---

## Files Changed

| File | Change |
|------|--------|
| `mcp-server/central-command/backend/sites.py` | Credential delivery always-on, gzip auth |
| `mcp-server/central-command/backend/csrf.py` | /api/logs/ CSRF exempt |
| `mcp-server/central-command/backend/device_sync.py` | site_appliances heartbeat |
| `mcp-server/central-command/backend/evidence_chain.py` | site_appliances heartbeat |
| `mcp-server/central-command/backend/fleet_updates.py` | Fleet order fallback endpoint |
| `mcp-server/central-command/backend/health_monitor.py` | Offline email alerting |
| `mcp-server/central-command/backend/routes.py` | Admin rule disable/enable |
| `mcp-server/central-command/backend/prometheus_metrics.py` | NEW: metrics endpoint |
| `mcp-server/central-command/backend/migrations/094_*.sql` | NEW: runbook categories |
| `mcp-server/central-command/backend/tests/test_flywheel_promotion.py` | 4 new tests |
| `mcp-server/main.py` | Reconciliation, flywheel hardening, logging middleware, metrics |
| `appliance/internal/daemon/daemon.go` | Checkin resilience, fleet fallback, v0.3.24 |
| `appliance/internal/daemon/phonehome.go` | Client recreation, TLS pin clear, order fetch |
| `appliance/internal/daemon/macosscan.go` | Wake-on-LAN, SSH probe |
| `appliance/internal/daemon/driftscan.go` | Scan timer fix (empty targets) |
| `appliance/internal/daemon/netscan.go` | ARP MAC parser |
| `appliance/internal/daemon/healing_executor.go` | nix-collect-garbage safety |
| `appliance/internal/daemon/config.go` | RequireSignedRules field |
| `appliance/internal/healing/l1_engine.go` | Signature enforcement toggle |
| `appliance/internal/orders/processor.go` | ApplianceID() getter |

---

## Next Session

1. **Replace VM appliance with second physical hardware** — eliminates VirtualBox issues
2. **Validate macOS scanning end-to-end** — fix iMac SSH, confirm 13 checks return data
3. **Network scanner subnet filtering** — restrict to configured ranges only
4. **Compliance scoring accuracy** — exclude unmanaged devices from compliance %
5. **Rotate external credentials** — Anthropic, AWS, SMTP, OAuth (hard blocker for customers)
6. **Client portal real-user testing** — edge cases unknown
7. **Billing/Stripe integration testing** — subscription gating depends on this

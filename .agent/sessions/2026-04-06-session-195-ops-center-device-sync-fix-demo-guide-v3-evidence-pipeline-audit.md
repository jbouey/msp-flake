# Session 195 — Ops Center, Device Sync Fix, Demo Guide v3

**Date:** 2026-04-06
**Commits:** 6
**Daemon:** v0.3.81 | **Agent:** v0.4.5

---

## Goals

- [x] Investigate and fix device sync pipeline (.250/.251 not updating)
- [x] Fix Lanzaboote Secure Boot error on nixos-rebuild switch
- [x] Build /ops Operations Center page (5 traffic lights + audit readiness)
- [x] Expand /docs with maintenance runbooks and reference material
- [x] Update demo video guide to v3
- [x] Fix rogue scheduled task allowlists

---

## Progress

### Device Sync Fix (Critical)
- Root cause: 3 competing sync sources — UUID replay overwriting IP-format device_ids
- CASE expression prevents UUID overwriting IP, GREATEST() prevents timestamp revert
- Cleaned 16 stale incidents, all 4 key devices updating live

### Ops Center
- ops_health.py: 5 traffic-light statuses + partner-scoped variant
- audit_report.py: per-org readiness badge + countdown + BAA config
- 56 unit tests passing
- OpsCenter.tsx + StatusLight.tsx + Documentation expansion (7 runbooks + 4 reference articles)

### Evidence Pipeline
- Discovered compliance_bundles has 231K entries (was querying legacy evidence_bundles table)
- 94% Ed25519 signed, 56% BTC-anchored, chain position 137,069 — production-ready

### Other
- Lanzaboote disabled (Secure Boot not in BIOS)
- Rogue task allowlist aligned (XblGameSaveTask, UserLogonTask)
- Sitemap + demo guide v3 PDF

### Blocked
- Mesh asymmetric routing (consumer router hardware)
- iMac SSH port 2222 (reverse tunnel workaround)
- Witness submit 500s
- Autodeploy file lock on agent.b64

---

## Files Changed

| File | Change |
|------|--------|
| backend/device_sync.py | CASE expression, GREATEST(), WHERE id, credential IP JOIN fix |
| backend/ops_health.py | NEW: 5 traffic-light compute + endpoints |
| backend/audit_report.py | NEW: audit readiness badge + countdown + config |
| backend/tests/test_ops_health.py | NEW: 36 tests |
| backend/tests/test_audit_report.py | NEW: 20 tests |
| backend/migrations/124_ops_audit_fields.sql | NEW: baa_on_file + next_audit_date |
| frontend/src/pages/OpsCenter.tsx | NEW: /ops page |
| frontend/src/components/composed/StatusLight.tsx | NEW: traffic-light component |
| frontend/src/pages/Documentation.tsx | 7 runbooks + 4 reference articles |
| frontend/src/constants/status.ts | Ops status config |
| frontend/src/constants/copy.ts | Ops labels + tooltips |
| iso/appliance-disk-image.nix | Lanzaboote disabled |
| appliance/internal/daemon/driftscan.go | Rogue task allowlist aligned |
| mcp-server/main.py | ops_health + audit_report routers registered |
| demo-videos/DEMO-VIDEO-GUIDE.html | v3 updates |

---

## Phase 2 (same session, continued)

### CI/CD Fix
- Pinned Python to 3.11, all deps exact-pinned to match production
- Added pydantic-core==2.27.2 + pydantic-settings==2.7.0 (missing transitive deps)

### Autodeploy File Lock
- Added per-target sync.Mutex with TryLock in autodeploy.go
- Concurrent deploy attempts to same hostname now skip with log message

### Witness Submit 500s — FIXED
- Root cause: `/api/witness/submit` missing from CSRF exempt paths
- Added to EXEMPT_PATHS in csrf.py
- Verified: 200 OK, attestations flowing, cross-witnessed evidence live

### AuditReadiness.tsx — COMPLETE
- Full component: badge, 6-item checklist, countdown, blockers, actions
- Generate Audit Report, Set Audit Date, Toggle BAA buttons
- Integrated into OpsCenter.tsx replacing placeholder

## Additional Files Changed

| File | Change |
|------|--------|
| backend/csrf.py | Witness submit CSRF exemption |
| backend/requirements.txt | Exact version pins + pydantic-core/settings |
| backend/tests/test_production_security.py | Witness exemption test |
| .github/workflows/deploy-central-command.yml | Python 3.11 pin |
| appliance/internal/daemon/autodeploy.go | Per-target deploy mutex |
| frontend/src/components/composed/AuditReadiness.tsx | NEW: full component |
| frontend/src/pages/OpsCenter.tsx | AuditReadiness integration |

## Next Session

1. Verify CI/CD passes with pinned deps
2. Fleet deploy autodeploy.go fix (needs daemon rebuild)
3. Demo video recording session (guide v3 ready, system has real data)
4. Mesh routing — needs same-subnet co-location for testing

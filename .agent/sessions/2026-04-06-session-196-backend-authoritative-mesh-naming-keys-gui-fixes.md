# Session 196 — Backend-Authoritative Mesh, Per-Appliance Identity, GUI Fixes

**Date:** 2026-04-06
**Daemon:** v0.3.81 → v0.3.82
**Commit:** 89f0d28 (22 files, +2249/-150)

---

## Summary

Resolved 5 issues from user screenshots, then designed and implemented a backend-authoritative mesh architecture for the 3-node multi-subnet testbed.

## Issues Fixed

1. **Appliance cards unclickable** — `z-10` on GlassCard created stacking context. Removed.
2. **Notification panel hiding behind sidebar** — Header `z-10` couldn't escape stacking context above Sidebar `z-50`. Bumped Header to `z-50`.
3. **Chaos lab SSH failure** — Default IP in `chaos_workstation_cadence.py` updated from `.246` to `.235`.
4. **Evidence chain broken (2 rejections)** — Single `sites.agent_public_key` caused key collisions with 3 appliances. Fixed with per-appliance keys (migration 126).
5. **All appliances named "osiriscare"** — Added `display_name` column (migration 125) with iterative naming.

## Backend-Authoritative Mesh (Hybrid C+)

### Problem
3 appliances across 2 subnets (88.x + 0.x). T640 on 0.x couldn't probe 88.x peers back — asymmetric routing. Client-side hash ring diverged.

### Solution
Backend computes target assignments server-side during checkin using identical hash ring algorithm (Python port of Go). Daemon prefers server assignments, falls back to local ring after 15 min.

### What Shipped
- **hash_ring.py** — Python consistent hash ring, cross-language test vectors
- **STEP 3.8c** in checkin handler — server-side target assignment
- **Daemon v0.3.82** — `OwnsTarget()` prefers server assignments
- **Evidence dedup** — 15-min window prevents overlap during failover
- **Removed** — split-brain detection, Network Stability panel, independent mode UI, mesh_topology config
- **Migrations** — 125 (display_name), 126 (per-appliance keys), 127 (assigned_targets)

### Test Coverage
- 9 Python hash ring tests
- 7 target assignment tests
- 4 evidence dedup tests
- 4 Go server assignment + cross-language tests

### Testbed State
| Appliance | IP | Subnet | Version | Status |
|-----------|-----|--------|---------|--------|
| osiriscare (T640) | 192.168.0.11 | 0.x | 0.3.82 | Online, 0 targets (no creds) |
| osiriscare-2 (Physical) | 192.168.88.241 | 88.x | 0.3.82 | Online, 4 targets |
| osiriscare-installer (T740) | 192.168.88.232 | 88.x | 0.3.82 | Online, 0 targets (no creds) |

## Spec + Plan
- `docs/superpowers/specs/2026-04-06-backend-authoritative-mesh-design.md`
- `docs/superpowers/plans/2026-04-06-backend-authoritative-mesh.md`

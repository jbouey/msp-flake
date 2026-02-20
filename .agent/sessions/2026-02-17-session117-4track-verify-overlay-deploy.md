# Session 117: 4-Track Plan Verification + v1.0.74 Overlay Deploy

**Date:** 2026-02-17
**Status:** COMPLETE

## Commits

| Hash | Description |
|------|-------------|
| `03693aa` | fix: msp-first-boot hard dependency on auto-provision service |

## 4-Track Audit Plan — Verified Complete

Audited all 22 items across 4 tracks. 21/22 were already implemented in previous sessions. Applied the one remaining fix:

| Track | Items | Status |
|-------|-------|--------|
| A: Go Agent | 7/7 | All done (GC pinning, error logging, backpressure, WMI checks, sanitization, timeout) |
| B: Central Command | 7/7 | All done (onboarding endpoints, pagination, metrics, broadcast, cache TTL, vite proxy, migration) |
| C: NixOS Hardening | 4/4 | 3 done previously, **C3 applied this session** (msp-first-boot requires) |
| D: GPO Pipeline | 4/4 | All done (cert warning, rollback, flag logging, 951 lines of tests) |

### C3 Fix Details
Added `requires = [ "msp-auto-provision.service" ];` to `msp-first-boot` in `iso/appliance-image.nix:1065`. This makes it a hard dependency — if provisioning fails, first-boot won't run silently without identity.

Verified: `nix eval --json` confirms requires is set. Python tests: 1037 passed.

## v1.0.74 Overlay Deployment

1. Built overlay: `compliance_agent-1.0.74.tar.gz` (444KB)
2. Uploaded to VPS: `/opt/mcp-server/agent-packages/` + `/var/www/updates/agent-overlay.tar.gz`
3. Issued `update_agent` orders for both appliances:
   - Physical: `overlay-1074-phys` for `physical-appliance-pilot-1aea78`
   - VM: `overlay-1074-vm` for `test-appliance-lab-b3c40c`

Appliances will pick up the overlay on next checkin (~5 min cycle).

## Changes in v1.0.74 (since v1.0.73)

Includes all code from the 4-track audit plan:
- GPO rollback mechanism
- gRPC cert enrollment warnings
- GPO flag persistence error logging
- 43 new tests (agent_ca, gpo_deployment, dns_registration, agent_deployment)
- All audit fixes from Go agent, Central Command, and NixOS tracks

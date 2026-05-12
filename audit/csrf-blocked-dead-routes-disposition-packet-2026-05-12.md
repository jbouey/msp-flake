# Task #120 disposition packet — 10 CSRF-blocked appliance/admin endpoints

**Status:** RESEARCH DELIVERABLE — Gate A required before implementation.
**Date:** 2026-05-12.
**Author:** Session 220 close-out work.

## Scope reframe

Original task #113 audit identified 7 "Category-C zero-auth" endpoints; runtime verification (`audit/csrf-blocks-zero-auth-endpoints-finding-2026-05-11.md`) expanded this to **10 silent-CSRF-403 routes** across 7 source files. Task #122 CI gate (commit `a612a7e6`) currently allowlists 6 of these as `_KNOWN_BLOCKED_DEAD_ROUTES`. This packet is the disposition triage to close that ratchet to 0.

## 24-hour prod traffic verification

`docker logs mcp-server --since 24h | grep -E "POST /api/(discovery|sensors|provisioning/rekey|framework-sync/trigger|runbook-config|evidence/verify-ots|portal/compliance-snapshot)"` returns:

```
14 hits total — ALL from 172.25.0.1 (Docker bridge loopback)
  - 8 × POST /api/discovery/status
  - 4 × POST /api/discovery/report
  - 2 × POST /api/sensors/heartbeat
  - 0  × all other 7 endpoints
```

The 14 hits are MY OWN earlier verification probes from this session. **Zero external caller traffic in 24h** for ANY of the 10 endpoints. Combined with the prior 12h check at task #113 time → no real appliance ever calls these in observable history.

All hits returned **HTTP 500** in those log lines (pre-`f701ca43` deploy). Post-fix the CSRF middleware now returns 403 cleanly (verified `curl http://178.156.162.116:8000/api/discovery/status -X POST` → HTTP 403 today).

## Per-endpoint disposition

Each row: (file:line, handler, current dep-shape, prod-callers in 24h, recommended action, blast-radius).

| # | File:line | Handler | Auth dep | 24h hits | Recommended action | Blast-radius if wrong |
|---|---|---|---|---|---|---|
| 1 | `portal.py:2608` | `receive_compliance_snapshot` | none (deprecated stub) | 0 | **DELETE** | None — stub is a no-op |
| 2 | `discovery.py:344` | `update_scan_status` | none | 0 | **DELETE** | None — daemon uses `/report` only per task #113 reframe |
| 3 | `discovery.py:194` | `report_discovery_results` | `_enforce_site_id` after Commit 2 hardening | 0 | **EXEMPT + keep** | Adding to `EXEMPT_PATHS` activates the hardened handler; daemon path may need real traffic before any code change |
| 4 | `sensors.py:271` | `complete_sensor_command` | `_enforce_site_id` | 0 | **DELETE entire router (sensors.py)** | None — sensors router var is unregistered in main.py (confirmed by task #122 gate's registration filter) |
| 5 | `sensors.py:324` | `record_sensor_heartbeat` | `_enforce_site_id` | 0 | same as #4 | same |
| 6 | `sensors.py:579` | `record_linux_sensor_heartbeat` | `_enforce_site_id` | 0 | same as #4 | same |
| 7 | `provisioning.py:816` | `rekey_appliance` | none | 0 | **HARDEN + KEEP** — appliance rekey is a real path; gate it behind `Depends(require_appliance_bearer)` AND add to EXEMPT_PATHS | Rekey path is part of long-lived appliance-identity rotation; deleting risks breaking a future rekey ceremony |
| 8 | `framework_sync.py:207` | `trigger_sync_all` | none (BackgroundTasks) | 0 | **HARDEN — `Depends(require_auth)` (admin)** | Admin-portal triggers a fleet-wide framework re-sync; DoS-class without auth |
| 9 | `framework_sync.py:214` | `trigger_sync_one` | none (BackgroundTasks) | 0 | same as #8 | same |
| 10 | `runbook_config.py:462` | `remove_appliance_runbook_override` | none | 0 | **HARDEN — `Depends(require_auth)` (admin)** | Removes a per-appliance runbook override; admin-only |
| 11 | `evidence_chain.py:3227` | `verify_ots_bitcoin` | none | 0 | **HARDEN — `Depends(require_auth)` (admin)** | OTS Bitcoin verification reads, no mutation; admin-only diagnostic |

Note: 11 entries here vs "10" in the audit reframe — the original count merged `discovery.py:194` and `discovery.py:344` into a single "discovery" bucket. They are separate handlers.

## Recommended PR shape

**PR-A — DELETEs (safe, zero risk):**
- `portal.py:2608` `receive_compliance_snapshot` + its `ComplianceSnapshot` Pydantic model + any route registration.
- `discovery.py:344` `update_scan_status` + its `ScanStatus` Pydantic model.
- `sensors.py` entire module (router unregistered, all 3 handlers dead).
- Remove the 6 `_KNOWN_BLOCKED_DEAD_ROUTES` entries from `tests/test_csrf_exempt_paths_match_appliance_endpoints.py` that match these deletes.
- Frontend route-orphan CI gate may need an update if `ComplianceSnapshot` or related schemas are exported.

Risk: pure code removal of unreachable surfaces. Caught by tests if any uplift exists. Sibling pattern: Session 185 deleted `server.py`.

**PR-B — HARDEN + EXEMPT (one endpoint):**
- `discovery.py:194` `report_discovery_results` — add `/api/discovery/report` to `csrf.py:EXEMPT_PATHS`. Handler is already auth-gated via `_enforce_site_id`. Removes its `_KNOWN_BLOCKED_DEAD_ROUTES` entry.

Risk: Activates a previously-dead path. Daemon's discovery code may need verification it actually sends to this endpoint (review `appliance/internal/daemon/discovery_*.go`).

**PR-C — HARDEN (admin-only, no exempt — keep CSRF-protected):**
- `provisioning.py:816` `rekey_appliance` — add `Depends(require_appliance_bearer)` (machine-to-machine path). EXEMPT_PATHS add (since appliances don't carry CSRF cookies). Removes its `_KNOWN_BLOCKED_DEAD_ROUTES` entry if any.
- `framework_sync.py:207/214` `trigger_sync_all|one` — add `Depends(require_auth)`. NO EXEMPT_PATHS add (admin browser-callable should keep CSRF).
- `runbook_config.py:462` `remove_appliance_runbook_override` — same as framework_sync.
- `evidence_chain.py:3227` `verify_ots_bitcoin` — same.

Risk: framework_sync trigger paths may have admin-portal UI buttons that need CSRF cookies wired. Frontend audit needed.

## Why three PRs, not one

1. **PR-A** is no-op deletion; safest to land first, smallest review surface.
2. **PR-B** activates one previously-dead path; needs daemon-side verification.
3. **PR-C** changes auth surface on 5 admin endpoints; needs frontend verification of CSRF-token flow.

Each PR ships with its own Gate A → Gate B; this packet is the umbrella scoping doc, not a single-PR-design.

## Out of scope for #120

- Migrating to a positive-allowlist model (auth-required-by-default) — Session 220 round-3 scope creep.
- Removing dead routers (sensors.py) requires checking that no frontend code expects `/api/sensors/*` endpoints to exist; PR-A already covers that.

## Inputs to Gate A

1. Is PR-A correct to DELETE rather than HARDEN+EXEMPT? Specifically `sensors.py` — Carol's view on whether an unregistered router with `_enforce_site_id`-gated handlers should be removed (yes) or kept as defense-in-depth dead code (no).
2. For PR-B, daemon source check: does `appliance/internal/daemon/` ever POST to `/api/discovery/report`? If not, even PR-B is delete-not-exempt.
3. For PR-C, what is the historical reason `framework_sync/trigger_*` exists without auth? Was it expected to be CRON-only (in which case → DELETE, not HARDEN)?
4. Maya: does PR-A loss of `/api/discovery/status` or `sensors.py` affect any frontend admin dashboard or partner-portal feature?
5. Steve: 3-PR shape vs single-PR — is the audit surface gain (smaller diffs, separate Gate A/B per change-class) worth the friction of 3× shipping cycles?
6. Coach: sibling pattern in Session 185 (`server.py` delete) — does PR-A match that ergonomics?

# Task #120 disposition packet — v2 (post Gate A BLOCK)

**Status:** RESEARCH DELIVERABLE — re-Gate-A required.
**Date:** 2026-05-12.
**Supersedes:** `audit/csrf-blocked-dead-routes-disposition-packet-2026-05-12.md` (v1).
**Gate A v1 verdict:** BLOCK, 5 P0s — `audit/coach-csrf-disposition-packet-gate-a-2026-05-12.md`.

## What changed from v1

| Gate A v1 P0 | v1 packet claim (WRONG) | v2 corrected |
|---|---|---|
| P0-1 | `provisioning.py:816 rekey_appliance` is dead (0 hits in 24h) | Live — daemon calls it from `appliance/internal/daemon/phonehome.go:716` POST `/api/provision/rekey`. Already CSRF-exempt via `/api/provision/`. Rare auto-recovery path (after N consecutive 401s) — 24h log silence is expected. **CLASSIFY: KEEP, no change.** |
| P0-2 | `framework_sync.py` trigger_* are CSRF-blocked dead routes | NOT CSRF-blocked — `/api/framework-sync/` is in EXEMPT_PREFIXES (csrf.py:150). Handlers have ZERO auth deps. Frontend `ComplianceLibrary.tsx:260-261` actively uses them. **CLASSIFY: real Category-C zero-auth, must HARDEN with `require_auth`. Frontend already sends session cookies — auth add is non-breaking.** |
| P0-3 | `evidence_chain.py:3227 verify_ots_bitcoin` is CSRF-blocked | NOT CSRF-blocked — `/api/evidence/` is in EXEMPT_PREFIXES (csrf.py:117). **CLASSIFY: real Category-C zero-auth, must HARDEN.** |
| P0-4 | Deleting `sensors.py` is safe — out of scope frontend-wise | SensorStatus.tsx frontend at `:37/53/69` calls `/api/sensors/sites/...`. **But the sensors router is UNREGISTERED in main.py today** (no `from dashboard_api.sensors`) — SO THE FRONTEND IS ALREADY HITTING 404. Deleting the dead backend handlers is correct; the frontend is the orphan that needs separate treatment. **CLASSIFY: backend DELETE-safe; FRONTEND orphan is a separate task.** |
| P0-5 | `runbook_config.py:462 remove_appliance_runbook_override` is POST under `/api/runbook-config` | It's `@router.delete` (line 461) under `/api/runbooks/appliances/{appliance_id}/{runbook_id}`. **CLASSIFY: not state-changing POST → not in CSRF-parity gate's scope (DELETE is also state-changing but the v1 packet had wrong method+path).** Actually — DELETE IS in `_STATE_CHANGING_METHODS` in the gate. So it IS in scope. But path is `/api/runbooks/...` which is NOT in EXEMPT_PATHS. Auth dep needed. **CLASSIFY: real zero-auth on a state-changing DELETE.** |

## Verified per-endpoint disposition (v2)

Sources verified by grep:
- Daemon Go code: `appliance/internal/daemon/phonehome.go:716` POSTs `/api/provision/rekey`.
- Frontend TS: `ComplianceLibrary.tsx`, `SensorStatus.tsx`, `useFleet.ts`.
- Backend routers: confirmed via `grep -nH "^router = APIRouter" file.py`.
- main.py imports: NO `from dashboard_api.sensors` (sensors router unregistered).

| # | File:line | Handler | Method | Full path | Auth | Real caller? | v2 disposition |
|---|---|---|---|---|---|---|---|
| 1 | `portal.py:2608` | `receive_compliance_snapshot` | POST | `/api/portal/compliance-snapshot` | none (stub) | None | **DELETE** — deprecated no-op stub, only ref in auto-gen `api-generated.ts` |
| 2 | `discovery.py:194` | `report_discovery_results` | POST | `/api/discovery/report` | `_enforce_site_id` (Commit 2) | NO daemon caller (verified `grep -rn discovery/report appliance/` empty) | **DELETE** — handler exists but daemon never sends; not a real bug |
| 3 | `discovery.py:344` | `update_scan_status` | POST | `/api/discovery/status` | none | NO daemon caller | **DELETE** |
| 4 | `sensors.py:140` | deploy sensor host | POST | `/api/sensors/sites/{site_id}/hosts/{hostname}/deploy` | `_enforce_site_id` | router unregistered → 0 callers reachable | **DELETE entire sensors.py** (3 POSTs + 1 DELETE + 7 GETs + 1 PUT). Frontend SensorStatus.tsx is independently broken (404s already); needs separate task. |
| 5 | `sensors.py:177` | DELETE sensor host | DELETE | same | same | same | same (DELETE method but unreachable) |
| 6 | `sensors.py:270` | `complete_sensor_command` | POST | `/api/sensors/commands/{command_id}/complete` | `_enforce_site_id` | unreachable | same |
| 7 | `sensors.py:323` | `record_sensor_heartbeat` | POST | `/api/sensors/heartbeat` | `_enforce_site_id` | unreachable | same |
| 8 | `sensors.py:496` | linux deploy | POST | `/api/sensors/sites/{site_id}/linux/{hostname}/deploy` | `_enforce_site_id` | unreachable | same |
| 9 | `sensors.py:579` | linux heartbeat | POST | `/api/sensors/linux/heartbeat` | `_enforce_site_id` | unreachable | same |
| 10 | `provisioning.py:815` | `rekey_appliance` | POST | `/api/provision/rekey` (CSRF-exempt) | none (manual `_resolve_admin`) | **LIVE** — daemon phonehome.go:716 | **NO CHANGE** — leave as is (rare auto-recovery, manual admin resolve is a known historical pattern, Session 213 round-table P0). |
| 11 | `framework_sync.py:206` | `trigger_sync_all` | POST | `/api/framework-sync/sync` (CSRF-exempt) | **none** | Frontend ComplianceLibrary.tsx:260 + useFleet.ts:1202 | **HARDEN: add `Depends(require_auth)` (admin)**. Frontend already sends session — non-breaking. |
| 12 | `framework_sync.py:213` | `trigger_sync_one` | POST | `/api/framework-sync/sync/{framework}` (CSRF-exempt) | **none** | Frontend useFleet.ts:1214 | same as #11 |
| 13 | `runbook_config.py:461` | `remove_appliance_runbook_override` | DELETE | `/api/runbooks/appliances/{appliance_id}/{runbook_id}` | **none** | Frontend? (needs grep) | **HARDEN: add `Depends(require_auth)`** if frontend caller exists; **DELETE** if not. |
| 14 | `evidence_chain.py:3227` | `verify_ots_bitcoin` | POST | `/api/evidence/verify-ots/...` (CSRF-exempt) | **none** | Frontend? (needs grep) | **HARDEN: add `Depends(require_auth)`** OR **DELETE** if no caller. Read-only diagnostic. |

## Revised PR shape

**PR-A — Safe DELETEs:**
- `portal.py:2608` `receive_compliance_snapshot` + `ComplianceSnapshot` model.
- `discovery.py:194` `report_discovery_results` + `discovery.py:344` `update_scan_status` + their Pydantic models.
- `sensors.py` entire module (10 handlers).
- Remove the 6 matching entries from `_KNOWN_BLOCKED_DEAD_ROUTES`.
- New task filed for the SensorStatus.tsx frontend orphan.

Risk: pure-deletion. Frontend grep confirms `SensorStatus.tsx` is the only customer of `/api/sensors/*` AND it's already broken; not making it worse.

**PR-B — Add auth to 3 real zero-auth endpoints:**
- `framework_sync.py:206/213` — add `Depends(require_auth)`.
- `evidence_chain.py:3227` — add `Depends(require_auth)` if used by frontend; delete otherwise (needs further grep).
- `runbook_config.py:461` — same.

Risk: admin path becomes auth-gated; verify session-cookie wire on each frontend caller (`ComplianceLibrary.tsx`, etc.). All three are admin-portal-only callers that already send cookies.

**PR-C — None.** Original v1 PR-C scope is absorbed into PR-A/PR-B after re-classification.

## Frontend grep needed before PR-B

For each of `verify_ots_bitcoin` + `remove_appliance_runbook_override`:
```
grep -rnE "verify-ots|runbooks/appliances/.*/{runbook_id}" mcp-server/central-command/frontend/src/
```
If used → HARDEN with auth. If not → DELETE.

## Sensors.py frontend orphan (NEW task)

`mcp-server/central-command/frontend/src/components/sensors/SensorStatus.tsx:37/53/69` fetches `/api/sensors/sites/...` but the router is unregistered. SensorStatus.tsx is already broken in prod. Either:
- (a) Register the sensors router (re-enable the feature), or
- (b) Delete SensorStatus.tsx + the parent component that mounts it.

Decision deferred — file as separate Gate-A-needed task.

## Ratchet projection (corrected)

v1 packet claimed 6→1-3 entries in `_KNOWN_BLOCKED_DEAD_ROUTES`. Correct projection:
- After PR-A: removes `/api/discovery/report` from `_KNOWN_BLOCKED_DEAD_ROUTES` (was added by task #122 gate). The other 5 entries (`/orders/acknowledge`, `/drift`, `/evidence/upload`, `/api/learning/promotion-report`, `/api/alerts/email`) are NOT in this packet's scope.
- Net: 6 → 5 entries (one cleared).
- The other 5 need a SEPARATE disposition triage (out of scope here).

## Open questions for Gate A v2

1. **Steve:** Is `provisioning.py:815` `_resolve_admin` manual-pattern still the right shape per Session 213 round-table? Should rekey migrate to `Depends(require_admin)` despite the circular-import history?
2. **Maya:** SensorStatus.tsx orphan — register router (UI is fine) or delete UI (router stays dead)? Product decision.
3. **Carol:** Do `verify_ots_bitcoin` + `remove_appliance_runbook_override` need `require_auth` or `require_admin`? Both are admin-portal-callable per their semantic role.
4. **Coach:** v1 packet had FIVE classification errors that Gate A caught. What process drove the errors? Answer: I used the 12h-log-traffic test as primary signal but didn't independently confirm CSRF-exemption status nor daemon-call status nor frontend-call status. Process improvement: every packet must include grep evidence for EACH of (CSRF-status, daemon-callers, frontend-callers) before classification.

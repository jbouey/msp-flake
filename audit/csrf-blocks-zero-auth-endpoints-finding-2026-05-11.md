# CSRF blocks "zero-auth" endpoints — reframing task #113 (2026-05-11)

## What the comprehensive audit found

`audit/zero-auth-comprehensive-audit-2026-05-11.md` (Session 220 task
#113 step 1) enumerated 349 state-changing backend endpoints and
classified 7 as Category-C "ZERO AUTH on mounted routers":

1. `runbook_config.py:461 remove_appliance_runbook_override`
2. `evidence_chain.py:3226 verify_ots_bitcoin`
3. `framework_sync.py:206 trigger_sync_all`
4. `framework_sync.py:213 trigger_sync_one`
5. `provisioning.py:815 rekey_appliance`
6. `discovery.py:343 update_scan_status`
7. `portal.py:2607 receive_compliance_snapshot` (deprecated stub)

## What runtime verification revealed

Direct curl probes to `/api/discovery/status`, `/api/discovery/report`,
`/api/sensors/heartbeat` from the VPS loopback all returned HTTP 500
— NOT auth-related. Docker logs show the actual rejection:

```
CSRF validation failed for POST /api/discovery/status (cookie=MISSING, header=MISSING)
fastapi.exceptions.HTTPException: 403: CSRF validation failed. ...
```

`csrf.py:EXEMPT_PATHS` allowlists machine-to-machine endpoints
(`/api/witness/submit`, `/api/journal/upload`, `/api/appliances/*`,
etc.) but does NOT include `/api/discovery/*` or `/api/sensors/*`.
The CSRF middleware fires BEFORE FastAPI Depends resolution → every
POST without a CSRF cookie/header is rejected → request never reaches
the handler's missing-auth check.

12-hour prod log search for `POST /api/(discovery|sensors)/` returns
**10 hits, all from local loopback (172.25.0.1) — i.e. my own
verification probes**. Zero real appliance traffic.

## Reframed security risk

| Layer | Behavior |
|---|---|
| CSRF middleware (first gate) | Blocks all callers without CSRF token/cookie → 403 → currently emitted as 500 (separate bug) |
| FastAPI Depends auth (second gate) | NEVER REACHED — CSRF rejects first |
| Handler body | NEVER REACHED |

**Attacker exploitability:** None. A real attacker without auth would
hit the CSRF wall.

**Real appliance functionality:** Also none. If any appliance code
attempts to POST to these endpoints, it gets the same CSRF block.
Same class as the Session 210-B `journal_upload` bug: appliance
trying to upload via `msp-journal-upload.timer` was being silently
CSRF-403'd, `journal_upload_events` had zero rows, and substrate
invariant `journal_upload_never_received` fired for months. Fix
was adding `/api/journal/upload` to `EXEMPT_PATHS`.

## Per-endpoint disposition

| Endpoint | Caller? | Recommended action |
|---|---|---|
| `discovery.py:343 update_scan_status` | None (CSRF blocks; daemon likely uses `/report` only) | Delete OR add to EXEMPT_PATHS + `Depends(require_appliance_bearer)` if daemon truly needs it |
| `discovery.py:194 report_discovery_results` | **Hardened in Commit 2** (eea92d6c) | Same as above — currently CSRF-blocked despite hardening |
| `sensors.py:295 record_sensor_heartbeat` | None (CSRF blocks) | Same |
| `sensors.py:548 record_linux_sensor_heartbeat` | None (CSRF blocks) | Same |
| `sensors.py:272 complete_sensor_command` | None (CSRF blocks) | Same |
| `provisioning.py:815 rekey_appliance` | None (CSRF blocks) | Same |
| `framework_sync.py:206/213 trigger_sync_*` | None (CSRF blocks) | DoS class on background-sync — admin-only path; add `Depends(require_admin)` AND `EXEMPT_PATHS` add OR keep CSRF-blocked (it's admin-portal-callable in theory) |
| `runbook_config.py:461 remove_appliance_runbook_override` | None | Same as framework_sync |
| `evidence_chain.py:3226 verify_ots_bitcoin` | None | Same |
| `portal.py:2607 receive_compliance_snapshot` | None | **Delete** — deprecated no-op stub |

## Separate bug discovered

**Starlette CSRF 403 → 500 unwrap**: when `csrf.py:190 raise HTTPException(403, ...)`
fires inside the request middleware, Starlette's TaskGroup
`collapse_excgroups` wraps it in an `ExceptionGroup` which the outer
error handler converts to 500. Customer-visible: every CSRF-rejected
POST returns 500 with `{"error": "Internal server error"}` instead
of 403. Operator UX impact + SIEM-pattern impact.

Class: `dashboard_api/csrf.py:190` `raise HTTPException(...)` inside
`async def dispatch` is not surviving the middleware chain's
`anyio.create_task_group()` unwrap.

## Round-3 task #113 reframing

Original scope: "harden 7 zero-auth endpoints."
Reframed scope:
- **Disposition triage** — none of the 7 are exploitable today (CSRF
  blocks). Decide DELETE vs HARDEN+EXEMPT per endpoint.
- **CSRF→500 unwrap bug** — separate fix, broader impact.
- **CI gate** — `test_csrf_exempt_list_matches_appliance_endpoints`
  would catch silent-block class of Session 210-B / today's audit.

Round-3 implementation NOT in this session — needs Gate A on the
new triage shape + the unwrap-bug fix scope.

## Verifiable claims

- `EXEMPT_PATHS` contents: `csrf.py:39-66` (15 entries enumerated).
- `discovery.py:343` source: no `Depends` on signature.
- `/report` hardening: `main.py` is mounted live router; `Depends`
  added in Commit 2 — but unreachable due to CSRF.
- 12h log search: `docker logs mcp-server --since 12h | grep -E "POST /api/(discovery|sensors)/" | wc -l` → 10 hits, all 172.25.0.1.
- CSRF→500 traceback: `docker logs mcp-server --since 2m | grep -A 8 "CSRF validation failed"`.

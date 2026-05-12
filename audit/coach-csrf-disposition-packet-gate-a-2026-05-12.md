# Gate A — CSRF-blocked dead-routes disposition packet (Task #120)

**Verdict: BLOCK**
**Date:** 2026-05-12
**Reviewer:** Fork Gate A (Steve / Maya / Carol / Coach)
**Packet under review:** `audit/csrf-blocked-dead-routes-disposition-packet-2026-05-12.md`

The packet's headline thesis — "all 11 endpoints are silently CSRF-403'd dead routes" — does NOT survive runtime CSRF-middleware verification. Three of the 11 endpoints are NOT CSRF-blocked at all because the `csrf.py:EXEMPT_PREFIXES` tuple already exempts their routers (`/api/provision/`, `/api/framework-sync/`, `/api/evidence/`). For those three, the "silent 403" framing is wrong; they are reachable today with **no auth and no CSRF**, which inverts the threat model the packet uses to justify "DELETE-safe" and "0 hits in 24h" disposition. Five P0s + 3 P1s below. None of the 3 PRs may proceed in current shape; the packet needs one round-trip rewrite.

---

## P0 — must close before any PR

### P0-1 (Steve, ship-blocker on PR-A): `provisioning.py:816` is LIVE-CALLED by the daemon

Packet row #7 claims `rekey_appliance` "0 hits in 24h, HARDEN + KEEP, risk: deleting risks breaking a future rekey ceremony." Runtime evidence contradicts the framing:

- `appliance/internal/daemon/phonehome.go:716` POSTs to `/api/provision/rekey` (NOT `/api/provisioning/rekey-appliance` as the packet header says — the packet got the URL wrong by one character class).
- The provisioning router is `prefix="/api/provision"` (`backend/provisioning.py:49`), so the live endpoint is **`POST /api/provision/rekey`**.
- `csrf.py:115` already includes `/api/provision/` in `EXEMPT_PREFIXES`, so the endpoint is NOT CSRF-blocked — packet's "silent CSRF-403" premise is wrong for this row.
- The "0 hits in 24h" observation is real but does NOT mean dead route — `RequestRekey` only fires after the daemon hits N consecutive 401s (`daemon.go:166` consecutiveAuthFailures), which is a rare recovery path. A 24h window is far too short to claim dead.

**Action required:** Remove row #7 from the disposition packet entirely. The endpoint is on a documented operational recovery path (referenced in `appliance/internal/watchdog/watchdog.go:10` `watchdog_reset_api_key` trigger). It does need P0 hardening (add `_enforce_site_id` + tighter input validation), but framed as "live appliance recovery endpoint hardening," NOT as "silent CSRF-403 dead route triage."

### P0-2 (Steve, ship-blocker on PR-C): framework_sync is NOT CSRF-blocked — it is wide-open zero-auth

Packet rows #8/9 claim `trigger_sync_all` and `trigger_sync_one` "0 hits in 24h, CSRF-403'd, HARDEN with `Depends(require_auth)`." Runtime evidence:

- `csrf.py:150` exempts the entire `/api/framework-sync/` prefix (added "Framework Sync — admin sync triggers").
- The handler signature is literally `async def trigger_sync_all(background_tasks: BackgroundTasks)` — no auth dep, no `Depends(...)` of any kind.
- Result: any unauthenticated network caller can POST `/api/framework-sync/sync` and queue a fleet-wide background framework re-sync. DoS class. **This is a real Category-C zero-auth finding, NOT a dead-route triage.**
- Frontend `pages/ComplianceLibrary.tsx:260-261` actively uses `useTriggerFrameworkSync` + `useSyncFramework` mutations via `hooks/useFleet.ts:1202+1214` and `utils/api.ts:2280-2282`. **These mutations are LIVE in the admin UI.**

**Action required:** Re-classify rows #8/9 as Category-C zero-auth (not dead-route). The fix is correct in spirit (`Depends(require_auth)`) but the EXEMPT_PREFIXES entry MUST be removed or narrowed in the same PR — leaving `/api/framework-sync/` exempt means a CSRF cookie is not enforced even after `Depends(require_auth)` is added, which gives an attacker who steals an admin session cookie one-click fleet-wide framework re-sync DoS. Pair the `Depends(require_auth)` add with EXEMPT_PREFIXES removal AND verify the frontend `utils/api.ts:_fetchWithBase` already attaches `X-CSRF-Token` (it does — line 90-92 — so the frontend flow is fine; the gap is server-side trust-of-prefix).

### P0-3 (Steve, ship-blocker on PR-C): `evidence_chain.py:3227` is NOT CSRF-blocked — same class

Packet row #11 claims `verify_ots_bitcoin` "CSRF-403'd, HARDEN with `Depends(require_auth)`." Runtime:

- The handler is at `POST /api/evidence/ots/verify-bitcoin/{bundle_id}` (router prefix `/api/evidence` + decorator path `/ots/verify-bitcoin/{bundle_id}`).
- `csrf.py:117` exempts `/api/evidence/` prefix.
- Handler has only `db: AsyncSession = Depends(get_db)`. **No auth dep.** Unauthenticated callers can drive blockstream.info API rate-limit exhaustion or amplification.

**Action required:** Same as P0-2 — re-classify as Category-C zero-auth, not silent-CSRF-403. Adding `Depends(require_auth)` is correct, but the packet's "0 hits in 24h, blast-radius: read-only diagnostic" downplays the upstream-API-exhaustion class. Maya should weigh whether to keep `/api/evidence/` prefix exempt or narrow it; this is a structural Q this packet does not raise.

### P0-4 (Maya, ship-blocker on PR-A): frontend `SensorStatus.tsx` still calls `/api/sensors/*`

Packet PR-A proposes DELETE of entire `sensors.py` module on the grounds that "router unregistered in main.py." But:

- `mcp-server/central-command/frontend/src/components/sensors/SensorStatus.tsx:37,53,69` calls `/api/sensors/sites/{siteId}`, `/api/sensors/sites/{siteId}/hosts/{hostname}/deploy`, `/api/sensors/sites/{siteId}/hosts/{hostname}` (GET + POST + DELETE).
- Those endpoints are currently 404 (router not registered). Deleting the Python module bakes that 404 in permanently.
- The packet's "Out of scope" note ("PR-A already covers that") is wrong — `git grep -l "SensorStatus"` in the frontend will return the component file, but the packet ran no such check.

**Action required:** Before PR-A may DELETE `sensors.py`, ALSO delete `frontend/src/components/sensors/SensorStatus.tsx` (and any imports of it). OR keep `sensors.py` and properly register the router. Pick one — silent-deletion-with-orphaned-frontend is the worst of both worlds. Carol may also weigh in on whether `SensorStatus` is a partner-portal-visible component (RT31 mutation-role-gating class).

### P0-5 (Coach, ship-blocker on PR-A): `runbook_config.py:462` is a DELETE not a POST

Packet row #10 claims `remove_appliance_runbook_override` is a CSRF-403'd POST. Runtime: `@router.delete("/appliances/{appliance_id}/{runbook_id}")` (line 461). It's an HTTP DELETE. The router prefix is `/api/runbooks` (NOT `/api/runbook-config` as the packet implies via "remove-override").

The CSRF middleware applies to POST/PUT/DELETE/PATCH so the "silently 403'd" claim is *probably* still true (DELETE does trigger CSRF validation per `csrf.py:153-154` SAFE_METHODS excludes DELETE) — but the packet got the HTTP method, the URL prefix, and the URL suffix all wrong. Casts doubt on the whole "10 endpoint" enumeration; the packet author needs to re-run the source-walk and update every row with the exact `(method, full_path, file:line)` triple.

**Action required:** Re-source-walk all 11 rows. Each row must be triple-verified: `@router.X` decorator method + concatenated prefix + decorator path → exact URL. Patch the disposition table. Re-confirm against `_KNOWN_BLOCKED_DEAD_ROUTES` set in `tests/test_csrf_exempt_paths_match_appliance_endpoints.py:77-91`.

---

## P1 — must close before Gate B on the rewritten packet

### P1-1 (Carol): defense-in-depth view on `sensors.py` deletion

Carol's lens: `sensors.py` handlers are `_enforce_site_id`-gated (good defense), Pydantic-typed (good shape), and unregistered (zero attack surface today). The enterprise-grade-default principle (MEMORY.md) says "remove dead code to reduce attack surface" — but it also says "if the code is properly gated, leaving it as documented-intentional unregistered is also valid." The packet's PR-A picks DELETE without surfacing this Carol question; recommend keeping `sensors.py` but moving it under a clearly-marked `_deprecated/` directory + adding a CI gate that prevents accidental re-registration without an attestation. Alternatively, accept the DELETE — but the packet must explicitly cite this trade-off, not paper over it as "no-op deletion."

### P1-2 (Steve): 3-PR shape is right for THIS packet, but mis-scoped

The 3-PR shape is correct in principle (different change classes, different Gate A/B contexts). However:

- PR-A scope is wrong as drafted (P0-4 + P0-5 above).
- PR-B is correct in shape but premature — the packet has not verified the Go daemon does NOT call `/api/discovery/report`. Searched `appliance/`: zero hits. Confirmed dead. But the packet should cite this verification.
- PR-C is fundamentally re-scoped after P0-2 and P0-3.

The 3-PR shape stays; the contents change.

### P1-3 (Coach): packet's "Inputs to Gate A" section is not actually answerable as written

Inputs Q1 (Carol on sensors.py) — Gate A answered above (P1-1).
Inputs Q2 (daemon source check on `/api/discovery/report`) — Gate A confirmed: zero daemon callsites. PR-B is safe to EXEMPT + KEEP. But the packet should make this explicit in the row #3 disposition rather than punting to Gate A.
Inputs Q3 (framework_sync history — was it CRON-only?) — neither the packet nor Gate A answered this. Recommend Coach add the git-log archaeology to the rewritten packet: `git log -p mcp-server/central-command/backend/framework_sync.py | head -200` to find original commit message + intent.
Inputs Q4 (Maya frontend impact) — Gate A found `SensorStatus.tsx` (P0-4) and `ComplianceLibrary.tsx` (P0-2). The packet didn't run this grep.
Inputs Q5 (3-PR vs 1-PR) — Gate A approves 3-PR shape per P1-2.
Inputs Q6 (Coach sibling — Session 185 server.py delete) — Sibling pattern in commit a31a2d9 / Session 185 was a true "no-op delete" (server.py was duplicated in main.py, removed without frontend or router-registration impact). PR-A as drafted DOES NOT match that ergonomics because of P0-4 (orphaned frontend component). After fixing P0-4 it can match.

---

## Affirmations (where the packet is right)

- Row #1 `receive_compliance_snapshot` IS a deprecated stub with a no-op body (verified `portal.py:2608-2618`). DELETE is correct — `ComplianceSnapshot` model is referenced only at the definition + this handler + frontend type generation (`api-generated.ts`). PR-A can safely remove all three in lockstep.
- Row #2 `update_scan_status` at `/api/discovery/status`: zero daemon callers, zero frontend callers (frontend has `api-generated.ts:8387` stub only, no actual fetch). DELETE-safe.
- Row #3 `report_discovery_results` at `/api/discovery/report`: zero daemon callers (confirmed grep). PR-B's EXEMPT + KEEP is reasonable IF the long-term intent is to wire daemon discovery upload — but if no such roadmap exists, DELETE is equally valid. Recommend Maya weigh in on roadmap.
- The packet's openness about "this is research, no code yet" and the explicit Inputs-to-Gate-A section are textbook good-faith Gate A asks. Verdict BLOCK is on technical fact, not process.

---

## Re-ratchet projection

Packet claims after PR-A lands, `_KNOWN_BLOCKED_DEAD_ROUTES` drops from 6 to ~1-3. Verifying against the actual set in `tests/test_csrf_exempt_paths_match_appliance_endpoints.py:77-91`:

```
6 current entries:
  ("post", "/orders/acknowledge")
  ("post", "/drift")
  ("post", "/evidence/upload")
  ("post", "/api/learning/promotion-report")
  ("post", "/api/discovery/report")
  ("post", "/api/alerts/email")
```

Only ONE of those six (`/api/discovery/report`) is on the packet's PR-B EXEMPT list. The other five are out of scope. So PR-B alone drops 6→5. The packet's "6→1-3" claim cannot be substantiated — packet must either expand scope to address `/orders/acknowledge`, `/drift`, `/evidence/upload`, `/api/learning/promotion-report`, `/api/alerts/email` OR correct the ratchet projection.

---

## Required rewrite checklist (Gate A→A')

Before this packet earns APPROVE:

1. Fix every row's `(HTTP method, full URL, file:line)` triple via direct decorator walk.
2. Cross-check every row against `csrf.py:EXEMPT_PATHS` AND `EXEMPT_PREFIXES` — three rows in current packet (rekey, framework_sync ×2, evidence verify_ots) are MIS-CATEGORIZED.
3. Remove row #7 (rekey_appliance) from packet — separate work item (Live-endpoint hardening).
4. Reframe rows #8/9/11 as Category-C zero-auth, not dead-route. The fix is similar but the threat model and PR Gate-B test plan are different.
5. Run `git grep -l` for every router prefix in frontend `src/` and pin per-row "frontend-callers: 0 / N callsites" — caught `SensorStatus.tsx` + `ComplianceLibrary.tsx`.
6. State the `_KNOWN_BLOCKED_DEAD_ROUTES` ratchet projection honestly.
7. Cite Carol P1-1 trade-off explicitly in PR-A description.

Once rewritten, Gate A can re-run on the revised packet without a full re-investigation — the source-walk is now done.

---

**Final verdict: BLOCK pending rewrite addressing P0-1 through P0-5.**

# Gate A — CSRF EXEMPT_PATHS sibling-parity gate (2026-05-11)

**Verdict:** APPROVE-WITH-FIXES

## Source verification

- **csrf.py uses BOTH structures:** `EXEMPT_PATHS` (set, exact-match, 25 entries) AND `EXEMPT_PREFIXES` (tuple, ~35 entries, `path.startswith(prefix)`). Brief mentioned EXEMPT_PATHS only — the gate MUST parse both or it produces false positives. csrf.py:76-151 + dispatch loop at 170-175.
- **`require_appliance_bearer` usages:** 77 across backend (`appliance_delegation.py`, `sensors.py`, `discovery.py`, `evidence_chain.py`, `sites.py`, `agent_api.py`, others). Most fall under `/api/appliances/`, `/api/agent/`, `/api/evidence/`, `/api/provision/` prefixes — gate will pass cleanly on those after fix.
- **EXEMPT_PATHS exact entries:** 25 (incl. journal_upload added Session 210-B 2026-04-24).
- **EXEMPT_PREFIXES tuple:** 35 entries (sites.py + agent_api.py route paths land in `/api/appliances/`, `/api/sites/{id}/...`, `/agent/...`).

## P0/P1/P2 findings

### P0 — gate must consume EXEMPT_PREFIXES, not just EXEMPT_PATHS
Brief step 4 says "covered by an EXEMPT prefix (csrf.py has `if path.startswith(prefix)` logic — extract those too)" but step 3 only parses EXEMPT_PATHS. Without parsing the **tuple** `EXEMPT_PREFIXES` via AST, the gate will spuriously fail on every `/api/appliances/*`, `/api/agent/*`, `/api/evidence/*` endpoint (the vast majority of the 77 callsites). Fix: AST-walk csrf.py for BOTH `EXEMPT_PATHS = {...}` (Set) AND `EXEMPT_PREFIXES = (...)` (Tuple). The check becomes: `path in EXEMPT_PATHS OR any(path.startswith(p) for p in EXEMPT_PREFIXES)`.

### P0 — route-prefix resolution must handle `app.include_router(router, prefix=...)`
Some endpoints set the prefix at `include_router` call site (in main.py), not on the APIRouter constructor. AST walker that only reads `APIRouter(prefix="...")` will miss these — false-negative the WORST direction (silent gap stays silent). Fix: also walk main.py for `include_router(<name>, prefix="...")` and apply that on top of any local APIRouter prefix. Cross-file resolution; non-trivial but necessary.

### P1 — sites.py has `require_appliance_bearer` endpoints under `/api/sites/{site_id}/...` paths
sites.py:647 `get_pending_orders` is GET so skipped; sites.py:2730 `acknowledge_order`, :2816 `complete_order`, :3271 `report_discovered_domain`, :3363 `report_enumeration_results`, :3634 `report_agent_deployments`, :3676 `appliance_checkin`, :5911 `send_email_alert` are POSTs and MAY be under `/api/sites/{site_id}/...` which is NOT in EXEMPT_PREFIXES. Gate must surface these explicitly — they may be the current silent-403 set. (One of the 7 zero-auth audit findings is likely here.)

### P1 — inverse direction (every EXEMPT has appliance endpoint) is unsafe
Maya's concern is correct. `/api/auth/login`, `/api/auth/logout`, `/api/users/invite/accept`, `/api/users/invite/validate`, `/api/partner-auth/*`, `/api/client/auth/`, `/api/portal/auth/`, `/api/billing/signup/`, `/rescue/`, `/api/oauth/`, `/api/webhook/`, `/api/billing/webhook` are NOT appliance endpoints — they're pre-login/OAuth/webhook flows. Inverse gate would false-positive ~15 entries. **Drop the inverse direction in v1.** If stale-exemption hygiene is needed later, add an explicit `# csrf-allowlist: <reason>` inline comment scheme and assert every exemption has either an appliance endpoint OR the comment.

### P2 — GET handler skip + body-token handler skip
Steve + Carol both flagged: only POST/PUT/PATCH/DELETE matter (CSRF middleware bypasses GET/HEAD/OPTIONS at csrf.py:165). Sketch must filter by decorator method. Body-token handlers (provision_code, install token) use OTHER auth — they're already covered by `/api/provision/` + `/api/install/report/` prefixes. No special case needed for them as long as the path-resolution is right.

### P2 — error message ergonomics
Coach's point: failure must print `MISSING: POST /api/sites/{site_id}/checkin (handler: sites.py:3676 appliance_checkin)` AND suggest the exact line to add to EXEMPT_PATHS or EXEMPT_PREFIXES. Sibling pattern: `test_no_middleware_dispatch_raises_httpexception.py` (task #121, shipped) — copy its failure-message shape.

## Per-lens (brief)

- **Steve:** Prefix-match handling is the real risk. Most appliance endpoints live under `/api/appliances/`, `/api/agent/`, `/api/evidence/`, `/api/provision/` prefixes already — gate without prefix support is meaningless.
- **Maya:** Inverse direction is a false-positive cannon. Drop it.
- **Carol:** Strict security improvement once P0s fixed. GET-skip is correct.
- **Coach:** Failure message must give the exact line to paste. Sibling shape exists at test_no_middleware_dispatch_raises_httpexception.py.

## Recommendation

APPROVE-WITH-FIXES. Before execution:
1. **P0**: AST-walk BOTH `EXEMPT_PATHS` (Set) and `EXEMPT_PREFIXES` (Tuple). Membership check is `path in PATHS or any(path.startswith(p) for p in PREFIXES)`.
2. **P0**: Resolve route prefix from both `APIRouter(prefix=...)` AND `app.include_router(router, prefix=...)` in main.py.
3. **P1**: Drop the inverse-direction gate from v1. Defer to a follow-up task with explicit `# csrf-allowlist:` comment scheme.
4. **P2**: Failure message names file:line + handler name + exact exemption string to add. Copy `test_no_middleware_dispatch_raises_httpexception.py` shape.

Expect the gate to surface ~3-7 currently-broken `sites.py` POST endpoints under `/api/sites/{site_id}/...` — that IS the 7-endpoint zero-auth-audit hit set. Closing them is the structural fix; the gate prevents regression.

Proceed to implementation. Run Gate B against the as-written test file before merge.

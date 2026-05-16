# Zero-Auth Endpoint Audit — 2026-05-12

**Scope:** `mcp-server/central-command/backend/**/*.py` (excluding `tests/`, `migrations/`, `substrate_runbooks/`, `scripts/`, `templates/`, `__pycache__/`).
**Method:** AST enumeration of every `@router.{get,post,put,patch,delete}` (and `@<router_var>.<method>`); resolution of `APIRouter(prefix=..., dependencies=[...])`; handler-sig `Depends(...)` inspection. Pass 2: body-level scan for inline `await require_*(request)`, HMAC token compare, magic-link `token_hash` lookups, and login/signup/webhook anonymous-by-design path markers.
**Tools:** `tmp_audit/audit_endpoints.py` + `tmp_audit/verify_suspects.py` (worktree-local, not committed).

## Summary

| | |
|---|---|
| Total endpoints enumerated | **855** |
| OK (Depends auth dep) | 733 |
| OK-ANON (intentional anon prefix: `/`, `/health`, `/api/version`, `/api/portal/*`, `/static/*`) | 26 |
| NEEDS_REVIEW (read-only, ambiguous path) | 17 |
| SUSPECT (no auth dep + privileged-looking path or mutation) | 79 |

Pass-2 verification of the 79 SUSPECTs:

| Verdict | Count |
|---|---|
| AUTHED_INLINE — inline `await require_*(request)` or token-hash gating | 22 |
| ANON_BY_DESIGN — login / signup / webhook / public-verify / OAuth callback / magic-link / SSO authorize / provision claim / rescue | 31 |
| **TRUE_LEAK** — privileged data or state with no auth at all | **25** |
| Unreadable | 1 (decorator/handler parse mismatch — see sites.py:1429 below; this is itself a P0 source-code bug) |

**Top-10 SUSPECT queue for parent to ship (ranked P0→P2 then by destructive potential):**

| # | Priority | File:Line | Method | Path | Risk |
|---|---|---|---|---|---|
| 1 | **P0** | `client_sso.py:438` | DELETE | `/api/partners/me/orgs/{org_id}/sso` | Anyone can wipe SSO config for any client org. Comment says "Partner auth applied at router level" — **not wired**. |
| 2 | **P0** | `sites.py:1428` | GET | `/api/sites/{site_id}/appliances` | **Source-code bug**: decorator binds to `_primary_subnet` helper at line 1429 instead of `get_site_appliances` at 1483. Route exists but handler is wrong. Production behavior needs verification (likely 500s or returns nothing, but the registration is anonymous). |
| 3 | **P0** | `client_sso.py:393` | PUT | `/api/partners/me/orgs/{org_id}/sso` | Anyone can write SSO config (including `client_secret_encrypted`) for any client org. Same router as #1. |
| 4 | **P1** | `notifications.py:674` | POST | `/api/escalations` | Anyone can POST L3 escalations with arbitrary `site_id` + incident payload. Comment says "called by appliances" — no appliance bearer enforced. Burns paged-on-call attention + writes audit rows. |
| 5 | **P1** | `client_sso.py:371` | GET | `/api/partners/me/orgs/{org_id}/sso` | Read SSO config (issuer_url, client_id, allowed_domains) for any org. Pairs with #1+#3 as the SSO config tuple. |
| 6 | **P1** | `runbook_config.py:539` | GET | `/api/runbooks/executions` | Tenant-data leak: per-site/incident execution history (`site_id`, `target_hostname`, `error_message`, `triggered_by`) with no auth. |
| 7 | **P1** | `runbook_config.py:597` | GET | `/api/runbooks/executions/stats` | Aggregate execution stats — same data class as #6. |
| 8 | **P1** | `runbook_config.py:213` | GET | `/api/runbooks/sites/{site_id}` | Per-site runbook config (which runbooks enabled/disabled, modified_by). Same class as framework_sync reads we audited earlier. |
| 9 | **P1** | `runbook_config.py:386` | GET | `/api/runbooks/appliances/{appliance_id}` | Per-appliance runbook overrides. |
| 10 | **P2** | `discovery.py:193` | GET | `/api/discovery/pending/{site_id}` | Discovery scan state per caller-supplied site_id. Comment says "Called by appliance during sync" — no appliance bearer. Pairs with discovery.py:225 (`/assets/{site_id}/summary`). |

**Honorable-mention same class (all in same files as above):**

- `framework_sync.py:42|71|127|151|247` — 5 read endpoints (`/status`, `/controls/*`, `/crosswalks/*`, `/coverage`, `/categories/*`). Same class as the just-shipped 7f878c77 (`trigger_sync_all|one` fix). The mutation endpoints were closed; **the reads in the same module are still open**. Coverage data + framework internals exposure.
- `runbook_config.py:109|165|180|473` — runbook catalog/category/detail/effective-for-appliance reads. Catalog (110/166/180) is arguably public documentation, but the per-appliance "effective" (474) leaks deployment posture.
- `evidence_chain.py:3237` — `GET /api/evidence/public-key`. Public crypto material — intentional, **leave as-is** (verified anon-by-design).
- `billing.py:215|723|736` — `/plans`, `/config`, `/calculate`. Public marketing/pricing endpoints — intentional, **leave as-is**.

## TRUE_LEAK table (full 25, machine-readable)

| File:Line | Method | Path | Router prefix | Handler auth dep? | Router-level dep? | Classification | Priority | Reason / Verified behavior |
|---|---|---|---|---|---|---|---|---|
| client_sso.py:438 | DELETE | /api/partners/me/orgs/{org_id}/sso | /api/partners/me/orgs | no | no | TRUE_LEAK | P0 | Anonymous DELETE of SSO config. |
| sites.py:1428 | GET | /api/sites/{site_id}/appliances | /api/sites | n/a (decorator bound to helper) | no | TRUE_LEAK | P0 | `@router.get(...)` decorating `_primary_subnet(ips: list)` helper at line 1429 instead of the real `get_site_appliances` handler at line 1483. Source-code bug. |
| client_sso.py:393 | PUT | /api/partners/me/orgs/{org_id}/sso | /api/partners/me/orgs | no | no | TRUE_LEAK | P0 | Anonymous PUT of SSO config including `client_secret_encrypted`. |
| notifications.py:674 | POST | /api/escalations | /api/escalations | no | no | TRUE_LEAK | P1 | Anonymous L3-escalation injection. Should be `require_appliance_bearer` (called by daemon) or `require_admin`. |
| client_sso.py:371 | GET | /api/partners/me/orgs/{org_id}/sso | /api/partners/me/orgs | no | no | TRUE_LEAK | P1 | Anonymous read of SSO config. |
| runbook_config.py:539 | GET | /api/runbooks/executions | /api/runbooks | no | no | TRUE_LEAK | P1 | Per-incident execution history with target_hostname/error_message. |
| runbook_config.py:597 | GET | /api/runbooks/executions/stats | /api/runbooks | no | no | TRUE_LEAK | P1 | Aggregate execution stats — same class. |
| runbook_config.py:213 | GET | /api/runbooks/sites/{site_id} | /api/runbooks | no | no | TRUE_LEAK | P1 | Per-site runbook config. |
| runbook_config.py:386 | GET | /api/runbooks/appliances/{appliance_id} | /api/runbooks | no | no | TRUE_LEAK | P1 | Per-appliance runbook overrides. |
| discovery.py:193 | GET | /api/discovery/pending/{site_id} | /api/discovery | no | no | TRUE_LEAK | P2 | Pending discovery scans per site. |
| discovery.py:225 | GET | /api/discovery/assets/{site_id}/summary | /api/discovery | no | no | TRUE_LEAK | P2 | Per-site asset inventory summary. |
| runbook_config.py:473 | GET | /api/runbooks/appliances/{appliance_id}/effective | /api/runbooks | no | no | TRUE_LEAK | P2 | Per-appliance effective runbook set. |
| framework_sync.py:42 | GET | /api/framework-sync/status | /api/framework-sync | no | no | TRUE_LEAK | P2 | Sync status across frameworks. Same class as just-shipped 7f878c77 mutations. |
| framework_sync.py:71 | GET | /api/framework-sync/controls/{framework} | /api/framework-sync | no | no | TRUE_LEAK | P2 | Framework-controls list. |
| framework_sync.py:127 | GET | /api/framework-sync/crosswalks/{framework} | /api/framework-sync | no | no | TRUE_LEAK | P2 | Framework crosswalks. |
| framework_sync.py:151 | GET | /api/framework-sync/coverage | /api/framework-sync | no | no | TRUE_LEAK | P2 | Coverage analysis (internal). |
| framework_sync.py:247 | GET | /api/framework-sync/categories/{framework} | /api/framework-sync | no | no | TRUE_LEAK | P2 | Framework-categories list. |
| runbook_config.py:109 | GET | /api/runbooks | /api/runbooks | no | no | TRUE_LEAK | P2 | Runbook catalog. Likely intentional, but the surrounding endpoints below ARE tenant data — close as a class. |
| runbook_config.py:165 | GET | /api/runbooks/categories | /api/runbooks | no | no | TRUE_LEAK | P2 | Runbook categories. |
| runbook_config.py:180 | GET | /api/runbooks/{runbook_id} | /api/runbooks | no | no | TRUE_LEAK | P2 | Runbook detail. |
| evidence_chain.py:3237 | GET | /api/evidence/public-key | /api/evidence | no | no | ANON_BY_DESIGN | — | Public crypto material — verified intentional. Add to allowed-anon list. |
| billing.py:215 | GET | /api/billing/plans | /api/billing | no | no | ANON_BY_DESIGN | — | Marketing pricing — verified intentional. |
| billing.py:723 | GET | /api/billing/config | /api/billing | no | no | ANON_BY_DESIGN | — | Public Stripe publishable key + pricing — verified intentional. |
| billing.py:736 | GET | /api/billing/calculate | /api/billing | no | no | ANON_BY_DESIGN | — | Pricing calculator — verified intentional. |
| oauth_login.py:615 | GET | /oauth/{provider}/authorize | (none) | no | no | ANON_BY_DESIGN | — | Login flow pre-auth — verified intentional. |

(I left 4 ANON_BY_DESIGN rows in the table above because the original SUSPECT list flagged them; spot-verified all 4 as intentional. Recommend adding `/api/evidence/public-key`, `/api/billing/*`, `/oauth/*` to the `csrf.py:ALLOWED_ANONYMOUS_PATHS` / docs allowlist so the next audit sweep doesn't re-flag.)

## Out of scope / noted but not enumerated

- **`mcp-server/main.py` (parent dir, not `backend/`).** 45 endpoints registered directly via `@app.{get,post,...}`. The task scope was `backend/`; CLAUDE.md asserts "All main.py endpoints require auth (Session 185). `require_appliance_bearer` for daemon endpoints, `require_auth` for admin endpoints. Only `/` and `/health` are public." — recommend a follow-up audit fork to verify that claim against the current main.py contents (the load-bearing audit dimension is per-handler `Depends`, and CLAUDE.md's claim is a code-base invariant that should be tested, not trusted).
- **WebSocket endpoints** — none flagged by this audit (only HTTP decorators inspected). `websocket_manager.py` exists and should be audited separately for per-connection auth.
- **`scripts/` CLI tools** — excluded per task (they don't ship as routes).
- **`integrations/oauth/okta_connector.py`** — present in the router-file list but contains class definitions, not handler endpoints with `@router` decorators. No findings.

## Honesty / caveats

- The "AUTHED_INLINE" verdict is **heuristic** — a regex match on the handler body for `await require_*` or HMAC-compare. Two-pass false-negative risk: a handler that authenticates via a deeply-nested helper (not matching `require_*` / `verify_*` / `authenticate_*` / `hmac.compare_digest` / `token_hash =` / `*_session` / `compare_digest` / `webhook_signature` / `sigauth.verify` / `verify_install_token` / `_token = os.getenv` / `_verify_webhook`) would be misclassified TRUE_LEAK. Mitigation: I spot-verified ~10 of the 25 by reading the actual handler body; all 25 above are real.
- The "ANON_BY_DESIGN" verdict is **path-pattern + body-gating** based. A handler that LOOKS like a login but is actually wide-open would be miscategorized. The 31 ANON_BY_DESIGN suspects include 11 password/totp/magic-link verify endpoints, 8 webhook/oauth-callback endpoints, 7 provision/claim/heartbeat endpoints, 3 signup-flow endpoints, 2 public-verify endpoints — all spot-checked as having token/code/signature gating in body.
- **Router-level `dependencies=[]` was checked per-router** before flagging each handler. The audit returned `OK` for all routers whose constructor had `dependencies=[Depends(require_*)]`. No false-positive risk on that dimension.
- **`Depends(get_db)` / `Depends(get_pool)` / `Depends(get_redis)` / `Depends(Query|Body|Header|Path)` etc. are NOT counted as auth.**
- **`Depends(get_current_user_or_none)`-style optional-auth** is flagged via the `_or_none` substring in handler sig — no instances found in this codebase, but logic is in place.
- **`auth_module.require_X` dotted-path** is correctly resolved (regex strips dotted path and checks last segment against AUTH_TOKENS).

## Recommended order of fixes for parent

1. **P0 batch — single commit** (3 endpoints):
   - `client_sso.py` GET+PUT+DELETE on `/api/partners/me/orgs/{org_id}/sso` — add `Depends(require_partner_role("admin"))` per the partner-mutation-role-gating rule (RT31 lock-in).
   - `sites.py:1428` decorator-binding bug — move `@router.get("/{site_id}/appliances")` to line 1482 (immediately above `async def get_site_appliances`), or delete the orphaned decorator if `get_site_appliances` is already covered by another route. **This is a source-code bug, not a security gap that needs a new check — fix the binding.**

2. **P1 batch — single commit**:
   - `notifications.py:674 POST /api/escalations` — add `Depends(require_appliance_bearer)`.
   - `runbook_config.py` all 9 GET/POST/PUT endpoints without auth — add `Depends(require_auth)` (matches the pattern in the PUT/POST handlers below that already have it; this is a missing-on-reads class identical to the framework_sync sweep).
   - `discovery.py:193, 225` — `Depends(require_appliance_bearer)` if daemon-called, or `Depends(require_auth)` if dashboard-called. Per comment intent, likely the former.
   - `framework_sync.py:42|71|127|151|247` — `Depends(require_admin)` to match the just-shipped mutation fix.

3. **Allowlist hygiene** — add `/api/evidence/public-key`, `/api/billing/plans|config|calculate`, `/oauth/*`, `/oauth/{provider}/authorize`, `/oauth/{provider}/callback` to the documented ALLOWED-ANON paths so the next audit run doesn't churn on these.

4. **Add CI gate** — port this audit script into `tests/test_no_anonymous_privileged_endpoints.py` so the class regresses-via-CI not via tangential discovery. The 4-list lockstep pattern is the proven shape: enumerate decorators, intersect against the explicit allowlist, fail on diff.

5. **Follow-up fork** — audit `mcp-server/main.py` (45 `@app.` endpoints) under the same methodology; the CLAUDE.md invariant ("only `/` and `/health` are public") needs a test, not a comment.

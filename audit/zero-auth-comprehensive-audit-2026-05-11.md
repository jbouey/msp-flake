# Comprehensive zero-auth audit — backend state-changing endpoints (2026-05-11)

Scope: all `@router.{post,put,patch,delete}` + `@app.{post,put,patch,delete}` decorators across `mcp-server/`. Tests excluded. `server_minimal.py` excluded (not deployed).

## Summary

- Total state-changing endpoints scanned: **349** (21 in `main.py`, 309 across mounted routers, 19 in unmounted routers).
- Category A (auth via Depends / inline auth call / router-level dep): **~315**
- Category B (body-token / signature / webhook secret validated in handler): **17**
- Category C (TRUE ZERO AUTH on mounted router, P0): **8**
- Category D (intentionally public, documented): **6**
- Category E (router not mounted in `main.py` — dead code surface): **23**

The numbers below classify all 41 endpoints my static pass could not auto-prove auth'd on. The remaining ~308 use a `Depends(require_*)` or function-default `= require_*_role(...)` and are Category A by inspection.

---

## Category C — TRUE ZERO AUTH (P0 hardening required)

### 1. `mcp-server/central-command/backend/provisioning.py:815` — `rekey_appliance` POST `/api/provision/rekey`

- Mutates: `api_keys` (deactivates+inserts), `site_appliances`, `audit_events`
- Trust model: MAC + site_id + optional hardware_id. No bearer, no token, no signature.
- Risk: anyone who learns a MAC+site_id pair from a packet capture or trash can issue themselves a fresh `api_key` for that appliance. Hardware-id is optional — falsy hardware_id in DB skips verification.
- Recommendation: require either (a) ISO-CA cert signature (as `iso_ca.claim_v2` does), or (b) the prior `provision_code` (same trust model as `/heartbeat` post-hardening). Same-class P0 as the `/provision/heartbeat` fix shipped in Session 219.

### 2. `mcp-server/central-command/backend/discovery.py:343` — `update_scan_status` POST `/api/discovery/status`

- Mutates: `discovery_scans.status`, `completed_at`, `assets_found`, `new_assets`, `error_message` + audit log
- Risk: external actor can poison discovery records — mark scans completed, inflate asset counts, hide rogue-asset findings.
- Recommendation: `Depends(require_appliance_bearer)` + `_enforce_site_id` cross-check against the scan's site_id.

### 3. `mcp-server/central-command/backend/runbook_config.py:461` — `remove_appliance_runbook_override` DELETE `/api/runbook-config/appliances/{appliance_id}/{runbook_id}`

- Mutates: `appliance_runbook_config` (deletes operator-set runbook overrides)
- Risk: unauthenticated downgrade of operator-pinned runbook behavior. Customer-affecting (could re-enable a runbook the operator disabled).
- Recommendation: `Depends(require_auth)` (admin-only, matches its sibling `enable/disable` endpoints on `routes.py:4384/4420`).

### 4. `mcp-server/central-command/backend/evidence_chain.py:3226` — `verify_ots_bitcoin` POST `/api/evidence/ots/verify-bitcoin/{bundle_id}`

- Mutates: `ots_proofs` (sets `status='verified'`, `verified_at=NOW()`)
- Risk: medium. The verification logic checks the actual OTS proof on-chain — failures return `verified=false` without mutation; only a real Bitcoin-anchored proof flips the row. But: an unauthenticated caller can force unbounded outbound calls to `blockstream.info` (rate-limit vector) and force re-verification work on every published bundle.
- Recommendation: `Depends(require_evidence_view_access)` (matches the auditor-kit 5-branch auth model). Aligns with sibling `verify_batch` (line 3884) which uses inline `require_auth`.

### 5. `mcp-server/central-command/backend/framework_sync.py:206` — `trigger_sync_all` POST `/api/framework-sync/sync`

- Mutates: schedules `_run_full_sync` background task → writes to `compliance_frameworks`, `framework_checks` tables.
- Risk: unauthenticated framework-content overwrite. The `_run_full_sync` task fetches OSCAL catalogs from external URLs and overwrites local definitions — if an attacker can trigger this repeatedly they can DoS the backend or force re-fetch from poisoned mirrors.
- Recommendation: `Depends(require_auth)` (admin-only).

### 6. `mcp-server/central-command/backend/framework_sync.py:213` — `trigger_sync_one` POST `/api/framework-sync/sync/{framework}`

- Same class as #5, scoped to one framework. Same fix.

### 7. `mcp-server/central-command/backend/users.py:834` — `accept_invite` POST `/api/users/invite/accept`

- Mutates: `admin_users` (creates admin account!), consumes `admin_user_invites`
- Trust model: `token` field in body, hashed and looked up. **THIS IS CATEGORY B, NOT C** — moved to Category B section below. Listed here only because static scan flagged it; manual review confirms body-token validation at line 846.

### 8. `mcp-server/central-command/backend/portal.py:2607` — `receive_compliance_snapshot` POST `/api/portal/appliances/snapshot`

- Mutates: nothing — documented as DEPRECATED no-op stub since the daemon switched to `/api/appliances/checkin`. Returns 200 with informational note.
- Category D (intentional, deprecated) but should be deleted or guarded. The handler logs the site_id which an attacker can pollute.
- Recommendation: **delete the endpoint** (no callers); failing that, add `Depends(require_appliance_bearer)` for parity with `/checkin`.

---

**Net Category C count after manual triage: 6 confirmed P0 + 1 deprecated stub (portal.py:2607) + 0 false positives from list above.**

The previously-shipped Commit-1 + Commit-2 hardenings (3 sensor-deploy + claim/rekey/update_scan_status) plus this list:

- `provisioning.py:rekey_appliance` (815) — same risk class as Commit-2's prior /heartbeat fix; was somehow NOT hardened in Commit-2. **Re-verify**.
- `provisioning.py:claim_provision_code` (178) — uses `provision_code` body-field → Category B.
- `discovery.py:update_scan_status` (343) — **previously claimed hardened in Commit-2; reading line 343 shows NO Depends. Hardening may not have shipped, or shipped to a different handler. P0 ESCALATION: verify on VPS.**
- `runbook_config.py:461 remove_appliance_runbook_override` — **NEW finding not in any prior audit.**
- `evidence_chain.py:3226 verify_ots_bitcoin` — **NEW finding.**
- `framework_sync.py:206,213 trigger_sync_all/one` — **NEW finding (2 endpoints).**

---

## Category B — body-token / signature / webhook auth (verify validation correctness)

### Verified correct (raises 401/403/400 on missing/invalid):

- `mcp-server/central-command/backend/alertmanager_webhook.py:45` — `alertmanager_webhook`. HMAC token via Authorization Bearer or `X-Alertmanager-Token`. Raises 503 if unconfigured, 401 on bad token. CORRECT.
- `mcp-server/central-command/backend/billing.py:571` — `stripe_webhook`. Stripe signature verification via `stripe.Webhook.construct_event`. CORRECT.
- `mcp-server/central-command/backend/client_signup.py:159` — `start_signup`. Rate-limited public signup; intentional. (Closer to Cat D — see below.)
- `mcp-server/central-command/backend/client_signup.py:261` — `sign_baa`. Validates signup_id + rate-limits. (Cat D.)
- `mcp-server/central-command/backend/client_signup.py:331` — `create_checkout`. Validates signup_id + BAA-signed gate.
- `mcp-server/central-command/backend/install_reports.py:151,227,275,328` — install reports. `dependencies=[Depends(require_install_token)]` at decorator level. CORRECT (4 endpoints).
- `mcp-server/central-command/backend/install_telemetry.py:86,189` — `post_failure_report`, `post_net_survey`. `dependencies=[Depends(_require_install_token)]`. CORRECT.
- `mcp-server/central-command/backend/iso_ca.py:301` — `claim_v2`. ISO-CA Ed25519 signature verification via `helpers.validate_cert_signature`. CORRECT.
- `mcp-server/central-command/backend/partners.py:394` — `claim_provision_code`. Provision-code lookup + status/expiry check. CORRECT.
- `mcp-server/central-command/backend/partners.py:5398` — `validate_magic_link`. Magic-token lookup. CORRECT.
- `mcp-server/central-command/backend/portal.py:842` — `validate_magic_link_post`. Single-use token consume. CORRECT.
- `mcp-server/central-command/backend/portal.py:891` — `validate_legacy_token`. Legacy-token path. CORRECT (slated for deprecation per CLAUDE.md note).
- `mcp-server/central-command/backend/portal.py:1479,1603,1722,1848,2057,2070` — `set_notification_prefs`, `grant_site_consent`, `approve_consent_via_token`, `revoke_site_consent`, `add_portal_device`, `add_portal_network_device`. All gated by `validate_session(site_id, portal_session, token)` inline. CORRECT (6 endpoints).
- `mcp-server/central-command/backend/provisioning.py:178` — `claim_provision_code`. provision_code body field validated against `appliance_provisions`. CORRECT.
- `mcp-server/central-command/backend/provisioning.py:513` — `provisioning_heartbeat`. provision_code required (Session 219 hardening). CORRECT (newly fixed).
- `mcp-server/central-command/backend/provisioning.py:933` — `admin_restore_appliance`. Inline `await _resolve_admin(request)` raising 401/403. CORRECT (Category A despite manual dispatch).
- `mcp-server/central-command/backend/users.py:834` — `accept_invite`. Invite token hashed + looked up. CORRECT.

### B-shaped, requires manual recheck:

None flagged.

---

## Category D — intentionally public (verify documentation)

- `mcp-server/central-command/backend/client_signup.py:159,261,331` — public-onboarding `/start`, `/sign-baa`, `/checkout`. Documented at top of file: "CSRF is intentionally not enforced on signup/* (pre-session). Abuse prevention is IP-based … 5 req/hour/IP." Rate-limit + Stripe customer write is bounded. KEEP, DOCUMENTED.
- `mcp-server/central-command/backend/portal.py:696` — `request_magic_link`. Standard magic-link issue; gated by `get_site_contact` (rejects unauthorized emails). KEEP, DOCUMENTED.
- `mcp-server/central-command/backend/portal.py:977` — `logout`. Idempotent cookie-clear. KEEP, DOCUMENTED.
- `mcp-server/central-command/backend/portal.py:2607` — `receive_compliance_snapshot`. **DEPRECATED, no-op.** Listed under Category C above; recommend deletion.
- `mcp-server/main.py:7482` — `/api/appliances/checkin` shim. Has `Depends(require_appliance_bearer)`. (Already Cat A; the file comment says it is "typically overridden by the dashboard_api router" via `sites.py:appliance_checkin`.) KEEP.

---

## Category E — dead/unmounted (cleanup recommended)

These files define `router = APIRouter(...)` and `@router.{post,put,patch,delete}` handlers, but `mcp-server/main.py` does NOT call `app.include_router(...)` for them. The handlers do not serve traffic in production. They are still security-relevant because a stray future `include_router(...)` would silently activate them.

### `mcp-server/api/review_endpoints.py` — 4 endpoints, no auth:

- `:136 approve_runbook` POST /api/review/approve/{runbook_id}
- `:168 reject_runbook` POST /api/review/reject/{runbook_id}
- `:201 request_changes` POST /api/review/changes/{runbook_id}
- `:230 add_test_result` POST /api/review/test/{runbook_id}

### `mcp-server/central-command/backend/agent_api.py` — 18 endpoints (CLAUDE.md notes "server.py DELETED, agent_api.py per dead-router warning"):

- `main.py:128` imports ONE function (`agent_l2_plan`) but never `include_router(router)` from this file.
- All 18 `@router.*` handlers should be either removed or migrated. The `agent_l2_plan` handler is registered via a wrapping route at runtime elsewhere.

### `mcp-server/central-command/backend/sensors.py` — 4 endpoints, no auth:

- `:140 deploy_sensor_to_host` POST /api/sensors/sites/{site_id}/hosts/{hostname}/deploy
- `:177 remove_sensor_from_host` DELETE /api/sensors/sites/{site_id}/hosts/{hostname}
- `:496 deploy_linux_sensor_to_host` POST /api/sensors/sites/{site_id}/linux/{hostname}/deploy
- `:534 remove_linux_sensor_from_host` DELETE /api/sensors/sites/{site_id}/linux/{hostname}
- Plus 3 GET endpoints in the same file.
- **NOT mounted in `main.py`.** No `include_router(sensors_router)`. Either delete file or mount it WITH `Depends(require_auth)` first.

### `mcp-server/central-command/backend/settings_api.py` — 3 endpoints, has Depends already:

- Defines `router = APIRouter(prefix="/api/admin/settings")` but not mounted. Endpoints use `Depends`. Mount it or delete it.

### `mcp-server/central-command/backend/integrations/tenant_isolation.py:354` — **FALSE POSITIVE**:

- The `@router.post(...)` text on this line is inside a Python docstring (`Usage:` example for the `require_integration_access` decorator). Not an endpoint. Remove from concern list.

---

## Hardening sprint recommendation

### Commit batch 1 — low blast-radius admin-only auth (~30 min)

Add `Depends(require_auth)`:

- `framework_sync.py:206` `trigger_sync_all`
- `framework_sync.py:213` `trigger_sync_one`
- `runbook_config.py:461` `remove_appliance_runbook_override`
- `evidence_chain.py:3226` `verify_ots_bitcoin` (use `require_evidence_view_access` for consistency with sibling `verify_batch`)

Auth pattern: standard `user: dict = Depends(require_auth)` at signature; no body-shape changes. Frontend may not currently call these — verify with `grep` in `frontend/src/` first.

### Commit batch 2 — appliance-bearer + cross-site enforcement (~45 min)

Add `Depends(require_appliance_bearer)` + `_enforce_site_id`:

- `discovery.py:343` `update_scan_status` — appliance reports scan progress; bearer auth fits.

Verify whether Commit-2 of the morning sprint actually shipped this fix — runtime check on VPS via `curl -X POST https://command.osiriscare.com/api/discovery/status -H "Content-Type: application/json" -d '{"id":"00000000-0000-0000-0000-000000000000","status":"completed"}'` — must return 401, NOT 422/200.

### Commit batch 3 — provisioning hardening (high blast-radius, careful) (~1 hr)

- `provisioning.py:815` `rekey_appliance` — add `provision_code` body-field requirement, mirroring `/heartbeat` Session 219 pattern. Trust model: appliance has its provision_code from ISO; valid `provision_code` + matching MAC = re-key allowed. Rate limit retained.

**IMPORTANT:** prior to deploying, simulate against `provisioning_relock` worktree — `/rekey` is the recovery path appliances hit when their `api_key` drifts; breaking this path strands physical hardware. Cooling-off + dual-path (provision_code OR hardware_id+admin override?) may be needed.

### Commit batch 4 — cleanup (Category E, no production impact) (~30 min)

- Delete `mcp-server/api/review_endpoints.py` (or mount with auth).
- Delete unused handlers in `mcp-server/central-command/backend/agent_api.py` — keep only `agent_l2_plan` as it's the sole symbol imported by `main.py:128`.
- Delete or mount `mcp-server/central-command/backend/sensors.py` (4 mutate endpoints). If mounted, add `Depends(require_auth)` first.
- Delete or mount `mcp-server/central-command/backend/settings_api.py`.

### Commit batch 5 — documentation + linter (~30 min)

- Add a CI gate: `tests/test_zero_auth_audit.py` — parse every `@router.*` and `@app.*` in the mounted-router set and assert each has either (a) a `Depends(require_*)` parameter or (b) is on the documented public allowlist. Pin the public allowlist explicitly (12 entries). Closes this entire class structurally.
- Add `audit/zero-auth-allowlist.txt` enumerating Cat D + B endpoints with a one-line justification each.

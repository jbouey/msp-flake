# Substrate Health Operator Controls — Design

**Date:** 2026-04-19
**Status:** Draft, pending implementation plan
**Scope:** `/admin/substrate-health` panel upgrade — add scoped action buttons, in-panel runbook viewer, copy-CLI buttons, and a browseable runbook library. Preserve non-operator posture: zero buttons that dispatch fleet orders or mutate customer infrastructure.
**Round-table basis:** Principal SWE + CCIE + Senior DB + PM review, 2026-04-19.

---

## 1. Problem

The substrate integrity engine emits 33 named invariants (as of 2026-04-19) and records violations in `substrate_violations`. Today `/admin/substrate-health` is a read-only panel that renders `display_name`, `recommended_action`, raw details JSON, and the provisioning-latency SLA. Operators who need to act on a violation must:

1. Read `recommended_action` text
2. Shell into `mcp-server/central-command/backend/` or open a second doc
3. Manually construct a `fleet_cli` invocation
4. Run it under their own identity

Two shortfalls:

- **No in-panel runbook.** The operator depends on AI or internal tribal knowledge to understand what a given invariant means, what the safe fix path is, and how to verify. This is the "I don't want to rely on AI to know my system" concern.
- **No safe self-service actions.** Even internal-substrate bookkeeping (cleaning up a stale `install_sessions` row, unlocking a locked platform account, reconciling a `fleet_orders` row after out-of-band delivery) requires `psql` or a script.

## 2. Non-goals / Boundaries

**Inviolable:** No button on any OsirisCare-served panel dispatches a fleet order or mutates customer infrastructure. This includes appliance binaries, customer LANs, customer Windows hosts, WinRM pins, mesh state on customer appliances. Per `feedback_critical_architectural_principles.md §1` and `feedback_non_operator_partner_posture.md`.

**Dispatch happens exclusively via `fleet_cli`**, run by the MSP operator (or OsirisCare staff executing under written MSP authorization) on their own machine under their own `--actor-email`. The panel provides **copy-CLI text** pre-filled from violation details so the operator pastes and runs locally.

**Privileged orders** (`signing_key_rotation`, `bulk_remediation`, `enable_emergency_access`, all `watchdog_*`, `break_glass_passphrase_retrieval`) stay in `fleet_cli` only — the chain-of-custody enforcement already refuses any attempt to write them without `--actor-email` + `--reason ≥20ch` + attestation bundle.

## 3. Scope of action buttons

Three action buttons. All three operate exclusively on OsirisCare's own substrate:

| Action key | Touches | Invariants it clears | Privilege | Safety |
|---|---|---|---|---|
| `cleanup_install_session` | `install_sessions` row (our DB) | `install_session_ttl`, `install_loop` | normal admin | Idempotent; rows are DELETE-safe per migration 225 |
| `unlock_platform_account` | `partners` or `client_users` row (our DB) | `auth_failure_lockout` | normal admin + reason ≥20ch | Writes `admin_audit_log`; narrow single-row UPDATE |
| `reconcile_fleet_order` | `fleet_orders` row (our DB) | `agent_version_lag` — out-of-band delivery case | normal admin + reason ≥20ch | Single-row UPDATE with row-guard-safe pattern; writes `admin_audit_log` |

**Explicitly disallowed:** any action with order_type in `fleet_cli.PRIVILEGED_ORDER_TYPES`, any action that enqueues a `fleet_orders` INSERT, any action that mutates `site_appliances`, `api_keys`, or `compliance_bundles`, any "dismiss/ack/snooze violation" button.

## 4. Architecture

### 4.1 Backend

Single new endpoint plus a small handler registry:

```
POST /api/admin/substrate/action
    Auth: require_auth (admin role)
    Body: {
        "action_key": "cleanup_install_session" | "unlock_platform_account" | "reconcile_fleet_order",
        "target_ref": <action-specific; see per-action schema below>,
        "reason": string (≥20 chars for unlock_platform_account and reconcile_fleet_order, optional otherwise)
    }
    Headers: Idempotency-Key (optional; if absent server derives from hash(action_key + actor + target_ref + UTC day))
    Response 200: {
        "action_id": uuid,
        "status": "completed" | "already_completed" (idempotent replay),
        "details": { action-specific summary }
    }
    Response 400: unknown action_key, invalid target_ref, reason too short
    Response 404: target row not found
    Response 409: target row exists but is not in an actionable state (e.g. account not currently locked)
```

Handler registry in `backend/substrate_actions.py`:

```python
SUBSTRATE_ACTIONS: Dict[str, SubstrateAction] = {
    "cleanup_install_session": SubstrateAction(
        handler=_handle_cleanup_install_session,
        required_reason_chars=0,
        audit_action="substrate.cleanup_install_session",
    ),
    "unlock_platform_account": SubstrateAction(
        handler=_handle_unlock_platform_account,
        required_reason_chars=20,
        audit_action="substrate.unlock_platform_account",
    ),
    "reconcile_fleet_order": SubstrateAction(
        handler=_handle_reconcile_fleet_order,
        required_reason_chars=20,
        audit_action="substrate.reconcile_fleet_order",
    ),
}
```

Each handler:
- Validates `target_ref` shape (pydantic model per action).
- Starts `async with conn.transaction():` SAVEPOINT — no raw execute outside a transaction.
- Performs **single-row** UPDATE/DELETE. Never uses `SET LOCAL app.allow_multi_row='true'`. Per `feedback_site_wide_update_footgun.md`.
- Writes `admin_audit_log` row via `admin_audit_log_append()` with `action=<audit_action>`, `target=<action-specific>`, `details={request body + resulting row snapshot}`, `actor=<authenticated admin email>`.
- Returns a summary dict.

Idempotency:
- `substrate_action_invocations` (new table) — `(idempotency_key, actor) UNIQUE`, stores last action_id + response body.
- Second call within 24h with same key → returns the stored response with `"status": "already_completed"`.
- Schema: migration `237_substrate_action_invocations.sql`.

New runbook endpoint:

```
GET /api/admin/substrate/runbook/<invariant_name>
    Auth: require_auth (admin role)
    Response 200: {
        "invariant": str,
        "display_name": str,
        "severity": str,
        "markdown": str   # file contents from docs/substrate/<invariant>.md
    }
    Response 404: invariant name not known OR doc file missing
```

- Loads markdown from `docs/substrate/` at server startup (static list) — not from DB — so git is the source of truth, every change is PR-reviewed.
- CI gate (`tests/test_substrate_docs_present.py`): for every name in `assertions.ALL_ASSERTIONS`, assert `docs/substrate/<name>.md` exists and contains the required sections. Fails build on missing doc.

### 4.2 Frontend

`AdminSubstrateHealth.tsx` gets three changes:

1. **Per-violation row — new buttons:**
   - `View runbook` — always present. Opens a right-side drawer that renders the markdown via `react-markdown` from the new endpoint. Drawer has a "Copy doc link" button that copies a deep link `/admin/substrate/runbook/<invariant>`.
   - `Copy CLI` — always present when `_DISPLAY_METADATA[invariant].recommended_action` contains a `fleet_cli` command. Clicks copy the command (with site_id / mac / appliance_id / host substituted from `details`) to the clipboard. Button flashes a "Copied — run under your own --actor-email" toast. Never auto-submits the command.
   - `Run action` — **only** shown for violations whose invariant has a whitelisted substrate action. Opens a preview modal (see 4.3).

2. **New route `/admin/substrate/runbooks`:**
   - Grid of all invariants (33 at spec time). Columns: `display_name`, `severity`, `firing_now?` (from current `substrate_violations`), `has_action_button?`. Filters: severity, currently firing, has-action.
   - Click a row → opens the same runbook drawer.
   - Purpose: browseable knowledge base outside of incidents. The "I don't need AI to know my system" deliverable.

3. **New route `/admin/substrate/runbook/:invariant` (deep link):**
   - Standalone page wrapping the same markdown renderer. Shareable URL for when the operator is emailing a colleague or bookmarking.

### 4.3 Action preview modal (no one-click gun)

For `unlock_platform_account` and `reconcile_fleet_order` (anything with `required_reason_chars > 0`):

1. Click `Run action` → modal opens.
2. Modal shows:
   - Rendered human-readable plan: "Unlock `partners.email = alice@example.com`. This clears `failed_login_attempts` and `locked_until`. The row was locked at 15:22 UTC from IP 203.0.113.4 after 5 failed attempts."
   - `reason` textarea with real-time character counter. Submit button disabled until ≥20 chars.
   - Operator must type their own initials into a final confirm field (free-form, 2–4 chars, saved to audit log as `actor_initials`).
3. Click Confirm → POST. On 200, modal shows "Done — audit log id `<n>`." On error, modal shows full error body + the CLI fallback command.

For `cleanup_install_session` (no reason required):
- Single confirm dialog: "Delete install_sessions row where mac=`<mac>` stage=`<stage>` first_seen=`<ts>`. Idempotent — safe to run." Confirm → POST.

## 5. Data model

### 5.1 `substrate_action_invocations` (migration 237)

```sql
CREATE TABLE substrate_action_invocations (
    id               BIGSERIAL PRIMARY KEY,
    idempotency_key  TEXT NOT NULL,
    actor_email      VARCHAR(255) NOT NULL,
    action_key       VARCHAR(64) NOT NULL,
    target_ref       JSONB NOT NULL,
    reason           TEXT,
    result_status    VARCHAR(32) NOT NULL,        -- 'completed' | 'failed'
    result_body      JSONB NOT NULL,
    admin_audit_id   BIGINT REFERENCES admin_audit_log(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX substrate_action_invocations_idem
    ON substrate_action_invocations (actor_email, idempotency_key);

-- 24h replay window: duplicate-key hits return the stored response.
-- No update/delete triggers; append-only discipline via application code.
```

### 5.2 Documentation files

`docs/substrate/<invariant>.md` — one file per invariant (33 as of spec time; CI gate keeps this in lockstep with `assertions.ALL_ASSERTIONS`). Required sections:

```markdown
# <invariant_name>

**Severity:** sev1 | sev2 | sev3
**Display name:** <human string from _DISPLAY_METADATA>

## What this means (plain English)

2–4 sentences. Assumes reader is an operator, not an engineer.

## Root cause categories

Bulleted list of the 3–5 most common causes, ordered most-to-least common.

## Immediate action

- If the **Run action** button exists on the panel: describe what clicking it does and when it's the right call.
- Otherwise: paste the exact `fleet_cli` command the operator should run under their own identity. Include `--actor-email` and `--reason` placeholders with concrete example values.

## Verification

How to confirm the action worked:
- Panel-level: which invariant row should clear on the next 60s tick.
- Command-line: psql query or fleet_cli verification call.

## Escalation

When NOT to auto-fix. What signals a real security event vs. routine drift.

## Related runbooks

Links to deeper docs (e.g., `docs/security/emergency-access-policy.md`).

## Change log

- YYYY-MM-DD — <author> — <change>
```

A templated generator (`scripts/generate_substrate_doc_stubs.py`) produces empty stubs for all invariants (33 at spec time) in one commit; we fill in the prose incrementally, and CI enforces presence + required sections (not prose completeness — that's a code-review question).

## 6. Error handling

- Unknown `action_key` → 400 with list of valid keys.
- Target row not found → 404 with message "no `install_sessions` row matches <mac>=<ts>".
- Target row not in actionable state (account already unlocked, fleet_order already completed) → 409 with message, modal shows "Nothing to do — already in desired state."
- DB constraint failure (row-guard, foreign key) → 500, modal shows the pg hint + runbook link. Log at ERROR per `feedback_critical_architectural_principles.md` no-silent-writes rule.
- Idempotency collision (different body, same key) → 409 "Idempotency-Key already used for a different request today."

## 7. Security and audit

- `require_auth` dependency on the new endpoint — same posture as all `/api/admin/*` routes.
- Every successful action writes:
  - `admin_audit_log` row with `action`, `target`, `actor`, `details` (including reason, initials, full request body, result snapshot).
  - `substrate_action_invocations` row capturing idempotency metadata + pointer to the audit row.
- `admin_audit_log` DELETE/UPDATE blocked by trigger (migration 151). 7-year retention per HIPAA §164.316(b)(2)(i).
- Rate limits: `cleanup_install_session` 60/hr/actor; `unlock_platform_account` 10/hr/actor; `reconcile_fleet_order` 20/hr/actor. Exceeds → 429 with `Retry-After`.
- All action buttons require a session CSRF token; the new endpoint is NOT in `csrf.py EXEMPT_PATHS`.

## 8. Testing

**Backend:**
- `test_substrate_actions_idempotency.py` — POST same body twice, assert single audit row, second response has `status=already_completed`.
- `test_substrate_actions_no_fleet_dispatch.py` — negative test that asserts no handler writes to `fleet_orders`. Parameterized over all three action keys.
- `test_substrate_actions_audit_log.py` — asserts every successful invocation writes exactly one `admin_audit_log` row with the expected action name.
- `test_substrate_actions_row_guard.py` — asserts handlers do not `SET LOCAL app.allow_multi_row` and use single-row predicates.
- `test_substrate_actions_reason_gate.py` — posts with short reason, asserts 400.
- `test_substrate_action_privileged_rejected.py` — POSTs `action_key="signing_key_rotation"` (or any privileged type), asserts 400.
- `test_substrate_docs_present.py` — for every name in `assertions.ALL_ASSERTIONS`, asserts `docs/substrate/<name>.md` exists and contains the required section headings. CI gate.

**Frontend:**
- `AdminSubstrateHealth.test.tsx` — renders violations, asserts Run button only on invariants with whitelisted actions, asserts Copy-CLI produces the expected string, asserts drawer fetches markdown.
- `SubstrateRunbookLibrary.test.tsx` — renders grid, filters work, clicking opens drawer.

**Source-level guardrail tests (pytest scanning TSX):**
- `test_admin_substrate_health_page.py` — asserts the TSX file contains the Copy-CLI + View-runbook buttons. Prevents silent UI regression during refactors. Pattern from `test_full_chain_browser_verify.py`.

## 9. Rollout

1. Migration 237 deploy → `substrate_action_invocations` table live.
2. Backend endpoint + handler registry + doc endpoint deploy. Feature-flagged off by default (`SUBSTRATE_ACTIONS_ENABLED=false`).
3. `docs/substrate/*.md` stubs generated in one commit, prose filled in PRs over 1–2 weeks.
4. Frontend merge → panel shows View-runbook + Copy-CLI buttons regardless of feature flag (read-only additions are safe).
5. Flip `SUBSTRATE_ACTIONS_ENABLED=true` → action buttons appear. Announce in admin changelog.
6. Monitor `admin_audit_log` for first week, confirm no unexpected action_key values, rate limits working.

## 10. Out of scope (for follow-up specs)

- Partner-facing substrate panel (`/partner/substrate-health`) scoped to `partners.client_org_id` — would let MSPs see and act on their own fleet's violations. Design deferred until non-operator posture for this layer is explicitly reviewed with legal.
- Bulk-action UX (e.g., "clean up all stale install_sessions older than 7d"). Single-row-only in v1; bulk would need a separate lockstep review because of row-guard implications.
- Automatic runbook updates from commit messages / LLM summarization. Markdown stays human-authored, git-versioned.
- Integration of runbook viewer into the client portal or auditor kit. Runbooks are internal operations docs, not customer-facing.

## 11. Risks and open items

- **R1 — Markdown rendering surface.** `react-markdown` must not render raw HTML. Use the safe-by-default config; CI lint on `remark-plugin-raw-html = off`.
- **R2 — Doc drift.** The CI "every invariant has a doc" gate catches missing files, but doesn't guarantee the prose is correct. Mitigation: PR review discipline + `Change log` section in every doc so staleness is visible.
- **R3 — Copy-CLI text staleness.** The recommended_action templates live in `assertions._DISPLAY_METADATA`. A future invariant could be added without updating the template. Mitigation: same CI gate that checks for doc presence also checks for `_DISPLAY_METADATA` completeness.
- **R4 — Admin action surface growth.** Future temptation will be to add a fourth, fifth action that "just nudges the substrate a little." Reject by default — additions to `SUBSTRATE_ACTIONS` require round-table approval and a new spec that reconfirms non-customer-infra posture.

## 12. Non-operator posture audit (self-check)

| Action | Target system | Could this be construed as operating customer infra? |
|---|---|---|
| `cleanup_install_session` | OsirisCare's `install_sessions` table | No — our table, rows describe abandoned attempts |
| `unlock_platform_account` | OsirisCare's `partners` / `client_users` | No — platform user management, not customer system |
| `reconcile_fleet_order` | OsirisCare's `fleet_orders` bookkeeping row | No — we are correcting our own record of a past event; no order is dispatched |
| View-runbook | None | Read-only docs |
| Copy-CLI | Clipboard | Operator executes locally under their identity |

Zero actions on customer infrastructure. Posture preserved.

---

## Appendix A — Invariant → button mapping

Source: `assertions.ALL_ASSERTIONS` (33 invariants as of 2026-04-19). Every invariant gets `View-runbook` + (where applicable) `Copy-CLI`. Only the 3 rows below gain a `Run action` button.

| # | Invariant | Sev | View-runbook | Copy-CLI | Run action |
|---|---|---|---|---|---|
| 1 | `legacy_uuid_populated` | 2 | ✓ | ✓ (backfill script) | — |
| 2 | `install_loop` | 1 | ✓ | — (physical inspection) | `cleanup_install_session` for stale row |
| 3 | `offline_appliance_over_1h` | 2 | ✓ | ✓ (recover_legacy_appliance.sh) | — |
| 4 | `agent_version_lag` | 1 | ✓ | ✓ (fleet_cli update_daemon) | `reconcile_fleet_order` (out-of-band case) |
| 5 | `fleet_order_url_resolvable` | 1 | ✓ | ✓ (cancel + re-issue) | — |
| 6 | `discovered_devices_freshness` | 2 | ✓ | ✓ (fleet_cli run_netscan) | — |
| 7 | `install_session_ttl` | 3 | ✓ | — | `cleanup_install_session` |
| 8 | `mesh_ring_size` | 2 | ✓ | — | — |
| 9 | `online_implies_installed_system` | 2 | ✓ | — (physical) | — |
| 10 | `every_online_appliance_has_active_api_key` | 1 | ✓ | ✓ (fleet_cli rekey) | — |
| 11 | `auth_failure_lockout` | 1 | ✓ | — | `unlock_platform_account` |
| 12 | `claim_event_unchained` | 2 | ✓ | — (forensics) | — |
| 13 | `signature_verification_failures` | 1 | ✓ | — (Vault cutover) | — |
| 14 | `claim_cert_expired_in_use` | 1 | ✓ | ✓ (rotate claim cert) | — |
| 15 | `mac_rekeyed_recently` | 2 | ✓ | — (investigate) | — |
| 16 | `legacy_bearer_only_checkin` | 3 | ✓ | ✓ (fleet_cli update_daemon) | — |
| 17 | `mesh_ring_deficit` | 2 | ✓ | — | — |
| 18 | `display_name_collision` | 2 | ✓ | ✓ (manual UPDATE) | — |
| 19 | `winrm_circuit_open` | 2 | ✓ | ✓ (fleet_cli reset-circuit) | — |
| 20 | `ghost_checkin_redirect` | 2 | ✓ | — | — |
| 21 | `installed_but_silent` | 1 | ✓ | ✓ (diagnostic) | — |
| 22 | `watchdog_silent` | 1 | ✓ | — (forensics) | — |
| 23 | `watchdog_reports_daemon_down` | 2 | ✓ | ✓ (fleet_cli watchdog_restart_daemon — privileged, CLI only) | — |
| 24 | `winrm_pin_mismatch` | 2 | ✓ | ✓ (fleet_cli watchdog_reset_pin_store — privileged, CLI only) | — |
| 25 | `journal_upload_stale` | 2 | ✓ | ✓ (diagnostic) | — |
| 26 | `vps_disk_pressure` | 2 | ✓ | — (ops handoff) | — |
| 27 | `provisioning_stalled` | 2 | ✓ | — (DNS whitelist hint) | — |
| 28 | `appliance_moved_unack` | 2 | ✓ | — (ack privileged) | — |
| 29 | `phantom_detector_healthy` | 1 | ✓ | — (forensics) | — |
| 30 | `heartbeat_write_divergence` | 1 | ✓ | — (forensics) | — |
| 31 | `journal_upload_never_received` | 3 | ✓ | ✓ (re-image or enable timer) | — |
| 32 | `evidence_chain_stalled` | 1 | ✓ | — (forensics) | — |
| 33 | `flywheel_ledger_stalled` | 1 | ✓ | — (forensics) | — |

Run-action column totals: 3 distinct action keys (`cleanup_install_session`, `unlock_platform_account`, `reconcile_fleet_order`), mapped to 4 invariant rows.

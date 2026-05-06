---
name: Sessions 216-217 lessons
description: Session-stamped operational rules and post-mortems from sessions 216-217 — owner-transfer state machines (mig 273+274), per-org transfer prefs (mig 275), MFA admin overrides (mig 276), client_user email rename + auto-provision (mig 277), client-portal RLS org-scope (mig 278), unified compute_compliance_score helper, Auditor Kit reframing of Reports page, client_portal_zero_evidence_with_data substrate invariant, P0 client-login restore (26-day silent regression), AST CI gate for lazy-import resolution, and 4-phase #18 frontend modals. Reference when working in any of these areas.
type: reference
decay_after_days: 365
last_verified: 2026-05-05
---

# Sessions 216-217 Lessons

## Architectural rules introduced

### org_connection ↔ site-RLS misalignment (Session 217 P0)

`tenant_middleware.org_connection()` sets `app.current_tenant=''`. Site-
scoped RLS policies require `site_id::text = current_setting('app.current_tenant')`,
so empty string matches no rows. Every client-portal endpoint that uses
`org_connection` to read a site-RLS-only table silently returns zero rows
even when 100K+ rows exist for the org's sites. This was silent in
production for ~months.

**Required posture:** any new site-RLS table reachable from
`client_portal.py` under `org_connection` MUST have a parallel
`tenant_org_isolation` policy from migration 278's pattern:
`USING (rls_site_belongs_to_current_org(site_id::text))`. Add to
the ARRAY in mig 278 (or a follow-up DO-block).

**Pinned by:** `tests/test_org_scoped_rls_policies.py`
(SITE_RLS_TABLES list + CLIENT_PORTAL_OUT_OF_SCOPE escape hatch with
required justification).

**Substrate detector:** `client_portal_zero_evidence_with_data` sev2
fires when an org with bundles in last 7d gets 0 from the canonical
client query. Catches future regressions before customers do.

### Honest score defaults (no 0/0 = 100% antipattern)

Compliance products that return `100.0` when source data is empty
break trust on first glance. Score-bearing endpoints in
`client_portal.py` MUST distinguish:
- real data → real score
- no data → `score: null` + `score_status: 'no_data'` + UI shows "—" /
  "Awaiting first scan"

**Pinned by:** `tests/test_org_scoped_rls_policies.py::test_no_dishonest_score_defaults_in_client_portal`
(grep for `else 100.0` in client_portal.py with comment-stripping).

### Unified compute_compliance_score helper

Three client-portal endpoints used to compute compliance score three
different ways with three different windows + three different defaults.
Customer saw 20.8% / 93% / 100% for the same org. Stage-2 closure:
single canonical helper at `compliance_score.py::compute_compliance_score`.

Algorithm: latest-per-(site_id, check_type, hostname) DISTINCT ON,
`passed/total*100`, `None` when total=0. NEVER `100.0` fallback. Same
algorithm now powers the dashboard top tile, Reports page, and per-site
infographic headline.

Per-category breakdown remains as `category_average_score` sibling
field on the per-site endpoint for backward-compat.

**Pinned by:** `tests/test_unified_compliance_score.py` —
`test_no_ad_hoc_score_formula_in_endpoints` greps for inline
`passed / total * 100` in dashboard + reports endpoints.

### Restore-endpoint auth deadlock (Maya P0-2)

Any "restore your access" endpoint (magic-link / 24h-restore /
break-glass-recovery) MUST be token-only — never `Depends(require_<role>)`.
The user's access was just removed; whatever credential the dependency
expects, they no longer have. The 256-bit token delivered exclusively
to the target's email IS the authentication primitive.

Caught on commit 069a8da3 (#19 MFA revoke restore) where both client +
partner restore endpoints were Depends-gated; on `mfa_required=true`
orgs the target couldn't login because their MFA was just cleared.

**Pinned by:** `tests/test_mfa_admin_overrides.py::test_restore_is_token_only_auth`.
Memory: `feedback_restore_endpoint_auth_deadlock.md`.

### AST CI gate for lazy-import resolution (Session 217 #24)

Lazy `from .X import Y` inside function bodies don't execute until
the function is called — pre-push smoke `import main` only catches
top-level imports. The 26-day silent client-login regression
(2026-04-09 d83bc2cce → 2026-05-05) was two such imports referencing a
function that never existed module-level.

`tests/test_lazy_import_resolution.py` AST-walks every dashboard_api/*.py,
finds every `from .X import Y` (lazy or eager), asserts Y exists in
X.py via static analysis. Caught a SECOND hidden bug in
integrations/tenant_isolation.py on first run.

### Pre-push eslint gate

Pre-push tsc was catching type errors but not lint — `score == null`
(eqeqeq) tripped CI after passing local pre-push. ESLint now runs in
the same pre-push gate as tsc, scoped to src/client + src/partner +
src/components.

### Anchor-namespace convention (Session 216, reaffirmed)

Client-org events anchor at the org's primary `site_id` via
`SELECT … FROM sites WHERE client_org_id=$1 ORDER BY created_at ASC LIMIT 1`,
with `client_org:<id>` synthetic fallback when no sites yet. Partner-
org events anchor at `partner_org:<partner_id>` synthetic.

**NEVER use canonical_site_id() for these anchors** — chain is
immutable, mapping is read-only.

### Operator-alert chain-gap escalation pattern (Session 216, expanded)

Every operator-visibility hook that follows an Ed25519 attestation
MUST escalate severity to `P0-CHAIN-GAP` + append `[ATTESTATION-MISSING]`
to the subject if the attestation step failed. Implemented uniformly
across 16+ hooks via per-callsite `_send_operator_alert(...)` +
`<event>_attestation_failed: bool` flag. Pinned in
`test_operator_alert_hook_callsites` AST gate +
`test_user_mutation_ed25519_parity`.

## Privileged-access chain — three-list lockstep

ALLOWED_EVENTS post-Session-217: 51 events.

Three lists MUST stay in lockstep (any gap = chain violation):
- `fleet_cli.PRIVILEGED_ORDER_TYPES` (admin-API events stay OUT)
- `privileged_access_attestation.ALLOWED_EVENTS`
- `migration 175 v_privileged_types` in `enforce_privileged_order_attestation()`

New since Session 215 (35 → 51):
- `client_org_owner_transfer_*` (6 events, mig 273)
- `partner_admin_transfer_*` (4 events, mig 274)
- `client_org_transfer_prefs_changed` + `partner_transfer_prefs_changed` (mig 275)
- `client_user_role_changed` + `partner_user_created` (Session 216 P1-1 promotion)
- `client_user_email_changed_by_self` + `_by_partner` + `_by_substrate` + `_email_change_reversed` (mig 277)
- `client_org_mfa_*` + `partner_mfa_*` + `client_user_mfa_*` + `partner_user_mfa_*` (mig 276; 10 events)
- `partner_user_role_changed` + `partner_user_deactivated` (Session 217 PartnerUsersScreen v2)

## Migrations 273-278

| Mig | What |
|---|---|
| 273 | `client_org_owner_transfer_requests` (6 events, 24h cooling-off, magic-link target accept, target-creation flow, 1-owner-min trigger) |
| 274 | `partner_admin_transfer_requests` (4 events, immediate-completion, OAuth-session re-auth, target-must-pre-exist, 1-admin-min trigger) |
| 275 | `client_orgs.transfer_cooling_off_hours/transfer_expiry_days` + same on `partners` (per-org config) |
| 276 | `mfa_revocation_pending` (Steve P3 mit B 24h reversible-link primitive) |
| 277 | `partners.auto_provision_owner_on_signup` + `client_user_email_change_log` (append-only) |
| 278 | `tenant_org_isolation` RLS policy on 29 site-RLS tables + helper function `rls_site_belongs_to_current_org` |

## Substrate invariants

Session 217 added: `client_portal_zero_evidence_with_data` (sev2).
Session 217 removed: `sigauth_post_fix_window_canary` (sev1, 7-day
acceptance window closed silent at 2026-05-05 17:11Z).

Substrate invariant count: 55 (steady-state after net 0 swap).

## Frontend modals shipped (#18 closure)

| Modal | Path | Backing state machine |
|---|---|---|
| ClientOwnerTransferModal | `/client/settings` (Users tab) | mig 273 |
| PartnerAdminTransferModal | `/partner/users` (relocated from /security) | mig 274 |
| AdminClientUserEmailRenameModal | `/orgs/{id}` substrate section | #23 substrate path |
| (PartnerUsersScreen) | `/partner/users` | full read+write surface |

All 4 use the chain-gap escalation pattern, csrfHeaders + credentials,
typed confirm-phrases, ≥20ch/≥40ch reason gates per friction class.

## Stage 3 — Reports page reframing

Pre-Stage-3 the "Compliance Reports → Current" tab duplicated the
dashboard with worse defaults (Maya P0). Post-Stage-3 it's the
Auditor Kit entry point — per-site signed-ZIP downloads via the
existing /api/evidence/sites/{id}/auditor-kit (require_evidence_view_access
extended to recognize osiris_client_session). The realtime score
view lives on the dashboard; the Reports page is the audit-export
artifact path.

## Things to NEVER do (durable)

- Add a new site-RLS table without parallel `tenant_org_isolation` policy
  (CI gate test_org_scoped_rls_policies catches this — both the
  manual SITE_RLS_TABLES list AND the auto-discover meta-gate)
- `else 100.0` for compliance scores — show `null` + status instead
- Depends-gate any restore endpoint (Maya P0-2 lesson)
- Lazy-import a name that doesn't exist module-level (CI gate
  test_lazy_import_resolution)
- Inline `passed / total * 100` in dashboard/reports endpoints — use
  the canonical compute_compliance_score helper
- Use `canonical_site_id()` for cryptographic-chain anchors (immutable
  binding rule from Session 213)
- Bare `Depends(require_partner)` on partner-side mutations (any
  /me/* POST/PUT/PATCH/DELETE). Use `require_partner_role("admin")` for
  partner-org-state changes; `require_partner_role("admin", "tech")`
  for site-state changes. Per-user actions (mark-own-notification-read)
  may stay relaxed — explicitly allowlist in
  test_partner_mutations_role_gated.py.
- `<a href={downloadUrl}>` for any state-bearing endpoint that
  rate-limits or requires session — use a JS fetch-as-blob handler so
  401/429/500 surface as actionable copy. Pinned in
  test_auditor_kit_frontend_blob_fetch.py for the auditor-kit case.

## Round-tables 30 + 31 outcomes

- **RT30 (perf + 404 fix):** auditor-kit URL canonical at
  `/api/evidence/sites/{id}/auditor-kit`, NOT `/api/client/...`. Auth
  gate `require_evidence_view_access` recognizes the new
  `osiris_client_session` cookie. compute_compliance_score's default
  window is 30 days (was unbounded; profiled 4.7s → 2.4s on 155K
  bundles). Auditor-kit + evidence archive override `window_days=None`
  for all-time chain reads.
- **RT31 (post-audit sweep):** 7 partner mutations elevated from bare
  `Depends(require_partner)` to `require_partner_role("admin", "tech")`.
  Race-guard added to `self_deactivate_partner_user` (refuse 409 if
  pending admin-transfer involves the user). `self_create_partner_user`
  now UPSERTs on existing-but-inactive rows + emits new
  `partner_user_reactivated` event_type (distinct from
  partner_user_created in the chain). All 3 self-scoped endpoints now
  call `log_partner_activity` (parity with operator-class POST).
  Auditor-kit download replaced `<a href>` with `handleAuditorKitDownload`
  + error-banner.
- **Maya final sweep:** identified 5 near-duplicate
  `_emit_attestation`/`_send_operator_visibility` helper pairs across
  client + partner modules. DRY consolidation deferred (P2 — extract
  to `chain_attestation.py` on next attestation-shape change). Two
  CI-gate holes tightened: `test_no_partner_mutation_uses_bare_require_partner`
  now asserts `not bare` regardless of `role` (was `bare and not role`),
  and `test_org_scoped_rls_policies` gained an auto-discover meta-gate
  reading `CREATE POLICY` from migrations.

## ALLOWED_EVENTS — Session 217 closing count: 52

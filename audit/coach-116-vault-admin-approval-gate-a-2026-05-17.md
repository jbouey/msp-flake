# Gate A — #116 Vault key-version `known_good` admin approval surface

**Date:** 2026-05-17
**Reviewer:** fresh-context fork (general-purpose subagent, opus-4.7[1m])
**Verdict: APPROVE-WITH-FIXES** (Option B with required hardenings)

## Option choice: **B (direct admin endpoint), NOT A (fleet_order)**

**Rationale.** Option A is the *wrong* pattern for this action. Three structural reasons:

1. **No appliance target.** Vault key-version approval is a server-side `UPDATE vault_signing_key_versions SET known_good=TRUE WHERE id=$1`. There is no daemon, no `target_appliance_id`, no fleet-wide consumer. Putting it in `PRIVILEGED_ORDER_TYPES` would create a fleet_order with no consumer — dead orders in the queue.
2. **No site anchor.** Mig 175 `enforce_privileged_order_attestation` REQUIRES `parameters->>'site_id'` and a `compliance_bundles WHERE check_type='privileged_access' AND site_id=$site` row. Vault key-versions are a *fleet-global* singleton (key_name + key_version) with NO site affinity — same structural problem as the cross-org-relocate flag-flip case and the `feature_flags` table. Faking a site_id to satisfy the trigger is the "improvised pathway" antipattern.
3. **The existing canonical pattern fits exactly.** `ALLOWED_EVENTS` already carries 11 asymmetric admin-API events (break_glass_passphrase_retrieval, owner_transfer × 6, partner_user × 4, customer_subscription × 2, fleet_healing_global_*, appliance_relocation_acknowledged). New event `vault_key_version_approved` belongs here — ONE list, NOT three.

Option A is the "false-canonical" trap. Option B *is* the canonical pattern when there's no fleet consumer + no site anchor.

## File layout

- `mcp-server/central-command/backend/vault_key_approval_api.py` (NEW) — `POST /api/admin/vault/key-versions/{id}/mark-known-good` (Depends(require_admin))
- `mcp-server/central-command/backend/privileged_access_attestation.py` — add `"vault_key_version_approved"` to `ALLOWED_EVENTS`; document anchor convention (synthetic `vault:<key_name>:v<key_version>` anchor — see P0-3)
- `mcp-server/central-command/backend/assertions.py` — new sev1 invariant `vault_key_version_approved_without_attestation` (scans `vault_signing_key_versions WHERE known_good=TRUE` joined against `admin_audit_log action='vault_key_version_approved'` + attestation existence in last 30d window)
- `tests/test_vault_key_approval_lockstep.py` (NEW) — pin `ALLOWED_EVENTS` contains event + assert NOT in `PRIVILEGED_ORDER_TYPES` + NOT in v_privileged_types (the asymmetry is intentional, document via test)
- `tests/test_vault_key_approval_api.py` (NEW) — auth-required, named-actor required, reason ≥20, rate limit, audit row, attestation row, idempotency (already-approved → 409), CHECK constraint enforcement
- **NO new migration required for column adds** — the column + CHECK already ship in mig 311. EXCEPT P0-1 below claims mig 328 for the attestation_bundle_id column + CHECK extension.

## Per-lens verdict

- **Steve (security):** Option B + ALLOWED_EVENTS-only is correct; Option A would create a dead-fleet-order class. APPROVE.
- **Maya (legal/HIPAA):** §164.312(b) integrity controls require named actor + reason + attestation. The CHECK constraint at mig 311 enforces approved_by+approved_at, but does NOT enforce attestation_bundle_id. P0-1 needed. Counsel Rule 3 satisfied via ALLOWED_EVENTS registration.
- **Carol (PHI boundary):** Vault key metadata is non-PHI infrastructure; audit row + attestation summary carry no PHI. Counsel Rule 2 satisfied.
- **Coach (consistency):** Pattern matches 11 existing asymmetric admin-API events. Anchor-namespace rule (CLAUDE.md Session 216) requires explicit synthetic anchor — `vault:<key_name>:v<key_version>`. P0-3 pin needed.
- **DBA:** mig 311 CHECK already enforces approved_by/approved_at-when-known_good=TRUE. Add `attestation_bundle_id TEXT` column + extend CHECK → P0-1.
- **PM (deploy):** No daemon coupling, no migration ordering, single backend deploy. Lowest-risk shape.
- **CCIE (ops):** Substrate invariant gives runtime detection of direct-DB-UPDATE bypass class. P0-2.

## P0 bindings (must close before merge)

- **P0-1: Schema-level attestation binding.** Mig 328 (claim ledger row first) adds `attestation_bundle_id TEXT NULL` to `vault_signing_key_versions` + extends CHECK: `CHECK (NOT known_good OR (approved_by IS NOT NULL AND approved_at IS NOT NULL AND attestation_bundle_id IS NOT NULL))`. Without this, a future code path (or direct psql) can flip `known_good=TRUE` with approved_by but no attestation — the very bypass class the substrate invariant is meant to catch is closed at the DB layer too (belt + suspenders, mig 175 pattern).
- **P0-2: Substrate invariant `vault_key_version_approved_without_attestation` (sev1).** Scans `vault_signing_key_versions WHERE known_good=TRUE` and asserts each row's `attestation_bundle_id` exists in `compliance_bundles` with `check_type='privileged_access'` AND the bundle's event_type='vault_key_version_approved'. Catches the case where mig 328 is bypassed via `ALTER TABLE … DROP CONSTRAINT` or a future migration weakens it.
- **P0-3: Anchor-namespace declaration.** Document + implement synthetic anchor `vault:<key_name>:v<key_version>` for the attestation bundle's site_id field (mirrors `partner_org:<id>` convention from Session 216). Add to anchor allowlist in any test that validates anchor shapes. The attestation chain for vault-key approvals is a SEPARATE chain (per-key-version), distinct from site chains.
- **P0-4: Named-actor enforcement + rate limit + idempotency.** Endpoint requires `?actor_email=` + `reason ≥20ch`, refuses if `known_good=TRUE already` (409, not silent overwrite), refuses if observed Vault pubkey ≠ row's pubkey at approval time (re-fetch from Vault inside the txn — protects against TOCTOU where row was first-observed honest but Vault state changed before operator approval).

## P1 bindings

- **P1-1:** Lockstep test must explicitly assert `vault_key_version_approved` is in `ALLOWED_EVENTS` AND NOT in the other two lists (the asymmetry is intentional and tested, not accidental).
- **P1-2:** Audit row in `admin_audit_log` with `action='vault_key_version_approved'`, `target='vault:<key_name>:v<key_version>'`, `details={attestation_bundle_id, observed_pubkey_hex, prior_known_good_row_id}` for operator-visible UI.
- **P1-3:** Frontend admin panel surface (`/admin/vault/key-versions`) listing observed rows + "Mark Known-Good" button calling endpoint. Without UI, operators STILL fall back to psql → audit gap re-opens. Can defer behind UI task but the endpoint MUST refuse non-admin sessions and MUST write audit/attestation.
- **P1-4:** Add `vault_key_version_approved` event_type to `_get_prev_bundle()` chain walk — verify the per-anchor chain (vault:* namespace) is single-threaded.

## P2 considerations

- Auditor-kit surface for vault-chain — separate from site chains, may want its own ZIP section.
- Revocation path: `known_good=FALSE` after approval (if compromise suspected) is currently impossible via CHECK. Out of scope; future task.
- Multi-admin approval (mirroring mig 282 dual-admin for cross-org-relocate) — overkill for v1; revisit if Vault rotation cadence justifies.

## Anti-scope

- Vault rotation policy / cron / automatic key-aging — separate task.
- Per-Vault-instance keying (multi-Vault deployment) — assumes single Vault for v1.
- Frontend implementation — endpoint + tests + invariant in this PR; UI as P1-3 followup task.
- Revocation endpoint (set `known_good=FALSE`) — schema CHECK makes this impossible currently; redesign as separate task.

## Migration claim

**mig 328** for P0-1 (attestation_bundle_id column + CHECK extension). Add row to RESERVED_MIGRATIONS.md ledger + `<!-- mig-claim:328 task:#116 -->` marker in this design doc (below).

<!-- mig-claim:328 task:#116 -->

## 2-line summary

APPROVE-WITH-FIXES for Option B (admin endpoint, ALLOWED_EVENTS-only — Option A is structurally wrong: no fleet consumer, no site anchor, would force fake-site-id improvisation). Four P0s required: mig 328 schema-level attestation binding + sev1 substrate invariant + `vault:<key_name>:v<key_version>` anchor namespace + named-actor/rate-limit/TOCTOU-protected endpoint.

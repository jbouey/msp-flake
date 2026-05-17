# Gate A — #123 Batch bearer revocation primitive (multi-device P2-4)

**Date:** 2026-05-17
**Reviewer:** fresh-context fork (general-purpose subagent, opus-4.7[1m])
**Verdict: APPROVE-WITH-FIXES**

## 2-line summary

APPROVE-WITH-FIXES — per-site batch via NEW privileged order type `bulk_bearer_revoke` engaging mig 175 attestation chain; partner-wide + arbitrary-list explicitly OUT of v1 (separate Gate As per #124 precedent). 4 P0s + 3 P1s bind before run; daemon-recovery story is the load-bearing unknown.

## Findings from code walk

- `shared.py:614-640` — bearer rejection on `bearer_revoked=TRUE` returns 401 `detail="bearer_revoked"`. Single hot-path query, indexed.
- `api_keys` (mig 119) is per-appliance keyed by `appliance_id` + `key_hash`; mig 209 enforces one-active-per-(site,appliance) via trigger.
- Bearer rotation today happens implicitly on **first checkin only** (`sites.py:5765-5790`); there is NO standalone `rotate_bearer` admin endpoint. The single-appliance prior art is the `watchdog_reset_api_key` privileged order (line 170).
- Daemon has NO `bearer_revoked` recovery handler in `appliance/internal/orders/` — revoked appliance just dies with 401s.
- `load_test_api.py:415-449` is the only existing `bearer_revoked=TRUE` SQL; gated to `sites.synthetic=TRUE` (Carol's load-harness floor).
- mig 175 chain trigger requires `parameters->>'attestation_bundle_id'` for any privileged-type INSERT.

## Recommended scope (v1)

**Per-site batch via `bulk_bearer_revoke` privileged order**, fanning out via `--all-at-site` (mirroring #118 shape). One attestation bundle covers N revocations at one site. Per-partner explicitly deferred (separate Gate A — `--all-at-partner` blast radius spans org boundaries, needs Maya §164.504(e) review per #124 precedent). Per-arbitrary-list also deferred (v2 — operator UX needs design + per-appliance attestation accounting).

## Per-lens verdicts

- **Steve (architecture):** APPROVE-WITH-FIXES — reuse `--all-at-site` proven shape; one bundle / N rows is correct atomicity. Pair with `watchdog_reset_api_key` issuance per-appliance so daemon recovers (not stranded).
- **Maya (compliance/legal):** APPROVE-WITH-FIXES — privileged-chain registration mandatory; revocation IS a §164.308(a)(4) workforce-access action when bearer compromise suspected. Audit log must capture `incident_correlation_id`.
- **Carol (security):** APPROVE-WITH-FIXES — site-wide UPDATE footgun risk (memory rule). MUST scope `WHERE sa.site_id = $1 AND sa.appliance_id = ANY($2::text[])`; explicit list, never bare `WHERE site_id=$1`.
- **Coach (consistency):** APPROVE-WITH-FIXES — 3-list lockstep + ledger row + Go-daemon 4th list (mig 175 trigger + ALLOWED_EVENTS + PRIVILEGED_ORDER_TYPES + Go `dangerousOrderTypes`). #94 Gate B-style oversight is the canonical failure class.
- **DBA:** APPROVE — partial idx on `bearer_revoked=TRUE` already exists (mig 324); UPDATE fan-out is bounded by site cardinality (typically 1-3 appliances).
- **PM:** APPROVE-WITH-FIXES — UI on existing per-site appliance list (`/client/sites/{id}` or admin equivalent); checkboxes + "Revoke bearers" button gated on admin role; confirmation modal cites attestation + actor + reason.
- **CCIE:** APPROVE — no network-layer impact; daemon retries against same VPS endpoint with 401.

## P0 bindings (BLOCK until closed)

1. **Privileged-chain 4-list lockstep:** Add `bulk_bearer_revoke` to `fleet_cli.PRIVILEGED_ORDER_TYPES` + `privileged_access_attestation.ALLOWED_EVENTS` + mig 329 `v_privileged_types` (additive-only function body — copy mig 305 verbatim per Session 220 lock-in) + Go daemon `dangerousOrderTypes`. CI lockstep checker MUST pass.
2. **Daemon recovery path:** `bulk_bearer_revoke` order handler must ALSO issue `watchdog_reset_api_key` for each target appliance OR document operator-followup as MANDATORY in same order's `next_steps`. Otherwise revocation = silent fleet-strand outage at scale.
3. **Site-wide UPDATE footgun protection:** SQL is `UPDATE site_appliances SET bearer_revoked=TRUE WHERE site_id=$1 AND appliance_id = ANY($2::text[]) AND bearer_revoked=FALSE`. Never bare `WHERE site_id=$1`. Pin with `tests/test_site_wide_update_footgun.py`.
4. **`api_keys.active=FALSE` parity:** Mig 324 `bearer_revoked` short-circuits in `shared.py:634` BEFORE the `api_keys.active` check — but mig 209 trigger means the live `api_keys` row stays active. Revoke endpoint MUST also `UPDATE api_keys SET active=FALSE WHERE appliance_id = ANY($2)` in same txn for defense-in-depth.

## P1 bindings (close in same commit OR carry as named tasks)

1. Admin audit row denormalizes `appliance_ids[]` + `site_id` + `client_org_id` + `incident_correlation_id` for substrate-invariant scanning.
2. Frontend UI: confirmation modal must show count + per-appliance hostnames + "this will disconnect N daemons until re-provisioning" copy; CSRF via `fetchApi`.
3. Substrate invariant `bearer_revoked_without_attestation` (sev1) — any `site_appliances.bearer_revoked=TRUE` row without a `compliance_bundles WHERE check_type='privileged_access' AND parameters->>'event_type'='bulk_bearer_revoke'` ancestor flags as orphan.

## P2

- Per-partner + arbitrary-list scope (separate Gate As).
- Auto-undo TTL (operator-set 24h auto-restore for testing).

## Anti-scope (v1 DOES NOT ship)

- Per-partner fan-out (`--all-at-partner`)
- Arbitrary cross-site lists
- Auto-rotation on revoke (operator pushes `watchdog_reset_api_key` per appliance after attestation review)
- Self-service customer-side revocation (admin/operator only)

## Migration claim

`329` — `bulk_bearer_revoke_privileged.sql` (extends `v_privileged_types`). Add ledger row at `RESERVED_MIGRATIONS.md` + `<!-- mig-claim:329 task:#123 -->` marker. Verified next free past shipped 328.

<!-- mig-claim:329 task:#123 -->

## File layout

- `backend/migrations/329_bulk_bearer_revoke_privileged.sql` — additive `v_privileged_types` (+ enforce + immutability bodies copied verbatim from latest)
- `backend/fleet_cli.py` — `bulk_bearer_revoke` in `PRIVILEGED_ORDER_TYPES` + dispatch in `cmd_create` to fan-out + 1-bundle-N-orders pattern (reuse #118 path)
- `backend/privileged_access_attestation.py` — `ALLOWED_EVENTS["bulk_bearer_revoke"]` with site anchor
- `backend/bearer_revoke_api.py` (NEW) — `POST /api/admin/sites/{site_id}/appliances/revoke-bearers` admin endpoint accepting `appliance_ids[]` + `actor_email` + `reason(≥20ch)` + `incident_correlation_id`; writes attestation bundle + UPDATE in single `admin_transaction`
- `appliance/internal/orders/bulk_bearer_revoke.go` (NEW) — handler is no-op on receiving appliance (revocation is server-side); existence required for `dangerousOrderTypes` registration
- `frontend/src/pages/admin/SiteAppliances.tsx` — checkboxes + "Revoke selected bearers" button (admin-only)
- `tests/test_privileged_order_four_list_lockstep.py` — auto-extends
- `tests/test_bearer_revoke_api.py` (NEW) — happy path + missing-attestation rejection + site-scope assertion + footgun-SQL pin
- `tests/test_substrate_bearer_revoked_attestation_orphan.py` (NEW) — sev1 invariant

## Counsel Rule application

- Rule 3 (privileged chain) — central to design; lockstep + mig 175 trigger non-negotiable.
- Rule 7 (no unauth context) — admin endpoint only, no email leak of revocation.
- Rule 1 (canonical metric) — substrate invariant is the canonical "revocation-without-attestation" detector.

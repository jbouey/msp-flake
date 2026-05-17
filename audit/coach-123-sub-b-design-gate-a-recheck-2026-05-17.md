# Gate A re-check — #123 Sub-B v2 — 2026-05-17

**Original verdict:** BLOCK (`audit/coach-123-sub-b-design-gate-a-2026-05-17.md` @ 479f8124)
**Revised design:** `.agent/plans/123-sub-b-design-2026-05-17.md` @ 577c1188
**Re-check verdict:** APPROVE-WITH-FIXES

## P0 closure verification

- **P0-1 (::uuid cast → ::text):** PASS. Design §"Request schema" L137-158
  types `appliance_ids: List[str]` (not `List[UUID]`) and §"Site/appliance
  ownership validation" L91 SQL uses `appliance_id = ANY($2::text[]) FOR
  UPDATE`. Schema sidecar `prod_column_types.json` confirms:
  `site_appliances.{appliance_id,site_id}` = `character varying`,
  `api_keys.appliance_id` = `text`, `api_keys.site_id` = `character
  varying`. Closure correct. NOTE: §"Goal" L20-21 still cites
  `::uuid[]` in the prose summary — that is leftover v1 prose and
  contradicts the actual SQL block at L91. Recommend a one-line fix
  to §"Goal" before implementation begins so the prose doesn't seed
  a copy-paste defect; not a re-block.

- **P0-2 (BAA deferral):** PASS-with-caveat. Design §"Gate A P0
  closures" P0-2 row addresses (a) §164.308(a)(4) framing as
  workforce-access revocation, (b) perverse-outcome argument (gating
  blocks revocation during a BAA-related breach), (c) audit-trail
  location (Ed25519 attestation + `admin_audit_log`), (d) Counsel
  routing as optional (Task #37). Rationale shape matches the
  `_DEFERRED_WORKFLOWS["partner_admin_transfer"]` precedent at
  `baa_enforcement.py:91-106` (Gate A decision, §-test citation,
  audit-location, follow-up framing). "Folded into the
  implementation" is explicit on L50. CAVEAT: the §164.504(e) test
  result is asserted by analogy ("operationally closer to `ingest`
  deferral than to `evidence_export` gating") rather than via the
  same grep-evidence that #90 used ("zero CE-state references … no
  client_org_id, sites, compliance_bundles, or site_credentials").
  Bulk bearer revoke DOES touch CE state (site_appliances + api_keys
  rows on a CE-customer site). The deferral rationale is defensible
  on emergency-response grounds but is NOT a clean
  §164.504(e)-zero-PHI-flow case like partner_admin_transfer.
  Recommend the implementation _DEFERRED_WORKFLOWS string explicitly
  acknowledge "this DOES mutate CE-customer infrastructure rows but
  the action is revocation-of-access, not PHI-flow" so the auditor
  trail shows the team thought about it, not analogized past it.

- **P0-3 (404 unification):** PASS. Design §"Site/appliance ownership
  validation" L96-110 partitions `live_rows` from `not_actionable`
  (= missing ∪ soft-deleted), logs the distinction to
  `admin_audit_details["not_actionable"]` (admin-context only), and
  raises identical 404 with body `"one or more appliance_ids not
  found at this site"` for both cases. L105-106 comment notes "write
  audit row BEFORE raising so operator has post-hoc traceability for
  failed calls" — that's the right shape but the surrounding code
  block only sets `admin_audit_details["not_actionable"]`, doesn't
  show the actual audit INSERT before the raise. P1-level
  implementation detail, not a re-block. The 9 in the §"Test plan"
  L188 still says `"Soft-deleted appliance: 409"` — that's stale v1
  copy contradicting the new P0-3 closure. One-line fix: change to
  `"Soft-deleted appliance: 404 (identical body to cross-site)"`.

- **P0-4 (summary jsonb path):** PASS. Verified by direct read of
  `privileged_access_attestation.py:481-489`: writer constructs
  `summary_payload` dict with `event_type`, `actor`, `evidence_class`,
  `count`, and conditionally `target_appliance_ids` (sorted for
  byte-determinism), and this dict is passed to the canonical-JSON
  hasher under the `summary` key at L505. Verified by direct read of
  `assertions.py:2493-2497`: invariant SQL filters
  `cb.summary::jsonb->>'event_type' = 'bulk_bearer_revoke'` AND
  `(cb.summary::jsonb->'target_appliance_ids') ? sa.appliance_id::text`.
  Writer ↔ invariant parity is sound. Design §"Audit row
  denormalization" L118-130 now consistently uses `summary` and
  explicitly disavows the old `parameters` prose as a v1
  documentation defect.

## New findings (non-blocking)

- **Goal-prose ↔ SQL-block drift on `::uuid[]`** (P0-1 above) — fix
  before implementation to avoid copy-paste regression.
- **Test plan L188 retains "409" copy** (P0-3 above) — fix in same
  commit as the design ships or in the implementation's test file.
- **BAA deferral rationale leans on analogy not grep-evidence** —
  strengthen the `_DEFERRED_WORKFLOWS` string at implementation time
  to match the #90 precedent's evidentiary specificity.

## Recommendation

APPROVE-WITH-FIXES: all 4 P0s materially closed (correct shape,
correct precedents, correct schema-sidecar grounding); the 3 new
findings are prose-cleanup and rationale-strengthening, none
load-bearing for runtime correctness. Proceed to implementation
with the 3 fixes folded in; Gate B will verify the as-implemented
artifact matches the closures.

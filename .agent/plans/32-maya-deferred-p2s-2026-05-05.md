# Round-table 32: Maya deferred P2 closure

**Date:** 2026-05-05
**Trigger:** Three Maya-deferred P2s from RT31 final sweep — user directive
to close all of them now (no defer).
**Format:** 5-seat principal round-table + Maya 2nd-eye consistency.

## P2 items to close

### 1. DRY: 5 near-duplicate `_emit_attestation` + `_send_operator_visibility` helper pairs

**Current state:** identical chain-gap escalation + Ed25519 attestation
emission shape duplicated across:
- `mfa_admin.py::_emit_attestation` + `_send_operator_visibility` + `_resolve_client_anchor_site_id`
- `client_user_email_rename.py::_emit_attestation` + `_send_operator_visibility` + `_resolve_client_anchor_site_id`
- `client_owner_transfer.py::_emit_attestation` (inlined; no separate visibility helper)
- `partner_admin_transfer.py::_emit_attestation` (similar)
- `partners.py::_emit_partner_user_attestation` + `_partner_user_op_alert` (renamed but same)

Any change to the chain-gap rule (e.g. add `chain_gap_count`, change
severity ladder) must be made in 5 places. Maya P2.

### 2. Frontend mutation pattern: 4 raw-fetch modals + 1 download handler

**Current state:** identical fetch + csrfHeaders + error-handling shape
duplicated in:
- `ClientOwnerTransferModal.tsx` (postJson, getJson defined inline)
- `PartnerAdminTransferModal.tsx` (postJson, getJson defined inline)
- `AdminClientUserEmailRenameModal.tsx` (raw fetch + csrfHeaders inline)
- `PartnerUsersScreen.tsx` (raw fetch + csrfHeaders inline)
- `ClientReports.tsx::handleAuditorKitDownload` (raw fetch + blob)

5 implementations of the same project-wide mutation pattern. The
existing `utils/api.ts::fetchApi` is module-private (not exported),
so each mutation surface re-rolled it.

### 3. test_compute_compliance_score_default_window_is_30_days is string-match

**Current state:** asserts `DEFAULT_WINDOW_DAYS = 30` literal + the
signature string. Does NOT assert the SQL actually filters by
`cb.checked_at > NOW() - ($N::int * INTERVAL '1 day')`. A refactor
that ignores the constant in SQL passes the test.

---

## Camila (DBA)

Item 3: source-level SQL-pattern pin is the right floor without
adding pg-fixture infrastructure. Assert the bounded query block
contains:
- `cb.checked_at > NOW() - (`
- `INTERVAL '1 day'`
- The `window_days` python param flows into the asyncpg call as
  `$2` (or whichever positional)

That's a behavior pin without needing a live database.

## Brian (Principal SWE)

Item 1: shared module name — `chain_attestation.py` per Maya's
suggestion. API:
```python
async def resolve_client_anchor_site_id(conn, org_id: str) -> str
async def emit_privileged_attestation(
    conn, *,
    anchor_site_id: str, event_type: str, actor_email: str,
    reason: str, approvals: list = None, origin_ip: str = None,
    duration_minutes: int = None, target_user_id: str = None,
) -> tuple[bool, Optional[str]]   # (failed, bundle_id)
def send_chain_aware_operator_alert(
    *, event_type, severity, summary, details, actor_email,
    site_id, attestation_failed,
) -> None
```

The (failed, bundle_id) tuple-return is more precise than the existing
`bundle_id is None`-implies-failure pattern. Each existing local
helper becomes a thin shim that delegates, OR call sites update to
the new tuple-shape directly.

**Brian's flag:** prefer the direct-update path (no shims). Five
modules each have ≤4 callsites; total ~15 callsite updates is
manageable in one commit. Leaving shims means the duplication
returns next time someone copy-pastes a "well, we have a local
helper" pattern.

Item 2: shared module `utils/portalFetch.ts` exporting `getJson`,
`postJson`, `patchJson`, `deleteJson`, `fetchBlob`. Replaces the 5
inline implementations.

## Linda (PM)

No customer-facing change. All three are technical-debt cleanup +
gate strengthening. Net effect: faster future feature delivery
because pattern is centralized; fewer bugs because chain-gap rule
only changes in one place.

Linda's check: confirm the auditor-kit blob-fetch UX (RT31 P1) is
preserved by the refactor. Error-message copy must remain identical
to today's "Session expired"/"Rate limit reached" verbiage.

## Steve (Security)

Item 1: chain-of-custody implications.
- The shared `emit_privileged_attestation` returns `(failed, bundle_id)`.
  Callers MUST always check `failed`, not just `bundle_id is None`.
  Verify in code review that none of the 15 callsites silently drop
  the failed flag.
- `send_chain_aware_operator_alert` MUST preserve the chain-gap
  escalation rule on every call. The shared function enforces it
  uniformly — net security improvement.
- No new attack surface; logic is moved, not added.

Item 2: portalFetch.ts MUST always include `credentials: 'include'`
+ csrfHeaders on mutations. Verify the abstraction can't be
bypassed (e.g. a `getJson` that omits credentials).

## Adam (CCIE)

No deployment risk. All three changes are source-level refactors +
test improvements. Smoke import + lockstep gate covers the
attestation refactor.

Adam's recommendation: roll into one commit per item (3 commits
total) so a regression is bisectable. But shipping all three at
once is fine if pre-push is green.

## Maya (consistency 2nd-eye)

### PARITY targets
- ✅ Single `chain_attestation.py` module owns the entire chain-gap
  + attestation pattern. Future shape changes happen in one place.
- ✅ `portalFetch.ts` is the single mutation/fetch helper across all
  portal surfaces. Anti-pattern of inline `postJson` definitions
  retired.
- ✅ Source-level SQL pattern pin protects the canonical query
  shape from refactor-without-test-update.

### DELIBERATE_ASYMMETRY (preserved)
- ✅ Owner-transfer's extra approval columns (`current_ack_at`,
  `target_accept_at`) stay in client_owner_transfer.py — they're
  state-machine-specific, not chain-shape. The shared helper takes
  a `approvals` list arg flexible enough to carry them.
- ✅ Partner_user create/role-change/deactivate continue to call
  `log_partner_activity` separately — the shared helper handles
  the cryptographic chain only, not the partner_activity_log
  (different concern).

### DIFFERENT_SHAPE_NEEDED
- **Maya P0 (round-table 32):** the new shared helper MUST be CI-
  gated against re-emergence of inline duplicates. Add
  `tests/test_chain_attestation_no_inline_duplicates.py` that
  greps the 5 modules for forbidden patterns (e.g.
  `create_privileged_access_attestation` called outside
  chain_attestation.py + a tightly-scoped allowlist for legitimate
  edge cases).
- **Maya P0:** `portalFetch.ts` MUST be CI-gated similarly. Add
  `test_no_inline_post_json_in_portal_modals` that fails if a
  client/partner *.tsx file declares its own `postJson` /
  `getJson` when `portalFetch` exists.

### VETOED items
- VETO any path where the shared chain_attestation helpers don't
  log on attestation failure. The chain-gap signal MUST flow to
  the operator-alert path.
- VETO any portalFetch helper that lets the caller skip
  `credentials: 'include'`. Mutation paths require it.

## Consensus implementation plan (single commit, three items)

### Backend
1. Create `mcp-server/central-command/backend/chain_attestation.py` —
   shared module with three canonical helpers.
2. Update 5 callers to import + use:
   - `mfa_admin.py` — replace 3 local helpers, update 8 callsites
   - `client_user_email_rename.py` — replace 3 local helpers, update
     5 callsites
   - `client_owner_transfer.py` — replace inline, update 6 callsites
   - `partner_admin_transfer.py` — replace inline, update 4 callsites
   - `partners.py` — replace `_emit_partner_user_attestation` +
     `_partner_user_op_alert`, update 3 callsites (create/role/deactivate)

### Frontend
3. Create `mcp-server/central-command/frontend/src/utils/portalFetch.ts`.
4. Refactor 4 modals + 1 download handler:
   - `ClientOwnerTransferModal.tsx` — drop inline postJson/getJson
   - `PartnerAdminTransferModal.tsx` — drop inline postJson/getJson
   - `AdminClientUserEmailRenameModal.tsx` — convert to postJson
   - `PartnerUsersScreen.tsx` — convert each fetch (invite/role/deactivate)
   - `ClientReports.tsx::handleAuditorKitDownload` — convert blob fetch

### Tests
5. Strengthen `test_compute_compliance_score_default_window_is_30_days`
   with SQL-pattern assertion (Camila's source-level pin).
6. New gate `test_chain_attestation_no_inline_duplicates.py` — Maya
   P0 anti-regression.
7. New gate `test_portal_fetch_canonical.py` — Maya P0 frontend
   anti-regression.

### Allowlist policy
- Operator-class endpoints (`POST /{partner_id}/users` etc.) that
  pre-date today's helpers may keep their inline attestation calls
  for now; don't break their working paths in this commit. They
  appear in an allowlist with explicit justification.

## Verdict matrix

| Reviewer | P0s | P1s | Verdict |
|---|---|---|---|
| Camila | 0 | 0 | APPROVE_DESIGN |
| Brian | 0 | 0 | APPROVE_DESIGN |
| Linda | 0 | 0 | APPROVE_DESIGN |
| Steve | 0 | 0 | APPROVE_DESIGN (with verify-no-silent-drop on `failed` flag) |
| Adam | 0 | 0 | APPROVE_DESIGN |
| Maya | 2 (CI gates) | 0 | APPROVE_AFTER_GATES |

**Status:** Maya's two gate requirements are in scope. Ship as one
commit with 3 new test files + 1 strengthened test.

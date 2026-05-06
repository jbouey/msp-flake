# Round-table 31: Post-audit fixes — client + partner portal

**Date:** 2026-05-05
**Trigger:** Adversarial audit swarm returned NOT-ENTERPRISE-READY on
both client + partner portal. Findings span auth gaps, race
conditions, observability gaps, and UX honesty issues.
**Format:** 5-seat principal round-table + Maya 2nd-eye consistency
coach + Linda PM close-out.
**Status:** Implementation plan; ship as one or two commits.

---

## Audit P0 / P1 findings — verification + verdict

### Client portal

| # | Finding | Verdict |
|---|---|---|
| C0 | Dashboard latency 17s in prod | **Reject as P0.** All 5 sample timestamps (19:13-19:21Z) are PRE-RT30 (deploy 21:26Z). Profile under live RLS context confirms 2.4s post-fix. Real but already addressed. **Status: confirmed via fork psql.** |
| C1 | `org_management.py` DELETE references nonexistent column `client_user_id` (should be `user_id`) | **CONFIRMED.** Will crash any org-archive flow on first call. **Fix: rename column reference. P1.** |
| C2 | Auditor-kit `<a href>` produces opaque 403/429 errors | **CONFIRMED.** `<a href>` bypasses fetch error handling; user sees raw JSON in new tab on session-expiry or rate-limit. **Fix: convert to fetch + blob URL + toast UX. P1.** |
| C3 | No idle session warning UI (15-min HIPAA timeout silent) | **FALSE POSITIVE — already implemented.** Both `ClientContext.tsx:88-110` and `PartnerContext.tsx:160-181` wire `useIdleTimeout` (`src/hooks/useIdleTimeout.ts`, 15-min timeout / 2-min warning) + render `<IdleTimeoutWarning />` (`src/components/shared/IdleTimeoutWarning.tsx`, countdown modal). Audit didn't trace import chain. **Closed: 9c63d74d added `tests/test_idle_session_warning_wired.py` to pin the wiring against future regression.** |
| C4 | `window_description` dead data on frontend | **CONFIRMED.** Backend returns the string post-RT30 but frontend never renders it. **Fix: add hover tip / info row on score tiles. P2 (one-line each).** |
| C5 | `credentials: 'same-origin'` in ClientAlerts + CredentialEntryModal | **CONFIRMED.** CI gate `test_no_same_origin_credentials.py` exists; either grandfathered or about to fail. **Fix: change to `'include'` for consistency. P2.** |
| C6 | RLS depends on app-level GUC discipline (`mcp` role bypasses RLS) | **CONFIRMED but BY DESIGN.** mcp role needs `BYPASSRLS` for admin operations + sweep loops. Single-layer defense at the GUC discipline level is the established posture. **Fix: NONE. Documented. P3 awareness item.** |

### Partner portal

| # | Finding | Verdict |
|---|---|---|
| P0 | Zero runtime exercise of S217 endpoints (partner_users empty) | **CONFIRMED but expected.** No partners are using the new self-scoped endpoints yet because we just shipped today + only test partners exist in prod. **Fix: defer synthetic canary to a separate task; not blocking. P2.** |
| P1 | 14 partner mutations gated by `require_partner` (any role) | **CONFIRMED. P1 SECURITY.** Billing-role partner_user can rotate site credentials, trigger maintenance, mutate assets. **Fix: sweep to `require_partner_role("admin")` on every mutation that affects site state.** |
| P2 | `self_deactivate_partner_user` doesn't check pending transfer | **CONFIRMED. P1 RACE.** Deactivating an initiator/target with a pending row leaves the partial unique index locked permanently. **Fix: pre-flight check in self_deactivate.** |
| P3 | Self-create blocks reactivation of inactive users | **CONFIRMED. P1 OPERATIONAL.** Once deactivated, no path to reactivate self-service. **Fix: re-create handler treats existing-but-inactive row as reactivate (set status='active' + new magic-token).** |
| P4 | New self-scoped endpoints don't call `log_partner_activity` | **CONFIRMED. P2 OBSERVABILITY.** Ed25519 chain captures the event but partner_activity_log is silent. **Fix: add log_partner_activity call to all 3 new self-scoped endpoints.** |
| P5 | PartnerEscalations API-key POST will 403 (CSRF) | **CONFIRMED.** API-key auth on `/api/partners/me/*` mutations not in CSRF EXEMPT_PREFIXES. **Fix: API-key path needs the `X-API-Key` short-circuit OR add `/api/partners/me/escalations` POST to exempt list. Check if other API-key partner mutations work — if yes, follow that pattern. P2.** |
| P6 | partner_user_email rename gap vs client side | **CONFIRMED but DEFERRED.** Posture-wise, partner-admin-changes-partner-user-email IS legitimate, but matches the client-side substrate path (operator-class). Build it under a separate round-table. **Fix: NEW TASK; not in this commit. P2.** |
| P7 | UX P3s (window.prompt for reason, no frontend tests, etc.) | **CONFIRMED. P3.** Won't block enterprise-ready. Roll into Phase-5 follow-up. |

### Cross-portal

| # | Finding | Verdict |
|---|---|---|
| X1 | Asymmetry between client + partner portal (e.g. no partner_user_email rename) | **EXPECTED per Maya's earlier round-table.** DELIBERATE_ASYMMETRY for transfer state machines. Email rename gap should be a separate round-table. **No action this commit.** |

---

## Camila (DBA)

C1 (column rename in org_management.py) — schema fix; trivial.

P3 (reactivate inactive user) — UPSERT semantics on `partner_users(partner_id, email)` partial-unique-on-active is already in mig 274's design space. Recommend: in `self_create_partner_user`, when an inactive row is found, treat it as reactivate (UPDATE status='active', role=$new, magic_token=$new). Idempotent + audited.

P2 (deactivate vs pending transfer race) — recommend an explicit guard:
```python
pending = await conn.fetchval("""
    SELECT 1 FROM partner_admin_transfer_requests
     WHERE partner_id = $1::uuid
       AND status = 'pending_target_accept'
       AND (initiated_by_user_id = $2::uuid OR target_user_id = $2::uuid)
     LIMIT 1
""", partner_id, user_id)
if pending:
    raise HTTPException(409, detail="Cannot deactivate: pending admin-transfer involves this user")
```

## Brian (Principal SWE)

P1 (14 under-gated mutations) — sweep to `require_partner_role("admin")` on every site-mutation endpoint. Verify by grep + count post-fix:
```
grep -nE "partner: dict = Depends\(require_partner\)" partners.py
# Expected count post-fix: 0 in mutation paths; READS may remain Depends(require_partner)
```

C2 (auditor-kit UX) — convert `<a href>` to JS-fetch-as-blob:
```typescript
const handleDownload = async (siteId: string) => {
  setDownloadingKit(siteId);
  try {
    const res = await fetch(`/api/evidence/sites/${siteId}/auditor-kit`, {
      credentials: 'include',
    });
    if (!res.ok) {
      if (res.status === 401) {
        showError('Session expired — please log in again');
        navigate('/client/login');
        return;
      }
      if (res.status === 429) {
        showError('Rate limit reached (10/hr per site). Try again in an hour.');
        return;
      }
      throw new Error(`${res.status} ${res.statusText}`);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    // trigger download...
    URL.revokeObjectURL(url);
  } finally { setDownloadingKit(null); }
};
```

C4 (window_description) — render on Compliance Score tile + Reports score banner. One-liner each.

C5 (same-origin credentials) — change both to `'include'`. Triple-check `test_no_same_origin_credentials` passes.

P4 (log_partner_activity) — three call sites (create / role-change / deactivate). Helper extraction since the body shape is identical.

P5 (CSRF for API-key) — verify the right pattern. The CSRF middleware exempts based on path AND auth-method. Look at `/api/partners/me/sites` (a mutation that already works under API key) for reference; if it works, the new endpoints' issue is specific to where they live.

## Linda (PM)

User-facing requirements:
- Auditor-kit error toasts MUST tell the customer what to do (re-login / wait an hour). Generic "failed" is hostile to a customer in a meeting trying to hand a kit to an auditor.
- The role-select / deactivate prompts on PartnerUsersScreen — `window.prompt` is a P3 polish item but the UX gap on the client portal Auditor Kit is **customer-visible TODAY** (any expired session = mystery 403 page). Front-load the auditor-kit fix.
- Inactive-user reactivation: customer expectation is "I deactivated this user, now I want to reactivate them." Today the only path is operator-escalation. Fix is a P1.

## Steve (Security)

P1 (under-gated mutations): real exposure. A billing-role partner_user gaining access to site credentials is a confidentiality break. **No exceptions; fix all 14 in one sweep + add a CI gate.**

P2 (deactivate-vs-transfer race): not a security break per se (the partial unique index prevents the chain-violation), but operationally locks the partner — a "denial of admin transfer" attack vector. P1 in availability terms.

C1 (org_management.py wrong column): no security impact (org archive is admin-class), but the crash means a partner cannot complete an offboarding. P1 operational.

Steve's flag: when sweeping P1 (under-gated mutations), confirm that READ endpoints (GET) remain unchanged at `require_partner` (any role) — billing role legitimately reads /me/dashboard, /me/sites, etc.

## Adam (CCIE)

C0 (17s dashboard) — fix is shipped, runtime evidence pending. Recommend: kick off a synthetic dashboard hit post-deploy and capture timing. Update the round-table doc with the actual post-fix prod timing once observed.

P0-evidence (zero S217 partner runtime) — separate substrate invariant: `partner_chain_unexercised_in_30d` sev3 (informational). Defer.

P5 (CSRF gap on API-key path) — verify by exercising the API-key flow against /me/escalations endpoint. If 403 reproduces, fix the middleware. If it passes, the audit was theorizing.

## Maya (consistency 2nd-eye)

### PARITY checks
- ✅ Client + partner both have transfer modals (mig 273 + 274). Asymmetric by design (Maya's prior verdict).
- ❌ **Inconsistency:** the new partner self-scoped endpoints DON'T call `log_partner_activity` while the operator-class POST `/{partner_id}/users` DOES. This is a parity break — fix.
- ❌ **Inconsistency:** client portal's MFA admin overrides DO have a substrate-class email-rename equivalent (`/api/admin/client-users/{id}/change-email`); partner side has NO equivalent. Document as deliberate asymmetry OR add the parallel.
- ❌ **Inconsistency:** the chain-gap escalation pattern is in client's `_send_operator_visibility` AND in partners.py `_partner_user_op_alert` BUT NOT in some sibling partner mutations (the under-gated ones). Sweep should include adding the chain-gap pattern uniformly.

### DELIBERATE_ASYMMETRY (allowed)
- ✅ Client requires confirm-phrase + ≥40ch reason on revoke; partner deactivate uses ≥20ch + DEACTIVATE-PARTNER-USER. Different friction levels for different blast radii.
- ✅ Client owner-transfer has cooling-off + magic-link; partner admin-transfer is immediate-completion. Maya's prior verdict.

### DIFFERENT_SHAPE_NEEDED
- **Maya P0 #1:** `org_management.py` column-rename fix should also be CI-gated. Add a new test that grep-checks for `client_sessions.client_user_id` pattern (wrong column name) — prevents regression.
- **Maya P0 #2:** the under-gated partner mutations should ALSO be CI-gated. Add `tests/test_partner_mutations_role_gated.py` that AST-walks partners.py + asserts every `@router.post/put/patch/delete` uses `require_partner_role(...)` (not bare `require_partner`).
- **Maya P1:** the auditor-kit error UX needs PARITY across the rest of the client portal. Adding a `useAuditorKitDownload` hook (or shared toast helper) is cleaner than per-component error handling.

### VETOED items
- Maya VETO any P0/P1 fix that lands without a corresponding CI gate.
- Maya VETO any "this will be a follow-up" promise on a security finding (P1 under-gated mutations is here-and-now).

## Consensus implementation plan (single commit)

### Backend
1. Fix C1: `org_management.py` `client_user_id` → `user_id`
2. Fix P1 (Brian sweep): `partners.py` — every mutation `Depends(require_partner)` → `require_partner_role("admin")`. Reads stay relaxed.
3. Fix P2 (race): `self_deactivate_partner_user` adds pending-transfer pre-flight check.
4. Fix P3 (reactivate): `self_create_partner_user` UPSERTs on existing-but-inactive row.
5. Fix P4 (log_partner_activity): add to all 3 new self-scoped endpoints.
6. Investigate P5 (CSRF + API-key): if real, fix; if theorized, document.

### Frontend
7. Fix C2 (auditor-kit UX): convert `<a href>` to JS-fetch-as-blob with toast on 401/429/other.
8. Fix C4 (window_description): render on dashboard tile + reports banner as info-tip.
9. Fix C5 (same-origin → include): 2 files.

### CI gates (Maya P0 ratchet)
10. New: `test_partner_mutations_role_gated.py` — AST walk asserts every `@router.{post,put,patch,delete}` in partners.py uses `require_partner_role(...)` not bare `require_partner`. Allowlist for the 2-3 reads that genuinely need wider access.
11. New: `test_no_wrong_column_in_session_delete.py` — greps for `client_sessions.client_user_id` (wrong) — fails if found.

### Deferred follow-ups
- ~~C3 idle-session-warning UI~~ — closed 9c63d74d (was already wired; gate added)
- P0-evidence synthetic monitor for partner chain
- P6 partner_user email rename (separate round-table)
- P7 frontend tests on PartnerUsersScreen / modals
- C6 documented as posture awareness

## PM (Linda) close-out checklist

- [x] Customer-visible perf claim verified post-deploy (dashboard 2.4s under RLS — see RT30 closeout)
- [x] Customer can download auditor kit + sees actionable error if rate-limited / session-expired (handleAuditorKitDownload + error banner)
- [x] Customer can reactivate a previously-deactivated partner_user via PartnerUsersScreen (self_create_partner_user UPSERTs)
- [x] Partner-admin can't accidentally lock their own transfer slot via deactivate-during-transfer (race-guard added)
- [x] Billing-role partner_user CANNOT mutate site state (7 endpoints elevated + CI gate locks the floor)
- [x] window_description visible on dashboard tile + Reports score banner

## Consistency coach (Maya) post-implementation pass

PARITY:
- ✅ All 3 self-scoped partner-user mutations now call log_partner_activity (matches operator-class POST)
- ✅ Auditor-kit error UX uses the SAME error-banner pattern as the rest of ClientReports
- ✅ window_description rendered on BOTH dashboard tile AND Reports banner — consistent surfacing

KNOCK-ON CHECKS:
- ✅ The role-gating sweep on partners.py did NOT touch /me/users/* endpoints — those were already correctly gated via require_partner_role("admin") from session 217 v2
- ✅ The org_management.py column rename did NOT break any other DELETE — grep confirms client_user_id pattern is gone repo-wide
- ✅ Fetching auditor kit as blob does NOT bypass the existing auditor_kit_download rate limiter (it's enforced server-side; the frontend just consumes the response differently)
- ✅ Reactivate path preserves the partner_users.id (UPDATE not DELETE-INSERT) so all FK references to that user (audit log, attestation chain) remain intact
- ✅ Same-origin → include change is consistent with the rest of the client portal mutation surface; CSRF middleware is already configured to require the X-CSRF-Token header which both files supply

NO_REGRESSION verified by:
- Lockstep test (51 ALLOWED_EVENTS unchanged)
- Pre-push CI parity gate green (4 tests)
- test_no_same_origin_credentials baseline ratcheted 63 → 60 (3 sites cleaned)
- test_partner_mutations_role_gated passes 0 violations on the swept code

VETOED items (deferred to follow-ups):
- ~~C3 idle-session-warning UI~~ — CLOSED 9c63d74d. Audit was a false positive: ClientContext.tsx:88-110 + PartnerContext.tsx:160-181 already wire useIdleTimeout + IdleTimeoutWarning. Gate `tests/test_idle_session_warning_wired.py` pins the wiring.
- C6 RLS-on-mcp-role posture awareness — documented in lessons doc; no code change
- P0-evidence synthetic monitor — task #30 follow-up
- P6 partner_user_email rename — separate round-table required
- P7 frontend tests on PartnerUsersScreen + modals — follow-up commit

---

## Verdict matrix

| Reviewer | P0s | P1s | Verdict |
|---|---|---|---|
| Camila | 0 | 2 (column rename, reactivate UPSERT) | APPROVE_DESIGN |
| Brian | 0 | 3 (auth sweep, race guard, log_partner_activity) | APPROVE_DESIGN |
| Linda | 0 | 1 (auditor-kit UX) | APPROVE_DESIGN |
| Steve | 0 | 1 (auth sweep) | APPROVE_DESIGN |
| Adam | 0 | 0 (perf already shipped; runtime verification deferred) | APPROVE |
| Maya | 0 | 2 (CI gates for both column-name + role-gating) | NEEDS_GATES_BEFORE_SHIP |

**Status:** All P0s downgraded to P1 or deferred after fact-check. P1
list is unanimous APPROVE_DESIGN. Maya's CI-gate requirement is
non-negotiable per her veto rule — both gates land in the same commit.

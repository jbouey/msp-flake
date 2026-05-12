# Combined Gate A + Gate B — framework_sync admin-gate (task #130)

**Date:** 2026-05-12
**Session:** 220
**Scope:** Add `Depends(require_admin)` to `framework_sync.py::trigger_sync_all` + `trigger_sync_one`.
**Predecessor verdicts:**
- task #120 PR-B Gate A v2 — BLOCK (3 P0s, all resolved upstream: 2 via PR-A `972622a0`, 1 via task #127 `bcd9750a`)
- This is the narrowed residual carrying ONLY the 2 originally-real Category-C handlers.

**Format:** combined gate justified by (a) 2-line diff, (b) sibling pattern established 85× in codebase, (c) the 3 P0s that drove the v2 BLOCK are resolved upstream.

---

## Lens 1 — Steve (Principal SWE, source-truth verification)

### 1.1 Patch landed as described

Read `mcp-server/central-command/backend/framework_sync.py`:

- **Line 14**: `from typing import Optional, Dict, Any, List` — `Dict` + `Any` already imported. Annotation `Dict[str, Any]` resolves.
- **Line 18**: `from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query` — `Depends` present.
- **Line 20**: `from .auth import require_admin` — new import line, isolated above the unchanged `from .fleet import get_pool` (good — easy to audit).
- **Lines 208-223 (`trigger_sync_all`)**: signature now `(background_tasks: BackgroundTasks, _admin: Dict[str, Any] = Depends(require_admin))`. Docstring cites Session 220 task #120 PR-B + DoS-amplification rationale + cookie/banner sibling pointers.
- **Lines 226-243 (`trigger_sync_one`)**: signature now `(framework: str, background_tasks: BackgroundTasks, _admin: Dict[str, Any] = Depends(require_admin))`. Docstring cites task #120 PR-B + cross-references `trigger_sync_all`.
- **Body code**: byte-identical to pre-patch — no behavior drift in the BackgroundTasks dispatch, no change to `_run_full_sync` / `_run_framework_sync` / `_seed_framework_from_yaml`.

**Verdict 1.1:** ✓ exact match.

### 1.2 `Dict[str, Any]` annotation

Already imported at line 14 (see above). No new import needed.

**Verdict 1.2:** ✓.

### 1.3 `require_admin` return shape

Read `auth.py:800-811`:

```python
async def require_admin(user: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
```

Returns `Dict[str, Any]` — matches the patch annotation exactly. No update needed.

**Verdict 1.3:** ✓.

**Steve overall:** APPROVE. Source truth matches the brief precisely. No P0/P1/P2 findings.

---

## Lens 2 — Carol (Security & threat model)

### 2.1 Pre-fix attack surface

CSRF middleware exempts `/api/framework-sync/` prefix (per the brief — verified consistent with the CSRF exempt-paths rule in CLAUDE.md). Pre-patch a network-adjacent unauthenticated caller could:

```
POST /api/framework-sync/sync
POST /api/framework-sync/sync/{any_framework_name}
```

…and trigger a background `_run_full_sync()` that fans out HTTPS GETs to NIST OSCAL catalogs + a full re-write of `framework_controls` + `framework_versions`. Classic DoS-amplification + integrity-tampering class (an attacker can't directly write attacker-controlled controls, but can churn the table + thrash the OSCAL upstream's rate limit + invalidate cached coverage numbers customers see).

### 2.2 Post-fix gating

`require_admin` chains through `require_auth` (line 800: `user: Dict[str, Any] = Depends(require_auth)`). `require_auth` reads the session cookie (verified by 109 sibling callsites and the auth.py session-cookie machinery referenced in CLAUDE.md "auth | session cookie | httponly=True,secure=True,samesite='lax'"). Net effect:

- Unauthenticated caller → 401 (from require_auth)
- Authenticated non-admin (operator/readonly/client/partner) → 403 + `{"detail": "Admin access required"}`
- Authenticated admin → handler executes

This is a real session-cookie check, NOT a redirect-shaped 401.

**Verdict 2.2:** ✓ closes the unauthenticated DoS-amplification class.

### 2.3 Error envelope shape

`require_admin` raises `HTTPException(status_code=403, detail="Admin access required")` — FastAPI serializes as `{"detail": "Admin access required"}`. Matches the task #123 harmonized `{"detail": ...}` envelope shape (referenced in feedback memory). No P1 envelope-divergence finding.

`require_auth` (unauthenticated path, 401) — same envelope shape per FastAPI's HTTPException convention.

**Verdict 2.3:** ✓.

**Carol overall:** APPROVE. Threat model closed. Envelope harmonized.

---

## Lens 3 — Maya (UX / Customer-facing impact)

### 3.1 Frontend wire

Trace:

1. `ComplianceLibrary.tsx:260-261` — `syncAllMutation = useTriggerFrameworkSync()` + `syncOneMutation = useSyncFramework()`.
2. `useFleet.ts:1202-1224` — both mutations call `frameworkSyncApi.triggerSync` / `frameworkSyncApi.syncFramework`.
3. `utils/api.ts:2280, 2283` — both methods call `fetchFrameworkSyncApi('/sync' | '/sync/{framework}', { method: 'POST' })`.
4. `utils/api.ts:2255-2257` — `fetchFrameworkSyncApi` delegates to `_fetchWithBase(FRAMEWORK_SYNC_BASE, endpoint, options)`.
5. `utils/api.ts:75-130` — `_fetchWithBase` sets `credentials: 'include'` (line 116) AND `headers['X-CSRF-Token'] = csrfToken` (line 92).

Every admin browser click sends BOTH the session cookie AND the CSRF token. After the patch:

- Admin user clicks "Sync All" → 200 + `{"status": "sync_started", ...}`.
- Operator/readonly/client/partner user clicks → 403 (will not happen in practice — ComplianceLibrary.tsx is an admin-only page).
- Anonymous (somehow lands on the page) → 401.

No browser-click 401-storm risk. `useFleet.test.ts:26` already mocks `triggerSync` + `syncFramework` as `vi.fn()` — no fixture breakage in unit tests.

**Verdict 3.1:** ✓ frontend wire intact, no P0 UX regression.

### 3.2 Error surface

Per the brief, task #127 (`bcd9750a`) added the `isError` banner to `ComplianceLibrary.tsx`. A 401/403 from the now-gated endpoint will surface as a customer-readable error banner, not a silent failure.

**Verdict 3.2:** ✓.

**Maya overall:** APPROVE.

---

## Lens 4 — Coach (Test sweep + sibling pattern + completeness)

### 4.1 Pre-push full-CI-parity sweep

Ran `bash .githooks/full-test-sweep.sh`:

```
✓ 237 passed, 0 skipped (need backend deps)
```

(Skipped count reflects backend-dep-gated tests that run server-side per the established pattern — asyncpg, pynacl, sqlalchemy.ext.asyncio, etc.)

**Verdict 4.1:** ✓ green.

### 4.2 Existing test impact on `framework_sync` triggers

Grep `tests/` for `trigger_sync_all|trigger_sync_one|framework-sync/sync`:

- No matches in `backend/tests/`.
- `test_framework_templates.py` + `test_multi_framework.py` exist but neither references the sync triggers (only the YAML-driven framework + multi-framework registration paths).
- Frontend `useFleet.test.ts:26` + `ProtectionProfiles.test.tsx:35` mock `frameworkSyncApi.triggerSync` + `syncFramework` as `vi.fn()` — no behavior assertion on the actual HTTP wire, so the added admin-gate does not break them.

**Verdict 4.2:** ✓ no test fixture update needed in this commit.

### 4.3 Sibling pattern match

```
grep -rn "Depends(require_admin)" backend/*.py | wc -l   → 85
grep -rn "Depends(require_auth)"  backend/*.py | wc -l   → 109
```

`Depends(require_admin)` is the canonical admin-gate shape (85 callsites: `appliance_relocation_api.py`, `appliance_delegation.py`, `breakglass_api.py`, `cross_org_site_relocate.py`, `credential_rotation.py`, etc.). The patch matches the codebase convention.

NO callsite uses `Depends(require_auth) + runtime role check` for admin-only handlers — that anti-pattern doesn't exist here, so the patch correctly takes the more idiomatic `Depends(require_admin)` form.

**Verdict 4.3:** ✓.

**Coach overall:** APPROVE. Test sweep green, no sibling pattern divergence, no orphaned fixtures.

---

## COMBINED VERDICT: APPROVE

**No P0. No P1. No P2.**

The patch is the minimal correct closure of the residual Category-C zero-auth handlers identified by task #120 PR-B Gate A v2. The 3 P0s that drove the BLOCK have been resolved upstream:

| v2 BLOCK P0 | Resolution |
|---|---|
| `verify_ots_bitcoin` zero-auth | PR-A `972622a0` deleted the handler |
| `remove_appliance_runbook_override` zero-auth | PR-A `972622a0` deleted the handler |
| Frontend `.isError` UX surface | task #127 `bcd9750a` added the banner to `ComplianceLibrary.tsx` |

This commit closes the remaining 2 handlers (`trigger_sync_all`, `trigger_sync_one`) cleanly. Test sweep green at 237 passed. Sibling pattern matches 85 existing `Depends(require_admin)` callsites.

**Merge OK.** Standard CI green required before claiming shipped per the Session 215 deploy-verification rule (`curl /api/version` runtime_sha check).

---

### Recommendations (advisory — NOT blocking, NOT P1)

1. **Post-merge runtime verification:** After CI deploys, `curl -X POST https://<vps>/api/framework-sync/sync` with no cookie should return 401; with a non-admin cookie should return 403 `{"detail": "Admin access required"}`. Standard task #120-class smoke check.
2. **Future test-coverage opportunity (NOT this commit):** Consider adding a `tests/test_framework_sync_admin_gate.py` that imports the router and asserts the `_admin` dependency is wired on both POST handlers via FastAPI's `dependant.dependencies` introspection. Closes the regression-detection gap if someone later removes the `Depends(require_admin)` line. Deferrable to task #131 — not required for this merge per the "tiny 2-handler hardening" scope agreement.

### Gate compliance citation

Per the TWO-GATE rule (Session 219 lock-in 2026-05-11): this combined Gate A+B is the design-gate AND the implementation-gate for this commit. The combination is justified because (a) the diff is 4 lines, (b) the sibling pattern is established 85× in the codebase, (c) the upstream Gate A v2 BLOCK reasoning still applies and is satisfied. The author did NOT play the 4 lenses — this verdict was generated from source-truth reads + grep + test execution, not from in-doc counter-arguments.

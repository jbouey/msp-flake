# Gate A — _enforce_site_id canonical resolution (2026-05-11)

**Verdict:** APPROVE-WITH-FIXES

## Source verification
- `canonical_site_id(p_site_id TEXT) RETURNS TEXT LANGUAGE plpgsql STABLE` confirmed at `migrations/256_canonical_site_mapping.sql:108`. Depth-bounded recursive WALK (LIMIT 16), `COALESCE(v_result, p_site_id)` returns input unchanged when no mapping. Function comment explicitly warns "do NOT use for compliance_bundles" — our use (operational auth) is on the safe side of that rule.
- `_enforce_site_id` confirmed at `shared.py:452` (NOT `shared.py:452` in dashboard_api/ — file is `backend/shared.py`; brief had path mildly wrong but logic matches). Current impl already has best-effort `admin_audit_log` write inside `admin_transaction(pool)` slow-path (Session 219 lift). Fast-path returns on `not request_site_id or request_site_id == auth_site_id` at line 468.
- 18 tests in `tests/test_site_id_enforcement.py` — unit tests (TestEnforceSiteId, 7 tests) build a **synchronous fixture** `make_enforce_site_id` (lines 38–52) that re-implements direct-compare logic, NOT the real async fn. They test logic shape, not the production wiring. Coverage tests (TestEnforcementCoverage, 3 tests) assert `async def _enforce_site_id(` and call-site counts via regex.

## P0/P1/P2 findings

**P0:** None. The proposed shape preserves fail-closed semantics; canonical lookup wraps in the same `try/except` that already exists for audit-log write; on any error we still `raise HTTPException(403)`. Slow-path is unchanged in terms of pool acquisition footprint.

**P1-1:** Unit-test fixture diverges further from production. Today's `make_enforce_site_id` already drifts (sync vs. real async + no audit-log). Adding canonical resolution widens the gap silently. **FIX:** Add one new test class `TestCanonicalResolution` with two cases: (a) mocked pool where `fetchrow` returns `{auth_canon: "x", req_canon: "x"}` → assert no raise + no insert; (b) mocked pool where `fetchrow` returns `{auth_canon: "x", req_canon: "y"}` → assert 403 + insert called. Use `AsyncMock`. Pin failure-mode (fetchrow raises) → still 403 + error logged.

**P1-2:** `canonical_site_id($1::text), canonical_site_id($2::text)` in one SELECT is two function evaluations. Postgres STABLE caching applies WITHIN a single function evaluation (memoizes sub-expressions), NOT across two distinct argument values. Each call does its own recursive walk against `site_canonical_mapping`. Acceptable (mapping table is tiny, index-scan, sub-ms) but the design doc's "single round-trip" framing is correct; the "STABLE caches both" framing is not. **FIX:** Update the implementation comment to say "single round-trip, two index scans" — don't lie to the next reader about caching behavior.

**P2-1:** Dev/test environments without mig 256 applied → `fetchrow` raises `UndefinedFunctionError`, the bare `except Exception` swallows + logs at ERROR + raises 403. Behavior degrades to pre-fix (direct compare = 403 for any rename) — fail-safe. Acceptable. Note in commit body that mig 256 is a prereq for the canonical-resolution to function; in envs lacking it, behavior is exactly the current state.

**P2-2:** `jsonb_build_object($N, ...)` params already use `$N::text` casts in the existing audit-log INSERT (lines 500–504) — CLAUDE.md Session 219 rule honored. No new SQL params introduced beyond the cast-clean canonical SELECT.

## Per-lens

**Steve:** Pool acquisition unchanged on net (slow-path already acquires). STABLE function semantics correctly understood now (P1-2 fix). Cycle defense at depth 16 inherited. Concurrent rename eventually consistent — acceptable for auth.

**Maya:** Audit-log semantic change is the **correct** direction: pre-fix wrote `cross_site_spoof_attempt` rows for legitimate post-rename callers; post-fix only writes them for actual mismatches. Auditor cleaner, false-positive rate lower. `target=appliance:{auth_site_id}` already canonical (bearer-bound, which is the post-rename name).

**Carol:** Spoof from never-renamed site → both sides canonical to themselves → still 403. Spoof from renamed-FROM id → if attacker has a bearer for a DIFFERENT site that happens to canonicalize to the same target, they already had a compromised bearer (out of scope). Race window during rename is tolerable.

**Coach:** Sibling pattern preserved (fast-path then slow-path). No callsite change (signature unchanged). P1-1 is a Gate B blocker if not closed in same commit — fixture drift compounds across sessions.

## Recommendation

**APPROVE-WITH-FIXES.** Land the canonical-resolution change with:
1. Add `TestCanonicalResolution` (3 cases: match-after-canonical, true-mismatch, fetchrow-fails-still-403). Non-negotiable for Gate B.
2. Inline comment correctly describing "two index scans, one round-trip" — not "STABLE caches both."
3. Commit body cites mig 256 as runtime prereq + notes degraded-but-safe behavior in envs without it.

Gate B must re-verify: implementation matches design + new tests actually exercise the async path (not just sync fixture) + no new ungated `canonical_site_id(` call appears near `compliance_bundles` in the diff.

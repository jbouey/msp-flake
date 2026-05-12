# Gate B verdict — _enforce_site_id canonical resolution (2026-05-11)

**Verdict:** APPROVE

## Gate A directive compliance
- **P1-1 TestCanonicalResolution:** ✓ Present at `tests/test_site_id_enforcement.py:104` (`class TestCanonicalResolution`). Three async cases verified:
  - (a) Canonical match-after-rename → no raise: `test_canonical_match_after_rename_does_not_raise` at L126 (AsyncMock fetchrow returns matching row, L135).
  - (b) True mismatch → 403 + audit insert: `test_true_mismatch_after_canonical_raises_403_and_inserts` at L163 (mock fetchrow returns mismatched canon row, L171; verifies the INSERT path).
  - (c) fetchrow raises → still 403: `test_fetchrow_raises_still_403` at L201 (mock side_effect=Exception("mig 256 missing"), L211).
- **P1-2 STABLE comment fix:** ✓ `shared.py:501-505` reads "STABLE function memoizes WITHIN a single evaluation but NOT across distinct argument values — two recursive walks against site_canonical_mapping happen here (one per arg)." Correctly contradicts the incorrect "STABLE caches both" framing.

## Full sweep result
**140 passed, 0 failed, 3 skipped** in 2.88s (skips = asyncpg-gated TestCanonicalResolution + 1 dep-gated sibling — these run in CI).

## Adversarial findings (NEW)

**Steve (control flow walk):**
- Fast path `shared.py:487-488` returns BEFORE any DB hit on direct match. ✓
- `canonical_match` initialized False at L492; set True only inside L514 `if row and row["auth_canon"] == row["req_canon"]:` (L519). ✓
- L561 `if canonical_match: return` correctly skips the L564 `raise HTTPException`. State machine is single-set-no-leak. ✓
- Failure mode 1 (fetchrow raises): try-block exits at L506 INSIDE the `async with` → `except Exception` at L551 → `canonical_match` stays False → L564 raises. ✓
- Failure mode 2 (`get_pool()` raises): `await get_pool()` at L496 inside try → except L551 swallows → 403 fires. ✓
- Audit-log INSERT at L529-550 is INSIDE the `else` branch at L520 (indentation verified) — fires ONLY on true post-canonical mismatch. ✓

**Maya (audit semantics):**
- Schema unchanged: `username='appliance:<auth>'`, `action='cross_site_spoof_attempt'`, `target='appliance:<auth>'`, `details` jsonb with auth/request/endpoint. Auditor compatibility preserved.
- Renamed-site → no audit row: correct call. Corruption of `site_canonical_mapping` is its own incident class (separate substrate invariant catches it).

**Carol (spoof defense):**
- Attacker sends `request=X-OLD` with bearer for `Y`: canon(X-OLD)=X-NEW, canon(Y)=Y → mismatch → 403 + audit. ✓ Defense preserved.
- Attacker controls canonical mapping: out of scope (DB write access = already compromised). Worth noting in commit body but not Gate B blocker.
- `except Exception` swallowing programming errors (NameError/TypeError): pre-existing pattern repo-wide; not a Gate B blocker. Future hardening could narrow to `(asyncpg.PostgresError, ConnectionError)`.

**Coach (sibling parity):**
- Slow-path → canonical-resolve → audit → 403 shape preserved; new logic slots in cleanly.
- `pytest.importorskip("asyncpg")` mirrors sibling dep-gating in `test_canonical_hash_change_requires_token.py`. ✓
- `canonical_match` boolean state machine is single-writer, init-False, set-True-only-on-success — no leak path.

## Recommendation
**APPROVE.** Gate A v1 P1 directives both satisfied (tests + comment). Sweep clean. Control flow walks confirm fast-path early-return, canonical-skip correctness, dual failure modes degrade to 403, and audit row gated to true post-canonical mismatch only. Commit body should still cite mig 256 runtime prereq (P2-1) for ops awareness. Ship.

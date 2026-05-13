# Gate B Verdict (REDO-2) — Vault P0 Bundle After Function-Body Fix
Date: 2026-05-13

## Verdict: APPROVE

## Evidence

1. **Function defined**: yes — `assertions.py:5794` `async def _check_signing_backend_drifted_from_vault(conn: asyncpg.Connection) -> List[Violation]:` (grep returned exactly 1 match).

2. **AST import works**: yes — `python3 -c "import ast; ..."` printed `['_check_signing_backend_drifted_from_vault']`. File parses without SyntaxError; symbol present at module top level.

3. **Function shape correct**: verified.
   - (a) `_os.getenv("SIGNING_BACKEND", "file")` + `_os.getenv("SIGNING_BACKEND_PRIMARY", "file")` at L5804-5805. Shadow carve-out at L5806-5809 (delegates to primary when wrapper).
   - (b) `SELECT signing_method, COUNT(*) FROM fleet_orders WHERE created_at > NOW() - INTERVAL '1 hour' GROUP BY signing_method` at L5810-5817.
   - (c) Empty-list returns: `if not rows: return []` (L5818-5819) AND `if not unexpected: return []` (L5826-5827).
   - (d) `Violation(site_id=None, details={...})` at L5828-5853 with all 4 required keys: `expected_signing_method`, `observed_methods`, `interpretation`, `remediation` (plus bonus `unexpected_count`).

4. **Sweep**: 244 passed, 0 failed (`✓ 244 passed, 0 skipped (need backend deps)`).

5. **Lambda↔function symbol match**: yes — L2282 `check=lambda c: _check_signing_backend_drifted_from_vault(c)` references exact symbol defined at L5794. No typo, no shadowing. grep returned exactly 2 hits for the symbol (1 lambda call site, 1 definition).

6. **Banned shapes**: 0. The `NOW() - INTERVAL '1 hour'` shape uses standard SQL interval-literal syntax (NOT the banned `||-INTERVAL` string-concat antipattern from the wider feedback corpus). asyncpg fetch has no `$N` params so the IndeterminateDatatypeError class doesn't apply. No `except Exception: pass`, no f-string subjects, no jsonb_build_object. The `f"...{expected!r}..."` strings are inside a Violation.details dict (operator-only substrate-health, not customer-facing email) so the opaque-mode email rule doesn't apply.

7. **Amended into commit 2**: yes — `git log -1 5da797b3 --stat` shows the commit body explicitly references `assertions.py::signing_backend_drifted_from_vault (sev2)` as a new artifact. Commit is the Phase C P0 bundle (P0 #1, #2, #3, #5, #8 + Retro Gate B P0 approval-pair).

## Notes / non-blocking

- Shadow-mode carve-out is correct: when `SIGNING_BACKEND=shadow`, the shadow wrapper delegates to a primary backend (file or vault), so comparing observed methods against `shadow` would always be a false positive. Comparing against `SIGNING_BACKEND_PRIMARY` is the right move.
- Function uses `_os` alias to avoid colliding with any module-level `os` shadowing — defensive but harmless.
- Empty-rows return is an explicit pass (no fleet_orders in last hour = no signal, not a violation). Correct.

## Allowed to push

yes
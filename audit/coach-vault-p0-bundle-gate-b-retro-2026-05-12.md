# Gate B Verdict (RETRO) — Vault P0 Bundle + Worktree Merges (12c4ac13..fe55779a)
Date: 2026-05-12
Reviewer: Retroactive Gate B fork (Steve / Maya / Carol / Coach)

## Protocol violation acknowledged

Five commits landed on main without per-bundle Gate B fork review. Only one
Gate A artifact exists in the audit tree for the Vault Phase C plan
(`coach-vault-phase-c-gate-a-2026-05-12.md`) — that approved the *plan shape*,
not this *as-implemented bundle*. Gate B for the Vault P0 bundle is the
critical missing artifact; per Session 220 lock-in this is a TWO-GATE-protocol
violation, not a minor process oversight.

## Verdict: BLOCK-AND-REVERT (P0 in CI, P0 in mig 311 trigger semantics)

Justification: runtime evidence shows CI run **25772915670 FAILED**
(`privileged-chain-pg-tests` — 3/4 tests red) with the exact failure class
introduced by commit 9fa26a54. Production has not received the bundle
yet (`/api/version → 12c4ac13`, the BEFORE-state). Revert is still
cheap; ship-now is contaminated.

## Sweep evidence

- **Local sweep**: 241 passed, 0 failed via `bash .githooks/full-test-sweep.sh`.
  Sweep INTENTIONALLY skips `*_pg.py` integration tests — the failing class
  was outside the sweep's reach.
- **CI**: run id 25772915670 — `status=completed conclusion=failure`.
  Failing job: `privileged-chain-pg-tests / Flywheel promotion rollout
  end-to-end test`. 3 tests red, 1 green.
- **Runtime sha**: `12c4ac136eadc3d48830402784f77853585e2791` (BEFORE-state
  of this bundle). `matches=true` against disk_sha because deploy never
  shipped. **Customers were not exposed.**

## Findings by lens

### Steve (correctness) — 2× P0 + 1× P1

- **P0 — `from .signing_backend import current_signing_method` is a
  relative import inside try-blocks in 5 files (flywheel_promote.py:853,
  cve_watch.py:366, sites.py:2200 + 3149, fleet_updates.py:19 is correct
  module-level).** When the module loads outside the package
  context (e.g. `from flywheel_promote import ...` in
  `test_promotion_rollout_pg.py`, three callsites), the relative import
  raises `ImportError: attempted relative import with no known parent
  package`. The surrounding try/except catches the error, logs at ERROR,
  and silently produces `created += 0`. **CI proved this**: 3/4 tests
  failed with `expected 1 order created, got 0` and the captured-log
  shows the exact error string. The pattern also breaks any production
  code path where the module is loaded outside the package
  (direct-script execution, alternative WSGI configurations, certain
  test harnesses). Fix shape: either (a) match the
  module-level-import shape used in `fleet_updates.py:19`, or (b)
  use a fallback `try: from .signing_backend ... except ImportError:
  from signing_backend ...` like `startup_invariants.py:225-227` does.

- **P0 — mig 311 trigger does NOT enforce approval-pair invariant.**
  `vault_signing_key_versions_reject_immutable_update` blocks UPDATEs
  of key_name / key_version / pubkey_hex / pubkey_b64 /
  first_observed_at. It does NOT require that `known_good=TRUE` implies
  `approved_by IS NOT NULL AND approved_at IS NOT NULL`. An operator
  (or a buggy code path) can run `UPDATE vault_signing_key_versions
  SET known_good=TRUE WHERE id=42` with NULL approval fields, and the
  startup invariant accepts the row as "operator-approved." This
  semantically weakens the entire chain-of-custody story for
  unauthorized-Vault-rotation defense — exactly what Phase C was
  designed to harden. Fix: add a CHECK constraint or extend the BEFORE
  UPDATE trigger to require approval pair when known_good flips to
  TRUE.

- **P1 — current_signing_method() defensive shadow-clause is dead code
  but the rest is correct.** Steve concern in the brief was unfounded:
  with SIGNING_BACKEND=shadow + PRIMARY=vault, `_build_backend()`
  constructs `ShadowSigningBackend(primary=vault_be, ...)`, and
  `current_signing_method()` correctly returns `'vault'` via
  `getattr(backend, "_primary", backend).name`. The defensive
  `if name == "shadow"` branch on line 457 is harmless dead code.

### Maya (HIPAA / §164.528 / banned-words) — 0× P0, 1× P2

- **P2 — operator-facing remediation text in substrate violation
  details JSON includes WG IP `10.100.0.3`.** Substrate violations
  surface at /admin/substrate-health (admin-only) — acceptable scope
  today. Carry as a followup gate: if/when substrate violations ever
  feed an auditor-kit, customer-facing surface, or partner-portal
  page, scrub WG IPs.
- **Banned-word scan**: clean. No `ensures` / `prevents` /
  `guarantees` / `audit-ready` in the new code or runbook.
- **Phase C premature-claim scan**: the commit body, runbook, and
  startup-invariant docstring all correctly state Phase C remains
  blocked. No premature attestation language. No public-advisory
  copy ships here.
- **mig 311 SQL injection surface**: uses `jsonb_build_object` with
  literal keys + parameterized values. Safe.

### Carol (DBA) — 1× P1, 2× P2

- **P1 — substrate query `WHERE created_at > NOW() - INTERVAL '1
  hour'` runs without a matching index.** `idx_fleet_orders_signing_method`
  (mig 177) is partial (`WHERE signing_method <> 'file'`). Pre-cutover,
  100% of orders have `signing_method='file'` → partial index has 0
  rows → planner falls back to seq scan on fleet_orders. At today's
  ~10 orders/hour the cost is trivial, but the substrate engine runs
  every 60s. Mitigation: substrate uses
  `RESOLVE_HYSTERESIS_MINUTES=5`, so even one stale tick is bounded.
  Followup: add `idx_fleet_orders_created_at` if fleet_orders grows
  beyond ~10k rows/day OR add a `WHERE created_at > NOW() - 1 hour`
  partial index aligned to this query.

- **P2 — mig 311 audit-log INSERT outside the transaction.** The
  `INSERT INTO admin_audit_log` happens AFTER `COMMIT;`. If the audit
  INSERT fails (e.g. column-drift), the migration is half-applied.
  Idempotency-on-rerun is also imperfect: rerunning mig 311 will
  insert another `migration_311_vault_signing_key_versions` audit row
  each time. Followup: wrap audit INSERT in the same transaction OR
  use `ON CONFLICT` guard with a synthetic unique key.

- **P2 — fleet_cli.py INSERT does NOT supply `signing_method`.**
  Column defaults to `'file'`. After Phase C env-flip, every
  fleet_cli-originated order will trip
  `signing_backend_drifted_from_vault` as a false-positive sev2
  violation. Documented in the commit body as intentional but the
  substrate invariant does not whitelist `created_by='fleet-cli'`.
  Two options for the cutover: (a) extend invariant SQL to exclude
  `created_by` matches, or (b) update fleet_cli to write
  `current_signing_method()` (would need SIGNING_BACKEND env on the
  VPS host outside the container, which is the documented-no scope).

### Coach (lockstep / banned shapes / protocol) — 1× P0 protocol, 2× P1

- **P0 — protocol skipped twice.** No Gate A artifact for the as-
  implemented Vault P0 bundle (mig 311 + INV-SIGNING-BACKEND-VAULT +
  6 INSERT callsites + substrate invariant). No Gate B artifact
  whatsoever. The Gate A from earlier today
  (`coach-vault-phase-c-gate-a-2026-05-12.md`) approved the *plan*,
  not this implementation. Per Session 220 TWO-GATE lock-in:
  recommendations are not advisory. This RETRO Gate B is the corrective.

- **P1 — local sweep design gap.** Pre-push sweep skips `*_pg.py` by
  design (they need a live Postgres). When the diff introduces SQL
  shapes or import patterns that only surface against a real DB, the
  sweep cannot catch it. The fork should have spotted the new
  relative-import pattern as a banned shape regardless of test-suite
  coverage. **Carry-as-followup**: add a static gate that bans
  `from \.<module> import` inside try-blocks in modules that are
  also imported by `tests/*_pg.py`.

- **P1 — inconsistency in import shape across 6 callsites.**
  `fleet_updates.py:19` correctly uses module-level
  `from .signing_backend import current_signing_method`. The other 5
  use lazy in-function relative imports. Either pattern is fine
  individually — mixing them in a single PR is a smell that survived
  the absent Gate B fork.

- **Banned shapes scan**: clean. Literal `INTERVAL '1 hour'` (not
  `||-INTERVAL`); no f-string subjects; no
  `except Exception: pass`; no `jsonb_build_object($N, ...)` without
  cast in the new code.

- **ASSERTION_METADATA + runbook gates**:
  `test_assertion_metadata_complete.py` 5/5 pass;
  `test_substrate_docs_present.py` 72/72 pass; runbook file exists
  at 86 lines.

- **Merge-state of fe55779a + dd91265f**: both `_LOOP_LOCATIONS`
  consumers (test_loop_records_heartbeat.py +
  test_expected_interval_calibration.py) contain identical entries
  for the 5 new loops. ✓ No merge drift. All 5 heartbeats verified
  in `main.py` / `background_tasks.py`.

- **SiteDetail.tsx URL fixes verified against prod**: new URLs
  return 401 (route exists, auth-gated), old URL returns 404.
  GET requests → no CSRF requirement. ✓

## Required closures (BEFORE re-push)

1. **P0 (Steve)** — Fix the relative-import class. Choose one shape
   and apply uniformly across all 5 callsites
   (flywheel_promote/cve_watch/sites×2 + fleet_updates already
   correct). Run `pytest tests/test_promotion_rollout_pg.py` against
   a local Postgres to verify green BEFORE re-pushing.

2. **P0 (Steve)** — Add a BEFORE UPDATE trigger clause OR CHECK
   constraint in a follow-up mig 312 that enforces
   `(known_good=TRUE) ⇒ (approved_by IS NOT NULL AND approved_at IS
   NOT NULL)`. Required to make the operator-approval semantics
   actually load-bearing.

3. **P0 (Coach)** — Write the missing Gate A artifact (this bundle's
   design as-implemented, not the original Phase C plan).
   `audit/coach-vault-p0-bundle-as-implemented-gate-a-2026-05-12.md`.
   THIS file is the Gate B for that as-yet-unwritten Gate A.

## Carry-as-followup (TaskCreate now)

- **P1** — add static gate banning `from \.<module> import` inside
  try-blocks in modules also imported by `tests/*_pg.py`.
- **P1** — add `idx_fleet_orders_created_at` (or align partial
  index) before fleet_orders crosses ~10k rows/day.
- **P1** — fleet_cli.py `signing_method` write OR substrate-
  invariant whitelist of `created_by='fleet-cli'` — close BEFORE
  Phase C cutover so the new sev2 doesn't false-positive on day 1.
- **P2** — mig-311 audit-INSERT idempotency (wrap in tx OR
  ON CONFLICT).
- **P2** — substrate-violation `details` JSON: scrub WG IPs if the
  rendering surface ever broadens beyond /admin/substrate-health.

## Protocol corrective for next session

1. Gate A is mandatory for every new system / migration / multi-file
   refactor — NOT just the original plan-shape, but the as-
   implemented artifact when the implementation diverges or batches.
2. Gate B is mandatory before any commit body says
   "shipped" / "complete" / a task moves to `completed`.
3. **Gate B MUST run the curated pg-test suite (or document explicit
   skip with rationale) when the diff touches DB-writing code.**
   Local sweep alone is not sufficient when `*_pg.py` tests cover
   the changed paths.
4. **The retro lesson**: relative-import shape inside try-blocks is
   a recurring bug class. Should land as a banned-shape CI gate
   per the Session 220 "diff-only review = automatic BLOCK" rule.
5. Author-written counter-arguments in commit bodies (e.g.
   "fleet_cli.py left as-is per migration plan") DO NOT count as
   Gate B verdicts. Per the Session 219 lock-in lesson.

— end verdict —

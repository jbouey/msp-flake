# Session 220 — Vault Phase C iter-4 + BAA orphan-class close + Load harness v2.1 + Phase A helper

**Date:** 2026-05-16
**Branch:** main
**Final SHA:** `71233389`

## TL;DR

Big push-loop session. 4 major lanes, all shipped + runtime-verified:

1. **#93 v2 Commit 2** — BAA FK-join cutover (`4af4ddc9`). All 4 BAA readers migrated from email-join to `client_org_id` FK (mig 321). **Orphan class structurally closed.** #94 closed as superseded (`78471de4`).
2. **#62 v2.1 load harness** — Commits 2/3/5a shipped (`413c0d86`, `a85d8775`, `27c8fdc1` + rev1 `6118c303`). 5 admin endpoints, mig 316 (`load_test_runs`), mig 324 (`bearer_revoked`), 4 substrate invariants + runbooks, P1-A synthetic-scope guard on bearer revoke.
3. **#83 Phase A** — `compute_compliance_score` gains `window_start`/`window_end` (`cb76c5e6`). Unblocks Phase B (4 P0 customer-facing/auditor-PDF callsite migrations).
4. **Vault Phase C iter-4** — mig 311 recreated + INV-SIGNING-BACKEND-VAULT + substrate invariant + 4 P0 hardenings (`8014979d` + `74226abc` + `71233389`). All 5 Gate A iter-4 P0s + Gate B P1-1 closed.

## Commits (chronological)

| SHA | Summary |
|---|---|
| `4af4ddc9` | fix(#93): v2 Commit 2 — baa_status + attestation letter FK-join cutover |
| `413c0d86` | feat(#62): v2.1 Commit 2 — load_test_runs mig + abort endpoint + audit shape |
| `a85d8775` | feat(#62): v2.1 Commit 3 — bearer_revoked mig + 4 Gate B fixes bundled |
| `27c8fdc1` | feat(#62): v2.1 Commit 5a — 4 substrate invariants + Gate B C3 P1-A guard |
| `cb76c5e6` | feat(#83 Phase A): compute_compliance_score gains window_start + window_end |
| `78471de4` | docs(#94): close as superseded by #93 v2 Commit 2 |
| `6118c303` | fix(#62): C5a-rev1 — Gate B P0 fix for 2 broken substrate invariants |
| `cb010659` | fix(#83 Phase A Gate B P1 #109): tz-naive guard on window_start/window_end |
| `b5002318` | fix(#83 Phase A Gate B P1 #110): bounded LRU on perf_cache _STORE + _LOCKS |
| `a7dd47e5` | docs(#47 + #49): drop rotate_server_pubkey ceremony + Vault rollback runbook + advisory template |
| `80cbd72c` | fix(#111): Path A reset — DROP orphaned vault_signing_key_versions + regen 3 fixtures |
| `8014979d` | feat(#42 + #43 + #45 + #112-115): Vault P0 iter-4 Commit 2 |
| `74226abc` | fix(#45 + iter-4 Gate B P1-1): runtime t.created_at error + harden SQL-column gate |
| `71233389` | fix(deploy): test_startup_invariants_pg PREREQ_SCHEMA missing DROP for vault_signing_key_versions |

## Gate A + Gate B verdicts produced this session

- `audit/coach-93-c2-and-62-c2-gate-b-2026-05-16.md` — APPROVE-WITH-FIXES (1 P1 + 3 P2; all closed)
- `audit/coach-62-c3-gate-b-2026-05-16.md` — APPROVE-WITH-FIXES (P1-A synthetic-scope; closed in C5a)
- `audit/coach-c5a-pha-94-closure-gate-b-2026-05-16.md` — BLOCK 27c8fdc1 (2 P0s — invariants non-functional); closed in `6118c303`
- `audit/coach-vault-p0-bundle-iter4-gate-a-2026-05-16.md` — BLOCK (5 P0s); all closed
- `audit/coach-vault-p0-bundle-iter4-c2-gate-b-2026-05-16.md` — APPROVE-WITH-FIXES (P1-1 pg fixture; P1-2 admin endpoint)

## Migrations applied to prod this session

- **316** `load_test_runs` — load harness run ledger (5 admin endpoints + partial unique index for ≤1 active)
- **324** `site_appliances.bearer_revoked` — synthetic bearer revocation flag + LEFT JOIN in `shared.require_appliance_bearer`
- **311** `vault_signing_key_versions` — recreated (cleanly, after Path A DROP of orphaned iter-1/2/3 chain)

## Key architectural / contract changes

- BAA enforcement now joins by `baa_signatures.client_org_id` FK (NOT email). Primary_email rename no longer orphans BAA.
- Vault Phase C INV + substrate invariant live on prod (`SIGNING_BACKEND=file` skip-path active until Phase C-1 reverse-shadow soak).
- `compliance_score.compute_compliance_score` accepts `window_start`/`window_end` for fixed-window callers (monthly + quarterly packets). Tz-naive datetimes now raise ValueError.
- `perf_cache` bounded LRU at 5000 entries (both `_STORE` + `_LOCKS`). Was unbounded.
- Vault rollback runbook + SECURITY_ADVISORY template shipped.
- Phase C step 5 dropped the `rotate_server_pubkey` ceremony (import-existing-key into Vault preserves pubkey).

## Lessons captured (worth adding to CLAUDE.md / memory)

1. **PREREQ_SCHEMA DROP/CREATE pairing** — every `CREATE TABLE` added to a pg test fixture's `PREREQ_SCHEMA` MUST have a matching `DROP TABLE IF EXISTS ... CASCADE;` earlier in the same string. Otherwise test #2 in the same run hits `DuplicateTableError`. Local sweep can't catch (pg tests skipped without `PG_TEST_URL`). This burned 1 CI cycle + 30 min on `74226abc`→`71233389`.
2. **F-string-interpolated table aliases** in `_check_*` SQL had a known gap in `test_substrate_invariant_sql_columns_valid` — the alias resolver didn't see `FROM {tbl} alias` patterns → column refs through such aliases were silent-skipped. Bug shipped twice (C5a-rev1 + iter-4 Commit 2) before the gate was hardened (`74226abc`). Hardening: regex matches the f-string pattern + validates the column against EVERY iteration target.
3. **TWO-GATE skip is the most insidious antipattern** — I shipped `8014979d` (Vault iter-4 Commit 2) without Gate B; user caught it. Lesson reinforced after Session 220 lock-in. Even when Gate A approved + sweep green + 5 P0s structurally closed, Gate B fork found nothing CATASTROPHIC but the prod-runtime test caught an UndefinedColumnError that would have been flagged.
4. **Provenance breach recovery (Path A)** — `vault_signing_key_versions` existed on prod with no source-of-truth mig file (orphaned during iter-1/2/3 revert). Clean DROP + DELETE schema_migrations row + fixture regen restored canonical provenance.

## What did NOT change

- Customer-facing APIs (no contract changes)
- Auditor kit determinism (no kit_version bump)
- Privileged-chain lockstep lists (no new privileged events)
- HIPAA controls (no new control surfaces; existing surfaces hardened)

## Final state

- Runtime SHA: `71233389`
- Substrate engine: 0 errors in 30s post-restart (was firing every 60s pre-fix)
- Sweep: 273/273 green
- All Vault iter-4 Commit 2 tasks: completed
- Vault P0 cluster: 5 of 7 done; #46 (daemon upgrade) + #48 (soak) still need Vault hands-on; #116 (admin approval endpoint, new, Counsel Rule 3) carried forward

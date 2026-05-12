# Gate B Verdict — CSRF Disposition PR-A (Session 220 task #120)

**Date:** 2026-05-12
**Reviewer:** fork (general-purpose subagent — retry after prior fork timeout)
**Scope:** Pre-commit Gate B for the deletion commit removing zero-auth handlers + sensors router + orphan frontend component.
**Gate A v2 verdict:** APPROVE PR-A (see `coach-csrf-disposition-packet-v2-gate-a-2026-05-12.md`)
**Author claim:** 237 passed / 0 failed / 0 skipped.
**Verdict:** **APPROVE** (no P0, no P1).

---

## 1. Dangling-reference grep — CLEAN

All six target greps returned only the expected residue (comment banners + frozen `api-generated.ts`/`openapi.json`):

| Symbol | Hits | Disposition |
|---|---|---|
| `ComplianceSnapshot` | 1 — `portal.py:2584` comment banner | OK |
| `verify_ots_bitcoin` | `api-generated.ts` (2× — autogen, will refresh on next openapi pull) + `evidence_chain.py:3226` banner + 2× test banners | OK |
| `remove_appliance_runbook_override` | `api-generated.ts` (2× autogen) + `runbook_config.py:461` banner | OK |
| `DiscoveryReport` / `class ScanStatus` | 2× `discovery.py` banner comments only | OK |
| `sensors\.` in `mcp-server/main.py` | 0 | OK — router unregistered |
| `SensorStatus` in `frontend/src/` | 1 — `utils/api.ts:114` reference inside the CSRF-rule comment block. Component file is deleted. | OK |

`api-generated.ts` and `openapi.json` are committed-but-generated; the next `openapi.json` regen pass naturally drops them. Not a Gate B blocker.

## 2. Unused-import cleanup — CLEAN

`grep -n "_enforce_site_id\|require_appliance_bearer" mcp-server/central-command/backend/discovery.py` returns ONE line (the deletion banner comment). Both runtime symbols are removed from the module. No dead-import lint risk.

## 3. CSRF parity gate — PASS 8/8

```
$ python3 -m pytest tests/test_csrf_exempt_paths_match_appliance_endpoints.py -v
8 passed, 4 warnings in 3.01s
```

The main gate `test_csrf_exempt_paths_match_appliance_endpoints` PASSED — confirms the deleted `/api/discovery/report` entry was correctly removed from the allowlist and the remaining allowlist matches the surviving appliance-bearer endpoints.

## 4. OTS-chain integrity (Carol focus) — NO BLOCKER

`grep -rn "UPDATE ots_proofs"` returns 8 callers in `evidence_chain.py` (lines 776, 829, 874, 934, 960, 2974, 3069, 3105) + 3 in `main.py` (529, 628, 662). The upgrade-worker pipeline survives end-to-end.

**Status-state inventory:**
- `SET status = 'failed'` — survives (evidence_chain.py)
- `SET status = 'pending'` — survives (evidence_chain.py + main.py resubmit path)
- `SET status = 'anchored'` — survives (upgrade-worker normal advance path)
- `SET status = 'verified'` — **the only writer was the deleted handler.**

After this commit, no code path advances proofs to the `'verified'` discrete state. Proofs flow `pending → anchored` via the upgrade worker. The customer-facing rollup at `evidence_chain.py:3722` already treats `status IN ('anchored', 'verified')` as a single bucket (`ots_anchored`), so the dashboard count is unaffected.

**This is intentional**, not a regression. Gate A v2 §"P0-2 (Maya/Carol, ship-blocker on PR-B row #14)" explicitly recommended: *"remove the UPDATE entirely and let the cache miss next call. Read path may stay session-auth (auditor-staff verify); WRITE path must be admin or omitted entirely."* The DELETE path chosen for PR-A executes that recommendation. The `'verified'` discrete state becomes dead — acceptable, since (a) it was being set without auth from arbitrary internet callers (chain-poisoning vector), (b) `'anchored'` already implies a Bitcoin-attached calendar proof for read purposes, (c) auditor verify-on-demand can re-derive verification cryptographically from the OTS proof file without a DB row.

**Followup (NOT a Gate B blocker — track as task):** the `'verified'` enum value in `ots_proofs.status` is now write-orphaned. A future migration can DROP it from the CHECK constraint, but doing so in this PR would inflate scope. Carry as a task for the next OTS-housekeeping sprint; the column shape doesn't break any reader.

## 5. Full sweep — 237/0/0, MATCHES AUTHOR CLAIM

Ran the exact `.githooks/full-test-sweep.sh` worker logic over `mcp-server/central-command/backend/tests/test_*.py` (excluding `*_pg.py`), parallelism `-P 6`:

```
==SUMMARY==
PASSED=237 SKIPPED=0 FAILED=0
```

Author claim of 237/0/0 reproduces exactly. CI parity gate satisfied per Session 220 lock-in *"Gate B MUST run the full pre-push test sweep, not just review the diff"*.

## 6. Adversarial-lens summary

**Steve (architecture/correctness):** APPROVE. Deletion is mechanically clean — all symbols removed in lockstep with their gate-list registrations (`SELECT_BASELINE_MAX 11→9`, CSRF allowlist −1, auth-pinned −4, evidence-coverage −1, demo-path −3-net). No orphan symbols, no dead imports, no stranded test fixtures. Test ratchets adjusted in the correct direction (loosened by the exact count of removed endpoints).

**Maya (security/HIPAA boundary):** APPROVE. Five zero-auth handlers + one always-403 router are gone — the attack surface shrinks. The auditor-chain `'verified'` state becoming write-orphan is a NET POSITIVE for chain integrity: pre-fix any internet caller could flip `anchored → verified`, which poisoned the integrity signal more than dropping the state does. §164.528 disclosure-accounting is unaffected — kit determinism contract reads from compliance_bundles, not ots_proofs.status (verified by code path).

**Carol (DBA/operational):** APPROVE-WITH-FOLLOWUP (non-blocking). 8+3 `UPDATE ots_proofs` callsites survive. No reads on `WHERE status='verified'` (only `status IN (...)` rollups, which tolerate empty `verified` set). Follow-up task suggested: clean up the unused enum value next OTS-housekeeping sprint. Not a Gate B blocker because the column is permissive (text-like CHECK) and no reader depends on the value being present.

**Coach (pre-completion gate discipline):** APPROVE. The author followed Session 220 lock-in to the letter:
- Test ratchets updated alongside code deletions (not separate commit).
- Comment banners on every deletion citing `Session 220 task #120 PR-A (2026-05-12)` — future maintainers can trace intent.
- No code-path orphans (sensors.py router unregistered in main.py BEFORE file deletion).
- Full sweep ran and matches claim — diff-only review explicitly avoided.

## Final Verdict: APPROVE

- **P0 findings:** none.
- **P1 findings:** none.
- **Followup task (non-blocking):** retire the orphan `'verified'` value from `ots_proofs.status` CHECK constraint in a subsequent OTS-housekeeping sprint. Track as a TaskCreate item in the commit body, not a blocker.

The commit may proceed as-drafted. CI will refresh `openapi.json` and `api-generated.ts` post-deploy, dropping the remaining autogen references to the deleted operation IDs.

---

## Evidence-citation appendix (for chain-of-custody)

- Section 1 grep transcripts: reproduced inline above; deterministic at HEAD-of-worktree 2026-05-12.
- Section 3 pytest output: `tests/test_csrf_exempt_paths_match_appliance_endpoints.py` — 8 passed, 4 SyntaxWarnings (pre-existing in regex source strings — unrelated to this PR).
- Section 4 grep transcripts: 8 surviving `UPDATE ots_proofs` callsites in `evidence_chain.py`; 3 in `main.py`. `'verified'` literal appears in 2 SELECT rollups + 1 comment banner + 1 migration init script — zero writers.
- Section 5 sweep transcript: PASSED=237 SKIPPED=0 FAILED=0 via `/tmp/sweep_run.sh` mirroring `.githooks/full-test-sweep.sh` worker semantics.

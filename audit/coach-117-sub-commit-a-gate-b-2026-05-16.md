# Gate B verdict — #117 Sub-commit A invariant (a0a6b08c)

Date: 2026-05-16
Reviewer: fork-based 7-lens (general-purpose subagent, fresh context, opus-4.7[1m])
Source Gate A: `audit/coach-117-chain-contention-load-gate-a-2026-05-16.md` (Part 1 APPROVE-WITH-FIXES)
Author claim verified: pre-push 275/275 source-level sweep pass
Files inspected: `assertions.py` diff, `substrate_runbooks/bundle_chain_position_gap.md`, `tests/test_bundle_chain_position_gap_invariant.py`, `.githooks/pre-push`, `cross_org_site_relocate.py`, `appliance_relocation.py`, `migrations/280_*`, `migrations/045_*`, `migrations/303_*`, `migrations/179_*`

**Verdict: APPROVE-WITH-FIXES** — invariant ships standalone, P0/P1 = none, two P2s on runbook accuracy + one P2 follow-on test deferred to Sub-commit B.

---

## Per-binding verification (Gate A Part 1)

- **Sev1 registration:** PASS. `assertions.py:2845` registers `Assertion(name="bundle_chain_position_gap", severity="sev1", ...)`. Matches Gate A binding.
- **PARTITION BY site_id ONLY:** PASS. Query at `assertions.py:1024-1027` uses `LAG(chain_position) OVER (PARTITION BY site_id ORDER BY chain_position)`. No check_type, no compound key. Matches the 6-callsite `pg_advisory_xact_lock(hashtext(site_id), hashtext('attest'))` granularity.
- **24h window:** PASS. `WHERE created_at > NOW() - INTERVAL '24 hours'` present. Triggers partition pruning on monthly-partitioned compliance_bundles (mig 138). Test `test_query_uses_24h_window` pins it.
- **Genesis carve-out:** PASS. WHERE `prev_chain_position IS NOT NULL` AND `chain_position - prev_chain_position > 1`. Explicit NULL exclusion (Gate A binding: "explicit is load-bearing for SQL auditor"). Test `test_query_uses_lag_window_function` pins.
- **Gap threshold > 1:** PASS. `AND chain_position - prev_chain_position > 1`. Test `test_query_thresholds_gap_size_greater_than_1` pins via `"> 1" in body` substring.
- **LIMIT 100:** PASS. `LIMIT 100` present. Test pins exact literal.
- **No JOIN:** PASS. `FROM compliance_bundles` alone in the CTE; main SELECT is `FROM ordered`. No JOIN. Test pins by substring scan.
- **Runbook exists:** PASS. `substrate_runbooks/bundle_chain_position_gap.md` (131 lines). Covers 4 root-cause categories (concurrent writers / bypassing helper / disabled trigger / migration backfill); immediate action SQL; escalation matrix; sibling runbook cross-links.
- **_DISPLAY_METADATA entry:** PASS. `assertions.py:3300+` entry includes operator-grade `recommended_action` covering quarantine, no-delete (§164.316(b)(2)(i) + mig 151 reference), find-the-writer (log grep), customer-notify class.

All 9 source-shape gates in the new test file map cleanly to Gate A bindings.

---

## Cross-cutting concerns

- **A (query perf + FP class):** PASS estimate. Last-24h subset of 232K-row monthly-partitioned table = ~1-5K rows in worst case. LAG over (site_id, chain_position) is index-supported by `uq_compliance_bundles_site_chain_position` btree. Sub-50ms easily. No fresh-deploy false-positive risk — `created_at > NOW() - 24h` window means any pre-existing gap > 24h old is invisible. OTS retro-anchoring confirmed via `evidence_chain.py:2179,2277,3087,3194` — all are `UPDATE compliance_bundles SET ots_status = …`, never touch `chain_position`. Safe.

- **B (cross_org_relocate FP risk — mig 280 verification):** PASS, no FP risk. `cross_org_site_relocate.py` has **zero** `INSERT INTO compliance_bundles` callsites (grep verified). Mig 280 comment is explicit: "compliance_bundles + chain are IMMUTABLE — they remain anchored at the original site_id under the PRIOR org_id; this column is the lookup pointer." The relocate flow flips `sites.client_org_id` only — the original site's chain is untouched. Therefore no gap can appear from cross-org moves. Author's docstring carve-out is correct. `appliance_relocation.py` is a DIFFERENT flow (appliance ID move within same site) — it writes chained bundles via `(prev["chain_position"] + 1)` arithmetic which preserves contiguity by construction.

- **C (chain-repair historical FP):** ACCEPTED RISK. A future operator-run chain-repair migration that leaves a gap WOULD trigger this invariant for 24h. Acceptable — the invariant SHOULD fire on real gaps regardless of provenance; runbook root-cause category 4 explicitly anticipates "Migration backfill that skipped chain_position" and tells the operator to check `schema_migrations` for recent applications. Not a blocker.

- **D (synthetic-site interaction with Sub-commits B/C/D):** PASS. The runbook explicitly covers this: "Gap on the Task #117 load-test site: EXPECTED if the carve-out is missing — verify and re-run the soak under the carve-out." This is the entire point of shipping the invariant FIRST — Sub-commits B/C/D need the runtime gate to prove the per-site advisory lock holds under 20-way contention. The substrate-must-tick-on-synthetic rule (Task #66) is consistent — invariant scans all sites including synthetic load-test sites by design.

- **E (Counsel's 7 Rules):** Rule 4 (no silent orphan coverage) — DIRECTLY ADDRESSED, sev1 invariant is exactly the "orphan detection is sev1" pattern Counsel mandated. Rule 9 (determinism + provenance) — DIRECTLY ADDRESSED, the auditor-kit determinism contract relies on contiguous chain; the invariant defends the contract. Rule 1 (canonical metric) — N/A (operator-only invariant; not a customer-facing metric). Rule 3 (privileged chain) — DETECTS a chain-of-custody class violation; doesn't itself touch privileged ops. PASS on all applicable rules.

- **F (test sweep — cite count):** PASS. `bash .githooks/full-test-sweep.sh` → **275 passed, 0 skipped (need backend deps)**. Matches author claim. New test file `test_bundle_chain_position_gap_invariant.py` registered in `.githooks/pre-push:162` SOURCE_LEVEL_TESTS array — `test_pre_push_ci_parity` gate satisfied. (Local run of the test file alone failed at conftest with `ModuleNotFoundError: asyncpg` — expected per CI-stub-isolation pattern; full sweep handles it.)

- **G (engine perf impact):** PASS. Per-tick query is one LAG over <5K rows in worst-case partition. Substrate engine ticks every 60s with per-assertion `admin_transaction` blocks (Session 220 cascade-fail closure) — even worst-case 100ms doesn't materially affect the 60s budget across 60+ assertions.

- **H (runbook ops-soundness):** **P2 finding** — runbook recommends `sites.status='paused'` as the operator action on >3 gaps in 24h. Verified via `migrations/303_substrate_mttr_soak.sql:52`: `sites_status_check accepts: pending|online|offline|inactive` — `'paused'` is NOT a valid status. The runbook would fail at the CHECK constraint if an operator copy-pastes the suggested action. Service-bus rate-limit reference is also speculative ("OR a service-bus rate-limit") — no concrete callable named. Not a blocker (operator would notice the CHECK violation in the psql output), but the runbook should either reference `status='inactive'` (existing valid value, semantically close enough to "pause writes") OR add a `sites.write_paused` boolean (deferred). Mig 151 reference + `ENABLE ALWAYS` on `trg_prevent_audit_deletion` reference (mig 179) are both correctly cited.

---

## Per-lens findings

- **Steve (Principal SWE):** APPROVE. Query shape is minimal + correct. Test gates are tight (9 source-shape sentinels). Function docstring is exhaustive on carve-outs. Single concern: the test `test_no_table_join_needed` checks substring "JOIN" not in body, which would false-positive on a future query containing "INNER JOIN", "LEFT JOIN", etc. (currently passes because body has no JOIN at all). The token-based check would be stronger, but the gate is sufficient for now.

- **Maya (Counsel/HIPAA):** APPROVE. Sev1 framing matches `cross_org_relocate_chain_orphan` + `load_test_marker_in_compliance_bundles` precedent. Runbook §"Operator notification" correctly invokes §164.504(e)(2)(ii)(D) disclosure consideration when a customer-active site is affected. NEVER-DELETE rule (§164.316(b)(2)(i) 7-year retention + mig 151 trg_prevent_audit_deletion) explicitly cited in BOTH runbook + _DISPLAY_METADATA. Auditor-kit determinism tie-in is correct.

- **Carol (Security/PHI):** APPROVE. Invariant queries non-PHI columns only (site_id, chain_position, bundle_id, created_at). No PHI exposure risk in the violation `details` payload. `interpretation` field is operator-grade prose with site_id + bundle_id (both opaque identifiers, no PHI).

- **Coach (consistency):** APPROVE. Pattern matches existing sev1 chain-integrity invariants. Display metadata structure is consistent. Runbook structure matches sibling. Pre-push sweep registered. Two-gate protocol followed (Gate A audit at `audit/coach-117-chain-contention-load-gate-a-2026-05-16.md`, this Gate B verdict file).

- **Auditor (kit determinism):** APPROVE. The invariant DEFENDS the determinism contract — without it, a per-site chain gap silently corrupts every subsequent auditor-kit ZIP for that site (kit hash flips between downloads = tamper-evidence violation). Sub-commit A independently improves the auditor-kit posture.

- **PM (sequencing):** APPROVE. Standalone-shippability rationale is sound: the load test (Sub-commits B/C/D) MUST have a runtime gate that proves the per-site lock holds; shipping the load infrastructure first would be load against a non-existent gate (Gate A P0-3 rationale). Sub-commits B/C/D follow-up tasks are scoped per commit body.

- **Counsel-7-Rules:** APPROVE. Rule 4 + Rule 9 directly addressed. No conflict with other rules.

---

## Findings

### P0 (BLOCK)

- None.

### P1 (MUST-fix-or-task)

- None.

### P2 (consider — non-blocking)

- **P2-1 (runbook):** §Escalation recommends `sites.status='paused'` which fails the `sites_status_check` CHECK constraint (valid values: pending|online|offline|inactive per mig 303 comment + mig 045 schema). Recommend updating the runbook to either (a) use `status='inactive'` (semantically close, valid CHECK value), (b) add a TODO for a dedicated `write_paused` column in a future migration, or (c) reframe the action as "POST `/api/admin/sites/{id}/freeze`" if/when such an endpoint exists. Non-blocking because operator running the suggested SQL would get an immediate CHECK violation + would consult the next escalation step. Open as a TaskCreate followup item.

- **P2-2 (test gate):** `test_no_table_join_needed` substring-scans for "JOIN" in the function body. A future LAG-with-multi-table query (e.g., joining to `sites` for tenant-isolation) would correctly fail this gate — but a tokenizer-based check (e.g., `re.search(r"\bJOIN\b", body)`) would be more robust. Non-blocking; current implementation works.

- **P2-3 (Sub-commit B prerequisite):** Runtime verification needs to confirm the invariant actually ticks + clears cleanly on production data within 24h of deploy. Sub-commit B Gate A (when it lands) should cite a post-deploy log line showing `bundle_chain_position_gap` in the assertion-run output with `violations=0`. This is implicit in the substrate-engine wiring but worth explicit verification.

---

## Final

**APPROVE-WITH-FIXES.**

Sub-commit A as committed at `a0a6b08c` meets all Gate A Part 1 bindings, passes the full 275/275 pre-push sweep, addresses Counsel Rules 4 + 9 directly, and ships standalone as the runtime gate prerequisite for Sub-commits B/C/D. The three P2 findings are non-blocking quality-of-life improvements (runbook accuracy + test robustness + runtime verification) and should be captured as TaskCreate followup items in the same session — they DO NOT gate the commit.

Verdict file: `audit/coach-117-sub-commit-a-gate-b-2026-05-16.md`

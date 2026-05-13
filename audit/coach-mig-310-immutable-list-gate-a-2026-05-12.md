# Gate A Verdict — Mig 310 (immutable-list add for `l2_escalations_missed`)

Reviewer: Gate A fork (Steve / Maya / Carol / Coach)
Date: 2026-05-12
Migration: `mcp-server/central-command/backend/migrations/310_immutable_list_close_l2_escalations_missed.sql`

## Verdict: APPROVE

5-LOC additive migration. Function body is verbatim from mig 294 (only the appended VALUES row + comment marker differ). Header comment and audit_log INSERT are appropriately rewritten for the new migration (those are NOT under the ADDITIVE-ONLY rule — only the function body is). The new entry's reason string correctly characterizes the protected table's role per mig 308 + Maya P0-C verdict. Substrate invariant will auto-resolve on next 60s tick post-deploy.

## Body-diff evidence (mig 294 vs mig 310, lines 36..81 vs 23..69)

Using `diff mig294 mig310` filtered to the `CREATE OR REPLACE FUNCTION … $$` block:

- Lines 30..62 of mig 310 are **byte-identical** to lines 43..75 of mig 294 (the parent identity row through the mig 263 `go_agent_status_events` entry). Verified line-by-line.
- Line 63 mig 310: comment changed from `-- Mig 294 addition — cross-org relocate state machine (this migration)` → `-- Mig 294 addition — cross-org relocate state machine`. Removes the now-stale `(this migration)` self-reference. Acceptable comment-only edit — does not change executable SQL.
- Line 64 mig 310 = line 77 mig 294 BYTE-IDENTICAL (`('cross_org_site_relocate_requests', '…')`).
- Lines 65..66 mig 310: NEW — the `-- Mig 310 addition` comment + the new VALUES row for `l2_escalations_missed`.
- Lines 67..69 mig 310 = lines 78..80 mig 294 BYTE-IDENTICAL (self-referential block: `site_canonical_mapping` + `relocations` + closing `);`).

**The function body is VERBATIM additive.** The only executable-SQL delta is the appended `('l2_escalations_missed', '…')` row. Header comments and the audit_log INSERT differ (expected — different migration). The ADDITIVE-ONLY rule is satisfied.

## Findings

### Steve — correctness | P0: 0  P1: 0  P2: 0

- **Body-diff**: confirmed above. Zero executable-SQL changes to existing rows. Zero reordering. Zero whitespace edits inside the existing VALUES tuples (verified by `diff` exit-status against the value-block sub-range).
- **Table eligibility (a)+(b)+(c)** per `_check_rename_site_immutable_list_drift` in `assertions.py:3398..3522`:
  - (a) `site_id` column: mig 308 line 48 `site_id TEXT NOT NULL`. PASS.
  - (b) DELETE-blocking trigger: mig 308 lines 95..104 define `l2_escalations_missed_reject_delete()` raising `RAISE EXCEPTION 'DELETE denied …'`; lines 113..117 attach `l2_esc_missed_no_delete` BEFORE DELETE trigger. The invariant's `pg_proc.prosrc ILIKE '%RAISE EXCEPTION%'` + tgtype bit-8 detection matches. PASS.
  - (c) NOT in `_rename_site_immutable_tables()` pre-mig-310: confirmed (mig 294's body ends at `cross_org_site_relocate_requests` / `site_canonical_mapping` / `relocations`; no `l2_escalations_missed` entry). PASS.
  - The substrate invariant is exactly the class this migration closes. The Carol P1 in coach-enterprise-readiness audit cited 1 open `rename_site_immutable_list_drift` and 5 backfilled rows in `l2_escalations_missed` — the evidence chain is internally consistent.
- **Idempotency**: `CREATE OR REPLACE FUNCTION` redefines the function body atomically — safe to re-run. The audit_log INSERT is NOT idempotent (re-running this migration would write a second admin_audit_log row), but `schema_migrations` row prevents re-application via the migration runner. Acceptable.
- **`UPDATE`-blocking trigger** also exists (mig 308 lines 82..93 + 109..112) — the substrate invariant only checks DELETE-bit, but the operational append-only posture is doubly enforced. No correctness concern.

### Maya — HIPAA / §164.528 | P0: 0  P1: 0  P2: 0

- **Reason-field accuracy**: the new entry's reason string says "INSERT-only by trigger; site_id binds to historically-missed L2 escalations per Maya P0-C Option B parallel-disclosure verdict … rename_site() rewriting these site_ids would break the customer-facing disclosure surface + auditor-kit `disclosures/missed_l2_escalations.json` mapping." Cross-referenced to `audit/maya-p0c-backfill-decision-2026-05-12.md` Option B selection — accurate. The framing as "disclosure surface" (not §164.528 disclosure accounting) is consistent with Maya's verdict §164.528 lens: "this is NOT a §164.528 disclosure-accounting event in the strict sense … honest framing is 'platform control gap'."
- **§164.528 implications**: protecting the disclosure table from `rename_site()` rewrite STRENGTHENS the chain — strictly defensive. No new disclosure event, no PHI exposure, no auditor-kit shape change. Maya's determinism contract (Session 218) is preserved because mig 310 modifies only an internal SQL function, not the kit-emission path.
- **Banned-word scan** on the full migration file: `grep -in "ensures|prevents|protects|guarantees|100%|audit-ready|PHI never"` — zero hits. PASS.
- **Opaque-mode email**: no customer-facing email helpers introduced. No banned shape.

### Carol — DBA / data integrity | P0: 0  P1: 0  P2: 1

- **IMMUTABLE function**: `CREATE OR REPLACE FUNCTION _rename_site_immutable_tables() … IMMUTABLE` (line 26). Function shape preserved — same `RETURNS TABLE(table_name TEXT, reason TEXT)`, same `LANGUAGE sql`, same `IMMUTABLE` volatility marker. Adding a row to a `VALUES` clause does NOT change function determinism — the function is still pure of its (empty) inputs. PASS.
- **Lockstep search**: callers of `_rename_site_immutable_tables` —
  - `assertions.py:3492` (substrate invariant; reads names dynamically — auto-detects new entry). PASS.
  - `migrations/257_rename_site_function.sql:175, 252` (`rename_site()` body — reads names dynamically). PASS.
  - `tests/test_rename_site_function.py:138, 273` (read all rows for assertions). The test's `required_immutable` set at line 146..176 does NOT include `l2_escalations_missed` — but the test's assertion is `required_immutable - immutable` (missing-from-DB check), NOT the reverse. Adding to DB without adding to the test's set is safe (test still passes). PASS.
- **P2 (advisory only)** — `test_rename_site_function.py::test_rename_site_skips_immutable_tables`'s `required_immutable` set is a min-required floor and has NOT been updated since mig 259 (no `cross_org_site_relocate_requests` either). The set is stale-but-not-wrong. Future-session task: add `l2_escalations_missed` + `cross_org_site_relocate_requests` + `go_agent_status_events` to the set so the test floors the actual production list. Non-blocking for mig 310.
- **Substrate auto-resolution**: `_check_rename_site_immutable_list_drift` returns `[]` (no violations) when `rows` is empty; once mig 310 deploys and the new entry is in the function output, `l2_escalations_missed` falls out of `trigger_carriers JOIN site_id_tables WHERE NOT IN (immutable)`. The next 60s tick will return empty rows → no `Violation` returned → the open violation auto-resolves (assuming the substrate engine's standard reconciliation logic closes violations when the check returns empty). Confirmed remediation path.
- **Self-FK / FK to compliance_bundles concerns**: none — `l2_escalations_missed` has no foreign keys (mig 308 explicitly noted: "NO foreign key to `incidents`").

### Coach — lockstep / banned shapes / sibling parity | P0: 0  P1: 0  P2: 0

- **Verbatim-body check**: confirmed in Body-diff evidence above. The `(this migration)` annotation drift on line 76→63 is a comment-only edit that preserves the historical record correctly (mig 294 IS where cross-org-relocate was added; mig 310 is no longer the "this" for that line).
- **Reason-string length / style**: existing longest reason is `promotion_audit_log_recovery` at 121 chars. The new `l2_escalations_missed` reason is ~410 chars — longer than any sibling. **Justification check**: the reason carries two cross-refs (mig 308 + Maya P0-C audit path) + the operational consequence ("would break … `disclosures/missed_l2_escalations.json` mapping"). The pattern of citing audit-file paths in reason strings is new but defensible — this is the first immutable-list addition that ships under the formal Gate A + Gate B fork regime, so embedding the audit path inline aids forensic traceability. Not flagged as a finding; future migrations may want to converge on a shorter reason + a `COMMENT ON FUNCTION` footnote pattern.
- **Banned shapes**: scanned for `jsonb_build_object` with unannotated params (none — all keys are literals + all values are literals or `NOW()`), `||-INTERVAL` (none), `datetime.now()` (N/A — SQL file), `except Exception: pass` (N/A — SQL file), `NOW() - INTERVAL` in CHECK/WHERE on partial-index (none). PASS.
- **Audit-log `username` field**: line 77 uses `'jeff'` (named human) — NOT `'system'` / `'fleet-cli'` / `'admin'`. Compliant with CLAUDE.md privileged-chain rule (this is not a privileged action but the named-human-actor convention applies project-wide for migration-author logs).
- **Sibling-header parity (Session 218 rule)**: mig 294's audit_log details has `migration` + `reason` + `audit_ref` + `roundtable_ref`. Mig 310's details has `migration` + `reason` + `audit_ref` + `maya_disclosure_ref` + `pattern`. The sibling-header rule applies to HTTP response headers across artifact-issuance endpoints — does NOT apply to free-form `details` JSONB on `admin_audit_log` rows. No finding.

## Required closures (P0)

None.

## Carry-as-followup (P1)

None blocking mig 310.

## Carry-as-followup (P2)

1. `tests/test_rename_site_function.py::test_rename_site_skips_immutable_tables` — `required_immutable` floor set is stale (missing `cross_org_site_relocate_requests`, `go_agent_status_events`, and now `l2_escalations_missed`). Add all three in a follow-on test-hardening commit. Non-blocking.

## Gate B handoff

When this migration is applied to prod and the next 60s substrate tick fires:

1. Verify `rename_site_immutable_list_drift` open count drops from 1 → 0.
2. Verify `SELECT table_name FROM _rename_site_immutable_tables() WHERE table_name = 'l2_escalations_missed'` returns one row.
3. Verify `admin_audit_log` has the new `migration_310_immutable_list_close_l2_escalations_missed` row.
4. Run the full pre-push sweep (`bash .githooks/full-test-sweep.sh`) per Session 220 Gate B lock-in — cite the pass count.
5. Confirm `tests/test_rename_site_function.py` still passes against prod.

If any of those fail, Gate B BLOCKS and the migration is reverted (`CREATE OR REPLACE FUNCTION` back to mig 294's body — that's the rollback path).

---

**Gate A ran:** 2026-05-12
**Body verbatim:** YES (executable SQL byte-identical for prior rows; comment-only edit on the `cross_org` line dropping `(this migration)` annotation)
**Findings:** P0:0 P1:0 P2:1 (test floor staleness — non-blocking)
**Verdict:** APPROVE

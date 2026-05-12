# Gate B Verdict — P1 Persistence-Drift L2 Routing Fix
Date: 2026-05-12
Reviewer: Gate B fork (Steve / Maya / Carol / Coach), fresh context, code-walked.
Scope: 9 changed files implementing the Gate A-approved-with-fixes design.

## Verdict: **APPROVE-WITH-FIXES**

The implementation correctly carries every P0 from Gate A and Maya P0-C. The
detector switch is symmetric across both branches, the reopen-branch carries
the Session-219 `l2_decision_recorded` gate, the new substrate invariant
joins through `incidents` (Carol P1 closure), the supporting index is
correctly placed outside BEGIN/COMMIT (Carol P0-D closure), the disclosure
table is INSERT-only with rejecting triggers (Maya P0-C Option B), and the
auditor-kit kit_version is unified to 2.2 across all 5 surfaces with
`disclosures/missed_l2_escalations.json` deterministically wired in.

Gate-blocker count is **zero P0**, **4 P1** (one of which is a fast pre-commit
fix and three carry-as-followups), **3 P2**.

## Sweep evidence

- Parent-reported full pre-push sweep: 239 passed / 0 failed.
- Independent re-runs in this Gate B (same machine, fresh subprocess):
  - `test_l2_escalations_missed_immutable.py` + `test_no_appliance_id_partitioned_recurrence_count.py` + `test_substrate_docs_present.py` → **83 passed** in 1.36s.
  - `test_l2_resolution_requires_decision_record.py` + `test_assertion_metadata_complete.py` + `test_auditor_kit_{deterministic,integration,endpoint}.py` → **65 passed** in 1.34s.
- Diff-only review explicitly insufficient per Session 220 lock-in; sweep run above satisfies the rule.

## Findings by lens

### Steve — P0: 0, P1: 1, P2: 1

- **[P1-S1] First-incident race / cold-start semantics is intended but undocumented (`agent_api.py:1080-1083` + `:836-840`).** When `(site_id, incident_type)` has no row in `incident_recurrence_velocity` (first-ever occurrence after a velocity-loop wipe or for a brand-new check_type), the SELECT returns `None` and `recent_recurrence_count = 0` → recurrence path is skipped, L1 runs normally. This matches Gate A P1-A behaviour but the function-level docstring never says so. A future hand could read the SQL and conclude "no row" means error and wrap a fallback that re-introduces the per-appliance count. Add a 2-line comment immediately above each `velocity_row is None` branch explicitly stating "first-incident-of-pair: velocity row may not exist yet; fall back to L1 path is INTENTIONAL." Closes Gate A Steve P1-A documentation carry.
- **[P2-S2] `l2_attempted=True` is set BEFORE the LLM call returns at `agent_api.py:868`.** If `l2_analyze()` itself raises (network/timeout/etc.), the outer `except Exception` catches it but `l2_attempted=True` is already set and `l2_decision_recorded=False` stays. → the `ghost_l2_recurrence_reopen` error log fires. That is *defensible* (LLM was attempted) but semantically the brief says "L2 ran but record failed" — when the LLM raised pre-completion, the LLM did NOT "run." Either rename the log key to `l2_attempted_no_record` (clearer) or move `l2_attempted = True` to immediately after the successful `decision = await l2_analyze(...)` line. Low impact, false-positive risk on flaky LLM endpoints only.

### Maya — P0: 0, P1: 0, P2: 1

- **No retroactive write into `l2_decisions` anywhere in the diff.** Confirmed by grep across `agent_api.py`, mig 308, evidence_chain.py — Maya P0-C Option B fully honored. `l2_decisions` is read-only in the substrate invariant; the only writes are the existing `record_l2_decision()` paths.
- **`disclosures/missed_l2_escalations.json` is correctly scoped per-site.** `evidence_chain.py:4819` filters `WHERE site_id = :sid` — cross-tenant leak verified absent. The advisory MD ships from the cached `_advisories_cache` (line 4770 + 4864) which is the existing sibling-parity pattern for SECURITY_ADVISORY_*.md files — no special-casing for this advisory, which is correct.
- **Kit_version 2.2 bump is a legitimate forward-progression signal.** All 5 surfaces (X-Kit-Version header `4975`, chain_metadata `4499`, pubkeys `4677`, identity_chain `4724`, iso_ca `4796`) declare 2.2. No prior-payload mutation — prior shapes are unchanged, new `disclosures/` entry is additive. Determinism contract preserved.
- **§164.528 framing is honest in the advisory MD.** Reviewed first 20 lines — explicit "technical-control SLA gap; PHI handling intact; compliance_bundles signing chain intact" language. No "ensures/prevents/guarantees/100%" in the customer-prose sections (one "prevents" in the substrate runbook prose flagged below as Coach P1-C3).
- **[P2-M1] Advisory ID is `OSIRIS-2026-05-12-RECURRENCE-DETECTOR-PARTITIONING` but file references it as `SECURITY_ADVISORY_2026-05-12_RECURRENCE_DETECTOR_PARTITIONING.md`.** No active gate enforces match — sibling advisories also drift between the two forms — but if a future auditor-kit gate hashes `advisory_id` against `advisory_filename`, this will trip. Carry as P2; out of scope for this fix.

### Carol — P0: 0, P1: 1, P2: 1

- **`CREATE INDEX CONCURRENTLY idx_l2_decisions_site_reason_created` is correctly OUTSIDE `BEGIN/COMMIT`** (mig 308 lines 217 `COMMIT;` then line 226 `CREATE INDEX CONCURRENTLY`). Carol Gate A P0 fully satisfied.
- **`l2_escalations_missed.uq_l2_esc_missed_site_type` is `UNIQUE(site_id, incident_type, detection_method)`** — allows future re-runs under a different `detection_method` string (e.g., a 2027 re-audit), exactly the Coach lens question.
- **Substrate invariant `_check_chronic_without_l2_escalation` correctly JOINs through `incidents`** (`assertions.py:5591` `JOIN incidents i ON i.id = ld.incident_id` — `l2_decisions` has no `incident_type` column, Gate A Carol P1 closed). LIMIT 50, runs at 60s cadence, supported by the new composite index. Plan-wise: `is_chronic=TRUE` is selective (existing partial index `idx_recurrence_velocity_chronic`), NOT-EXISTS on `(site_id, escalation_reason, created_at)` hits the new index, JOIN through `incidents.id` uses the primary key. Should be sub-100ms on prod volume.
- **[P1-C1] Backfill INSERT in mig 308 has a semantic oddity but no correctness bug.** Line 194 `GROUP BY v.site_id, v.incident_type, v.resolved_4h`. `incident_recurrence_velocity` has `UNIQUE(site_id, incident_type)` (mig 156 line 33), so `resolved_4h` is functionally dependent — the GROUP BY collapses to one row per `(site, type)` anyway. But the `MIN(computed_at)` + `MAX(computed_at)` aggregates over a single row produce identical timestamps, which is misleading-but-not-wrong. Recommend `DISTINCT ON (v.site_id, v.incident_type)` for clarity, OR drop the aggregate functions entirely. Defer — no behavioral impact; the disclosure JSON ships correct data.
- **[P2-C2] Comment in `evidence_chain.py:4823` says "Table not yet present (mig 307 not applied)" but the actual migration is 308.** Drift between docstring + filename. Fast fix: `mig 307` → `mig 308`. Will not cause a runtime bug but a future operator reading the catch-block could check for mig 307 status, find it applied (the OTS proofs migration), and conclude the table SHOULD exist. Fix before commit.

### Coach — P0: 0, P1: 2, P2: 1

- **Three new substrate invariants present in BOTH `ALL_ASSERTIONS` (lines 2261, 2267, 2273) AND `_DISPLAY_METADATA` (lines 3007, 3025, 3041).** `_populate_display_metadata()` runs at import (`assertions.py:3058`); `test_assertion_metadata_complete` passes. Sibling-parity rule honored.
- **SLA carve-out `LONG_OPEN_BY_DESIGN` includes `l2_recurrence_partitioning_disclosed`** (line 594) — informational sev3 won't fire `substrate_sla_breach` against itself. `recurrence_velocity_stale` is NOT in the carve-out, which is correct: stale-velocity SHOULD eventually resolve so SLA breach against it is meaningful.
- **3 runbooks present** (`chronic_without_l2_escalation.md`, `l2_recurrence_partitioning_disclosed.md`, `recurrence_velocity_stale.md`). `test_substrate_docs_present.py` passes.
- **Function-naming convention `_check_*` honored** for all 3 new check functions.
- **`test_no_appliance_id_partitioned_recurrence_count.py` is in `.githooks/pre-push` SOURCE_LEVEL_TESTS** (line 315) — Coach P1-A from Gate A fully closed at the pre-push lane.
- **[P1-C1] Substrate runbook `l2_recurrence_partitioning_disclosed.md:88` uses the banned word "prevents"** — "forward-looking gate that prevents this class from regressing." CLAUDE.md legal-language rule lists "prevents" as banned. Substrate runbooks ARE operator-facing prose; the rule reads as project-wide, not customer-only. Fast fix: `prevents` → `helps detect / blocks regressions of`. Should land before commit.
- **[P1-C2] Coach P0 from Gate A (writing the runbooks in the same commit) is HONORED, but a future regression class still exists.** None of the new files appear in `test_substrate_runbook_format.py`-style content-shape gates beyond presence. Consider extending the next-sprint task to add section-presence checks (7 canonical headings) for each new runbook. Carry as P1 followup; out of scope this commit.
- **[P2-C3] Banned-shape sweep (Session 219 patterns) clean.**
  - No `f-string subjects` in new code (advisory MD is markdown; not an email send).
  - No `||-INTERVAL` (`INTERVAL '10 minutes'` is canonical Postgres literal).
  - No `jsonb_build_object($N,...)` without casts (the migration `admin_audit_log` insert uses literal strings; no untyped params).
  - No `appliance_id`-partitioned recurrence count (the new CI gate enforces this).
  - No `except Exception: pass` after `conn.execute` (the try/except in `agent_api.py:909` and `evidence_chain.py:4822` both log + re-set defensive defaults).
  - No bare `pool.acquire()` in a `*_loop` introduced.

## Required pre-shipment closures (P0)

**None.** All Gate A P0s + Maya P0-C closed in the implementation.

## Recommended pre-commit fixes (P1, fast)

1. **[Coach P1-C1]** Edit `mcp-server/central-command/backend/substrate_runbooks/l2_recurrence_partitioning_disclosed.md:88` — replace `prevents` with `blocks regressions of` or `helps detect`. Banned-word policy is project-wide per CLAUDE.md.
2. **[Carol P2-C2]** Edit `mcp-server/central-command/backend/evidence_chain.py:4823` — change `mig 307 not applied` to `mig 308 not applied`. Cosmetic but the comment is otherwise misleading to future maintainers.

Both are 1-line edits; not blockers but trivial to land in the same commit.

## Carry-as-followup (P1 / P2)

- **[Steve P1-S1]** Add a 2-line "first-incident-of-pair is intentional fallback to L1" comment at `agent_api.py:837-840` and `:1081-1083`.
- **[Steve P2-S2]** Consider moving `l2_attempted = True` flag to immediately after the `l2_analyze` return at `agent_api.py:868` — semantically tighter.
- **[Carol P1-C1]** Simplify `mig 308` backfill `GROUP BY v.resolved_4h` to `DISTINCT ON (v.site_id, v.incident_type)` in a follow-up migration if/when the table is touched again.
- **[Maya P2-M1]** Sprint-add: advisory-ID-vs-filename CI gate (parallel to sibling advisories drifting between the two forms).
- **[Coach P1-C2]** Extend `test_substrate_docs_present` (or add new) to verify each new runbook contains the canonical 7 sections.

## Commit-body recommendations

Per Session 220 TWO-GATE lock-in, the commit body MUST cite BOTH gate verdicts:

```
fix(routing): switch chronic-pattern detector to (site_id, incident_type)
              and ship parallel L2-escalation-missed disclosure

Closes Session 220 RT-P1 persistence-drift class. Pre-fix the agent_api.py
recurrence detector partitioned counts by appliance_id; multi-daemon sites
(3 daemons at north-valley-branch-2) sliced the count below the >=3-in-4h
threshold and never escalated to L2. 320 missed escalations / 7d.

Gate A verdict: APPROVE-WITH-FIXES — audit/coach-p1-persistence-drift-l2-gate-a-2026-05-12.md
  - Steve P0-A (reopen-branch l2_decision_recorded gate) — closed
  - Steve P0-B (recurrence_velocity_stale sev3 SPOF guard) — closed
  - Maya P0-C (Option B parallel table, NOT backfill) — closed via mig 308
  - Carol P0-D (composite index for substrate invariant) — closed CONCURRENTLY
  - Coach P0 (runbook in same commit) — closed; 3 runbooks ship

Maya P0-C verdict: Option B — audit/maya-p0c-backfill-decision-2026-05-12.md

Gate B verdict: APPROVE-WITH-FIXES — audit/coach-p1-persistence-drift-l2-gate-b-2026-05-12.md
  - 0 P0, 4 P1 (2 fast in-commit fixes + 2 carry-followups), 3 P2
  - Pre-push sweep: 239 passed
  - Pre-commit fixes landed: Coach P1-C1 ('prevents' word) + Carol P2-C2 (mig number comment)

Files:
  - agent_api.py: detector switch + reopen-branch L2 gate + ghost log
  - assertions.py: 3 new substrate invariants + SLA carve-out
  - evidence_chain.py: kit_version 2.1→2.2 + disclosures/missed_l2_escalations.json
  - migrations/308_l2_escalations_missed.sql: INSERT-only disclosure table + CONCURRENTLY index
  - docs/security/SECURITY_ADVISORY_2026-05-12_*.md: advisory
  - substrate_runbooks/*.md: 3 new runbooks
  - tests/test_no_appliance_id_partitioned_recurrence_count.py: CI gate
  - tests/test_l2_escalations_missed_immutable.py: CI gate
  - .githooks/pre-push: SOURCE_LEVEL_TESTS allowlist += 1
```

## Commit ordering

Single commit is acceptable. The fix touches 9 files but they form one
logical unit (detector switch + the supporting disclosure surface + CI gates
forbidding regression). Splitting would create a window where the detector
switch is live but the disclosure table is empty, OR vice versa.

## Author honesty check

This came back APPROVE-WITH-FIXES with 4 P1 findings — not rubber-stamped.
Two P1s are 1-line edits, three are documentation-class carries. No P0s
because the author DID engage with all 5 Gate A P0s + Maya P0-C in the
implementation. If the author lands the 2 fast pre-commit fixes (banned word
+ mig-number comment), the commit can ship.

# Gate A Verdict — P1 Persistence-Drift L2 Routing Fix
Date: 2026-05-12
Reviewer: Gate A fork (Steve / Maya / Carol / Coach)
Scope: 3-part fix — (1) detector switch to `incident_recurrence_velocity`, (2) backfill mig for 320 missed escalations, (3) new substrate invariant `chronic_without_l2_escalation` (sev2).

## Verdict: APPROVE-WITH-FIXES

The design is sound and is the right shape. The bug exists exactly as the brief describes (verified — `agent_api.py:1014-1024` and `:823-833` both filter `WHERE appliance_id = :appliance_id`; velocity loop at `background_tasks.py:1134-1196` groups by `(site_id, incident_type)`). Symmetric to Session 219 mig 300. But **5 P0s** must close before any code lands; **3 P1s** carry as named followups.

## Findings by lens

### Steve — P0: 2, P1: 2, P2: 1

- **[P0] `agent_api.py:823-877` reopen-branch is a latent Session 219 violation; the fix MUST land the `l2_decision_recorded` gate here at the same time.** Compare to the new-incident branch at `:1326-1344` (gate is present) — the reopen branch records L2 then continues regardless of failure. Replacing the count query with a velocity-table SELECT WITHOUT also retrofitting the Session 219 guard ships a fix that immediately violates the substrate invariant `l2_resolution_without_decision_record` for every reopen-path L2 escalation. CI gate `test_l2_resolution_requires_decision_record.py` does NOT cover the reopen path because reopen never SETs `resolution_tier='L2'` directly (it only writes the audit row + decision row, no UPDATE incidents) — but the symmetric "decision-row-without-success-flag" is still a Session 219 design violation. **Required: add the `l2_decision_recorded` guard around the reopen-branch `record_l2_decision()` call.**

- **[P0] Single-point-of-failure when velocity loop misses ticks.** `background_tasks.py:1193-1194` logs a `warning` and continues on exception — the detector then sees `computed_at < NOW() - 10min` and silently falls back to "no recurrence" → L1 path runs unchanged. Today's broken-by-partitioning bug still routes through L1; the proposed fix has the SAME failure mode on loop drift. **Required: log + heartbeat-emit `recurrence_velocity_stale` (sev3) when the detector sees `computed_at > 10min` AND opens a new incident.** Without this, an outage of the velocity loop silently kills L2 escalation across the entire fleet and the dashboard signal that surfaces this drift never fires.

- **[P1] Race when velocity loop is mid-tick.** The velocity loop's INSERT...SELECT...ON CONFLICT runs in a single statement, so within-tick state is consistent. But a concurrent `report_incident` that opens a 4th incident of the same `(site_id, incident_type)` between two velocity ticks reads stale `resolved_4h=2` and routes to L1, while the next tick will flag `is_chronic=TRUE`. Trade-off accepted given the 5-min cadence is fast relative to recurrence cadence. Document as explicit limitation in the recurrence-detection function docstring.

- **[P1] `recurrence_context` JSON shape is consumed downstream at `l2_planner.py` via `l2_details["recurrence"]`.** Keys today: `recurrence_count_4h`, `recurrence_count_7d`, `message`. The velocity table gives `resolved_4h` (int) but NOT `resolved_7d` from the same row (it has `resolved_7d` actually — verified mig 156:21). Preserve exact key names when populating from velocity row; do NOT rename to match the table's column names — downstream LLM prompt template depends on the current keys.

- **[P2] Tests touched.** No existing test exercises the recurrence-detection path; mig-300 sibling test (`test_l2_resolution_requires_decision_record.py`) is source-walk only. A new pytest exercising the velocity-table-read path against a fixture DB is out of scope for Gate A but should be a Gate B carry.

### Maya — P0: 1, P1: 1, P2: 1

- **[P0] Backfill mig requires Maya §164.528 review on retroactive-disclosure-accounting impact, but the brief glosses this.** Mig 300 was approved because the synthetic L2 decision rows materialize an audit fact ("L2 was attempted") that was lost. The proposed 308 backfill is **different in kind**: it claims L2 *should have run* on 320 historical incidents where it actually did NOT. There was no LLM call; the system routed to L1 (correctly applying the symptom fix). Inserting `escalation_reason='recurrence_backfill'` rows asserts retroactively that escalation occurred — auditors reading `v_l2_outcomes` (mig 285) will see 320 new L2 events where none existed. **This is not symmetric to mig 300; it's a different §164.528 question.** Required: explicit Maya sign-off on whether the backfill rows should (a) materialize as `l2_decisions` rows at all, (b) materialize as a different table (e.g., `l2_escalations_missed` synthetic-audit-only), or (c) NOT backfill and instead generate a one-time customer disclosure that the recurrence detector was broken from <date> to 2026-05-12.

- **[P1] `escalation_reason='recurrence_backfill'` vs `escalation_reason='backfill'` precedent.** Mig 300 used `'backfill'`. The proposed `'recurrence_backfill'` diverges. Either: (a) extend mig 300's `'backfill'` convention with a separate `pattern_signature` distinguishing the two waves, or (b) explicitly justify the new value in the migration header. `escalation_reason VARCHAR(50)` has no CHECK constraint (verified mig 156:9), so no schema migration of constraints is needed — but precedent matters.

- **[P2] `ALLOWED_EVENTS` entry in `privileged_access_attestation.py:52`** — NOT required. Mig 300 didn't add one (verified — `'backfill_l2_orphans'` is not in ALLOWED_EVENTS; backfill mig writes to `admin_audit_log` directly, not via the attestation chain). Confirms backfill is operationally observable but not a privileged-chain event. Sibling pattern fine.

### Carol — P0: 1, P1: 2, P2: 1

- **[P0] Substrate invariant query is unscoped — full table scan every 60s.** Proposed: `SELECT * FROM incident_recurrence_velocity v WHERE is_chronic=TRUE AND computed_at > NOW() - 24h AND NOT EXISTS (SELECT 1 FROM l2_decisions ... WHERE created_at > NOW() - 24h)`. The `incident_recurrence_velocity` table has the index `idx_recurrence_velocity_chronic(is_chronic, velocity_per_hour DESC)` (mig 156:36) which is selective on `is_chronic=TRUE` — fine. But the `NOT EXISTS` against `l2_decisions` joining by `(site_id, incident_type)` lacks an index — `l2_decisions` indices are `(incident_id)`, `(pattern_signature)`, `(created_at)`, `(runbook_id)`, `(site_id)`, `(prompt_version)` (verified migs 061+078+171). **There is no composite `(site_id, incident_type, escalation_reason, created_at)` index.** At 232K+ `l2_decisions` rows this NOT-EXISTS subquery seq-scans every 60s. **Required: add `CREATE INDEX CONCURRENTLY idx_l2_decisions_site_type_reason ON l2_decisions(site_id, escalation_reason, created_at DESC) WHERE escalation_reason IN ('recurrence','recurrence_backfill')` in the same migration as the invariant.** Without this the invariant becomes its own performance regression.

- **[P1] `l2_decisions` has no `incident_type` column** — verified via migs 061, 078, 122, 156, 171. The invariant CANNOT join `velocity.(site_id, incident_type)` to `l2_decisions` directly. Must JOIN through `incidents` table: `l2_decisions ld JOIN incidents i ON i.id::text = ld.incident_id WHERE i.site_id = v.site_id AND i.incident_type = v.incident_type`. Brief's "matching `l2_decisions` row with `escalation_reason IN ('recurrence', 'recurrence_backfill')` in the last 24h for the same `(site_id, incident_type)`" is structurally not queryable without the incidents JOIN. **Required: rewrite the invariant SQL to JOIN through `incidents`, and add `created_at` filtering on `incidents` too (constrain the join's row count).**

- **[P1] Backfill mig safety.** The `INSERT INTO l2_decisions SELECT FROM incidents WHERE i.resolution_tier='L1' AND i.incident_type IN ('windows_update','defender_exclusions','rogue_scheduled_tasks') AND NOT EXISTS ...` walks the `incidents` table — partitioned. Verify no `ON CONFLICT` clause (`l2_decisions` has no unique constraint on `incident_id` — verified). Run inside `BEGIN/COMMIT` (mig 300 does). No full-table locks expected since INSERT only acquires row-level locks on inserted rows. Confirm SET LOCAL `lock_timeout` and `statement_timeout` on the migration runner.

- **[P2] PgBouncer prepared-statement implications on the new SELECT.** SQLAlchemy `text()` with `:site_id`/`:incident_type` binds compile to a prepared statement per (session, query). `incident_recurrence_velocity` lookup is keyed against `(site_id, incident_type)` columns that are both `VARCHAR(255)` — no `::text` cast needed (no ambiguous-param risk). Confirms with Session 219 jsonb_build_object rule scope — N/A here.

### Coach — P0: 1, P1: 3, P2: 2

- **[P0] Runbook lockstep gate `test_substrate_docs_present.py` requires `substrate_runbooks/chronic_without_l2_escalation.md` with the 7 canonical sections (`## What this means`, `## Root cause categories`, `## Immediate action`, `## Verification`, `## Escalation`, `## Related runbooks`, `## Change log`).** Brief mentions "canonical 7-section runbook" but does not commit to writing it in the same commit. Session 220 lock-in (per CLAUDE.md): "L1 Phase 1 (`39c31ade`) shipped without `substrate_runbooks/l1_resolution_without_remediation_step.md` — `test_substrate_docs_present` failed at CI." **Required: write the runbook in the same commit as the assertion or pre-push fails deterministically.**

- **[P1] No CI gate forbids the per-`appliance_id` partitioned recurrence count pattern from regressing.** Once the fix lands, a future hand could introduce the same shape elsewhere (e.g., a new `report_drift` variant, a sites.py L1-fallback path). Carry-as-followup: write `tests/test_no_appliance_id_partitioned_recurrence_count.py` that scans `agent_api.py + sites.py + main.py` for the regex shape `SELECT COUNT\(\*\).{0,200}FROM incidents.{0,400}appliance_id\s*=` AND a same-window `incident_type` filter, allow-listing explicit per-appliance use cases.

- **[P1] Banned-shape risk check.** Brief's invariant uses `computed_at > NOW() - INTERVAL '24 hours'`. NOT a banned shape (`NOW() -` in a filter clause is fine; banned is `NOW()` in a partial-index predicate — feedback `feedback_three_outage_classes_2026_05_09.md`). f-string in SQL: none proposed. `jsonb_build_object` unannotated: N/A. ✓

- **[P1] Sprint-sibling drift.** The brief's "match by `(site_id, incident_type)`" mirrors the velocity loop but `l2_decisions` is keyed by `incident_id` (not by `(site_id, incident_type)`). The new invariant introduces a NEW semantic join that no sibling check uses. Maya backfill carve-out (above P0) compounds this: if Maya rules backfill out, the invariant's `IN ('recurrence', 'recurrence_backfill')` clause needs adjustment. Coach insists: **do not finalize the invariant SQL until Maya's P0 closes** — the data shape the invariant looks for depends on Maya's answer.

- **[P2] `recurrence_context` schema lockstep.** The detector's JSON object shape is implicitly contracted with `l2_planner.py` LLM prompt template. Drift here is silent (LLM prompt template still renders, but with `KeyError` if accessed as `.get`-less). Coach carry: add an explicit dataclass / TypedDict for `recurrence_context` to make the contract enforceable. Out of scope for this fix but flag for next sprint.

- **[P2] Opacity / legal-language check.** None of the new code is customer-facing email-class (no opaque-mode parity needed). The `'recurrence'` `'recurrence_backfill'` `escalation_reason` values are internal — auditor-kit ZIPs may expose them via `l2_decisions` rollup; verify auditor-kit field allowlist doesn't surface raw `escalation_reason` to client-facing surfaces.

## Required pre-execution closures (P0)

1. **Steve P0-A** — Add `l2_decision_recorded` gate around the reopen-branch L2 call at `agent_api.py:866-870` in the same commit. Same shape as `:1326-1344`.
2. **Steve P0-B** — Add stale-velocity heartbeat / sev3 invariant emit when detector sees `computed_at > NOW() - INTERVAL '10 minutes'`. Loop-outage detection is mandatory or the fix has worse failure mode than today.
3. **Maya P0** — Outside-counsel-style review of whether the 320-row backfill materializes as `l2_decisions` rows at all. Three options on the table: (a) backfill as proposed with new `escalation_reason='recurrence_backfill'`, (b) backfill into a parallel table that does NOT pollute `v_l2_outcomes`, (c) skip backfill + issue customer disclosure. **Verdict required from Maya before mig number is assigned.**
4. **Carol P0** — Add `CREATE INDEX CONCURRENTLY idx_l2_decisions_site_type_reason` in the same migration as the invariant introduction. Substrate invariants run at 60s cadence; an unindexed NOT-EXISTS at this cadence is a self-inflicted slow-query class.
5. **Coach P0** — `substrate_runbooks/chronic_without_l2_escalation.md` written in the same commit as the assertion. 7 sections. Cite `incident_recurrence_velocity`, `l2_decisions`, `compute_l2_success_rate` (mig 285). Reference mig 300 + Session 219 in Change Log.

## Carry-as-followup (P1)

- **Steve P1-A** — Document the 5-min-cadence race in the recurrence-detection function docstring.
- **Steve P1-B** — Preserve `recurrence_context` JSON key names (`recurrence_count_4h`, `recurrence_count_7d`, `message`) when populating from velocity row — do NOT inherit the table column names (`resolved_4h`, `resolved_7d`).
- **Coach P1-A** — Add `tests/test_no_appliance_id_partitioned_recurrence_count.py` source-walk gate after the fix lands.

## Sweep results

Gate A is a design review — pre-push test sweep is Gate B's job per CLAUDE.md lock-in. **Gate B MUST run `bash .githooks/full-test-sweep.sh` and report pass/fail count** (Session 220 lock-in: diff-scoped Gate B review caused 3 deploy outages in the last 24h). Specifically Gate B must run:
- `tests/test_substrate_docs_present.py` (will fail if runbook missing)
- `tests/test_l2_resolution_requires_decision_record.py` (must pass after reopen-branch retrofit)
- Full source-walk sweep to catch new banned-shape regressions

## Implementation order recommendation

1. **Maya P0 first** — Until Maya rules on backfill shape, the invariant SQL cannot be finalized (it filters on `escalation_reason IN (...)` whose enum depends on her verdict). Block on this.
2. **Coach P0 (runbook)** + **Carol P0 (index migration)** in parallel — both are mechanical and can land independent of Maya's answer.
3. **Steve P0-A (reopen-branch gate)** as a STANDALONE commit before the detector switch — it's a Session 219 violation independent of this fix; landing it separately lets us measure its own pre/post substrate-invariant signal.
4. **Detector switch** (the actual ~15 LOC change at `:1014-1024` + `:823-833`) — only after #1-3 close. Use SQLAlchemy `text()` (the report_incident handler uses `Depends(get_db)` AsyncSession, not asyncpg).
5. **Backfill mig** (number assigned after Maya rules) — last, as the historical-correction step.
6. **Substrate invariant + runbook** registered last, so the first sweep after deploy fires clean (post-backfill, no historical violations to spam the dashboard).
7. **Gate B fork** — mandatory before any commit body says "shipped". Per Session 220 lock-in, Gate B must run the full pre-push sweep AND cite pass/fail counts AND review the IMPLEMENTED diff against THIS Gate A verdict.

## Author honesty check

If this comes back from the parent session as APPROVE-as-is on first read with no engagement on the 5 P0s, suspect the fork was under-scoped and re-run with broader read of `compliance_bundles` audit-chain semantics, the Maya §164.528 retroactive-PDF impact rule (CLAUDE.md mig 306 carry-task #117), and the `v_l2_outcomes` mig 285 contract.

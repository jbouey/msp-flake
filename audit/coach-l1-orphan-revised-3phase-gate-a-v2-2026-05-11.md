# Gate A v2 — L1-orphan 3-phase plan with corrected ground truth (2026-05-11)

**Verdict:** APPROVE-WITH-FIXES

Ground-truth column corrected (v1 P0-1/P0-2/P0-3 closed). Real orphan count
cited. Severity downgrade to sev2 defensible. Three new P0s found in the
implementation details — all fixable inline without redesign. Phase 1 ships
after P0s; Phase 2/3 hold as planned.

---

## P0 findings (must fix before Phase 1 lands)

### P0-1 (Steve) — Backfill mig 305 cites columns that don't exist

Plan: `step_kind='auto_recovered'`, `notes='L1-ORPHAN-BACKFILL-MIG-305'`,
`success=NULL`.

Actual `incident_remediation_steps` schema
(`mcp-server/central-command/backend/migrations/137_remediation_steps_table.sql:6-15`):
```
id SERIAL PK, incident_id UUID, step_idx INT, tier VARCHAR(10),
runbook_id VARCHAR(255), result VARCHAR(100), confidence FLOAT, created_at TIMESTAMPTZ
```

There is NO `step_kind` column, NO `notes` column, NO `success` column. Mig 305
as drafted will fail at parse time. Same bug class as Gate A v1 P0-1 (querying
a column the writer no longer uses). Use existing columns:

```sql
INSERT INTO incident_remediation_steps (incident_id, step_idx, tier, runbook_id, result, confidence, created_at)
SELECT i.id, 0, 'L1', 'L1-ORPHAN-BACKFILL-MIG-305', 'backfill_synthetic', NULL, i.resolved_at
  FROM incidents i
  LEFT JOIN incident_remediation_steps irs ON irs.incident_id = i.id
 WHERE i.status='resolved' AND i.resolution_tier='L1' AND irs.id IS NULL
   AND i.reported_at > NOW() - INTERVAL '90 days'
ON CONFLICT DO NOTHING;
```

`runbook_id` is the auditor-distinguisher (mirrors mig 300 sibling pattern's
`pattern_signature='L2-ORPHAN-BACKFILL-MIG-300'`). `result='backfill_synthetic'`
mirrors mig 300's `llm_model='backfill_synthetic'`.

### P0-2 (Steve) — Mig 151 triggers will block any UPDATE/DELETE on backfilled rows

`mcp-server/central-command/backend/migrations/151_evidence_delete_protection.sql:64-83`
installs `remediation_steps_no_delete` + `remediation_steps_no_update` triggers
that BLOCK all UPDATEs and DELETEs on this table. Once mig 305 lands those
1131+19+43 = ~1193 synthetic rows, they CANNOT be deleted or corrected later
even if Phase 2 root-cause analysis reveals the backfill labelled some
incorrectly. Plan must EITHER:

(a) Phase 2 first, mig 305 after Phase 2 verdict (safer — gives root cause
    a chance to refine the synthetic `result` label); OR
(b) accept that mig 305's `result='backfill_synthetic'` is the
    forever-immutable label and explicitly call this out in the auditor-kit
    README so customers/auditors can filter on it.

Recommend (a) — defer backfill to Phase 2's commit, ship Phase 1 invariant
+ CI gate without backfill. Phase 1's invariant correctly fires sev2 on the
~1131 historical orphans for the first 24h cycle — that's the visibility
signal we want, not noise to silence.

### P0-3 (Coach) — Phase 1 CI gate scope is incomplete

Plan: "AST-walks every Python file under `backend/`." The L2 sibling
(`tests/test_l2_resolution_requires_decision_record.py:37-41`) hardcodes a
3-file allowlist: `agent_api.py`, `sites.py`, `main.py` — NOT a `backend/`
recursive walk. The sibling pattern exists because broad walks hit false
positives in test fixtures + migration files + comment strings.

`grep "resolution_tier = \"L1\"" backend/` confirms only 2 callsites
(`agent_api.py:1105 + 1131`). Use the same 3-file scope as the L2 gate
(includes `main.py` and `sites.py` defensively for future writes).

---

## P1 findings (must fix before close-out)

### P1-1 (Steve) — Window function correctness check

Plan uses `COUNT(*) OVER (PARTITION BY i.site_id) AS site_total_orphans`
inside a query with `LIMIT 50`. Per the Postgres planner, window functions
in the SELECT list run BEFORE the LIMIT clause — so `site_total_orphans`
reflects the full partition count, not the limited surface. Plan's
parenthetical "window runs BEFORE limit, so count is correct" is right.
Add a one-line SQL comment so the next reader doesn't have to re-derive it.

### P1-2 (Maya) — `monitoring_reason` discriminator at 4 callsites

`health_monitor.py:672/694/725` write `resolution_tier='monitoring'` via raw
SQL UPDATE. Plan's `details->>'monitoring_reason'` populated at each callsite
is the right shape, but `incidents.details` is JSONB and not currently set
by these UPDATEs. Phase 3 PR must add `details = COALESCE(details, '{}'::jsonb) || jsonb_build_object('monitoring_reason', $1::text)`
to each UPDATE — and per the Session 219 lesson at the bottom of CLAUDE.md
("`jsonb_build_object($N, ...)` params need explicit casts"), the `::text` cast
is mandatory or production fires `IndeterminateDatatypeError` under PgBouncer.

### P1-3 (Maya) — `substrate_violations` exposure check

Verified: zero references to `substrate_violations` in `client_portal.py`,
`partners.py`, or `auditor_kit_zip_primitives.py`. Customer-facing surfaces
do not leak the table. Operator-only — proceed.

### P1-4 (Carol) — Phase 3 fix vs. Phase 2 root cause

Carol question: "should Phase 3 write `incident_remediation_steps` at the
daemon healing_executor completion point, BEFORE auto-resolve fires?"
Verified: `appliance/internal/daemon/healing_executor.go:644` calls
`d.incidents.ReportHealed(hostname, checkType, tier, runbookID)`. The
relational step write happens in `agent_api.py:1248-1262` on receipt of
that backend POST. The auto-clean path in `sites.py` checkin handler
likely beats the daemon-side ReportHealed callback in a race.

This is exactly the kind of finding Phase 2 is supposed to surface — DO NOT
prejudge it in Phase 3 design. Phase 3 ships AFTER Phase 2 names the
offending callsite(s), per the plan's stated ordering. Plan is correct;
just keep Phase 3's design TBD-pending-Phase-2.

---

## P2 findings (named follow-ups)

### P2-1 (Coach) — Mig 305 rollback path

`mcp-server/central-command/backend/migrations/151_evidence_delete_protection.sql:64-83`
makes `incident_remediation_steps` rows DELETE+UPDATE-blocked. If mig 305
lands wrong rows, recovery requires temporarily disabling the trigger
(`SET LOCAL session_replication_role = 'replica'` or DROP+RECREATE). Mig 305
commit body MUST cite this rollback path so the on-call who has to undo it
isn't paged into a "wait, why can't I DELETE" footgun. TaskCreate as named
followup.

### P2-2 (Coach) — `remediation_history` JSONB column drop

Carried from Gate A v1. Not this sprint.

---

## Per-lens analysis

### Steve
Ground-truth join corrected. Window function semantics correct.
`DISTINCT ON` dedup on `(site_id, COALESCE(dedup_key, id::text))` correct.
Mig 305 schema is wrong (P0-1) and DELETE-blocked rows are forever (P0-2) —
both fixable inline.

### Maya
v1 P1-3 dissolved (branch-1 has zero exposure → no §164.528 retroactive
disclosure obligation, counsel TaskCreate becomes informational-only).
`substrate_violations` confirmed operator-only (P1-3). `monitoring_reason`
discriminator design correct, needs `::text` cast (P1-2).

### Carol
Audit chain integrity preserved — `incident_remediation_steps` is
audit-class via mig 151 triggers but NOT Ed25519-chained (verified — no
`prev_hash`/`compliance_bundles` join in mig 137 or 155). Backfill does
not break a cryptographic chain. Phase 3 design TBD until Phase 2 root
cause lands (P1-4).

### Coach
3-phase ordering matches RT21 cross_org_relocate canonical shape. CI gate
scope should mirror L2 sibling exactly (P0-3), not invent a broader walk.
Mig 305 rollback path needs explicit callout (P2-1). Each phase still gets
its own Gate B verdict file as v1 mandated.

---

## Phase-by-phase verdict

- **Phase 1 (invariant + CI gate, no backfill): SHIP after P0-1 dropped from
  this PR (move backfill to Phase 2 commit) + P0-3 (CI gate scope =
  agent_api/sites/main).** P1-1 SQL comment is cosmetic; ride as same-PR
  cleanup.
- **Phase 2 (diagnostic + mig 305 backfill with corrected schema): HOLD
  pending Phase 1 ship.** Diagnostic doc is the deliverable; backfill mig is
  the secondary artifact; both need Gate B before mig 305 applies. P0-1 +
  P0-2 + P2-1 closed in this phase's commit.
- **Phase 3 (labeling fix, daemon mirror, `monitoring_reason` discriminator):
  HOLD pending Phase 2 root-cause naming the callsite.** P1-2 (`::text`
  cast) closes here.

---

## Recommendation

**APPROVE-WITH-FIXES.** Three P0s, all inline-fixable, no redesign required.

Mandatory before Phase 1 commit:
1. Drop mig 305 from Phase 1 PR; move to Phase 2 (P0-1 + P0-2).
2. CI gate scope = `agent_api.py + sites.py + main.py` (P0-3), not recursive
   walk.

Mandatory before Phase 2 commit:
3. Mig 305 rewritten against real schema (P0-1 corrected SQL above).
4. Mig 305 commit body cites mig 151 rollback path (P2-1).
5. Run Phase 2 diagnostic + Gate B before mig 305 applies.

Mandatory before Phase 3 commit:
6. `monitoring_reason` JSONB write uses `$1::text` cast (P1-2 / CLAUDE.md
   Session 219 lesson).
7. Phase 3 final design re-reviewed at Gate A v3 after Phase 2 identifies
   actual callsite (don't ship a fix before knowing what's broken).

Each phase keeps its own Gate B per v1 P1-5. P1-3 closed (no customer
exposure). P1-4 counsel TaskCreate becomes informational, not blocking.

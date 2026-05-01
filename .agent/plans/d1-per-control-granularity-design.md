# D1 — Per-Control Granularity Schema Design

**Status:** APPROVED FOR 2026-05-22 IMPLEMENTATION
**Date:** 2026-05-01
**Author:** Session 214 score=0 audit closure
**Consistency-coach review:** fork `aa6f50ab17612b673`, APPROVE_WITH_CHANGES (7 mandatory). All 7 applied.
**Round-table review:** fork `afe3ec8f1c4903756`, **SCHEDULE_FOR_LATER (2026-05-22)** + 3 final deltas (below).
**Implementation gate:** apply 3 final deltas at impl time:

1. **Brian (SWE):** add defensive `if not statuses: continue` guard before `_agg()` call in writer.
2. **Diana (DBA):** drop the proposed `idx_efm_status_lookup` index from mig 269 — redundant given existing UNIQUE `(bundle_id, framework, control_id)`. Add only if post-deploy `EXPLAIN ANALYZE` shows the planner doesn't pick the existing UNIQUE for the JOIN.
3. **Steve (SRE):** schedule backfill within 24h of post-mig-deploy + add `data_completeness` field to API response (separate post-deploy follow-up PR).

**Round-table dissent recorded:** Camila APPROVE_AS_DESIGNED; Priya APPROVE_AS_DESIGNED but **PM-blocks-today** on priority grounds (score=0 already restored, D1 is accuracy refinement, queue not empty).

---

## Problem statement

Migration 268 closed the score=0 fleet-wide bug, but the 6-day consistency
coach surfaced a **pre-existing semantic gap exposed by the fix**:

> `cb.check_result` is BUNDLE-LEVEL (any per-host fail → bundle 'fail').
> The function joins to `evidence_framework_mappings` (per-control) and
> assigns the BUNDLE's aggregate to each control via `DISTINCT ON`. So
> a bundle covering 5 controls with 1 actual failure marks all 5 as
> failing in the score.

**Customer impact:** scores **under-report** compliance. A clinic with 8
of 10 actual passing controls reads as 0/10 if any single host failed
any check. From a HIPAA posture this is conservative (better than over-
reporting), but it makes the score floor brittle and operator-confusing.

**On-the-ground scope:** disposition round-table voted IMMEDIATE_FOLLOWUP
(~1h, same-session bundle), but inspection found:
- Schema change required: `evidence_framework_mappings.check_status`
- Writer update required: `evidence_chain.py::map_evidence_to_frameworks`
- Backfill required: 117,222 existing mapping rows need historical
  per-control status derivation

Multi-hour design + multi-pass deploy. Escalated to SCHEDULED with
explicit deviation justification (CLAUDE.md addendum).

---

## Investigation findings

### Writer call site (evidence_chain.py:1607-1630)

The writer iterates `for check in bundle.checks`. Each check entry has:
- `check` / `check_type` — the check name (e.g. `firewall_status`)
- `hostname` — the per-host target
- `status` — per-host result (`pass` / `compliant` / `warning` / `fail` /
  `non_compliant`)
- `hipaa_control` — sometimes present (Windows-side compliance details)

**The writer KNOWS the per-host status at INSERT time.** It just doesn't
write it.

### Mapping logic

`framework_mapper.py::get_controls_for_check_with_hipaa_map(check_type,
enabled_frameworks)` returns a list of `{framework, control_id}` tuples
per check_type. The crosswalk is in YAML at
`mcp-server/central-command/backend/framework_mappings/`.

**A single bundle's checks can map M:N to controls:**
- Multiple checks of the same type on multiple hosts
- One check_type → multiple controls (`firewall_status` covers
  164.312(e)(1) and possibly 164.308(a)(4)(ii)(B))
- Multiple check_types → same control (audit-coverage class)

So per-control status MUST be aggregated across all (host, check) tuples
that map to that control.

### Aggregation rule

Match the writer's existing taxonomy at `evidence_chain.py:1137-1138`:
```python
PASSING = {"pass", "compliant", "warning"}
FAILING = {"fail", "non_compliant"}
```

Per-control aggregation:
- ANY check status in `FAILING` → control is `fail` (worst-case wins,
  HIPAA conservative)
- ELSE ANY check status in `PASSING` → control is `pass`
- ELSE → `unknown`

This is the same rule mig 268's function applies at the bundle level;
moving it down to per-control granularity is the fix.

---

## Proposed design

### Schema change (mig 269)

**Why no append-only-with-trigger pattern (coach #7):** the
`evidence_framework_mappings` table is a DERIVED projection used for
score aggregation, NOT a chain-of-custody audit table. The
source-of-truth chain is `compliance_bundles` (Ed25519 + OTS-anchored).
Re-ingest of a bundle SHOULD overwrite the projection's check_status
(via ON CONFLICT DO UPDATE), so blocking UPDATE/DELETE here would
fight the writer. compliance_bundles is correctly in the
`_rename_site_immutable_tables()` list; this table is correctly NOT.

```sql
ALTER TABLE evidence_framework_mappings
    ADD COLUMN IF NOT EXISTS check_status VARCHAR(20);

-- Index supports the new score function's per-control lookup
CREATE INDEX IF NOT EXISTS idx_efm_status_lookup
    ON evidence_framework_mappings (framework, control_id, check_status)
    WHERE check_status IS NOT NULL;

-- CHECK constraint matches the writer/reader taxonomy
-- Coach #3: DROP+ADD pattern for re-run idempotency (mig 267 sibling)
ALTER TABLE evidence_framework_mappings
    DROP CONSTRAINT IF EXISTS efm_check_status_valid;
ALTER TABLE evidence_framework_mappings
    ADD CONSTRAINT efm_check_status_valid
    CHECK (
        check_status IS NULL
        OR check_status IN ('pass', 'fail', 'unknown')
    );
```

**No backfill in mig 269.** Backfill is a separate Python script
(deferred — function handles NULL gracefully).

**Width validation:** values are 4-7 chars vs VARCHAR(20). The new
`test_check_constraint_fits_column.py` D6 gate validates automatically.

### Writer update (`evidence_chain.py::map_evidence_to_frameworks`)

**Coach #1+#2:** also upgrade `admin_connection(pool)` →
`admin_transaction(pool)` (multi-statement admin path; Session 212
routing-pathology rule). And replace `except Exception: pass` at the
INSERT site with a savepoint-wrapped structured-error logger.

Replace per-check INSERT loop with per-control aggregation:

```python
from .tenant_middleware import admin_transaction  # not admin_connection

PASSING = {"pass", "compliant", "warning"}
FAILING = {"fail", "non_compliant"}

async with admin_transaction(pool) as conn:                    # ← coach #1
    # Build per-control statuses: (framework, control_id) → list[status]
    control_to_statuses: dict[tuple[str, str], list[str]] = {}
    for check in checks:
        check_type = check.get("check") or check.get("check_type")
        status = check.get("status")
        if not check_type or not status:
            continue
        controls = get_controls_for_check_with_hipaa_map(check_type, enabled)
        for ctrl in controls:
            key = (ctrl["framework"], ctrl["control_id"])
            control_to_statuses.setdefault(key, []).append(status)

    # Aggregate per control
    def _agg(statuses: list[str]) -> str:
        if any(s in FAILING for s in statuses):
            return "fail"
        if any(s in PASSING for s in statuses):
            return "pass"
        return "unknown"

    # Per-control INSERT in a savepoint so a single failed row doesn't
    # poison the outer admin_transaction (coach #2 — CLAUDE.md asyncpg
    # savepoint invariant + Block 3 sweep).
    for (framework, control_id), statuses in control_to_statuses.items():
        agg = _agg(statuses)
        try:
            async with conn.transaction():  # nested savepoint
                await conn.execute("""
                    INSERT INTO evidence_framework_mappings
                        (bundle_id, framework, control_id, check_status)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (bundle_id, framework, control_id)
                    DO UPDATE SET check_status = EXCLUDED.check_status
                """, bundle_id, framework, control_id, agg)
        except Exception as e:
            logger.error(
                "evidence_framework_mapping_insert_failed",
                exc_info=True,
                extra={
                    "bundle_id": bundle_id,
                    "framework": framework,
                    "control_id": control_id,
                    "exception_class": type(e).__name__,
                },
            )
```

**Backwards-compat:** ON CONFLICT DO UPDATE means re-ingestion of an
existing bundle rewrites the check_status (rare but safe).

### Function update (mig 269 — same migration)

```sql
CREATE OR REPLACE FUNCTION calculate_compliance_score(
    p_appliance_id VARCHAR, p_framework VARCHAR, p_window_days INTEGER DEFAULT 30
) RETURNS TABLE(...) AS $$
DECLARE
    v_site_id VARCHAR;
BEGIN
    -- Resolve site_id (unchanged from mig 268)
    SELECT site_id INTO v_site_id ...
    -- ...

    RETURN QUERY
    WITH control_status AS (
        SELECT DISTINCT ON (efm.control_id)
            efm.control_id,
            efm.check_status   -- ← per-control, NOT bundle-level
        FROM compliance_bundles cb
        JOIN evidence_framework_mappings efm ON cb.bundle_id = efm.bundle_id
        WHERE cb.site_id = v_site_id
          AND efm.framework = p_framework
          AND cb.created_at >= NOW() - make_interval(days => p_window_days)
          AND efm.check_status IS NOT NULL  -- skip pre-backfill rows
        ORDER BY efm.control_id, cb.created_at DESC
    )
    SELECT
        COUNT(*)::INTEGER AS total_controls,
        COUNT(*) FILTER (WHERE check_status = 'pass')::INTEGER AS passing_controls,
        COUNT(*) FILTER (WHERE check_status = 'fail')::INTEGER AS failing_controls,
        COUNT(*) FILTER (WHERE check_status = 'unknown')::INTEGER AS unknown_controls,
        ROUND(
            COUNT(*) FILTER (WHERE check_status = 'pass')::DECIMAL
            / NULLIF(COUNT(*), 0) * 100,
            2
        ) AS score_percentage
      FROM control_status;
END;
$$ LANGUAGE plpgsql;
```

### Backfill (deferred — separate task post-deploy)

Backfill script `scripts/backfill_efm_check_status.py`:
- Iterates compliance_bundles in chunks of 1000 (by created_at DESC —
  newest first so 30-day window populates fastest)
- For each bundle:
  - **Coach #6: JOIN to existing `evidence_framework_mappings WHERE
    bundle_id = ?` first to find which (framework, control_id) pairs
    ALREADY exist** — do NOT call `get_controls_for_check_with_hipaa_map`
    to discover NEW controls. The historical mapping was written with
    YAML at ingest time; the YAML may have changed. Backfill must
    update existing rows ONLY, not expand the mapping set.
  - Parse `checks` JSONB; group per-host status by check_type.
  - For each existing mapping row, derive the per-control aggregate
    using only the checks whose check_type maps to that control under
    the CURRENT YAML. (Slight risk if a checks→control mapping was
    REMOVED from YAML — that mapping won't get backfilled. Mitigation:
    log a metric for "no current YAML mapping for {check_type}" so
    operator can flag drift.)
- UPDATEs `evidence_framework_mappings SET check_status = ? WHERE id =
  ? AND check_status IS NULL`
- Idempotent (skips populated rows via the `IS NULL` filter)
- Estimated runtime: 117K rows / 1000-row chunks / ~100ms per chunk ≈
  12 minutes

**Pre-backfill behavior:** the function's `WHERE efm.check_status IS NOT
NULL` excludes un-backfilled rows. Sites' scores show only NEW bundles
until backfill catches up. **This means north-valley's score will
TEMPORARILY drop to a smaller-window-only value** until the script runs.

### Test pinning

**Coach #5: 3 additional tests required:**

- `test_per_control_granularity_pg.py` (NEW, DB-gated): insert a
  fixture bundle with 2 hosts × 2 controls (1 host fails 1 check),
  verify per-control aggregation correctly scores the unaffected
  control as passing.
- `test_per_control_lockstep.py` (NEW, source-level): the writer's
  `PASSING` + `FAILING` constants in `evidence_chain.py`, the function's
  `WHERE IN (...)` filter literals in mig 269, and the CHECK
  constraint's IN-list values must use byte-identical taxonomy. AST-
  parse all three; assert sets equal.
- Extend `test_admin_transaction_for_multistatement.py` to include
  `map_evidence_to_frameworks` as a pinned site (post coach #1 swap).
- Backfill script idempotency test: in-process, run twice, assert
  second run UPDATEs zero rows.
- D6 gate (already shipping) validates `efm_check_status_valid` CHECK
  fits VARCHAR(20).

### Forensic disclosure

Once backfill completes, scores will JUMP UP (under-reporting eliminated).
For north-valley currently showing 60% / 100% / 10% (mid-deploy state),
backfilled scores will likely be HIGHER (more controls scored as passing
when only 1 host fails out of N).

**Disclosure-tier rationale (coach #7):**

> Per Session-203 disclosure-first commitment: this is a CALCULATION
> CORRECTION, not a hidden-state reveal. **`compliance_bundles` chain
> integrity is unaffected** (Ed25519 signatures + OTS proofs remain
> valid for every historical bundle); only the DERIVED summary table
> (`compliance_scores`) changes its computation method. **Auditors
> computing compliance from the auditor kit get the correct answer
> at every point in time** — the kit ships per-bundle pass/fail
> evidence, not the dashboard score. The fix corrects an
> under-reporting bias in the dashboard's derived score; the
> evidence chain itself is unchanged.

Below public-advisory threshold. Memory entry + commit-message
disclosure + audit-log JSONB sufficient.

---

## Open design questions for round-table

1. **Aggregation rule for `unknown`**: should `unknown` per-control
   count as failing (HIPAA conservative — incomplete evidence is a gap)
   or as a separate bucket? Current proposal: separate bucket
   (`unknown_controls`), excluded from score numerator.

2. **`compliance` vs `warning` per-host status**: writer treats both
   as passing. Should `warning` per-host roll up into per-control `pass`
   directly, or distinguish (`warning` aggregates to control `warn`)?
   Current proposal: roll into `pass` (matches writer).

3. **Deploy ordering**: ship mig 269 + writer update together, OR
   stage them (mig first, code next deploy)? Coupling them risks the
   mig-264-class regression (CHECK accepts but column doesn't); the
   D6 gate guards against that, but extra caution is warranted given
   we're TWO failed deploys deep this week.

4. **Backfill timing**: post-deploy IMMEDIATE (within hours) or
   SCHEDULED? Pre-backfill scores will be partial (small-window only).
   Customers reading the dashboard during the backfill window see
   degraded data — repeating the score=0 customer-trust burn at
   smaller scale.

---

## Risk register

| Risk | Mitigation |
|---|---|
| Schema change blocks live INSERTs (mig 264 class) | ADD COLUMN with default NULL is non-blocking; index is CONCURRENT-safe |
| Writer regression (broken aggregation) | Test fixture before deploy; check_status=NULL is the safe degraded state |
| ON CONFLICT DO UPDATE may overwrite a correctly-populated row with NULL | Aggregation always produces a value for processed checks; only edge case is empty `checks` array which already returns 0 controls |
| Backfill long-running causes lock contention | Chunk by 1000, sleep between chunks, run off-hours |
| Customer dashboard reads partial data during backfill | (a) backfill backwards-in-time so latest bundles get status first; (b) operational annotation on dashboard during backfill window |
| **(coach #4a) Race writer ↔ backfill** | Writer ON CONFLICT DO UPDATE always writes a value; backfill UPDATEs WHERE check_status IS NULL only. Writer wins atomic — backfill's filter no-ops on the now-populated row. Idempotent. |
| **(coach #4b) YAML crosswalk drift between historical ingest and backfill** | Backfill JOINs `evidence_framework_mappings WHERE bundle_id = ?` FIRST to find which (framework, control_id) pairs ALREADY exist; derives status only for those. Doesn't expand the mapping set under YAML drift. Logs metric for "no current YAML mapping for {check_type}" to surface drift. |
| **(coach #4c) PgBouncer admin_connection→admin_transaction** | Pre-fix writer used `admin_connection(pool)` for a multi-statement INSERT loop (Session 212 routing-pathology class). Coach #1 swap to `admin_transaction(pool)` closes this. Pinned by extended test_admin_transaction_for_multistatement.py. |

---

## Verification post-deploy

1. INSERT a synthetic bundle with mixed pass/fail per check; verify
   `evidence_framework_mappings.check_status` populated correctly per
   control.
2. Manually call `calculate_compliance_score`; verify per-control state
   used (cross-check by deriving expected from bundle JSONB by hand).
3. Pull dashboard for north-valley; verify score reflects per-control
   reality (should be HIGHER than the bundle-level under-reporting).
4. Run backfill script; verify (a) idempotent (re-run is no-op), (b)
   final score = mathematical truth from JSONB derivation.

---

## Round-table experts needed

- **Principal SWE** — code review the writer aggregation
- **Postgres DBA** — review schema + index + CHECK + the function update
- **Compliance/Security** — confirm forensic disclosure tier; sign off
  on the under-reporting → over-reporting boundary preservation
- **SRE** — deploy ordering + backfill window + risk on dashboard UX
- **PM** — ship-now-or-stage decision; backfill timing

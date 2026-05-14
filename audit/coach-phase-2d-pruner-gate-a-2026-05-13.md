# Gate A — Phase 2d `canonical_metric_samples_pruner` daily task

**Task:** #65 — Phase 2d daily pruner for `canonical_metric_samples` (mig 314)
**Date:** 2026-05-13
**Reviewer:** Class-B 7-lens fork (Steve / Maya / Carol / Coach / OCR / PM / Attorney)
**Pseudocode reviewed:** the suggested implementation in the Gate A brief
**Prior artifact:** `audit/coach-canonical-compliance-score-drift-v3-patched-gate-a-2026-05-13.md` (Phase 2a Gate A v4) — P2-E11 (DETACH-then-DROP) + Maya P2-M1 (DETACH CONCURRENTLY for PG14+) explicitly forward-referenced this task.

---

## 200-word executive summary

Phase 2d pseudocode is structurally sound and adopts the correct DETACH-then-DROP order required by Phase 2a Gate A v4 P2-E11. PG 16.11 confirmed on prod via `docker exec mcp-postgres psql -U mcp -d mcp -c "SELECT version()"` — `DETACH PARTITION CONCURRENTLY` is fully supported and Maya P2-M1 deferral is no longer warranted. **Verdict: APPROVE-WITH-FIXES.** Three P0s must close before execute:

1. **P0-S1 sibling-pattern divergence**: pseudocode uses `admin_transaction(pool)` for DDL; existing siblings (`partition_maintainer_loop`, `heartbeat_partition_maintainer_loop`) use `_asyncpg.connect(_migration_db_url())` because the app role lacks `CREATE`/`DROP` on schema public. Pseudocode will fail with `permission denied` on the first DROP. Adopt the superuser pattern verbatim.
2. **P0-M1 lock model**: regular `DETACH PARTITION` blocks the substrate invariant + sampler INSERTs for the duration. PG 16 supports `DETACH PARTITION CONCURRENTLY` — use it.
3. **P0-S2 regex escape**: pseudocode uses `r"^canonical_metric_samples_\d{4}_\d{2}$"` inside a triple-quoted SQL string; raw-string `\d` is fine but reviewer must verify pseudocode is migrated to a Python regex, not a Postgres `~` operator (currently the latter — works in PG, but needs `\d` not `\\d`).

Two P1s + one P2 follow. Effort: ~2h to fix + ship. Lower priority — 3-month headroom + sampler INSERT-only at 10% — but blocking before unattended August cutover (~10 weeks out).

---

## Per-lens verdict

### 1. Steve (Engineering) — APPROVE-WITH-FIXES

**P0-S1 (BLOCKING): Sibling-pattern divergence — superuser-connection mismatch.**
Pseudocode opens via `admin_transaction(pool)`. The pool is `mcp_app` (via PgBouncer). The role:
- Does NOT have `CREATE` on schema public → `CREATE TABLE ... PARTITION OF ...` fails with `permission denied for schema public` (this is the EXACT failure mode that wedged `partition_maintainer_loop` pre-#78, captured in the docstring at line 1601-1606).
- Almost-certainly does NOT have `DROP` on canonical_metric_samples_* child tables.
- `ALTER TABLE ... DETACH PARTITION` requires ownership of the parent (also not held by mcp_app).

**Fix:** model identically on `partition_maintainer_loop` (lines 1591-1644) and `heartbeat_partition_maintainer_loop` (lines 2162-2210):
```python
import asyncpg as _asyncpg
conn = await _asyncpg.connect(_migration_db_url())
try:
    # all DDL here
finally:
    await conn.close()
```
- Single-shot superuser socket per tick.
- `_migration_db_url()` helper already exists at `background_tasks.py:2152`.

**P0-S2 (BLOCKING): regex literal in SQL.**
The pseudocode embeds `'^canonical_metric_samples_\d{4}_\d{2}$'` in a Python triple-quoted string passed to Postgres. In Postgres `~`-regex this matches correctly because `\d` is a POSIX/PCRE shorthand the planner accepts. **But** if the author migrates to a Python `re.match` (which is also in the pseudocode immediately below), they MUST not use the same string verbatim — Python's `re` is fine with `\d`, but a bare string `"\d"` will emit a DeprecationWarning in Python 3.12+. Use `r"..."` raw-string in Python.

**P1-S1: Edge case — year-end forward-look.**
The forward-look creates `pname = f"canonical_metric_samples_{next_year}_{next_month:02d}"`. The `next_month` math is `(today.month % 12) + 1` — correct for January through November, also correct for December (`(12 % 12) + 1 = 1`, with `next_year` incremented). Verified. **However**, the `upper` computation at the bottom (`date(next_year + (1 if next_month == 12 else 0), 1 if next_month == 12 else next_month + 1, 1)`) is hard to read and easy to break. **Recommend** lifting the `(year, month) → date` rollover into a tiny helper `next_month_first(d: date) -> date` used by both the partition-name and the bounds. The proven sibling at `partition_maintainer_loop:1618-1624` already factors this cleanly.

**P1-S2: Forward-look horizon = 1 month only.**
Pseudocode creates ONLY the next month. `partition_maintainer_loop` uses `PARTITION_MAINTAINER_LOOKAHEAD_MONTHS = 3` (line 1587). If the pruner wedges for 30+ days (chain of CI/deploy/key-rotation incidents), a single-month lookahead means INSERTs land in the *default* partition (which mig 314 doesn't even create — there is no `DEFAULT` clause in the migration). **Verify mig 314 has no DEFAULT partition** (confirmed by reading file — none exists). **Fix:** mirror sibling at 3-month lookahead; idempotent `CREATE TABLE IF NOT EXISTS` makes this free.

**P1-S3: Leap years.**
The bounds use `date(year, 2, 1)` style — Python's `date()` constructor doesn't care about leap years for the 1st of the month. February partitions are sized 28 or 29 days correctly because the bounds are `[Feb-1, Mar-1)`. Verified safe.

**P2-S1: Missing partition_maintainer_dry coverage.**
The substrate invariant `partition_maintainer_dry` (sev1, `assertions.py:2227`) lists `(compliance_bundles, portal_access_log, appliance_heartbeats, promoted_rule_events)` as monitored. `canonical_metric_samples` is NOT in the list. **Fix in same commit:** add `canonical_metric_samples` to the invariant's monitored-table list. Else a wedged pruner is silent until INSERTs start failing.

**Pseudocode review:** structurally correct on DETACH-then-DROP order. The `partition_upper < cutoff` check correctly identifies fully-aged partitions (not just partitions whose START is < cutoff — that would over-prune the current 30-day window).

---

### 2. Maya (Database) — APPROVE-WITH-FIXES

**P0-M1 (BLOCKING): Use `DETACH PARTITION CONCURRENTLY`.**
PG version on prod verified: `PostgreSQL 16.11`. Maya P2-M1 from Phase 2a Gate A v4 explicitly deferred CONCURRENTLY pending PG-version check — that's now closed. Regular `DETACH PARTITION` takes `AccessExclusiveLock` on the parent for the duration of the operation, which blocks:
- the `_check_canonical_compliance_score_drift` invariant SELECT (every 60s),
- the `sample_metric_response` INSERTs (10% sample rate × N customer-facing endpoints).

`DETACH PARTITION CONCURRENTLY` takes only `ShareUpdateExclusiveLock` on the parent and `AccessExclusiveLock` on the child (the partition being detached, which is by definition outside the 30-day query window so nobody is reading or writing it).

**Caveats Maya wants in the implementation:**
- `DETACH CONCURRENTLY` cannot run inside a transaction block. Each DETACH must be its own statement on the asyncpg conn (no `BEGIN`/`COMMIT` wrap). Same class as the CREATE INDEX CONCURRENTLY rule called out in `feedback_asyncpg_concurrently_multi_statement.md`.
- If a CONCURRENTLY operation is interrupted, the partition is left in a `DETACHED FINALIZE PENDING` state and must be cleaned up with `ALTER TABLE ... DETACH PARTITION ... FINALIZE` on the next tick. The pruner MUST detect this state (`pg_partitioned_table` + `pg_inherits` won't show it; need `pg_class.relispartition` is false + a `pending` marker). Pseudocode currently does not handle this. **Add a pre-loop sweep:** `SELECT relname FROM pg_class WHERE relname ~ '^canonical_metric_samples_\d{4}_\d{2}$' AND NOT relispartition` → these are detached-but-not-finalized; run `... FINALIZE` for each, then DROP.

**Alternative if CONCURRENTLY adds too much state-machine complexity:** keep regular DETACH but note that the parent-lock window is sub-millisecond for an empty partition (the child has zero rows being read because the substrate's `captured_at > NOW() - 15 minutes` filter excludes everything in a 30-day-old partition). This is acceptable for now and is what `partition_maintainer_loop` implicitly assumes for its CREATE path (no DROPs there). **Maya recommends CONCURRENTLY for future-proofing but does not BLOCK on it** if the implementation includes a comment explaining the sub-ms lock-window rationale.

**P1-M1: Default partition for INSERT-overflow safety.**
Mig 314 has NO `DEFAULT` partition. If both the pruner wedges AND the forward-look-month math is off-by-one, INSERTs that fall outside any defined range will FAIL with `no partition of relation "canonical_metric_samples" found for row`. Per the sampler's soft-fail wrapper (`canonical_metrics_sampler.py:99-107`), this surfaces as a `WARNING` log line — silent customer-facing impact, but invisible without log scraping. **Recommend** adding a `canonical_metric_samples_default DEFAULT` partition in a follow-up migration, OR (better) document the absence and rely on `partition_maintainer_dry` (sev1) to catch the wedge.

**P1-M2: Index propagation.**
Mig 314's two indexes (`idx_canonical_metric_samples_tenant`, `idx_canonical_metric_samples_drift`) are defined on the PARENT — Postgres auto-creates per-partition indexes via PARTITION PROPAGATION when a new partition is attached. The pseudocode's `CREATE TABLE ... PARTITION OF canonical_metric_samples FOR VALUES FROM (...) TO (...)` will inherit these indexes automatically. Verified safe.

**P2-M1: Drop-vs-detach semantics.**
After `DETACH PARTITION`, the child table still exists as a standalone heap. The follow-up `DROP TABLE` operates on a non-partitioned standalone — no parent-lock implications. Correct.

---

### 3. Carol (Security) — APPROVE (N/A scope)

Pruner runs as superuser DDL, no customer-facing surface. The CHECK constraint on `classification` ensures no operator-internal samples can be in the pruned data anyway. No PHI involvement (the samples are score values + endpoint paths + helper input — all canonical-helper inputs, no PHI).

**One nit:** the audit trail. `partition_maintainer_loop` does NOT write to `admin_audit_log` for every CREATE — it logs `partition_maintainer_tick_complete` to slog. For the **DROP** path specifically, Carol recommends an `admin_audit_log` row per dropped partition (`action='canonical_metric_samples_partition_dropped'`, `details={partition_name, row_count, partition_upper}`). Auditor-replayable evidence that the platform pruned customer-impacting data on its operational schedule, not an unscheduled "operator pulled the rug" event. Low priority — informational, not blocking.

---

### 4. Coach — APPROVE-WITH-FIXES

**Sibling-pattern alignment audit:**

| Concern | Pseudocode | `partition_maintainer_loop` | `heartbeat_partition_maintainer_loop` | `client_telemetry_retention_loop` | Verdict |
|---|---|---|---|---|---|
| Connection pattern | `admin_transaction(pool)` ❌ | `_asyncpg.connect(_migration_db_url())` ✅ | `_asyncpg.connect(_migration_db_url())` ✅ | `async_session()` (no DDL) | **DIVERGENT — fix per P0-S1** |
| Startup delay | 3600s | 600s | 120s | 600s | Acceptable but inconsistent; recommend 600s |
| Tick interval | 86400s (24h) | 86400s (24h) | 3600s (1h) | 86400s (24h) | Matches sibling — correct |
| `_hb()` heartbeat | ✅ at top of while | ✅ at top of while | ✅ at top of while | ✅ at top of while | Matches |
| CancelledError handling | ❌ MISSING | ✅ `except asyncio.CancelledError: break` | ✅ same | ✅ same | **MISSING — Coach P0-C1** |
| Exception logging | `logger.exception` (string interp) | `logger.error(..., exc_info=True)` | `logger.error(f"... {e}", exc_info=True)` | `logger.error(..., exc_info=True)` | **DIVERGENT — use `exc_info=True` not `.exception`** |
| Forward-look horizon | 1 month | 3 months (`PARTITION_MAINTAINER_LOOKAHEAD_MONTHS`) | 3 months (`HEARTBEAT_PARTITION_LOOKAHEAD_MONTHS`) | N/A | **DIVERGENT — fix per P1-S2** |
| Audit logging | none | none (slog only) | none | structured slog | Acceptable but Carol nit |

**P0-C1 (BLOCKING): CancelledError must be caught.**
Pseudocode wraps everything in a single `except Exception as e:`. On graceful shutdown, `_supervised(name, fn)` cancels the task; the cancellation propagates as `CancelledError` which IS a subclass of `BaseException` (not `Exception`) in Python 3.8+, so the current `except Exception` is fine BY ACCIDENT — but every sibling explicitly catches `CancelledError` to break the while-loop cleanly. **Fix:** copy the exact handler shape from `partition_maintainer_loop:1637-1643`.

**P1-C1: Constants should be named.**
Pseudocode embeds `30` (retention days), `3600` (startup delay), `86400` (tick), `30` (cutoff timedelta). Siblings extract these to module-level constants (`PARTITION_MAINTAINER_INTERVAL_SECONDS`, etc.). **Fix:** add:
```python
CANONICAL_METRIC_SAMPLES_RETENTION_DAYS = 30
CANONICAL_METRIC_SAMPLES_LOOKAHEAD_MONTHS = 3
CANONICAL_METRIC_SAMPLES_PRUNER_INTERVAL_SECONDS = 86400
CANONICAL_METRIC_SAMPLES_PRUNER_STARTUP_DELAY_SECONDS = 600  # match sibling
```

**Recommendation:** Implementation effort drops if the author starts from `partition_maintainer_loop` (lines 1591-1644) and adds the DROP block. Don't write the loop from scratch.

---

### 5. OCR (Auditor lens) — APPROVE

Spot-checked auditor-kit primitives (`auditor_kit_zip_primitives.py`) — no references to `canonical_metric_samples`. The samples table is operator-internal infrastructure for the substrate's runtime-drift invariant; it is NOT customer-facing evidence, NOT referenced in the auditor kit, NOT a §164.528 artifact.

30-day retention is therefore an operator-internal-only window. It does NOT need disclosure in `_AUDITOR_KIT_README`. It MAY need disclosure in `docs/POSTURE_OVERLAY.md` (Task #51, completed) under the "data classes and retention" section — verify on commit that the overlay's retention table includes `canonical_metric_samples = 30 days` for completeness. Low priority — informational.

**No auditor-kit determinism implications** (the kit doesn't read this table).

---

### 6. PM — APPROVE

**Effort:** ~2h (was 1.5h pre-review) — bumped because of P0-S1 connection-pattern rewrite + P0-M1 CONCURRENTLY-or-rationalize + P0-C1 cancellation handler + sibling-style refactor + the substrate-invariant monitored-table update.

**Priority:** **P2.** Three months of headroom (next forced partition cliff is 2026-08-01 — 80 days out). 10% sample rate × low customer-facing endpoint count = low ingest pressure. No customer-facing impact today. **But:** must close before the 2026-07-01 forward-look horizon — if we ship 3-month lookahead, we have until 2026-04 + 3mo = 2026-07-13 for the loop to first need to create the August partition. **Schedule:** land in next sprint, no later than 2026-06-01.

**Followups to file:**
- Task #65a: backfill `canonical_metric_samples` into the `partition_maintainer_dry` invariant monitored-table list (P0-S2.1 hardening).
- Task #65b: consider adding a `_default` partition to mig 314 (P1-M1, Maya recommendation).
- Task #65c: POSTURE_OVERLAY.md retention table — add `canonical_metric_samples = 30d`.

---

### 7. Attorney (in-house counsel) — APPROVE (N/A scope)

Operator-internal infrastructure. No PHI, no customer-facing surface, no §164.528 disclosure-accounting implications. Aligns with Counsel Rule 1 (canonical-source registry runtime half) by keeping the drift-detection plumbing operational + bounded. The 30-day retention window is well below the §164.530(j) 6-year operational-records retention floor, so this is data hygiene not legal-records destruction.

No Master BAA implications.

---

## Sibling-pattern alignment — vs `partition_maintainer_loop`

The pseudocode is structurally similar but should be made **functionally identical except for the DROP block**. After fixes:

```python
async def canonical_metric_samples_pruner_loop():
    """..."""
    import asyncpg as _asyncpg
    from datetime import date, timedelta
    import re

    await asyncio.sleep(CANONICAL_METRIC_SAMPLES_PRUNER_STARTUP_DELAY_SECONDS)
    while True:
        _hb("canonical_metric_samples_pruner")
        try:
            today = date.today()
            cutoff = today - timedelta(days=CANONICAL_METRIC_SAMPLES_RETENTION_DAYS)
            conn = await _asyncpg.connect(_migration_db_url())
            try:
                # 0. Finalize any prior interrupted DETACH CONCURRENTLY.
                pending = await conn.fetch("""
                    SELECT c.relname FROM pg_class c
                     WHERE c.relname ~ '^canonical_metric_samples_\d{4}_\d{2}$'
                       AND NOT c.relispartition
                """)
                for row in pending:
                    name = row["relname"]
                    try:
                        await conn.execute(
                            f"ALTER TABLE canonical_metric_samples "
                            f"DETACH PARTITION {name} FINALIZE"
                        )
                    except Exception:
                        pass  # may already be finalized

                # 1. DROP partitions older than 30 days.
                old_partitions = await conn.fetch("""
                    SELECT child.relname
                      FROM pg_inherits i
                      JOIN pg_class child ON child.oid = i.inhrelid
                      JOIN pg_class parent ON parent.oid = i.inhparent
                     WHERE parent.relname = 'canonical_metric_samples'
                       AND child.relname ~ '^canonical_metric_samples_\d{4}_\d{2}$'
                """)
                _RE = re.compile(r"^canonical_metric_samples_(\d{4})_(\d{2})$")
                for row in old_partitions:
                    name = row["relname"]
                    m = _RE.match(name)
                    if not m:
                        continue
                    p_year, p_month = int(m.group(1)), int(m.group(2))
                    next_year = p_year + (1 if p_month == 12 else 0)
                    next_month = 1 if p_month == 12 else p_month + 1
                    partition_upper = date(next_year, next_month, 1)
                    if partition_upper < cutoff:
                        # DETACH CONCURRENTLY (PG14+); each is its own statement.
                        await conn.execute(
                            f"ALTER TABLE canonical_metric_samples "
                            f"DETACH PARTITION {name} CONCURRENTLY"
                        )
                        await conn.execute(f"DROP TABLE {name}")
                        logger.info(
                            "canonical_metric_samples_partition_dropped",
                            extra={"partition": name},
                        )
                        # Carol nit: also INSERT into admin_audit_log here.

                # 2. Forward-look: create the next 3 months if missing.
                for offset in range(1, CANONICAL_METRIC_SAMPLES_LOOKAHEAD_MONTHS + 1):
                    year = today.year + ((today.month - 1 + offset) // 12)
                    month = ((today.month - 1 + offset) % 12) + 1
                    start = date(year, month, 1)
                    end_year = year + (1 if month == 12 else 0)
                    end_month = 1 if month == 12 else month + 1
                    end = date(end_year, end_month, 1)
                    pname = f"canonical_metric_samples_{year:04d}_{month:02d}"
                    await conn.execute(
                        f"""
                        CREATE TABLE IF NOT EXISTS {pname}
                        PARTITION OF canonical_metric_samples
                        FOR VALUES FROM ('{start.isoformat()}')
                                    TO ('{end.isoformat()}')
                        """
                    )
            finally:
                await conn.close()
            logger.info("canonical_metric_samples_pruner_tick_complete")
        except asyncio.CancelledError:
            break
        except Exception:
            logger.error(
                "canonical_metric_samples_pruner_loop_failed",
                exc_info=True,
            )
        await asyncio.sleep(CANONICAL_METRIC_SAMPLES_PRUNER_INTERVAL_SECONDS)
```

Diff vs `partition_maintainer_loop`: this loop adds (1) the DETACH-FINALIZE pre-sweep, (2) the DROP block, (3) the regex-based partition discovery. Everything else is verbatim sibling-pattern.

---

## DETACH CONCURRENTLY vs DETACH regular — verdict

**PG version on prod: PostgreSQL 16.11** (verified via `docker exec mcp-postgres psql -U mcp -d mcp -c "SELECT version()"` 2026-05-13).

`DETACH PARTITION CONCURRENTLY` available since PG14, supported on prod. **Use it.**

**Tradeoffs:**

| Property | DETACH regular | DETACH CONCURRENTLY |
|---|---|---|
| Parent lock | AccessExclusiveLock | ShareUpdateExclusiveLock |
| Transaction-block compatible | yes | **no** (each statement standalone) |
| Interrupted-state cleanup | not needed | requires FINALIZE sweep |
| Implementation complexity | low | medium (state-machine) |
| Sub-ms lock-window for empty-by-design partition | ✓ | n/a |

**Recommendation: use CONCURRENTLY** with the FINALIZE pre-sweep. The state-machine complexity is small (10 lines), the operational safety is meaningful (substrate invariant + sampler INSERTs never blocked even if a partition contains residual rows from a clock-skew or backfill scenario). Belt-and-suspenders aligned with the "no PgBouncer holds during DDL" pattern.

**Acceptable alternative:** regular DETACH with an explicit comment: `# 30-day-old partition is empty by design (substrate invariant only scans last 15min, sampler only writes current-month); AccessExclusiveLock window is sub-millisecond.` Maya does NOT block on this — but the CONCURRENTLY path is cheap enough to be the default.

---

## Particular-probes results

1. **Existing `partition_maintainer_loop`**: confirmed at `background_tasks.py:1591-1644`. Sibling pattern documented above.
2. **PG version on prod**: `PostgreSQL 16.11 on x86_64-pc-linux-musl` — `DETACH CONCURRENTLY` supported.
3. **Mig 314 partition shape vs regex**: `canonical_metric_samples_2026_05` / `2026_06` / `2026_07` — regex `^canonical_metric_samples_\d{4}_\d{2}$` matches. Verified.
4. **Sampler INSERTs land in correct partition**: `canonical_metrics_sampler.py:88-98` inserts WITHOUT specifying partition; the parent's range routing handles it. `captured_at` defaults to `NOW()` (column default in mig 314 line 23). As long as the forward-look creates the next month before the cliff, INSERTs route correctly. **No default partition exists** → mig 314 will REJECT inserts outside any range with `no partition of relation found for row`. This is the partition_maintainer_dry (sev1) class — adding `canonical_metric_samples` to that invariant's monitored list (P2-S1) closes the wedge-detection gap.

---

## Final overall verdict — APPROVE-WITH-FIXES

**Blocking P0s (must close before commit):**
- P0-S1: superuser connection pattern (`_migration_db_url()` not `admin_transaction(pool)`)
- P0-M1: prefer `DETACH PARTITION CONCURRENTLY` (PG 16.11 supports) + FINALIZE pre-sweep
- P0-C1: catch `CancelledError` explicitly to break the while-loop
- P0-S2: regex string handling — use raw `r"..."` in Python paths

**P1s (close in same commit or as named followup):**
- P1-S1: factor `next_month_first(d)` helper for clarity
- P1-S2: 3-month forward-look horizon (not 1-month)
- P1-S3: leap year — verified safe, no action
- P1-M1: consider adding default partition to mig 314 (followup migration)
- P1-M2: index propagation — verified safe, no action
- P1-C1: name the constants at module level

**P2s (followup):**
- P2-S1: add `canonical_metric_samples` to `partition_maintainer_dry` invariant monitored-tables list (Task #65a)
- P2-OCR: POSTURE_OVERLAY.md retention table entry (Task #65c)
- P2-Carol: per-DROP `admin_audit_log` row (informational)

**Gate A status: APPROVE-WITH-FIXES — execute Phase 2d as-corrected.**

**Gate B requirement:** the implementation as-shipped must be reviewed by a fresh fork (4-lens minimum) running the full pre-push test sweep, citing CI green + at least one observed tick of `canonical_metric_samples_pruner_tick_complete` in prod slog before claiming task #65 complete. Diff-only review is automatic BLOCK per Session 220 lock-in.

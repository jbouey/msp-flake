# Gate B — Task #75 STEP 7c `pending_deploys` hot-path carve-out (AS-IMPLEMENTED)

**Date:** 2026-05-14
**Gate:** B (pre-completion) — Session 220 two-gate lock-in
**Lenses:** Steve / Maya / Carol / Coach / Auditor-Counsel / PM
**Diff under review:** uncommitted, branch `main`, `mcp-server/central-command/backend/` —
`sites.py` STEP 7c rewrite + new `tests/test_pending_deploys_dedup.py` +
`test_no_raw_discovered_devices_count.py` BASELINE_MAX 6→5 + `.githooks/pre-push` SOURCE_LEVEL_TESTS.
**Gate A reference:** `audit/coach-75-hot-path-carveout-gate-a-2026-05-14.md` (APPROVE-WITH-FIXES, 3 P0s).

---

## SUMMARY (≤250 words)

**VERDICT: APPROVE.** The AS-IMPLEMENTED diff faithfully executes Gate A's prescribed
AFTER SQL. All 3 Gate A P0s verified in the real code: (P0-1) STEP 7c is the query
rewritten, the `UPDATE ... SET device_status='deploying'` at ~5688 is byte-identical;
(P0-2) `local_device_id` projected through `dd.*` in the CTE and out the outer SELECT,
the UPDATE stays keyed on `local_device_id = ANY($2::text[])`; (P0-3) the
`site_credentials` LIKE-JOIN is on `FROM dd_freshest`, outside the CTE. The CTE shape is
byte-identical to the proven prod sibling at `sites.py:7287`. Join condition
`COALESCE(dd.mac_address,'') = cd.mac_dedup_key` exactly matches mig 319's
`mac_dedup_key GENERATED ALWAYS AS (COALESCE(mac_address,''))`. Connection context
`tenant_connection(...)` is unchanged — mig 320's `canonical_devices_tenant_isolation`
(app.current_tenant) policy fires; RLS holds.

**Full pre-push sweep (run by reviewer):** `bash .githooks/full-test-sweep.sh` →
**258 passed, 0 failed, 0 skipped-as-failure** after staging the new test file.

**Re-deploy edge: SOUND.** `DISTINCT ON (cd.canonical_id) ... ORDER BY ... last_seen_at DESC`
picks exactly ONE row per canonical device — the freshest observation. If that row's
`device_status='deploying'`, the outer `WHERE device_status='pending_deploy'` drops it.
A stale older `pending_deploy` row cannot leak through because DISTINCT ON already
discarded it. No re-deploy loop.

**`BASELINE_MAX` 6→5: CORRECT and genuinely earned** — verified by computing the gate
against `HEAD:sites.py` (raw count 5) vs current (4). STEP 7c at HEAD was literally
`FROM discovered_devices dd`; the rewrite to `FROM dd_freshest dd` removed one counted
occurrence.

**Source-shape test: ACCEPTED as honest** — dedup is 100% in SQL; a `_pg.py` behavioral
followup is recommended but NOT a Gate B blocker (P2 followup, named below).

---

## FULL PRE-PUSH SWEEP RESULT

```
$ git add mcp-server/central-command/backend/tests/test_pending_deploys_dedup.py
$ bash .githooks/full-test-sweep.sh
✓ 258 passed, 0 skipped (need backend deps)
```

Diff-only review was NOT done — the full curated source-level sweep was executed by the
reviewer per Session 220 lock-in. The new test file was staged FIRST so
`test_pre_push_ci_parity.py` does not flag it as untracked. Targeted re-run of the two
directly-affected files: `test_pending_deploys_dedup.py` (4) +
`test_no_raw_discovered_devices_count.py` (4) → **8 passed in 0.28s**.

---

## 3-P0 VERIFICATION (AS-IMPLEMENTED, real code)

| P0 | Gate A requirement | Verified in `sites.py` | Result |
|----|--------------------|------------------------|--------|
| **P0-1** | It is the STEP 7c query; the `UPDATE` at ~5688 stays byte-identical | STEP 7c (lines ~5650-5682) is the rewritten query. `UPDATE discovered_devices SET device_status='deploying', agent_deploy_attempted_at=NOW() WHERE site_id=$1 AND local_device_id=ANY($2::text[])` at ~5708 — diff confirms it is UNCHANGED (not in the diff hunk). | ✅ PASS |
| **P0-2** | `local_device_id` projected; UPDATE keyed on it | CTE selects `cd.canonical_id, dd.*` (carries `local_device_id`). Outer SELECT projects `dd.local_device_id` explicitly. `device_ids = [p["device_id"] ...]` → UPDATE `WHERE local_device_id = ANY($2::text[])`. Write-coupling intact. | ✅ PASS |
| **P0-3** | `site_credentials` JOIN on `FROM dd_freshest`, NOT inside CTE | CTE body (`WITH dd_freshest AS (...)`) contains only `canonical_devices cd JOIN discovered_devices dd` — NO `site_credentials`. Outer query: `FROM dd_freshest dd JOIN site_credentials sc ON sc.site_id=$1 AND sc.credential_name LIKE dd.hostname || ' (%'`. JOIN is outside the CTE. | ✅ PASS |

**Join-condition correctness vs mig 319 schema:** `ON dd.site_id=cd.site_id AND dd.ip_address=cd.ip_address AND COALESCE(dd.mac_address,'')=cd.mac_dedup_key`. Mig 319: `mac_dedup_key TEXT GENERATED ALWAYS AS (COALESCE(mac_address,'')) STORED`; unique index `(site_id, ip_address, mac_dedup_key)`. Join keys match the canonical key exactly. ✅

**Divergence from proven sibling (sites.py:7287):** NONE in CTE shape — `WITH dd_freshest AS (SELECT DISTINCT ON (cd.canonical_id) cd.canonical_id, dd.* FROM canonical_devices cd JOIN discovered_devices dd ON dd.site_id=cd.site_id AND dd.ip_address=cd.ip_address AND COALESCE(dd.mac_address,'')=cd.mac_dedup_key WHERE cd.site_id=$1 ORDER BY cd.canonical_id, dd.last_seen_at DESC)` is byte-identical between the two. The only differences are the outer SELECT/JOIN/WHERE — appropriate, the two readers serve different purposes. ✅

---

## RE-DEPLOY EDGE — SOUNDNESS TRACE

Claim under test: a canonical device whose freshest observation is `'deploying'` (older
rows still `'pending_deploy'`) must NOT be re-picked.

1. CTE: `SELECT DISTINCT ON (cd.canonical_id) ... ORDER BY cd.canonical_id, dd.last_seen_at DESC`
   — for each `canonical_id`, Postgres keeps **exactly one** row: the one with the max
   `last_seen_at`. The status of that row is whatever the freshest observation says.
2. If the device is mid-deploy, the freshest observation row has
   `device_status='deploying'`. The older `'pending_deploy'` rows are **discarded by
   DISTINCT ON** — they never reach the outer query.
3. Outer `WHERE device_status='pending_deploy'` then drops the surviving `'deploying'`
   row.
4. Net: device does not re-enter the batch. **No re-deploy loop.** ✅

The inverse worry — could a stale `'pending_deploy'` row leak — is structurally
impossible: DISTINCT ON emits one row per canonical_id, and that row is always the
freshest. The status filter operates on that single row only.

Note one residual semantic (NOT a defect, matches sibling + matches pre-fix intent):
if the freshest observation is e.g. `'active'` but an older row is `'pending_deploy'`,
the device also drops out. That is the correct canonical-device semantic — the device's
current state is its freshest observation. Pre-fix raw-row behavior would have
(incorrectly) re-picked it. The carve-out is strictly more correct here.

---

## PER-LENS VERDICT

### Steve (correctness / SQL) — APPROVE
Rewritten STEP 7c matches Gate A's prescribed AFTER SQL exactly. All 3 P0s verified in
real code (table above). Join condition correct vs mig 319. CTE byte-identical to the
proven sibling at 7287. `$1` is referenced 3× (CTE `cd.site_id=$1`, outer
`sc.site_id=$1`) — all single-type `text` (site_id), no PgBouncer ambiguous-param risk.
`LIMIT 5` now operates on DISTINCT canonical rows — the starvation bug is closed.

### Maya (perf / DB) — APPROVE
Gate A's "no new index needed" holds. The CTE join `canonical_devices cd JOIN
discovered_devices dd` is driven by `WHERE cd.site_id=$1` → uses
`canonical_devices_site_last_seen_idx (site_id, last_seen_at DESC)` or the unique
`(site_id, ip_address, mac_dedup_key)` index for the probe; `discovered_devices` side
joins on `(site_id, ip_address, mac)` which is its existing access path. `DISTINCT ON`
sort is bounded by per-site row count (tens, not millions). Hot path runs once per
appliance checkin — acceptable. No PgBouncer statement-cache concern: single-type `$1`,
stable query text. No new index required.

### Carol (security / RLS) — APPROVE
`async with tenant_connection(pool, site_id=checkin.site_id) as deploy_conn` is
UNCHANGED by the diff (verified: the `async with` line is identical pre/post). This sets
`app.current_tenant` = site_id literal. Mig 320's `canonical_devices_tenant_isolation`
policy (`site_id = current_setting('app.current_tenant', true)`) fires for the new
`canonical_devices` read; `discovered_devices_tenant_isolation` (mig 080) fires for the
joined table. Mig 320 was explicitly authored to unblock exactly this callsite
("UNBLOCKS Task #75 sites.py:5644") and has shipped (task #85 completed). RLS defense-in-
depth intact. No connection-context regression.

### Coach (did the diff MISS anything?) — APPROVE
- **(a) BASELINE_MAX 6→5:** CORRECT and earned. Computed the gate against
  `HEAD:sites.py` → raw count 5; against current → 4. STEP 7c at HEAD was literally
  `FROM discovered_devices dd` (line 5657), counted by the gate. The rewrite to
  `FROM dd_freshest dd` legitimately removes one occurrence. Gate's `glob("*.py")` is
  non-recursive so `scripts/` is excluded — top-level total 6→5. The decrement is
  honest, not a baseline fudge.
- **(b) Source-shape vs behavioral test:** ACCEPTED as an honest call. The dedup logic
  is 100% in the SQL CTE; a mock-the-fetch unit test physically cannot exercise it (it
  would only run the Python row-iteration loop). A true behavioral test of "collapses-
  to-one / limit5-counts-distinct / real-device-not-dropped / credential-join-survives"
  needs real Postgres + multi-appliance fixture rows and belongs in a `_pg.py` file. The
  4 source-shape tests pin the load-bearing structural invariants (CTE present, DISTINCT
  ON, marker, local_device_id flow-through + UPDATE coupling, credential-JOIN-outside,
  status-filter-outside) so a future refactor cannot silently undo them. Runtime
  correctness is additionally covered by the proven sibling at 7287. **This is
  sufficient for Gate B.** Recommended P2 followup (named below) — NOT a blocker.
- **(c) `# canonical-migration:` marker placement:** The marker is on the comment block
  immediately above the `fetch(` call. `test_pending_deploys_dedup.py` asserts it is
  present inside the extracted STEP 7c slice — PASSES. For
  `test_no_raw_discovered_devices_count.py`: that gate greps `FROM discovered_devices`
  and the rewritten query uses `FROM dd_freshest` + `JOIN discovered_devices` (substring
  `JOIN`, not `FROM`), so the gate no longer counts this query AT ALL — the marker is
  technically inert *for that specific gate* (nothing left to credit). This is harmless:
  the marker still correctly documents the migration and satisfies the new dedup test.
  No misplacement, just slight redundancy. **No action needed.**

### Auditor / Counsel — N/A (confirmed)
This is the internal appliance↔Central-Command checkin channel. The query reads
device-inventory + site_credentials for deploy provisioning — no customer-facing metric,
no PHI boundary crossing, no privileged-order path, no email/notification. Gate A's N/A
stands. Counsel Rule 1 is *advanced* by this change (one more reader on the canonical
source), not threatened.

### PM (scope) — APPROVE
Scope is exactly the last Phase 2 Batch 2 reader: the `pending_deploys` query. Diff
touches 4 files — `sites.py` (the reader), the new gate, the ratchet decrement, the
pre-push registration. No scope creep. Task #75 also names a "24h soak" — that is a
post-merge operational step, correctly NOT part of this code diff; it remains an open
item on the task.

---

## RECOMMENDED FOLLOWUP (P2 — NOT a Gate B blocker)

**FU-1 (P2):** Add `tests/test_pending_deploys_dedup_pg.py` — a real-Postgres behavioral
test that seeds a multi-appliance site with one physical `pending_deploy` device
duplicated across 3 `discovered_devices` rows + ≥5 other real `pending_deploy` devices,
and asserts (1) the duplicated device collapses to one batch entry, (2) `LIMIT 5` counts
distinct canonical devices so a real device is not starved, (3) a device whose freshest
observation is `'deploying'` does not re-enter, (4) the `site_credentials` LIKE-JOIN
still resolves credentials post-CTE. This closes the behavioral gap the source-shape
gate intentionally cannot cover. Should be a named TaskCreate item; carry it on Task #74
or as a #75 spin-out. Per Session 220 lock-in, this P2 is carried as a named followup —
it does NOT block marking #75's code complete.

---

## FINAL VERDICT

**APPROVE.**

- Full pre-push sweep: 258 passed / 0 failed (run by reviewer, post-staging).
- All 3 Gate A P0s verified in AS-IMPLEMENTED code.
- Re-deploy edge traced and SOUND.
- `BASELINE_MAX` 6→5 confirmed correct and genuinely earned.
- Source-shape test approach is an honest call given SQL-only dedup; one named P2
  `_pg.py` followup recommended (non-blocking).
- RLS, perf, scope, counsel: all clear.

The commit body must cite BOTH gate verdicts (Gate A
`audit/coach-75-hot-path-carveout-gate-a-2026-05-14.md` +
Gate B `audit/coach-75-hot-path-carveout-gate-b-2026-05-14.md`) and register FU-1 as a
named followup task.

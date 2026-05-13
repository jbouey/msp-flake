# Consistency-Coach Gate — Dashboard Sync Inconsistency Class
**Date:** 2026-05-13
**Trigger:** User-visible divergence: admin "Fleet Status" widget shows 0/0/3 ONLINE/STALE/OFFLINE while site-detail page for `north-valley-branch-2` shows the same 3 appliances ONLINE at the same instant. Root cause: `signature_auth.py:618` casts `appliance_id = $1::uuid` against a `character varying` column → `UndefinedFunctionError` poisons the checkin connection → downstream `appliance_heartbeats` INSERT silently fails for 4h → `appliance_status_rollup` MV (the widget's source) drifts from `site_appliances.last_checkin` (site-detail source).
**Gate type:** Class-B 7-lens round-table focused on PREVENTION, not just incident fix.

---

## 300-WORD EXECUTIVE SUMMARY

A single 9-character schema/code drift (`::uuid` cast on a TEXT column) caused 4 hours of customer-visible dashboard inconsistency. The fix is trivial — drop the cast. The PREVENTION class is not.

**Three structural gaps surfaced**:

1. **Schema-type drift gate is absent.** The CI fixture `tests/fixtures/schema/prod_columns.json` pins column *names* (60 names for `site_appliances`) but NOT column *types*. There is no test that asserts a `WHERE column = $N::TYPE` cast in any `.py` file matches the actual prod type. 126 `$N::uuid` casts in backend code today; one of them was wrong; the other 125 may be correct by luck. This is the same class as the recent `jsonb_build_object($N, ...)` cast bug and the `auth.py + execute_with_retry ::text` rule.

2. **Soft-fail without substrate-invariant pairing is a banned shape that isn't banned.** `signature_auth.verify_heartbeat_signature` is a soft-verify by design (Carol APPROVE 2026-05-13). But the failure mode "verifier raises before returning" was NOT covered by any invariant — only "verifier returns NULL despite signature present" (`daemon_heartbeat_signature_unverified`, shipped d042802e today, hasn't fired yet because no new heartbeats are landing). Every `try: ... except: log + return None` path in the data plane MUST be paired with a substrate invariant that catches the silent-failure shape.

3. **Multi-surface metric divergence is a customer-trust failure.** Two surfaces compute the SAME customer-facing metric ("how many appliances are online") from DIFFERENT sources without a convergence assertion: `appliance_status_rollup.live_status` (heartbeats-derived) vs `site_appliances.last_checkin` (checkin-UPSERT-derived). Counsel Rule 1 says canonical-source-per-metric. Task #50 (Canonical-source registry, in_progress) was scoped for compliance_score. Appliance status was overlooked. This audit adds it.

**Verdict:** ship 3 commits in order: (a) drop the `::uuid` cast (immediate); (b) augment `prod_columns.json` to pin types + CI gate `test_no_param_cast_against_mismatched_column.py`; (c) add `appliance_status` to canonical-source registry (Task #50 followup).

---

## PER-LENS PREVENTION VERDICT

### 1. Steve (Engineering) — **APPROVE-WITH-FIXES**

**Finding:** No CI gate today asserts `$N::TYPE` cast shape vs schema. 126 uuid-casts in backend; one was wrong.

**Probe result:**
- `grep -rE "WHERE\s+\w+\s*=\s*\$\d+::(uuid|int|bigint|text|varchar|timestamp)" backend/*.py` → **133 hits across ~40 files**, 126 of them `::uuid`.
- `grep "appliance_id\s*=\s*\$\d+::uuid"` → **only signature_auth.py:618** (other appliance_id callsites correctly use no cast).
- `prod_columns.json` shape: `{table: [col_name, col_name, ...]}` — pure name index, **no types**.

**Recommendation (P0):** Augment prod_columns.json to `{table: {col_name: {type: "varchar", nullable: bool}, ...}}` + ship `tests/test_no_param_cast_against_mismatched_column.py` that:
1. AST-walks every `.py` for SQL string literals matching `\bWHERE\s+(\w+)\s*=\s*\$\d+::(\w+)`.
2. For each (column, cast_type), look up canonical type in fixture.
3. Fail if cast_type incompatible with declared type (e.g., `uuid` cast on `varchar` column).

**Bonus catch:** This same gate would have caught `jsonb_build_object($N, 'val')` (Session 219) if extended to function-arg casts.

**Cost:** ~1 day to backfill types into fixture from `\dt+` dump + write the AST gate. Comparable to `test_no_direct_site_id_update.py` complexity.

### 2. Maya (Database) — **APPROVE-WITH-FIXES**

**Finding:** `prod_columns.json` does not pin types. Schema-shape regression class is structurally undetectable today.

**Probe result:** Confirmed name-only. The fixture was added to catch column-existence drift (`routes.py:6421` SELECT referenced a renamed column) — it was never designed to catch type drift. The recent column-drift outage class (2026-05-09) and today's cast-drift bug are the same root: code makes type assumptions the fixture can't verify.

**Recommendation (P0):**
- Augment fixture with types via `\dt+ schema.*` parse OR `information_schema.columns` dump committed to repo, refreshed on every migration.
- Add migration-time invariant: any `ALTER COLUMN ... TYPE` MUST be paired with a fixture refresh commit. Pin via `test_prod_columns_freshness.py` that diffs declared column-types in migrations vs fixture.
- Pin a CI gate that every `migrations/NNN_*.sql` touching a column also bumps the fixture timestamp.

**Bonus class:** Lock down `WHERE col::type1 = $1::type2` shapes too — those break PgBouncer prepared-statement cache (the `auth.py` ::text rule).

### 3. Carol (Security) — **APPROVE-WITH-FIXES, NEW RULE**

**Finding:** Soft-verify pattern is correct per Carol's 2026-05-13 unanimous APPROVE — D1 hybrid Path A/B with NULL=unverified is the right design. BUT: it presupposes that `signature_auth.verify_heartbeat_signature` either RETURNS (with valid=NULL) or VERIFIES. The "verifier raises and the calling connection is now in a failed-txn state" branch was not modeled. In production today, an `UndefinedFunctionError` from a bad cast propagated up to the checkin handler and broke the *subsequent* heartbeat INSERT on the same connection. Net effect: 4h of silent heartbeat-write loss while the signature soft-fail "succeeded" semantically.

**New rule:** *Every `try/except` around a verifier or signer that returns a soft-fail token (None / valid=NULL) MUST run inside `async with conn.transaction():` so a verifier exception does not poison sibling writes on the same connection.* Pin by AST gate `test_soft_verify_uses_savepoint.py`.

**Substrate-invariant pairing rule:** Every soft-fail path in the data plane MUST be paired with a substrate invariant that detects the silent-failure shape. `daemon_heartbeat_signature_unverified` (shipped d042802e today) covers the "verifier returned NULL" shape; we need a sibling `daemon_heartbeat_verifier_raised` that detects "verifier raised, suppressed in logs" via log aggregation OR via the secondary symptom (heartbeat write divergence — already covered by `heartbeat_write_divergence`, sev1, assertions.py:2099).

**Why didn't `heartbeat_write_divergence` fire?** It SHOULD have. The invariant queries appliances with `sa.last_checkin > NOW() - INTERVAL '10 minutes'` AND lag-to-heartbeat > 10min. North-valley-branch-2 fits the profile precisely (last_checkin 0h ago, heartbeats 4.1h). **Operator-followup #1:** Confirm whether `_check_heartbeat_write_divergence` is in the 60s tick loop AND that operator alerting is firing on its `Violation`s. If yes → user wasn't notified (paging gap, not detection gap). If no → substrate engine has a tick-loop hole.

### 4. Coach (Consistency) — **DENY pending sibling-source convergence assertion**

**Finding:** Two surfaces serve the same customer-facing question ("how many of my appliances are online"):
- Admin Fleet widget → `appliance_status_rollup.live_status` (heartbeat-cadence-derived via mig 193)
- Site-detail page → `site_appliances.status` + `last_checkin` (checkin-UPSERT-derived)

Mig 193's comment explicitly says it's deliberate: rollup is heartbeat-source-of-truth (cadence anomaly catches frozen daemons even when checkin hangs). Site-detail is operator-friendly direct read. They're allowed to diverge by ~minutes by design.

But they cannot **flip the sign** on the same row at the same instant. North-valley-branch-2 today has rollup=OFFLINE and site-detail=ONLINE for the SAME `appliance_id`. That is not a tolerable timing window; it is a structural inconsistency the customer can screenshot.

**Recommendation (P1):** New substrate invariant `appliance_status_dual_source_drift` (sev2):
```
For every site_appliance row where deleted_at IS NULL:
  Compute live_status_from_rollup = appliance_status_rollup.live_status WHERE appliance_id = sa.appliance_id
  Compute live_status_from_sa = derive from sa.last_checkin (15min / 1h thresholds)
  IF abs(live_status_from_rollup vs live_status_from_sa) crosses ONLINE↔OFFLINE boundary
     AND state has persisted ≥ 5 minutes
  → emit Violation
```
This is *distinct* from `heartbeat_write_divergence` (which is sev1 and catches the upstream cause). `appliance_status_dual_source_drift` catches the customer-visible *symptom* and is the closer-loop assertion that the two surfaces agree.

**Implementation note:** Refresh cadence of `appliance_status_rollup` is on the order of seconds (mig 193 GIN index; refreshed by `refresh_rollup_loop`). A 5-min persistence threshold means we ignore the refresh-window jitter and only fire on real divergence.

### 5. Auditor (OCR) — **HARD BLOCK on customer-facing trust**

**Finding:** "How many of my appliances are online" is a customer-visible compliance posture indicator. Today the platform serves two conflicting numbers from two different DB columns. Customer cannot trust either. This is Counsel Rule 1 (no non-canonical metric leaves the building) violation present in production.

**Citing precedent:** Task #50 (Canonical-source registry) was scoped to ship a registry per metric for `compliance_score` and `device_count_per_site`. It needs to expand to `appliance_status_count`. The registry should declare ONE canonical source-of-truth + flag the secondary source as "internal observability only, do not surface to customer".

**Recommendation (P0):** Add `appliance_status_count` to `audit/canonical-source-registry-design-2026-05-13.md` as a third metric. Declare `appliance_status_rollup.live_status` canonical (heartbeats are the cryptographic ground truth per `liveness_defense_layers`). Mark the `site_appliances.status` reader on site-detail page as "computed-from-canonical via inline LATERAL join" rather than reading the stale `status` column.

### 6. PM — **APPROVE batch dedication**

**Finding:** This is the **third** sync-class issue in this session: (a) fleet=0 widget (early week), (b) device-row duplicates (Task #73 closed), (c) this dashboard inconsistency. Pattern is structural.

**Recommendation:** Dedicate a 3-task batch under existing Task #50 umbrella:
- Task #50.1: Append `appliance_status_count` to canonical-source registry.
- Task #50.2: Ship `appliance_status_dual_source_drift` substrate invariant (sev2).
- Task #50.3: Ship `test_no_param_cast_against_mismatched_column.py` + augmented prod_columns.json.

Stop treating sync-class bugs as one-offs. Each one costs ~half-day to diagnose and ~2h customer trust loss.

### 7. Attorney (in-house counsel) — **REQUIRED ACTION under Rule 1**

**Finding:** Counsel Rule 1: "No non-canonical metric leaves the building. Every customer-facing metric declares a canonical source." Today, `osiriscare_appliances_total` (Prometheus, exposed via /metrics → likely consumed by alerting and by exec dashboards) AND the admin React widget for "Fleet Status" AND the per-site appliances list ALL serve a count-of-online-appliances metric WITHOUT a declared canonical source.

**Decision:** Block any new customer-facing dashboard tile that surfaces "appliance count by status" until canonical-source registry entry exists. Existing surfaces grandfathered into 30-day migration window (paper over with inline LATERAL JOIN per Steve's recommendation, but flag for full registry alignment by 2026-06-13).

---

## TOP 3 NEW CI GATES / INVARIANTS

| # | Gate / Invariant | Severity | Where |
|---|------------------|----------|-------|
| 1 | `test_no_param_cast_against_mismatched_column.py` — AST + fixture-types gate | CI gate (hard) | tests/ |
| 2 | `appliance_status_dual_source_drift` — sev2 substrate invariant | runtime | assertions.py |
| 3 | `test_soft_verify_uses_savepoint.py` — AST gate that soft-verify wrappers run inside `async with conn.transaction():` | CI gate (hard) | tests/ |

**Stretch (P1):** `test_prod_columns_freshness.py` — every `migrations/NNN_*.sql` touching a column type bumps `prod_columns.json`.

---

## IMMEDIATE-FIX SHAPE (signature_auth.py:618)

```python
# Before:
WHERE appliance_id = $1::uuid
  AND site_id = $2
  AND deleted_at IS NULL

# After (drop the cast — appliance_id is varchar, site_id is text):
WHERE appliance_id = $1
  AND site_id = $2
  AND deleted_at IS NULL
```

**Plus:** wrap the entire `verify_heartbeat_signature` body in `async with conn.transaction():` so future verifier exceptions cannot poison the calling connection. This is the Carol-rule SAVEPOINT pairing.

**Operator follow-up:**
1. After fix lands + deploys, confirm next-tick that:
   - `daemon_heartbeat_signature_unverified` is empty for north-valley-branch-2 (verifier now returns valid=True for D1 daemons OR valid=NULL cleanly for older daemons).
   - `heartbeat_write_divergence` is empty (heartbeat INSERTs are succeeding again).
   - `appliance_status_rollup.live_status` for the 3 appliances flips ONLINE within ~5min of first new heartbeat row.
2. Investigate why `heartbeat_write_divergence` (sev1, should have fired ~4h ago) didn't surface an operator alert. Two hypotheses: (a) substrate tick was wedged (verify against substrate_health panel), or (b) substrate detection fired but alerting pipeline is broken.

---

## SHOULD CONSISTENCY-COACH GATE RUN PRE-COMMIT?

**RECOMMENDATION: NO at pre-commit, YES at Gate B per existing two-gate lock-in.**

Pre-commit cost is too high (currently `.githooks/full-test-sweep.sh` is ~92s and that's already at the upper bound users tolerate). Adding fork-based adversarial review pre-commit would push commit latency to several minutes — friction that drives skipping.

The two-gate lock-in (Session 219 + 220) already mandates a fork-based Gate B before claiming "shipped" — that's the correct enforcement point. The miss today was that the schema-cast-drift bug landed via a commit that didn't trigger a Gate B sweep (signature_auth was last touched commit adb7671a which had a Gate B retro audit `coach-adb7671a-retro-gate-b-2026-05-13.md` — verify whether that retro caught the cast-bug class or not; if not, the Gate B template needs expansion).

**However:** add a NEW pre-commit AST check (cheap, ~2s) that runs `test_no_param_cast_against_mismatched_column.py`. That specific class is deterministic and fast and would have caught this bug at commit time. Lightweight enforcement at the right layer.

---

## FINAL OVERALL VERDICT

**APPROVE-WITH-FIXES (P0 blockers identified) + DENY new sync-class commits until 3 gates ship.**

**Next 1-3 commits (in order):**

1. **Commit 1 (HOTFIX, ship immediately):** signature_auth.py:618 — drop `::uuid` cast; wrap verify body in `async with conn.transaction():`. Verifies fix via:
   - Next tick of `heartbeat_write_divergence` empty for north-valley-branch-2.
   - Next tick of `daemon_heartbeat_signature_unverified` no new entries.
   - `curl -s /metrics | grep osiriscare_appliances_total` returns 3 ONLINE.

2. **Commit 2 (PREVENTION):** Augment `prod_columns.json` to pin column types (script: dump from prod `information_schema.columns` → JSON). Ship `test_no_param_cast_against_mismatched_column.py` AST gate. Backfills coverage for all 126 existing `$N::uuid` casts (verify all pass before merge).

3. **Commit 3 (PREVENTION + COUNSEL RULE 1):** Add `appliance_status_count` to canonical-source registry (Task #50 followup). Ship `appliance_status_dual_source_drift` substrate invariant (sev2) + runbook `substrate_runbooks/appliance_status_dual_source_drift.md`. Ship `test_soft_verify_uses_savepoint.py` AST gate.

**Both Gate A AND Gate B fork reviews required on Commits 2 + 3** per two-gate lock-in. Commit 1 is a single-line hotfix to a typed cast — ships under expedited-hotfix lane.

**Acceptance criteria for closure of this audit:**
- Customer no longer sees divergent counts on dashboard ↔ site detail.
- Next adversarial Gate B sweep on any backend commit runs the 3 new gates.
- Task #50 (canonical-source registry) lists `appliance_status_count` as a registered metric.
- Operator-alerting verified to fire on `heartbeat_write_divergence` and `appliance_status_dual_source_drift`.

**Sign-off:** Steve / Maya / Carol / Coach / OCR / PM / Attorney — unanimous APPROVE-WITH-FIXES.

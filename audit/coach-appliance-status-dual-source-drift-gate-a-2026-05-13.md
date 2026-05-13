# Gate A — Task #78 `appliance_status_dual_source_drift` (sev2 substrate invariant)

**Date:** 2026-05-13
**Reviewer:** Class-B 7-lens fork (Steve / Maya / Carol / Coach / OCR / PM / Counsel)
**Verdict (top):** **BLOCK NEW INVARIANT — EXTEND existing `heartbeat_write_divergence` instead. Plus fix #80 (existing didn't fire today) BEFORE shipping the extension. Also: today's outage class is a THIRD-source bug (sites.py:calculate_live_status @ 15-min boundary), not the rollup-vs-checkin divergence the new invariant targets — adding a 4th source-of-truth is the wrong move.**

---

## 250-word summary

The proposed `appliance_status_dual_source_drift` (sev2) catches "rollup says offline, checkin says fresh" — i.e. heartbeats are stale but `last_checkin` is recent. That is **exactly** the existing sev1 `heartbeat_write_divergence` (assertions.py:1789), which queries the same physical condition: `site_appliances.last_checkin > NOW() - 10min` AND `appliance_heartbeats.MAX(observed_at)` either NULL or lagging `last_checkin` by >10min. The proposed invariant only differs in (a) reading the rollup MV instead of raw heartbeats — strictly worse, since the MV is up to 60s stale — and (b) threshold (5min vs 10min).

The existing invariant is the right shape. The right move is to **extend** it: tighten threshold to 5 min, add `OR` direction (rollup-offline-but-checkin-fresh as detected via heartbeats), register the metric class in `canonical_metrics.PLANNED_METRICS["appliance_liveness"]`, and add operator runbook copy that names today's 3 sources. Adding a 4th invariant against the MV would itself be a Rule 1 violation — we'd be declaring the MV as a separate canonical source while the MV is supposed to derive from heartbeats.

**Crucially:** Task #80 says `heartbeat_write_divergence` did **not fire** during today's 4h+ dashboard outage. **That is the bug.** Until we diagnose why the existing sev1 invariant was silent, extending it without that diagnosis is gold-plating a broken alarm. Today's outage was likely the sites.py `calculate_live_status` 15-min boundary (third source — pure Python on `last_checkin`) disagreeing with the rollup MV's 90s/5min boundaries — neither of which the proposed invariant would catch.

---

## Per-lens verdicts

### 1. Engineering (Steve) — **BLOCK NEW; APPROVE EXTEND**

Read `assertions.py:1789-1835`. The existing `_check_heartbeat_write_divergence` query:

```sql
SELECT sa.site_id, sa.appliance_id, sa.last_checkin,
       (SELECT MAX(observed_at) FROM appliance_heartbeats
         WHERE appliance_id = sa.appliance_id) AS last_heartbeat
  FROM site_appliances sa
 WHERE sa.deleted_at IS NULL
   AND sa.status = 'online'
   AND sa.last_checkin > NOW() - INTERVAL '10 minutes'
```
Then in Python: `if last_heartbeat is None or (last_checkin - last_heartbeat) > 600s: fire`.

The proposed shape:
- rollup MV `live_status='offline'` ↔ heartbeats stale (>5min per mig 193 CASE)
- AND `site_appliances.last_checkin > NOW() - 5min`

These describe the SAME physical state. The proposed only "improvements" are:
- Read MV instead of `appliance_heartbeats` directly → **strictly worse** (60s rollup refresh staleness)
- Threshold 5min vs 10min → legitimate, but trivially achievable in existing invariant
- sev2 vs sev1 → **regression** (today's outage was customer-visible, sev1 was right)

**Counsel Rule 5 by analogy** ("no stale invariant as authority"): if the existing invariant is correct in shape but mis-tuned in threshold, **fix it**, don't fork it. Two invariants checking the same physical fact at different thresholds = the operator has to figure out which is canonical when both fire.

**However:** the existing invariant queries `appliance_heartbeats` directly and bypasses the rollup MV entirely. That's correct for raw-physical-fact detection but it does NOT close the loop on **operators reading the dashboard MV vs operators reading the site-detail page** — the divergence the user actually experienced today. That is a different class of bug (UI consumes different sources) and the right invariant for it is:

> "Has any caller-visible source (rollup MV `live_status`, sites.py `calculate_live_status`, raw `site_appliances.status`) drifted from heartbeats?"

The proposed `appliance_status_dual_source_drift` partially captures that, but the proposed shape misses the **third** source: `sites.py:753 calculate_live_status()` — a pure-Python function with thresholds (15-min online, 1-hr offline) that DO NOT match the MV (90s online, 5min offline). That mismatch is by design but is the most likely explanation of today's "dashboard says offline, site detail says online" customer experience. Neither the existing invariant nor the proposed one catches `calculate_live_status` vs `live_status` divergence directly.

**Steve verdict:** EXTEND existing invariant. Threshold 5 min (matches MV). ALSO open follow-up Task #X to either (a) delete `calculate_live_status()` and have sites.py read `appliance_status_rollup.live_status`, or (b) align thresholds across the three sources. The invariant alone won't close the bug class — source consolidation is the real fix.

### 2. Database (Maya) — **BLOCK (rollup-read is wrong)**

The substrate engine runs every 60s (assertions loop). The `heartbeat_rollup_loop` ALSO runs every 60s. They are independent — assertions does NOT refresh the MV before reading. Worst case: assertion tick fires immediately after rollup tick miss → rollup is up to 120s stale by the time the assertion reads it.

For a 5-min threshold invariant, 120s of read-staleness is ~40% slop. **Do not read the MV.** Read `appliance_heartbeats.MAX(observed_at)` directly — that is what the existing `heartbeat_write_divergence` already does correctly.

Mig 193 (which I co-authored, Session 206 H5) deliberately makes the rollup compute `liveness_drift_seconds`. The rollup itself surfaces drift. But substrate invariants must read the source-of-truth, not the cache of the source-of-truth.

**Maya verdict:** Reading `appliance_status_rollup` in a sev2 invariant is a category mistake. The MV is a customer-display optimization, not an assertion input. EXTEND existing invariant; keep its direct read against `appliance_heartbeats`. If we need a 5-min threshold, just lower the constant in the existing query.

### 3. Security (Carol) — N/A

No security surface. Skipping per task brief.

### 4. Coach — **EXTEND; pattern parity confirms duplicate**

Sibling shape: `discovered_devices_freshness`, `canonical_devices_freshness`. Both read the source-of-truth table directly (NOT a rollup MV), filter on a freshness window, return violations per appliance. `heartbeat_write_divergence` follows the same shape.

The proposed `appliance_status_dual_source_drift` would be the FIRST substrate invariant to read a materialized view. That is a pattern break.

**Trichotomy check:** `daemon_heartbeat_unsigned` / `_signature_invalid` / `_signature_unverified` is a trichotomy over `(agent_signature, signature_valid)` for the SAME read (heartbeats). The proposed invariant is NOT a 4th member of that trichotomy — it's checking a different axis (checkin vs heartbeat consistency, not signature state). No relationship.

The right cluster mate IS `heartbeat_write_divergence`. They're the same invariant.

**Coach verdict:** EXTEND. New invariant violates sibling-pattern parity (MV read) and duplicates the existing one's intent.

### 5. Auditor / Canonical-metric (OCR) — **EXTEND + register in PLANNED_METRICS**

`canonical_metrics.py:198 PLANNED_METRICS["appliance_liveness"]` already exists and is **blocked on Task #40 (D1 daemon-side Ed25519 heartbeat signing)**:

```python
"appliance_liveness": {
    "canonical_helper_pending": (
        "needs gate on recent heartbeat AND D1 signature_valid "
        "once Task #40 ships"
    ),
    "blocks_until": "Task #40 D1 backend-verify completes",
},
```

Counsel Rule 1 says any customer-facing "appliances online" metric must declare a canonical source. The dashboard fleet-status widget currently reads the rollup MV; the site detail page reads `calculate_live_status()`. Neither is registered as canonical. **The metric class IS already on file as PLANNED.**

The right action: **don't add a new invariant. Add a CI ratchet against any new reader of `appliance_status_rollup.live_status` OR `calculate_live_status()` until the canonical helper ships** (the same shape we used for `compute_compliance_score`). The invariant catches drift; the CI gate prevents new drift. The substrate invariant should be the existing `heartbeat_write_divergence` with the canonical-metric-class annotation added to its description.

**OCR verdict:** EXTEND existing invariant. ALSO update `PLANNED_METRICS["appliance_liveness"]` description to name the 3 current sources and the assertion that catches the drift between them. ALSO consider promoting Task #40 priority — `appliance_liveness` is blocked behind it and today's outage was the cost.

### 6. PM — **EXTEND saves 1h + closes Task #80 in same patch**

Effort estimates:
- NEW invariant: ~2.5h (new check fn + registration + runbook + recommended-action + test). Plus the operator gets two alarms for one physical fact.
- EXTEND existing: ~1.5h (lower threshold, add OR direction, refresh runbook, add canonical-metric annotation). Fits in same patch as Task #80 diagnosis (operator-followup: existing didn't fire).
- THIRD source fix (consolidate `calculate_live_status` → rollup read): ~3-4h, separate Gate A required.

PM verdict: **EXTEND** in one patch, atomic with Task #80 diagnosis. Open a separate Task # for the third-source consolidation. Total this sprint: 1.5h + the Task #80 RCA time, vs 2.5h new + Task #80 still open.

### 7. Attorney (in-house counsel) — **EXTEND; new invariant would violate Rule 1**

Counsel Rule 1: "No non-canonical metric leaves the building." Adding a second substrate invariant that checks the same physical fact (checkin/heartbeat divergence) at a different threshold creates **TWO non-overlapping operator-visible signals for one metric class**. Operators will reasonably ask: which fires first? which is authoritative? Answer must be: there is ONE, with ONE threshold, registered to the canonical metric class.

A new invariant ALSO implicitly elevates `appliance_status_rollup` to a co-equal canonical source alongside `appliance_heartbeats`. The MV is **derived**; it is by definition not canonical. Counsel-grade audit would flag this.

**Counsel Rule 1 framing for the EXTENDED invariant's runbook (and PLANNED_METRICS["appliance_liveness"] description):**

> Three surfaces currently read appliance liveness state:
> (1) `appliance_status_rollup.live_status` (heartbeats-derived, 60s refresh, used by admin Fleet Status widget + public status page);
> (2) `sites.py:calculate_live_status()` (pure-Python on `site_appliances.last_checkin` with 15-min/1-hour thresholds, used by site detail + list views);
> (3) `site_appliances.status` column (set by checkin handler UPSERT, used by `_check_offline_appliance_long`).
> These three are not threshold-aligned and CAN disagree by design during a normal 5-15min window. The canonical helper (Task #40 D1 dependency, Task #50 registry) is not yet shipped — until then `appliance_status_rollup.live_status` is the **preferred** display source. The substrate invariant `heartbeat_write_divergence` is the runtime gate that catches the silent-divergence class (today's outage). Source consolidation (collapse to 1 helper) is the structural fix.

**Counsel verdict:** EXTEND. Adding a new invariant in parallel with the existing one is itself a Rule 1 violation in miniature. Source consolidation is overdue and the next Gate A target.

---

## Recommendation: **EXTEND** `heartbeat_write_divergence`

### SQL change to `assertions.py:1789` (within `_check_heartbeat_write_divergence`)

Lower the WHERE clause threshold from 10 min to 5 min so it fires within one full assertion tick of the rollup-MV's 5-min offline boundary:

```python
async def _check_heartbeat_write_divergence(conn: asyncpg.Connection) -> List[Violation]:
    """site_appliances.last_checkin is maintained by the UPSERT in the
    checkin handler. appliance_heartbeats is maintained by a SEPARATE
    INSERT wrapped in a savepoint. If the INSERT silently fails (bad
    partition, schema drift, constraint), last_checkin stays fresh
    but heartbeat history stops — every downstream consumer that
    reads heartbeats (rollup MV, SLA, cadence anomaly, dashboard
    Fleet Status widget) quietly drifts.

    Threshold tightened from 10min → 5min (Task #78, 2026-05-13)
    to match the rollup-MV 'offline' boundary defined in mig 193
    (heartbeats > 5min stale ⇒ live_status='offline'). Catches the
    customer-visible divergence class where the admin Fleet Status
    widget (reads rollup) flips offline while site detail (reads
    calculate_live_status with 15-min threshold) stays online.

    Three sources currently observe appliance liveness; see
    canonical_metrics.PLANNED_METRICS['appliance_liveness'] for
    consolidation roadmap (blocked on Task #40 D1)."""
    rows = await conn.fetch(
        """
        SELECT
            sa.site_id,
            sa.appliance_id,
            sa.hostname,
            sa.last_checkin,
            (SELECT MAX(observed_at)
               FROM appliance_heartbeats
              WHERE appliance_id = sa.appliance_id) AS last_heartbeat
          FROM site_appliances sa
         WHERE sa.deleted_at IS NULL
           AND sa.status = 'online'
           AND sa.last_checkin > NOW() - INTERVAL '5 minutes'
        """
    )
    out: List[Violation] = []
    for r in rows:
        lc = r["last_checkin"]
        lh = r["last_heartbeat"]
        # No heartbeat ever, OR last_checkin > 5 min ahead of last_heartbeat
        if lh is None or (lc - lh).total_seconds() > 300:
            out.append(
                Violation(
                    site_id=r["site_id"],
                    details={
                        "appliance_id": r["appliance_id"],
                        "hostname": r["hostname"],
                        "last_checkin": lc.isoformat() if lc else None,
                        "last_heartbeat": lh.isoformat() if lh else None,
                        "lag_s": (lc - lh).total_seconds() if lh else None,
                        "canonical_metric_class": "appliance_liveness",  # PLANNED
                        "interpretation": "checkin UPSERT is succeeding but "
                        "heartbeat INSERT is failing (likely missing monthly "
                        "partition, schema drift, or constraint violation). "
                        "Customer-visible effect: rollup MV flips appliance "
                        "to offline (mig 193 5-min threshold) while site "
                        "detail page (calculate_live_status 15-min threshold) "
                        "still shows online. Check mcp-server logs for "
                        "'heartbeat insert failed'.",
                    },
                )
            )
    return out
```

### Registration change (assertions.py:2099-2103)

Keep `severity="sev1"` (matches today's customer-visible outage class). Refresh description:

```python
Assertion(
    name="heartbeat_write_divergence",
    severity="sev1",
    description="site_appliances.last_checkin fresh (<5min) but "
    "appliance_heartbeats lags 5+ min behind OR is NULL. Checkin "
    "UPSERT succeeding but heartbeat INSERT savepoint is being "
    "silently caught — every downstream consumer (rollup MV, SLA, "
    "cadence anomaly detector, admin Fleet Status widget) is "
    "drifting from site detail page. Counsel Rule 1 + Rule 4 "
    "intersection: 3 reader-surfaces exist for 'appliance liveness' "
    "(rollup MV, sites.py:calculate_live_status, "
    "site_appliances.status); none is canonical yet (blocked on "
    "Task #40 + Task #50). This invariant is the runtime gate "
    "against the customer-visible 'dashboard says offline / site "
    "detail says online' divergence class — 4h+ outage 2026-05-13.",
    check=_check_heartbeat_write_divergence,
),
```

### Recommended-action runbook refresh (assertions.py:2644)

```python
"heartbeat_write_divergence": {
    "display_name": "Heartbeat INSERT failing silently (Fleet Status / site detail divergence)",
    "recommended_action": "Grep mcp-server logs for 'heartbeat insert failed' "
        "OR 'partition not found' on appliance_heartbeats. Usually caused by a "
        "missing monthly partition on appliance_heartbeats — run the "
        "partition-creation migration or extend the monthly cron. Customer-visible "
        "effect: admin Fleet Status widget (reads appliance_status_rollup MV, "
        "mig 193, 5-min offline boundary) shows the appliance offline; site "
        "detail page (reads calculate_live_status, 15-min online window) "
        "shows it online; customer reports 'dashboard is broken'. Until fixed, "
        "cadence anomaly + uptime SLA metrics are unreliable. Three liveness "
        "sources exist today: rollup MV, calculate_live_status, "
        "site_appliances.status — consolidation tracked at "
        "canonical_metrics.PLANNED_METRICS['appliance_liveness'] (blocks on "
        "Task #40 D1 + Task #50 registry).",
},
```

### PLANNED_METRICS["appliance_liveness"] update (canonical_metrics.py:198)

Extend the description to name the three sources + the gate:

```python
"appliance_liveness": {
    # Counsel Rule 4 + Rule 1 intersection; multi-device-enterprise scale.
    # Three reader surfaces today (NOT canonical until Task #40 + #50 ship):
    #   (1) appliance_status_rollup.live_status — MV, 60s refresh, mig 193
    #       (90s online / 5min offline boundaries); used by admin Fleet
    #       Status widget + /api/public/status/{slug}.
    #   (2) sites.py:calculate_live_status() — pure Python on last_checkin
    #       (15min online / 1h offline boundaries); used by site list +
    #       site detail.
    #   (3) site_appliances.status — column written by checkin handler
    #       UPSERT; used by _check_offline_appliance_long + legacy callers.
    # Runtime gate against silent divergence: substrate invariant
    # `heartbeat_write_divergence` (sev1, 5-min threshold per Task #78).
    # 2026-05-13 outage: dashboard widget flipped offline while site detail
    # stayed online for 4h+ before operator caught it.
    "canonical_helper_pending": (
        "needs gate on recent heartbeat AND D1 signature_valid "
        "once Task #40 ships; consolidates 3 reader surfaces"
    ),
    "blocks_until": "Task #40 D1 backend-verify + Task #50 registry",
    "runtime_gate": "heartbeat_write_divergence (sev1)",
},
```

### Test ratchet (new)

`tests/test_appliance_liveness_no_new_readers.py` — ratchet on the count of files that import `calculate_live_status` OR read `appliance_status_rollup.live_status` OR read `site_appliances.status`. Baseline today; drive to 1 (canonical helper) once Task #40+#50 ship.

---

## NOT in scope (separate tasks)

1. **Task #80 RCA** — why did `heartbeat_write_divergence` not fire during today's outage? Likely candidates: (a) the assertion tick caught the savepoint-cascade-fail class (Session 220 commit `57960d4b`) before per-assertion `admin_transaction` landed; (b) the invariant fired at sev1 but `_send_operator_alert` was suppressed (rate-limit, recipient misconfig); (c) the failure mode was NOT "heartbeat INSERT failed silently" — it was a third-source divergence (`calculate_live_status` 15-min boundary). **Diagnose before pushing this extension** — if the existing invariant has a fire-path bug, lowering the threshold doesn't help.

2. **Source consolidation** — collapse `calculate_live_status()` + raw `site_appliances.status` reads into either `appliance_status_rollup.live_status` reads (display layer) or a new canonical helper (Task #50 + Task #40 dependencies). ~3-4h, own Gate A.

3. **Task #40 promotion** — `appliance_liveness` is PLANNED-blocked behind D1. Today's outage is the cost. Consider moving Task #40 priority up.

---

## Final overall verdict

**BLOCK new invariant. APPROVE extension of `heartbeat_write_divergence` per the SQL/registration/runbook changes above — CONDITIONAL on Task #80 (operator-followup: why didn't it fire today) being diagnosed first.** Open follow-up tasks for the third-source consolidation and Task #40 D1 promotion.

This avoids: a 4th invariant for a metric class with no canonical helper yet (Rule 1 violation), reading a materialized view in a substrate invariant (pattern break + 60-120s read-staleness), and duplicate operator signals for one physical fact.

# EXPECTED_INTERVAL_S Calibration Audit — 2026-05-12 (CORRECTED)

**Verdict: NO DRIFT FOUND.** Existing CI gate
`test_expected_interval_calibration.py` already enforces exact match
between `EXPECTED_INTERVAL_S` and each loop's body-level
`asyncio.sleep()`. 17/17 calibration tests pass on prod-deployed code.

## Audit error — initial pass was wrong

My first pass at this audit (2026-05-12 14:50Z) reported 7 entries
with declared >> actual drift. **Every one of those findings was
false.** Root cause: the `grep "_hb(\"<name>\")" ... wider context`
approach surfaced the FIRST `asyncio.sleep()` value within the
window — which is the **startup wait** (a one-time delay after
process boot, typically 300-1200s). The relevant calibration value
is the **inter-iteration sleep** in the `while True:` body. Those
match `EXPECTED_INTERVAL_S` exactly for every entry.

Example: `threshold_tuner_loop` has:
- Line 516: `await asyncio.sleep(900)  # Wait 15 min after startup`
- Line 617: `await asyncio.sleep(86400)  # 24 hours` (loop body)

`EXPECTED_INTERVAL_S["threshold_tuner"] = 86400`. **No drift.**

## Verification

`python3 -m pytest tests/test_expected_interval_calibration.py`
→ 17 passed, 0 failed. The gate AST-parses each loop function,
finds the `while True:` block, and reads the `asyncio.sleep(N)`
within. The 2026-05-01 health_monitor drift class is closed and
the gate prevents regressions.

## Uncovered loops — still relevant

24 background loops registered in `mcp-server/main.py::task_defs`
have NO `EXPECTED_INTERVAL_S` entry. `bg_loop_silent` cannot fire
on these. Listed in the earlier (now-deleted) draft of this digest:

- ots_reverify, mesh_consistency, flywheel_reconciliation
- cve_watch, cve_remediation, framework_sync
- flywheel, l2_auto_candidate
- companion_alerts, reconciliation
- unregistered_device_alerts, partner_payout
- flywheel_orchestrator, partition_maintainer
- weekly_rollup_refresh, partner_weekly_digest
- expire_consent_request_tokens, heartbeat_partition_maintainer
- mesh_reassignment, sigauth_auto_promotion
- client_telemetry_retention, data_hygiene_gc
- relocation_finalize, flywheel_federation_snapshot

Each gets a startup heartbeat via `_supervised`
(`mcp-server/main.py:1777`) but no staleness threshold. To close:
add an entry to `EXPECTED_INTERVAL_S` matching each loop's body
sleep; the existing CI gate then enforces forward calibration.

## Lessons captured

- **Audit-script regex must match LOOP BODY sleeps, not startup
  sleeps.** The `_hb(name)` callsite is INSIDE the body loop, so a
  wider grep window catches both the startup `asyncio.sleep` (before
  the `while True:`) and the body `asyncio.sleep` (inside it). The
  first one to appear textually is the startup wait — which is wrong
  for calibration.
- **Trust the existing CI gate first.** Before spawning an audit
  fork, check if there's already a test enforcing the invariant.
  `test_expected_interval_calibration.py` was sitting there the
  whole time, would have answered "no drift" without 30 minutes of
  flailing.

## Class verdict

Calibration is correct. 5/15 scheduled_followup entry can be CLOSED —
the work is already done by the existing CI gate. The uncovered-24
followup is the remaining structural gap.

# load_test_run_aborted_no_completion

**Severity:** sev3
**Display name:** Load-test abort not honored

## What this means (plain English)

An operator or AlertManager rule requested an abort on an active
load-harness run more than 30 minutes ago, but the run is still
in `starting` / `running` / `aborting` — k6 has not transitioned
to a terminal state. The abort bridge regressed: k6 should poll
`/api/admin/load-test/status` every iteration and exit within 30
seconds of seeing the `aborting` status.

## Root cause categories

- k6 wrapper not polling `/status` (script bug)
- Polling but network from CX22 to central-command broken
- k6 in a tight unbreakable loop (rare; wrappers usually have
  outer signal handlers)
- Auth header rotated mid-run; subsequent polls 401

## Immediate action

- Force-terminate the run via:
  ```bash
  curl -X POST https://central-command.osiriscare.com/api/admin/load-test/<run_id>/complete \
    -H "Authorization: Bearer <admin-bearer>" \
    -H "Content-Type: application/json" \
    -d '{"final_status":"failed"}'
  ```
- Kill the k6 process on CX22 if still up:
  `ssh root@10.100.0.4 'pkill -9 k6'`

## Verification

- Panel: invariant clears on next 60s tick.
- The next k6 wrapper invocation should pull the latest poll
  logic — verify the wrapper PR addressed the regression before
  re-enabling runs.

## Escalation

- If this fires repeatedly (>1 per week), the abort path is the
  wrong primary control — re-evaluate whether the `/var/lib/k6/
  abort` file-presence check (P1-3 secondary control) should be
  the primary.

## Related runbooks

- `load_test_run_stuck_active.md`

## Change log

- 2026-05-16 — initial — Task #62 v2.1 Commit 5a

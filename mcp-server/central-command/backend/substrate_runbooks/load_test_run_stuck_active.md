# load_test_run_stuck_active

**Severity:** sev2
**Display name:** Load-test run stuck active

## What this means (plain English)

A load-harness run started more than 6 hours ago and is still
marked starting / running / aborting. k6 should never run that
long. The most likely cause is that k6 (or its wrapper) crashed
without calling the `/complete` endpoint, leaving the row
"in-flight" forever. The partial unique index on
`load_test_runs` will block any NEW run from starting until this
one is reaped.

## Root cause categories

- k6 process killed (CX22 box rebooted, OOM, network drop)
- Wrapper script crashed before /complete (Python exception,
  signal, ssh-tunnel drop)
- Operator started a long-soak run manually and forgot it

## Immediate action

- If the **Run action** button exists on the panel: it issues
  `POST /api/admin/load-test/{run_id}/complete` with
  `final_status='failed'`.
- Otherwise: run

  ```bash
  curl -X POST https://central-command.osiriscare.com/api/admin/load-test/<run_id>/complete \
    -H "Authorization: Bearer <admin-bearer>" \
    -H "Content-Type: application/json" \
    -d '{"final_status":"failed"}'
  ```

## Verification

- Panel: invariant row clears on next 60s tick once the run
  transitions to a terminal state.
- DB: `SELECT status FROM load_test_runs WHERE run_id = '<run_id>';`
  should show `failed` or `aborted`.

## Escalation

If multiple stuck runs accumulate (say >3 in 24h), the CX22 box
or wrapper is regressed. Page on-call and pause new runs until
investigated.

## Related runbooks

- `load_test_run_aborted_no_completion.md`
- `synthetic_traffic_marker_orphan.md`

## Change log

- 2026-05-16 — initial — Task #62 v2.1 Commit 5a

# relocation_stalled

**Severity:** sev2
**Display name:** Admin-initiated relocation stalled (>30 min pending)

## What this means (plain English)

Someone (an admin or partner) clicked "Relocate to…" in the dashboard
or hit the relocate API. The backend pre-created the destination row,
minted a fresh API key, and asked the daemon to complete the move.
Thirty minutes later the daemon still hasn't checked in under the new
site, so something between "we asked" and "daemon did it" is broken.

## Root cause categories

- **Daemon offline** — most common. The appliance was powered off,
  network cable pulled, or daemon crashed mid-move. Check
  `site_appliances.last_checkin` for the source appliance_id.
- **Daemon version too old** — should have been caught by the
  endpoint's version-gate (≥0.4.11 required for the fleet_order
  path), but worth checking. If `details.fleet_order_id` is null in
  the violation, the operator was given an `ssh_snippet` instead and
  hasn't run it yet.
- **Reprovision order failed on the daemon** — the daemon picked up
  the order but `handleReprovision()` errored (config.yaml unwritable,
  yq missing, etc). Check `/var/lib/msp/appliance-daemon.log` for the
  order ACK + traceback.
- **Network split mid-move** — daemon ACKed the order, started
  restart, then couldn't reach the API under the new identity. Daemon
  will keep retrying; if reachability returns, finalize sweep flips
  the row to 'completed' on its own.

## Immediate action

- If the appliance is online: wait one more minute. The
  `relocation_finalize_loop` runs every 60s; if the daemon completes
  the move, the row auto-flips to 'completed'.
- If `details.fleet_order_id` is null AND `details.ssh_snippet` was
  shown to the operator at relocate time: rerun the SSH snippet from
  the original API response. The pre-minted api_key in the response
  is still valid (api_keys row stays active until the move
  completes).
- If the daemon is offline: bring it back online. The auto-rekey path
  will land the move on first checkin (≥0.4.11) or the operator must
  finish via SSH (legacy daemons).

## Verification

```sql
-- Confirm the relocate completed
SELECT status, completed_at FROM relocations WHERE id = <details.relocation_id>;
-- Should be 'completed' with a populated completed_at.

-- Confirm target site_appliances has fresh last_checkin
SELECT site_id, hostname, last_checkin, status
  FROM site_appliances
 WHERE appliance_id = '<details.target_appliance_id>';
-- Should show the new site_id, status='online', last_checkin within last minute.

-- Confirm source has been soft-deleted
SELECT site_id, deleted_at, status FROM site_appliances
 WHERE appliance_id = '<details.source_appliance_id>';
-- Should show deleted_at populated, status='relocated'.
```

## If the move can't be salvaged

If the daemon is permanently bricked or the move was a mistake:

```sql
-- Mark the relocation failed (admin-only path)
UPDATE relocations
   SET status = 'failed', completed_at = NOW()
 WHERE id = <relocation_id>;

-- Clean up the unused target site_appliances row
UPDATE site_appliances
   SET deleted_at = NOW(),
       deleted_by = 'relocation_failed_cleanup',
       status = 'cancelled'
 WHERE appliance_id = '<target_appliance_id>'
   AND last_checkin IS NULL;

-- Deactivate the unused target api_key
UPDATE api_keys
   SET active = false
 WHERE appliance_id = '<target_appliance_id>'
   AND active = true;
```

The source site_appliances row stays at `status='relocating'` until
the operator manually clears it (or re-runs the relocation).

## Escalation

If 60+ min has elapsed and none of the verification queries pass:
the move has truly failed. Treat as a P2 ops incident. Page the
on-call owner of the appliance fleet. Possible underlying causes
needing engineering attention: the daemon binary at v0.4.11 has a
regression in handleReprovision() (test against a staging appliance
to reproduce); api.osiriscare.net is unreachable from the
appliance's LAN (check DNS filtering at the customer site); the
daemon's pre-restart ACK path is racing the systemctl restart and
the order never lands as completed (consider increasing
restart_delay_sec on the order).

## Related runbooks

- Endpoint: `POST /api/sites/{site_id}/appliances/{appliance_id}/relocate`
- Daemon handler: `appliance/internal/orders/processor.go::handleReprovision`
- Auto-detected sibling: `appliance_moved_unack` invariant (subnet diff)
- Customer-facing record: `compliance_bundles` row with
  `check_type='appliance_relocation'` and `check_result='admin_initiated'`
- Audit row: `admin_audit_log` with `action='appliance.relocate'`

## Change log

- 2026-04-25 — initial runbook (Session 210-B RT-4). Added alongside
  the relocation_stalled invariant + finalize_pending_relocations()
  background sweep. Closes the gap surfaced in the round-table QA on
  the relocate endpoint: a daemon that doesn't pick up its move
  within 30 min must be visible in the substrate, not silently stuck.

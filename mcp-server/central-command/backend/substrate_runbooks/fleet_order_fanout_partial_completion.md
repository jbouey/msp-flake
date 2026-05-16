# fleet_order_fanout_partial_completion

**Severity:** sev2
**Display name:** Fleet-order fan-out has K-of-N unacked at 6h+

## What this means (plain English)

A `fleet_cli --all-at-site` fan-out (Task #118) creates ONE
privileged attestation bundle that covers N fleet_orders — one
per target appliance. After 6 hours, at least one of those N
orders has NO completion row in `fleet_order_completions`.

The operator sees the fan-out as "issued" but doesn't see
"K-of-N never executed." This invariant surfaces the gap so the
operator knows to follow up.

Why 6h: daemons heartbeat every 60s + pull orders on the same
cadence; an unacked fleet_order at 6h means the appliance is
offline OR not pulling orders. Mig 161 retries failed orders
within 1h — anything still unacked at 6h is beyond retry budget.

Why sev2 (not sev3 per parent Gate B sketch): per Gate A iter-4
2026-05-16 §P1-2, sibling parity with
`enable_emergency_access_failed_unack` (sev2). sev3 would fall
below the operator-attention threshold for chain-of-trust-
affected fan-outs.

## Root cause categories

1. **Target appliance offline > 6h** — most common; check
   `site_appliances.last_checkin` for the target_appliance_id.
   If offline, the operator should triage the appliance (reboot,
   network check) before the fan-out can complete on it.

2. **Daemon stuck on a prior order** — appliance is online but
   the daemon is wedged on a long-running prior fleet_order
   (e.g., nixos_rebuild). Tail logs for `appliance_id=<...>`
   + look for the prior order's status.

3. **`fleet_order_completion` writer broken** — the daemon may
   have actually completed the order but the completion-row
   INSERT failed. Verify: query the appliance's local journal
   for the fleet_order_id; if it ran, mismatch is server-side.

4. **Fan-out mid-deploy where appliance hadn't received order**
   — rare race where the fan-out issued during a checkin window
   that skipped this appliance. Mig 161's retry-after-1h covers
   most of this; if still unacked at 6h, retry has also failed.

## Immediate action

1. **Drill down on the specific fleet_order_id:**
   ```sql
   SELECT fo.id, fo.order_type, fo.parameters, fo.created_at,
          fo.expires_at, fo.status, sa.last_checkin, sa.status AS app_status
     FROM fleet_orders fo
     LEFT JOIN site_appliances sa
       ON sa.appliance_id = fo.parameters->>'target_appliance_id'
    WHERE fo.id = '<details.fleet_order_id>';
   ```

2. **Check the appliance's last_checkin:**
   - If `last_checkin > now() - 1h`: appliance is online — class 2
     or 3 (daemon stuck / completion writer broken)
   - If `last_checkin < now() - 6h`: appliance is offline — class 1

3. **If appliance is genuinely decommissioned:** soft-delete via
   `UPDATE site_appliances SET deleted_at = now() WHERE
   appliance_id = '<...>'` AND reissue the fan-out via
   `fleet_cli ... --target-appliance-id <remaining-uuids>` for
   the live appliances. The soft-delete filter in #118's
   enumeration will exclude this appliance from future fan-outs.

## Verification

- Invariant row clears on next 60s tick once one of:
  - `fleet_order_completions` row gets inserted for the (fleet_
    order_id, appliance_id) pair
  - 24h passes (rolling window slides past the issuance)
  - Operator cancels the order via `fleet_cli cancel <order-id>`

## Escalation

- **>10% of a fan-out unacked at 6h:** P0 — the fan-out's target
  set was misconfigured (e.g., includes inactive appliances the
  soft-delete filter missed). Pause issuing new fan-outs to the
  affected site until the soft-delete invariant is reconciled.
- **Same appliance_id appears across multiple recent fan-outs as
  orphaned:** the appliance is chronically problematic. Either
  reboot/replace OR soft-delete to remove from fan-out targeting.

## Related runbooks

- `fleet_order_url_resolvable.md` (sev1 sibling — covers the
  URL-side completion-blocker, different class)
- `appliance_offline_extended.md` (sev2 — root cause class 1
  often triggers this too)

## Change log

- 2026-05-16 — initial — Task #128, #118 Gate B P2-1 closure.
  Fork verdict: audit/coach-128-fanout-completion-orphan-gate-a-
  2026-05-16.md (APPROVE-WITH-FIXES; 3 P0s + 3 P1s all closed
  in implementation).

# appliance_disk_pressure

**Severity:** sev2
**Display name:** Appliance /nix store out of space

## What this means (plain English)

An appliance has surfaced a `No space left on device` error in a completed admin order or fleet order completion in the last 24 hours. The /nix store is full (or the partition holding /var/lib/msp/evidence is). While it stays full: nixos_rebuild silently fails, evidence bundles cannot fsync, daemon upgrades cannot stage, and rollback targets get pruned. Mirrors `vps_disk_pressure` but scoped per-appliance — one row per affected appliance in the last 24h.

## Root cause categories

- **Generation accumulation.** Multiple failed rebuilds + nightly system.autoUpgrade retain every intermediate closure; without GC the store grows unbounded. Most common cause for the first fire on a given appliance.
- **Undersized /nix partition.** The disk image defaults to 20 GB for /nix, which is marginal for NixOS appliances running >90 days. Reprovisioning with a larger partition is the permanent fix.
- **Large evidence backlog.** The evidence directory under /var/lib/msp sometimes shares a partition; a stuck OTS-anchor queue or failed S3 upload can pile up.
- **Log / journal growth.** Uncapped journald size on a chatty daemon (rare since journal_upload is live).

## Immediate action

- Issue a `nix_gc` fleet_order against the affected appliance (handler shipped 2026-04-22):

  ```
  fleet_cli create nix_gc \
    --actor-email you@example.com \
    --reason "appliance_disk_pressure invariant fired; reclaim /nix/store" \
    --param older_than_days=7 \
    --param optimise=true \
    --param site_id=<site>
  ```

  The handler runs `nix-collect-garbage --delete-older-than 7d` under systemd-run (escapes the daemon's ProtectSystem=strict sandbox) and reports `bytes_freed` in the completion payload.

- Verify the GC actually freed bytes:

  ```sql
  SELECT foc.appliance_id,
         foc.output->>'before_bytes' AS before,
         foc.output->>'after_bytes'  AS after,
         foc.output->>'bytes_freed'  AS freed
    FROM fleet_order_completions foc
    JOIN fleet_orders fo ON fo.id = foc.fleet_order_id
   WHERE fo.order_type = 'nix_gc'
   ORDER BY foc.completed_at DESC LIMIT 5;
  ```

  If `bytes_freed` is near zero, generations are newer than the window — try `older_than_days=3`. If still zero, the pressure is outside /nix (evidence dir or /var/log) and GC won't help; escalate.

## Verification

- Panel: invariant row auto-resolves on the next 60s tick after the 24h window rolls past the last observed `No space left` error. Firing a successful `nix_gc` does not itself clear the row — a new non-ENOSPC completion must land inside the window.
- CLI:

  ```sql
  SELECT appliance_id, completed_at, error_message
    FROM admin_orders
   WHERE error_message ILIKE '%no space left%'
     AND completed_at > NOW() - INTERVAL '24 hours'
   ORDER BY completed_at DESC;
  ```

  Zero rows = invariant cleared at the next tick.

## Escalation

NOT an auto-fix: GC is operator-authorized precisely because it prunes rollback targets. Escalate to an engineer if (a) two consecutive `nix_gc` orders return `bytes_freed = 0`, (b) the error recurs within 24h of a successful GC, or (c) multiple appliances on the same site fire simultaneously (site-wide storage fault suspected). A single appliance repeatedly hitting this after aggressive GC is a signal to reprovision with a larger disk image.

## Related runbooks

- `nixos_rebuild_success_drought` — the downstream outcome invariant; disk pressure is the #1 silent root cause.
- `vps_disk_pressure` — the host-side mirror of this invariant; same class of failure, different surface.
- `evidence_chain_stalled` — may fire jointly when the evidence partition shares the full disk.

## Change log

- 2026-04-22 — generated — cut-in following the 0.4.7 diagnostic upgrade revealing ENOSPC as the silent root cause behind the 59-day `nixos_rebuild_success_drought`.

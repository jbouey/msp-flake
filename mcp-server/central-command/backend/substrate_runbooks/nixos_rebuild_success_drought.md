# nixos_rebuild_success_drought

**Severity:** sev2
**Display name:** No nixos_rebuild has succeeded fleet-wide in 7d

## What this means (plain English)

Operators have triggered one or more `nixos_rebuild` orders in the last 7 days, but **none have completed successfully**. NixOS-level remediation (systemd units, kernel params, boot loader, firmware packages) can no longer be delivered to the fleet. Every minute this sits open, drift between shipped-as-ISO config and live-appliance config widens. Does NOT fire when the system is quiet — a fleet with zero attempts is correct; the drought only opens after operators start trying and fail.

## Root cause categories

- **Flake-eval regression.** Pinned nixpkgs commit started failing under a transitive dep update (most common when lanzaboote or a custom module upgrades).
- **Daemon truncating the error.** Pre-0.4.7 daemons tail-truncated `nixos-rebuild test` output to 500 chars, hiding the `error:` banner — every failure looks identical in `admin_orders.error_message`.
- **Runtime-only option in the config.** An option like `boot.loader.systemd-boot.*` that requires a real `switch` (not `test`) — the canary succeeds interactively but fails on the appliance path.
- **Lanzaboote / signature hash drift.** Secure-boot pipeline refuses to sign the new closure.

## Immediate action

- Upgrade the target appliance to daemon ≥ 0.4.7 if not already (it persists `/var/lib/msp/last-rebuild-error.log` and returns head+tail 4KB):

  ```
  fleet_cli create update_daemon \
    --param binary_url=https://api.osiriscare.net/updates/appliance-daemon-0.4.7 \
    --param binary_sha256=<sha> \
    --param version=0.4.7 \
    --param site_id=<site> --skip-version 0.4.7
  ```

- Pull the latest failure directly from the DB — the `error_message` column carries the nix banner on 0.4.7+:

  ```sql
  SELECT appliance_id, error_message, result
    FROM admin_orders
   WHERE order_type='nixos_rebuild' AND status='failed'
   ORDER BY completed_at DESC LIMIT 1;
  ```

- Locally sanity-check the same flake-ref from the repo root before the next canary:

  ```
  nix eval .#nixosConfigurations.osiriscare-appliance-disk.config.system.build.toplevel.drvPath
  ```

  If local fails too, fix the flake before re-firing. If local succeeds but the appliance fails, it's environment-specific (nixpkgs cache drift, disk pressure, lanzaboote pubkey mismatch).

## Verification

- Panel: invariant row auto-resolves once one `nixos_rebuild` admin_order lands `status='completed'` (next 60s tick after completion).
- CLI:

  ```sql
  SELECT COUNT(*) FILTER (WHERE status='completed') AS good,
         COUNT(*) FILTER (WHERE status='failed')    AS bad,
         MAX(completed_at) FILTER (WHERE status='completed') AS last_success
    FROM admin_orders
   WHERE order_type='nixos_rebuild'
     AND created_at > NOW() - INTERVAL '7 days';
  ```

  The `good` column must be ≥ 1 before the invariant clears.

## Escalation

NOT an auto-fix. A failed rebuild IS the signal — issuing more orders without a root-cause fix just adds noise to the audit log. Escalate to an engineer if the failure recurs on a second appliance AND the reproducer at HEAD succeeds locally. Security-relevant escalations: repeated lanzaboote hash mismatches on one site-only may indicate tampering with the local nix store.

## Related runbooks

- `agent_version_lag` — target daemon version stuck; often paired with this during a failed upgrade.
- `fleet_order_url_resolvable` — upstream gate; check before blaming the rebuild path.

## Change log

- 2026-04-21 — generated — SWE-2 cut-in following 2026-04-21 round-table; 59-day success drought on fleet.

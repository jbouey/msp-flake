# installed_but_silent

**Severity:** sev2
**Display name:** Installer ran but the installed system never checked in
**Added:** 2026-04-23 (v40.4 round-table)

## What this means (plain English)

The installer posted `/api/install/report/start` at least once (we have an `install_sessions` row from ≥20 min ago) but the installed system has **never** produced a fresh checkin in `site_appliances`. Installer ran, box rebooted, and then silence.

This is the exact class of failure that bricked 3/3 v40.0-v40.2 reflashed appliances for 4+ hours on 2026-04-23 while every other invariant stayed silent.

## Why existing invariants missed it

`provisioning_stalled` and `provisioning_network_fail` both require `install_sessions.checkin_count >= 3`. They fire when the **installer** is actively retrying. They don't fire when the installer runs ONCE, completes, and hands off to an installed system that then fails silently — which is what v40.0-v40.2 did. `installed_but_silent` is the outcome-edge signal that covers that gap.

## Root cause categories

- DNS race in `msp-auto-provision` (resolvconf hadn't written `/etc/resolv.conf` when the service fired). Fixed in v40.4 via `|| true` on the DNS stage-1 capture.
- Classpath / missing binary (e.g. `${pkgs.inetutils}/bin/host` → moved to `bind.host`). Fixed in v40.3 + `_bg_bin_check` preamble.
- Central Command unreachable from the site's egress (firewall block on api.osiriscare.net).
- Post-auto-rekey split-brain auth (daemon sub-components hold stale key; logs/evidence/agent endpoints 401 while checkin 200). Requires daemon fix 0.4.7+.
- Phase 0 break-glass hang (fixed in v40.1 — removal of `Before=[sysinit.target, multi-user.target]`).

## Immediate action

1. SSH in as `msp` (v40.1+ ships sshd on boot with the ISO-embedded operator pubkey):

   ```
   ssh msp@<appliance-ip>
   ```

2. Check `msp-auto-provision`:

   ```
   sudo systemctl status msp-auto-provision.service       # NOPASSWD on v40.4+
   sudo journalctl -u msp-auto-provision -n 100           # NOPASSWD
   ```

3. If failed, restart (self-heal now works on v40.4+ thanks to `Restart=on-failure`, but manual restart is faster):

   ```
   sudo systemctl restart msp-auto-provision.service      # NOPASSWD
   ```

4. LAN beacon — the machine-readable diagnostic:

   ```
   curl -s http://<appliance-ip>:8443/ | jq
   ```

   Read `state`, `last_error`, and `install_gate.last_stage_failed`. They name the exact broken stage (`dns`, `tcp_443`, `tls`, `health`, or `dns_filter_suspected`).

5. If the daemon is running AND checkin 200s AND evidence/logs/agent endpoints 401 — that's the split-brain auth bug (v40.4 audit item #5). Daemon sub-components hold a stale api_key after auto-rekey. Requires a daemon rebuild (0.4.7+); no field mitigation until the new daemon ships.

## Verification

- Panel: invariant row clears on next 60s tick after `site_appliances.last_checkin` updates.
- DB:

   ```sql
   SELECT mac_address, last_checkin, EXTRACT(EPOCH FROM NOW() - last_checkin)::int AS age_s
   FROM site_appliances
   WHERE mac_address = '<MAC>';
   ```

   Fresh age (< 120 s) = daemon is now checking in.

## Escalation

- Never auto-fix. The fleet-order path requires the daemon to be checking in, which is the thing that's broken. Operator SSH is the only rescue for this invariant.
- If multiple appliances at the same site fire this simultaneously, that's a site-wide network fault (DNS filter, egress ACL). Escalate to the site's IT contact; don't reflash more boxes until the site-side issue is resolved.

## Related runbooks

- `provisioning_network_fail` — installed system retrying but gate never passes (fires ≥90s after installed-system first boot)
- `provisioning_stalled` — both installer AND installed failing 15+ min (DNS filter most likely)
- `install_loop` — installer looping ≥5 times at `live_usb` (hardware / boot order issue)
- `offline_appliance_over_1h` — appliance that WAS working went silent

## Change log

- 2026-04-21 — generated — stub created
- 2026-04-23 — populated during v40.4 round-table (audit item #11)

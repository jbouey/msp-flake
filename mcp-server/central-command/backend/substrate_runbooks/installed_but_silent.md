# installed_but_silent

**Severity:** sev1
**Display name:** Install ran but installed system never phoned home

## What this means (plain English)

An appliance went through the live-USB install phase (we saw ≥5 `install_sessions` checkins from the live USB), but the installed system has never produced a fresh heartbeat in `site_appliances`. The install "appeared to complete" from the operator's perspective — USB ejected, box rebooted — but the daemon on the installed disk never came online.

Sibling invariant: `installer_halted_early` (sev2) covers the **under**-5-checkin class — installer posted `/start` once or twice, then silence. This one (sev1) covers the **over**-5-checkin class — installer looped multiple times, install eventually completed, then the installed daemon never checked in.

## Root cause categories

- Boot loader corruption — the installed system's systemd-boot can't find the kernel; BIOS falls back to PXE or halts.
- ESP / raw-image dd mismatch — dd completed but the partition table / BOOTX64.EFI path is wrong for this firmware.
- Phase 0 break-glass hang (pre-v40.1; fixed by removing `Before=[sysinit.target, multi-user.target]`).
- Classpath / syntax bug in the installed system (pre-v40.3 `pkgs.inetutils/bin/host`, pre-v40.3 em-dash beacon). Fixed in v40.3+.
- DNS filter blocking api.osiriscare.net for the installed system's MAC (check `provisioning_stalled`).
- Site-side egress ACL blocking 178.156.162.116:443 for the installed system.

## Immediate action

1. SSH in as `msp` (v40.1+ ships sshd on boot with the ISO-embedded operator pubkey):

   ```
   ssh msp@<appliance-ip>
   ```

2. Check the local status beacon on `:8443` — the `state` + `last_error` + `install_gate` fields name the exact failure stage:

   ```
   curl -s http://<appliance-ip>:8443/ | jq
   ```

3. If beacon says `daemon_crashed` or `awaiting_provision`, retrieve the break-glass passphrase via `/api/admin/appliance/{id}/break-glass` (5/hr rate-limited), `sudo -i`, and inspect:

   ```
   systemctl status msp-auto-provision.service
   journalctl -u msp-auto-provision -n 100
   systemctl status appliance-daemon.service
   journalctl -u appliance-daemon -n 100
   ```

4. If the installed system never reached multi-user.target, check for a Phase 0 hang: `journalctl -u msp-breakglass-provision.service` should show "Finished Phase 0" quickly; if it's still running, you're looking at a pre-v40.1 regression.

## Verification

- DB: `site_appliances.last_checkin` for this MAC goes fresh (< 120s) after operator action.
- Panel: invariant row resolves on next 60s tick.

## Escalation

- If 2+ appliances at the same site fire this simultaneously, that's a site-wide boot or network fault. Check the site's DHCP, firewall, and DNS filter before touching more boxes.
- Never auto-fix. The fleet-order path requires the daemon to be checking in, which is precisely what's broken here.

## Related runbooks

- `installer_halted_early` — under-5 installer checkins then silence (sibling, sev2)
- `provisioning_network_fail` — installed system retrying but 4-stage gate never passes (sev2)
- `provisioning_stalled` — installer AND installed both failing; DNS filter most likely (sev2)
- `install_loop` — installer looping ≥5 times at `live_usb` (hardware / boot order issue, sev1)

## Change log

- 2026-04-21 — initial stub
- 2026-04-23 — populated (v40.4 round-table post-audit). Sibling invariant `installer_halted_early` added to cover the under-5-checkin class that bricked v40.0-v40.2 appliances.

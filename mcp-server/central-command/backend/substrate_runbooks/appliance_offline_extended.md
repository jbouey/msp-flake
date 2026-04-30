# appliance_offline_extended

**Severity:** sev2
**Display name:** Appliance offline > 24 hours — phone the customer

## What this means (plain English)

An appliance has not checked in for **24 hours or more**. This is
the escalation tier above `offline_appliance_over_1h` (sev2,
fires at 1h):

| Tier | Threshold | Implication |
|---|---|---|
| `offline_appliance_over_1h` | 1h | "Wait one cycle, probably transient" |
| `appliance_offline_extended` | 24h | "Phone the customer — something is genuinely wrong" |

Both invariants run independently; an appliance that hits 24h will
have BOTH firing. The duration ladder gives operators escalation
graduation without needing to compute it client-side.

**Diagnostic hint (round-table P2):** the 1h sibling depends on
`mark_stale_appliances_loop` having flipped `status='offline'`.
This 24h tier uses raw `last_checkin` arithmetic and does NOT
require the status-flip. So if `appliance_offline_extended`
fires WITHOUT `offline_appliance_over_1h` also firing, that's a
signal that `mark_stale_appliances_loop` is wedged or dead —
the appliance has been silent past both thresholds, but the
status field hasn't been flipped. Investigate the
`mark_stale_appliances_loop` heartbeat in the substrate task
supervisor.

## Root cause categories

- **Customer site WAN outage.** Internet down. Multi-day outages
  are rare in healthcare-SMB but not impossible (storms, ISP
  changes, building-wide power events).
- **Customer powered the appliance off.** Either intentional
  (relocation, EOL) or accidental (cleaning crew unplugged).
- **Daemon crashed and didn't restart.** Should be self-healing
  via systemd; if persistent across multiple reboots, daemon
  build regression.
- **Appliance hardware failure.** Disk, NIC, PSU. If the appliance
  responds to ICMP from the customer LAN but daemon doesn't run,
  systemd / disk / build problem; if ICMP doesn't respond, hardware.

## Immediate action

**Per CLAUDE.md non-operator partner posture: substrate exposes
the signal; the operator (MSP / partner) decides whether to
notify the customer per their BAA. Do NOT auto-page the
clinic from substrate.**

For the operator:

1. Verify the appliance's `last_checkin` matches the violation:
   ```
   ssh root@<vps> "docker exec mcp-postgres psql -U mcp -d mcp -c \"SELECT site_id, hostname, agent_version, status, last_checkin, NOW() - last_checkin AS since FROM site_appliances WHERE deleted_at IS NULL ORDER BY last_checkin ASC NULLS LAST\""
   ```

2. Cross-reference with `discovered_devices_freshness` and
   `journal_upload_stale` invariants. If all three fire on the
   same appliance, it's full site-offline — phone the customer.

3. Contact the partner ops_email (substrate-to-operator
   notification is in scope; substrate-to-clinic is NOT).

4. Partner decides whether to:
   - Phone the customer to confirm site state
   - Dispatch a tech for hardware investigation
   - Schedule a remote reflash if the daemon won't recover
   - Mark the site as decommissioned if the customer has churned

5. **Do not auto-decommission from substrate.** Setting
   `site_appliances.status = 'decommissioned'` is an operator
   decision; substrate exposes "offline 24h+", operator decides
   "is this churn or transient."

## Verification

- Panel: invariant row clears on next 60s tick once the
  appliance checks in successfully.
- CLI:
  ```sql
  SELECT site_id, hostname, last_checkin,
         NOW() - last_checkin AS since_offline
    FROM site_appliances
   WHERE deleted_at IS NULL
     AND status NOT IN ('decommissioned', 'relocating', 'relocated')
     AND last_checkin < NOW() - INTERVAL '24 hours'
   ORDER BY last_checkin ASC;
  ```

## Escalation

Sev2 — operator response expected within the workday, not paging.

If the same appliance has been firing this for 7+ days,
the customer relationship is the issue, not the appliance.
Round-table review is the right escalation, not engineering.

## Related runbooks

- `go_agent_heartbeat_stale.md` — workstation-class sibling
- `discovered_devices_freshness.md` — site-network freshness
- `journal_upload_stale.md` — appliance journal-upload pipeline
- `installed_but_silent.md` — installation-silent class

## Change log

- 2026-04-30 — created — Session 214 round-table fleet-edge
  liveness slice. Provides escalation graduation above the
  1-hour sev2 sibling (`offline_appliance_over_1h`).

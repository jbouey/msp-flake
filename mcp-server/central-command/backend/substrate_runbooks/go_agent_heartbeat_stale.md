# go_agent_heartbeat_stale

**Severity:** sev2
**Display name:** Workstation agent silent > 6 hours

## What this means (plain English)

A workstation Go agent has not sent a heartbeat to mcp-server in 6+
hours. The state machine in `_go_agent_status_decay_loop` (mig 263)
has already flipped this row's status field from `connected` to
`stale` / `disconnected` / `dead` based on heartbeat age. The
invariant fires sev2 because 6 hours is longer than legitimate
after-hours / commute / lunch / patch-cycle gaps for a production
workstation.

**Doctrine context:** before mig 263 (Session 214), `go_agents.status`
was gRPC-stream-write-only and never decayed. We empirically
observed 4 chaos-lab agents showing `status='connected'` 7+ days
after their host (iMac) was powered off — the dashboard claimed
all-good while reality was four dark boxes. This invariant + the
state machine close that doctrine gap.

**Excludes** rows tagged `agent_version='dev'` — those are chaos-
lab targets that are intentionally bouncy (deliberately attacked
+ snapshotted-back) and shouldn't pollute the production-fleet
dashboard. Real customer agents will never carry the `dev` tag.

## Root cause categories

- **Workstation legitimately powered off.** Most common cause.
  Customer turned off the machine end-of-day, on vacation, OS
  update reboot.
- **`osiriscare-agent` Windows service not auto-starting.** If the
  workstation rebooted but the agent service is set to
  StartupType=Manual, it won't reconnect on boot. Rare on
  production fleet but worth checking.
- **Site-level network egress blocked.** Customer firewall update
  or DNS change that breaks the path to `api.osiriscare.net`. If
  this happens to ALL agents at a site simultaneously (e.g. 4 hosts
  go silent within minutes of each other), it's almost certainly
  network, not individual workstations. **Tell-tale: same
  `site_id`, simultaneous staleness across many agents.**
- **Agent version regression.** A new agent build that crashes on
  startup. Cross-reference `agent_version` across stale rows; if
  they all share a version that recent fleet has churned to, the
  build is suspect.

## Immediate action

Substrate's job ends at the alarm. The partner (MSP) is the BAA-
holding operator and decides whether to notify the customer.
**NEVER directly notify the clinic from substrate — that's BA
territory.**

For the operator (MSP / round-table):

1. List the violation rows from `/admin/substrate-health` or:
   ```
   ssh root@<vps> "docker exec mcp-postgres psql -U mcp -d mcp -c \"SELECT details FROM substrate_violations WHERE invariant_name = 'go_agent_heartbeat_stale' AND resolved_at IS NULL\""
   ```

2. Check whether multiple agents at the same `site_id` are stale
   simultaneously. If yes → site-network event, not per-host.

3. Contact the partner ops_email. Substrate should NOT auto-page
   the customer; the partner decides escalation per BAA.

4. If the customer confirms physical-off, no action required —
   the invariant auto-resolves when the agent next reports a
   heartbeat (typically within seconds of service start).

5. If the customer confirms power-on but agent silent, escalate
   to fleet-debugging:
   - Log into the workstation; check `Get-Service osiriscare-agent`
   - Verify outbound HTTPS to `api.osiriscare.net` works
   - Check agent log file for crash on startup

## Verification

- Panel: invariant row should clear on the next 60s tick after
  the agent sends a heartbeat (resolved_at populates).
- CLI:
  ```sql
  SELECT agent_id, last_heartbeat, status,
         NOW()::timestamp - last_heartbeat AS since_heartbeat
    FROM go_agents
   WHERE last_heartbeat < NOW()::timestamp - INTERVAL '6 hours'
     AND (agent_version IS NULL OR agent_version != 'dev')
   ORDER BY last_heartbeat ASC;
  ```
  Expected post-resolution: zero rows.

## Escalation

Sev2 — operator action expected within the workday, not paging.

7-day stale across multiple workstations from the same site is
a separate concern: site is likely fully offline, escalate to
the appliance_offline_extended runbook (the appliances at that
site should also be firing that sev2). Cross-correlate before
phoning anyone.

## Related runbooks

- `appliance_offline_extended.md` — appliance-class sibling
- `discovered_devices_freshness.md` — site-network freshness
- `claim_event_unchained.md` — agent identity / claim chain

## Change log

- 2026-04-30 — created — Session 214 round-table fleet-edge
  liveness slice. Closes the doctrine violation observed where
  status='connected' persisted on agents dark for 7+ days.

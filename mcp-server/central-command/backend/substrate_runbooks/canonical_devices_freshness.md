# canonical_devices_freshness

**Severity:** sev2
**Display name:** Canonical Devices Reconciliation Loop Stale

## What this means (plain English)

The 60s background loop that maintains the canonical view of physical devices per site (`canonical_devices` table, Task #73 Phase 1, mig 319) has not updated rows for an active site in more than 60 minutes. Customers may see stale device counts in their monthly compliance packet PDFs (`compliance_packet.py` emits the canonical count post-migration) AND on the device-inventory page (`device_sync.get_site_devices` reads canonical post-migration). The underlying `discovered_devices` data is still being collected by appliances — only the deduplicated canonical view is stale.

## Root cause categories

- **Reconciliation loop stalled** — generic stuck-background-loop class. Check `/admin/substrate-health` for `bg_loop_silent`.
- **Per-site UPSERT failing** — the reconciliation transaction for a specific site_id is throwing errors and rolling back every tick. Check mcp-server logs for ERROR-level entries mentioning `canonical_devices`.
- **New site, transient** — a site whose appliances reported their first observations within the last 60s — the loop has not yet ticked for it. Clears on next tick.
- **discovered_devices RLS context mismatch** — the loop runs under admin context to read all sites; if the conn isn't in admin context, RLS hides every row and the loop processes zero updates.

## Immediate action

This is an operator-facing alert. **DO NOT surface to clinic-facing channels** — substrate-internal reconciliation state is not customer-relevant per Session 218 task #42 opaque-mode parity rule.

1. **Check substrate-health panel** — is `bg_loop_silent` firing? If yes, the loop has stopped ticking entirely.
2. **Check mcp-server logs** for ERROR-level entries:
   ```
   docker logs mcp-server 2>&1 | grep -i "canonical_devices\|reconcile_canonical" | tail -50
   ```
3. **If stalled > 5 minutes:** restart the backend (this is operational hygiene, not a security event):
   ```
   docker restart mcp-server  # forces background_tasks re-init
   ```
4. **Verify reconciliation resumed:**
   ```sql
   SELECT site_id, MAX(reconciled_at), COUNT(*)
     FROM canonical_devices
    WHERE site_id = '<flagged site_id>'
    GROUP BY site_id;
   ```
   `MAX(reconciled_at)` should be within the last 60s after restart.

## Verification

- Panel: invariant row clears on next 60s tick after a successful reconciliation pass.
- CLI: `SELECT site_id, MAX(reconciled_at) FROM canonical_devices GROUP BY site_id ORDER BY 2 ASC LIMIT 10;` — all active sites should show timestamps within the last 60 minutes.

## Escalation

NOT a security event. Operational hygiene only. Customer-impact path: monthly compliance packet PDF generation reads `canonical_devices` for `total_devices` count — if a packet generation request fires during a > 1hr stale window, the customer's packet may emit slightly stale counts. Past Ed25519-signed packets remain immutable; only newly-issued packets during the stale window are affected.

If stale > 1 hour AND a compliance packet generation is imminent: escalate to engineering for loop debug. Otherwise wait for next tick + verify clearance.

## False-positive guard

The invariant only fires for `site_appliances WHERE status='online' AND deleted_at IS NULL` — recently-deleted appliances or sites without active appliances don't trigger. New sites with zero canonical_devices rows YET (loop hasn't ticked) fire on first scan; clears within 60s once the first appliance reports.

## Related runbooks

- `discovered_devices_freshness.md` — sibling invariant on the source table. If BOTH this and that fire, the issue is upstream (appliances not reporting) rather than the reconciliation loop.
- `bg_loop_silent.md` — generic stuck-background-loop class. Fires when ANY background loop's heartbeat goes stale.
- `canonical_compliance_score_drift.md` — sibling Counsel Rule 1 invariant for the compliance_score canonical helper.

## Change log

- 2026-05-13 — initial — Task #73 Phase 1 canonical_devices rollout per user directive ("long lasting enterprise solutions, NOT a hotfix"). Counsel Rule 1 + Rule 4 closure.

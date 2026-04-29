# flywheel_orphan_telemetry

**Severity:** sev1
**Display name:** Flywheel telemetry orphaned from live appliance fleet

## What this means (plain English)

`execution_telemetry` rows are landing under a `site_id` that no longer has any
live appliances in `site_appliances` (all rows soft-deleted or the site_id was
renamed). The 30-min `_flywheel_promotion_loop` aggregates these rows into
`aggregated_pattern_stats`, which means evidence accumulates against a dead
site and never reaches the canonical site's promotion candidates. Left alone,
the data flywheel runs but its output is invisible — promotions stall while
the dashboard still says "healthy."

## Root cause categories

- **Site rename / appliance relocate** that didn't migrate `execution_telemetry`
  alongside `aggregated_pattern_stats` (the documented "Site rename is a
  multi-table migration" rule was extended in migration 255 — earlier renames
  may still leak).
- **Soft-deleted appliances** under a site_id that the agent is still posting
  telemetry to (stale config.yaml, missed `reprovision` order).
- **Spoofed / misconfigured appliance** posting checkins under a site_id
  that has no provisioning record. This is the security-relevant tail — see
  Escalation.

## Immediate action

- Confirm whether the orphan site_id is a **known dead site** (rename / decom)
  or **unknown**:

  ```
  psql ... -c "SELECT site_id, status, deleted_at FROM site_appliances
                WHERE site_id = '<orphan_site_id>'
                ORDER BY deleted_at DESC NULLS FIRST"
  ```

- **Known dead site (rename/decom):** run a relocate-style migration to move
  `execution_telemetry`, `incidents`, `l2_decisions`, and
  `aggregated_pattern_stats` to the canonical site_id. Migration 255 is the
  reference template. `compliance_bundles` MUST stay under the original
  site_id (the Ed25519 + OTS signature binds it).

  ```
  fleet_cli relocate-telemetry --orphan-site-id <orphan> \
       --canonical-site-id <canonical> \
       --actor-email you@example.com \
       --reason "post-relocate orphan cleanup, ticket <id>"
  ```

  (If the CLI subcommand doesn't exist yet, hand-author a migration following
  the migration 255 pattern and submit through the normal migration channel.)

- **Unknown site_id:** treat as escalation (below). Do NOT run the
  relocate-telemetry path until you've confirmed it's not a spoofed appliance.

## Verification

- Panel: invariant row should clear on the next 60s tick once the orphan
  rows fall out of the 24h window OR are migrated to the canonical site.
- CLI:

  ```
  psql ... -c "SELECT et.site_id, COUNT(*) AS orphan_24h
                 FROM execution_telemetry et
                WHERE et.created_at > NOW() - INTERVAL '24 hours'
                  AND et.site_id NOT IN (
                      SELECT DISTINCT site_id FROM site_appliances
                       WHERE deleted_at IS NULL
                  )
             GROUP BY 1
               HAVING COUNT(*) > 10"
  ```

  Expected: zero rows.

- Confirm the next `_flywheel_promotion_loop` tick (≤30 min) does NOT
  resurrect orphan rows in `aggregated_pattern_stats`:

  ```
  SELECT site_id, COUNT(*) FROM aggregated_pattern_stats
   WHERE site_id = '<orphan_site_id>' GROUP BY 1
  ```

  Should return 0.

## Escalation

Do **NOT** auto-migrate when the orphan site_id is:

- **Not present** in `sites` or `site_appliances` history at all (no rename
  trail, no decom record). Could be a spoofed appliance bypassing
  `_enforce_site_id` somehow — page security on-call.
- **Receiving telemetry from an appliance that ALSO checks in under a
  different site_id.** Possible identity-key reuse or misconfigured
  `reprovision`. Page fleet on-call before touching the data.
- **Anchored by a `compliance_bundles` chain that's still receiving new
  rows.** That means an appliance is actively producing evidence under the
  orphan site — it's not actually dead. Investigate before migrating.

In all three cases: capture a snapshot of `execution_telemetry`,
`compliance_bundles`, and `site_appliances` for the orphan site_id before
any cleanup. The append-only audit trail is the only forensic surface.

## Related runbooks

- `appliance_moved_unack.md` — relocate didn't complete
- `evidence_chain_stalled.md` — same class of "fleet looks live but writes
  aren't landing where expected"
- `flywheel_ledger_stalled.md` — promotion ledger writes failing for a
  different reason

## Change log

- 2026-04-28 — created — F3 from flywheel round-table verdict; pairs with
  migration 255 (orphan relocate) and the new `PhantomSiteRolloutError`
  precondition (F2).

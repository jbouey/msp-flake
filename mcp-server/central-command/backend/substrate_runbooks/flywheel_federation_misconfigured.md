# flywheel_federation_misconfigured

**Severity:** sev3
**Display name:** Federation flag is ON but no tier is enabled+calibrated

## What this means (plain English)

The mcp-server container has `FLYWHEEL_FEDERATION_ENABLED` set to a
truthy value (`true`/`1`/`yes`/`on`), but no row in
`flywheel_eligibility_tiers` has both `enabled=TRUE` AND
`calibrated_at IS NOT NULL`. This is the F6 federation tier "two
switches" defense-in-depth: env flag + per-tier kill switch +
calibration timestamp must all align for federation to actually
activate. When any of those is missing, the flywheel read path
falls back to the original hardcoded thresholds (5, 0.90, 3, 7
days) and emits a `logger.warning` per 30-min loop tick.

Production behavior is **unchanged** — the fallback is the same
behavior as if the env flag were unset. The invariant fires sev3
to flag the visibility gap, not because anything is broken.

## Root cause categories

- **Operator flipped the env flag intending to enable federation
  but forgot to run the calibration migration.** Most common cause.
- **Calibration migration ran but flipped the wrong tier.** Less
  common; check `tier_state` in the violation details.
- **Calibration migration was rolled back without flipping the env
  var off.** Rare; should be caught by the deploy workflow but worth
  checking.
- **Two operators in flight simultaneously** — one set the env var,
  the other is mid-calibration. Wait for the calibration to land.

## Immediate action

Check the violation details for the current state:

```sql
SELECT tier_name, tier_level, enabled, calibrated_at,
       min_total_occurrences, min_success_rate
  FROM flywheel_eligibility_tiers
 ORDER BY tier_level;
```

Then decide which path is the operator's true intent:

**Path A — federation should be OFF.** Unset the env var on mcp-server
and restart:

```
ssh root@<vps> "docker exec mcp-server printenv FLYWHEEL_FEDERATION_ENABLED"
# If set, edit /opt/mcp-server/docker-compose.yml or .env to remove,
# then `docker compose restart mcp-server`.
```

The substrate invariant clears on the next 60s tick once the env
var is unset and the container reloads.

**Path B — federation SHOULD be ON.**

> **NOTE: As of 2026-04-30, no calibration migration has shipped yet.**
> If you're reading this and the substrate fired this invariant, the
> operator who set the env var got ahead of the calibration work. See
> `docs/specs/2026-04-30-f6-federation-eligibility-tier-design.md`
> § "Phase 2" for the full scoping. Until that work lands, Path A
> (unset the env var) is the correct resolution.

Once the calibration migration exists, run it. The calibration
migration must:

1. Be authored after the 2-3 week observation window completes.
2. Be round-table reviewed (HIPAA + threshold values + cross-org
   isolation for tiers >= 1).
3. Set thresholds based on real production data, not seed
   placeholders.
4. Audit-log the calibration in `admin_audit_log` with action
   `substrate.flywheel_federation_calibration.completed`.

See `docs/specs/2026-04-30-f6-federation-eligibility-tier-design.md`
for the full design.

## Verification

- Panel: invariant row should clear on the next 60s tick after the
  remediation lands.
- CLI: re-run the same query the invariant uses:

  ```sql
  SELECT COUNT(*)
    FROM flywheel_eligibility_tiers
   WHERE enabled = TRUE
     AND calibrated_at IS NOT NULL;
  ```

  Path A: env var should be unset (`docker exec mcp-server printenv
  FLYWHEEL_FEDERATION_ENABLED` returns empty). Invariant clears
  because the env-var check at the top of the assertion returns
  no-violation.

  Path B: query above returns ≥ 1. Invariant clears because the
  active_count check returns ≥ 1.

## Escalation

Sev3 — no behavior degradation. **Do not page on this.** It surfaces
in the daily substrate-health digest.

If the invariant has been firing for >7 days, that's a signal the
operator who set the env var either forgot or didn't follow through
on calibration. Escalate to round-table for "is federation actually
the right path here, or should we revert."

## Related runbooks

- `docs/specs/2026-04-30-f6-federation-eligibility-tier-design.md` —
  full F6 design, including phase 2 + phase 3 scoping.
- (none — this is a meta invariant about an in-flight feature; no
  operational substrate runbooks share its class)

## Change log

- 2026-04-30 — created — F6 fast-follow from MVP slice round-table.
  Closes the operator-visibility gap on the federation
  misconfiguration class.

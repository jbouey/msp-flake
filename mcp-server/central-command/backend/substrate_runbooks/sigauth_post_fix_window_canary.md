# sigauth_post_fix_window_canary

**Severity:** sev1
**Display name:** Sigauth wrap-fix post-deploy canary tripped

## What this means (plain English)

The wrap-in-transaction fix shipped in commit `303421cc`
(2026-04-28) was a SPECULATIVE fix — leading hypothesis was
PgBouncer transaction-pool routing splitting `SET app.is_admin`
from the subsequent `_resolve_pubkey()` SELECT, but no forensic
event was captured to confirm the mechanism. The rationale doc
(`docs/security/sigauth-wrap-rationale-2026-04-28.md`) records
the override that authorized shipping despite that.

The acceptance criterion was a 7-day empirical-clean window:
substrate stays clean from deploy (2026-04-28 17:11Z) through
2026-05-05 17:11Z. Task #169 was closed EARLY on user override
on 2026-04-28 22:51Z (~5h40m post-deploy) with this canary as
the compensating control.

This canary fires sev1 on ANY invalid sigauth observation across
any appliance during the 7d window. It is tighter than the
rolling-6h `sigauth_enforce_mode_rejections` invariant because
it counts a single fail across the entire window, not just the
last 6 hours.

If this fires, the routing hypothesis is **empirically refuted**.
The wrap-fix did not address the actual root cause.

## Root cause categories (what to investigate when this fires)

These are the alternative hypotheses to PgBouncer routing,
ranked by validation-doc preference:

- **MVCC tuple chain depth.** site_appliances rows for high-
  traffic appliances accumulate dead tuples between vacuum runs.
  A SELECT with a snapshot from before STEP 3.6c's commit could
  see no row even though the row "exists." Check `pg_stat_user_tables`
  for n_dead_tup vs n_live_tup on site_appliances; if dead is
  significant, autovacuum tuning is the next step.
- **`deleted_at` flicker.** Some background worker briefly sets
  deleted_at on the row (e.g. an erroneous health-check that
  marks the appliance "decommissioned" then reverts). Check
  admin_audit_log for any update_site_appliance entries near
  the canary fire timestamp.
- **autovacuum stuck.** A long-running vacuum holds a snapshot
  that prevents the visibility map from updating. `pg_stat_activity`
  WHERE backend_type='autovacuum worker' will show this.

## Immediate action

- **DO NOT ship another fix.** This is the explicit pivot
  trigger; new fix requires a fresh round-table per the rationale
  doc.

- **Capture the forensic context.** The
  `logger.error("sigauth_unknown_pubkey", ...)` line should have
  fired with the rejection. Pull it:
  ```
  ssh root@178.156.162.116 'docker logs --since=24h mcp-server \
    2>&1 | grep sigauth_unknown_pubkey'
  ```
  The `signature_enforcement_mode` extra carries enforce/observe.
  If `enforce` → the wrap-fix is wrong. If `observe` → an appliance
  was demoted in the window and the rejection is informational
  (still investigate but don't pivot).

- **Re-open task #169.** Add a comment with the canary fire
  timestamp + the forensic log excerpt + which alternative
  hypothesis (MVCC / deleted_at / autovacuum) the evidence
  points to.

- **Trigger new round-table.** The pivot decision (which
  alternative hypothesis to test first) needs second-eye
  consensus per the discipline established in session 212.

## Verification

This canary auto-clears when:
- The fail count in the window drops to zero (won't happen for
  the firing observation — it's append-only)
- OR the window closes (`observed_at < '2026-05-05 17:11:00+00'`)
  and no rows fall inside it. After 2026-05-05 17:11Z UTC the
  invariant produces zero violations regardless of substrate
  state. The invariant should be REMOVED from `ALL_ASSERTIONS`
  in the next session after that date.

## Escalation

If this canary fires AT THE SAME TIME as
`sigauth_enforce_mode_rejections` AND the forensic log shows
`signature_enforcement_mode='enforce'` — that's the strongest
possible refutation of the routing hypothesis. Roll the wrap-fix
back to its pre-303421cc state OR consider the wrap-fix harmless
but inadequate; the actual mechanism is one of the alternative
hypotheses.

## Related runbooks

- `sigauth_enforce_mode_rejections` — the parent invariant; this
  canary is the tighter detection floor for the post-deploy
  acceptance window
- `signature_verification_failures` — broadest umbrella signal

## Change log

- 2026-04-28 — created — round-table 2026-04-28 P1 close-out for
  task #169 user-override early-close. Will be removed from
  ALL_ASSERTIONS after 2026-05-05 17:11Z if the canary stays
  silent through the window.

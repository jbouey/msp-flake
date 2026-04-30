# F6 — Federation tier for eligibility thresholds (design spec)

**Status:** DESIGN — implementation is multi-day, deliberately deferred.
**Round-table source:** Session 213 (2026-04-29) flywheel architecture
review, P3 finding.
**Owner / next pickup:** TBD — needs scoping session before any code.

---

## Problem

Promotion eligibility for L2-discovered patterns is currently a single
flat threshold:

```
total_occurrences >= 5
AND success_rate    >= 0.90
AND l2_resolutions  >= 3
AND last_seen > NOW() - INTERVAL '7 days'
```

(See `mcp-server/main.py` Step 2 of `_flywheel_promotion_loop`.)

Two failure modes from this flatness, observed but not yet hard-cased:

1. **Cross-org under-promotion.** A pattern that has clearly graduated
   at the platform tier (`platform_pattern_stats.distinct_orgs >= 5`,
   `success_rate >= 0.90` aggregated across 50 sites) doesn't promote
   to a NEW site that hasn't yet hit `total_occurrences >= 5` locally.
   The new site has to recapitulate the learning curve every time —
   wasted incidents.

2. **Per-org over-promotion.** A pattern that's been validated against
   one large customer's environment may not generalize. The current
   thresholds don't differentiate between "5 sites in one org all
   showing the same thing" (likely environmental coupling) vs "5 sites
   across 5 orgs" (true generalization signal).

## Proposed federation tier

Introduce three eligibility tiers, evaluated in order of strength:

```
Tier 0 — local      : current thresholds (per-site)
Tier 1 — org        : aggregated across sites in same client_org_id
Tier 2 — platform   : aggregated across all orgs (already exists in
                      platform_pattern_stats — would just be wired in)
```

A new site benefits from Tier 1/Tier 2 promotion BEFORE it has
generated enough local telemetry to clear Tier 0 — provided the
higher-tier thresholds are met. This closes failure mode (1).

For failure mode (2), Tier 2 (platform) requires `distinct_orgs >= 3`
explicitly, separating "one org" from "cross-org generalization".

## Schema sketch

```sql
CREATE TABLE flywheel_eligibility_tiers (
    tier_name    TEXT PRIMARY KEY,
    tier_level   INTEGER NOT NULL UNIQUE,  -- 0=local, 1=org, 2=platform
    min_total_occurrences  INTEGER NOT NULL,
    min_success_rate       FLOAT NOT NULL,
    min_l2_resolutions     INTEGER NOT NULL,
    max_age_days           INTEGER NOT NULL,
    min_distinct_orgs      INTEGER,  -- only meaningful at Tier 2
    min_distinct_sites     INTEGER,  -- only meaningful at Tier 1+
    description            TEXT NOT NULL
);
```

Seed rows:

| tier_level | name | min_occ | min_rate | min_l2 | max_age | distinct_orgs | distinct_sites |
|---|---|---|---|---|---|---|---|
| 0 | local | 5 | 0.90 | 3 | 7 | — | 1 |
| 1 | org | 15 | 0.90 | 5 | 14 | — | 3 |
| 2 | platform | 50 | 0.95 | 10 | 30 | 3 | 5 |

(Numbers TBD — calibrate against current production telemetry before
shipping. Probably need 2–3 weeks of observation data.)

## Round-table risks to design out

(Capturing now so the future round-table doesn't re-discover them)

1. **Promotion-loop infinite-recurse.** If a Tier 2 promotion causes
   a Tier 1 trigger somewhere (because the L1 rule starts firing in
   another site), and that trips Tier 1 → does the loop terminate?
   Answer: yes — promotion writes to `promoted_rules` not to
   `aggregated_pattern_stats`, so the eligibility query is read-only
   against the source telemetry. But the design must be explicit.

2. **Org-boundary leak.** A Tier 1 promotion to Org A based on Org B's
   telemetry would be a HIPAA cross-org disclosure. The eligibility
   query at Tier 1 MUST filter by `client_org_id = $caller_org`. The
   query at Tier 2 may aggregate across orgs (the platform exists for
   that) but the PROMOTED rule must only roll out to sites in scope.

3. **Phantom-promotion class re-emergence.** F2's
   `PhantomSiteRolloutError` precondition gates rollout to dead sites.
   With federation, a Tier 2 promotion fires for sites that have NEVER
   seen the pattern locally — the precondition still holds (we check
   for live appliances, not for prior local exposure), but the
   round-table should re-verify.

4. **`_rename_site_immutable_tables()` extension.** Federation
   introduces the new `flywheel_eligibility_tiers` table — operational
   config, NOT audit-class. Should NOT be in the immutable list. The
   substrate invariant `rename_site_immutable_list_drift` won't fire
   on it because it lacks a DELETE-blocking trigger. Confirmed safe.

## Operator surface

`GET /api/admin/sites/{site_id}/flywheel-diagnostic` (F7) should be
extended to report which tier(s) each promotion candidate clears, so
an operator investigating "why didn't this promote" gets a structured
answer instead of a guess.

## Test surface (when implemented)

- Tier-resolution tests: a candidate clearing Tier 1 but not Tier 0
  should promote at Tier 1, NOT skip.
- Cross-org isolation: Tier 1 query MUST filter by client_org_id.
- Platform-tier guard: Tier 2 requires `distinct_orgs >= 3`.
- Three-list lockstep: any new lifecycle event_type for tier
  promotions goes through the existing flywheel spine pattern (mig
  181 + flywheel_state.py) — not a parallel ledger.

## Estimated effort

- 3-5 days end-to-end including round-table iterations.
- Calibration window before threshold values are locked: 2-3 weeks
  of observation data.
- Should ship behind a feature flag (`FLYWHEEL_FEDERATION_ENABLED`)
  with explicit shadow-mode → enforce-mode cutover (mirror the
  Session 209 orchestrator playbook).

---

## Why this is a separate session

Session 213 closed three flywheel bug classes architecturally
(eligibility-fragmentation, phantom-promotion, recreation-cycle).
F6 is a CAPABILITY addition, not a fix — different posture, different
risk profile, different round-table angles (data-policy + cross-org
HIPAA + threshold calibration). Bundling it with same-session
hotfixes would dilute both. Spec lives here; implementation is
queued for a dedicated session with explicit scoping.

## Status: 2026-05-05 sigauth watch

Independent of F6 — passive monitoring through 2026-05-05 17:11Z.
Auto-reopens task #169 if `sigauth_enforce_mode_rejections` or
`sigauth_post_fix_window_canary` fires. After 2026-05-05 if both
stayed silent, REMOVE `sigauth_post_fix_window_canary` from
`ALL_ASSERTIONS`. See `.agent/claude-progress.json::scheduled_followups[0]`.

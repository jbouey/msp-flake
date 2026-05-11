# Phase 4 Coach Review — Substrate-MTTR Soak

Reviewed artifacts:
- `.agent/plans/24-substrate-mttr-soak-2026-05-11.md`
- `mcp-server/central-command/backend/migrations/303_substrate_mttr_soak.sql` (already applied to prod)
- `scripts/substrate_mttr_soak_inject.py`
- `scripts/substrate_mttr_soak_report.py`

## Verdict

**BLOCK** — this is not a 1h-smoke-and-fix situation. The design has three
independent dealbreakers: (a) the loop the soak claims to measure is not
actually exercised by the synthetic incidents, (b) the analyzer cannot
distinguish a broken engine from a working one because the auto-resolve
window upper-bounds every measurement, and (c) production-DB-applied
migration 303 has already begun contaminating real operator surfaces
(admin `/api/fleet` and the recurrence/federation flywheels) and there is
NO query-path filter coverage as the design doc promised. Production is
live with a synthetic clinic visible to admins right now.

Migration 303 is applied. The synthetic `client_org`, `site`, and
`substrate_mttr_soak_runs` table all exist in prod. Reversing or
quarantining them is now part of the cleanup before any soak can run.

---

## P0 findings (must fix before any run, including the 1h smoke)

### P0-1. The substrate engine fires NO invariant on raw `incidents` rows. The test cannot detect what it claims to test.
- File: `mcp-server/central-command/backend/assertions.py`
- The only invariant that touches `incidents` is `_check_l2_resolution_without_decision_record` (line 1100-1154), which fires on `resolution_tier='L2' AND status='resolved' AND NOT EXISTS l2_decisions`. The injector sets `resolution_tier='monitoring'` (`scripts/substrate_mttr_soak_inject.py:167`), so this invariant does NOT fire on synthetic incidents.
- No other invariant in `assertions.py` enumerates `FROM incidents` (verified via `grep "FROM incidents " assertions.py` → 1 hit, line 1126).
- Consequence: the substrate engine creates ZERO `substrate_violations` rows in response to a soak injection. The design doc (lines 64-69) claim "Substrate engine sees them, fires invariant, creates substrate_violations row" is FALSE.
- The analyzer's measured `resolved_at - reported_at` is therefore measuring **`injector.UPDATE NOW() - injector.INSERT NOW()`** — the injector's own scheduling latency, not the substrate engine. The result is bounded above by `RESOLUTION_WINDOW_SECONDS` (10min for sev1, 30min for sev2, 4h for sev3) and bounded below by tick granularity (60s).
- Fix: either (a) reframe Phase 4 as "incident-resolution latency soak, not substrate-MTTR soak" (and rename the doc + scripts), OR (b) inject something the substrate engine ACTUALLY observes — e.g., write a synthetic `substrate_violations` row directly, or seed a known-firing condition (orphaned L2 decision, stuck order, etc.). The current design measures the wrong thing.

### P0-2. Auto-resolve window mathematically bounds every measurement below the SLA. P99 can never exceed the resolution window even if the engine is dead.
- File: `scripts/substrate_mttr_soak_inject.py:48-53` + `:149-181`
- `RESOLUTION_WINDOW_SECONDS = {critical: 600, medium: 1800, low: 14400}` and `_resolve_expired_incidents` is called every 60s tick, UPDATEing every open incident past its window.
- `SLA_HOURS` in the analyzer (`scripts/substrate_mttr_soak_report.py:27-32`): sev1=4h, sev2=24h, sev3=720h. The resolution window is 24×–180× SMALLER than the SLA window. So P99 is mechanically pinned to ≤ window + 60s tick granularity.
- The report will always show "P99 ≤ SLA → ✅" by construction. A green report does not imply the loop holds; it implies the resolver fired on schedule. A red report (P99 > resolution_window + ε) would indicate the INJECTOR is broken, not the substrate engine.
- Fix: drop the auto-resolution path entirely and let the actual healing-tier pipeline (or operator ack) resolve. If the goal is to NOT exercise the healing tier, then the metric is not MTTR — it's "did we see the alert appear". Measure `substrate_violations.last_seen_at - incidents.reported_at` (i.e., detect latency), not `resolved_at - reported_at` (i.e., the artificial close).

### P0-3. The promised `SUBSTRATE_ALERT_SOAK_SUPPRESS` env does not exist in code. Sev1 + sev2 soak alerts WILL email real operators.
- Design doc lines 42-45 + 132 say a per-LABEL env override drops alerts with `labels.soak_test='true'`.
- `grep -rn "SOAK_SUPPRESS\|soak_test" mcp-server/central-command/backend/` → only references are in migration 303 (the partial index). `alertmanager_webhook.py` has NO soak filter (verified — only `SUBSTRATE_ALERT_MIN_SEVERITY` exists at line 98).
- Sev1 + sev2 soak alerts pass the default `min_sev=sev2` cutoff and email `ALERTMANAGER_RECIPIENTS`. With 24/24h sev1 + 120/24h sev2 = 144 paging events in 24h → operator inbox flood + real-alert masking.
- Compounds with P0-1: those alerts won't actually fire because the substrate engine doesn't open violations on synthetic incidents — but if P0-1 is "fixed" by directly seeding violations, P0-3 immediately bites. They must be fixed together.
- Fix: implement `SUBSTRATE_ALERT_SOAK_SUPPRESS` in `alertmanager_webhook.py` BEFORE running any soak. Suggested patch (after the severity filter, around line 108):
  ```python
  if (os.getenv("SUBSTRATE_ALERT_SOAK_SUPPRESS") or "").lower() == "true":
      pre_soak = len(alerts)
      alerts = [a for a in alerts
                if (a.get("labels", {}).get("soak_test") or "").lower() != "true"]
      dropped_soak = pre_soak - len(alerts)
      if dropped_soak:
          logger.info("alertmanager_soak_suppressed", extra={"dropped": dropped_soak})
  ```
  And the injector must add `labels.soak_test='true'` to whatever it injects into the alert path (currently it injects only into `incidents.details`, which doesn't propagate to Alertmanager labels).

### P0-4. Migration 303's synthetic site appears on `/api/fleet` admin dashboard and on `/admin/metrics` trending — production contamination is live now.
- `mcp-server/central-command/backend/routes.py:138-175` (`GET /api/fleet`): selects `FROM sites s … WHERE s.status != 'inactive'`. The synthetic site has `status='online'` (mig 303 line 52). Every admin/operator who loads the fleet view will see a row "MTTR Soak Synthetic" with org name "SYNTHETIC-mttr-soak (substrate validation, NOT a real customer)".
- `mcp-server/central-command/backend/routes.py:2120-2143`: `SELECT FROM incidents WHERE created_at > NOW() - make_interval(days => $1)` — when 624 synthetic incidents land, the admin incidents-per-day graph inflates 5×–10× over real traffic.
- The design doc lines 36-39 promised "Substrate invariants, scoring queries, and customer-facing reports all filter on `details->>'soak_test' != 'true'`. (One-line WHERE clause added per query path.)" Zero such filters have been added.
- Fix options:
  1. Hard-quarantine the synthetic site at the admin layer too: `WHERE s.site_id != 'synthetic-mttr-soak'` in `routes.py:151`, the trending query at `:2127-2134`, and every other admin enumeration. Easier to audit than the per-incident `soak_test` predicate.
  2. Add a `sites.synthetic BOOLEAN DEFAULT false` column in a migration 304, set true for the soak site, and filter `WHERE NOT s.synthetic` everywhere. Survives if the site_id ever gets renamed.
- Either way, write the CI gate (design doc deliverable #4 `test_mttr_soak_filter_universality.py`) BEFORE the next run. Without it, every future endpoint will leak.

### P0-5. The data-flywheel `incident_recurrence_velocity` task WILL ingest soak data and mark `is_chronic=true` for the synthetic site.
- `mcp-server/central-command/backend/background_tasks.py:1149-1182` (`recurrence_velocity_loop`): unconditional `FROM incidents WHERE status='resolved' AND resolved_at > NOW() - INTERVAL '7 days' GROUP BY site_id, incident_type`. No soak filter.
- After 24h: synthetic site gets 4 rows in `incident_recurrence_velocity` — `(synthetic-mttr-soak, ransomware_indicator, resolved_4h=24, velocity=6/hr, is_chronic=true)` and three siblings. Persists 7 days.
- `recurrence_auto_promotion_loop` (background_tasks.py:1192+) then sees `is_chronic=true` and tries to auto-promote L1 rules from L2 decisions — but those L2 decisions DON'T EXIST for soak incidents (auto-resolution is "monitoring", not "L2"), so promotion likely no-ops. Still: production-side stats table corrupted with soak data.
- Steve counter-mitigation in design doc lines 113-117 ("ALL soak data filtered by `details->>'soak_test'='true'` on flywheel ingest queries") is not implemented.
- Fix: either filter in `recurrence_velocity_loop` ON the JOIN (`AND (i.details->>'soak_test' IS NULL OR i.details->>'soak_test' != 'true')`), or hard-exclude `WHERE i.site_id != 'synthetic-mttr-soak'`. The latter is simpler and survives JSONB mutation.

### P0-6. Flywheel federation tier-org enumeration includes the synthetic org.
- `mcp-server/central-command/backend/flywheel_federation_admin.py:198-205`: `SELECT DISTINCT s.client_org_id FROM sites s WHERE s.client_org_id IS NOT NULL AND s.status != 'inactive'`. Synthetic site matches both predicates.
- The synthetic client_org `00000000-...-ff04` becomes a federation candidate. If federation auto-promotes a rule based on a soak-induced pattern, the synthetic org "votes" alongside real customers.
- The design doc's Maya mitigation (line 122 "synthetic site_id is NOT in any auditor-kit org mapping") understates the surface: it's in the FEDERATION mapping too.
- Fix: same as P0-5 — exclude `synthetic-mttr-soak` at the source query.

---

## P1 findings (should fix before 24h run; OK for 1h smoke ONLY after P0s are fixed)

### P1-1. `--resume-run-id` does not actually resume — it re-injects from scratch.
- `scripts/substrate_mttr_soak_inject.py:208-213`: on resume, sets `soak_run_id = cfg.resume_run_id` but tick counter restarts at 0 and end-time is recomputed from `now + duration_hours`. The injector does not consult any persisted in-flight state.
- After an 8h container restart on a 24h run, the script will inject another 24h of incidents on top of the existing ones, double-billing the synthetic site.
- Fix: either remove the flag (advertise it as unimplemented) or persist `last_tick` to `substrate_mttr_soak_runs.config->>'last_tick_at'` every tick and compute remaining duration from `started_at + duration_hours - now`.

### P1-2. Analyzer's `high` severity bucket always reports `count=0` because the injector never injects `high`.
- `scripts/substrate_mttr_soak_inject.py:248-251` injection loop iterates `("critical", "medium", "low")` — no "high".
- `scripts/substrate_mttr_soak_report.py:86` iterates `("critical", "high", "medium", "low")` and reports a row for `high` with `count=0`. The verdict cell shows `⚠️` (sla_met_p99 is None). This is a visible bug in the output table.
- Fix: drop `high` from the analyzer's iteration order, OR add `("high", "high", cfg.sev2_per_hour // 2)` to the injection loop.

### P1-3. Analyzer does not measure detect-latency despite design doc deliverable saying it must.
- Design doc line 60-62 lists `detected_at = first substrate_violations row referencing this incident` and `alerted_at = first alertmanager_webhook entry` as REQUIRED per-incident measurements.
- `scripts/substrate_mttr_soak_report.py:70-73` admits: "For v1 we use simpler measurement: resolution_at - reported_at = end-to-end injector latency (NOT substrate-only). Future hardening: join substrate_violations for true detect-time."
- Phase 4's stated deliverable is detect-latency. The analyzer ships only end-to-end. Either downscope the deliverable in the doc or implement the JOIN.
- Compounds with P0-1: even if implemented, there ARE no substrate_violations rows pointing at soak incidents (no invariant correlates incidents → violations), so the JOIN would return empty.

### P1-4. Failure-mode → measurement mapping is missing.
- Design doc lines 92-99 list six failure modes the soak must detect ("substrate misses detection", "alertmanager drops alert", "email fails", etc).
- The analyzer outputs only P50/P95/P99/open_count per severity. Given a red P99, the report cannot distinguish "alertmanager dropped the alert" from "substrate engine missed detection" from "email SMTP outage". All six failure modes collapse to the same MTTR-too-high signal.
- Fix: the analyzer needs to query `substrate_violations` per-incident (failure mode 1, 2, 6), Alertmanager-receiver logs or `email_alerts` audit (failure mode 3, 4), and incident-state-history (failure mode 5). Each failure-mode gets its own count in the report.

### P1-5. Sev3 paging-tier verification is impossible in this design — the alertmanager filter (Session 219) drops sev3 before the email channel.
- Design doc scope item: "Verify the alertmanager_webhook severity filter correctly suppresses sev3 paging."
- `alertmanager_webhook.py:98` defaults `SUBSTRATE_ALERT_MIN_SEVERITY=sev2`. Sev3 alerts return `{"sent": false, "reason": "all_below_severity_cutoff"}`.
- Sev3 soak incidents (480/24h, the majority of the load) never traverse the email path. The soak cannot measure email-tier latency for sev3 because the filter EXISTS by design.
- The analyzer's sev3 SLA verdict (`p99 ≤ 30 × 24 × 60 minutes`) is checking the auto-resolution loop, not the alert loop. Marking sev3 ✅ here proves nothing about the alert filter.
- Fix: either separate the sev3 test (run a dedicated 1h sev3-only soak with `SUBSTRATE_ALERT_MIN_SEVERITY=sev3` to confirm sev3 emails DO route when override is set), or remove the sev3 SLA verdict from this soak entirely.

### P1-6. `_resolve_expired_incidents` writes `resolution_tier='monitoring'` without `_send_operator_alert` chain-gap escalation.
- CLAUDE.md mandate (Session 216): "Every operator-visibility hook that follows an Ed25519 attestation MUST escalate severity to P0-CHAIN-GAP + append [ATTESTATION-MISSING]".
- The injector bypasses the standard incident-resolution path (which lives in agent_api.py + main.py and goes through the Flywheel Spine event ledger). It UPDATEs incidents directly, leaving NO `flywheel_events` row, NO attestation, NO L2/L1 decision record.
- Compounds with the L2-orphan invariant (P0-1): because `resolution_tier='monitoring'` is used and not `'L2'`, the orphan check happens to NOT fire — but this is a coincidence. Any future invariant that fires on "resolved without flywheel_events" would page on all 624 soak incidents.
- Fix: route through the normal incident-resolution path, OR mark `resolution_tier='auto_recovered'` (mig 264 explicitly carved this out for non-attested closures), OR add a sentinel `details->>'soak_synthetic_resolution'='true'` and add a carve-out to every relevant invariant. Each fix carries risk; the cheapest is `auto_recovered` IF future invariants accept that tier as a chain-gap-exempt closure.

### P1-7. Injector dead code at line 239-244 is misleading.
- Three `target_sev*` calculations + a `pass` statement + a comment "wrong, simpler". Looks like an incomplete refactor.
- Either delete the dead code or comment it as historical-context. As-is, a reviewer cannot tell whether these targets were intended to be wired in.

### P1-8. The `dry-run` mode is degenerate.
- `scripts/substrate_mttr_soak_inject.py:215-223`: inserts then immediately DELETEs three incidents. This skips the resolution path, skips the substrate-tick observation window, and skips alert-routing. It validates ONLY the INSERT statement (which `_inject_one` already proves by virtue of `ON CONFLICT` not raising).
- Recommend: dry-run injects one of each severity, waits ~120s for substrate engine to tick at least once, then asserts a corresponding `substrate_violations` row exists. Otherwise dry-run is theater.

---

## P2 findings (nice to have)

### P2-1. Timing drift over 24h.
- `while …: sleep(60)` after ~6 SQL calls per tick. On loaded DB, each tick takes 60.2-60.5s. Over 1440 ticks (24h), drift accumulates to ~5-10 min. Inject counts drop slightly below target (e.g. sev3 might fire 470 not 480). Not fatal; flag it in the report so we don't chase phantom rate-loss.
- Fix: target end-time-anchored sleep: `await asyncio.sleep(max(0, next_tick_at - now))`.

### P2-2. Migration 303 inserts an `admin_audit_log` row with `username='system:mig-303'`.
- CLAUDE.md privileged-chain rule: "Never log actor as system/fleet-cli/admin — actor MUST be a named human email."
- This is a migration-time audit row, not a privileged-fleet-order audit row, so the rule is borderline-inapplicable. But CI gates that scan `admin_audit_log` for non-email actors will flag it. Recommend `username='jeff+migration-303@osiriscare.com'` (the operator who applied the migration) or whatever the gate accepts.

### P2-3. `_ensure_synthetic_appliance` doesn't set `deleted_at` and uses bogus MAC `00:00:00:00:00:00`.
- Per CLAUDE.md (Session 218 "Portal site_appliances + sites filters"): every portal query must filter `sa.deleted_at IS NULL`. The synthetic appliance has `deleted_at=NULL` — so it WILL appear in any admin portal that doesn't exclude `synthetic-mttr-soak`. Compounds with P0-4.
- The MAC `00:00:00:00:00:00` is a real IEEE-reserved address. If any uniqueness gate exists on `mac_address` and any other synthetic-test row ever uses the same, collision. Use `02:00:00:00:00:01` (locally-administered prefix) or a deterministic-but-unique synthetic MAC.

### P2-4. Resolution-window definition uses string-keyed severity that mismatches the analyzer.
- Injector key set: `{low, medium, high, critical}` (`scripts/substrate_mttr_soak_inject.py:48-53`).
- Analyzer SLA key set: `{critical, high, medium, low}` (`scripts/substrate_mttr_soak_report.py:27-32`).
- Sev3 is `low` (4h window) but the SLA is 30 days. Sev2 is `medium`/`high` (30min window) but SLA is 24h. Sev1 is `critical` (10min window) but SLA is 4h.
- These map correctly but neither file documents the severity→sev-tier mapping explicitly. A reviewer cannot tell at a glance that `low → sev3 → SLA=720h`. Add a single header-comment table in BOTH files.

### P2-5. Idempotency holes in migration 303.
- The `INSERT INTO admin_audit_log` is unconditional (no `WHERE NOT EXISTS`). Re-running migration 303 (e.g., via `migrate.py --replay`) creates duplicate audit rows.
- Either guard with `WHERE NOT EXISTS (SELECT 1 FROM admin_audit_log WHERE action='substrate_mttr_soak_setup' AND details->>'migration'='303_substrate_mttr_soak')` or accept that audit rows are append-only and noise is OK.

---

## Adversarial-review per lens

### Steve (Principal SWE)
- Container-restart resume: P1-1 — `--resume-run-id` is non-functional.
- Cadence variance: P2-1 — measurable but not fatal.
- SQL parameterization: clean. All queries use `$N` placeholders; no f-string interpolation of user input. `route` in cadence loop is from argparse int, not from DB.
- Engine-vs-injector race: P0-1 / P0-2 — the substrate engine is not racing because it's not participating. The injector races itself only.
- `resolution_tier='monitoring'`: legal per mig 106/264 CHECK constraint. Does not trigger any L1/L2 invariant today (P0-1 confirmed).

### Maya (Legal / Compliance)
- 624 synthetic incidents in `incidents` table: P0-4, P0-5, P0-6 — they surface on admin `/api/fleet`, `/admin/metrics` trending, `incident_recurrence_velocity`, and federation tier-org enumeration. Customer-facing surfaces (client/partner portals) appear safe because synthetic site has no partner_id and no real-customer org mapping, BUT admin and federation surfaces are contaminated NOW.
- Auditor-kit: NOT directly at risk (compliance_bundles requires real evidence events, none are generated for synthetic site). The design doc's stated reason ("client_org_id IS NOT NULL filter") is WRONG — synthetic IS NOT NULL. The actual safety comes from "no real appliance generates bundles for synthetic site". Document the right invariant.
- Severity='critical' email page-flood: P0-3 — `SUBSTRATE_ALERT_SOAK_SUPPRESS` is unimplemented; 144 sev1+sev2 paging events per 24h would hit operator inboxes if any substrate violation row WERE created (currently masked by P0-1).
- Audit-log row marking: P2-2 — `username='system:mig-303'` borderline-violates the "named human email" rule.

### Carol (Security / HIPAA)
- `soak-test@example.invalid`: `.invalid` is RFC 6761 reserved and guaranteed not to resolve. Safe — no real email path can deliver to it.
- RLS posture: synthetic `client_orgs` row has no `current_partner_id`. Partner-portal RLS won't surface it. If a future `cross_org_site_relocate` accidentally binds this org to a real partner_id, real-customer policies would gate on synthetic state — but that's a hypothetical operator error, not a current vulnerability.
- Ed25519 evidence chain: synthetic appliance has no per-appliance `agent_public_key`, no `compliance_bundles` rows, no chain entries. Real-site chains at the same Mac-keyed appliance: NONE — synthetic MAC is `00:00:00:00:00:00` (P2-3 noted). Chain integrity untouched.
- Privileged-chain (mig 175): synthetic incident_types (`ransomware_indicator`, `backup_not_configured`, `patching_drift`, `informational_audit`) are NOT in `PRIVILEGED_ORDER_TYPES` (those are fleet_orders types like `enable_emergency_access`). No attestation_bundle_id requirement. Clean.

### Coach (Consistency)
- Failure-mode → measurement: P1-4 — 6 failure modes in design doc, 1 metric in analyzer. The analyzer cannot tell which failure mode fired given a red P99.
- Detect-latency: P1-3 — design doc deliverable says required; analyzer says "deferred". Update the doc or implement.
- SLA-vs-resolution-window inversion: P0-2 — the whole test is a bounded-above measurement of the injector itself. This is THE single most important finding. The test cannot fail in any way that surfaces a real engine problem.
- Sev3 path coverage: P1-5 — alertmanager filter drops sev3 by design; soak's sev3 measurements measure the filter, not the loop.
- `--resume-run-id` truthiness: P1-1 — flag exists, behavior doesn't.
- Severity-key parallelism: P2-4 — injector and analyzer use the same labels but neither documents the mapping.
- Filter-coverage CI gate (deliverable #4): unimplemented. The plan promises it. Until it exists, every new endpoint silently re-leaks soak data.

---

## What the design got right

- Using a deterministic UUID + idempotent UPSERTs for the synthetic `client_orgs` + `sites` rows: re-running mig 303 is safe.
- Partial index `idx_incidents_soak_test` with `WHERE details->>'soak_test'='true'`: cheap, doesn't affect non-soak query plans.
- Choosing `example.invalid` for the synthetic primary email (RFC 6761).
- Recognizing in the design doc that this is "5-10× real production load" — the rate-multiplier framing is honest.
- Steve/Maya/Carol counter-arguments section in the design doc: shows the author tried to anticipate objections, which is the right shape. They were just incomplete (above).

---

## Recommended next step

Do NOT run the 1h smoke as currently written. The order of operations is:

1. **Revert or quarantine migration 303 in prod** (drop `synthetic-mttr-soak` from `sites`, drop synthetic `client_orgs` row, drop `substrate_mttr_soak_runs` table) OR ship the admin/federation/flywheel filter patches FIRST.
2. Implement `SUBSTRATE_ALERT_SOAK_SUPPRESS` in `alertmanager_webhook.py` (P0-3).
3. Decide what the soak is actually measuring: substrate engine detect latency OR injector self-latency. Reframe scripts + doc accordingly (P0-1).
4. Drop the auto-resolution path OR change the metric to detect-latency (P0-2).
5. Add the `test_mttr_soak_filter_universality.py` CI gate (deliverable #4, currently unimplemented).
6. Re-round-table.

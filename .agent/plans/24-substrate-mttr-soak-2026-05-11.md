# Multi-tenant Phase 4 — Substrate-MTTR Loop Runtime Soak

**Goal:** Prove the substrate engine's detect→alert→resolve loop holds
under sustained synthetic load and meets the contractual MTTR SLAs
(sev1 ≤4h, sev2 ≤24h, sev3 ≤30d).

## Scope

- ✅ IN: Inject synthetic incidents at production-like cadence, measure
  end-to-end MTTR per severity, output a 24h soak report.
- ✅ IN: Verify the alertmanager_webhook severity filter (Session 219)
  correctly suppresses sev3 paging.
- ❌ OUT: Load testing (req/s throughput) — separate task #97.
- ❌ OUT: Real customer L1/L2 healing tier validation — synthetic
  incidents intentionally route to L3 (no auto-resolution).

## Soak Profile

| Severity | Rate | 24h Total | Realistic? |
|---|---|---|---|
| sev1 | 1/hr | 24 | High end (real fleet: ~1/week) |
| sev2 | 5/hr | 120 | High end (real fleet: ~10/day) |
| sev3 | 20/hr | 480 | Realistic (real fleet: ~100/day) |
| **Total** | **26/hr** | **624** | 5-10× real production load |

Synthetic incidents are explicit overcount — we want to STRESS the loop,
not match it. If MTTR holds at 5× load, real production has 5× headroom.

## Design

**Synthetic site:** `synthetic-mttr-soak` — new row in `sites` table,
status=`active`, clinic_name=`MTTR Soak Synthetic`, no real appliances.
Isolates soak data from real customer telemetry.

**Injection marker:** every soak-incident has
`details->>'soak_run_id' = '<uuid>'` AND `details->>'soak_test' = 'true'`.
Substrate invariants, scoring queries, and customer-facing reports
all filter on `details->>'soak_test' != 'true'`. (One-line WHERE clause
added per query path.)

**Alert filter:** alertmanager_webhook.py already filters by
`SUBSTRATE_ALERT_MIN_SEVERITY=sev2`. Soak sev3 already silent. Soak
sev1+sev2 would page operators — add per-soak-run override
`SUBSTRATE_ALERT_SOAK_SUPPRESS=true` env that drops any alert with
`labels.soak_test='true'`. Default false.

**Tracking:** new table `substrate_mttr_soak_runs`:
```
soak_run_id    UUID PRIMARY KEY
started_at     TIMESTAMPTZ NOT NULL
ended_at       TIMESTAMPTZ
config         JSONB  -- {rates: {sev1:1,sev2:5,sev3:20}, duration_hours: 24}
status         TEXT   -- 'running'|'completed'|'aborted'
summary        JSONB  -- {p50_mttr: {...}, p95_mttr: {...}, p99_mttr: {...}}
```

**Per-incident tracking:** for each injected incident, capture:
- `reported_at` (DB insert)
- `detected_at` (first substrate_violations row referencing this incident)
- `alerted_at` (first alertmanager_webhook entry for the violation)
- `resolved_at` (incident.resolved_at)
- `mttr_seconds` = `resolved_at - reported_at`

For the synthetic soak: synthetic incidents are immediately marked
resolved=False at injection. Substrate engine sees them, fires
invariant, creates substrate_violations row. After a configured
"soak resolution window" (sev1: 10min, sev2: 30min, sev3: 4hr),
the injector resolves the incident programmatically. This isolates
the substrate-engine latency from healing-tier latency.

## Phase 4 Deliverables

1. `scripts/substrate_mttr_soak_inject.py` — injector CLI:
   - `--duration-hours 24`
   - `--profile prod-5x` (named profile in config)
   - `--dry-run` (insert + immediately delete to test path)
   - `--soak-run-id <uuid>` (resume an existing run)

2. Migration 303: `synthetic-mttr-soak` site + `substrate_mttr_soak_runs` table.

3. `scripts/substrate_mttr_soak_report.py` — analyzer CLI:
   - `--soak-run-id <uuid>`
   - outputs JSON + markdown report with P50/P95/P99 + SLA verdict

4. CI gate `test_mttr_soak_filter_universality.py` — assert every
   site-listing / scoring / customer-facing endpoint filters on
   `details->>'soak_test' != 'true'` (or the soak site_id).

5. Round-table review of the design BEFORE running 24h (chaos-lab is
   shared with real customers).

## Failure modes to detect

- Substrate engine misses detection (incident exists, no violation row)
- Substrate engine detects but takes >60s (cadence-broken)
- Alertmanager webhook drops the alert (filter bug)
- Email channel fails (SMTP / sendgrid outage)
- Operator-acknowledged incidents not closing the substrate row
- Substrate violation row stuck open after incident resolved (hysteresis bug)

Any of these surface as MTTR P99 ≫ P50 in the soak report.

## Execution plan

1. Day 1: Build injector + analyzer, run 1h smoke test.
2. Day 1 evening: Round-table review + fix any smoke-test findings.
3. Day 2-3: 24h soak with all severities.
4. Day 3 PM: Report + decisions on tuning.

## Counter-arguments (Steve/Maya/Carol)

**Steve (Principal SWE):** "624 synthetic events / 24h pollutes the
data flywheel — patterns will treat soak runs as real recurrence."
→ Mitigation: ALL soak data filtered by `details->>'soak_test'='true'`
on flywheel ingest queries; injector outputs CHECK gate output for the
filter coverage AST scan at end of run.

**Maya (Legal):** "Soak data in customer-visible auditor kit risks
misleading customers. Synthetic incidents in audit log could appear
fabricated to auditors."
→ Mitigation: synthetic site_id is NOT in any auditor-kit org
mapping. compliance_bundles for soak site are never generated
(check_evidence_loop already filters by client_org_id != NULL).
Audit log entries are marked `username='system:soak-test'`.

**Carol (Security):** "Soak runs could mask a real incident if an
operator silences the soak-alert filter and a real one comes through."
→ Mitigation: alert filter is per-LABEL not per-site, so a real
incident at the synthetic site (if it ever happened) still alerts.
The override is `SUBSTRATE_ALERT_SOAK_SUPPRESS` env, default false,
and drops ONLY rows where `labels.soak_test='true'` is explicitly set
by the injector.

## When done

- Phase 4 task #94 marked complete with the 24h report attached.
- Tasks #97 (load test harness) + #98 (MTTR SLA 24h) unblock.

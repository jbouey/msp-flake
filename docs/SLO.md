# Service Level Objectives

<!-- updated 2026-05-16 — Session-220 doc refresh -->

**Last updated:** 2026-05-16 (Session 220 close — D1 heartbeat-verification soak + BAA-enforcement substrate invariant + L2-decision-record gate)
**Owner:** Platform engineering
**Review cadence:** quarterly + on any SLO breach

Defines what "up" means for OsirisCare. SLOs are commitments; SLIs are
measurements. When an SLI goes red, the runbook in
`docs/security/alert-runbooks.md` kicks in.

Scope note: evidence-chain integrity SLOs are stricter than uptime
SLOs. We can survive a 10-minute portal outage. We cannot survive a
single tampered evidence bundle.

---

## Tier-1: Evidence integrity (credibility-critical)

These are the product. If they slip, the sales pitch lies.

| SLI | Target | Measurement |
|-----|--------|-------------|
| Evidence bundle chain validity | **100%** over 90 days | `chain_tamper_detector` finds 0 broken bundles across active-site walks |
| Evidence bundle signature validity | **99.99%** | `compliance_bundles.signature_valid = true` ratio over 90 days |
| OpenTimestamps anchoring within 24h | **99%** of bundles written in the last 30 days | `ots_proofs.anchored_at - compliance_bundles.created_at < 24h`; CHECK constraint on `ots_proofs.status` excludes ad-hoc 'verified' (mig 307) |
| Privileged action → attestation linkage | **100%** | Every `fleet_orders WHERE order_type IN (privileged_types)` has a matching `compliance_bundles.bundle_id` (enforced at DB via migration 175 + extended for `delegate_signing_key` via mig 305) |
| L2 resolution → decision-record linkage | **100%** | Every `incidents WHERE resolution_tier='L2'` has a matching `l2_decisions` row. Enforced at `agent_api.py` + `main.py` via `l2_decision_recorded` gate (Session 219 mig 300); substrate invariant `l2_resolution_without_decision_record` (sev2) catches regressions |
| Cross-org-relocate chain orphan | **0** | Substrate invariant `cross_org_relocate_chain_orphan` (sev1) catches sites with `prior_client_org_id` set but no completed relocate row |
| BAA-gated workflow advancement without active BAA | **0 unauthorized** | Substrate invariant `sensitive_workflow_advanced_without_baa` (sev1) scans 5 gated workflows + auditor_kit_download in last 30d, excludes admin + legacy `?token=` carve-outs. Cliff: 2026-06-12 |

**Error budget:** zero. Any Tier-1 breach requires immediate
incident response + public disclosure via
`docs/security/SECURITY_ADVISORY_*.md` within 72h
(HIPAA §164.404 breach-notification window).

---

## Tier-1.5: Heartbeat-signature verification (D1 soak — prerequisite for BAA v2.0)

| SLI | Target | Measurement |
|-----|--------|-------------|
| Per-pubkeyed-appliance signature validity | **≥99%** `signature_valid IS TRUE` | mig 313 D1 heartbeat-verification path; per-appliance rolling window |
| Open `daemon_heartbeat_signature_{unverified,invalid,unsigned}` violations | **0** | Substrate invariants (sev2) |
| Continuous clean-soak duration | **≥7 days** before v2.0 drafting | PRE-1 gate at `docs/legal/v2.0-hardening-prerequisites.md` |

This tier exists explicitly to gate Master BAA v2.0 language. The
v1.0-INTERIM BAA (effective 2026-05-13) scopes every signed-claim to
evidence bundles. v2.0 may extend signed-claim language to per-event /
per-heartbeat verification ONLY after this tier shows a 7-day clean
soak. CI backstop: `tests/test_baa_artifacts_no_heartbeat_verification_
overclaim.py` (baseline 0).

---

## Tier-2: Availability (operational)

| SLI | Target | Measurement |
|-----|--------|-------------|
| `GET /api/dashboard/*` availability | 99.9% (8.76 hours/year) | probe every 60s from external monitor |
| `POST /checkin` availability | 99.95% | appliances retry on failure, so this can be lower than admin |
| `POST /api/evidence/submit` availability | 99.9% | appliances retry up to 24h; beyond that, evidence loss |
| `POST /api/agent/orders/complete` availability | 99.9% | order-completion path (mig 286) — Maya-rework of trigger-based design |
| Frontend load time (p95) | < 3s | Lighthouse + real-user monitoring |
| Frontend error rate | < 0.5% | `window.onerror` + failed-request ratio |
| Auditor-kit download determinism | byte-identical | Two consecutive downloads with no chain progression + no OTS transitions + no presenter-brand edits MUST produce byte-identical ZIPs (Session 218 contract; pinned at `auditor_kit_zip_primitives.py::_kit_zwrite`) |

**Error budget:** 30-day rolling window. Consuming > 50% in < 7 days
triggers a change-freeze until root cause is understood.

---

## Tier-3: Background loops (data flywheel + substrate engine)

Loops instrumented via `bg_heartbeat`. Staleness target is 3x the
declared `EXPECTED_INTERVAL_S`.

| Loop | Expected interval | Staleness alert threshold |
|------|-------------------|---------------------------|
| `privileged_notifier` | 60s | 180s |
| `chain_tamper_detector` | 3600s | 10800s |
| `merkle_batch` | 600s | 1800s |
| `evidence_chain_check` | 1800s | 5400s |
| `fleet_order_expiry` | 300s | 900s |
| `audit_log_retention` | 86400s | 259200s |
| `health_monitor` | 60s | 180s |
| `substrate_assertions` | 60s | 180s — per-assertion `admin_transaction()` blocks (Session 220 `57960d4b`) so one InterfaceError costs 1/60+ assertions, not 100% |

**Substrate Integrity Engine MTTR target:** mean-time-to-resolution
for sev1/sev2 invariant violations ≤ 24h on real (non-synthetic)
sites. Synthetic-site quarantine (mig 315 + mig 323 `synthetic`
marker column) ensures soak-rig data does not pollute customer-facing
readers.

Runbook: `docs/security/alert-runbooks.md` → "Loop heartbeat stale".

---

## Tier-4: Response times

| Operation | Target p95 | Target p99 |
|-----------|-----------|-----------|
| `POST /checkin` roundtrip | 500ms | 2s |
| Appliance fleet-order delivery latency | 60s (one checkin cycle) | 120s |
| Evidence bundle write | 200ms | 1s |
| Dashboard page load | 1s | 3s |
| Magic-link email delivery | 60s | 5min |
| Privileged-access request → approval email | 120s (= notifier interval + SMTP) | 300s |
| Auditor-kit download (155K-bundle org) | 2.4s | 8s (post-`window_days=30` optimization for canonical compliance-score helper) |

---

## Incident severity (IR)

| Level | Criteria | Response time | Comms |
|-------|----------|---------------|-------|
| SEV-1 | Tier-1 breach, full outage, PHI exposure, signing key compromise, BAA-gated-workflow bypass | 15min to ack, 1h to mitigate | Partner notification immediate |
| SEV-2 | Tier-2 target breached for > 30min, < full outage; substrate sev2 invariant open > 24h | 30min to ack, 4h to mitigate | Partner notification if > 2h |
| SEV-3 | Tier-3 staleness, Tier-4 target breached | next business day | Status page only |
| SEV-4 | Degraded path with documented workaround | weekly triage | None |

---

## Self-imposed constraints (policy, not measurement)

- **No feature ship without a runbook.** New alert ⇒ new entry in
  `alert-runbooks.md`. New SLI ⇒ new row in this file. New substrate
  invariant ⇒ runbook under `backend/substrate_runbooks/<name>.md`
  (Session 220 lock-in `39c31ade`: missing runbook fails
  `test_substrate_docs_present`).
- **Migrations never rollback on production.** Forward fix only.
  Rollbacks are for the CI schema smoke test. (Exception:
  `migrate.py down` is available for staging replay.) Migration numbers
  pre-claimed via `backend/migrations/RESERVED_MIGRATIONS.md` ledger.
- **No destructive ops in autonomous code paths.** Any code path
  that can DELETE, DROP, TRUNCATE, or overwrite user data requires
  explicit operator confirmation (a fleet order, a session-auth
  admin endpoint, or a CLI flag — never a scheduled job).
- **PHI boundary:** PHI scrubbed at appliance egress via `phiscrub`
  package (14 patterns). Central Command is PHI-free by architectural
  commitment (Counsel Rule 2). Any PHI leak is SEV-1.
- **Two-Gate adversarial review (Session 219 lock-in).** Any new
  system / migration / soak / chaos run MUST receive a fork-based
  4-lens review (Steve / Maya / Carol / Coach) at Gate A (pre-execution)
  AND Gate B (pre-completion). Gate B MUST execute the full pre-push
  sweep, not just diff review. Verdicts archived to `audit/coach-
  <topic>-<gate>-YYYY-MM-DD.md`.

---

## Measuring against these SLOs

Every SLO above maps to a Prometheus metric + alert rule. The alert
rules are documented in `docs/security/alert-runbooks.md`. The
Prometheus scrape config lives on the VPS at
`/opt/mcp-server/prometheus/prometheus.yml` (not in git because it
includes customer-specific scrape targets).

**Approximate-row-count rule (Session 219):** Prometheus metrics that
expose a row-count on partitioned tables (`log_entries`,
`compliance_bundles`, `aggregated_pattern_stats`, `incidents`) MUST
use `SUM(reltuples)` from `pg_class` — `SELECT COUNT(*)` on partitioned
tables triggers statement timeouts and burns PgBouncer slots
(57×/hr regression caught at `prometheus_metrics.py:521`).

To validate SLO compliance manually at any point:

```bash
# Tier-1: chain integrity per active site
docker exec mcp-postgres psql -U mcp -d mcp -c "
  SELECT site_id, COUNT(*) AS broken
  FROM admin_audit_log
  WHERE action = 'CHAIN_TAMPER_DETECTED'
    AND created_at > NOW() - INTERVAL '90 days'
  GROUP BY site_id;"

# Tier-1: open substrate invariant violations (sev1/sev2)
docker exec mcp-postgres psql -U mcp -d mcp -c "
  SELECT invariant_name, severity, site_id, detected_at
  FROM v_substrate_violations_active
  WHERE severity IN ('sev1', 'sev2')
    AND synthetic IS NOT TRUE
  ORDER BY severity, detected_at DESC;"

# Tier-1.5: D1 heartbeat verification soak posture (BAA v2.0 PRE-1)
docker exec mcp-postgres psql -U mcp -d mcp -c "
  SELECT appliance_id,
         AVG((signature_valid)::int)::numeric(5,4) AS valid_ratio,
         COUNT(*) AS heartbeats_7d
  FROM appliance_heartbeats
  WHERE created_at > NOW() - INTERVAL '7 days'
  GROUP BY appliance_id
  ORDER BY valid_ratio ASC;"

# Tier-2: recent request latency (from app logs)
docker exec mcp-server python3 -c "
  from dashboard_api.prometheus_metrics import request_latency
  for l in request_latency.collect():
    print(l)
"

# Tier-3: loop staleness
curl -s http://localhost:8000/api/admin/health/loops \
  -H "Cookie: session=$ADMIN_SESSION" | jq '.loops[] | select(.status=="stale")'
```

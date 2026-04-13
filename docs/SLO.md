# Service Level Objectives

**Last updated:** 2026-04-13 (Phase 15 enterprise hygiene)
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
| OpenTimestamps anchoring within 24h | **99%** of bundles written in the last 30 days | `ots_proofs.anchored_at - compliance_bundles.created_at < 24h` |
| Privileged action → attestation linkage | **100%** | Every `fleet_orders WHERE order_type IN (privileged_types)` has a matching `compliance_bundles.bundle_id` (enforced at DB via migration 175) |

**Error budget:** zero. Any Tier-1 breach requires immediate
incident response + public disclosure via
`docs/security/SECURITY_ADVISORY_*.md` within 72h
(HIPAA §164.404 breach-notification window).

---

## Tier-2: Availability (operational)

| SLI | Target | Measurement |
|-----|--------|-------------|
| `GET /api/dashboard/*` availability | 99.9% (8.76 hours/year) | probe every 60s from external monitor |
| `POST /checkin` availability | 99.95% | appliances retry on failure, so this can be lower than admin |
| `POST /api/evidence/submit` availability | 99.9% | appliances retry up to 24h; beyond that, evidence loss |
| Frontend load time (p95) | < 3s | Lighthouse + real-user monitoring |
| Frontend error rate | < 0.5% | `window.onerror` + failed-request ratio |

**Error budget:** 30-day rolling window. Consuming > 50% in < 7 days
triggers a change-freeze until root cause is understood.

---

## Tier-3: Background loops (data flywheel)

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

---

## Incident severity (IR)

| Level | Criteria | Response time | Comms |
|-------|----------|---------------|-------|
| SEV-1 | Tier-1 breach, full outage, PHI exposure, signing key compromise | 15min to ack, 1h to mitigate | Partner notification immediate |
| SEV-2 | Tier-2 target breached for > 30min, < full outage | 30min to ack, 4h to mitigate | Partner notification if > 2h |
| SEV-3 | Tier-3 staleness, Tier-4 target breached | next business day | Status page only |
| SEV-4 | Degraded path with documented workaround | weekly triage | None |

---

## Self-imposed constraints (policy, not measurement)

- **No feature ship without a runbook.** New alert ⇒ new entry in
  `alert-runbooks.md`. New SLI ⇒ new row in this file.
- **Migrations never rollback on production.** Forward fix only.
  Rollbacks are for the CI schema smoke test. (Exception:
  `migrate.py down` is available for staging replay.)
- **No destructive ops in autonomous code paths.** Any code path
  that can DELETE, DROP, TRUNCATE, or overwrite user data requires
  explicit operator confirmation (a fleet order, a session-auth
  admin endpoint, or a CLI flag — never a scheduled job).
- **PHI boundary:** PHI scrubbed at appliance egress via `phiscrub`
  package. Central Command is PHI-free. Any PHI leak is SEV-1.

---

## Measuring against these SLOs

Every SLO above maps to a Prometheus metric + alert rule. The alert
rules are documented in `docs/security/alert-runbooks.md`. The
Prometheus scrape config lives on the VPS at
`/opt/mcp-server/prometheus/prometheus.yml` (not in git because it
includes customer-specific scrape targets).

To validate SLO compliance manually at any point:

```bash
# Tier-1: chain integrity per active site
docker exec mcp-postgres psql -U mcp -d mcp -c "
  SELECT site_id, COUNT(*) AS broken
  FROM admin_audit_log
  WHERE action = 'CHAIN_TAMPER_DETECTED'
    AND created_at > NOW() - INTERVAL '90 days'
  GROUP BY site_id;"

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

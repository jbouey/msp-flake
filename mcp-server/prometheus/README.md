# OsirisCare Prometheus Alert Rules

Alert rule definitions for the OsirisCare platform. Phase 15 enterprise
hygiene — every gauge with operational meaning has a corresponding
alert rule here, and every alert references a runbook.

## Files

- `alert_rules.yml` — alerting rules grouped by concern (security
  invariants, privileged access, background loops, evidence chain)

## Deployment

The prometheus config on the VPS (at `/opt/mcp-server/prometheus/`)
must:

1. Include `rule_files: ['/etc/prometheus/alert_rules.yml']` in its
   global config
2. Mount this file to that path:
   ```yaml
   # docker-compose.yml excerpt
   prometheus:
     volumes:
       - ./mcp-server/prometheus/alert_rules.yml:/etc/prometheus/alert_rules.yml:ro
   ```

After any change, reload prometheus:
```bash
docker exec prometheus kill -HUP 1
```

## Adding a new alert

Each alert MUST:

1. Have a `summary` (≤ 80 chars) and `description` (prose — what the
   alert means and who should care).
2. Reference a runbook — either inline in `description` or via the
   `runbook` annotation. If no runbook exists, write one in
   `docs/security/alert-runbooks.md` IN THE SAME COMMIT.
3. Use `for:` to require sustained condition. `for: 0m` is
   reserved for credibility-critical events (`CHAIN_TAMPER_DETECTED`).
4. Tag `severity` (`critical` | `warning`) and `category` (freeform
   but align with existing entries).

## Metric source

All `osiriscare_*` metrics come from the mcp-server `/metrics`
endpoint (requires admin auth — Prometheus scrapes with a service
account cookie). The export code lives in
`mcp-server/central-command/backend/prometheus_metrics.py`.

The bg_heartbeat / startup_invariants / chain_tamper metrics are
process-local, so they're exported even when the DB is unreachable.
That is intentional: a DB-down event should not hide loop health.

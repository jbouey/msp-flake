# Flywheel Promotion Incident Runbook

**Scope:** L2 → L1 learning pipeline failures — stuck candidates, broken auto-promotion, degraded rules.

**Owner:** Platform Engineering
**Escalation:** `osiriscare_flywheel_stuck_candidates > 0` for > 1h OR no promotions in 7 days

---

## 1. Architecture

The flywheel has 3 stages:

1. **Telemetry → Stats:** `execution_telemetry` (L1/L2 resolutions) aggregates into `aggregated_pattern_stats` per (site, pattern_signature)
2. **Stats → Eligible:** Patterns with ≥5 occurrences, ≥90% success rate, ≥3 L2 resolutions flip `promotion_eligible = true`
3. **Eligible → Promoted:** Eligible patterns become `promoted_rules` + `l1_rules` (both `promoted` and `synced` sources) via `flywheel_promote.promote_candidate()`

**Key invariant:** Every `learning_promotion_candidates` row with `approval_status='approved'` MUST have a matching `promoted_rules` row (matched by `site_id` + `pattern_signature`).

---

## 2. Monitoring

### Primary Metrics

| Metric | Healthy | Warning | Critical |
|--------|---------|---------|----------|
| `osiriscare_flywheel_stuck_candidates` | 0 | — | > 0 |
| `osiriscare_flywheel_last_promotion_age_seconds` | < 604800 (7d) | > 1209600 (14d) | > 2592000 (30d) |
| `osiriscare_flywheel_eligible_waiting` | < 10 | 10-50 | > 50 |
| `osiriscare_flywheel_promoted_rules{status="disabled"}` | < 3 | 3-10 | > 10 |

### Grafana Rules

```yaml
- alert: FlywheelStuckCandidates
  expr: osiriscare_flywheel_stuck_candidates > 0
  for: 1h
  labels:
    severity: critical
  annotations:
    summary: "{{ $value }} approved candidates missing promoted_rules entry"
    runbook: docs/FLYWHEEL_INCIDENT_RUNBOOK.md#stuck-candidates

- alert: FlywheelStalled
  expr: osiriscare_flywheel_last_promotion_age_seconds > 604800
  for: 1h
  labels:
    severity: warning
  annotations:
    summary: "No promotions in {{ $value | humanizeDuration }}"
    runbook: docs/FLYWHEEL_INCIDENT_RUNBOOK.md#stalled-pipeline
```

---

## 3. Diagnostics

### Quick Health Check

```bash
ssh root@178.156.162.116
docker exec mcp-server python3 -c "
import asyncio, asyncpg, os
async def check():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('+asyncpg','').replace('pgbouncer:6432','mcp-postgres:5432').replace('mcp_app','mcp'))
    row = await conn.fetchrow('''
        SELECT
            (SELECT COUNT(*) FROM learning_promotion_candidates WHERE approval_status = 'approved') as approved,
            (SELECT COUNT(*) FROM promoted_rules WHERE status = 'active') as active_promoted,
            (SELECT COUNT(*) FROM l1_rules WHERE source = 'promoted' AND enabled = true) as l1_promoted,
            (SELECT COUNT(*) FROM aggregated_pattern_stats WHERE promotion_eligible = true) as eligible,
            (SELECT COUNT(*) FROM learning_promotion_candidates lpc LEFT JOIN promoted_rules pr ON pr.pattern_signature = lpc.pattern_signature AND pr.site_id = lpc.site_id WHERE lpc.approval_status = 'approved' AND pr.rule_id IS NULL) as stuck
    ''')
    print(f'approved={row[0]} active_promoted={row[1]} l1_promoted={row[2]} eligible={row[3]} STUCK={row[4]}')
    await conn.close()
asyncio.run(check())
"
```

**Healthy signature:** `stuck=0`, `approved ≈ active_promoted`

### Admin Dashboard

Navigate to **Runbooks** page (sidebar). The stats cards show:
- **Total Runbooks** (built-in + learned)
- **Learned (L2→L1)** — this is the flywheel output, should grow over time

---

## 4. Playbook: Stuck Candidates

**Symptom:** `osiriscare_flywheel_stuck_candidates > 0`

**Meaning:** `learning_promotion_candidates.approval_status = 'approved'` but no corresponding `promoted_rules` row. Usually means a divergent approval path was used.

### Auto-repair

The `flywheel_reconciliation_loop` background task runs every 30 minutes and auto-repairs stuck candidates by calling `flywheel_promote.promote_candidate()`. Just wait 30 min.

### Manual repair

```bash
ssh root@178.156.162.116
docker exec -it mcp-server python3 << 'EOF'
import asyncio, asyncpg, os, json
from dashboard_api.flywheel_promote import promote_candidate
async def fix():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('+asyncpg','').replace('pgbouncer:6432','mcp-postgres:5432').replace('mcp_app','mcp'))
    stuck = await conn.fetch("""
        SELECT lpc.id, lpc.site_id, lpc.pattern_signature, lpc.custom_rule_name,
               aps.success_rate, aps.total_occurrences, aps.l2_resolutions, aps.recommended_action
        FROM learning_promotion_candidates lpc
        LEFT JOIN promoted_rules pr ON pr.pattern_signature = lpc.pattern_signature AND pr.site_id = lpc.site_id
        LEFT JOIN aggregated_pattern_stats aps ON aps.site_id = lpc.site_id AND aps.pattern_signature = lpc.pattern_signature
        WHERE lpc.approval_status = 'approved' AND pr.rule_id IS NULL
    """)
    for c in stuck:
        async with conn.transaction():
            await promote_candidate(conn=conn, candidate=dict(c), actor='manual_repair', actor_type='system')
        print(f"Repaired: {c['id']}")
    await conn.close()
asyncio.run(fix())
EOF
```

### Root cause investigation

If stuck candidates keep appearing:
1. Check which code path is creating them:
   ```sql
   SELECT approved_at, COUNT(*) FROM learning_promotion_candidates
   WHERE approval_status = 'approved'
   GROUP BY date_trunc('hour', approved_at)
   ORDER BY 1 DESC LIMIT 10;
   ```
2. Check logs for `promote_candidate` failures:
   ```bash
   docker logs mcp-server 2>&1 | grep -i "promotion_audit_log write failed"
   ```
3. Both admin (`routes.py:/learning/promote/`) and partner (`learning_api.py:/candidates/../approve`) paths MUST call `flywheel_promote.promote_candidate()`. If you see raw `INSERT INTO promoted_rules` outside that module, it's a bug.

---

## 5. Playbook: Stalled Pipeline

**Symptom:** `osiriscare_flywheel_last_promotion_age_seconds > 604800` (no promotions in 7+ days)

### Diagnosis

```sql
-- Are there eligible patterns?
SELECT COUNT(*) FROM aggregated_pattern_stats WHERE promotion_eligible = true;

-- Is the flywheel loop running?
SELECT * FROM admin_audit_log WHERE action LIKE '%flywheel%' ORDER BY created_at DESC LIMIT 5;
```

### Common Causes

1. **Not enough L2 data:** Need ≥3 L2 resolutions per pattern. Check `aggregated_pattern_stats.l2_resolutions`
2. **Flywheel loop crashed:** Check container logs for `Flywheel promotion scan failed`
3. **All eligible patterns already promoted:** Healthy — no action needed, pipeline is just quiet

### Recovery

```bash
# Restart the container (reboots all background loops)
docker compose restart mcp-server

# Verify flywheel loop is running
docker logs mcp-server 2>&1 | grep "flywheel" | tail -10
```

---

## 6. Playbook: Auto-Disabled Rules

**Symptom:** `osiriscare_flywheel_promoted_rules{status="disabled"} > 5`

**Meaning:** The canary rollout detected that promoted rules are performing below 70% success rate and auto-disabled them. This is WORKING AS INTENDED but worth investigating.

### Diagnosis

```sql
SELECT rule_id, runbook_id, created_at,
       (SELECT COUNT(*) FROM execution_telemetry et
        WHERE et.runbook_id = l1.runbook_id
          AND et.created_at > l1.created_at) as executions,
       (SELECT COUNT(*) FILTER (WHERE success) FROM execution_telemetry et
        WHERE et.runbook_id = l1.runbook_id
          AND et.created_at > l1.created_at) as successes
FROM l1_rules l1
WHERE source = 'promoted' AND enabled = false
ORDER BY created_at DESC LIMIT 20;
```

### Response

1. **Expected failures** (promoted too aggressively): Refine eligibility criteria in `background_tasks.py` step 4
2. **Unexpected failures** (regression in runbook): Investigate the underlying runbook, not the rule
3. **Systemic failures** (many rules disabled): Pause auto-promotion by commenting out Step 6 in `flywheel_promotion_loop`

---

## 7. Known Issues / Gotchas

1. **Rule ID collisions** — Multiple sites with the same pattern generate the same rule_id. `promoted_rules` has composite unique on `(rule_id, site_id)` but `l1_rules` has `rule_id` as PK. The first site wins the global rule, others get `SYNC-` versions.
2. **Candidate orphans from old admin path** — Before the shared `flywheel_promote` module existed, the admin `/learning/promote/{id}` endpoint skipped `promoted_rules` entirely. Any candidates from that era need reconciliation to repair.
3. **Pattern signature drift** — Two candidates with the same underlying rule but different `pattern_signature` values (e.g. `firewall:RB-WIN-001` vs `firewall_status:RB-WIN-SEC-001`) both count as "approved" but only one gets promoted. Normal — the other gets a hash-suffixed rule_id on reconciliation.
4. **`learning_promotion_candidates.success_rate` is often NULL** — it's a denormalized cache. The live value is in `aggregated_pattern_stats`. The flywheel pulls from `aggregated_pattern_stats` when computing confidence.

---

## 8. Escalation Matrix

| Condition | Response Time | Action |
|-----------|---------------|--------|
| `stuck_candidates > 0` for > 1h | Immediate | Run manual repair (section 4) |
| `stuck_candidates > 10` | Immediate | Investigate divergent approval path + alert engineering |
| `stalled > 7 days` with `eligible_waiting > 10` | 1 hour | Check flywheel loop crash, restart container |
| `disabled > 10` in 30 days | 4 hours | Review promotion eligibility criteria |
| Reconciliation loop firing repair repeatedly | 1 day | Find the code path creating divergent state |

---

## 9. Reference

- **Shared promotion module:** `mcp-server/central-command/backend/flywheel_promote.py`
- **Flywheel loop:** `background_tasks.py` `flywheel_promotion_loop()`
- **Reconciliation loop:** `background_tasks.py` `flywheel_reconciliation_loop()`
- **Admin promote endpoint:** `routes.py` `POST /learning/promote/{pattern_id}`
- **Partner approve endpoint:** `learning_api.py` `POST /candidates/{pattern_id}/approve`
- **Tests:** `tests/test_flywheel_promote.py`
- **Metrics:** `osiriscare_flywheel_*` (section 2)
- **Tables:** `learning_promotion_candidates`, `promoted_rules`, `l1_rules`, `runbooks`, `runbook_id_mapping`, `promotion_audit_log`, `aggregated_pattern_stats`, `platform_pattern_stats`

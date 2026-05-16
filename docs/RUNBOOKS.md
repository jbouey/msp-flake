# Runbooks & Evidence Bundles

<!-- updated 2026-05-16 — Session-220 doc refresh -->

> **Two runbook families:** (1) **operational runbooks** — human procedures
> for an on-call operator (`docs/runbooks/*.md`); (2) **substrate
> invariant runbooks** — per-invariant remediation references read by
> the Substrate Integrity Engine (`mcp-server/central-command/backend/
> substrate_runbooks/*.md`). Every substrate invariant MUST ship with a
> matching runbook file — `tests/test_substrate_docs_present` is the CI
> gate; missing runbooks blocked 3 deploys in Session 220 alone.

## Runbook Structure

Each runbook is a YAML file with pre-approved steps, HIPAA citations, and required evidence fields.

```yaml
id: RB-BACKUP-001
name: "Backup Failure Remediation"
hipaa_controls:
  - "164.308(a)(7)(ii)(A)"
  - "164.310(d)(2)(iv)"
severity: high
steps:
  - action: check_backup_logs
    timeout: 30s
  - action: verify_disk_space
    timeout: 10s
  - action: restart_backup_service
    timeout: 60s
  - action: trigger_manual_backup
    timeout: 300s
rollback:
  - action: alert_administrator
evidence_required:
  - backup_log_excerpt
  - disk_usage_before
  - disk_usage_after
  - service_status
  - backup_completion_hash
```

## Standard Runbooks

### RB-BACKUP-001: Backup Failure
- Check backup logs
- Verify disk space
- Restart backup service
- Trigger manual backup
- HIPAA: §164.308(a)(7)(ii)(A), §164.310(d)(2)(iv)

### RB-CERT-001: Certificate Expiry
- Check certificate expiry dates
- Generate new CSR
- Request renewal
- Install new certificate
- HIPAA: §164.312(a)(2)(iv), §164.312(e)(1)

### RB-DISK-001: Disk Full
- Identify large files
- Clear old logs
- Clean temp directories
- Verify free space
- HIPAA: §164.308(a)(1)(ii)(D)

### RB-SERVICE-001: Service Crash
- Check service status
- Review crash logs
- Restart service
- Verify health
- HIPAA: §164.312(b)

### RB-CPU-001: High CPU
- Identify top processes
- Check for runaway jobs
- Kill or restart offender
- Verify normalization
- HIPAA: §164.308(a)(1)(ii)(D)

### RB-RESTORE-001: Weekly Test Restore
- Select random backup
- Create scratch VM
- Restore to scratch
- Verify checksums
- Cleanup scratch VM
- HIPAA: §164.308(a)(7)(ii)(A), §164.310(d)(2)(iv)

## Evidence Bundle Structure

Every monitored event generates standardized evidence:

```json
{
  "bundle_id": "EB-20251023-0001",
  "client_id": "clinic-001",
  "incident_id": "INC-20251023-0001",
  "runbook_id": "RB-BACKUP-001",
  "timestamp_start": "2025-10-23T14:32:01Z",
  "timestamp_end": "2025-10-23T14:35:23Z",
  "operator": "service:mcp-executor",
  "hipaa_controls": ["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"],
  "inputs": {
    "log_excerpt_hash": "sha256:a1b2c3...",
    "disk_usage_before": "87%"
  },
  "actions_taken": [
    {
      "step": 1,
      "action": "check_backup_logs",
      "result": "failed",
      "script_hash": "sha256:d4e5f6..."
    },
    {
      "step": 2,
      "action": "verify_disk_space",
      "result": "ok",
      "script_hash": "sha256:g7h8i9..."
    },
    {
      "step": 3,
      "action": "restart_backup_service",
      "result": "ok",
      "script_hash": "sha256:j1k2l3..."
    }
  ],
  "outputs": {
    "backup_completion_hash": "sha256:m4n5o6...",
    "disk_usage_after": "62%"
  },
  "sla_met": true,
  "mttr_seconds": 202,
  "evidence_bundle_hash": "sha256:p7q8r9...",
  "storage_locations": [
    "local:/var/lib/msp/evidence/EB-20251023-0001.json",
    "s3://compliance-worm/clinic-001/2025/10/EB-20251023-0001.json"
  ]
}
```

## Monthly Compliance Packet Template

```markdown
# HIPAA Compliance Report
**Client:** Clinic ABC
**Period:** October 1-31, 2025
**Baseline:** NixOS-HIPAA v1.2

## Executive Summary
- Incidents detected: 12
- Automatically remediated: 10
- Escalated to administrator: 2
- SLA compliance: 98.3%
- MTTR average: 4.2 minutes

## Controls Status
| Control | Status | Evidence Count | Exceptions |
|---------|--------|---------------|-----------|
| 164.308(a)(1)(ii)(D) | ✅ Compliant | 45 audit logs | 0 |
| 164.308(a)(7)(ii)(A) | ✅ Compliant | 4 backup tests | 0 |
| 164.312(a)(2)(iv) | ⚠️ Attention | 1 cert renewal | 1 |

## Incidents Summary
[Table of incidents with runbook IDs, timestamps, MTTR]

## Baseline Exceptions
[List of approved exceptions with expiry dates]

## Test Restore Verification
- Week 1: ✅ Successful (3 files, 1 DB table)
- Week 2: ✅ Successful (5 files)
- Week 3: ✅ Successful (2 files, 1 DB)
- Week 4: ✅ Successful (4 files)

## Evidence Artifacts
[Links to WORM storage for all bundles]

---
Generated: 2025-11-01 00:05:00 UTC
Signature: sha256:x9y8z7...
```

## Substrate Integrity Engine (Session 207+, hardened Session 219-220)

The Substrate Integrity Engine runs every 60s and asserts ~78 invariants
against production state. Each invariant is sev0/sev1/sev2 and resolves
to a markdown runbook under `mcp-server/central-command/backend/
substrate_runbooks/*.md` (operator reads on alert).

### Per-assertion `admin_transaction` isolation (Session 219 commit `57960d4b`)

Pre-fix the engine held ONE `admin_connection` across all 60+ assertions
per tick. One `asyncpg.InterfaceError` poisoned the conn and blinded
every subsequent assertion — 1.6% intended fidelity loss became 100%
cascade-fail.

Fix: per-assertion `admin_transaction(pool)` blocks at
`assertions.py::run_assertions_once`. `_ttl_sweep` runs in its OWN
independent block so sigauth reclaim doesn't get dropped by transient
upstream errors. CI gate `tests/test_assertions_loop_uses_admin_transaction.py`
(5 tests) pins the design.

### Healing-tier integrity invariants (Session 220)

| Invariant | Severity | Detects |
|---|---|---|
| `l1_resolution_without_remediation_step` | sev2 | L1 escalate-action false-heal — daemon returns "escalated" w/ no `success` key, backend persisted as L1. Pre-fix: 1,137 prod orphans across 90 days. |
| `l2_resolution_without_decision_record` | sev2 | `resolution_tier='L2'` set without matching `l2_decisions` row — ghost-L2 audit gap. Mig 300 backfills with `pattern_signature='L2-ORPHAN-BACKFILL-MIG-300'`. |
| `chronic_without_l2_escalation` | sev1 | Recurring incidents (≥3 in 7d) never escalated to L2 — flywheel deadweight. |
| `sensitive_workflow_advanced_without_baa` | sev1 | BAA-gated workflow advanced without an active BAA. See "BAA enforcement triad" below. |
| `cross_org_relocate_chain_orphan` | sev1 | `sites.prior_client_org_id` set but no completed relocate state-machine row — bypass-path detector. |
| `canonical_compliance_score_drift` | sev1 | Customer-facing compliance score diverges from canonical helper output. |
| `daemon_heartbeat_signature_unverified` | sev1 | D1 backend heartbeat-verification soak telemetry (precondition for Master BAA v2.0). |

Full list under `mcp-server/central-command/backend/substrate_runbooks/`.

### Healing-tier semantics (Session 220 L1 escalate-action fix)

The Go daemon returns `{"success": false}` on the `escalate` action
(`appliance/internal/healing/healing_executor.go:106-110`) and
`l1_engine.go:335-350` fail-closed defaults `Success = false` on
missing-key AND `output == nil` paths. The backend (`main.py:4870`)
downgrades `resolution_tier='L1' → 'monitoring'` when
`check_type in MONITORING_ONLY_CHECKS` as a server-side safety net for
the asynchronous daemon fleet-update window.

**Commit-order rule:** ship backend Layer 2 FIRST (live in ~5min); the
daemon Layer 1 fix follows over hours-to-days as fleet updates roll out.

## BAA Enforcement Triad (Counsel Rule 6, Session 220 Tasks #52/#91/#92/#98)

Every CE-mutating workflow MUST be either gated by `require_active_baa(workflow)`
or explicitly registered in `_DEFERRED_WORKFLOWS` with a written
exemption rationale. Triad:

1. **List 1** — `baa_enforcement.BAA_GATED_WORKFLOWS`. Active members:
   `owner_transfer`, `cross_org_relocate`, `evidence_export`,
   `new_site_onboarding`, `new_credential_entry`.
2. **List 2** — enforcing callsites: `require_active_baa(workflow)`
   (client-owner context), `enforce_or_log_admin_bypass(...)` (admin
   carve-out, logs `baa_enforcement_bypass` to `admin_audit_log` —
   never blocks), `check_baa_for_evidence_export(_auth, site_id)`
   (method-aware auditor-kit branches).
3. **List 3** — `sensitive_workflow_advanced_without_baa` sev1
   substrate invariant scans state-machine tables +
   `admin_audit_log auditor_kit_download` rows over last 30d.

CI gate `tests/test_baa_gated_workflows_lockstep.py` pins List 1 ↔ List 2.
Cliff date 2026-06-12 for full coverage on all CE-mutating workflows.

## HIPAA Control Mapping

```yaml
164.308(a)(1)(ii)(D): RB-AUDIT-001 → evidence/auditlog-checksum.json
164.308(a)(7)(ii)(A): RB-BACKUP-001 → evidence/backup-success.json
164.310(d)(2)(iv): RB-RESTORE-001 → evidence/restore-test.json
164.312(a)(2)(iv): RB-CERT-001 → evidence/encryption-status.json
164.312(b): RB-AUDIT-002 → evidence/auditd-forwarding.json
```

## Evidence Pipeline

For every incident/runbook:
1. **Inputs**: logs/snippets with hashes
2. **Actions**: scripts + hashes + results
3. **Outputs**: final state
4. **Operator**: service principal
5. **Timestamps**: start/end
6. **SLA timers**: met/missed
7. **Control IDs**: HIPAA citations

Written to:
- Append-only local log (hash-chained)
- WORM S3/MinIO bucket with object lock

Nightly job emits Compliance Packet PDF per client.

---

## Recovery Runbook — Migration-Induced Restart Loop

**Symptom:** mcp-server container in `Restarting (N)` loop. Logs show either
"column X does not exist" (schema drift in new migration) or
`DuplicatePreparedStatementError` (PgBouncer backend poisoning from
prior restart loop). CI deploy fails because it can't `docker exec` into
the container.

**Reference postmortem:** `docs/postmortem-2026-04-13-migration-162-outage.md`

### Diagnosis

```bash
ssh root@178.156.162.116
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep mcp-server
docker logs mcp-server --tail 50 2>&1 | grep -iE 'migration|Prepared|column|Duplicate'
docker exec mcp-postgres psql -U mcp -d mcp -c \
  "SELECT version, name FROM schema_migrations ORDER BY version DESC LIMIT 5;"
```

### Recovery (in order)

1. **Identify the failing migration.** The container log will name the file
   (e.g. `Applying: 162_backfill_synthetic_l2_runbook_ids` followed by
   `ERROR: column X does not exist`).

2. **Fix the migration locally** — inspect production schema, correct
   column references, commit. Example diagnostic:
   ```bash
   ssh root@178.156.162.116 "docker exec mcp-postgres psql -U mcp -d mcp -c \
     'SELECT column_name FROM information_schema.columns WHERE table_name=<table>;'"
   ```

3. **Push fixed migration manually to the VPS mount** (CI is blocked by
   the restart loop):
   ```bash
   scp <migration.sql> root@178.156.162.116:/opt/mcp-server/dashboard_api_mount/migrations/
   ```

4. **Atomic restart sequence** (MUST be this order):
   ```bash
   ssh root@178.156.162.116 '
     docker stop mcp-server
     docker exec mcp-postgres psql -U mcp -d postgres -c \
       "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=\"mcp\" AND pid != pg_backend_pid();"
     docker restart mcp-pgbouncer
     sleep 3
     docker start mcp-server
   '
   ```

5. **Verify recovery:**
   ```bash
   curl -sf https://api.osiriscare.net/health
   ssh root@178.156.162.116 "docker exec mcp-postgres psql -U mcp -d mcp -c \
     \"SELECT version, name, applied_at FROM schema_migrations ORDER BY version DESC LIMIT 5;\""
   ```

### Why this sequence

- **`docker stop mcp-server` first** — breaks the restart loop so we stop
  creating new orphaned prepared statements each ~10s
- **`pg_terminate_backend` ALL mcp backends** — forces Postgres to reap
  the backend processes holding stale prepared statements
- **Restart PgBouncer** — clears the pool and forces fresh backend
  connections
- **Start mcp-server** — now it gets clean backends, migration applies,
  app starts normally

### Non-interventions that DON'T work

- Restarting only PgBouncer while mcp-server is still running: new
  orphaned prepared statements created immediately on next retry
- Restarting only mcp-server: PgBouncer's pool still holds dirty backends
- Waiting for CI to self-heal: CI `docker exec` requires a running
  container, which is precisely what's broken

### Prevention

- **Pre-flight schema check in CI** — apply new migrations against a
  scratch DB seeded from production schema before merging the PR
- **Migration dry-run locally** — `docker exec mcp-postgres psql -U mcp
  -d mcp -f <migration.sql>` against a fresh branch of the production DB
- **Never trust the schema you remember** — always query
  `information_schema.columns` before writing UPDATE/ALTER migrations



# Mesh Incident Response Runbook

**Scope:** Operational procedures for multi-appliance mesh failures — ring drift, coverage gaps, split-brain scenarios, and peer discovery issues.

**Owner:** Platform Engineering
**Escalation:** If ring_drift_sites > 0 for > 10m OR target_overlaps > 0 persistent

---

## 1. Architecture Refresher

OsirisCare uses a **backend-authoritative mesh** (Session 196):

- Each multi-appliance site runs 2-10 appliances on the same or different subnets
- Appliances discover peers via ARP (same subnet) or backend-delivered peer info (cross-subnet)
- Target assignment is computed **server-side** in the checkin response using a consistent hash ring
- Local ring on each appliance is a 15-minute fallback if server is unreachable
- Each appliance scans only its assigned subset of targets — no duplicate scanning

**Key invariant:** `ring_size == online_appliance_count` on every appliance in the mesh.

---

## 2. Monitoring Alerts

### Primary Metrics (Prometheus)

| Metric | Healthy | Warning | Critical |
|--------|---------|---------|----------|
| `osiriscare_mesh_drift_sites` | 0 | — | > 0 |
| `osiriscare_mesh_target_overlaps` | 0 | — | > 0 |
| `osiriscare_mesh_target_orphans` | 0 | — | > 0 |
| `osiriscare_mesh_avg_ring_size` | == online_count | differs by 1 | differs by > 1 |
| `osiriscare_mesh_assignment_changes_1h` | < 10 | 10-50 | > 50 (thrashing) |

### Grafana Alert Rules

```yaml
- alert: MeshRingDrift
  expr: osiriscare_mesh_drift_sites > 0
  for: 10m
  labels:
    severity: critical
  annotations:
    summary: "{{ $value }} mesh sites have ring size disagreeing with online count"
    runbook: "docs/runbooks/MESH_INCIDENT_RUNBOOK.md#ring-drift"

- alert: MeshTargetOverlap
  expr: osiriscare_mesh_target_overlaps > 0
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "{{ $value }} targets are owned by multiple appliances (duplicate scans)"
    runbook: "docs/runbooks/MESH_INCIDENT_RUNBOOK.md#target-overlap"

- alert: MeshTargetOrphan
  expr: osiriscare_mesh_target_orphans > 0
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "{{ $value }} targets have no owner (coverage hole)"
    runbook: "docs/runbooks/MESH_INCIDENT_RUNBOOK.md#target-orphan"

- alert: MeshAssignmentThrashing
  expr: osiriscare_mesh_assignment_changes_1h > 50
  for: 30m
  labels:
    severity: warning
  annotations:
    summary: "Mesh assignments changing rapidly ({{ $value }}/hour)"
    runbook: "docs/runbooks/MESH_INCIDENT_RUNBOOK.md#thrashing"
```

### Backend Logs

```bash
docker logs mcp-server 2>&1 | grep -iE "MESH_RING_DISAGREEMENT|MESH_RING_DRIFT|MESH_TARGET_OVERLAP"
```

---

## 3. Diagnostic Commands

### Quick Health Check

```bash
ssh root@178.156.162.116
docker exec mcp-server python3 -c "
import asyncio, asyncpg, os
async def check():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('+asyncpg','').replace('pgbouncer:6432','mcp-postgres:5432').replace('mcp_app','mcp'))
    mesh = await conn.fetch('''
        SELECT site_id,
               COUNT(*) as total,
               COUNT(*) FILTER (WHERE last_checkin > NOW() - INTERVAL '5 minutes') as online,
               AVG((daemon_health->>'mesh_ring_size')::int) as avg_ring,
               AVG((daemon_health->>'mesh_peer_count')::int) as avg_peers
        FROM site_appliances
        GROUP BY site_id
        HAVING COUNT(*) > 1
    ''')
    for row in mesh: print(row)
    await conn.close()
asyncio.run(check())
"
```

### Admin Dashboard

Navigate to **Sites → [Site Name]**. The **Mesh Health** panel at the top shows:
- Ring agreement status (green = healthy, red = split-brain)
- Per-appliance ring_size, peer_count, target_count
- Recent assignment changes (last 10)
- Coverage overlaps

### Prometheus

```bash
curl -s http://localhost:8000/metrics | grep osiriscare_mesh
```

### Audit History

```sql
-- Recent assignment changes for a site
SELECT appliance_id, assignment_epoch, ring_size, target_count, created_at
FROM mesh_assignment_audit
WHERE site_id = 'north-valley-branch-2'
ORDER BY created_at DESC
LIMIT 20;
```

---

## 4. Incident Playbook: Ring Drift

**Symptom:** `osiriscare_mesh_drift_sites > 0` OR admin dashboard shows "Ring drift detected"

**Meaning:** Number of appliances reporting ring size doesn't match the count of online appliances.

### Step 1: Identify the drifted site

Check the Mesh Health panel on the admin dashboard. Each appliance's `ring_size` should equal the online count.

### Step 2: Check peer discovery

```sql
-- Are all appliances actually reachable?
SELECT appliance_id, last_checkin, daemon_health->>'mesh_peer_count' as peers
FROM site_appliances WHERE site_id = '<site>';
```

**If one appliance shows peer_count=0:** It's isolated. Check:
1. gRPC port 50051 open between subnets (`nc -zv <peer_ip> 50051`)
2. CA certs match across appliances (TLS probe failure)
3. ARP table has the peer (for same-subnet)
4. Backend-delivered peer info arriving in checkin response

### Step 3: Force re-discovery

Send a `force_checkin` fleet order to the lagging appliance:

```bash
docker exec mcp-server python3 /app/dashboard_api/fleet_cli.py \
  create --site-id <site> --type force_checkin
```

The next checkin will recompute the ring with fresh peer info.

### Step 4: Restart the daemon (last resort)

SSH to the appliance and restart:

```bash
ssh root@<appliance_wg_ip>
systemctl restart appliance-daemon
```

This triggers full peer re-discovery on startup.

---

## 5. Incident Playbook: Target Overlap

**Symptom:** `osiriscare_mesh_target_overlaps > 0`

**Meaning:** Two or more appliances are scanning the same target. Wasted work, possible duplicate incidents.

### Root cause

Usually means:
1. Two appliances briefly disagreed on ring membership during transition
2. Backend-authoritative assignment hasn't propagated yet
3. Local-ring fallback kicked in when server was briefly unreachable

### Response

**Transient (clears within 10 min):** No action. The next checkin cycle synchronizes assignments.

**Persistent (> 10 min):** Indicates stuck assignment. Check audit history:

```sql
SELECT appliance_id, assignment_epoch, target_count, created_at
FROM mesh_assignment_audit
WHERE site_id = '<site>'
ORDER BY created_at DESC
LIMIT 10;
```

If `assignment_epoch` is the same across all appliances but targets overlap → **BUG**, escalate to engineering.

If epochs differ → one appliance has stale assignment. Force checkin (see Ring Drift Step 3).

---

## 6. Incident Playbook: Target Orphan

**Symptom:** `osiriscare_mesh_target_orphans > 0`

**Meaning:** A target exists but no appliance claims it. **Compliance coverage hole.**

### This is critical — clients are not being scanned.

### Step 1: Identify orphaned targets

```sql
-- Find targets that should be scanned but aren't in any assignment
SELECT DISTINCT t.target
FROM (
    SELECT jsonb_array_elements_text(windows_targets) as target
    FROM site_credentials WHERE site_id = '<site>'
) t
WHERE NOT EXISTS (
    SELECT 1 FROM site_appliances
    WHERE site_id = '<site>'
      AND assigned_targets::jsonb ? t.target
);
```

### Step 2: Force assignment recomputation

Trigger a checkin from any online appliance:

```bash
docker exec mcp-server python3 /app/dashboard_api/fleet_cli.py \
  create --site-id <site> --type force_checkin
```

### Step 3: Verify hash ring correctness

Cross-check the Python hash_ring against the Go implementation using the cross-language vector test in `test_hash_ring.py`. If they diverge, engineering escalation.

### Step 4: Temporary workaround

Disable mesh for the site (single-appliance mode) until fix:

```sql
-- Disable mesh coordination temporarily (manual override)
UPDATE site_appliances SET assigned_targets = NULL WHERE site_id = '<site>';
```

Each appliance will scan everything (duplicate work but no coverage holes).

---

## 7. Incident Playbook: Assignment Thrashing

**Symptom:** `osiriscare_mesh_assignment_changes_1h > 50`

**Meaning:** Assignments changing more than ~1 per minute. Usually indicates a flapping peer.

### Step 1: Identify the flapping appliance

```sql
SELECT appliance_id, COUNT(*) as change_count
FROM mesh_assignment_audit
WHERE site_id = '<site>' AND created_at > NOW() - INTERVAL '1 hour'
GROUP BY appliance_id
ORDER BY change_count DESC;
```

### Step 2: Check its network

A flapping peer means it's joining/leaving the ring rapidly:
- Unstable WAN link (cross-subnet mesh)
- Intermittent WireGuard tunnel
- Network flap between checkin cycles

```bash
ssh root@<appliance_wg_ip>
journalctl -u wg-quick@wg0 -n 50
ping -c 10 <peer_ip>
```

### Step 3: Extend grace period (temporary)

If the flap is < 2 min recovery, the current grace period (~90s) isn't enough. Edit `mesh.go` to bump `gracePeriod` for the affected deployment.

### Step 4: Permanent fix

Usually requires fixing the underlying network (replace router, upgrade ISP circuit, move to wired).

---

## 8. Known Issues / Gotchas

1. **Cross-subnet requires backend delivery** — ARP only works on same subnet. Check that `Checkin site: delivering N mesh peer(s)` log appears.
2. **Round-robin vs hash ring** — For targets < 2x nodes, we use deterministic round-robin. Fewer targets than expected? Check the round-robin path.
3. **Ring epoch is Unix timestamp** — Clock skew > 1 minute between appliances could cause assignment rejection.
4. **TLS probe requires matching CA** — If an appliance joins the mesh with a different CA, TLS probe fails and the peer never joins the ring. Check `/etc/ssl/certs/msp-ca.crt` matches.
5. **Audit entries only on change** — The mesh_assignment_audit table only gets rows when assignment differs from the previous. No change = no audit entry.

---

## 9. Escalation Matrix

| Condition | Response Time | Action |
|-----------|---------------|--------|
| Ring drift > 10 min | 15 min | Force checkin, check peer discovery |
| Target overlap > 10 min | 30 min | Investigate assignment epoch mismatch |
| Target orphan ANY | Immediate | Coverage hole — follow orphan playbook |
| Assignment thrashing > 30 min | 1 hour | Investigate network flap |
| Full mesh down (all peers = 0) | Immediate | Check gRPC port, CA certs, WireGuard |

---

## 10. Reference

- **Mesh design:** `docs/ARCHITECTURE.md` (mesh section)
- **Hash ring (Python):** `mcp-server/central-command/backend/hash_ring.py`
- **Hash ring (Go):** `appliance/internal/daemon/mesh.go`
- **Assignment compute:** `sites.py` STEP 3.8c
- **Consistency check loop:** `background_tasks.py` `mesh_consistency_check_loop()`
- **Audit table:** `mesh_assignment_audit` (migration 145)
- **Dashboard panel:** `frontend/src/components/composed/MeshHealthPanel.tsx`
- **Tests:** `tests/test_hash_ring.py`, `tests/test_target_assignment.py`, `appliance/internal/daemon/mesh_test.go`

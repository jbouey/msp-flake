# Mesh Incident Response Runbook

**Scope:** operational procedures for multi-appliance mesh failures —
ring drift, coverage gaps, split-brain scenarios, peer-discovery issues.

**Owner:** Substrate-engineering on-call (rotation in `.agent/reference/`;
weekly schedule in pager). For after-hours pages, see §11 Escalation.

**Audience:** Osiris admins responding to a Prometheus mesh alert OR an
admin-dashboard "Ring drift detected" indicator.

**Severity baseline:**
- **P0** — coverage hole (`osiriscare_mesh_target_orphans > 0`) OR
  full mesh down (all peers = 0). Customer scans not happening.
- **P1** — ring drift > 10 min OR target overlap > 30 min. Wasted
  work + delayed convergence; not a coverage hole yet.
- **P2** — assignment thrashing (> 50 changes/hour). Symptom of
  underlying network instability.

**Last verified against deployed code:** 2026-05-06 (post-adversarial-
audit + Maya 2nd-eye + production fix to `mesh_consistency_check_loop`
to filter soft-deleted appliances).

---

## 1. Architecture refresher

OsirisCare uses a **backend-authoritative mesh** (Session 196):

- Each multi-appliance site runs 2-10 appliances on the same or
  different subnets.
- Appliances discover peers via ARP (same subnet) or backend-
  delivered peer info (cross-subnet).
- Target assignment is computed **server-side** in the checkin
  response using a consistent hash ring (Python: `hash_ring.py`;
  Go: `appliance/internal/daemon/mesh.go`; cross-language vector
  test pins agreement: `tests/test_hash_ring.py`).
- Local ring on each appliance is a 15-minute fallback if server
  is unreachable.
- Each appliance scans only its assigned subset of targets — no
  duplicate scanning.

**Key invariant:** `ring_size == online_appliance_count` on every
appliance in the mesh. Soft-deleted appliances (`deleted_at IS NOT NULL`)
are EXCLUDED from the count on both sides — verified in
`background_tasks.mesh_consistency_check_loop()` after the 2026-05-06
audit.

---

## 2. Monitoring alerts

### Primary metrics (Prometheus) — verified against `prometheus_metrics.py`

| Metric | Healthy | Warning | Critical |
|---|---|---|---|
| `osiriscare_mesh_appliance_count` | per-site count of NON-deleted appliances | — | sudden drop > 1 unexplained |
| `osiriscare_mesh_online_count` | == appliance_count | online < count | online == 0 (full mesh down) |
| `osiriscare_mesh_drift_sites` | 0 | — | > 0 for > 10 min |
| `osiriscare_mesh_target_overlaps` | 0 | — | > 0 for > 30 min |
| `osiriscare_mesh_target_orphans` | 0 | — | > 0 ANY (P0) |
| `osiriscare_mesh_avg_ring_size` | == online_count | differs by 1 | differs by > 1 |
| `osiriscare_mesh_avg_peer_count` | == online_count - 1 | differs by 1 | differs by > 1 (split-brain) |
| `osiriscare_mesh_assignment_changes_1h` | < 10 | 10-50 | > 50 (thrashing) |

### Grafana alert rules

> **Note:** the `runbook` annotation path is `docs/runbooks/MESH_INCIDENT_RUNBOOK.md`
> (this file's actual repo path). Earlier draft used `docs/MESH_INCIDENT_RUNBOOK.md`
> which 404'd from alert clicks.

```yaml
- alert: MeshTargetOrphan
  expr: osiriscare_mesh_target_orphans > 0
  for: 0m  # P0 — fire immediately on any orphan
  labels:
    severity: critical
  annotations:
    summary: "{{ $value }} targets have no owner (coverage hole)"
    runbook: "docs/runbooks/MESH_INCIDENT_RUNBOOK.md#6-incident-playbook-target-orphan"

- alert: MeshFullDown
  expr: osiriscare_mesh_online_count == 0 and osiriscare_mesh_appliance_count > 0
  for: 2m
  labels:
    severity: critical
  annotations:
    summary: "Mesh fully offline ({{ $labels.site_id }}) — 0 of {{ $value }} appliances reporting"
    runbook: "docs/runbooks/MESH_INCIDENT_RUNBOOK.md#11-escalation-matrix"

- alert: MeshRingDrift
  expr: osiriscare_mesh_drift_sites > 0
  for: 10m
  labels:
    severity: critical
  annotations:
    summary: "{{ $value }} mesh sites have ring size disagreeing with online count"
    runbook: "docs/runbooks/MESH_INCIDENT_RUNBOOK.md#4-incident-playbook-ring-drift"

- alert: MeshTargetOverlap
  expr: osiriscare_mesh_target_overlaps > 0
  for: 30m  # transient overlap up to 10 min is normal during rebalance
  labels:
    severity: warning
  annotations:
    summary: "{{ $value }} targets are owned by multiple appliances (duplicate scans)"
    runbook: "docs/runbooks/MESH_INCIDENT_RUNBOOK.md#5-incident-playbook-target-overlap"

- alert: MeshAssignmentThrashing
  expr: osiriscare_mesh_assignment_changes_1h > 50
  for: 30m
  labels:
    severity: warning
  annotations:
    summary: "Mesh assignments changing rapidly ({{ $value }}/hour)"
    runbook: "docs/runbooks/MESH_INCIDENT_RUNBOOK.md#7-incident-playbook-assignment-thrashing"
```

### Backend logs

Connect via the documented bastion / SSH-tunnel pattern (see
`.agent/reference/NETWORK.md`), NOT direct VPS root for diagnostics:

```bash
# From inside the running mcp-server container
docker logs mcp-server 2>&1 | grep -iE "MESH_RING_DISAGREEMENT|MESH_RING_DRIFT|MESH_TARGET_OVERLAP|MESH_TARGET_ORPHAN"
```

---

## 3. Diagnostic commands

> **Important:** every diagnostic SQL query below FILTERS
> `deleted_at IS NULL`. Soft-deleted appliances must NEVER appear in
> diagnostic counts (they would inflate "total" and trigger false
> ring-drift). Pinned by `tests/test_no_unfiltered_site_appliances_select.py`.

### Quick health check (admin dashboard preferred)

The first stop should be the **Mesh Health panel** on the admin
dashboard at `Sites → [Site Name]`. The panel shows:

- Ring agreement status (green = healthy, red = split-brain)
- Per-appliance ring_size, peer_count, target_count
- Recent assignment changes (last 10)
- Coverage overlaps

If the dashboard is unreachable, fall back to the SQL diagnostic
below. The SQL must run via the documented `admin_connection` route
(through PgBouncer at `pgbouncer:6432`); never connect directly to
`mcp-postgres:5432` because that bypasses PgBouncer's connection
pooling and the RLS-aware `app.is_admin` GUC plumbing the rest of
the platform relies on.

### SQL: per-site mesh status

Open a short-lived `psql` shell inside the running mcp-server
container. The container's `DATABASE_URL` env var is already set
correctly; use the form that does NOT echo the URL into your
local shell history:

```bash
# Drop into an interactive container shell first, then run psql
# inside it. This keeps DATABASE_URL out of the LOCAL .bash_history
# (Steve sec-2nd-eye review 2026-05-06).
docker exec -it mcp-server bash
# now inside the container:
psql "$DATABASE_URL"
```

```sql
SELECT site_id,
       COUNT(*) FILTER (WHERE deleted_at IS NULL) AS total_active,
       COUNT(*) FILTER (
           WHERE deleted_at IS NULL
             AND last_checkin > NOW() - INTERVAL '5 minutes'
       ) AS online,
       AVG((daemon_health->>'mesh_ring_size')::int) FILTER (
           WHERE deleted_at IS NULL
             AND daemon_health IS NOT NULL
       ) AS avg_ring,
       AVG((daemon_health->>'mesh_peer_count')::int) FILTER (
           WHERE deleted_at IS NULL
             AND daemon_health IS NOT NULL
       ) AS avg_peers
  FROM site_appliances
 GROUP BY site_id
HAVING COUNT(*) FILTER (WHERE deleted_at IS NULL) > 1;
```

Healthy result: for each multi-appliance site, `avg_ring ==
total_active` AND `avg_peers == total_active - 1`.

### Prometheus

```bash
docker exec mcp-server curl -s http://localhost:8000/metrics | grep osiriscare_mesh
```

### Audit history

```sql
-- Recent assignment changes for a site
SELECT appliance_id, assignment_epoch, ring_size, target_count, created_at
  FROM mesh_assignment_audit
 WHERE site_id = '<site>'
 ORDER BY created_at DESC
 LIMIT 20;
```

> **Gotcha (carried from §8):** the audit table only writes a row
> when an assignment DIFFERS from the previous. No change = no row.
> A long quiet period in the audit means either (a) assignment
> stable, OR (b) the audit-write path is broken. Cross-check with
> `osiriscare_mesh_assignment_changes_1h` — if the metric also
> shows 0 over the same window, you're in case (a).

---

## 4. Incident playbook: ring drift

**Symptom:** `osiriscare_mesh_drift_sites > 0` OR admin dashboard
shows "Ring drift detected"

**Meaning:** number of appliances reporting ring size doesn't
match the count of NON-deleted online appliances.

**Severity:** P1 if isolated; **P0** if it correlates with a
target_orphans alert (drift turning into a coverage hole).

### Step 1: Identify the drifted site

Check the Mesh Health panel on the admin dashboard. Each
appliance's `ring_size` should equal the online count. If the
panel is unavailable, run the §3 SQL.

### Step 2: Check peer discovery

```sql
-- Are all NON-deleted appliances actually reachable?
SELECT appliance_id,
       last_checkin,
       daemon_health->>'mesh_peer_count' AS peers,
       daemon_health->>'mesh_ring_size' AS ring_size
  FROM site_appliances
 WHERE site_id = '<site>'
   AND deleted_at IS NULL
 ORDER BY appliance_id;
```

**If one appliance shows `peers=0`:** it's isolated. Check, in
order of likelihood:

1. **gRPC port reachability.** From the lagging appliance's
   WireGuard peer (e.g., the VPS), test port 50051 reach to each
   peer:

   ```bash
   ssh root@<appliance_wg_ip>
   for p in <peer1_ip> <peer2_ip>; do
       nc -zv -w 2 "$p" 50051 || echo "  ↑ unreachable"
   done
   ```

2. **CA cert match.** TLS probes fail silently when the peer's CA
   doesn't match. Verify the substrate CA cert is identical:

   ```bash
   ssh root@<appliance_wg_ip>
   sha256sum /etc/ssl/certs/msp-ca.crt  # compare across all peers
   ```

3. **Same-subnet peers — ARP.** On NixOS appliances use `ip neigh`
   (NOT `arp -a`):

   ```bash
   ip neigh show | grep -E "<peer_subnet_prefix>"
   ```

4. **WireGuard logs.** The systemd unit name on NixOS appliances
   should be `wg-quick-wg0.service` OR `wg-quick@wg0.service`
   depending on how the NixOS module was wired. If the first
   command returns "unit not found," try the others:

   ```bash
   journalctl -u wg-quick-wg0 -n 50 --no-pager 2>/dev/null \
     || journalctl -u wg-quick@wg0 -n 50 --no-pager 2>/dev/null \
     || systemctl list-units --type=service | grep -i wireguard
   ```

5. **Cross-subnet peers — backend delivery.** The checkin response
   carries peer info for cross-subnet meshes. Confirm the log line:

   ```bash
   docker logs mcp-server 2>&1 | grep "delivering N mesh peer" | tail -5
   ```

### Step 3: Force re-discovery via fleet order

The `force_checkin` order tells the lagging appliance to re-checkin
immediately, picking up fresh peer info:

```bash
docker exec mcp-server bash -lc \
  'cd /app/dashboard_api && python3 fleet_cli.py create force_checkin --param site_id=<site> --expires 1'
```

> **Syntax note (carried from 2026-05-06 audit):** `fleet_cli.py
> create` takes order_type as a POSITIONAL argument, NOT
> `--type=<x>`. Including the site_id requires `--param
> site_id=<value>`, since `fleet_orders` is fleet-wide and per-site
> scoping rides the parameters JSON (CLAUDE.md note: "Fleet CLI
> must include `--param site_id=<site>` for v0.3.82 compatibility").
> `force_checkin` is NOT in `PRIVILEGED_ORDER_TYPES`, so no
> `--actor-email` + `--reason` are required for this order — but
> consider including them anyway for incident-trail continuity.

### Step 4: Verify success

After Step 3, wait ~90s (one checkin cycle) then re-run §3 SQL.
Expected: `avg_ring` matches `total_active` for the affected site.
Also check Prometheus:

```bash
docker exec mcp-server curl -s http://localhost:8000/metrics | \
  grep "osiriscare_mesh_drift_sites"
```

Should return `0`. If still > 0 after 2 minutes, proceed to Step 5.

### Step 5: Restart the daemon (last resort)

> **SSH posture:** SSH to the appliance MUST go through the
> documented bastion + WireGuard hub (see `.agent/reference/NETWORK.md`).
> The bastion enforces MFA for the admin user; never SSH directly
> from your workstation. If you don't see the appliance via the
> bastion, the WireGuard tunnel is probably down — that's a
> different incident class (network-down) and Step 5 won't help.

```bash
ssh root@<appliance_wg_ip>          # via bastion + WG hub only
systemctl restart appliance-daemon
```

Triggers full peer re-discovery on startup. **Capture an audit
trail entry** since this is a substrate-class state change. Use
psql variable-substitution so a tired 2am responder doesn't paste
literal `<your-admin-uuid>` strings (which would silently fail the
INSERT and leave NO audit row):

```bash
docker exec -it mcp-server bash    # then run psql below
psql "$DATABASE_URL"
```

```sql
-- Substitute values at the top, then run the INSERT. psql will
-- error fast with "invalid UUID" if you forget — better than a
-- silent SQL error that leaves no audit trail.
\set admin_uuid 'XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX'
\set admin_email 'on-call@osiriscare.io'
\set appliance_id 'site-foo-AABBCCDDEEFF'
\set incident_ref 'PD-12345'
\set bastion_ip '<bastion-source-ip>'

INSERT INTO admin_audit_log (
    user_id, username, action, target, details, ip_address
)
VALUES (
    :'admin_uuid'::uuid,
    :'admin_email',
    'mesh_incident_daemon_restart',
    'site_appliance:' || :'appliance_id',
    jsonb_build_object(
        'reason', 'mesh ring drift unresolved after force_checkin',
        'incident_ref', :'incident_ref'
    ),
    :'bastion_ip'
);
```

If this remediation pattern becomes routine, file a follow-up
ticket for engineering to wire it into the existing
`appliance_relocation_acknowledged` attestation pattern (Session
207) — currently mesh-incident restarts ride only `admin_audit_log`,
not the cryptographic chain.

---

## 5. Incident playbook: target overlap

**Symptom:** `osiriscare_mesh_target_overlaps > 0`

**Severity:** P1 (wasted work, possible duplicate incidents; not a
coverage hole).

### Root cause

Usually means:

1. Two appliances briefly disagreed on ring membership during
   transition.
2. Backend-authoritative assignment hasn't propagated yet.
3. Local-ring fallback kicked in when server was briefly
   unreachable.

### Response

**Transient (< 30 min):** no action. The next checkin cycle (~5 min
per appliance) synchronizes assignments. Alert is suppressed for
the first 30 min via Grafana `for: 30m`.

**Persistent (> 30 min):** indicates stuck assignment. Check audit
history:

```sql
SELECT appliance_id, assignment_epoch, target_count, created_at
  FROM mesh_assignment_audit
 WHERE site_id = '<site>'
 ORDER BY created_at DESC
 LIMIT 10;
```

| Pattern observed | Diagnosis | Action |
|---|---|---|
| `assignment_epoch` differs across appliances | One has stale assignment | Force checkin (§4 Step 3) on the lagging one |
| `assignment_epoch` identical, but overlap persists | **BUG** — escalate engineering | §11 escalation |
| Audit table empty for the window | Audit-write path broken OR no changes | Cross-check `assignment_changes_1h` metric — if 0, assignments stable; if > 0, audit pipeline issue |

### Verify success

After force_checkin, wait two checkin cycles (~10 min) then check:

```bash
docker exec mcp-server curl -s http://localhost:8000/metrics | grep target_overlaps
```

Should return `0`.

---

## 6. Incident playbook: target orphan

**Symptom:** `osiriscare_mesh_target_orphans > 0`

**Severity:** **P0 — coverage hole. Customer targets are not being
scanned. Page on-call substrate engineer immediately.**

### Step 1: Identify orphaned targets

The orphan detector compares the SET of expected targets (per the
site's discovered devices) against the UNION of `assigned_targets`
across all NON-deleted online appliances. Any expected target not
appearing in any assignment is orphaned.

```sql
-- Build the universe of expected targets for the site from the
-- discovered_devices table (the platform's source-of-truth for
-- "what the appliance fleet has discovered to scan").
--
-- Earlier drafts of this runbook referenced site_credentials.windows_targets
-- — that COLUMN DOES NOT EXIST in the schema. Don't run that query;
-- use this one.
WITH expected_targets AS (
    SELECT DISTINCT ip_address AS target
      FROM discovered_devices
     WHERE site_id = '<site>'
       AND ip_address IS NOT NULL
),
covered_targets AS (
    SELECT DISTINCT jsonb_array_elements_text(assigned_targets) AS target
      FROM site_appliances
     WHERE site_id = '<site>'
       AND deleted_at IS NULL
       AND last_checkin > NOW() - INTERVAL '5 minutes'
)
SELECT et.target
  FROM expected_targets et
 WHERE NOT EXISTS (
     SELECT 1 FROM covered_targets ct WHERE ct.target = et.target
 );
```

If the result is empty but the metric still > 0, the metric query
in `prometheus_metrics.py` may be using a different definition of
"expected" — file an engineering ticket.

### Step 2: Force assignment recomputation

```bash
docker exec mcp-server bash -lc \
  'cd /app/dashboard_api && python3 fleet_cli.py create force_checkin --param site_id=<site> --expires 1'
```

### Step 3: Verify hash-ring consistency

If the orphan persists after force_checkin, the Python `hash_ring`
and the Go `mesh.go` may disagree on which node owns the orphaned
target. The cross-language vector test pins this:

```bash
docker exec mcp-server python3 -m pytest tests/test_hash_ring.py -v
```

Failure → engineering escalation. The Go daemon binary cannot be
patched in place; fixing requires a daemon rebuild + redeploy via
the Nix flake (`nix build .#appliance-iso`) — multi-hour cycle.

### Step 4: Temporary workaround — single-appliance fallback

If steps 1-3 are not converging within 15 min and the coverage hole
is widening, fall back to single-appliance-per-site mode where each
appliance scans its full configured target set (duplicate work but
no coverage hole).

> **DANGER:** the workaround requires a SITE-WIDE UPDATE on
> `site_appliances`. Per CLAUDE.md, site-wide UPDATEs MUST set
> `app.allow_multi_row='true'` for the same transaction. Failing
> to do so makes the UPDATE either fail (with the row-guard
> trigger active) OR succeed but leave an unaudited mass-mutation.

Use psql variable-substitution so the placeholders don't ride
through into the actual SQL — Postgres will error fast on missing
substitutions instead of silently leaving an audit row with literal
`<your-admin-uuid>` text.

```sql
\set admin_uuid 'XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX'
\set admin_email 'on-call@osiriscare.io'
\set site_id 'north-valley-branch-2'
\set incident_ref 'PD-12345'
\set bastion_ip '<bastion-source-ip>'

BEGIN;
SET LOCAL app.allow_multi_row = 'true';

-- Audit-trail the override BEFORE the mutation
INSERT INTO admin_audit_log (
    user_id, username, action, target, details, ip_address
)
VALUES (
    :'admin_uuid'::uuid,
    :'admin_email',
    'mesh_incident_single_appliance_fallback',
    'site:' || :'site_id',
    jsonb_build_object(
        'reason', 'target_orphan unresolved; falling back to single-appliance scan to close coverage hole',
        'incident_ref', :'incident_ref',
        'expected_recovery', 'within 30 min after fix is deployed; revert via re-issuing force_checkin once mesh is restored'
    ),
    :'bastion_ip'
);

UPDATE site_appliances
   SET assigned_targets = NULL
 WHERE site_id = :'site_id'
   AND deleted_at IS NULL;

COMMIT;
```

Each appliance will scan everything until the next checkin
re-establishes assignments. After fix is deployed, re-issue
force_checkin and confirm the metric returns to 0.

### Step 5: Verify success

```bash
docker exec mcp-server curl -s http://localhost:8000/metrics | grep target_orphans
```

Should return `0`. If you used Step 4 fallback, also confirm the
`admin_audit_log` row was written.

---

## 7. Incident playbook: assignment thrashing

**Symptom:** `osiriscare_mesh_assignment_changes_1h > 50`

**Severity:** P2 — symptom of underlying network instability.
Doesn't itself cause a coverage hole, but precedes ring drift.

### Step 1: Identify the flapping appliance

```sql
SELECT appliance_id, COUNT(*) AS change_count
  FROM mesh_assignment_audit
 WHERE site_id = '<site>'
   AND created_at > NOW() - INTERVAL '1 hour'
 GROUP BY appliance_id
 ORDER BY change_count DESC;
```

### Step 2: Diagnose its network

A flapping peer means it's joining/leaving the ring rapidly:

- Unstable WAN link (cross-subnet mesh)
- Intermittent WireGuard tunnel
- Network flap between checkin cycles

```bash
ssh root@<appliance_wg_ip>
journalctl -u wg-quick@wg0 -n 50 --no-pager
ping -c 10 -W 1 <peer_ip>
ip -s link show wg0  # check rx/tx errors + drops
```

### Step 3: Grace-period extension is NOT a hot fix

The runbook previously suggested "Edit `mesh.go` to bump
gracePeriod." Reality: `mesh.go` is compiled into the appliance
daemon binary. Changes require:

1. Patch `appliance/internal/daemon/mesh.go` with a new constant.
2. Rebuild via `nix build .#appliance-iso` OR `nix build .#appliance-daemon`.
3. Push the new daemon to the fleet via the documented update flow
   (NOT scp — see CLAUDE.md "DEPLOY VIA GIT PUSH, NOT SCP").

This is a multi-hour deploy cycle, not a hot fix. For during-
incident response, focus on Step 4 — the underlying network is
the right thing to stabilize.

### Step 4: Permanent fix

Usually requires fixing the underlying network: replace router,
upgrade ISP circuit, move flapping link to wired, or split the
multi-appliance site into single-subnet mesh groups if the cross-
subnet path is unreliable.

### Verify success

After the network fix, wait 1 hour then check:

```bash
docker exec mcp-server curl -s http://localhost:8000/metrics | grep assignment_changes_1h
```

Should fall below 10/hour.

---

## 8. Known issues / gotchas

1. **Cross-subnet requires backend delivery.** ARP only works on same
   subnet. Check that `Checkin site: delivering N mesh peer(s)` log
   appears for every cross-subnet site.
2. **Round-robin vs hash ring.** For targets < 2× nodes, the assignment
   uses deterministic round-robin (`sites.py` STEP 3.8c). Fewer targets
   than expected? Check the round-robin path before suspecting the
   hash ring.
3. **Ring epoch is Unix timestamp.** Clock skew > 1 minute between
   appliances could cause assignment rejection. NixOS appliances use
   `systemd-timesyncd` by default; confirm it's running:
   ```bash
   ssh root@<appliance_wg_ip> 'systemctl status systemd-timesyncd'
   ```
4. **TLS probe requires matching CA.** If an appliance joins the mesh
   with a different CA, TLS probe fails silently and the peer never
   joins the ring. Check `/etc/ssl/certs/msp-ca.crt` SHA matches across
   all peers (§4 Step 2).
5. **Audit entries only on change.** The `mesh_assignment_audit` table
   only gets rows when assignment DIFFERS from previous. No change =
   no row. A long quiet period in the audit could mean (a) stable
   assignment OR (b) audit-write path broken. Cross-check the metric
   `osiriscare_mesh_assignment_changes_1h` — if 0, you're in case (a).
6. **Soft-deleted appliances must be excluded.** Every diagnostic SQL
   in this runbook filters `deleted_at IS NULL`. The
   `mesh_consistency_check_loop` was missing this filter pre-2026-05-06
   and produced phantom drift alerts. The CI gate
   `tests/test_no_unfiltered_site_appliances_select.py` ratchets the
   total backend-wide; new code that adds an unfiltered query without
   `# noqa: site-appliances-deleted-include` fails the gate.

---

## 9. Reference

- **Mesh design:** `docs/ARCHITECTURE.md` (mesh section)
- **Hash ring (Python):** `mcp-server/central-command/backend/hash_ring.py`
- **Hash ring (Go):** `appliance/internal/daemon/mesh.go`
- **Assignment compute:** `mcp-server/central-command/backend/sites.py` STEP 3.8c (~line 5157)
- **Consistency check loop:** `mcp-server/central-command/backend/background_tasks.py::mesh_consistency_check_loop` (~line 743). **Verified 2026-05-06** to filter `deleted_at IS NULL`.
- **Audit table:** `mesh_assignment_audit` (mig 145)
- **Target-assignments column:** `site_appliances.assigned_targets` JSONB (mig 127)
- **Soft-delete column:** `site_appliances.deleted_at` (mig 153)
- **Dashboard panel:** `mcp-server/central-command/frontend/src/components/composed/MeshHealthPanel.tsx`
- **Tests:** `tests/test_hash_ring.py`, `tests/test_target_assignment.py`, `appliance/internal/daemon/mesh_test.go`
- **Soft-delete gate:** `tests/test_no_unfiltered_site_appliances_select.py`
- **Discovered targets source:** `discovered_devices` table

---

## 10. Post-incident actions

After resolving any P0/P1 incident:

1. **Postmortem.** Capture timeline, root cause, remediation, and
   detection-gap analysis. File under `.agent/sessions/YYYY-MM-DD-mesh-
   incident-<short-desc>.md` per the project session-tracking pattern.
2. **Runbook update.** If any step in this runbook was wrong,
   incomplete, or misled the responder, fix it in this document AND
   update the change log at §12. The runbook is the artifact future
   responders trust at 2am — drift here is the most expensive class
   of drift on the platform.
3. **Engineering ticket.** If the incident pointed at a code-level bug
   (hash-ring divergence, audit-pipeline gap, etc.), open a ticket
   referencing the `incident_ref` captured in `admin_audit_log`.
4. **Substrate invariant audit.** If the incident pattern wasn't
   caught by an existing substrate invariant in `assertions.py`,
   propose a new one in the post-incident doc. Mesh-class events
   currently rely on Prometheus + this runbook; substrate invariants
   would catch the same patterns at the substrate-engine layer.

---

## 11. Escalation matrix

| Condition | Response time | First responder | Escalation |
|---|---|---|---|
| Target orphan ANY (P0) | Immediate (page) | substrate on-call | If unresolved 30 min → backend lead. If unresolved 1 hr → CTO |
| Full mesh down (all peers = 0) | Immediate (page) | substrate on-call | Network-class first; CCIE rotation if VPN/TLS-class |
| Ring drift > 10 min (P1) | 15 min | substrate on-call | Backend lead if unresolved 1 hour |
| Target overlap > 30 min (P1) | 30 min | substrate on-call | Engineering ticket if `assignment_epoch` identical (BUG) |
| Assignment thrashing > 30 min (P2) | 1 hour | substrate on-call | CCIE rotation for network class |

**On-call rotation:** documented in `.agent/reference/`. Pager via
your team's on-call platform (PagerDuty / OpsGenie). Substrate on-
call for all of the above; CCIE rotation engaged via "join page"
when network-class is suspected.

**Communication during incident:**

- Update an incident channel (Slack / equivalent) every 15 min with
  diagnostic findings + actions taken, regardless of resolution
  state.
- Capture every command run during incident in the channel. Future
  postmortem reviewers MUST be able to reconstruct the timeline.
- DO NOT make code changes to production during a P0 without a
  reviewer ack in the channel — even a Bash one-liner. Mesh-class
  bugs have ripple effects.

---

## 12. Change log

- **2026-05-06 (v2.1)** — post-fix round-table 2nd-eye. After v2
  shipped (commit 2ebeeffb), a process check identified that the
  rewrite itself had not been adversarially reviewed. Re-convened
  Sam/Carol/Dana/Adam/Steve/Maya on the SHIPPED commit. Two sev1
  claims surfaced were FALSE ALARMS (the agent missed
  `ALTER TABLE discovered_devices ADD COLUMN site_id` from mig 080
  and the parallel `device_status` add from mig 096). Five real
  hardenings applied:
    - §3 quick-health command reworked to drop into container shell
      first, keeping `DATABASE_URL` out of local bash history (Steve)
    - §4 Step 2 + §5 added WireGuard systemd unit fallbacks for
      NixOS variants (`wg-quick-wg0` vs `wg-quick@wg0`) (Carol)
    - §4 Step 5 + §6 Step 4 audit-INSERTs converted to psql
      `\set` variable-substitution pattern so a 2am responder
      pasting placeholders gets a fast error instead of a silent
      no-audit-row failure (Sam + Steve)
    - §4 Step 5 SSH callout requires bastion + WG hub (MFA-gated)
      with a clarifying note (Steve)
    - §12 change log gained the RT-ticket / commit-reference
      pattern for full traceability (Maya)
- **2026-05-06 (v2)** — adversarial audit + Maya 2nd-eye + production fix.
  Round-table found 23 issues across the v1 runbook; v2 is a full
  rewrite addressing every finding (commit 2ebeeffb):
  - Production fix: `mesh_consistency_check_loop()` now filters
    `deleted_at IS NULL` on both the per-site rollup and per-
    appliance ring queries (false ring-drift class fixed).
    `tests/test_no_unfiltered_site_appliances_select.py` baseline
    ratcheted 85 → 83.
  - SQL diagnostics now filter soft-deleted appliances throughout.
  - §6 orphan-detection query rewritten — `site_credentials.windows_
    targets` column DOES NOT EXIST; v1 query would have raised
    "column does not exist." Replaced with `discovered_devices`-
    based universe.
  - `fleet_cli.py` syntax corrected — order_type is positional, not
    `--type`; site_id rides via `--param site_id=<value>`.
  - Grafana annotations path corrected to `docs/runbooks/`
    (was 404'ing from alerts).
  - §11 escalation matrix populated with on-call + CCIE rotation +
    response-time tree (was vague "Platform Engineering").
  - §10 post-incident-actions section added.
  - "Verify success" steps added to every incident playbook.
  - Site-wide UPDATE workaround in §6 Step 4 wrapped in
    `SET LOCAL app.allow_multi_row='true'` + audit-log capture
    (was raw site-wide UPDATE that would either fail the row-guard
    or bypass audit).
  - §4 Step 5 daemon restart now requires `admin_audit_log` entry
    (was untracked). Future enhancement noted: wire to the
    `appliance_relocation_acknowledged`-class attestation chain.
  - Removed reference to "edit mesh.go for hot-fix" — that's a
    multi-hour daemon-rebuild path, not a hot fix.
  - Quick-health Python one-liner replaced with `psql "$DATABASE_URL"`
    (was string-replacing `DATABASE_URL` to bypass PgBouncer + RLS).
- **TBD next verification:** every 90 days OR after any mesh-class
  incident. Engineer responsible for the verification stamps the
  date on this section's first line.

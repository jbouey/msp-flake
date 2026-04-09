# OpenTimestamps Incident Response Runbook

**Scope:** Operational procedures for OTS proof pipeline failures, calendar outages, and blockchain verification issues.

**Owner:** Platform Engineering
**Escalation:** If anchor lag > 24h OR all calendars down > 4h

---

## 1. Monitoring Alerts

### Primary Metrics (Prometheus)

| Metric | Healthy | Warning | Critical |
|--------|---------|---------|----------|
| `osiriscare_ots_latest_anchor_age_seconds` | < 7200 (2h) | > 21600 (6h) | > 86400 (24h) |
| `osiriscare_ots_oldest_pending_seconds` | < 43200 (12h) | > 86400 (24h) | > 172800 (48h) |
| `osiriscare_ots_proofs{status="pending"}` | < 100 | > 500 | > 2000 |
| `osiriscare_ots_proofs{status="expired"}` | 0 | > 10 | > 100 |
| `osiriscare_ots_calendar_success_24h` | >= 24 per calendar | 1-23 | 0 |

### Grafana Alert Rules

```yaml
- alert: OTSAnchorLag
  expr: osiriscare_ots_latest_anchor_age_seconds > 21600
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "OTS proofs not anchoring for {{ $value }}s"
    runbook: "docs/OTS_INCIDENT_RUNBOOK.md#calendar-outage"

- alert: OTSCalendarDown
  expr: osiriscare_ots_calendar_success_24h == 0
  for: 4h
  labels:
    severity: critical
  annotations:
    summary: "Calendar {{ $labels.calendar }} has 0 successful anchors in 24h"
    runbook: "docs/OTS_INCIDENT_RUNBOOK.md#calendar-outage"
```

---

## 2. Diagnostic Commands

### Quick Health Check

```bash
# VPS: Check current pipeline state
ssh root@178.156.162.116
docker exec mcp-server python3 -c "
import asyncio, asyncpg, os
async def check():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('+asyncpg','').replace('pgbouncer:6432','mcp-postgres:5432').replace('mcp_app','mcp'))
    stats = await conn.fetch('''
        SELECT status, COUNT(*),
               MAX(anchored_at) as last_anchor,
               MIN(submitted_at) FILTER (WHERE status='pending') as oldest_pending
        FROM ots_proofs GROUP BY status
    ''')
    for s in stats: print(s)
    await conn.close()
asyncio.run(check())
"
```

### Backend Logs

```bash
docker logs mcp-server 2>&1 | grep -i ots | tail -30
docker logs mcp-server 2>&1 | grep "OTS_REVERIFY_FAILURE"
```

### Prometheus

```bash
curl -s http://localhost:8000/metrics | grep osiriscare_ots
```

---

## 3. Incident Playbook: Calendar Outage

**Symptom:** `osiriscare_ots_calendar_success_24h == 0` for one or more calendars.

### Step 1: Verify Calendar Server Status

```bash
# Test each calendar manually
curl -X POST https://alice.btc.calendar.opentimestamps.org/digest \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-binary @<(printf '\x00%.0s' {1..32})

curl -X POST https://bob.btc.calendar.opentimestamps.org/digest ...
curl -X POST https://finney.calendar.eternitywall.com/digest ...
```

If HTTP 200 → calendar is up. If timeout/error → outage confirmed.

### Step 2: Check OTS Community Status

- Twitter: @opentimestamps
- GitHub Issues: https://github.com/opentimestamps/opentimestamps-server/issues
- Mattermost: https://mattermost.opentimestamps.org

### Step 3: Failover

If primary calendar is down, the system already falls back to the next in `OTS_CALENDARS` list. If ALL are down:

1. **Do nothing** — bundles will batch and retry on next cycle
2. Proofs mark as `pending` — they will anchor when calendars recover
3. Anchor lag alerts will fire but data is not lost

### Step 4: Manual Resubmit (if calendars recover after 24h+)

```bash
# Trigger manual resubmit via API
curl -X POST https://api.osiriscare.net/api/dashboard/ots/resubmit-expired \
  -H "Cookie: session=..." \
  -d '{"limit": 500}'
```

### Step 5: Worst Case — Add New Calendar

Edit `mcp-server/central-command/backend/evidence_chain.py`:

```python
OTS_CALENDARS = [
    "https://alice.btc.calendar.opentimestamps.org",
    "https://bob.btc.calendar.opentimestamps.org",
    "https://finney.calendar.eternitywall.com",
    "https://new-calendar.example.com",  # Add here
]
```

Deploy via CI/CD.

---

## 4. Incident Playbook: Re-Verification Failure

**Symptom:** Log line `OTS_REVERIFY_FAILURE: anchored proofs failed re-verification`

### Investigation

The `ots_reverify_sample_loop` samples 10 random anchored proofs every 6 hours. Any failure indicates:

1. **Parse failure** (`parse_failed`) — `proof_data` column corruption
2. **Hash mismatch** (`hash_mismatch`) — bundle hash changed after anchoring (TAMPERING)
3. **Replay failure** (`replay_failed`) — parser bug or truncated proof
4. **Block fetch failed** (`block_fetch_failed`) — blockstream.info outage (not a real failure)

### Response

**If hash_mismatch on ANY proof:**
- **STOP** — this is potential tampering
- Immediately snapshot the database
- Compare with backup from 7 days ago
- Audit `admin_audit_log` for any manual UPDATEs on `compliance_bundles` or `ots_proofs`
- Escalate to CISO

**If parse_failed / replay_failed:**
- Get the failing bundle_id from the log
- Export the proof: `SELECT proof_data FROM ots_proofs WHERE bundle_id = '<id>'`
- Base64 decode, save as `broken.ots`
- Try `ots info broken.ots` — if OTS CLI can't parse either, the data was corrupted at write time

**If block_fetch_failed:**
- Not an integrity issue — blockstream.info is flaky
- Will retry on next cycle (6h later)
- If persists > 24h, switch to mempool.space as secondary

---

## 5. Chain-of-Custody Export (For Auditors)

### Generating Export

```bash
# REST API
curl "https://api.osiriscare.net/api/dashboard/sites/{site_id}/chain-of-custody?start_date=2026-01-01&end_date=2026-03-31" \
  -H "Cookie: session=..." > coc-q1-2026.json
```

### Independent Verification (Auditor Instructions)

The export is self-contained. Auditors need only:

1. **`sha256sum`** — verify bundle hashes
2. **`ots verify`** — verify OpenTimestamps proofs (install: `pip install opentimestamps-client`)
3. **Any Bitcoin block explorer** — confirm block heights exist

Steps for each bundle in the export:

```bash
# 1. Verify hash chain
prev_hash=""
for bundle in $(jq -c '.chain_of_custody.bundles[]' coc-q1-2026.json); do
    actual_prev=$(echo $bundle | jq -r '.prev_sha256')
    if [ -n "$prev_hash" ] && [ "$prev_hash" != "$actual_prev" ]; then
        echo "CHAIN BREAK at $(echo $bundle | jq -r '.bundle_id')"
    fi
    prev_hash=$(echo $bundle | jq -r '.sha256')
done

# 2. Verify OTS proof for a single bundle
echo '<base64_proof>' | base64 -d > proof.ots
ots verify proof.ots  # Queries calendar, confirms Bitcoin anchor

# 3. Verify Bitcoin block exists
curl https://blockstream.info/api/block-height/944296
```

---

## 6. Known Issues / Gotchas

1. **Merkle batching is hourly per site** — fresh bundles wait up to 60 minutes before entering the pipeline
2. **Bitcoin confirmations take 1-6 hours** — pending → anchored is not instant
3. **Calendar downtime is common** — OTS is community-run, not a paid SaaS. 4-24h outages happen
4. **Block reorgs** — Bitcoin can reorg up to ~6 blocks. Proofs anchored in the last hour may shift block numbers
5. **`blockstream.info` rate limits** — the re-verify loop uses public API. At scale, consider self-hosting a Bitcoin node

---

## 7. Escalation Matrix

| Condition | Response Time | Action |
|-----------|---------------|--------|
| Calendar success rate < 50% (1h) | 1 hour | Investigate, manual test calendars |
| Anchor lag > 6h | 4 hours | File OTS GitHub issue, monitor |
| Anchor lag > 24h | Immediate | Page on-call, consider adding calendar |
| `OTS_REVERIFY_FAILURE` with hash_mismatch | Immediate | CISO + freeze deployments |
| All calendars down > 4h | 30 min | Post status page update, email clients |

---

## 8. Reference

- **OpenTimestamps spec:** https://github.com/opentimestamps/python-opentimestamps/blob/master/README.md
- **OTS server code:** https://github.com/opentimestamps/opentimestamps-server
- **Blockstream API:** https://github.com/Blockstream/esplora/blob/master/API.md
- **Our implementation:** `mcp-server/central-command/backend/evidence_chain.py`
- **Background loops:** `mcp-server/central-command/backend/background_tasks.py`
- **Parser tests:** `mcp-server/central-command/backend/tests/test_ots_parser.py`

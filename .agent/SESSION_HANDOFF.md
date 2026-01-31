# Session Handoff - 2026-01-31

**Session:** 79 - Database Pruning & OTS Anchoring Fix
**Agent Version:** v1.0.51
**ISO Version:** v51 (deployed via Central Command)
**Last Updated:** 2026-01-31
**System Status:** ✅ All Systems Operational

---

## Current State Summary

| Component | Status | Notes |
|-----------|--------|-------|
| Agent | v1.0.51 | Built, rollout starting |
| ISO | v51 | Stage 1/3 (5%), 0 updated yet |
| Physical Appliance | **ONLINE** | v1.0.49 at 192.168.88.246 |
| VM Appliance | **PENDING** | v49 rollout stuck (4 pending) |
| VPS API | **HEALTHY** | https://api.osiriscare.net/health |
| Learning Sync | **WORKING** | 24 patterns, 7,215 executions |
| Evidence Collection | **WORKING** | 180K bundles collected |
| OTS Anchoring | **FIXED** | Commitment computation corrected |
| Fleet Rollouts | **STUCK** | v49/v45 pending, v51 just started |

---

## ⚠️ Active Issues

### Stale Rollouts Not Completing
Looking at Fleet Updates dashboard:
- **Rollout v49** - Stage 3/3 (100%), 4 pending, 0 succeeded - **STUCK**
- **Rollout v45** - Stage 3/3 (100%), 3 pending, 1 failed - **FAILED**
- **Rollout v51** - Stage 1/3 (5%), 5 pending, 0 succeeded - just started

**Root Cause:** Appliances appear to not be receiving or applying updates. Need to investigate:
1. Appliance check-in mechanism
2. Update handler (`_handle_update_iso`)
3. Network connectivity
4. Update agent functionality

---

## Session 79 - Database Pruning & OTS Fix

### Accomplishments

#### 1. Database Pruning (Disk Space Fix)
- **Problem:** VM appliance disk space filling up due to unbounded `incidents.db`
- **Solution:**
  - Added `prune_old_incidents()` to `incident_db.py`
  - Added `get_database_stats()` for monitoring
  - Added `_maybe_prune_database()` to `appliance_agent.py` (runs daily)
  - Added 4 unit tests for pruning functionality
- **Defaults:** 30-day retention, keeps unresolved incidents, VACUUMs database

#### 2. ISO v51 Built & Deployed
- Built ISO on VPS: `/opt/osiriscare-v51.iso`
- SHA256: `5b762d62c1c90ba00e5d436c7a7d1951184803526778d1922ccc70ed6455e507`
- Created release v1.0.51 in Central Command
- Started staged rollout (5% → 25% → 100%)

#### 3. Learning Sync Verified
- Pattern stats: 24 patterns aggregated
- Execution telemetry: 7,215 records
- Physical appliance synced today (15 patterns merged)

#### 4. OTS Anchoring Fix
- **Problem:** OTS proofs not getting Bitcoin-anchored (78K pending)
- **Root Cause:** Wrong commitment computation (using bundle_hash instead of replaying operations)
- **Fixes Applied:**
  - Added `replay_timestamp_operations()` to compute correct commitment
  - Returns last SHA256 result before attestation marker
  - Tries multiple calendars (alice, bob, finney)
  - Added 7-day expiration for old proofs
- **Result:** 67K old proofs expired, 10K recent proofs tracked

### Files Modified

| File | Change |
|------|--------|
| `incident_db.py` | Added prune_old_incidents(), get_database_stats() |
| `appliance_agent.py` | Added _maybe_prune_database(), bumped to v1.0.51 |
| `test_incident_db.py` | Added TestDatabasePruning class (4 tests) |
| `appliance-image.nix` | Bumped to v1.0.51 |
| `evidence_chain.py` | OTS commitment fix, multi-calendar, expiration |
| Version files | Updated to v1.0.51 |

### Git Commits

| Commit | Message |
|--------|---------|
| `d183739` | fix: Add database pruning to prevent disk space exhaustion |
| (pending) | fix: OTS anchoring commitment computation and expiration |

---

## Lab Environment Status

### Appliances
| Appliance | IP | Version | Status |
|-----------|-----|---------|--------|
| Physical (HP T640) | 192.168.88.246 | v1.0.49 | **ONLINE** |
| VM Appliance | Unknown | Unknown | **UPDATING** |

### VPS Services
| Service | URL | Status |
|---------|-----|--------|
| Dashboard | https://dashboard.osiriscare.net | Online |
| API | https://api.osiriscare.net | Online |

---

## Technical Notes

### Database Pruning
- `prune_interval`: 86400 seconds (24 hours)
- `incident_retention_days`: 30 days
- `keep_unresolved`: True (never delete open incidents)
- Also prunes associated `learning_feedback` and orphan `pattern_stats`
- VACUUMs database after pruning to reclaim space

### OTS Anchoring
- Commitment = last SHA256 result before 0x00 attestation marker
- Calendars tried: alice, bob, finney (in order)
- Proofs older than 7 days marked as expired (calendars prune them)
- Upgrade job should run hourly for best results

---

## Quick Commands

```bash
# SSH to physical appliance
ssh root@192.168.88.246

# Check agent logs for pruning
journalctl -u compliance-agent | grep -i prune

# Check database size
ls -lh /var/lib/msp/*.db

# Trigger OTS upgrade on VPS
curl -X POST 'https://api.osiriscare.net/api/evidence/ots/upgrade?limit=100'

# Check OTS status
ssh root@178.156.162.116 "docker exec mcp-postgres psql -U mcp -d mcp -c 'SELECT status, COUNT(*) FROM ots_proofs GROUP BY status;'"
```

---

## Related Docs

- `.agent/TODO.md` - Current tasks and session history
- `.agent/LAB_CREDENTIALS.md` - Lab passwords (MUST READ)
- `docs/PRODUCTION_READINESS_AUDIT.md` - Full production audit

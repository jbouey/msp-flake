# Session 105: Chaos Test Validation + Reliability Scorecard

**Date:** 2026-02-11
**Duration:** ~1.5 hours (continuation of session 104)
**Agent Version:** 1.0.64

## What Was Done

### 1. Clean Chaos Test Execution
- Deleted incidents.db to clear all flap suppressions from prior tests
- Restarted agent, waited for first scan cycle to complete
- Launched FULL_SPECTRUM_CHAOS.sh from iMac (192.168.88.50)
- All 8 Linux attacks + 13 Windows attacks executed successfully

### 2. Chaos Test Analysis (5 runs total across sessions 104-105)
Analyzed agent logs during and after 180s healing window:

**Linux healing results (from agent logs, post-180s):**
- LIN-SSH-001 (RootLogin): SUCCESS at ~4min
- LIN-FW-001 (Firewall): SUCCESS at ~5min
- LIN-SVC-002 (Audit/rsyslog): SUCCESS at ~6min
- LIN-SUID-001 (SUID removal): SUCCESS at ~6min
- LIN-KERN-001 (IP forward): SUCCESS at ~6min
- LIN-CRON-001 (Cron perms): SUCCESS at ~6min
- LIN-SSH-002/003/004: FLAP SUPPRESSED (pre-existing drift contamination)
- LIN-KERN-002: FLAP SUPPRESSED (same)

**Windows healing results:**
- DC/WS Firewalls: 100% healed within 180s (all 5 runs)
- SRV Firewall: ~40% within 180s (depends on scan timing)
- Registry persistence: 100% healed
- Scheduled task persistence: healed (second cycle)
- Audit policy: healed (second cycle)
- No runbook: DNS hijack, SMB signing, network profile, WinUpdate, screen lock

### 3. Production Reliability Scorecard
| Metric | Basic Compliance | Full Coverage |
|--------|-----------------|---------------|
| Detection | 100% | 100% |
| Rule Coverage | 15/15 = 100% | 15/21 = 71% |
| Execution Success | 100% | 100% |
| Mean Time to Heal | Linux 4-6min, Win 1-3min | Same |
| Composite Score | **95/100** | **68/100** |

### 4. Documentation Updates
- `hipaa/compliance.md`: Added flap detection section (thresholds, granular keys, synced rules)
- `nixos/infrastructure.md`: Added overlay system section (package structure, build command)
- `claude-progress.json`: Updated to session 105, agent v1.0.64, new lessons and tasks

### 5. Root Cause Analysis
Two structural issues identified:
1. **Scan cycle serialization**: Linux + Windows in same cycle, 300s timeout. Linux attacks during Windows scan phase aren't detected for 5-7min
2. **Flap false positives**: Pre-existing drift healed before chaos attacks → flap counter incremented → chaos re-break triggers suppression at count 3

## Key Commits (from session 104, continued here)
- `574286a` - fix: Granular flap detection — use runbook_id to prevent false positives

## Pending Items
- Task #22: Decouple Linux/Windows scan cycles (HIGH)
- Task #23: Add 6 missing Windows runbooks (MEDIUM)
- Task #24: Add L1 rules for 4 Linux checks (MEDIUM)
- Task #25: Fix flap tracker counting failed heals (MEDIUM)
- Task #26: Upload v1.0.64 overlay to VPS (LOW)
- Task #20: Fix sed quoting in chaos script (MEDIUM)
- Task #2: Rotate leaked credentials (HIGH)

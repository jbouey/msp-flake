# Session 190 — OpenClaw Outage + Healing Pipeline Fix

**Date:** 2026-03-30 / 2026-03-31
**Previous Session:** 189

---

## Goals
- [x] Diagnose + fix OpenClaw 502 outage
- [x] Security audit (compromise check)
- [x] Optimize OpenClaw for 8GB VPS
- [x] Audit healing pipeline end-to-end in production
- [x] Fix L1 rule matching for 9 failing chaos categories
- [x] Fix chaos lab parser + scoring
- [ ] Run chaos test to 90%+ (pending — 6AM cron will produce first score)

---

## Progress

### OpenClaw (178.156.243.221)
- Gateway dead since 2026-03-23 (update button killed process, service not enabled)
- No compromise — only owner SSH key, fail2ban active
- Enabled service, updated 2026.3.22→2026.3.28, built UI
- Optimized: NODE_COMPILE_CACHE, NO_RESPAWN, concurrency reduced, context pruning
- Heartbeat+compaction → openai/gpt-4.1-nano (8x cheaper than Haiku)

### Healing Pipeline (CRITICAL FIX)
- **Root cause:** `/api/agent/l2/plan` had NO L1 rule lookup — bypassed all 112 L1 rules, went straight to LLM
- **Fix:** Added L1 DB query + keyword fallback before LLM in agent_api.py
- **Synced** keyword fallback map (added credential, smb to main.py)
- **Verified on appliance:** 4/5 non-monitoring checks healing (bitlocker 2671ms, defender 2685ms)

### Chaos Lab Fixes (iMac 192.168.88.50)
- Parser v13 pattern added — was parsing 0 scenarios, now works
- Morning/afternoon result split (separate score files)
- end_of_day_report.py merges both runs
- Added 1PM scenario regeneration cron based on mid-day gaps

### iMac SSH Issue
- Port 22 keeps closing after macOS updates (Big Sur known bug)
- Remote Login is ON, firewall unblocked, but port still drops
- Need LaunchDaemon plist installed locally (requires sudo at console)

---

## Files Changed

| File | Change |
|------|--------|
| mcp-server/central-command/backend/agent_api.py | Added L1 lookup + keyword fallback to /api/agent/l2/plan |
| mcp-server/main.py | Added credential, smb to keyword fallback map |
| mcp-server/central-command/backend/tests/test_l2_spend.py | Updated tests + added L1 match tests |
| (VPS) /etc/systemd/system/openclaw-gateway.service | NODE_COMPILE_CACHE + NO_RESPAWN |
| (VPS) /root/.openclaw/openclaw.json | Model + performance tuning |
| (iMac) chaos-lab/scripts/parse_execution_log.py | Added v13 log format pattern |
| (iMac) chaos-lab/execution_wrapper.sh | Morning/afternoon result split |
| (iMac) chaos-lab/scripts/end_of_day_report.py | Merge morning+afternoon results |
| (iMac) crontab | Added 1PM regen, kept 2x daily execution |
| (iMac) chaos-lab/scripts/email_results.py | NEW — emails score after every execution run |
| (iMac) execution_wrapper.sh | Added post-run email call |
| (iMac) /Library/LaunchDaemons/com.local.enablessh.plist | NEW — SSH persistence across reboots |

---

## Next Session
1. Check chaos lab score email — run in progress (PID 2257), should arrive within ~2 hours
2. Fix remaining healing gaps based on score: firewall_dangerous_rules, screen_lock_policy, registry_persistence
3. Iterate: fix → run → score → fix until 90%+
4. All 4 VMs running (DC, ws01, srv01, linux)
2. Install SSH persistence LaunchDaemon on iMac (needs console access)
3. Fix remaining healing gaps: firewall_dangerous_rules, screen_lock_policy, registry_persistence
4. Investigate iMac ARP conflict (b8:09:8a:c6:e5:e7 appears for .50, .250, .251)

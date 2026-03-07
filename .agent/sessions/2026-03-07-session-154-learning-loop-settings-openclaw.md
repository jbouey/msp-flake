# Session 154: Learning Loop Fixes, Settings Page, OpenClaw Config

**Date:** 2026-03-07
**Duration:** ~3 hours

## Summary

Fixed three bugs on the Learning Loop page, added five new Settings sections, and reconfigured the OpenClaw server for optimal model usage and origin access.

## Changes Made

### Learning Loop Page (3 bugs fixed)
1. **Invisible badge text** — `bg-level-l1/l2/l3` colors weren't defined in `tailwind.config.js`. Added `level.l1` (green), `level.l2` (orange), `level.l3` (red).
2. **Empty promotion timeline** — Frontend calls `/api/dashboard/learning/history` (routes.py), NOT `/api/learning/history` (main.py). Routes.py queried empty legacy `patterns` table. Fixed to query `learning_promotion_candidates` with execution stats via `execution_telemetry` lateral join.
3. **False coverage gaps (50% → 85%)** — Coverage query only checked `incident_pattern->>'check_type'` but most L1 rules store `incident_type`. Fixed by checking both JSONB keys + fuzzy rule_id matching.

### Settings Page (5 new sections)
- **Default Healing Tier** — standard/full_coverage/monitor_only dropdown
- **Learning Loop** — min success rate, min executions, auto-promote toggle
- **SMTP Configuration** — host, port, from, username, password, TLS toggle
- **Branding** — company name, logo URL, support email
- **Evidence Storage** — MinIO endpoint, WORM bucket, OTS calendar URL, retention days

### OpenClaw Server (178.156.243.221)
- Reconfigured model chain: `haiku → gpt-4o-mini → sonnet` (was haiku → sonnet → ollama)
- Added OpenAI API key as failover provider
- Removed Ollama (kept timing out, cascading failures)
- Lowered concurrency (2 main / 4 subagent)
- Fixed "origin not allowed" error: added `controlUi.allowedOrigins` + `trustedProxies` config
- Skills audit: 92/98 ready, fixed missing frontmatter on debug-pro + trend-watcher

## Commits
- `56d0dca` fix: learning loop page — invisible text + promotion timeline redesign
- `c023b9c` fix: add /api/learning/history endpoint — promotion timeline was empty
- `610438c` fix: add missing learning endpoints + fix coverage gap false negatives
- `6b7df22` feat: settings page — healing tier, SMTP, branding, evidence, learning thresholds
- `3bb3e6a` fix: routes.py learning history queries learning_promotion_candidates

## Key Insight
Frontend API routing: `API_BASE = '/api/dashboard'` means the frontend hits `routes.py` (mounted at `/api/dashboard`), NOT `main.py` endpoints (mounted at `/api`). Adding endpoints to main.py alone is invisible to the dashboard UI.

## Pending
- VM appliance stale — iMac host needs physical wake
- OpenClaw origin fix deployed, user should hard-refresh browser (Cmd+Shift+R)
- clawhub login needed for installing additional skills from hub (rate-limited without auth)

# Session 110: Production Audit + Critical Fixes
**Date:** 2026-02-15 | **Duration:** ~2 hours

## Key Changes Made

### 1. Evidence Signature Fix (HIGH - DEPLOYED)
- **Root cause**: Double PHI scrub in `appliance_client.py` — phone regex matched NTP float values (e.g. `16.2353515625`) inside `signed_data` JSON string, corrupting Ed25519 signatures
- **Fix**: Added `pre_scrubbed=True` parameter to `_request()` so `submit_evidence()` skips redundant second scrub
- **File**: `packages/compliance-agent/src/compliance_agent/appliance_client.py`
- **Commit**: `9a7a78f`

### 2. CVE Sync Fix (DEPLOYED)
- `isinstance(affected_cpes, str)` guard was in local code but not deployed to VPS
- Deployed via same push — fixes `'str' object has no attribute 'get'` error in CVE sync loop
- **File**: `mcp-server/central-command/backend/cve_watch.py` line 608-609

### 3. Disk Space Cleanup (86% → 66%)
- **Freed 29GB** on VPS (21GB → 50GB free)
- Capped journald at 1G (was 3.9G unbounded)
- Removed stale `/root` files (gallery-dl 1.6G, puppeteer 609M, old repos)
- Pruned Docker images (python:3.11, old builds ~2.5G)
- Unmounted stale ISO loopback in `/tmp` (1.1G)
- Removed old appliance images/ISOs keeping only latest versions (~5G)
- Added `/etc/docker/daemon.json` for log rotation

### 4. L1 Healing Rule Fixes (DEPLOYED)
- **L1-FW-002**: New rule for `check_type="firewall_status"` (agent sends this, L1-FW-001 only matched `"firewall"`)
- **L1-AUDIT-002**: New rule for `check_type="audit_policy"` (no built-in rule existed)
- **L1-WIN-SMB-001**: Fixed wrong runbook `RB-WIN-SEC-006` → `RB-WIN-SEC-007` in `l1_rules_full_coverage.json`
- **File**: `packages/compliance-agent/src/compliance_agent/level1_deterministic.py`, `config/l1_rules_full_coverage.json`
- **Commit**: `5dc0dd5`

### 5. Brand Toolkit (DEPLOYED)
- Created `mcp-server/central-command/frontend/public/brand-toolkit.html`
- Updated personal banner text to right side (clear of LinkedIn profile pic)
- 6 downloadable LinkedIn marketing graphics via Canvas API

## Full System Audit Results

### PASS
- All 7 Docker containers healthy (46 days uptime)
- API health: Redis, Postgres, MinIO all connected
- OTS blockchain: 103K proofs, 98.5K anchored, 0 bad block heights
- Hash chain integrity: 204K compliance bundles, 0 gaps, 0 broken links
- WORM protection: append-only triggers working
- HIPAA mapping: 51/51 runbooks mapped, 16 controls covered
- SSL/HTTPS: HTTP/2, security headers, cert valid 90 days
- System resources: 66% disk, 6.3GB RAM available, CPU load 0.21

### WARN
- L1 execution success rate: 54.9% (runbook ID mismatches cause failures)
- Evidence bundles table: only 2 rows (compliance_bundles has 204K — different code path)
- Remediation orders: 421 pending never acknowledged, 1,119 expired, 0 completed
- CVE sync: loop failing on NVD response parsing (needs type guard on configs)
- patterns.pattern_signature: VARCHAR(64) truncation blocking new pattern inserts
- Duplicate CSP headers from both Caddy and backend middleware
- 11% error rate in logs (mostly auth/CSRF/rate-limit — expected)

### FAIL
- L1 rule counters: all 19 rules show 0 matches (feedback loop broken)
- Flywheel promotion: 21 eligible patterns, 0 deployed rules (pipeline stalled)
- Background task registry: no persistent health tracking table
- WebSocket path not routed through Caddy for www/dashboard domains

## Commits
1. `9a7a78f` — Evidence signature fix + CVE sync deploy + brand toolkit
2. `5dc0dd5` — L1 rule fixes (firewall_status, audit_policy, SMB runbook ID)

## Next Session Priorities
1. Fix patterns.pattern_signature VARCHAR(64) → VARCHAR(255)
2. Fix L1 rule counter feedback loop (telemetry → l1_rules)
3. Fix remediation order delivery pipeline (0 completions ever)
4. Fix runbook ID mismatches (agent internal IDs vs server IDs)
5. Run full spectrum chaos lab test and validate improved healing rate
6. Add WebSocket routing to Caddyfile
7. Remove duplicate CSP header (pick Caddy OR backend)

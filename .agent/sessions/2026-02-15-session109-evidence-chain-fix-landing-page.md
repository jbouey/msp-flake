# Session 109 - Evidence Chain Race Fix + Landing Page + Client Portal

**Date:** 2026-02-15
**Agent Version:** 1.0.70

## Completed

### Client Portal Healing Logs (commits 37ebf33, 3ee12e9)
- 3 new backend endpoints: `GET /client/healing-logs`, `GET /client/promotion-candidates`, `POST /client/promotion-candidates/{id}/forward`
- New `ClientHealingLogs.tsx` with two tabs (Healing Logs + Promotion Candidates)
- Migration 042: added client endorsement columns to `learning_promotion_candidates`
- Fixed bug: `main.py` was not importing client_portal routers (only `server.py` was)

### OsirisCare Landing Page (commits 3ee12e9, 2fcdc87)
- Created `LandingPage.tsx` with medical-grade aesthetic (teal/slate, DM Sans + Source Serif)
- Hostname-based routing: www.osiriscare.net serves landing page, dashboard.osiriscare.net serves admin
- Caddy config added for www.osiriscare.net + bare domain redirect
- DNS already configured (CNAME www -> osiriscare.net, A @ -> 178.156.162.116)

### Evidence Chain Race Condition Fix (commit 3a68713)
- **Root cause:** Concurrent evidence submissions both read same MAX(chain_position) without locking
- **Impact:** 1,137 broken hash chain links (0.55% of 203,076 bundles), started Feb 11
- **Code fix:** `pg_advisory_xact_lock(hashtext(site_id))` serializes per-site submissions
- **Also:** Changed ORDER BY from `checked_at` to `chain_position`, uses `"0"*64` sentinel for genesis prev_hash
- **Data repair (migration 043):**
  - Re-sequenced 10,825 chain positions
  - Fixed 1,435 prev_hash references
  - Recomputed 203,075 chain hashes
  - Added unique index on (site_id, chain_position)
- **Result:** 1,137 broken links -> 0 broken links

### Chaos Tests
- Run 1 (earlier session): 10/21 healed in 300s (48%)
- 3x back-to-back test launched (still running when session ended)
- Run 1 of 3x: 7/16 verified checks healed in 300s
  - Windows: 5/8 (WS-FW, SRV-FW, NetProfile, Task, Registry)
  - Linux: 2/8 (Firewall, rsyslog)
  - Persistent gaps: DC-Firewall, DC-DNS, DC-SMB, SSH configs, audit, kernel params, cron perms, SUID

### macOS Runbook Plan
- 17 HIPAA compliance checks proposed
- 13/17 L1 auto-healable via SSH + `defaults`/`launchctl`/`fdesetup`
- Key checks: FileVault, Firewall, Screen Lock, Auto-login, Gatekeeper, SIP, NTP, etc.
- Plan complete, implementation pending

## Key Findings

### Evidence Chain Architecture
- `compliance_bundles` table stores hash chain per site
- `prev_hash` is NOT NULL — genesis bundles use 64-zero sentinel
- `chain_hash = SHA256(bundle_hash:prev_hash:chain_position)`
- `verify_chain_integrity` endpoint at `/sites/{site_id}/verify-chain` already uses correct GENESIS_HASH logic
- WORM triggers prevent DELETE and content modification (working)

### Chaos Test Patterns
- DC healing consistently fails (firewall, DNS, SMB) — likely GPO-related or WinRM timeout
- Linux SSH config changes not being healed (no L1 rule for SSH-001/002 remediation?)
- Flap suppression needs clearing between runs for clean results

## Commits
- `3a68713` fix: Evidence chain race condition — advisory lock + repair 1,137 broken links
- `2fcdc87` feat: Serve landing page at www.osiriscare.net with hostname detection
- `3ee12e9` feat: Add OsirisCare marketing landing page at /welcome
- `37ebf33` feat: Client portal healing logs + promotion approval flow

## Next Priorities
1. **DC healing gap** — investigate why DC firewall/DNS/SMB never heal (GPO override? WinRM timeout?)
2. **Linux SSH/audit/kernel healing** — verify L1 rules exist and are being matched
3. **macOS runbook implementation** — 17 checks, SSH-based, follows Linux patterns
4. **3x chaos test results** — collect and analyze when complete
5. **Evidence chain monitoring** — verify zero new broken links after fix deployed

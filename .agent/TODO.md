# Current Tasks & Priorities

**Last Updated:** 2025-12-04
**Sprint:** Phase 2 Complete

---

## 🔴 Critical (This Week)

### 1. Evidence Bundle Signing (Ed25519)
**Status:** ✅ COMPLETE (2025-12-03)
**Why Critical:** HIPAA §164.312(b) requires tamper-evident audit controls
**Files:** `evidence.py`, `crypto.py`, `agent.py`
**Acceptance:**
- [x] Ed25519 key pair generation on first run (`ensure_signing_key()`)
- [x] Sign bundles immediately after creation (in `store_evidence()`)
- [x] Signature stored in bundle + separate .sig file
- [x] Verification function for audit (`verify_evidence()`)

### 2. Auto-Remediation Approval Policy
**Status:** ✅ COMPLETE (2025-12-03)
**Why Critical:** Disruptive actions (patching, BitLocker) need governance
**Files:** `approval.py`, `healing.py`, `web_ui.py`
**Acceptance:**
- [x] Document which actions need approval (`ACTION_POLICIES` in approval.py)
- [x] Add approval queue to web UI (`/approvals`, `/api/approvals/*`)
- [x] Block disruptive actions until approved (integrated in healing.py)
- [x] Audit trail of approvals (SQLite with `approval_audit_log` table)

### 3. Fix datetime.utcnow() Deprecation
**Status:** ✅ COMPLETE (2025-12-03)
**Why Critical:** Python 3.12+ deprecation, causes log noise
**Files:** Fixed in `drift.py`, `src/agent.py`
**Acceptance:**
- [x] Replace all `datetime.utcnow()` with `datetime.now(timezone.utc)`
- [x] Zero deprecation warnings in test run
- [x] All 169 tests passing

---

## 🟡 High Priority (Next 2 Weeks)

### 4. Windows VM Setup & WinRM Configuration
**Status:** ✅ COMPLETE (2025-12-04)
**Why:** Windows VM needed for integration testing
**Files:** `~/win-test-vm/Vagrantfile` (on 2014 iMac)
**Acceptance:**
- [x] Recreated Windows VM with proper WinRM port forwarding (port 55987)
- [x] WinRM connectivity verified via SSH tunnel
- [x] Windows integration tests passing (3/3)
- [x] Auto healer integration tests passing with USE_REAL_VMS=1

### 5. Web UI Federal Register Integration Fix
**Status:** ✅ COMPLETE (2025-12-03)
**Why:** Regulatory monitoring not showing in dashboard
**Files:** `web_ui.py`
**Acceptance:**
- [x] Fix indentation/syntax error (integration was missing, now added)
- [x] `/api/regulatory` returns HIPAA updates
- [x] Dashboard shows regulatory alerts (via `/api/regulatory/updates`, `/api/regulatory/comments`)

### 6. Test BitLocker Runbook
**Status:** ✅ COMPLETE (2025-12-04)
**Files:** `runbooks/windows/runbooks.py` (RB-WIN-ENCRYPTION-001)
**Acceptance:**
- [x] Detection phase tested - AllEncrypted=True, Drifted=False
- [x] Verified via WinRM SSH tunnel (127.0.0.1:55985)
- [x] Windows integration tests passing (3/3)

### 7. Test PHI Scrubbing with Windows Logs
**Status:** ✅ COMPLETE (2025-12-04)
**Files:** `phi_scrubber.py`, `tests/test_phi_windows.py` (17 tests)
**Acceptance:**
- [x] Fetched real Windows Security Event logs via WinRM
- [x] Verified all PHI patterns redacted (SSN, MRN, email, IP, phone, CC, DOB, address, Medicare)
- [x] Created comprehensive test suite for Windows log formats
- [x] All 17 Windows PHI tests passing

---

## 🟢 Medium Priority (This Month)

### 8. Implement Action Parameters Extraction
**Status:** ✅ COMPLETE (2025-12-03)
**Files:** `learning_loop.py:194-297`, `tests/test_learning_loop.py`
**Why:** Data flywheel can't promote L2 patterns without params
**Acceptance:**
- [x] Extract parameters from successful L2 resolutions (already implemented with action-specific keys, majority voting, list handling)
- [x] Store in incident_db for pattern matching (integrated with PromotionCandidate)
- [x] Unit tests for extraction (33 tests added covering all methods)

### 9. Implement Rollback Tracking
**Status:** ✅ COMPLETE (2025-12-03)
**Files:** `learning_loop.py:534-739`, `web_ui.py:526-543, 1330-1457`
**Why:** Can't measure remediation stability without rollback data
**Acceptance:**
- [x] Track if remediation was rolled back (`monitor_promoted_rules()`, `_rollback_rule()`, `get_rollback_history()`)
- [x] Factor into pattern promotion decisions (`rollback_on_failure_rate` config, auto-rollback when >20% failure)
- [x] Dashboard shows rollback rate (Web UI: `/api/rollback/stats`, `/api/rollback/history`, `/api/rollback/monitoring`)
- [x] Fixed `outcome` column bug in post-promotion stats query
- [x] Added 7 rollback tests to test_learning_loop.py, 7 tests to test_web_ui.py

### 10. Web UI Evidence Listing Performance
**Status:** ✅ COMPLETE (2025-12-03)
**Files:** `web_ui.py:807-914`
**Why:** Recursive glob on every request
**Acceptance:**
- [x] Cache evidence file list (`_get_evidence_cache()` with 60-second TTL)
- [x] Invalidate on new bundle (`invalidate_evidence_cache()` method)
- [x] Pagination for large lists (already existed, now uses cached data)
- [x] Fixed ZeroDivisionError on invalid per_page parameter
- [x] Added 5 cache tests to test_web_ui.py

### 11. Fix incident_type vs check_type Column
**Status:** ✅ COMPLETE (2025-12-03)
**Files:** `web_ui.py:875`
**Why:** Causes SQL errors on incident queries
**Acceptance:**
- [x] Change query to use `check_type`
- [x] Verify incidents display in web UI (query fixed)

---

## 🔵 Low Priority (Backlog)

### 12. L2 LLM Guardrails Enhancement
**Status:** ✅ COMPLETE (2025-12-04)
**Files:** `level2_llm.py`, `tests/test_level2_guardrails.py` (42 tests)
**Acceptance:**
- [x] Full blocklist implemented (70+ dangerous patterns)
- [x] Regex patterns for complex commands (rm variants, wget|bash, etc.)
- [x] Action parameter validation (recursive checking)
- [x] All 42 guardrail tests passing
- [x] Note: Crypto mining patterns removed due to AV false positives (strings trigger AV even in blocklist)

### 13. Unskip Test Cases
**Status:** ✅ MOSTLY COMPLETE (2025-12-04)
**Files:** `test_drift.py`, `test_auto_healer_integration.py`
**Why:** 7 tests were skipped due to Windows VM dependency
**Acceptance:**
- [x] Windows VM connectivity restored (port 55987)
- [x] 6 of 7 skipped tests now passing with USE_REAL_VMS=1
- [x] Only 1 test still skipped: NixOS VM connectivity (no NixOS VM configured)
- [x] Test count: 429 passed, 1 skipped (was 423 passed, 7 skipped)

### 14. Async Pattern Improvements
**Status:** ✅ PARTIAL (Drift checks already parallel)
- [x] Drift checks use `asyncio.gather()` for parallel execution (drift.py:92-99)
- [ ] Evidence upload batch processing (future enhancement)

### 15. Backup Restore Testing Runbook
**Status:** ✅ COMPLETE (2025-12-04)
**Files:** `backup_restore_test.py`, `tests/test_backup_restore.py` (27 tests)
**HIPAA:** §164.308(a)(7)(ii)(A)
**Acceptance:**
- [x] Weekly automated restore test (`BackupRestoreTester.run_restore_test()`)
- [x] Verify checksums (`_verify_restored_files()` with SHA256)
- [x] Evidence of successful restore (`RestoreTestResult` with action trail)
- [x] Support for restic and borg backup types
- [x] Status tracking with history (`backup-status.json`)
- [x] Integration with healing engine (`run_restore_test` action)

---

## ✅ Recently Completed

- [x] Three-tier auto-healing (L1/L2/L3)
- [x] Data flywheel (L2→L1 promotion)
- [x] PHI scrubber module
- [x] BitLocker recovery key backup enhancement
- [x] Federal Register HIPAA monitoring
- [x] Windows compliance collection (7 runbooks)
- [x] Web UI dashboard
- [x] Evidence bundle signing (Ed25519)
- [x] Auto-remediation approval policy
- [x] Federal Register regulatory integration
- [x] L2 LLM Guardrails (70+ patterns, 42 tests) - 2025-12-04
- [x] BitLocker runbook tested on Windows VM - 2025-12-04
- [x] PHI scrubbing with Windows logs (17 tests) - 2025-12-04
- [x] 396 passing tests, 4 skipped (was 300)
- [x] Backup Restore Testing Runbook (27 tests) - 2025-12-04
- [x] Fix Starlette TemplateResponse deprecation - 2025-12-04
- [x] Windows VM recreated with WinRM port 55987 - 2025-12-04
- [x] 6 of 7 skipped tests now passing - 2025-12-04
- [x] 429 passed, 1 skipped (with USE_REAL_VMS=1)

---

## Quick Reference

**Run tests:**
```bash
cd /Users/dad/Documents/Msp_Flakes/packages/compliance-agent
source venv/bin/activate
python -m pytest tests/ -v --tb=short
```

**Check for deprecation warnings:**
```bash
python -m pytest tests/ 2>&1 | grep -c "DeprecationWarning"
```

**SSH to appliance:**
```bash
ssh -p 4444 root@174.178.63.139
```

**Web UI tunnel:**
```bash
ssh -f -N -L 9080:192.168.56.103:8080 jrelly@174.178.63.139
open http://localhost:9080
```

**Windows VM WinRM tunnel:**
```bash
ssh -f -N -L 55987:127.0.0.1:55987 jrelly@174.178.63.139
# Then run tests with:
WIN_TEST_HOST="127.0.0.1:55987" WIN_TEST_USER="vagrant" WIN_TEST_PASS="vagrant" USE_REAL_VMS=1 python -m pytest tests/ -v
```

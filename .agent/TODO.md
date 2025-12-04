# Current Tasks & Priorities

**Last Updated:** 2025-12-03
**Sprint:** Phase 2 Completion / Phase 3 Planning

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

### 4. Windows Firewall Fix for VM-to-VM
**Status:** Documented, not applied  
**Blocked By:** Windows VM offline  
**Files:** N/A (Windows config)  
**Action:**
```powershell
New-NetFirewallRule -Name "WinRM_HostOnly" `
  -DisplayName "WinRM from Host-Only Network" `
  -Enabled True -Direction Inbound -Protocol TCP `
  -LocalPort 5985 -RemoteAddress 192.168.56.0/24 -Action Allow
```

### 5. Web UI Federal Register Integration Fix
**Status:** ✅ COMPLETE (2025-12-03)
**Why:** Regulatory monitoring not showing in dashboard
**Files:** `web_ui.py`
**Acceptance:**
- [x] Fix indentation/syntax error (integration was missing, now added)
- [x] `/api/regulatory` returns HIPAA updates
- [x] Dashboard shows regulatory alerts (via `/api/regulatory/updates`, `/api/regulatory/comments`)

### 6. Test BitLocker Runbook
**Status:** Enhanced runbook created, untested  
**Blocked By:** Windows VM offline  
**Files:** `runbooks/windows/RB-WIN-ENCRYPTION-001-enhanced.yaml`  
**Acceptance:**
- [ ] Enable BitLocker on test VM
- [ ] Verify recovery key backed up to AD
- [ ] Verify recovery key backed up locally
- [ ] Evidence bundle generated

### 7. Test PHI Scrubbing
**Status:** Module created, needs live test  
**Blocked By:** Windows VM offline  
**Files:** `phi_scrubber.py`, `windows_collector.py`  
**Acceptance:**
- [ ] Collect logs with fake PHI patterns
- [ ] Verify all patterns redacted
- [ ] Evidence bundle shows `phi_scrubbed: true`

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
Add stricter blocklist for generated code:
- `rm -rf /`
- `mkfs`
- `dd if=/dev/zero`
- `chmod -R 777`

### 13. Unskip Test Cases
**Files:** `test_drift.py`, `test_auto_healer_integration.py`  
**Why:** 7 tests currently skipped  
**Reason:** Complex mocking for AV/EDR, Windows VM dependency

### 14. Async Pattern Improvements
Use `asyncio.gather()` for parallel operations in:
- Drift checks (run all 6 in parallel)
- Evidence upload (batch upload)

### 15. Backup Restore Testing Runbook
**Files:** New runbook needed  
**HIPAA:** §164.308(a)(7)(ii)(A)  
**Acceptance:**
- [ ] Weekly automated restore test
- [ ] Verify checksums
- [ ] Evidence of successful restore

---

## 📋 Phase 3 Planning

### Cognitive Function Split (ADR-005)
Refactor monolithic agent into five agents:
- Scout (discovery)
- Sentinel (detection)
- Healer (remediation)
- Scribe (documentation)
- Oracle (analysis)

### Local LLM Deployment
- Evaluate Llama 3 8B for L2 fallback
- Benchmark token/second on NixOS VM
- Cost analysis vs API

### Multi-Tenant Support
- Reseller mode partitioning
- Per-tenant isolation
- Billing integration

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
- [x] 300 passing tests (fixed 8 test failures in test_web_ui.py)

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

# Current Tasks & Priorities

**Last Updated:** 2025-12-03
**Sprint:** Phase 2 Completion / Phase 3 Planning

---

## 🔴 Critical (This Week)

### 1. Evidence Bundle Signing (Ed25519)
**Status:** Not started  
**Why Critical:** HIPAA §164.312(b) requires tamper-evident audit controls  
**Files:** `evidence.py`, `crypto.py`  
**Acceptance:**
- [ ] Ed25519 key pair generation on first run
- [ ] Sign bundles immediately after creation
- [ ] Signature stored in bundle + separate .sig file
- [ ] Verification function for audit

### 2. Auto-Remediation Approval Policy
**Status:** Not started  
**Why Critical:** Disruptive actions (patching, BitLocker) need governance  
**Files:** `healing.py`, `web_ui.py`  
**Acceptance:**
- [ ] Document which actions need approval
- [ ] Add approval queue to web UI
- [ ] Block disruptive actions until approved
- [ ] Audit trail of approvals

### 3. Fix datetime.utcnow() Deprecation
**Status:** 907 warnings in logs  
**Why Critical:** Python 3.12+ deprecation, causes log noise  
**Files:** `mcp_client.py`, `offline_queue.py`, `utils.py`, `incident_db.py`, `evidence.py`  
**Acceptance:**
- [ ] Replace all `datetime.utcnow()` with `datetime.now(timezone.utc)`
- [ ] Zero deprecation warnings in test run

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
**Status:** Indentation error in web_ui.py  
**Why:** Regulatory monitoring not showing in dashboard  
**Files:** `web_ui.py` (lines ~850-900)  
**Acceptance:**
- [ ] Fix indentation/syntax error
- [ ] `/api/regulatory` returns HIPAA updates
- [ ] Dashboard shows regulatory alerts

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
**Status:** Returns empty dict  
**Files:** `learning_loop.py:194-202`  
**Why:** Data flywheel can't promote L2 patterns without params  
**Acceptance:**
- [ ] Extract parameters from successful L2 resolutions
- [ ] Store in incident_db for pattern matching
- [ ] Unit tests for extraction

### 9. Implement Rollback Tracking
**Status:** Config exists, no code  
**Files:** `learning_loop.py:54`  
**Why:** Can't measure remediation stability without rollback data  
**Acceptance:**
- [ ] Track if remediation was rolled back
- [ ] Factor into pattern promotion decisions
- [ ] Dashboard shows rollback rate

### 10. Web UI Evidence Listing Performance
**Status:** Slow with many files  
**Files:** `web_ui.py:698-746`  
**Why:** Recursive glob on every request  
**Acceptance:**
- [ ] Cache evidence file list
- [ ] Invalidate on new bundle
- [ ] Pagination for large lists

### 11. Fix incident_type vs check_type Column
**Status:** Query uses wrong column name  
**Files:** `web_ui.py:811-812`  
**Why:** Causes SQL errors on incident queries  
**Acceptance:**
- [ ] Change query to use `check_type`
- [ ] Verify incidents display in web UI

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
- [x] 161 passing tests

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

# Gate B verdict — zero-auth hardening Commit 2 (2026-05-11)

**Verdict:** BLOCK

## Gate A directive compliance
- Heartbeat B2 (provision_code on HeartbeatRequest, 401 missing, 403 invalid): ✓
- 5 post-claim endpoints get `require_appliance_bearer + _enforce_site_id` (or site-scoped UPDATE): ✓ (provisioning.py:434 status, discovery.py:194 report, sensors.py:272/295/548)
- Scope restraint — 7 newly-found zero-auth endpoints deferred to TaskCreate #113 (NOT silently allowlisted): ✓ (see `_COMMIT_2_HARDENED_HANDLERS` in test_appliance_endpoints_auth_pinned.py:119-126)

## Pre-push sweep result (MANDATORY)
- Commit-2 + sibling pinned tests: **25 passed** (test_appliance_endpoints_auth_pinned: 3, test_appliance_delegation_auth_pinned: 4, test_site_id_enforcement: 18)
- Full CI-parity sweep: **229 passed, 0 skipped, 1 FAILED**
- FAIL: `tests/test_no_unfiltered_site_appliances_select.py::test_unfiltered_site_appliances_select_ratchet` — **BASELINE_MAX=83, found 84**. This is the EXACT class Commit 1 missed (`test_site_id_enforcement` regression). Without the sweep this ships → CI red → outage class.

## Adversarial findings

### P0 (BLOCK)
1. **Soft-delete ratchet regression** (Carol/Coach). Commit 2 adds 4+ new `FROM site_appliances`/`UPDATE site_appliances` statements without `AND deleted_at IS NULL`:
   - `provisioning.py:461` UPDATE in `update_provision_status` (status update)
   - `provisioning.py:475` SELECT for cross-site lookup
   - `provisioning.py:492` SELECT in onboarding-stage update subquery
   - `provisioning.py:552` SELECT by mac_address in heartbeat
   Fix: add `AND deleted_at IS NULL` to each WHERE clause (a soft-deleted appliance must NOT be auth-resurrectable via heartbeat/status). Re-run sweep until ratchet passes; bump baseline in `test_no_unfiltered_site_appliances_select.py` ONLY for justified noqa-tagged cases (e.g. the `provisioning.py:475` cross-tenant lookup may legitimately need soft-deleted rows for the forensic-403 path — tag with `# noqa: site-appliances-deleted-include — cross-site forensic lookup`).

2. **`'active'` status value is unreachable** (Steve). `provisioning.py:536` filters `status IN ('pending','claimed','active')` but the `appliance_provisions` CHECK constraint (`migrations/003_partner_infrastructure.sql:73`) is `('pending','claimed','expired','revoked')` — **no `'active'`**. The third tuple value is dead code. Either:
   - Remove `'active'` from the IN list (cosmetic, matches schema), OR
   - Confirm a later mig added `'active'` (none found in `migrations/`) and document the source.
   Currently harmless but misleading; future reader will assume `'active'` is a real state.

### P1
3. **Backward-compat break for in-flight daemons** (Maya). `provision_code: str` is REQUIRED on `HeartbeatRequest`. Any daemon running pre-fix binary sending JSON without `provision_code` gets 422 Unprocessable Entity (not 401 — Pydantic validation rejects before handler runs). Fleet currently has v0.4.13 + v0.3.82 daemons; verify neither pre-claim path is in production. If pre-claim daemons exist in the field, B2 needs `Optional[str] = None` + handler-level `if not code: 401` so the error stays in our control surface and operator alerts can pattern-match the 401 string. Recommend: 24-72h flight time after coordinated fleet push.

### P2
4. **Permanent allowlist hole risk** (Coach). `_COMMIT_2_HARDENED_HANDLERS` is a closed set; if a future engineer renames `update_scan_status` (one of the 7 deferred handlers) the gate stays silent. Mitigation already partially present: the test asserts every handler in the set IS found (catches rename of the 6 we DO enforce). The 7 deferred handlers have no parallel "must remain in TaskCreate #113 OR be added here" check. Acceptable for sprint scope; add to TaskCreate #113 as exit criteria.

5. **`provision_code.upper().strip()` case-folding** (Carol). `provision_code` column is `VARCHAR(32) UNIQUE`; case sensitivity is implicit (PG default = case-sensitive). The `/claim` and `/validate` endpoints also `.upper().strip()` (provisioning.py:181, 395), so heartbeat is consistent with siblings — codes are de-facto uppercase by convention. Acceptable. NOTE: if any historical row has lowercase letters, this rejects valid codes. Spot-check: `SELECT COUNT(*) FROM appliance_provisions WHERE provision_code != upper(provision_code)` before merge.

## Per-lens analysis

- **Steve**: Schema audit caught dead `'active'` enum value (P0 #2). JOIN on `sensors.py /commands/complete` not directly inspected this gate — defer to sibling test coverage; recommend a follow-up Steve pass on the JOIN-key correctness if not already covered.
- **Maya**: BC-break risk on required `provision_code` (P1 #3). Operator UX: 422 from Pydantic is opaque vs 401 from handler.
- **Carol**: Soft-delete bypass (P0 #1) is the actual security regression. A soft-deleted appliance row at `site_appliances` should NOT be revivable by a heartbeat with the right MAC + valid provision code.
- **Coach**: Full sweep FAIL = automatic BLOCK per `feedback_consistency_coach_pre_completion_gate.md` 2026-05-11 lock-in. Sibling test (`test_appliance_delegation_auth_pinned.py`) shape mirrored correctly — AST walk + allowlist + 3-test triplet.

## Recommendation

**BLOCK.** Two changes required before merge:
1. Add `AND deleted_at IS NULL` to the 4 new `site_appliances` WHERE clauses in provisioning.py (lines 461, 475, 492, 552). For line 475's cross-site forensic lookup, judge if soft-deleted rows are intentional — if so, inline `# noqa: site-appliances-deleted-include — <reason>`. Re-run `bash .githooks/full-test-sweep.sh` until 230 passed / 0 failed.
2. Remove `'active'` from the `status IN (...)` tuple in `provisioning.py:536` (or document the source mig that added it).

P1 #3 (BC-break) is judgment: if fleet manifest confirms zero pre-fix daemons in pre-claim state, ship as-is; otherwise switch to `Optional[str] = None` + 401 in handler.

After both P0s closed and sweep green: Gate B re-runs once → APPROVE expected.

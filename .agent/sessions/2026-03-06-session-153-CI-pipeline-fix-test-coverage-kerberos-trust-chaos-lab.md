# Session 153 — CI Pipeline Fix, Test Coverage, Kerberos Trust, Chaos Lab

**Date:** 2026-03-06
**Started:** 09:22
**Previous Session:** 152

---

## Goals

- [x] Fix CI/CD pipeline — make pytest blocking, add vitest
- [x] Fix all test mock failures (3 test files, 36 failures → 0)
- [x] WinRM credential validation — full stack implementation
- [x] ws01 Kerberos domain trust — rejoin domain
- [x] Chaos lab hardening — prevent future time drift breaking Kerberos
- [x] Research email notifications + evidence bundle export status

---

## Progress

### Completed

1. **CI/CD pipeline green** (4 commits):
   - Removed `|| true` from pytest, added vitest step
   - Created `requirements.txt` for CI backend deps
   - Fixed test mocks: `dependency_overrides[get_pool]` → `unittest.mock.patch()` (get_pool called directly, not via Depends)
   - Fixed `promote_pattern` import (`dashboard_api._routes_impl`, not `dashboard_api.routes` package)
   - Fixed patch paths: `websocket_manager.broadcast_event`, `order_signing.sign_admin_order`
   - Fixed FakeConn key ordering, auth gating test approach

2. **WinRM credential validation** (full stack):
   - Backend: fleet order queuing in `partners.py`
   - Go daemon: `handleValidateCredential` with WinRM probe + AD read
   - Completion hook: updates `site_credentials.validation_status`

3. **ws01 Kerberos trust fixed**:
   - DC time corrected (was stuck at Jan 13 snapshot date)
   - Deleted stale NVWS01 AD object, rejoined ws01 to domain

4. **Chaos lab hardened**:
   - ForceTimeSync scheduled task on DC (boot-time `w32tm /resync /force`)
   - Fresh snapshot: `post-kerberos-fix-2026-03-06`
   - W32Time/NTP added to CRITICAL EXCLUSION in `generate_and_plan_v2.py`
   - All 5 lab VMs started

5. **Research**:
   - Email: Fully configured, all 7 flows wired, SMTP working
   - Evidence: Org ZIP works but queries wrong table (`evidence_bundles` vs `compliance_bundles`)

### Blocked

None.

---

## Files Changed

| File | Change |
|------|--------|
| `.github/workflows/deploy-central-command.yml` | pytest blocking + vitest step |
| `mcp-server/central-command/backend/requirements.txt` | Created for CI |
| `mcp-server/central-command/backend/tests/test_companion.py` | Mock fixes (patch get_pool) |
| `mcp-server/central-command/backend/tests/test_flywheel_promotion.py` | Import + patch path fixes |
| `mcp-server/central-command/backend/tests/test_partner_auth.py` | Pool patch + key ordering fixes |
| `mcp-server/central-command/backend/partners.py` | WinRM validation fleet order |
| `mcp-server/central-command/backend/sites.py` | validate_credential completion hook |
| `appliance/internal/orders/processor.go` | validate_credential handler (19th) |
| `appliance/internal/orders/processor_test.go` | Handler count 18→19 |
| `appliance/internal/daemon/daemon.go` | handleValidateCredential |
| iMac: `chaos-lab/scripts/generate_and_plan_v2.py` | W32Time exclusion |

---

## Key Learnings

- `app.dependency_overrides[fn]` only works for `Depends(fn)` — direct calls need `patch()`
- `dashboard_api.routes` is a package, not `routes.py` — use `_routes_impl`
- DC snapshot restore resets clock — Kerberos 5-min tolerance breaks trust silently
- FakeConn substring key matching is order-dependent — specific keys first

---

## Additional Work (same session)

5. **Evidence org ZIP fix** — changed query from `evidence_bundles` to `compliance_bundles` with correct column names (`bundle_id`, `check_result`, `checked_at`, `bundle_hash`). Committed, CI green, deployed.

6. **Chaos lab snapshot config** — updated all 3 `VM_SNAPSHOT` references in `config.env` from `pre-chaos-clean` to `post-kerberos-fix-2026-03-06`.

7. **Email verification in production** — both email types tested and delivered:
   - Companion alert email: `send_companion_alert_email()` → True
   - Critical incident alert: `send_critical_alert()` → True
   - SMTP config: `mail.privateemail.com`, user `jbouey@osiriscare.net`

8. **Frontend UX audit** — full 22-point audit of all pages. Result: **production-ready**, no blockers. Only finding: Reports page shows "Coming soon" placeholder (intentional, not in main nav).

9. **All 5 lab VMs started** (northvalley-linux and northvalley-srv01 were stopped).

### Additional Files Changed

| File | Change |
|------|--------|
| `mcp-server/central-command/backend/evidence_chain.py` | Fix org ZIP to query compliance_bundles |
| iMac: `chaos-lab/config.env` | Update snapshot refs to post-kerberos-fix-2026-03-06 |

---

## Next Session

1. End-to-end customer onboarding flow test (create site → deploy → check-in → incidents → evidence download)
2. Test companion portal OAuth with real Microsoft/Google account
3. Verify magic link emails work for client portal login
4. Confirm test alert emails aren't landing in spam
5. Consider hiding Reports nav link before demo

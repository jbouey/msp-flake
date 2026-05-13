# Gate B Re-Fork — Task #53 Phase 0 v2 (BLOCK fix)

**Date:** 2026-05-13
**Scope:** Re-verify Phase 0 implementation after removing `_EMAIL_ALERTS` from `_OPAQUE_MODULES` allowlist.
**Lenses:** Coach + Engineering (minor-tier 2-lens re-fork)
**Prior verdict:** BLOCK (`audit/coach-opaque-mode-phase0-implementation-gate-b-2026-05-13.md`) — adding `_EMAIL_ALERTS` to allowlist broke 2 tests because module is mixed-recipient (operator subjects at 257/780/1584 + latent leak at 820).

---

## Engineering Lens — verification

### 1. `_EMAIL_ALERTS` removal

Grep result on `tests/test_email_opacity_harmonized.py`:
```
$ grep -n "_EMAIL_ALERTS" tests/test_email_opacity_harmonized.py
(no matches)
```
**VERIFIED.** No stray references — symbolic constant fully removed.

`_OPAQUE_MODULES` membership (lines 83-94) is now:
- `_OWNER_TRANSFER`
- `_EMAIL_RENAME`
- `_CLIENT_PORTAL`
- `_ORG_MGMT`
- `_RELOCATE`
- `_PORTAL`
- `_SITES`
- `_BG_TASKS`
- `_CLIENT_SIGNUP`
- `_ALERT_ROUTER` ← Phase 0 addition (retained)

`_EMAIL_ALERTS` is NOT in the tuple. Module file path constant is also absent.

### 2. Five subject rewrites — confirmed unchanged

| File | Line | Subject literal | Status |
|------|------|-----------------|--------|
| `alert_router.py` | 353 | `"[OsirisCare] Compliance digest"` | opaque |
| `alert_router.py` | 410 | `"[OsirisCare] Compliance monitoring active"` | opaque |
| `alert_router.py` | 537 | `"[OsirisCare] Compliance alert"` | opaque |
| `alert_router.py` | 628 | `"[OsirisCare Partner] Client non-engagement"` | opaque |
| `email_alerts.py` | 951 | `"[OsirisCare] SRA remediation reminder"` | opaque |

All 5 retain the `Task #53 v2 Phase 0` comment with `Rule 7 opaque-mode` rationale.

### 3. Latent line-820 leak — verified still present (Phase 1 scope)

`email_alerts.py:820`:
```python
msg["Subject"] = f"[OsirisCare] Compliance Alert: {module_label} overdue for {org_name}"
```
`send_companion_alert_email` — interpolates `org_name` into subject. This is the leak documented in the deferral comment at test_email_opacity_harmonized.py:74-78. NOT fixed in Phase 0 (deliberately deferred). Phase 1 must:
1. Recipient-split `email_alerts.py` (operator-bucket vs customer-bucket) OR
2. Rewrite line 820 subject to opaque AND classify the module.

### 4. Pre-push sweep result

```
$ python3 -m pytest tests/test_email_opacity_harmonized.py -v
collected 8 items
test_owner_transfer_helpers_have_opaque_signatures      PASSED
test_email_rename_helper_has_opaque_signature           PASSED
test_subjects_are_opaque_across_all_modules             PASSED
test_bodies_are_opaque_across_all_modules               PASSED
test_mime_subject_assignments_opaque                    PASSED
test_call_sites_do_not_pass_forbidden_kwargs            PASSED
test_meta_every_send_email_caller_is_classified         PASSED
test_operator_modules_are_not_in_opaque_scope           PASSED

======================== 8 passed in 2.35s ========================
```
**8/8 PASS.** Including `test_meta_every_send_email_caller_is_classified` (every send_email caller is in either operator or opaque allowlist — no orphans) and `test_operator_modules_are_not_in_opaque_scope` (no overlap between buckets).

The prior BLOCK was driven by `test_subjects_are_opaque_across_all_modules` + `test_bodies_are_opaque_across_all_modules` failing on email_alerts.py operator subjects. Both now pass because email_alerts.py is correctly classified as out-of-scope (operator-class with one customer-facing carve-out at line 947 that uses a plain literal — opaque-by-construction).

---

## Coach Lens — deferral honesty

### Documentation review — lines 60-80

The deferral comment (test_email_opacity_harmonized.py:60-80) explicitly states:
- email_alerts.py is mixed-recipient (operator at 257/780/820/1584 + 1 latent customer-side leak at 820)
- SRA reminder at line 947 IS customer-facing and WAS rewritten (acknowledged in same commit)
- "the module as a whole cannot enter `_OPAQUE_MODULES` without structural recipient-split first"
- "Phase 1 design (own Gate A) addresses email_alerts.py module-level gating + the line-820 latent leak fix"

**Verdict: HONEST.** The comment:
1. Cites the exact line numbers of operator subjects (257/780/820/1584)
2. Names the latent leak (line 820) explicitly — not hand-waved as "TBD"
3. States the structural prerequisite (recipient-split) for Phase 1
4. Commits Phase 1 to its own Gate A (no skipping the two-gate rule)
5. Does NOT claim "deferred to v2" without justification (which would violate the round-table rule against advisory P0s)

The line-820 leak IS a known P1 — it's an active context leak under the same threat model RT21 was designed for (mistyped/forwarded recipient sees client org_name in subject). The deferral is justified because:
- The leak existed before Phase 0 — Phase 0 did NOT regress it.
- Fixing it requires recipient-bucket refactor of `send_companion_alert_email` (companion is customer-side; should not get org_name in subject).
- Doing it in Phase 0 would expand scope beyond the "harmonize 4+1 alert-class subjects" charter.

No advisory language ("will look at later", "noted") — explicit Phase 1 commitment with Gate A requirement. Passes the consistency-coach honesty bar.

### Risk acknowledgment

The Phase 0 commit ships with:
- ✅ 5 customer-facing subjects rewritten opaque
- ✅ alert_router.py fully gated (in `_OPAQUE_MODULES`)
- ⚠️ email_alerts.py:820 latent leak documented + Phase 1-scheduled
- ⚠️ email_alerts.py:947 SRA reminder rewritten but module NOT gated (relies on author discipline until Phase 1)

The ⚠ items are honestly disclosed in the source comment — auditors reading the test file will see them. This is the correct posture: known-debt with named follow-up, not silent regression.

---

## Verdict

**APPROVE — Phase 0 v2 may ship.**

- Engineering: `_EMAIL_ALERTS` cleanly removed, no stray references, 8/8 tests pass, all 5 subject rewrites intact.
- Coach: deferral comment is honest about the line-820 latent leak and commits Phase 1 to its own Gate A.

Ship sign-off: GO.

Phase 1 must open its own Gate A covering:
1. email_alerts.py recipient-split design (operator-bucket vs customer-bucket helpers)
2. Line-820 `send_companion_alert_email` subject rewrite to opaque
3. Add `_EMAIL_ALERTS` (or split-result modules) to `_OPAQUE_MODULES` once structurally safe
4. Backfill ratchet test to prevent future operator-subject regressions into customer-facing helpers

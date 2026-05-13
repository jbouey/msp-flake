# Task #53 Phase 0 implementation — minor-tier Gate B

**Per-lens verdict:**
- Engineering (Steve): **BLOCK**
- Coach: **BLOCK**

**Overall:** **BLOCK** — 2 of 8 opacity tests fail on the modified tree. Phase 0 cannot ship in its current shape.

---

## Engineering findings

### Subject rewrites — APPROVE (5/5 correct in isolation)

All 5 rewritten subjects are plain string literals with no f-string interpolation. Grep + AST inspection confirms:

| File | Line | Subject literal | Pattern |
|------|------|-----------------|---------|
| alert_router.py | 353 | `"[OsirisCare] Compliance digest"` | class-hint |
| alert_router.py | 410 | `"[OsirisCare] Compliance monitoring active"` | class-hint |
| alert_router.py | 537 | `"[OsirisCare] Compliance alert"` | class-hint |
| alert_router.py | 628 | `"[OsirisCare Partner] Client non-engagement"` | class-hint |
| email_alerts.py | 951 | `"[OsirisCare] SRA remediation reminder"` | class-hint, no count |

All match Lens 5/6 PM/medical-tech recommendation (descriptive class hint, no urgency-amplifying "Action required", no org/clinic/site name, no count).

### Test allowlist additions — BLOCK (P0)

**Two test failures on `_OPAQUE_MODULES` += `_EMAIL_ALERTS`:**

1. `test_mime_subject_assignments_opaque` — 4 violations in `email_alerts.py`:
   - Line 257: `msg["Subject"] = subject` — operator alert, subject is a runtime-composed string with severity/event_type/site_id
   - Line 780: `msg["Subject"] = subject` — `send_digest_email` parameter passthrough (subject is opaque at this callsite, but the AST scanner sees a non-literal Name)
   - Line 820: `msg["Subject"] = f"[OsirisCare] Compliance Alert: {module_label} overdue for {org_name}"` — **leaks `{org_name}` directly**; recipient is `companion_user` (clinic-side, NOT operator)
   - Line 1584: `msg["Subject"] = subject` — alertmanager subject from `_am_compose_subject` (operator-only, but again non-literal)

2. `test_operator_modules_are_not_in_opaque_scope` — explicit sentinel hard-codes `email_alerts.py` in `operator_modules` and asserts NO overlap with `_OPAQUE_MODULES`. The Phase 0 commit adds `_EMAIL_ALERTS` to `_OPAQUE_MODULES` → direct sentinel violation.

**Structural problem:** `email_alerts.py` is a **mixed-recipient module** (operator alerts + companion-clinic alerts + digest passthrough + SRA reminder + alertmanager). The "split-recipient-model comment" in the test file (lines 70-75) acknowledges this but does NOT resolve the binary opaque/operator classification the existing 8 tests enforce. You cannot place a mixed module in `_OPAQUE_MODULES` without first either (a) splitting the customer-facing helpers into a new file, or (b) per-line carve-outs in the AST scanners.

### alert_router.py allowlist addition — APPROVE

`_ALERT_ROUTER` cleanly enters `_OPAQUE_MODULES`. The 4 rewritten subjects are all literals; no operator-only subject paths in alert_router.py were caught by the scanners. `test_subjects_are_opaque_across_all_modules` and `test_mime_subject_assignments_opaque` both pass for this module.

### Latent customer-leak in email_alerts.py:820 (Phase 1 scope, but worth flagging now)

`send_companion_alert_email` interpolates `{module_label}` AND `{org_name}` directly into the Subject. Companion is a clinic-side role (`companion_users` table, surfaced through the Companion Portal — see `companion.py:1229`). This is functionally a customer-facing surface that Phase 0 did NOT rewrite. The Gate B fork should have caught it; Gate A's "5 known subject lines" scope was incomplete.

---

## Coach findings

### Sibling parity — APPROVE for alert_router.py, BLOCK for email_alerts.py

The 4 alert_router.py rewrites match the existing `cross_org_site_relocate.py` / `client_owner_transfer.py` / `client_user_email_rename.py` opaque-subject pattern verbatim (plain bracketed prefix + class hint, no template substitution).

The single email_alerts.py rewrite at line 951 is correct *in isolation* but the surrounding module cannot enter `_OPAQUE_MODULES` without violating the operator-allowlist sentinel.

### Spec adherence — PARTIAL

Per the briefing, the v2 design §3 Phase 0 spec is "5 rewrites + 2 module additions". The 5 rewrites are correct. The 2 module additions are **wrong as designed** — adding `_EMAIL_ALERTS` to `_OPAQUE_MODULES` is incompatible with the same file's preexisting `OPERATOR_ALLOWLIST` + `operator_modules` sentinel membership. This is a design-vs-implementation contradiction Gate A should have caught; the Phase 0 spec needed to either:
- (Option A) factor the SRA reminder into a new `customer_email_alerts.py` and pin THAT module to `_OPAQUE_MODULES`, or
- (Option B) extend the AST scanners with a per-line carve-out mechanism (`# noqa: opaque` style) and document the operator-vs-customer carve-out boundary explicitly.

The current implementation chose neither — it adds the mixed module to the opaque list and inherits 4 immediate test failures.

### Regression check — operator paths NOT damaged at runtime

The 4 violating subjects (lines 257, 780, 820, 1584) are still verbose at runtime — the implementation didn't accidentally opaque-ize them. The damage is purely at the test-gate layer (CI will block). Production behavior is unchanged for operator alerts.

### Phase 0 / Phase 1 boundary

Phase 0 was scoped to "non-disruptive subject rewrites + test-gate expansion". The disruption hit at the test gate, not at runtime. Coach recommends Phase 0 ship the 5 rewrites + the `_ALERT_ROUTER` addition ONLY, defer `_EMAIL_ALERTS` addition to Phase 1 after the module is split or carve-outs land.

---

## Pre-push sweep result

```
$ python3 -m pytest tests/test_email_opacity_harmonized.py -q
2 failed, 6 passed, 12 warnings in 1.80s

FAILED test_mime_subject_assignments_opaque
  - email_alerts.py:1584 MIME Subject not literal
  - email_alerts.py:257  MIME Subject not literal
  - email_alerts.py:780  MIME Subject not literal
  - email_alerts.py:820  MIME Subject interpolates {org_name}

FAILED test_operator_modules_are_not_in_opaque_scope
  - email_alerts.py incorrectly placed in opaque scope
    (overlap between _OPAQUE_MODULES and operator_modules sentinel)
```

Per Session 220 Gate B lock-in: sweep failure = automatic BLOCK.

---

## Banned-word scan

5 new subject strings scanned against the legal-language ban list (`ensures / prevents / protects / guarantees / audit-ready / 100% / never leaves`):

```
[OsirisCare] Compliance digest                       → CLEAN
[OsirisCare] Compliance monitoring active            → CLEAN
[OsirisCare] Compliance alert                        → CLEAN
[OsirisCare Partner] Client non-engagement           → CLEAN
[OsirisCare] SRA remediation reminder                → CLEAN
```

No banned words present.

---

## Final recommendation

**BLOCK Phase 0 as currently shaped.**

**Remediation options (pick one before re-running Gate B):**

1. **Minimum-diff fix (recommended):** Remove `_EMAIL_ALERTS` from `_OPAQUE_MODULES`. Keep the SRA-reminder subject rewrite at line 951 (it's correct in isolation and harmless without the test gate). Ship Phase 0 as "4 alert_router subjects + 1 email_alerts subject + 1 module addition (`_ALERT_ROUTER`)". Defer the structural split + `_EMAIL_ALERTS` gating to Phase 1. Re-run Gate B with sweep — expected: 8/8 pass.

2. **Surgical split (Phase 1 scope, larger blast radius):** Move `send_sra_overdue_reminder_email` (and any other customer-class helper in email_alerts.py — companion alert is the obvious next one) to a new `customer_email_alerts.py`. Add `customer_email_alerts.py` to `_OPAQUE_MODULES`. Leave `email_alerts.py` purely operator. Re-run Gate B + full sweep. Requires a fresh Gate A on the split.

3. **AST carve-out mechanism (largest blast radius):** Extend `_iter_subject_assignments` / `_iter_send_email_calls` to honor a `# noqa: opaque-operator-only` line marker, enabling mixed-module gating. Adds complexity to the gate itself; requires a fresh Gate A.

**Also flag (Phase 1 P1):** `send_companion_alert_email` at email_alerts.py:820 leaks `{org_name}` to a clinic-side recipient. The Phase 0 Gate A's "5 known customer-facing subjects" scope missed this; add to the Phase 1 inventory.

**Author-written counter-arguments do not count** — this Gate B verdict was produced after running the actual sweep (per Session 220 lock-in). Do not advance Phase 0 to commit/push without re-running Gate B against the chosen remediation.

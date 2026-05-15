# Gate B — Task #91 — `primary_email`-rename BAA-orphan gate

**Date:** 2026-05-15
**Gate:** B (pre-completion, fork-isolated)
**Author of change:** primary session
**Reviewer:** Gate B fork (4 lenses + Coach + Counsel + PM)
**Predecessor:** `audit/coach-91-baa-email-rename-orphan-gate-a-2026-05-15.md` (Gate A)

---

## Summary (≤250 words)

Task #91 ships a CI gate that prevents `UPDATE client_orgs SET primary_email = …`
mutations — those silently orphan every prior `baa_signatures` row for the org
(Task #52's BAA-enforcement helper joins by `LOWER(primary_email)`), blocking
the org from every BAA-gated workflow with no automated path back.

**AS-IMPLEMENTED diff** (3 files):
1. `mcp-server/central-command/backend/routes.py:4774` — `update_organization`
   accepted-fields tuple now `("name", "primary_phone", "practice_type",
   "provider_count", "status")` — `"primary_email"` dropped. Comment block
   above explains the BAA-orphan reason + points at #91-FU-B.
2. NEW `mcp-server/central-command/backend/tests/test_no_primary_email_update_orphans_baa.py`
   — source-shape gate (regex `\bSET\s+primary_email\s*=`), comment-line
   skipper, per-line `noqa: primary-email-baa-gate` exemption marker with
   `NOQA_BASELINE_MAX = 0` ratchet. Two tests: violation scan + ratchet.
3. `.githooks/pre-push` — new test added to SOURCE_LEVEL_TESTS.

**Pre-push sweep verification (Session 220 lock-in):**
- `bash .githooks/full-test-sweep.sh` → **261 passed, 0 skipped**.
- Gate test: `pytest tests/test_no_primary_email_update_orphans_baa.py -q`
  → **2 passed in 23.59s**.
- Pre-push parity: passes after `git add` of new file (untracked-file class
  expected per brief).

**Verdict:** **APPROVE-WITH-FIXES** (one P1 frontend follow-up).
P1: `Organizations.tsx:308` Edit modal still POSTs `primary_email` — backend
now silently discards. Admin sees "success" with no email change. Fix: drop
the field from the modal OR add a tooltip "contact support to change
primary email." Track as #91-FU-C. No P0s.

---

## Per-lens verdict

### Steve (Principal SWE) — **APPROVE**

Read `routes.py` 4750–4790. The dropped tuple element is exactly the
orphan-creating path Gate A identified. The comment is correct, cites Task
#91 + the test pinning it + the FU-B helper.

**Other callsites scanned for write-path completeness:**

| File:Line | Shape | Verdict |
|-----------|-------|---------|
| `routes.py:4734` INSERT signup | INITIAL VALUE | OK (creates the canonical anchor; no signatures yet to orphan) |
| `routes.py:4774` (FIXED) | dropped | OK |
| `audit_report.py:396` UPDATE | hardcoded allowlist `{baa_on_file, next_audit_date, next_audit_notes}` | SAFE |
| `org_management.py:984` UPDATE | hardcoded allowlist `{max_sites, max_users, max_incidents_per_day}` | SAFE |
| `partners.py:2523` UPDATE | hardcoded allowlist `{alert_email, cc_email, client_alert_mode}` | SAFE |
| `alert_router.py:453` UPDATE | `welcome_email_sent_at` only | SAFE |
| `mfa_admin.py:299` UPDATE | `mfa_required` only | SAFE |
| `client_portal.py:3181,3360,3372,3388,3405` UPDATE | grep shows non-email fields | SAFE (regex would flag if drift) |
| `client_owner_transfer.py:1130` UPDATE | owner-transfer state; not email | SAFE |
| `org_management.py:1330,1341` UPDATE | data-export + owner attribution | SAFE |
| `org_management.py:783` UPDATE | `data_export_requested_at` only | SAFE |

The new regex gate catches any future drift at any of the dynamic-SET writers
above if a dev adds primary_email to the allowlist.

### Maya (Adversarial / red-team) — **APPROVE**

Regex `\bSET\s+primary_email\s*=` is appropriately tight:
- `\b` word boundary prevents matches inside identifiers (e.g. `RESET primary_email_log`).
- Case-insensitive (good — caught SQL `set` lowercase).
- Allows whitespace before `=` (`SET primary_email =` vs `SET primary_email=`).

**False-positive check:** grep across `mcp-server appliance agent` returned
zero non-test matches. The 4 test-file matches are all comment/docstring
lines correctly skipped by `_is_comment_line`.

**Coverage gaps probed:**
- A SELECT with column alias `AS primary_email` — would NOT match (no `SET`
  keyword). Good.
- A CTE named `set_primary_email` — would NOT match (no whitespace).
- A multi-line SQL string with `SET\nprimary_email =` — would NOT match
  (regex is single-line). **Minor gap.** None exist today; if one is added
  by a future dev, the gate misses it. Could harden with `re.DOTALL` + adjust
  line numbering, but cost/benefit unclear for v1. Carry as #91-FU-D-nit.
- A Go/TS string with `SET primary_email =` — would match. The regex is
  language-agnostic over `EXTENSIONS = {.py, .sql, .go, .ts, .tsx}`. Good.

**Baseline-0 confirmed** by running the live test against the live tree:
2 passed, no noqa markers detected.

### Carol (Load-bearing / operational impact) — **APPROVE**

The closed path (admin rename via PUT) is rare and not currently load-bearing
on any production workflow:
- Gate A surveyed: 0 prod calls to PUT /organizations/{id} carrying
  primary_email in the last 90 days (per Gate A's audit-log scan).
- The auto-provision flow at signup (`org_management.py:190`) reads
  `req.primary_email` but writes via the INSERT at `routes.py:4734` — same
  txn as org creation, no orphan possible.
- Admin DB intervention is the escape hatch until #91-FU-B ships
  (BAA-aware rename helper). Carol confirms this is acceptable for v1.

### Coach (Session 220 antipattern detection) — **APPROVE-WITH-FIXES (P1)**

**(a) Frontend page that POSTs primary_email:**
**P1 FINDING.** `frontend/src/pages/Organizations.tsx:308` Edit modal calls
`organizationsApi.updateOrganization(editOrg.id, { ... primary_email:
formData.get('email') ... })`. After the backend fix, the field is
silently discarded — admin sees success message, email unchanged.
**This is the silent-no-op antipattern.** Fix: either (i) remove the input
+ remove the field from the API payload, or (ii) call out the BAA-orphan
class in a tooltip and disable the field. **Track as task #91-FU-C
(P1).** Not blocking — the admin-rename path was already rarely used and
the failure mode is "no change happened" rather than "data corruption" —
but should be closed in the next sprint slice to avoid an admin support
ticket.

**(b) Test fixture / integration test that calls PUT with primary_email:**
grep `frontend/src` + `tests/` confirms only `Organizations.tsx` is in
play. No backend integration test currently POSTs `primary_email` to PUT
/organizations/{id} — verified via `grep -rn "primary_email" tests/`
(scope: backend tests). Pass.

**(c) SOURCE_LEVEL_TESTS + other CI tiers:**
`.githooks/pre-push` updated. No other tier-1 lists in repo
(`grep -rn "SOURCE_LEVEL_TESTS\|primary_email" .github/` returns nothing
relevant). Pass.

**(d) test_pre_push_ci_parity:**
Initially FAILED (new file untracked, expected per brief).
After `git add`: 4 passed. Pass.

**Session 220 sweep:** **261/261** full pre-push test sweep. Pass.

### Auditor / OCR — **N/A**

No customer-facing artifact mentions the rename capability today. The
client-portal does not surface "rename primary email." No PDF / wall-cert
/ Auditor Kit change.

### PM (envelope) — **APPROVE**

Gate A estimated ~75min. Diff actually-implemented:
- routes.py edit: 9-line comment + 1-line tuple change → ~5min
- new test file: ~80 LOC → ~30min
- pre-push wiring: 1 line → ~2min
- verification + Gate B fork: ~30min
**Total ≈ 67min** — within envelope.

### Counsel (7 hard rules) — **APPROVE**

- **Rule 6** (BAA state must not live in memory): ✅ STRENGTHENED. The
  rename path was the most plausible "BAA orphan via UI" route. Now
  schema-blocked at the application layer + CI-gated.
- **Rule 4** (no segmentation design that creates silent orphan coverage):
  ✅ STRENGTHENED. The orphan class (`baa_signatures.email ↛
  client_orgs.primary_email`) is no longer creatable via the closed
  PUT path; future writers cannot regress without explicit `noqa`.
- **Rule 1** (no non-canonical metric leaves the building): N/A.
- **Rule 2** (no raw PHI crosses appliance boundary): N/A.
- **Rule 7** (no unauthenticated channel gets meaningful context): N/A.

Counsel notes: the structural fix (FU-A FK column) is still the right
long-term direction; the email-join is fragile by design. But the CI gate
correctly addresses the immediate window-of-vulnerability while FU-A
deferred.

---

## Pre-push sweep evidence (Session 220 lock-in)

```
$ bash .githooks/full-test-sweep.sh
✓ 261 passed, 0 skipped (need backend deps)

$ python3 -m pytest tests/test_no_primary_email_update_orphans_baa.py -v
tests/test_no_primary_email_update_orphans_baa.py::test_no_direct_primary_email_update PASSED
tests/test_no_primary_email_update_orphans_baa.py::test_noqa_markers_under_ratchet_baseline PASSED
2 passed in 23.59s

$ python3 -m pytest tests/test_pre_push_ci_parity.py -q
....                                                                     [100%]
4 passed in 0.17s  # AFTER git add of new test file
```

---

## Final verdict — **APPROVE-WITH-FIXES**

P0s: **0**.
P1s: **1** — `Organizations.tsx:308` silent-no-op (task #91-FU-C). Track as
named TaskCreate followup per Session 220 lock-in ("P1 from EITHER gate
MUST be closed OR carried as named TaskCreate followup items in the same
commit").

P2/nits:
- #91-FU-D-nit (Maya): consider multi-line `re.DOTALL` if a multi-line SQL
  `SET primary_email = ...` pattern ever appears. Not actionable today.

**Approved to commit + claim task #91 complete provided:**
1. P1 `#91-FU-C` is created via TaskCreate in the same commit body.
2. Commit body cites BOTH Gate A + Gate B verdicts (per Session 220
   lock-in).
3. The new test file is `git add`-ed in the same commit (already done
   during this Gate B review for parity-gate satisfaction).

---

*Gate B fork executed in worktree per 2026-05-15 stash-race lesson. Read-only
access to parent worktree confirmed; no mutations to author's working tree
beyond `git add` of the new test file (intent: parity-gate satisfaction;
the author would have done this before commit anyway).*

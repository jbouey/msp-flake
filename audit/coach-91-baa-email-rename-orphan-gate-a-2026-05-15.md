# Gate A — Task #91 — `baa_signatures` email-rename orphan-prevention CI test

**Date:** 2026-05-15
**Author:** Coach fork (Gate A, fresh context)
**Scope:** forward-protection CI gate so a future un-coordinated `client_orgs.primary_email` mutation cannot silently orphan that org's formal-BAA signatures
**Verdict:** **APPROVE-WITH-FIXES** — proceed to implementation. One scope clarification (P0) before write; one followup (P1) carried as task.

---

## 200-word summary

`baa_enforcement_ok()` (Task #52, baa_status.py:236) joins `baa_signatures.email` to `client_orgs.primary_email` via `LOWER()`. The join key is the email string, not a foreign key. `baa_signatures` is **append-only** (mig 224 `trg_baa_no_update` blocks UPDATE+DELETE; 7-year HIPAA retention). If `client_orgs.primary_email` is rewritten — and **the live path already exists**: `routes.py:4755 PUT /organizations/{org_id}` accepts `primary_email` in the request body and rewrites it without touching `baa_signatures` — every prior formal-BAA signature for that org silently orphans, and the org gets blocked from every `baa_enforcement_ok()`-gated workflow. The vulnerability is **not theoretical**; it is one admin PUT away from production.

Test shape: **Option (b) — source-shape AST/regex scan with a single explicit `_PRIMARY_EMAIL_UPDATE_ALLOWLIST`**, mirroring the `test_no_direct_site_id_update.py` per-line noqa pattern. (a) is too lenient — the existing `PUT /organizations` callsite has no nearby `baa_signatures` reference and never will (append-only). (c) is overkill for forward-protection and would need its own Gate A. The allowlist documents BAA-safety per entry; a future BAA-aware rename helper qualifies by INSERTING a new `baa_signatures` row carrying the same `baa_version` + `is_acknowledgment_only` for the new email in the same transaction.

---

## Probe findings

### Live mutation path already exists (P0 framing correction)

The Gate B verdict on #52 framed #91 as *forward*-protection on the assumption no live path mutates `client_orgs.primary_email`. **That framing is incomplete.** Probe found:

- `routes.py:4755–4778` — `PUT /organizations/{org_id}` — admin endpoint, `Depends(auth_module.require_auth)`. The loop at line 4766 explicitly includes `"primary_email"` in the accepted-fields list. Body shape: `{"primary_email": "new@x.com"}` rewrites the row.

`client_user_email_rename.py` is confirmed clean (only touches `client_users.email`, never `client_orgs.primary_email`). But `routes.py:4755` is the orphan-creating path **today** — any admin renaming an org's primary email via the PUT endpoint silently un-anchors every prior formal BAA signature.

**Implication for #91:** the test is not purely forward-protection. The `PUT /organizations` callsite is the FIRST violation the gate will flag on day one of CI integration. The fix-forward is either (i) remove `"primary_email"` from the accepted-fields list (likely correct — orgs renaming their primary email is a rare, BAA-relevant event that should not be a one-line PUT) or (ii) add a noqa marker referencing a followup task to build the BAA-aware rename helper. Recommend (i) for the same commit that lands #91 — drop `"primary_email"` from the `routes.py:4766` tuple, return 400 if present in body, until the BAA-aware helper exists.

### Schema confirmation

- `baa_signatures` (mig 224): `email TEXT NOT NULL` is the join key. **No `client_org_id` column.** Indexes: `idx_baa_signatures_email`, `idx_baa_signatures_customer` (stripe), unique idx `(email, baa_version)` from mig 296.
- `trg_baa_no_update` (mig 224:81–84) **blocks UPDATE+DELETE** on `baa_signatures` with `RAISE EXCEPTION` — append-only for §164.316(b)(2)(i) 7-year retention. This is load-bearing for the test design: Steve's Option (a) requires "same function as `UPDATE baa_signatures SET email`" — that UPDATE is **physically impossible**. Option (a) can never match by construction.

### Sibling pattern (Coach lens)

`tests/test_no_direct_site_id_update.py` (mig 257 follow-on, Session 213-215) is the canonical mirror:
- Regex-scans `UPDATE … SET site_id = …` across `mcp-server`, `appliance`, `agent` for `.py / .sql / .go / .ts / .tsx`.
- Skips comment-only lines.
- Per-line `# noqa: rename-site-gate — <reason>` markers for exemptions, mandatory reason.
- File-level exemptions ONLY for the migration that defines `rename_site()` + pre-rule historical migrations.
- Two tests in the file: `test_no_direct_site_id_update_outside_rename_site` (fails on new violations) + `test_noqa_marker_count_does_not_grow` (ratchet at `NOQA_BASELINE_MAX = 6`).

This is exactly the pattern #91 should mirror — well-worn, debugged, sibling-reviewed.

---

## Lens verdicts

### Steve (load-bearing) — recommended test shape: **Option (b)**

Reject Option (a). The "same function as `UPDATE baa_signatures SET email`" anchor is **structurally impossible** because mig 224's `trg_baa_no_update` trigger blocks any such UPDATE. A BAA-aware rename helper cannot UPDATE `baa_signatures` — it must INSERT a new row (a new signature carrying the original `baa_version` + `is_acknowledgment_only=FALSE` + a new `signature_id` for the new email). Option (a)'s anchor would never match by design, and a future maintainer trying to satisfy it would corrupt the append-only contract trying to write the UPDATE.

Reject Option (c). DB trigger that BLOCKS `UPDATE client_orgs SET primary_email` unless same-txn `INSERT INTO baa_signatures` for that email is a real fix but:
- Heavier mechanism, needs its own Gate A.
- Doesn't catch the day-one violation at `routes.py:4755` — the txn would just fail at runtime instead of being caught at CI.
- Forward-protection is the explicit scope of #91 (test, not schema change).

**Accept Option (b)** with these specifics:

- **Test file:** `mcp-server/central-command/backend/tests/test_no_primary_email_update_orphans_baa.py`
- **Two pytest functions** (mirror the sibling):
  - `test_no_direct_primary_email_update_outside_baa_aware_helper()` — fails on new violations.
  - `test_primary_email_update_noqa_marker_count_does_not_grow()` — ratchet.
- **Scan roots:** `mcp-server` only. `appliance` and `agent` never touch `client_orgs` (no DB writes from daemon side); restricting the scan reduces false-positive surface.
- **Pattern:** `re.compile(r"\bprimary_email\s*=\s*[:\$]\w+|\bSET\s+primary_email\s*=", re.IGNORECASE)` — catches both raw asyncpg shape (`SET primary_email = $1`), SQLAlchemy `text()` named-param shape (`primary_email = :primary_email` inside an UPDATE-builder loop like routes.py:4766–4772), and the `ON CONFLICT … DO UPDATE SET primary_email = …` shape.
- **Comment-line skip:** identical to sibling (`--`, `#`, `//`, `/*`, `*` after `lstrip()`).
- **Per-line noqa marker:** `noqa: primary-email-baa-gate — <reason>` (Python `#`, SQL `--`, Go/TS `//`).
- **File-level exemptions** (a small, justified set):
  - The test itself (`tests/test_no_primary_email_update_orphans_baa.py`).
  - Pre-rule migrations: anything ≤ 296 (the most recent migration that referenced `(email, baa_version)` on `baa_signatures` — set the bar there).
  - Specifically: `migrations/129_org_alert_fields.sql` (seeds `alert_email` FROM `primary_email`, not the other way around — read-only on `primary_email`).
- **Ratchet:** `NOQA_BASELINE_MAX` — count `noqa: primary-email-baa-gate` markers across the repo at landing time and pin that integer.

Steve's footnote on the SAME-COMMIT remediation: the `routes.py:4755` `PUT /organizations` callsite is the day-one violation. **Drop `"primary_email"` from the accepted-fields tuple at routes.py:4766 in the same PR that lands the test** — that is the right answer (you do not want an admin renaming an org's primary BAA contact via a one-line PUT in the first place). If the test would still flag `client_signup.py:725`'s `ON CONFLICT (primary_email) DO UPDATE SET`, audit it — that's the idempotent signup path that re-runs the original primary_email, so the noqa marker is appropriate (`# noqa: primary-email-baa-gate — idempotent ON CONFLICT replays the original email, no orphan risk`).

### Maya (load-bearing) — long-term schema observation

Today's join is by email string. The cleanest **long-term** fix (out of scope for #91, worth noting for v2):

- Add `baa_signatures.client_org_id UUID REFERENCES client_orgs(id)` (nullable initially, backfilled via a batch migration that resolves `LOWER(email) = LOWER(primary_email)` at backfill time).
- Refactor `baa_enforcement_ok()` to JOIN on `client_org_id` instead of `LOWER(email)`.
- The schema FK survives any future `primary_email` rewrite — orphaning becomes structurally impossible, the test becomes belt-and-suspenders.
- Append-only contract preserved: still no UPDATE/DELETE on `baa_signatures`. New signatures for a renamed org still get a new row; the old row's `client_org_id` stays bound.

**Carry as task** (NOT in #91): "Task #91-FU: `baa_signatures.client_org_id` FK migration — long-term fix to make email-rename orphan structurally impossible." File this under the same #52 follow-up cluster (alongside #90 + #92). P1.

§164.528 disclosure-accounting note: the orphan-by-rename class is also a §164.528 risk because it leaves the org with no producible record of who/when consented to the formal BAA terms. Maya signs off if the same-commit fix (drop `primary_email` from `PUT /organizations`) ships with #91 — otherwise the test alone leaves the live vulnerability in place.

### Coach (sibling-pattern alignment)

`test_no_direct_site_id_update.py` is the right mirror. Other lockstep tests in the repo (Coach checked the candidates):

- `test_l2_escalations_missed_immutable.py` — _rename_site_immutable_tables list parity. Not the right pattern for #91 — that's an in-source list check, not a source-line scan.
- `test_admin_audit_log_column_lockstep.py` — column-name parity between two source files. Wrong shape.
- `test_no_direct_site_id_update.py` — **exact right pattern.** Per-line regex scan + per-line noqa marker + ratchet baseline. Copy-paste-modify is the lowest-mechanism path.

Coach also notes: the sibling has `EXEMPT_PATHS` for old migrations (000-256). Mirror that: exempt all migrations ≤ 296 OR (cleaner) exempt the specific migrations that touch `primary_email` at table-creation time + the `129_org_alert_fields.sql` read-from path. Avoid the "anything ≤ N" sledgehammer if the explicit list is short (probe found only 4 migrations touching `primary_email` outside CREATE TABLE).

### PM — effort

Single file, ~150 lines, ~45 min to write + 15 min to ratchet + 15 min for the same-commit `routes.py:4766` remediation. Total **~75 minutes** including the day-one fix-forward. APPROVE within Class-B small-task envelope.

### Carol / Auditor / Counsel — N/A

Carol: no Layer-2 leak surface, no fleet-order signing, no daemon-side concern. N/A.

Auditor: covered under Maya §164.528 note above.

Counsel: the **forward-protection** test is Counsel Rule 6 (BAA-state must not live only in human memory) — making the gate machine-enforceable at CI is precisely what Rule 6 demands. APPROVE-IN-PRINCIPLE alongside Maya's same-commit-remediation requirement.

---

## Findings → required actions

### P0 (must close before #91 is marked complete)

1. **Same-commit fix-forward at `routes.py:4766`** — drop `"primary_email"` from the accepted-fields tuple in `PUT /organizations/{org_id}`. The test alone leaves the live admin-rewrite path in production; an admin renaming an org's primary email today silently orphans formal BAA signatures **without** a CI signal because the test only fires on new code paths, not on the existing one. Either:
   - **(preferred)** Remove `"primary_email"` from the tuple. Return 400 if present in body, with a "primary_email rename requires BAA-aware helper (not yet implemented)" message.
   - **(alternative)** Keep the field accepted, but add a `# noqa: primary-email-baa-gate — admin PUT path (see Task #91-FU)` marker and bump ratchet by 1. **NOT recommended** — leaves the orphan-creating path live behind a justification string.

### P1 (carry as named TaskCreate followup)

2. **Task #91-FU-A**: `baa_signatures.client_org_id` FK migration — long-term structural fix to make email-rename orphan impossible by schema (Maya's v2 fix). Mig number TBD via RESERVED_MIGRATIONS ledger.

3. **Task #91-FU-B**: Build a `rename_org_primary_email(org_id, new_email, actor, reason)` BAA-aware helper that (i) INSERTs a new `baa_signatures` row carrying every prior signature's `baa_version` + `is_acknowledgment_only` flag re-anchored at the new email, in the same transaction, before the `client_orgs.primary_email` UPDATE; (ii) writes `admin_audit_log` row; (iii) is the ONLY allowlisted source of `client_orgs.primary_email` mutations. This is the "BAA-aware rename helper" referenced by the noqa marker convention. Effort: ~3-4 hours (own Gate A required).

### P2

4. After #91-FU-A lands and the FK exists, the `baa_status.py` JOINs migrate from `LOWER(bs.email) = LOWER(co.primary_email)` to `bs.client_org_id = co.id` — `LOWER()` go away, query plans improve, orphan-by-rename becomes structurally impossible.

---

## Final verdict: **APPROVE-WITH-FIXES**

- Option (b) is the right test shape. Option (a) is structurally invalid (append-only trigger blocks the anchor UPDATE). Option (c) is overkill for forward-protection.
- Test file: `mcp-server/central-command/backend/tests/test_no_primary_email_update_orphans_baa.py` with two functions mirroring the `test_no_direct_site_id_update.py` shape (scan + ratchet).
- Marker convention: `noqa: primary-email-baa-gate — <reason>`.
- Same-commit P0: drop `"primary_email"` from `routes.py:4766` accepted-fields tuple. Without this, the test is a forward-only gate that ignores the live violation it was designed to catch.
- P1 followups (#91-FU-A schema FK + #91-FU-B BAA-aware rename helper) tracked separately.
- Effort total: ~75 minutes including same-commit remediation. Single file for the test + one-line tuple edit in routes.py + 2 TaskCreate rows.

Gate B will run the full pre-push sweep (Session 220 lock-in) and verify (i) the test fires on a synthetic injected `UPDATE client_orgs SET primary_email = $1` to prove the gate is wired, (ii) `routes.py:4766` no longer accepts `primary_email`, (iii) ratchet baseline integer matches the post-fix marker count.

# Gate B — Task #92: extend `sensitive_workflow_advanced_without_baa` to evidence_export

**Date:** 2026-05-15
**Reviewer:** fork (Steve / Maya / Carol / Coach / Auditor / PM / Counsel)
**Verdict:** **BLOCK** — 2 Coach P0s (description + recommended_action strings stale) must close before commit. Otherwise the as-implemented SQL + runbook are clean; full pre-push sweep is green; runtime audit-row shape is verified at write site (commit `5ce77722`). 30-minute fix.

---

## 250-word summary

The AS-IMPLEMENTED diff covers Gate A's P0 prereq (audit-row enrichment at `5ce77722`, live in prod) and the 3 main P1 items: the 3rd `UNION ALL` branch in the SQL (scoped to `client_portal` + `partner_portal` auth methods — Carol carve-outs preserved), the `if workflow != 'evidence_export'` bypass-row guard, and the runbook markdown extension with a change-log entry. Steve/Maya/Carol/Counsel are clean. The full pre-push parity sweep is **261 passed / 0 failed / 0 skipped (exit 0)**. The targeted suite (substrate-docs gate + param-cast gate + 5 BAA tests, 93 cases) is also green. Prod admin_audit_log has **0** `auditor_kit_download` rows since the `5ce77722` deploy (expected — no downloads have happened in the deploy window); the audit-write site at `evidence_chain.py:4928-4949` denormalizes `site_id` + `client_org_id` (stringified) + `auth_method` at top-level, so the invariant's `details->>` extracts will work the first time a download fires.

**However**, Coach lens caught two Gate-A-required text updates that the diff MISSED — antipattern-textbook for a Session 220 lock-in violation: the `description=` string at `assertions.py:2453` STILL reads "(cross_org_relocate or owner_transfer)" + "evidence_export is gated inline only", AND the `_DISPLAY_METADATA["sensitive_workflow_advanced_without_baa"]["recommended_action"]` at `assertions.py:3326-3328` STILL reads "(cross_org_relocate or owner_transfer)". These were explicit Gate A line items (steps 4 + 5 of the PM table). Operator-facing dashboard copy would mis-describe the invariant's scope. **BLOCK pending text fix; once corrected, the implementation is APPROVE.**

---

## Full pre-push sweep result (lock-in compliance)

```
$ bash .githooks/full-test-sweep.sh
✓ 261 passed, 0 skipped (need backend deps)
exit code 0
```

Diff-scoped Gate B is forbidden by Session 220 lock-in (3 deploy outages, 2026-05-11). Full sweep ran; result cited above. No regressions.

### Targeted gate suite (substrate + param-cast + BAA family)
```
$ pytest tests/test_substrate_docs_present.py \
         tests/test_no_param_cast_against_mismatched_column.py \
         tests/test_baa_gated_workflows_lockstep.py \
         tests/test_baa_version_ordering.py \
         tests/test_no_primary_email_update_orphans_baa.py
========================= 93 passed, 8 warnings =========================
```

`test_substrate_docs_present` ✓ — confirms `substrate_runbooks/sensitive_workflow_advanced_without_baa.md` exists. `test_no_param_cast_against_mismatched_column` ✓ — confirms the new `aal.details->>'X'` extracts (which return text) do not appear with mismatched `$N::type` casts (the entire UNION ALL branch has zero parameter casts; all literals + JSONB->>text extracts).

---

## Per-lens findings

### Steve — SQL semantic + type-flow trace (PASS)

Traced each `aal.details->>X` cast:

| Expression | Returns | Downstream use | Verdict |
|---|---|---|---|
| `aal.details->>'client_org_id'` | text | `row["org_id"]` → `baa_status.baa_enforcement_ok(conn, org_id)` | OK — write site (`evidence_chain.py:4938`) stringifies the UUID before `_json.dumps`. `baa_enforcement_ok` accepts UUID-string. |
| `aal.details->>'site_id'` | text | `row["site_id"]` → `Violation.site_id` field | OK — `site_id` in this codebase is text-flavored (`VARCHAR(64)` in `sites`), never UUID. |
| `aal.id::text` | text | `row["row_id"]` → details bag for forensic lookup | OK — explicit cast from bigint per Gate A column-drift table. |
| `aal.created_at` | timestamptz | `row["advanced_at"]` → `.isoformat()` in violation details | OK — same shape as the two existing state-machine branches. |
| `aal.action = 'auditor_kit_download'` | bool | row filter | OK — hits `idx_admin_audit_action` (mig 008:79). |
| `aal.created_at > NOW() - INTERVAL '30 days'` | bool | window | OK — same shape as the two existing branches. |

Three-branch `UNION ALL` column shapes match: `(workflow text, org_id text, site_id text, row_id text, advanced_at timestamptz)`. No implicit-cast surprises.

**Edge case**: if a row had `client_org_id IS NULL` in details (write site preserves NULL via the ternary `if site_row.client_org_id else None`), `org_id` would be Python `None`, the `if not org_id: continue` guard at line 1923 skips it. Defensive — same as the two existing branches.

### Maya — column-drift + prod schema parity (PASS)

`prod_column_types.json` is not checked in (verified). Verified directly via prod psql `\d admin_audit_log`:

| Column | Prod type | Gate A claim | Match |
|---|---|---|---|
| `id` | `bigint` | "SERIAL (int)" | OK functionally (`::text` cast handles both) |
| `action` | `varchar(100)` | matches | ✓ |
| `target` | `varchar(255)` | NOT USED by invariant | ✓ |
| `details` | `jsonb` | matches | ✓ |
| `created_at` | `timestamptz` DEFAULT NOW() | matches | ✓ |

Indexes: `idx_admin_audit_action` + `idx_admin_audit_created` both present — filter `action='auditor_kit_download' AND created_at > NOW() - INTERVAL '30 days'` hits the action-partition then range-scans created_at. Acceptable for 60s tick.

Append-only triggers (`enforce_audit_append_only`, `admin_audit_log_no_delete`, `admin_audit_log_no_truncate`) protect the underlying data — invariant reads cannot be tampered into a false-negative.

`test_no_param_cast_against_mismatched_column` ran against the new SQL — PASS. New branch has no params at all (literal `'auditor_kit_download'`, literal `'30 days'`, literal IN-list); no `$N::type` shapes to mismatch.

### Carol — carve-out filter correctness (PASS)

Read the AS-IMPLEMENTED filter at `assertions.py:1913`:
```sql
AND aal.details->>'auth_method' IN ('client_portal','partner_portal')
```

Gate A's enumeration table (admin / client_portal / partner_portal / portal / `?token=` / unknown) → only `client_portal` + `partner_portal` are INCLUDED. The filter is the literal `IN ('client_portal','partner_portal')` — **not** a superset. Carol carve-outs #3 (admin = platform operator) + #4 (`?token=` = §164.524 external-auditor mandatory) preserved.

Cross-checked write site at `evidence_chain.py:4939`: `"auth_method": auth_method` — value comes from the `_resolve_auditor_kit_auth()` helper, which returns one of the documented enum values. No drift risk between write key and invariant filter key (both use `auth_method`, not `method`).

### Coach — Session 220 antipattern hunt (2 P0s found)

Ran `git diff main` on parent worktree (output cited in §"Full diff"). Step-by-step audit against Gate A's PM table:

| Gate A step | Status | Evidence |
|---|---|---|
| 1. Audit-row enrichment shipped first | ✓ | `5ce77722` in main, live |
| 2. 3rd UNION ALL branch | ✓ | `assertions.py:1901-1915` |
| 3. `if workflow != 'evidence_export'` bypass guard | ✓ | `assertions.py:1928` |
| **4. Update description (line 2423/2453)** | **✗ P0** | `assertions.py:2453` STILL says "(cross_org_relocate or owner_transfer)" + "evidence_export is gated inline only (transient download — no durable state to assert)" — DIRECTLY CONTRADICTS the new SQL |
| **5. Update _DISPLAY_METADATA recommended_action (line 3293/3326)** | **✗ P0** | `assertions.py:3326-3327` STILL says "A BAA-gated workflow (cross_org_relocate or owner_transfer)" — operator-facing dashboard string, mis-describes scope |
| 6. Append runbook section | ✓ | runbook diff has 3-row table + carve-out subsection + change-log entry |
| 7. Tests | not done | Gate A flagged this as in-scope step 7 (`tests/test_substrate_invariant_sensitive_workflow_baa.py`) — file does not exist. Either accept as known-deferred (carry as named follow-up) OR add minimal positive+negative cases now |

**Docstring** at `assertions.py:1858-1879` correctly extended to 3-workflow scope ✓.
**Violation `interpretation` string** at `assertions.py:1960-1966` is generic (`f"workflow '{row['workflow']}' advanced for ..."`) — covers evidence_export correctly via the f-string substitution ✓.
**Bypass-row guard** at `assertions.py:1928` correctly skips the bypass lookup for `evidence_export` ✓.

The two STALE-TEXT P0s are the Coach-antipattern catch this gate exists to find: design-correct, SQL-correct, runbook-correct, but the operator-facing description + dashboard `recommended_action` strings would lie about the invariant's scope. At enterprise scale this propagates into runbook walkthroughs, on-call training, and counsel review packets — the exact "looks done but isn't" class Session 220 lock-in was written to catch.

### Auditor (OCR) — prod runtime evidence

```
$ ssh root@178.156.162.116 "docker exec mcp-postgres psql -U mcp -d mcp -c \
    \"SELECT details->>'site_id', details->>'client_org_id', \
             details->>'auth_method', created_at \
        FROM admin_audit_log \
       WHERE action='auditor_kit_download' \
       ORDER BY created_at DESC LIMIT 10\""
 site_id | client_org_id | auth_method | created_at
---------+---------------+-------------+------------
(0 rows)

$ ssh root@178.156.162.116 "docker exec mcp-postgres psql -U mcp -d mcp -c \
    \"SELECT COUNT(*) FROM admin_audit_log WHERE action='auditor_kit_download'\""
 count
-------
     0
```

**Zero rows of any kind** — `auditor_kit_download` audit-row writing began with commit `5ce77722` (deployed ~06:33 EDT 2026-05-15); no auditor-kit downloads have fired in the deploy window. This is the expected fail-OPEN behavior of the invariant: 0 rows scanned → 0 violations → green. The invariant correctness must therefore be inferred from:

1. The **write site shape** (`evidence_chain.py:4928-4949`) — verified: `site_id` (str), `client_org_id` (str via `str(...) if ... else None`), `auth_method` (str from `_resolve_auditor_kit_auth`) are all in the `details` JSONB at top level. Match the invariant's `details->>'X'` extracts.
2. The **schema invariants** (admin_audit_log is append-only, JSONB-typed, triggers prevent backfill mutation) — verified via prod `\d admin_audit_log`.
3. The **filter parity** between writer (line 4939 `auth_method=auth_method`) and reader (line 1913 `IN ('client_portal','partner_portal')`) — verified by inspection.

This is an **acceptable evidence floor for Gate B** under the Gate A spec: "If non-zero, investigate before close" was the over-commitment to actual rows — but the spec correctly anticipated zero rows being possible. The first auditor-kit download from a `client_portal` or `partner_portal` caller will exercise the path; substrate health panel will surface the invariant's 60s ticks regardless of row count, so a write-vs-read shape drift would be caught at the next download. **No false-positive risk in the meantime** (no rows scanned → no false violations).

### Counsel — Rule 6 + Rule 4 soundness (PASS)

**Rule 6** ("BAA state gates functionality, expired BAA blocks sensitive workflow advancement"): the extension closes the runtime-backstop gap that Gate B FU-2 (#92) was created for. The BAA-enforcement lockstep now has three surfaces matched: (a) build-time CI gate (`test_baa_gated_workflows_lockstep.py`) covers all 3 workflow keys; (b) inline runtime gate (`check_baa_for_evidence_export` raises 403); (c) substrate-invariant 60s scan over the past 30 days. Defense in depth restored.

**Rule 4** ("orphan detection is sev1, not tolerable warning"): invariant severity is `sev1` (verified at `assertions.py:2452`). Extension does not weaken severity. Counsel mandate preserved.

§164.504(e) test: extension catches CE-affiliated identities (client_portal / partner_portal callers) who successfully downloaded PHI-adjacent attestation evidence for an org whose BA relationship has no current formal BAA. That is the exact class §164.504(e) anticipates; runtime backstop is now wired.

### PM — effort envelope

| Step | Estimate | Actual |
|---|---|---|
| P0 prereq + UNION ALL + guard + runbook | 2-3h | ~2h (already shipped) |
| **P0 fix Coach found** (description + recommended_action strings) | — | ~30min (2 string edits, re-run sweep) |
| Total | 2-3h | ~2.5h |

Within envelope assuming Coach P0s are fast-fixed.

---

## P0 / P1 / P2 carryover

**P0 (BLOCK pending close — Coach catch):**

1. **`assertions.py:2453` description string** still cites "(cross_org_relocate or owner_transfer)" and the stale "evidence_export is gated inline only (transient download — no durable state to assert)" sentence. MUST update to mention all three workflows + delete the contradictory sentence. Sample replacement: `"A BAA-gated sensitive workflow (cross_org_relocate, owner_transfer, OR evidence_export — Task #92) advanced/was-downloaded in the last 30 days for a client_org with no active formal BAA ... evidence_export is scanned via admin_audit_log auditor_kit_download rows from client_portal+partner_portal auth methods (admin + legacy-token branches carved out)..."`
2. **`assertions.py:3326-3327` `_DISPLAY_METADATA` recommended_action** still cites "(cross_org_relocate or owner_transfer)". MUST update to mention all three. This is the operator-facing string rendered on `/admin/substrate-health` — direct customer/operator-visibility impact.

**P1 (in same task or named follow-up — Gate A step 7):**

3. Dedicated test file `tests/test_substrate_invariant_sensitive_workflow_baa.py` (positive + negative cases for the evidence_export branch). Gate A explicitly scoped step 7 of the PM table to this; AS-IMPLEMENTED diff did not add it. Either land it now or carry as a TaskCreate'd FU. Recommend: carry, since substrate invariants in this codebase are typically tested via the live `/admin/substrate-health` shadow-run rather than pinned unit tests — but the gap should be NAMED, not silent.

**P2 (future, NOT blocking):**

4. If `admin_audit_log` grows past ~5M rows: add partial expression index `(created_at) WHERE action='auditor_kit_download'` (Gate A P2 carryover — unchanged).
5. First auditor-kit-download-after-deploy: re-query prod and confirm at least one row has the 3 new top-level keys, as fail-safe verification. Track on substrate-health dashboard rather than spinning a manual probe task.

---

## Full diff (for reviewer convenience)

`assertions.py`: +25 / -10 in `_check_sensitive_workflow_advanced_without_baa`. Docstring 3-workflow scope ✓; UNION ALL branch ✓; bypass guard ✓. **Description string at line 2453 unchanged** (P0 #1). **Display metadata at line 3326-3328 unchanged** (P0 #2).

`substrate_runbooks/sensitive_workflow_advanced_without_baa.md`: +27 / -10. 3-row workflow table ✓; carve-out subsection ✓; change-log entry ✓.

---

## Severity sanity check

Invariant `severity="sev1"` (`assertions.py:2452`). Counsel Rule 4 mandate ("orphan detection is sev1, not tolerable warning") satisfied. No mismatch to flag.

---

## Final verdict: **BLOCK** (until 2 Coach P0s close)

The implementation is design-correct, SQL-correct, schema-correct, prod-shape-verified, and full-sweep-green (261 passed). The two missing text updates are 30-minute edits — but they are explicit Gate A scope items, and they would lie about the invariant's behavior to operators reading the substrate-health dashboard + the assertion engineering description. That is the Session 220 lock-in's exact target class: "implementation matches the design diff, but the surrounding artifact has drifted."

Fix the two strings, re-run `bash .githooks/full-test-sweep.sh`, re-cite the count, and this Gate B flips to APPROVE in the same session. No re-design needed; no new prod evidence needed.

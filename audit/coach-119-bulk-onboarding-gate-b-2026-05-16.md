# Gate B — #119 fleet_cli provision-bulk-create (as-implemented in 24f24cbd)

Date: 2026-05-16
Reviewer: Gate B 7-lens fork (general-purpose subagent, fresh context)
Commit under review: **24f24cbd** — `feat(#119): fleet_cli provision-bulk-create`
Gate A reference: `audit/coach-119-bulk-onboarding-gate-a-2026-05-16.md` (APPROVE-WITH-FIXES)

## Overall verdict: **APPROVE-WITH-FIXES**

Implementation faithful to Gate A narrow scope. 3 P0 bindings all satisfied. 4 P1 bindings all satisfied. No scope creep. **One P1 finding** (input/EOFError robustness) + **two P2 polish items** — close-in-next-commit OR carry as named tasks; do not block #119.

## Test sweep (CLAUDE.md Session 220 mandatory)

`bash .githooks/full-test-sweep.sh` → **278 passed, 0 skipped** (need backend deps). Includes the new `tests/test_provision_bulk_create_cli.py` (18 sentinels — see SOURCE_LEVEL_TESTS addition in `.githooks/pre-push`). All 18 source-shape gates pass.

## Per-lens verdict

- **Steve (Principal SWE):** APPROVE — clean separation (`provision_code.py` shared module, no FastAPI in CLI), allowlist-driven loader, mirror of partner-endpoint INSERT shape, `statement_cache_size=0` consistent with cmd_create/cmd_list/cmd_cancel. Partner-exists pre-check OUTSIDE the txn — correct (fail-fast w/o holding a write txn).
- **Maya (Security/HIPAA):** APPROVE — single aggregate audit row, `provision_ids[]` JSONB array, `username=actor`, no PHI in `target` field (`partner:{uuid}` opaque), banned-actor set includes blank+system+fleet-cli+admin+operator. CSV/JSON column allowlist closes injection of `expires_days` / `partner_id` overrides.
- **Carol (CCIE/Ops):** APPROVE-WITH-FIXES — UX strong: UUID-parse validation, site_id regex pre-flight, count-confirm, dry-run preview, explicit error messages. **P1 (Carol):** `input()` at line 766 raises `EOFError` on closed stdin (CI/script pipe). Should `try/except EOFError → sys.exit("no TTY for confirmation; use --dry-run to preview")`.
- **Coach (DBA):** APPROVE — `async with conn.transaction():` wraps all INSERTs (all-or-nothing). `SET app.is_admin='true'` issued on same conn before txn. Partner-exists `fetchval` uses `$1::uuid` cast (matches CLAUDE.md asyncpg jsonb cast rule). No COUNT(*) on partitioned tables, no missing savepoints. `secrets.token_hex(8)` collision probability astronomically low; UNIQUE constraint on `provision_code` would reject a hypothetical collision and roll back the whole batch (acceptable failure mode).
- **Auditor (Counsel Rule 4):** PASS — single-txn truly closes the orphan class. No partial-success state reachable. `provision_ids[]` array in audit row gives cross-batch cardinality for reconciliation. Substrate invariant correctly NOT added (txn IS the invariant).
- **PM (scope discipline):** APPROVE — no new HTTP endpoint, no privileged-chain addition, no substrate invariant, no site pre-creation, no WG-IP pre-alloc, no email notification. Anti-scope list from Gate A fully respected (8 items verified via test sentinels).
- **Counsel (7-rule filter):** Rule 3 N/A (not privileged — confirmed by `test_no_privileged_chain_engagement`). Rule 4 closed (single-txn). Rule 6 N/A (BAA gates fire at claim, not at code creation; provision row pre-CE-binding). Rule 7 N/A (CLI-only operator surface; dry-run JSON includes actor_email but stays inside operator terminal — `print` to stdout, not log sink). PASS on all applicable rules.

## P0 binding verification (Gate A → as-implemented)

- **P0-1 (single aggregate audit row):** PASS — `cmd_provision_bulk_create` body contains exactly 1 `INSERT INTO admin_audit_log` (verified by `test_p0_1_single_aggregate_audit_row`), positioned at lines 812-826 OUTSIDE the `for entry in rows:` loop (lines 788-809). `details->>'provision_ids'` is `[r["id"] for r in results]`. Action literal `'provision_bulk_create'`.
- **P0-2 (100-cap before conn):** PASS — `if len(rows) > 100` at line 726 sys.exits BEFORE `asyncpg.connect()` at line 776. Verified by `test_p0_2_100_cap_before_conn`.
- **P0-3 (actor-email validation):** PASS — argparse `required=True` + runtime `if "@" not in actor or actor in _BANNED_ACTOR_EMAILS` at line 714. Banned set: `{"system", "fleet-cli", "admin", "operator", ""}`. `.lower()` normalization closes case-bypass.

## P1 binding verification

- **P1-1 (shared module):** PASS — `provision_code.py` exists (50 LOC, no FastAPI deps). `fleet_cli` imports via relative-then-absolute fallback (lines 707-710). `partners.py:194-197` re-exports identical fallback. Asserted by 3 sentinels.
- **P1-2 (column allowlist):** PASS — `_PROVISION_BULK_INPUT_COLUMNS = frozenset({"client_name", "target_site_id"})`. Both JSON and CSV branches subtract unknown columns and sys.exit. (Note: Gate A sketch included `expires_days` as a per-row column; as-implemented `--expires-days` is a CLI-level uniform value. This is a **scope-tightening** vs. Gate A, not a regression — narrower attack surface.)
- **P1-3 (18 source-shape sentinels):** PASS — file exists, registered in `.githooks/pre-push`, all 18 tests pass in sweep.
- **P1-4 (non-idempotency docstring):** PASS — `"NOT IDEMPOTENT"` literal in docstring at line 699-701.

## P1 findings (close-in-next-commit OR named task)

- **P1-NEW (Carol/Steve): `input()` EOFError on closed stdin.** Line 766: `typed = input(...)`. If a future operator pipes `cat batch.csv | python fleet_cli.py provision-bulk-create ...` or runs from a non-TTY context, `input()` raises uncaught `EOFError` → ugly traceback. Fix sketch:
  ```python
  try:
      typed = input(f"...").strip()
  except EOFError:
      sys.exit("confirmation requires TTY; use --dry-run to preview without inserting")
  ```
  Two-line guard. Not a security issue (failure mode is loud crash, not silent insert), so this is P1 not P0.

## P2 polish (non-blocking)

- **P2-1 (Coach): Header-less CSV silent failure mode.** `csv.DictReader` treats row 0 as headers. Operator who exports a header-less CSV from Excel gets `unknown column(s) ['Acme Clinic']` — the *value* of row 0 becomes a column name. Current error message is technically correct but operator-confusing. Polish: detect 1-column dict with no recognized keys + suggest header row. Defer to backlog.
- **P2-2 (Steve): JSON `[{}, {}]` silent acceptance.** Two empty dicts → 2 codes with NULL `client_name` and NULL `target_site_id`. Allowed by schema (both columns nullable) and arguably valid (operator may want anonymous codes). Document in `--help` text that empty rows produce unbound codes. Defer.

## Anti-scope verification (8 items from Gate A)

1. NO new HTTP endpoint — confirmed by `test_no_new_http_endpoint_for_bulk_create` (no `@router.post`, no `APIRouter`).
2. NO site pre-creation bundling — no `INSERT INTO sites` in cmd body.
3. NO privileged-chain attestation — confirmed by `test_no_privileged_chain_engagement` (no `compliance_bundles`, no `create_privileged_access_attestation`, no `attestation_bundle_id`).
4. NO new substrate invariant — confirmed by `test_no_new_substrate_invariant_added` (no `Assertion(`, no `_check_provision_bulk`).
5. NO new fleet_orders rows — verified by grep: no `INSERT INTO fleet_orders` in cmd body.
6. NO WG-IP pre-allocation — verified: no `wireguard_ip` reference in cmd body.
7. NO auto-partner-creation — confirmed: partner-exists fetchval → sys.exit if missing.
8. NO email notification on bulk-create — verified: no `_send_*_email` in cmd body, no SMTP imports added.

## Sibling-pattern consistency (Coach class)

- **#118 cmd_create uses random-nonce confirm; #119 uses count-confirm.** Gate A explicitly approved the divergence: privileged events get random-nonce (anti-muscle-memory); non-privileged bounded-N events get count-confirm. Both are first-class confirmation patterns. Not a fork — a deliberate two-tier UX. RECOMMEND adding a 1-line comment in cmd_create cross-referencing the divergence so future contributors don't homogenize the patterns.
- **`statement_cache_size=0` parity:** All 4 `asyncpg.connect()` calls in fleet_cli (lines 298, 590, 776, 842) use the kwarg consistently. PASS.
- **`SET app.is_admin='true'` parity:** cmd_create + cmd_cancel + cmd_provision_bulk_create all set this on the dedicated conn. cmd_list does NOT (read-only, not subject to fleet_orders_admin_only RLS). Consistent with pattern.

## Counsel 7-Rules deep-dive

- **Rule 1 (no non-canonical metric):** N/A — no metrics emitted.
- **Rule 2 (no raw PHI):** PASS — `client_name` is a partner-supplied label (e.g. "Acme Clinic"), not PHI per HHS guidance (clinic name is not individually-identifiable health info). No PHI fields written. CSV input is operator-controlled, not patient-data.
- **Rule 3 (privileged chain):** N/A — pre-CE-binding event class, no privileged registration.
- **Rule 4 (orphan coverage):** PASS — all-or-nothing single-txn structural close.
- **Rule 5 (no stale doc):** N/A — no doc artifacts produced.
- **Rule 6 (BAA in memory):** N/A — BAA gates fire at claim → site → first-evidence, all covered by `BAA_GATED_WORKFLOWS`.
- **Rule 7 (no unauth context):** PASS — CLI-only, no email/webhook/SMS surface. Dry-run JSON includes `actor_email` but goes to operator terminal stdout, not a customer-facing channel.

## Final verdict

**APPROVE-WITH-FIXES.** Implementation faithful to Gate A narrow scope; all P0 + P1 bindings satisfied; anti-scope respected; test sweep green (278 passed). One P1 (EOFError on closed stdin) + two P2 polish items — close in the next commit OR open named TaskCreate items. **Cite both gate verdicts in the next commit body** per CLAUDE.md TWO-GATE protocol.

Path: `audit/coach-119-bulk-onboarding-gate-b-2026-05-16.md`

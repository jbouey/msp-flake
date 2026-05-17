# Gate A — #119 bulk-onboarding primitive (multi-device P1-3)
Date: 2026-05-16
Reviewer: fork-based 7-lens (general-purpose subagent, fresh context)
Verdict: **APPROVE-WITH-FIXES** (scope narrowing required — premise partially inaccurate)

## Premise re-check (must read first)

The task brief says "no batch primitive exists." **This is wrong as stated.** `partners.py:2890 POST /me/provisions/bulk` already implements the all-or-nothing bulk-provision-code primitive (max 100 entries, single `admin_connection` txn, `ProvisionBulkCreate` Pydantic model with `entries: List[ProvisionBulkEntry]`, partner-activity audit row with `target_type='provision_bulk'`). The actual gaps for a 20-appliance partner onboarding flow are:

1. **No operator-side equivalent.** Partner-portal API exists; `fleet_cli` has no `provision` subcommand at all. An operator onboarding a partner the partner-admin hasn't yet self-served must do it through the DB or per-appliance UI.
2. **No CSV ingestion.** UI only — operators with a 20-row spreadsheet copy-paste.
3. **No post-batch claim visibility.** After bulk-create, operator must `GET /me/provisions?status=pending` and reconcile by eye.
4. **Site pre-creation not bundled.** `target_site_id` is optional — if omitted, the site_id is generated at `/api/provision/claim` time (provisioning.py:230-237). Operators who WANT a chosen site_id today must pre-`INSERT INTO sites` separately.

## Recommended design (narrow scope)

**Surface:** `fleet_cli provision bulk-create` subcommand only. Do NOT add a new HTTP endpoint — partner-side `/me/provisions/bulk` already exists; admin-CLI hits the DB directly via the same path as `cmd_create` (no PgBouncer, dedicated asyncpg conn, `SET app.is_admin='true'`). One code path, one audit shape.

**Sketch (under 80 LOC):**
```python
async def cmd_provision_bulk_create(args):
    rows = _load_input(args.input)  # CSV or JSON; columns: client_name, target_site_id (opt), expires_days (opt)
    if len(rows) > 100: sys.exit("max 100 per batch (matches partner endpoint cap)")
    if not args.partner_id: sys.exit("--partner-id required")
    # Pre-flight validation BEFORE any INSERT
    for i, r in enumerate(rows):
        if r.get("target_site_id") and not _SITE_ID_RE.match(r["target_site_id"]):
            sys.exit(f"row {i}: target_site_id must match [a-z0-9-]+")
    if args.dry_run:
        print(json.dumps([{"row": i, **r} for i, r in enumerate(rows)], indent=2, sort_keys=True))
        return
    typed = input(f"This will create {len(rows)} provision codes for partner {args.partner_id}. Type the number {len(rows)} to confirm: ")
    if typed.strip() != str(len(rows)): sys.exit("confirmation mismatch")
    conn = await asyncpg.connect(DATABASE_URL); await conn.execute("SET app.is_admin='true'")
    async with conn.transaction():  # all-or-nothing (mirror partner endpoint)
        # Verify partner exists ONCE
        if not await conn.fetchval("SELECT 1 FROM partners WHERE id=$1::uuid", args.partner_id):
            sys.exit(f"partner {args.partner_id} not found")
        out = []
        for r in rows:
            code = _generate_provision_code()  # extract from partners.py to shared module
            row = await conn.fetchrow("""INSERT INTO appliance_provisions
                (partner_id, provision_code, target_site_id, client_name, expires_at)
                VALUES ($1::uuid, $2, $3, $4, NOW() + ($5::int || ' days')::interval)
                RETURNING id, provision_code, created_at""",
                args.partner_id, code, r.get("target_site_id"), r.get("client_name"),
                r.get("expires_days", 30))
            out.append({"id": str(row["id"]), "code": row["provision_code"], "client": r.get("client_name")})
        # Single audit row (cross-batch cardinality), not N
        await conn.execute("""INSERT INTO admin_audit_log
            (username, action, target, details, ip_address, created_at)
            VALUES ($1, 'provision_bulk_create', $2, $3::jsonb, NULL, NOW())""",
            args.actor_email, f"partner:{args.partner_id}",
            json.dumps({"count": len(out), "provision_ids": [o["id"] for o in out]}))
    print(json.dumps({"count": len(out), "provisions": out}, indent=2, sort_keys=True))
```

## Per-lens verdict (7 lines)

- **Steve (Principal SWE):** APPROVE — narrow CLI wrapper, reuses existing endpoint's all-or-nothing txn semantics. Reject scope creep (no new HTTP endpoint, no site pre-creation, no auto-WG-IP).
- **Maya (Counsel/Auditor):** APPROVE-WITH-FIXES — single audit row with `provision_ids[]` array, NOT N rows; opaque-mode subject (no client_name in audit `target`). `--actor-email` mandatory (named human, not "system"/"fleet-cli" — privileged-chain naming convention).
- **Carol (Security):** APPROVE-WITH-FIXES — bulk-create is NOT a privileged order type (no chain custody required; `appliance_provisions` is pre-claim, no PHI/identity yet). HOWEVER: 100-cap MUST be enforced server-side too; `--partner-id` MUST be UUID-parse-validated; CSV parser MUST refuse any column not in the allowlist (defense vs CSV injection of `expires_days=99999`).
- **Coach (consistency):** APPROVE-WITH-FIXES — count-confirm prompt mirrors #118's pattern (dynamic N, not constant). Audit `target_type` differs across surfaces (partner uses `provision_bulk`, CLI should use `provision_bulk_create` — pick ONE before shipping).
- **Auditor (HIPAA §164.528):** PASS — pre-claim provision codes are not yet bound to a CE; no §164.528 disclosure-accounting impact until claim.
- **PM (operator ergonomics):** APPROVE — closes the 20-appliance fatigue gap; CSV input matches the spreadsheet operators already maintain.
- **Counsel 7-Rules:** Rule 3 N/A (not privileged); Rule 4 covered by all-or-nothing txn (no partial-success orphans); Rule 7 N/A (operator-only surface, no unauth channel); Rule 6 N/A (BAA-gated workflow only fires at claim+post, not at code creation).

## Counsel's 7 Hard Rules deep-dive

- **Rule 3 (privileged chain):** This is NOT a privileged order type. `appliance_provisions` row creation does not mutate customer evidence or compliance state — it issues a single-use code consumable by a future `/api/provision/claim`. NO addition to `PRIVILEGED_ORDER_TYPES` / `ALLOWED_EVENTS` / `v_privileged_types`. PASS.
- **Rule 4 (orphan coverage):** All-or-nothing single-txn INSERT closes the partial-success class structurally. No "5-of-20 succeeded silently" orphan. Substrate invariant NOT required (the txn IS the invariant). PASS.
- **Rule 7 (no unauth context):** CLI-only surface inside the container; CSV input is operator-supplied. Dry-run output stays inside the operator's terminal. PASS.
- **Rule 6 (BAA in memory):** Provision-code creation is pre-CE-binding; no BAA gate required at this step (BAA enforcement fires at claim → site-creation → first-evidence-bundle path, which is already covered by `BAA_GATED_WORKFLOWS`). PASS.

## P0 bindings (BLOCK)

- **P0-1: Single aggregate audit row, NOT N rows.** Partner endpoint's `log_partner_activity(target_id=f"bulk:{len(results)}")` precedent is correct. The CLI MUST mirror: ONE `admin_audit_log` row per `bulk_create` invocation with `details->>'provision_ids'` as a JSONB array. N rows would (a) explode the audit log, (b) lose the cross-batch cardinality, (c) cost a quadratic substrate-invariant scan.
- **P0-2: 100-entry hard cap on the CLI side AND a server-side ceiling.** The partner endpoint caps at 100. The CLI must cap at 100 (matching). Plus a defensive sentinel — if `len(rows) > 100` reaches the INSERT loop, sys.exit BEFORE opening the conn. Operator with a 500-row CSV must split intentionally, not silently DoS the audit chain.
- **P0-3: `--actor-email` mandatory, validated as email shape.** The privileged-chain naming convention (`fleet_cli.py` REQUIRED for privileged order types) doesn't apply here, BUT Maya Rule on audit-actor naming (NEVER `system`/`fleet-cli`/`admin`) DOES. CLI MUST reject blank/`fleet-cli`/`system` values for `--actor-email`.

## P1 bindings (close-in-commit OR named task)

- **P1-1: Extract `generate_provision_code()` to a shared module.** Currently lives in partners.py only. CLI importing from `partners` drags FastAPI + routing into the CLI. Move to `provision_code.py` or similar. Otherwise CLI inlines its own generator and the two drift over time (Coach class).
- **P1-2: CSV parser allowlist.** Use `csv.DictReader` + filter to `{client_name, target_site_id, expires_days}`. Reject unknown columns with `sys.exit(f"unknown column: {col!r}")`. Closes CSV injection of arbitrary kwargs.
- **P1-3: CI gate `test_provision_bulk_create_single_audit_row`.** AST-walk over `cmd_provision_bulk_create` to verify exactly ONE `admin_audit_log INSERT` outside the `for r in rows:` loop body. Mirror `test_fan_out_uses_single_attestation_bundle` from #118.
- **P1-4: Idempotency note in docstring + verify behavior.** `provision_code` has a `UNIQUE` constraint (verify in `prod_unique_indexes.json`). Re-running the same script twice with the SAME generator seed would collide; `_generate_provision_code()` uses `secrets.token_hex()` so collisions are astronomically unlikely BUT the docstring must state: "each run produces fresh codes; this is NOT idempotent against the same input file. Use `--dry-run` first."

## P2 considerations (non-blocking)

- **P2-1: No site pre-creation.** Operators wanting a chosen `site_id` still pre-INSERT into sites. Keep this OUT of #119 — bundling site-create + provision-code into one CLI invocation widens blast radius AND crosses the partner/operator audit boundary. Separate task if demand surfaces.
- **P2-2: No auto-WG-IP allocation.** WireGuard IP is allocated at claim time (provisioning.py:127). Pre-allocating per row would create N unused WG slots if the codes are never claimed. Leave as-is.
- **P2-3: Status-reconciliation subcommand (`fleet_cli provision status --partner-id X`)** — separate sibling task, NOT in #119 scope. File as #120 followup if not already tracked.

## Anti-scope (what NOT to add)

1. NO new HTTP endpoint — partner endpoint already covers the API path.
2. NO site pre-creation bundling (P2-1).
3. NO privileged-chain attestation (this is NOT a privileged event class).
4. NO new substrate invariant (single-txn semantics make orphans impossible).
5. NO new fleet_orders rows (provision codes are NOT fleet orders).
6. NO WG-IP pre-allocation (P2-2).
7. NO auto-partner-creation if `--partner-id` doesn't exist — exit with clear error.
8. NO email notification to client_contact_email on bulk-create (partner_signup already handles client-onboarding email; duplicating here is opaque-mode-email-parity violation).

## Migration claim

**NO migration required.** `appliance_provisions` schema is sufficient (mig 003 + mig 296 already shipped). No new columns, no new indexes, no new trigger. The 100-cap is application-level only. If a future iteration ever wanted a DB CHECK on `LENGTH(provision_code)` or similar it would claim a number THEN — for #119, skip the ledger entry.

(Note: ledger currently shows 317-318 reserved; mig 325 is the latest shipped on disk; #129 is in flight on 325 per task brief. If a migration WERE needed it would claim **326** — but this design has no SQL.)

## CI gates required (ship in same commit)

1. `test_provision_bulk_create_single_audit_row` (P1-3 above) — AST walk over cmd_provision_bulk_create.
2. `test_provision_bulk_csv_allowlist` — source-walk that DictReader output is filtered through a hardcoded set.
3. `test_provision_bulk_actor_email_required` — sys.exit path is hit when `--actor-email` is blank/`system`/`fleet-cli`.
4. `test_provision_bulk_100_cap` — sys.exit path is hit when input file has >100 rows.
5. Reuse `test_no_unfiltered_site_appliances_select` baseline — this design does NOT query site_appliances (good); confirm no regression.

## Final verdict

**APPROVE-WITH-FIXES.** The brief overstated the gap; narrow the scope to a CLI wrapper around the existing partner-endpoint semantics. 3 P0s (single-audit-row, server-side cap, actor-email validation) MUST close before merge. P1s close-in-commit OR carried as named task. Gate B MUST run full pre-push sweep, not diff-only.

Path: `audit/coach-119-bulk-onboarding-gate-a-2026-05-16.md`

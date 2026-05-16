# Gate A — #118 fleet_cli --target-appliance-id + --all-at-site
Date: 2026-05-16
Reviewer: fork-based 7-lens (general-purpose subagent, fresh context)
Verdict: APPROVE-WITH-FIXES

## A. Privileged-chain compatibility

**1-bundle:N-orders cardinality verdict: WORKS AS-IS, no migration needed.**

mig 175 `enforce_privileged_order_attestation()` (and re-emitted body in mig 218 + 305) is a `BEFORE INSERT ... FOR EACH ROW` trigger. The check is `SELECT EXISTS (... WHERE bundle_id = v_bundle_id AND site_id = v_site_id AND check_type='privileged_access')`. There is NO uniqueness constraint on `attestation_bundle_id` either in `fleet_orders` or in the trigger. The supporting `idx_compliance_bundles_priv` is on `compliance_bundles (bundle_id, site_id) WHERE check_type='privileged_access'` — partial, but bundle_id is the natural unique key on compliance_bundles, NOT on fleet_orders.

Conclusion: N fleet_orders rows can cite the SAME `attestation_bundle_id`. Each row's trigger fires independently, each EXISTS probe succeeds against the same bundle.

mig 176 (UPDATE guard) also looks at parameters fields not unique-keys; no impact.

**Sibling precedent:** `_kill_switch_per_site_attestation` (`main.py:3432`) takes the opposite design — N sites → N bundles, one per site. That choice is forced by `compliance_bundles.site_id` requiring a real site anchor and the per-site evidence chain semantics. For `--all-at-site` the target IS a single site (N appliances at the SAME site), so the inverse argument applies: ONE bundle covers all N orders cleanly, no FK contortion.

**Required design change:** NONE for the trigger. BUT see Finding P1-1 — `_get_prev_bundle()` requires `conn.is_in_transaction()` (assert at line 340) and takes a `pg_advisory_xact_lock`, so the bundle write must be inside an `admin_transaction`. The N-order INSERTs that follow can be batched inside the SAME txn for atomicity (all-or-nothing semantics — see P0-1 below).

**`create_privileged_access_attestation()`** returns `{bundle_id, bundle_hash, chain_position, chain_hash, signature}` — a value object, not a connection-scoped resource. Reusing `bundle_id` across N subsequent fleet_orders INSERTs is safe.

## B. fleet_cli wiring

**--target-appliance-id sketch:**
```python
p_create.add_argument("--target-appliance-id", help="UUID — validates against site_appliances.appliance_id (active rows only)")
# in cmd_create, after parse_params:
if args.target_appliance_id:
    if "target_appliance_id" in params:
        sys.exit("--target-appliance-id and --param target_appliance_id=... are mutually exclusive")
    try: uuid.UUID(args.target_appliance_id)
    except ValueError: sys.exit("--target-appliance-id must be UUID")
    # Validate against site_appliances WHERE deleted_at IS NULL
    row = await conn.fetchrow(
        "SELECT site_id, hostname, status FROM site_appliances "
        "WHERE appliance_id = $1::uuid AND deleted_at IS NULL", args.target_appliance_id)
    if not row: sys.exit(f"appliance {args.target_appliance_id} not found or soft-deleted")
    params["target_appliance_id"] = args.target_appliance_id
    if "site_id" not in params: params["site_id"] = row["site_id"]
    elif params["site_id"] != row["site_id"]:
        sys.exit(f"--param site_id mismatches appliance's actual site")
```

**--all-at-site sketch:**
```python
p_create.add_argument("--all-at-site", help="SITE_ID — fan out to every active appliance at this site")
p_create.add_argument("--confirm", action="store_true", help="Required for --all-at-site on privileged order types")
p_create.add_argument("--dry-run", action="store_true")

# after attestation, BEFORE the existing single-row INSERT:
if args.all_at_site:
    targets = await conn.fetch(
        "SELECT appliance_id, site_id, hostname, status, mac_address, last_checkin "
        "FROM site_appliances "
        "WHERE site_id = $1 AND deleted_at IS NULL "
        "ORDER BY hostname",
        args.all_at_site)
    if not targets:
        sys.exit(f"no active appliances at site {args.all_at_site!r}")
    n = len(targets)
    if args.dry_run:
        print(json.dumps([{
            "appliance_id": str(r["appliance_id"]),
            "site_id": r["site_id"],
            "hostname": r["hostname"],
            "status": r["status"],
            "mac": r["mac_address"],  # P0-3: see Counsel Rule 7
            "last_checkin": r["last_checkin"].isoformat() if r["last_checkin"] else None,
        } for r in targets], indent=2))
        return
    if order_type in PRIVILEGED_ORDER_TYPES:
        if not args.confirm:
            sys.exit("--all-at-site on a privileged order type requires --confirm")
        typed = input(f"This will fan out {n} {order_type} orders. Type the number {n} to confirm: ")
        if typed.strip() != str(n):
            sys.exit("confirmation mismatch — refusing")
    # ONE attestation bundle for the whole fan-out (already created above).
    # Loop INSERT N fleet_orders, all citing the same attestation_bundle_id.
    async with conn.transaction():
        for t in targets:
            per_params = dict(params)  # shallow-copy
            per_params["target_appliance_id"] = str(t["appliance_id"])
            nonce_i, sig_i, payload_i = sign_order(signing_key, order_type, per_params, now, expires_at)
            await conn.execute("INSERT INTO fleet_orders ...", ...)
```

**--dry-run output format:** newline-pretty JSON array, fields as above. Sortable by hostname for deterministic diffs. NEVER include daemon health, IP addresses, evidence rejection counts (operator-only telemetry NOT needed for the dry-run decision — keep narrow).

## C. Sibling-surface parity

**No partner-facing bulk-action sibling exists today.** `partners.py` has `/me/provisions/bulk` (PROVISION CODE creation — not fleet_orders) and `bulk_drift_config` (org-level config write — not fleet_orders). Neither writes privileged fleet_orders.

`privileged_access_api.py` is the partner-portal sibling for SINGLE privileged-order requests with client-approval workflow. It does NOT have a bulk path. Out-of-scope for this Gate A — but flag for #124 follow-up (`--all-at-partner`): if/when partner-portal grows a bulk surface, it should use the SAME 1-bundle-N-orders shape so the API and CLI codepaths stay coherent.

**No conflict with `require_partner_role` gates** — fleet_cli is admin-only (direct DB conn, runs inside mcp-server container).

## D. Counsel's 7 Rules

**Rule 3 (privileged chain):** NO new entry needed in the three lockstep lists. `--all-at-site` is a CLI fan-out for an EXISTING privileged order type, not a new event class. The N fleet_orders rows are all of an already-registered `order_type`. PASS.

**Rule 4 (orphan coverage):** Three concerns —
- Offline appliances: `--all-at-site` enumerates `WHERE deleted_at IS NULL`. An appliance that is "active but offline" (last_checkin > 24h) IS included — the order queues; it will be served when the appliance returns. The substrate has `fleet_order_url_resolvable` invariant (assertions.py:2367) for URL-class orphans, but no "fleet_order issued via fan-out but unacknowledged after N hours" invariant. This is a P2 — single-order fan-out doesn't materially change the orphan-tracking story, but at scale (20 appliances × weekly cadence) operators need visibility. Recommend filing follow-up task to add `fan_out_completion_gap` invariant. NON-BLOCKING.
- Dry-run output must show last_checkin so operator sees offline targets BEFORE confirming. Sketch above covers this.
- Soft-deleted appliances correctly excluded. PASS.

**Rule 7 (no unauth context):** `--dry-run` output is operator-only (CLI runs inside container, requires shell access). Hostname + status + last_checkin are operator-level telemetry. mac_address is on the boundary — Carol's RT33 P2 vetoed `mac/ip/daemon_health` from the customer-facing portal `/api/client/appliances` endpoint, but this is operator-CLI (not customer-facing). **Recommendation: keep mac in dry-run** (operator needs it for cross-reference with physical inventory) but **OMIT ip_addresses** (Layer-2 leak risk if dry-run output is ever pasted into customer support tickets). PASS-with-narrowing.

## E. PG test fixture impact

Verified `prod_columns.json` line 4714-4775: `site_appliances` includes `deleted_at`, `deleted_by`, `appliance_id`, `site_id`, `hostname`, `status`, `mac_address`, `last_checkin`. Every column the sketch SELECTs is in the prod fixture. NO new PREREQ_SCHEMA needed.

`appliance_id` is in the `{uuid, text}` ambiguous-class — the new `WHERE appliance_id = $1::uuid` cast in --target-appliance-id validation will trigger `test_no_uuid_cast_on_text_column.py`. Confirm the column is uuid-typed in `prod_column_types.json` before shipping the cast, OR use `WHERE appliance_id::text = $1` instead. **P1-2.**

## F. CI gate hardening required

1. **`test_fan_out_uses_single_attestation_bundle`** — AST/source-walk gate that any `--all-at-site` code path creates EXACTLY ONE attestation call before the INSERT loop (NOT N attestations inside the loop, which would defeat the point + violate the chain-position semantics). Pin via `ast.walk` looking for `create_privileged_access_attestation` calls outside `for ... in targets:` body.
2. **`test_all_at_site_filters_soft_deletes`** — source-walk that the enumeration query contains `deleted_at IS NULL` on the same line as `FROM site_appliances`. Same shape as `test_client_portal_filters_soft_deletes.py`.
3. **`test_count_confirm_uses_dynamic_n`** — source-walk that the `input()` prompt format-string interpolates `n` (NOT a constant). Catches the regression where future refactor accidentally hardcodes the prompt.
4. **`test_dry_run_excludes_ip_addresses`** — output-shape gate that the dry-run dict literal does NOT include `ip_addresses`, `daemon_health`, or `agent_public_key` keys. Closes Carol's Layer-2-leak class for CLI surface.

## G. Pre-existing class re-check

- **admin_transaction multi-query:** Not applicable — fleet_cli opens its own dedicated `asyncpg.connect(...)` (line 270) and does `SET app.is_admin = 'true'` (line 281). Not going through PgBouncer pool. The bundle write + N INSERTs CAN share the connection and SHOULD be inside `async with conn.transaction():` for atomicity (P0-1).
- **CONCURRENTLY INDEX:** N/A — no schema change.
- **jsonb_build_object unannotated params:** Existing code at line 370-373 already does `jsonb_build_object('fleet_order_id', $1::text)` with explicit cast. New code must follow the same pattern. The N-loop UPDATE-stamp pattern needs to be reviewed if cross-link is preserved per-order (NOT just for the first order). **P1-3.**
- **COUNT(*) on large tables:** `count_recent_privileged_events` uses `COUNT(*)` on `compliance_bundles` filtered by `site_id` + `check_type='privileged_access'` + 7d window. This is small per-site (≤3/week target). PASS.

## Findings

### P0 (BLOCK)

- **P0-1: Atomic transaction for the fan-out.** The bundle write + N INSERTs MUST be inside a single `async with conn.transaction():` block. Without it, a partial-success leaves the bundle written but only K-of-N orders inserted — operationally fine (the trigger ensures each individual order has chain custody) but auditor-confusing ("bundle says N events, only K orders found"). The bundle's `summary` payload currently encodes `"count": 1` — for fan-out it MUST encode `"count": N` and `"target_appliance_ids": [...]`. Extend `create_privileged_access_attestation()` with optional `fan_out_targets: List[str]` kwarg OR add a new `create_fan_out_attestation()` sibling. Atomicity-first: if any single INSERT fails the trigger, ROLLBACK the bundle too. NOT BLOCKING SHIP if this is documented as "first INSERT may roll back the bundle write" — but the summary-count divergence IS blocking.

### P1 (MUST-fix-or-task)

- **P1-1: bundle attestation requires explicit transaction.** `_get_prev_bundle()` asserts `conn.is_in_transaction()`. The current fleet_cli `cmd_create` does NOT wrap in a transaction (just connects, sets app.is_admin, then calls `create_privileged_access_attestation`). This is a LATENT BUG IN MAIN already — the assert would fire on every privileged-order CLI invocation. Verify this works in prod (maybe `is_in_transaction()` returns True for any non-autocommit asyncpg conn?), and if not, the wrapping fix is needed for both single-target and fan-out paths. **Investigate before shipping — may be a pre-existing P0 in main hiding behind asyncpg default behavior.**
- **P1-2: `appliance_id = $1::uuid` cast risks `test_no_uuid_cast_on_text_column.py`.** Check `prod_column_types.json` for `site_appliances.appliance_id` type. If text, use `appliance_id::text = $1` instead. If uuid, confirm with the gate's allowlist.
- **P1-3: per-order audit cross-link in the fan-out.** The existing `UPDATE admin_audit_log SET details = details || jsonb_build_object('fleet_order_id', $1::text)` at line 370 matches by `details->>'bundle_id'`. With N orders sharing ONE bundle, this UPDATE will match the single admin_audit_log row N times (overwriting on each iteration). Either change the cross-link semantics (use array append: `jsonb_set(details, '{fleet_order_ids}', ...)`) OR aggregate all N order IDs into a single UPDATE after the loop. Either way the existing one-line UPDATE is wrong for fan-out.

### P2 (consider)

- **P2-1: `fan_out_completion_gap` substrate invariant.** Add follow-up task — sev3 invariant that fires when a fan-out bundle's `target_appliance_ids` are not all completed within 6h. Closes the "issued but never ack'd" orphan class at fan-out scale.
- **P2-2: rate-limit accounting.** `count_recent_privileged_events` counts BUNDLES, not orders. The 3/site/week cap is correct (1 bundle = 1 privileged event regardless of fan-out N). Good. Document this in the bundle-summary docstring so future engineers don't "fix" it.
- **P2-3: --dry-run output stability.** Sort `targets` by hostname (sketch already does). Cite this in docstring so consumers can diff outputs across CLI runs.

## Implementation binding requirements

1. Single attestation bundle for the entire fan-out. Bundle summary MUST encode `count = N` and `target_appliance_ids = [...]`. Extend `create_privileged_access_attestation` with `fan_out_targets` kwarg.
2. Bundle write + N order INSERTs wrapped in a single `async with conn.transaction():` block. All-or-nothing semantics.
3. Enumeration query: `SELECT ... FROM site_appliances WHERE site_id = $1 AND deleted_at IS NULL ORDER BY hostname`. Soft-delete filter on the SAME line as `FROM` per `test_client_portal_filters_soft_deletes.py` convention.
4. Count-confirm: random-number-back via stdin `input()`. Constant string would trip CI gate F-3.
5. --dry-run output: JSON array, sorted by hostname, fields `{appliance_id, site_id, hostname, status, mac, last_checkin}`. EXCLUDE `ip_addresses`, `daemon_health`, `agent_public_key`, `auth_failure_*`. F-4 gate enforces.
6. `--target-appliance-id` validation: UUID-parse + soft-delete check. If `--param site_id=...` also provided, MUST match the appliance's actual site_id; mismatch = exit.
7. Cross-link UPDATE at line 370 reshape: either array-append OR single post-loop UPDATE. The N-overwrite bug is real.
8. Investigate P1-1 (assert in `_get_prev_bundle()` — is fleet_cli currently broken in prod?) BEFORE adding new callsites; if broken, fix that FIRST in a separate commit.
9. No three-list lockstep change. `--all-at-site` is a CLI ergonomic, not a new event class.
10. New CI gates (F-1 through F-4 above) ship in the SAME commit as the feature.
11. Confirm `appliance_id` column type in `prod_column_types.json` before using `::uuid` cast (P1-2).

## Final
APPROVE-WITH-FIXES

Path: `audit/coach-118-fleet-cli-multi-target-gate-a-2026-05-16.md`

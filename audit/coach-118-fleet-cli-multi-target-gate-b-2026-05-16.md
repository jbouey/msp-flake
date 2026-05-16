# Gate B verdict — #118 fleet_cli multi-target (e0204e40)
Date: 2026-05-16
Reviewer: fork-based 7-lens (general-purpose subagent, fresh context)
Verdict: APPROVE-WITH-FIXES

## P0 closure verification (per Gate A bindings)

1. **Atomic txn wrap + latent-bug closure** — VERIFIED.
   `fleet_cli.py:419` opens `async with conn.transaction():`. The privileged-attestation `create_privileged_access_attestation(...)` call at L453 is INSIDE the block; the iter_targets fan-out loop (L497) is INSIDE; the audit-cross-link UPDATE at L527-535 is INSIDE. Block exits cleanly at L543. `_get_prev_bundle`'s `assert conn.is_in_transaction()` (L340 of privileged_access_attestation.py) now passes. Latent bug since 2026-05-09 closed.

2. **Single-bundle-per-fan-out** — VERIFIED.
   Exactly ONE `create_privileged_access_attestation(` call in `cmd_create` (L453). Sits BEFORE the `for per_target in iter_targets:` loop (L497). The 9th CI gate `test_single_bundle_per_fan_out` enforces both invariants via call-count + positional check.

3. **Cross-link aggregate `fleet_order_ids` (jsonb array)** — VERIFIED.
   L530-535: `UPDATE admin_audit_log SET details = details || jsonb_build_object('fleet_order_ids', $1::jsonb)` with `json.dumps([str(r["id"]) for r in fleet_order_rows])`. N=1 case yields `["uuid"]` array. CI gate `test_cross_link_uses_aggregate_array_not_singular` enforces. The earlier write inside `privileged_access_attestation.py:540` still includes singular `fleet_order_id: None` — harmless coexistence; no downstream reader keys on the singular form (grep confirms no `details->>'fleet_order_id'` anywhere in repo).

4. **Count-confirm dynamic-N** — VERIFIED.
   L387 binds `N = len(target_appliance_ids)`. L388-396 prompts using `{N}`. L402 checks `confirm != str(N)`. CI gate `test_dynamic_n_confirm_prompt` enforces both the dynamic binding + dynamic compare.

5. **Soft-delete filter** — VERIFIED.
   L316-326 enumeration: `WHERE site_id = $1 AND deleted_at IS NULL ORDER BY appliance_id`. CI gate `test_soft_delete_filter_on_site_enumeration` enforces.

6. **Counsel Rule 7 dry-run allowlist** — VERIFIED.
   L332-344 projects only `appliance_id, site_id, mac, hostname, status, last_checkin`. Excluded: `ip_addresses`, `daemon_health`, `agent_public_key`. CI gate `test_dry_run_field_allowlist` enforces (explicit forbidden-list + mandatory `mac` keep).

7. **UUID validation upfront on `--target-appliance-id`** — VERIFIED.
   L246-252: `_uuid_mod.UUID(args.target_appliance_id)` with `sys.exit` on `ValueError`/`AttributeError`. CI gate `test_target_appliance_id_validates_as_uuid` enforces.

8. **Mutual exclusion** — VERIFIED.
   L239-243: sys.exit when both args provided. CI gate `test_target_appliance_id_and_all_at_site_mutually_exclusive` enforces.

All 8 Gate A P0 closures verified in the as-implemented artifact.

## Cross-cutting concerns

- **A (summary_payload back-compat):** SAFE. New kwarg `target_appliance_ids: Optional[List[str]] = None` is additive with `None` default. Verified all 18 existing callers (privileged_access_api, partner_*, client_*, breakglass, chain_attestation, org_management, appliance_relocation, etc.) — none pass `target_appliance_ids`. Default branch in `privileged_access_attestation.py:451-456` yields `count: 1` + no `target_appliance_ids` key in summary, identical to pre-#118 shape. Bundle hash for existing callers is byte-identical. CI gate pins `default is None`.

- **B (sign per-iteration):** VERIFIED. L501-503: `sign_order(...)` runs inside the loop body, once per `per_target`. Each iteration mutates `per_params["target_appliance_id"]` (L500) then re-signs. Distinct signatures per appliance. `params["attestation_bundle_id"]` is set at L483 BEFORE the loop (so every per_params copy carries it via `dict(params)` at L498) — mig 175 trigger satisfied for every INSERT.

- **C (race conditions):** ACCEPTABLE. Per-site advisory lock (`pg_advisory_xact_lock(hashtext($1), hashtext('attest'))` in `_get_prev_bundle`) serializes concurrent `--all-at-site` invocations on the same site. 20 parallel `--target-appliance-id` calls at the same site also serialize. Cross-site contention is unaffected. No deadlock risk: lock is acquired once at the start of the transaction with a stable hashtext-pair ordering.

- **D (error paths):** VERIFIED.
   - Attestation write fails → `PrivilegedAccessAttestationError` → `sys.exit` at L473-477. SystemExit raises through `async with conn.transaction()` → txn rolls back → no fleet_orders. ✓
   - Mid-loop FK violation on a fleet_order INSERT → exception propagates out of `async with conn.transaction()` → all prior K successful INSERTs in this txn ROLLBACK. The attestation bundle (written earlier in the same txn) also rolls back. Atomic. ✓
   - Rate-limit hit (L444-451) → `sys.exit` BEFORE the attestation INSERT runs (inside txn but pre-attestation), so txn rolls back the SET app.is_admin only; no bundle, no orders. ✓

- **E (CI gate completeness):** 9 gates pin 9 structural contracts comprehensively. Coverage gap: there is NO behavioral integration test (real PG) exercising the actual fan-out — only source-shape assertions. Recommended as a P1 follow-up to add a `tests/test_fleet_cli_multi_target_pg.py` that drives `cmd_create` with `--all-at-site` against a seeded test schema. The 4 listed gates + 5 sentinels are sufficient for ship, but the integration smoke would catch SQL-shape drift (e.g., column rename in `site_appliances`) that the AST gates miss.

- **F (Counsel's 7 Rules):**
   - Rule 3 (privileged chain): unchanged. No new event_type. 3-list lockstep unaffected. ✓
   - Rule 4 (orphan coverage): partial gap. Offline appliances at the site receive a fleet_order row but never ack it. `fleet_order_url_resolvable` substrate invariant only covers `update_daemon`. For privileged fan-out, the operator sees pending orders via `fleet_cli list` — visible but not sev1-alerted. P2 follow-up to add a `privileged_fan_out_orphan_completion` invariant.
   - Rule 7 (no unauth context): dry-run output is operator-CLI. The field allowlist correctly drops ip/daemon_health/agent_public_key. `mac` kept (operator inventory need). `last_checkin` ISO timestamp is fine. ✓

- **G (Carol UX — random-N vs N prompt):** ACKNOWLEDGED-NOT-FIXED. Gate A spec said "random-N defeats muscle-memory"; the implementation uses the actual count `N`. The CI gate comment even acknowledges "operator typing the same number repeatedly builds muscle memory". For 20-appliance sites N is stable; operator running daily fan-outs at the same site type "20" repeatedly. P1: replace with either (a) a 4-char random nonce printed at top of prompt that the operator must echo, or (b) site_id-tail-N echo (`type 20-northvalley to confirm`). Suggest issue followup.

- **H (Maya — admin_audit_log singular reader + kit determinism):**
   - Singular-key readers: grep confirms ZERO live readers of `details->>'fleet_order_id'` anywhere in the backend / frontend / SQL / migrations. The MoveApplianceModal frontend reference is a different `fleet_order_id` (response-body field of a different API). The original `fleet_order_id: None` write in privileged_access_attestation.py:540 still happens (singular key persists in details, value=null). Cross-link UPDATE adds `fleet_order_ids` (plural). Both keys coexist; no dashboard/report breaks. ✓
   - Kit determinism: summary_payload now sorts `target_appliance_ids` (L459 `sorted(target_appliance_ids)`); `_canonical` uses `sort_keys=True, separators=(",",":")`. Bundle hash is byte-deterministic for given input. Two consecutive downloads with no chain progression remain byte-identical. ✓

## Test sweep
`bash .githooks/full-test-sweep.sh` → **274 passed, 0 skipped**. Clean.

## Per-lens findings

- **Steve (architecture/SRE):** APPROVE. Per-site advisory lock + per-iteration signing + atomic txn is the right shape. Latent-bug fix is real value.
- **Maya (audit/HIPAA/DB):** APPROVE-WITH-FIXES. Cross-link array shape correct; bundle determinism preserved. Singular-key audit-log coexistence is benign. Suggest follow-up: drop the now-meaningless `"fleet_order_id": None` write in `privileged_access_attestation.py:540` when the caller is fleet_cli (or just always — none of the 18 callers pass it through to a meaningful value at audit-write time).
- **Carol (UX/operator):** APPROVE-WITH-FIXES. Count-prompt friction inadequate at scale — see (G). Layer-2 leak fix is excellent.
- **Coach (process/lock-in):** APPROVE-WITH-FIXES. Per-task Gate A file `audit/coach-118-fleet-cli-multi-target-gate-a-2026-05-16.md` referenced in commit body DOES NOT EXIST on disk. Only the batch Gate A (`coach-multi-device-p1-batch-gate-a-2026-05-16.md`) is present, which explicitly says "task-level Gate A still required before implementation begins." Process violation per TWO-GATE lock-in: implementation proceeded without per-task design-level Gate A. P0 (process) — backfill the per-task Gate A file documenting the design that was actually implemented OR explicitly carry as a tracked deviation.
- **Auditor:** APPROVE. Attestation chain integrity preserved; multi-target scope correctly encoded in summary.
- **PM:** APPROVE. Scope is contained, follow-ups are clear.
- **Counsel:** APPROVE. 7 Rules filter clean (with Rule 4 P2 follow-up noted).

## Findings

### P0 (BLOCK)
- **COACH-P0-1 Per-task Gate A artifact missing.** Commit body cites `audit/coach-118-fleet-cli-multi-target-gate-a-2026-05-16.md`; file does not exist. TWO-GATE lock-in (CLAUDE.md §lock-in 2026-05-11) requires Gate A verdict file before implementation. **Required action:** EITHER write the missing per-task Gate A verdict file retroactively documenting the design that shipped (and noting it was authored post-hoc), OR amend the commit body to cite only the batch Gate A and explicitly disclose the per-task Gate A skip. Without this, the commit body is materially inaccurate.

### P1 (MUST-fix-or-task)
- **CARC-P1-1 Count-confirm uses stable N — muscle memory class.** Replace count-echo with random-nonce echo OR `N-<site-tail>` echo. File as TaskCreate followup with explicit deadline.
- **TEST-P1-1 No PG-fixture integration test.** Add `tests/test_fleet_cli_multi_target_pg.py` exercising actual cmd_create against a seeded schema. Source-shape gates miss SQL drift.
- **AUDIT-P1-1 Drop or populate the singular `fleet_order_id: None` write** in `privileged_access_attestation.py:540` to eliminate the dead key (since cross-link aggregate is now authoritative).

### P2 (consider)
- **ORPH-P2-1 Substrate invariant for fan-out orphan completion.** Sev2 invariant scanning privileged fleet_orders >24h old with `< N` completions where `parameters.attestation_bundle_id` covers N targets. Closes Counsel Rule 4 fan-out tail.
- **DOC-P2-1 Update CLAUDE.md privileged-chain section** to mention the 1-bundle:N-orders fan-out shape (mig 175 trigger's EXISTS semantics make it satisfiable) so future readers don't re-debate.

## Final
APPROVE-WITH-FIXES.

P0 is a process-class violation (missing Gate A artifact), not a code defect — the as-implemented artifact is solid and the test sweep is clean (274/274). Author must (a) backfill the per-task Gate A file OR amend the commit body, AND (b) carry the 3 P1s + 2 P2s as named TaskCreate followups in the same commit/branch per TWO-GATE recommendations-are-not-advisory rule. With those, ship is approved.

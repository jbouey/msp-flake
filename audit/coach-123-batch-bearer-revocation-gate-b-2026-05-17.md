# Gate B verdict — #123 Sub-A — 2026-05-17

**Commits:** 16906008..e9971cb4
**Reviewer:** fresh-context fork (general-purpose subagent, opus-4.7[1m])
**Verdict:** APPROVE-WITH-FIXES

## 2-line summary

Foundation-only commit is clean on lockstep + mig 305 verbatim parity + sev1 invariant SQL shape. One P0 caught: `appliance/internal/orders/processor.go:562` adds `"bulk_bearer_revoke": true` to `dangerousOrderTypes` but no `handleBulkBearerRevoke` exists, which violates the round-table-2026-04-29 P3 invariant pinned by `TestDangerousHandlersRegistered` (reprovision_test.go:220). Prod-verified at e9971cb4 only because the deploy workflow does not run Go tests — latent CI hole.

## Lens findings

### Steve (architecture)

- **mig 329 verbatim parity vs mig 305: PASS.** Read both files end-to-end. Function bodies of `enforce_privileged_order_attestation` (lines 43-108 in mig 329 vs 28-92 in mig 305) and `enforce_privileged_order_immutability` (lines 110-163 vs 94-146) are byte-identical except for the single ARRAY entry append `'bulk_bearer_revoke'` (mig 329:59 + mig 329:126). All required Session 220 lock-in elements survive: (a) `parameters->>'site_id'` cross-bundle check (line 70+83-88+90-95); (b) `PRIVILEGED_CHAIN_VIOLATION` error prefix (lines 74,85,99,133,146,155); (c) `USING HINT` clause (line 79-80); (d) `v_was_privileged <> v_is_privileged` UPDATE-spoof check (line 131-137); (e) `attestation_bundle_id` + `site_id` immutability checks (lines 143-159). Additive-only rule fully honored.

- **P0 — Go test regression: `TestDangerousHandlersRegistered` will fail.** `appliance/internal/orders/reprovision_test.go:220-232` iterates every entry in `dangerousOrderTypes` and asserts `p.handlers[orderType]` exists in `NewProcessor()`. Commit `16906008` adds `"bulk_bearer_revoke": true` to the map (processor.go:562) with NO matching handler. The commit comment at processor.go:548-561 explicitly justifies this ("daemon never RECEIVES this order type ... will 'unknown order type' deny") — but that justification directly contradicts the test invariant added by round-table 2026-04-29 P3 (test docstring: "Symptom would be: pre-checkin path correctly rejects the order, but post-checkin dispatch falls through to 'unknown order type' because the handler is missing — operator confusion, no audit narrative"). Either: (a) implement a stub `handleBulkBearerRevoke` that returns a defined-error result; (b) remove from `dangerousOrderTypes` and rely solely on the backend admin-API gate (this is the actually-correct architectural choice per the commit's own reasoning — the daemon should never receive this order, so the deny-list entry is dead weight); (c) update `TestDangerousHandlersRegistered` to exempt server-side-only privileged types via an allowlist comment. CI deploy passed only because `deploy-central-command.yml` does not run `go test ./appliance/...` — running `go test ./appliance/internal/orders/...` locally will fail.

- **The 4-list lockstep architecture has an asymmetry the design treats as "defense in depth" but is functionally dead weight.** If the daemon truly never receives `bulk_bearer_revoke` (server-side UPDATE only), then the Go entry is purely symbolic — sig verification is irrelevant to an order that doesn't exist on the daemon path. The 4-list rule from CLAUDE.md was written when the 4th list (Go) was the LAST-LINE check for orders the daemon would receive. Adding a "ghost" entry just to satisfy lockstep dilutes the original semantics. Recommend revisiting whether `delegate_signing_key` (mig 305 precedent) should have followed the same pattern — checking the test_privileged_order_four_list_lockstep.py `PYTHON_ONLY` allowlist mentioned in CLAUDE.md "delegate_signing_key" line, mig 305 went the OPPOSITE direction (Python-only). #123 inconsistently went the Go-too direction. This is an architectural inconsistency that should be reconciled.

### Maya (security/HIPAA)

- **`bulk_bearer_revoke` event_type shape: APPROVE.** Mirrors `#118 bulk_remediation` fan-out (one attestation, N targets). `summary_payload["target_appliance_ids"] = sorted(target_appliance_ids)` at privileged_access_attestation.py:485 produces the byte-deterministic array the substrate invariant queries via `(cb.summary::jsonb->'target_appliance_ids') ? sa.appliance_id::text`. Writer shape ↔ invariant shape consistent.

- **JSONB array containment correctness: APPROVE.** `?` operator on a JSONB array tests whether a TEXT element exists in the array. `appliance_id::text` cast is required because `?` operates on text keys. Verified `appliance_id` is text-class in the prod schema. Correct.

- **§164.308(a)(4) citation: APPROVE.** Workforce-access controls is the correct regulatory hook for credential revocation. Also see §164.308(a)(3)(ii)(C) for termination procedures — runbook could cite both for completeness but §164.308(a)(4) is the load-bearing one.

- **Synthetic carve-out backdoor: NOT EXPLOITABLE in current code.** Verified that `sites.synthetic = TRUE` has NO runtime write path — only set in mig 315 build-time. The non_prefixed_site_marker_is_synthetic substrate invariant (assertions.py:2410-2451) actively detects future regressions where a real site gets flipped to synthetic. The carve-out is safe AS LONG AS no future endpoint adds an `UPDATE sites SET synthetic = TRUE` writer. Maya P1: add a CI gate banning `UPDATE sites SET synthetic` outside the `noqa: synthetic-gate` allowlist (mirrors the `rename-site-gate` CI gate pattern). Without that gate, this is one PR away from a sev0.

- **Runbook escalation gate for retroactive attestation: caveat.** The runbook says "the endpoint is idempotent on the already-TRUE column; it'll write the missing attestation without flipping the already-TRUE column" — but Sub-A doesn't ship the endpoint. This claim is a Sub-B contract that the operator runbook is asserting BEFORE Sub-B exists. If Sub-B's endpoint forgets idempotency, the runbook's recovery procedure breaks. Tie this in a Sub-B Gate A as a hard requirement.

### Carol (test/CI)

- **Lockstep test coverage: APPROVE for symmetric case.** `test_privileged_chain_allowed_events_lockstep.py:200-205` extends the expected set to include `bulk_bearer_revoke`. `test_privileged_order_four_list_lockstep.py` was NOT modified — but per CLAUDE.md it auto-extends because `bulk_bearer_revoke` is in ALL 4 lists (no allowlist entry needed). Carol caveat: have not opened the four-list test to verify it actually auto-discovers the diff vs. needing a literal — taking the commit body's word that it "passes unchanged."

- **Schema fixture parity: PASS.** No schema migration in this commit (mig 329 only redefines functions, doesn't touch columns). `prod_columns.json`, `prod_column_types.json`, `prod_column_widths.json`, `prod_unique_indexes.json` need no update. Verified: `summary` column on `compliance_bundles` is `jsonb` in `prod_column_types.json`; the `::jsonb` cast in the invariant SQL is unnecessary but harmless redundancy.

- **Source-shape test over-strictness: GENERALLY OK but one risk.** `test_invariant_pins_per_appliance_array_containment` requires `target_appliance_ids` literal to appear in the function body. A legit refactor that renamed to `target_ids` would fail. Acceptable — the test is asserting the chain-of-custody contract and the field name IS the contract. However, `test_invariant_pins_attestation_event_type` requires both `'privileged_access'` and `'bulk_bearer_revoke'` literals. If Sub-B accidentally writes a slightly different `event_type` string (e.g. `'bulk_bearer_revocation'`), the substrate invariant would silently never fire (false negative). The test catches drift in the invariant SQL itself, but does NOT pin parity to the writer. Carol P1: add `tests/test_bulk_bearer_revoke_event_type_consistency.py` that asserts the writer (privileged_access_attestation.py) and the invariant SQL (assertions.py) use the identical event_type string literal.

- **P0 (already raised by Steve): Go test regression.** `TestDangerousHandlersRegistered` will fail on `go test ./appliance/internal/orders/...`. Not caught by the deploy workflow because Go tests are not run there. This is the exact class CLAUDE.md "Gate B MUST run the full pre-push test sweep" lock-in was designed to catch — except the sweep is Python-only. **Add a `go test ./appliance/...` step to .githooks/full-test-sweep.sh** OR to CI as a hard P1 follow-up.

- **Adjacent comment change `{...}` → `(...)`: SEMANTICALLY OK.** Verified via reading `scripts/check_privileged_chain_lockstep.py:70`: the regex `[^{{}}]+?` (escaped `{` and `}`) would indeed treat literal `{...}` inside a Python set comment as set-literal terminators and break extraction. The `(...)` swap is a legit fix. The semantic meaning ("ALLOWED_EVENTS ⊇ collection-of-collections") is preserved.

- **Runbook truth-check filename fix (e9971cb4): MINIMAL but valid.** Only one wrong ref existed at line 116 of the runbook. Confirmed via grep — no other references to `test_substrate_bearer_revoked_attestation_orphan.py` exist anywhere. Fix is complete.

### Coach (consistency-coach pre-completion gate)

- **Pre-push sweep result: PASS — 285 passed, 0 skipped.** Ran `bash .githooks/full-test-sweep.sh` from worktree root. Full output: `✓ 285 passed, 0 skipped (need backend deps)`. Per Session 220 lock-in "Gate B MUST run the full pre-push test sweep" — this gate is satisfied for the Python side. **Critical caveat: the sweep does NOT run Go tests.** The Go P0 from Steve/Carol is invisible to this gate. The lock-in rule should be extended to "pre-push sweep MUST include Go tests when the diff touches appliance/" — file a P1 follow-up.

- **`test_runbook_truth_check.py` SOURCE_LEVEL_TESTS inclusion (Coach Q5): YES, must add.** The e9971cb4 fix existed because the runbook prose drift was caught only at CI test_runbook_truth_check, not in the curated pre-push. Per CLAUDE.md `#68 sweep-parity rule`, every CI-only test that catches a deploy outage class is a candidate for SOURCE_LEVEL_TESTS promotion. This is a P1 — adding it to SOURCE_LEVEL_TESTS closes the entire class. (The full-test-sweep.sh runs ALL `test_*.py` non-pg, so it DID run this commit — but the faster curated lane is the iteration default. Promotion to curated SOURCE_LEVEL_TESTS guarantees zero-iteration pickup.)

- **assertions.py change is additive-only: VERIFIED.** Diff at backend/assertions.py adds: (a) new `_check_bearer_revoked_without_attestation` function (lines 2459-2525); (b) new `Assertion(name="bearer_revoked_without_attestation", ...)` registration in `ALL_ASSERTIONS` (lines 3375-3380); (c) new `_DISPLAY_METADATA["bearer_revoked_without_attestation"]` entry (lines 4390-4408). No existing assertion modified, no existing helper modified, no existing display-metadata entry touched. Pure additive. No risk to other invariants.

- **Sub-B prep work that should have shipped in Sub-A: NONE BLOCKING but two should-haves:**
  1. The runbook section "Auth path inconsistency" (root cause #4) cites a "pin: a test that confirms shared.py reads from site_appliances.bearer_revoked (not a moved table)" — but that test is not shipped in Sub-A. Defer to Sub-B is acceptable, but track explicitly.
  2. The runbook step 2 explicitly cites the Sub-B endpoint (`POST /api/admin/sites/<site_id>/appliances/revoke-bearers`) in the recovery procedure. Operators reading the runbook before Sub-B ships will hit 404. Add a note: "Sub-B endpoint expected by YYYY-MM-DD; until then, manual psql + the privileged_access_attestation.create_privileged_access_attestation() helper is the recovery path."

- **Synchronization issue between Steve's "4-list dead weight" finding and the architecture:** The Go-list entry of `bulk_bearer_revoke` exists in tension with the mig 305 precedent (delegate_signing_key went Python-only). Either the Go entry is right and mig 305 should be retrofitted to add Go-side entry, OR mig 305 is right and this commit should drop the Go-side entry and add to PYTHON_ONLY. The current state is internally inconsistent. Coach P0: pick one direction across both privileged types and reconcile.

## P0 (must close before next commit/marking complete)

1. **Go test `TestDangerousHandlersRegistered` will fail.** `appliance/internal/orders/reprovision_test.go:220-232` iterates `dangerousOrderTypes` and asserts handler existence in `NewProcessor()`. The `"bulk_bearer_revoke": true` entry has no handler. **Choose ONE of three remediation paths:**
   - (a) Remove `bulk_bearer_revoke` from `dangerousOrderTypes` entirely (it's server-side-only — the deny-list entry is functionally dead). Add to `tests/test_privileged_order_four_list_lockstep.py::PYTHON_ONLY` instead, mirroring the `delegate_signing_key` mig 305 precedent.
   - (b) Implement a stub `handleBulkBearerRevoke` that returns `{"success": false, "error": "server-side-only order type — should not reach daemon"}` and register it in `NewProcessor()`.
   - (c) Update `TestDangerousHandlersRegistered` to exempt explicit "server-side-only" entries via a sibling map like `dangerousServerSideOnly: map[string]bool{"bulk_bearer_revoke": true}` with a comment explaining the carve-out.
   Recommendation: (a) — matches existing precedent (mig 305 delegate_signing_key), closes architectural inconsistency, removes dead weight from the daemon's deny-list. ALSO commit body should be amended to no longer claim "4-list lockstep" — it's a 3-list lockstep with a Python-only carve-out.

## P1 (named follow-up tasks in same session)

1. **Add `test_runbook_truth_check.py` to `.githooks/pre-push` SOURCE_LEVEL_TESTS array.** Closes the e9971cb4 deploy-iteration class structurally. (#68 sweep-parity rule.)

2. **Add `go test ./appliance/...` to `.githooks/full-test-sweep.sh`** (or pre-push) when the diff touches `appliance/`. Closes the Go-test invisibility-to-Gate-B class that this commit just exposed.

3. **Add CI gate banning `UPDATE sites SET synthetic` outside an allowlist** (mirrors `tests/test_no_direct_site_id_update.py` pattern). Synthetic carve-out today is one PR-mistake away from a sev0 chain-of-custody bypass. File as ratchet-0 baseline.

4. **Add test pinning event_type literal parity** between `privileged_access_attestation.py` writer and `assertions.py` invariant. Currently each side independently uses `'bulk_bearer_revoke'`; if either drifts, the other silently never fires.

5. **Sub-B Gate A must include "idempotency on already-TRUE" as a hard requirement** — the Sub-A runbook recovery procedure depends on it.

6. **Reconcile Go-list inconsistency between mig 305 (delegate_signing_key, Python-only) and #123 (bulk_bearer_revoke, Go-too).** Pick one canonical pattern for "server-side-only privileged types" and apply uniformly. Document the decision in CLAUDE.md privileged-chain section.

## Recommendation

**APPROVE-WITH-FIXES.** The foundation is well-built: mig 329 perfectly preserves mig 305's function bodies (additive-only rule honored), the substrate invariant SQL shape correctly matches the future Sub-B writer shape, runbook + tests are coherent, sweep passes 285/0. The single P0 (Go test regression) is mechanical to fix via option (a) above — drop the Go-side entry, add to PYTHON_ONLY, amend commit body. The 4 P1s are all sweep-parity / consistency-gate items that strengthen the foundation but do not block forward progress to Sub-B.

**Production state at e9971cb4 (`runtime_sha == disk_sha == matches:true`) is safe** — no live endpoint exists, no writers exist outside the load-test-synthetic-only path. The Go test failure is a CI/dev-loop issue, not a production runtime issue. The author can ship the P0 fix as a separate commit (`fix(#123 Sub-A): drop bulk_bearer_revoke from Go dangerousOrderTypes — server-side-only, mirror mig 305 PYTHON_ONLY precedent`) before Sub-B execution.

**Do NOT mark #123 Sub-A complete until P0 is closed.** The Steve/Coach finding (4-list dead weight + mig 305 inconsistency) is architecturally load-bearing — leaving it unreconciled means the next privileged-type addition will be ambiguous on which precedent to follow.

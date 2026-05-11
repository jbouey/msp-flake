# Gate B re-review verdict — zero-auth hardening Commit 1 (2026-05-11)

**Verdict:** APPROVE

## Fix verification

- **P0 mig 305 additive: PASS.** `migrations/305_delegate_signing_key_privileged.sql` is byte-identical to `migrations/223_enable_recovery_shell_order_type.sql` except:
  (a) `'delegate_signing_key'` appended to `v_privileged_types` in BOTH functions — `enforce_privileged_order_attestation` (line 43) AND `enforce_privileged_order_immutability` (line 109). Steve/Maya check satisfied — UPDATE-into-different-type spoof closed.
  (b) Header comment block (lines 1-24) cites Session 219 + Gate B.
  Full v_privileged_types array preserved (12 entries each function). `PRIVILEGED_CHAIN_VIOLATION` error prefix preserved on all 4 RAISE EXCEPTION sites (305:57, 305:69, 305:83, 305:116/128/138). `USING HINT` clause preserved (lines 63-64). Cross-bundle site_id check preserved (lines 74-79). BEGIN at line 26, COMMIT at line 148 — transaction scope intact.

- **P1-1 username shape: PASS.** `shared.py:490` sets `actor_username = f"appliance:{auth_site_id}"`. Line 497 placeholder is `$1::text` (Session 219 ::text cast rule honored). Line 508 passes `actor_username` as `$1`. Target field at line 509 is `f"appliance:{auth_site_id}"` — parity confirmed.

- **P1-2 + P1-3 deferred to TaskCreate: ACCEPTED** (#111 + #112 per author claim). Gate B convention permits deferral with named follow-up for P1.

## Adversarial findings (any NEW issues)

**Carol — mig rewrite review:** Single BEGIN/COMMIT pair. No SQL injection — all interpolated values (`NEW.order_type`, `v_bundle_id`, `v_site_id`, `OLD.parameters->>...`) flow through `%` format specifiers in RAISE EXCEPTION, which are safe (RAISE format is not string-eval). No dropped GRANT/REVOKE. Function definitions use `CREATE OR REPLACE` — idempotent. No trigger DROP/recreate — the existing triggers from mig 175/223 keep pointing at the redefined function bodies. **No new issues.**

**Steve/Maya — both-functions parity:** Confirmed — `delegate_signing_key` appears in BOTH arrays at the same ordinal position (last entry, line 43 and line 109). The UPDATE-into-privileged-set spoof Steve flagged in Gate A is closed by `enforce_privileged_order_immutability`'s `v_was_privileged <> v_is_privileged` check (305:114). **No new issues.**

## Pre-push sweep result

```
$ python3 scripts/check_privileged_chain_lockstep.py
[lockstep] OK — chain of custody lists consistent

$ pytest tests/test_pre_push_ci_parity.py tests/test_appliance_delegation_auth_pinned.py \
    tests/test_privileged_chain_allowed_events_lockstep.py \
    tests/test_l1_resolution_requires_remediation_step.py \
    tests/test_l2_resolution_requires_decision_record.py \
    tests/test_substrate_docs_present.py tests/test_assertions_loop_runs_clean.py \
    tests/test_assertion_metadata_complete.py
93 passed in 0.80s
```

All three lists (`fleet_cli.PRIVILEGED_ORDER_TYPES`, `attestation.ALLOWED_EVENTS`, `mig 305 v_privileged_types`) contain `delegate_signing_key`.

## Recommendation

**APPROVE — proceed to commit + push.** All three Gate B P0/P1 fixes verified clean. No new issues surfaced. Author may now flip the auth on `appliance_delegation.py:258` in the same commit; the chain-of-custody DB trigger will accept the order because the three-list lockstep is intact. Carry P1-2 (#111) + P1-3 (#112) as named follow-ups per Gate B convention.

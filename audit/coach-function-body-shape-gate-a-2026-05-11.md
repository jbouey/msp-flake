# Gate A — privileged-chain function-body shape CI gate (2026-05-11)

**Verdict:** APPROVE-WITH-FIXES

## Source verification

- Migrations defining `enforce_privileged_order_attestation`: **3** — 175 (canonical, line 1 def), 223 (adds `enable_recovery_shell_24h`), 305 (adds `delegate_signing_key`).
- Migrations defining `enforce_privileged_order_immutability`: **3** — 175 (canonical for this fn too), 223, 305. Also redefined in 176 — but 176 is the **CANONICAL** for `immutability` (175 has a stub-shaped version that 176 supersedes). Verify carefully which mig is the body-truth source per function — they differ.
- Mig 218 exists (`218_privileged_types_watchdog.sql`, 138 lines) but its two `CREATE OR REPLACE FUNCTION` blocks are NOT the two privileged-chain fns — they're a watchdog. **Exclude from the gate.** Filter by `grep -l "enforce_privileged_order_(attestation|immutability)"` (which excludes 218) not by filename pattern.
- Mig 176 (`176_privileged_chain_update_guard.sql`) DOES redefine `enforce_privileged_order_immutability` (line 20) — **must be in scope**.
- Delimiter shape: uniform `LANGUAGE plpgsql AS $$ ... $$;` across all 4 in-scope migs (175/176/223/305). No `$function$` variants. Safe to anchor parser on `$$`.

## P0/P1/P2 findings

**P0-1 — canonical-source pinning is wrong as written.** Spec says "canonical = mig 175 body" for BOTH functions. But mig 176 supersedes 175's `immutability` body (that's literally what 176 exists for). Gate must declare canonical per-function: `attestation → 175`, `immutability → 176`. Otherwise day-one the gate FAILS on its own checked-in tree, or worse — pins to the wrong body and lets 305's regression re-land.

**P0-2 — canonical-body tamper backdoor (Carol).** Comparing N≥2 redefs against an in-tree file means: edit canonical mig 175/176 + the new mig in the same commit → both match → silent pass. Mitigation MUST be a SHA256 of the normalized canonical body PINNED IN THE TEST FILE as a literal constant. Any change to the canonical body breaks the test until the dev updates the pinned hash, forcing explicit ack.

**P0-3 — legitimate-body-change path undefined.** Spec waves at "update mig 175 with header comment" but doesn't say how the gate accepts it. Concrete shape: when canonical-hash literal is updated in the test file, that diff alone is reviewable. Require the commit message to contain `PRIVILEGED-CHAIN-BODY-CHANGE: <reason>` (enforce via separate pre-push gate or PR template). No per-migration opt-out comment — that's a footgun.

**P1-1 — normalize step regex.** `v_privileged_types TEXT[] := ARRAY[...]` spans multiple lines in 223/305. Use a non-greedy DOTALL regex: `re.sub(r"v_privileged_types\s+TEXT\[\]\s*:=\s*ARRAY\[.*?\];", "<ARRAY>", body, flags=re.DOTALL)`. Test with a fixture per migration to prove normalization is stable.

**P1-2 — lockstep redefinition.** Don't enforce "both fns redefined together". Mig 176 deliberately touches only `immutability`. Allow partial; assert per-function independently.

**P2-1 — diff output.** On mismatch print unified diff PLUS the exact 2-line remediation (revert vs. update-canonical-hash). Mirror `tests/test_privileged_chain_function_body_shape.py` next to existing `test_no_direct_site_id_update.py` (sibling pattern).

## Per-lens (brief)

- **Steve:** P0-1 + P1-1 are real; regex must be DOTALL non-greedy, tested per-mig.
- **Maya:** Mig 218 exclusion confirmed; mig 176 inclusion mandatory; partial-redef must be allowed.
- **Carol:** P0-2 hash-pin is non-negotiable — without it the gate is theater.
- **Coach:** Sibling pattern is `test_privileged_chain_lockstep` + escalate-drift gate; failure message must name the 2 valid responses verbatim.

## Recommendation

APPROVE-WITH-FIXES. Address all 3 P0s before implementation:
1. Per-function canonical source (attestation→175, immutability→176).
2. Pin canonical normalized-body SHA256 as test-file literal (tamper backdoor closer).
3. Define explicit body-change protocol: update pinned hash + `PRIVILEGED-CHAIN-BODY-CHANGE:` commit-msg token.

P1s addressable in implementation. Gate B will verify the as-implemented shape catches a synthetic mig-305-style regression in a fixture.

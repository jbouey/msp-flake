# Gate B verdict — function-body shape CI gate (2026-05-11)

**Verdict:** APPROVE

## Gate A directive compliance

- **P0-1 per-function canonical:** Two separate hashes pinned at lines 75-76: `_CANONICAL_ATTESTATION_HASH` and `_CANONICAL_IMMUTABILITY_HASH`. Latest-mig-per-function logic at line 139/170 (`redefs[-1]`). Cutoffs at lines 81-82 are per-function (att=217, imm=222 — different because mig 218 changed attestation but kept its own immutability body, mig 223 changed immutability). Confirmed by extraction (see Adversarial findings).
- **P0-2 SHA256 hash pinning:** Pinned as Python literals at lines 75-76 inside the test file (in-repo, signed via git). Canonical-tamper backdoor closed: editing mig 305 without bumping literal breaks `test_canonical_hashes_match_current_mig_305`.
- **P0-3 change protocol:** Failure messages at lines 154 and 183 cite `PRIVILEGED-CHAIN-BODY-CHANGE: <reason>` token AND the two valid responses ((a) revert verbatim, (b) update hash + commit token). Header docstring lines 29-33 + canonical-constant docstring lines 65-74 both restate the protocol.

## Full sweep result (MANDATORY)

136 passed, 0 failed (~2.22s). Confirmed.

## Adversarial findings (NEW)

**Hash extraction verification (Steve P1):** Re-ran `_collect_redefinitions` against all 5 migrations:

```
attestation:  175=e4208b26  218=16d64e0a*  223=16d64e0a*  305=16d64e0a* (canonical)
immutability: 176=a98c3877  218=123a947f   223=6ff6cd07*  305=6ff6cd07* (canonical)
```

Cutoffs correctly exclude:
- mig 175 (pre-218 attestation body)
- mig 176 (pre-223 immutability body)
- mig 218 immutability (pre-223 body, intermediate evolution `123a947f`)

The asymmetric cutoff (att=217 vs imm=222) is REQUIRED and correct — mig 218 redefined both functions but only the attestation body matches the current canonical. Author's "mig 218 IS in scope" Gate A correction is accurate for attestation; the immutability body at mig 218 is legitimately historical.

**Steve P2 — new DECLARE block class:** Confirmed gap. `_ARRAY_RE` only strips `v_privileged_types TEXT[] := ARRAY[...]`. A future mig adding e.g. `v_new_array TEXT[] := ARRAY[...]` would land in the hash → forces explicit bump. This is the INTENDED behavior (additive-only contract). Not a defect; documented as gate semantics.

**Carol — hash-bump-without-token attack:** Confirmed documented gap. If author edits both mig 305 body AND `_CANONICAL_ATTESTATION_HASH` in the same commit without `PRIVILEGED-CHAIN-BODY-CHANGE:` token, test passes silently. Mitigation relies on review discipline (file diff visibility) — same pattern as escalate-drift gate sibling. Defense-in-depth recommendation: future CI gate that scans `git log` for `_CANONICAL_*_HASH` literal changes and requires the token. Carry as P2 followup (not Gate B-blocking).

**Maya — synthetic regression fidelity:** `test_synthetic_body_change_is_caught` strips site_id + PRIVILEGED_CHAIN_VIOLATION + USING HINT — exact mig-305-v1 regression class. Faithful positive control.

**Coach — sibling pattern parity:** Matches `test_escalate_rule_check_type_drift.py` and `test_privileged_chain_allowed_events_lockstep.py` shape (path discovery → normalize → hash/allowlist → positive control). Listed in `.githooks/pre-push` SOURCE_LEVEL_TESTS. No new substrate assertion / no new migration → substrate-doc class N/A.

## Recommendation

APPROVE. Gate A P0-1/P0-2/P0-3 closed structurally. Full sweep green. Per-function cutoff asymmetry is correctly reasoned and matches extracted reality. One non-blocking P2 followup: CI gate to flag canonical-hash literal mutations missing `PRIVILEGED-CHAIN-BODY-CHANGE:` token — carry as TaskCreate item, not a Gate B blocker. Ship.

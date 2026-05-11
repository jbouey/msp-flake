# Gate B verdict — hash-bump-token CI gate (2026-05-11)

**Verdict:** APPROVE

## Gate A directive compliance
- P0-1 fetch-depth: 0: ✓ — `grep -c "fetch-depth: 0" .github/workflows/deploy-central-command.yml` returns **4** (all 4 checkout steps updated).
- P2-1 regex pinned to names: ✓ — `_HASH_LITERAL_PATTERN` at line 69-72 literally references `_CANONICAL_(ATTESTATION|IMMUTABILITY)_HASH`. Refactor renames will deliberately break this gate (desired tight coupling).
- P2-2 grandfather: ✓ — `_GRANDFATHERED_COMMITS` (line 61-63) contains `7e741507a564190bb51e1c6851dae15b7c509cea` with task #111 comment. `test_grandfathered_commits_are_real_shas` verifies it exists in git history.

## Full sweep result
**140 passed, 0 failed** in 3.76s. Matches author's reported 4/4 new gate + 140/140 curated sweep.

## Adversarial findings

### Steve
- **Shallow-clone detection (line 134-150):** Robust. Uses `.git/shallow` marker file presence as ground truth — orphan branches and freshly-init repos won't have this marker, so `depth==1` alone doesn't trigger the AssertionError. Subprocess `CalledProcessError` (outside git repo / tarball install) returns cleanly. Both modes handled correctly.
- **Git availability:** `_run_git` uses `check=True` → loud failure if git is missing. For test execution context this is acceptable (CI always has git; local dev always has git). Non-issue.
- **`git log -p` performance on pinned file:** Filtered to ONE file via `-- <path>`. On a repo with 3000+ commits, filtered log-p is sub-second. No concern.

### Maya
- **Grandfather-list attacker model:** An attacker adding a malicious SHA to `_GRANDFATHERED_COMMITS` shows up in PR diff — reviewer-attention defense. `test_grandfathered_commits_are_real_shas` enforces SHA existence (not semantic correctness). Acceptable per Gate A v1 framing. Defense-in-depth via sibling `test_canonical_hashes_match_current_mig_305` catches stale-value replay.

### Carol
- **Case-sensitive token (`PRIVILEGED-CHAIN-BODY-CHANGE:`):** Strictness IS the signal — lowercase variants fail, which is correct. Documented at line 74-76.
- **Replay defense:** Author's claim confirmed — body-shape pin (`test_canonical_hashes_match_current_mig_305`) catches stale-hash replay. This gate's scope is correctly narrowed to token-presence.

### Coach
- **Sweep:** 140/140 confirmed. ✓
- **Sibling pattern:** Matches `test_pre_push_allowlist_only_references_git_tracked_files` (git-aware) + `test_privileged_chain_function_body_shape.py` (companion). Documented in module docstring lines 38-42. ✓
- **Subprocess robustness:** `_run_git` uses `cwd=_REPO`, `capture_output=True`, no global state mutation. Clean. ✓
- **Introducing-commit grandfathering for THIS gate:** The commit landing `test_canonical_hash_change_requires_token.py` does NOT mutate `_CANONICAL_*_HASH` literals (this file ADDS unrelated literals to the watchlist). `_hash_mutating_commits()` only matches commits touching `_PINNED_FILE` (`test_privileged_chain_function_body_shape.py`), not this new file. So the landing commit body needs no token. ✓

### Net
No P0 / P1 / P2 findings. Gate A v1 directives fully complied. Full curated sweep clean. Synthetic positive control (`test_synthetic_violation_via_in_memory_diff`) verifies the regex catches the target shape. Robust shallow-clone fail-loud. Robust grandfather-SHA-exists check.

## Recommendation
**APPROVE — ship as-is.** Commit body need not contain the token (this commit adds the gate, doesn't mutate the pinned hashes). Suggest commit body cite both Gate A v3 + Gate B verdicts per TWO-GATE lock-in rule.

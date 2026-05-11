# Gate A — canonical hash-bump token requirement (2026-05-11)

**Verdict:** APPROVE-WITH-FIXES

## Source verification
- CI fetch-depth: **SHALLOW** (default = 1). Verified: `.github/workflows/deploy-central-command.yml` has 4× `actions/checkout@v4` steps, NONE specify `fetch-depth: 0`. `git log -p` will only see HEAD commit in CI.
- Pre-push hook git access: VERIFIED — `.githooks/pre-push` already shells `git` (pattern present); local dev box has full history.
- Sibling pattern `test_pre_push_allowlist_only_references_git_tracked_files` confirmed git-aware shape is established.

## P0/P1/P2 findings

**P0-1 (Steve): CI cannot execute the gate as drafted.** Shallow checkout means `git log -p` returns 1 commit. The gate must EITHER (a) add `fetch-depth: 0` to the deploy workflow checkout (cheap; ~5s on a ~3000-commit repo) OR (b) be declared pre-push-only and ratchet-gated in CI by a separate static assertion (e.g. CI verifies the LATEST commit-message-of-HEAD has the token IFF the hash-literal lines changed vs `HEAD~1` — single-parent diff, works on shallow). Recommend (a): consistent with sibling git-aware gates; future gates will need it too.

**P1-1 (Maya): split-commit attack is real and out-of-scope of the proposed gate.** Author can land migration-weakening in commit N and hash-bump in commit N+1; the gate flags only N+1, but N alone weakened the migration and the existing `test_canonical_hashes_match_current_mig_305` test was RED between N and N+1 (caught by CI on commit N). So the split-commit path is already blocked by the existing pin test failing on commit N. ACCEPT as-is; document in gate docstring.

**P2-1 (Steve): regex brittleness.** Pin the regex to the literal constant names `_CANONICAL_ATTESTATION_HASH` and `_CANONICAL_IMMUTABILITY_HASH` (case-sensitive, anchored at line start with optional indent). If a future refactor moves the constants, the refactoring commit MUST update this gate's regex — that's the desired coupling.

**P2-2 (Coach): grandfather initial commit.** Hardcode the SHA of the introducing commit (task #111 commit) in a `_GRANDFATHERED_COMMITS = frozenset({"<sha>"})` constant. Cleaner than "first appearance" heuristic.

## Per-lens (brief)
- **Steve:** CI shallow-checkout blocker is P0. Fix via fetch-depth:0.
- **Maya:** Split-commit class already covered by existing pin test. OK.
- **Carol:** Replay/stale-hash attack constrained by the existing pin test. OK.
- **Coach:** Grandfather list + reference sibling-pattern docstring.

## Recommendation
APPROVE-WITH-FIXES — proceed to implementation conditional on: (1) add `fetch-depth: 0` to deploy workflow checkout steps in the SAME commit as the gate, (2) grandfather task-#111 introducing-commit SHA as `_GRANDFATHERED_COMMITS`, (3) docstring documents split-commit class is covered by existing `test_canonical_hashes_match_current_mig_305` pin. Gate B must verify CI actually runs the new test on a synthetic violation commit (negative control).

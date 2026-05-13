# Gate A — Pre-push Silent-Pass Hardening (Task #68)

**Date:** 2026-05-13
**Scope:** `.githooks/pre-push` lines 26–355 — close the silent-skip class that allowed `fd54e637` to deploy with a missing `## Escalation` substrate runbook section despite the author running `bash .githooks/pre-push origin main` and seeing `rc=0`.
**Reviewer:** 7-lens fork (Steve / Maya / Carol / Coach / Auditor / PM / Counsel)
**Verdict:** **APPROVE-WITH-FIXES** — ship **Option A + explicit empty-CHANGED guard + manual-invocation print**. Pure Option A is necessary but not sufficient: the real bug class is `CHANGED=""` silently meaning "nothing to check," not "env var unset." See patch below.

## 150-word summary

The silent-pass on `fd54e637` was not caused by `ISO_CHANGED` / `BACKEND_CHANGED` being **unset** — they are unconditionally initialized to `0` on lines 27–29. It was caused by `git diff --name-only "@{u}..HEAD"` returning an **empty string** when `HEAD == @{u}` (which is exactly the state right after a successful push, the common manual-rerun case). The loop then never flips either flag, and every gated block — smoke-import, py3.11 syntax, SOURCE_LEVEL_TESTS, full sweep, frontend tsc — is silently bypassed with `rc=0`. Option A as stated ("default env vars to 1 if unset") does not fix this because the vars are already set; the fallback `git diff --name-only "HEAD~5..HEAD"` after `||` also doesn't fire because the first command exits 0. The correct fix is to detect **empty CHANGED** explicitly and force all flags to 1, plus log the manual-invocation mode so the developer sees what ran.

---

## Probes

### What `$ISO_CHANGED` and `$BACKEND_CHANGED` actually evaluate to

Lines 26–36 of `.githooks/pre-push`:

```bash
CHANGED=$(git diff --name-only "@{u}..HEAD" 2>/dev/null || git diff --name-only "HEAD~5..HEAD")
BACKEND_CHANGED=0
ISO_CHANGED=0
FRONTEND_CHANGED=0
for f in $CHANGED; do
    case "$f" in
        mcp-server/central-command/frontend/*) FRONTEND_CHANGED=1 ;;
        mcp-server/*) BACKEND_CHANGED=1 ;;
        iso/*)        ISO_CHANGED=1 ;;
    esac
done
```

**Confirmed locally:**

```
$ git diff --name-only "@{u}..HEAD"   # exits 0 with empty stdout
$
```

When manual-invoked after push (or any time HEAD already matches upstream), `CHANGED=""`. The `||` fallback to `HEAD~5..HEAD` **never fires** because the first git command exits 0 — `||` only fires on non-zero. Loop iterates zero times. All three flags stay at `0`.

Result: line 39 (`smoke-import`) skipped, line 60 (py3.11 syntax) skipped, line 341 (SOURCE_LEVEL_TESTS) skipped, line 375 (full sweep) skipped, line 433 (frontend tsc) skipped. Hook exits 0. **This is the bug.**

### Real git-push invocation vs manual invocation

When git invokes `pre-push` for a real push, it passes the remote ref + sha + local ref + sha on stdin AND the hook sees `HEAD` pointing at commits not yet on the remote — so `@{u}..HEAD` is non-empty as long as the developer is pushing new commits. The bug only manifests on **manual reruns** ("did the hook even fire? let me try it standalone"), which is exactly the workflow that produced `fd54e637`.

### Three sibling gates also silently skip

Same root cause silently skips the **frontend tsc** gate (line 433), the **smoke-import** (line 39), and the **CI Python 3.11 syntax check** (line 60). Today's deploy-class is broader than just SOURCE_LEVEL_TESTS.

---

## Option analysis

**Option A as literally written ("default env vars to 1 if unset") does not work.** The variables are already set to `0` by lines 27–29. `[ -z "$BACKEND_CHANGED" ]` is always false. **The fix has to operate on `CHANGED` being empty, not on the flags being unset.**

| Option | Effect | Cost | Correctness |
|---|---|---|---|
| **A (revised)** — when `CHANGED` is empty, force all flags to 1 + log "manual mode" | Fixes silent-skip on every manual rerun | ~45s when manual-rerun; ~0s on real push (CHANGED non-empty so flags stay branch-targeted) | ✅ closes class |
| B — always run SOURCE_LEVEL_TESTS unconditionally | Heavy: ~45s on every docs-only push too | ~45s × every push including pure-MD/pure-audit pushes | ⚠️ over-aggressive; the existing branch gating exists for a reason (docs-only pushes today exit in ~50ms) |
| C — `--force` flag opt-in | Relies on humans remembering to opt in. fd54e637 author rebuilt the exact behavior they thought was running. Won't catch the next manifestation. | trivial | ❌ does not close class |

**Recommendation: Option A (revised) with three additional guards** — see patch.

---

## Code patch

Replace lines 26–36 with:

```bash
# Determine which subsystems the push touches. We gate two separate
# local checks: (a) Python smoke-import for backend, (b) source-level
# iso pytests for iso/*. Each only runs when relevant — infra-only
# doc pushes stay under a second.
#
# 2026-05-13 silent-pass fix (task #68): when CHANGED is empty
# (happens on manual reruns where HEAD == @{u}, e.g. "did the hook
# even fire? let me try it standalone"), force every flag to 1 and
# announce manual-mode so the developer SEES what ran. The fd54e637
# silent-pass deployed because @{u}..HEAD returned empty, the for-loop
# iterated zero times, every gate silently skipped, hook exited 0.
CHANGED=$(git diff --name-only "@{u}..HEAD" 2>/dev/null || git diff --name-only "HEAD~5..HEAD")
BACKEND_CHANGED=0
ISO_CHANGED=0
FRONTEND_CHANGED=0
if [ -z "$CHANGED" ]; then
    # Manual invocation or push-already-at-upstream — assume worst case.
    # Trades ~45s for closure of the silent-skip class.
    echo "[pre-push] note: empty diff against @{u} — running ALL gates (manual-rerun mode)"
    BACKEND_CHANGED=1
    ISO_CHANGED=1
    FRONTEND_CHANGED=1
else
    for f in $CHANGED; do
        case "$f" in
            mcp-server/central-command/frontend/*) FRONTEND_CHANGED=1 ;;
            mcp-server/*) BACKEND_CHANGED=1 ;;
            iso/*)        ISO_CHANGED=1 ;;
        esac
    done
fi
```

**Why this shape vs the suggested `[ -z "$BACKEND_CHANGED" ] && BACKEND_CHANGED=1`:** The flags are already initialized at lines 27–29. Defaulting *unset* values to 1 catches **zero** real cases — they're never unset. The actual bug is that `CHANGED=""` causes the loop to no-op while every flag stays at its initialized `0`. The fix has to short-circuit before the loop.

**The `echo` line is load-bearing.** Without it the developer sees no signal distinguishing "ran in 50ms because nothing changed" from "ran ALL gates including the 45s sweep." Today's silent-pass class is partly an observability bug — the hook is too quiet to tell you what mode it picked.

---

## Per-lens verdict

### 1. Engineering (Steve) — APPROVE-WITH-FIXES

The suggested Option A as written is **wrong** — it defaults *unset* env vars to 1, but the flags are unconditionally initialized to 0 on lines 27–29, so the default-clause never fires. The real bug is `CHANGED=""` → empty loop → flags stay 0. My patch operates on the right variable.

Are there real cases where someone WANTS to skip source-level tests? **Yes — docs-only pushes** (~50ms current cost vs ~45s under naive Option B). The patch preserves this: when `CHANGED` is non-empty and contains only `docs/*` or `.agent/*` or `audit/*`, the loop runs but flips no flags. Only the **empty-CHANGED case** triggers the all-on fallback. This keeps the docs-only fast lane (legitimate workflow) intact while closing the manual-rerun silent-pass.

The `||` fallback on line 26 is also subtly broken — `git diff` exits 0 on empty diff, so the right side never fires. Leaving it for a followup; the new `[ -z "$CHANGED" ]` branch makes it moot in practice.

### 2. Database (Maya) — N/A

No DB surface.

### 3. Security (Carol) — APPROVE

Hook-bypass attack vector: git hooks are local-only and bypassable with `--no-verify`; they are not a security boundary, they are a deploy-quality boundary. The fix doesn't change the threat model. No new attack surface. **APPROVE.**

One adjacent observation worth a P2 followup (not blocking): the `.githooks/full-test-sweep.sh` call on line 377 already has a `PRE_PUSH_SKIP_FULL` opt-out. A malicious actor with local repo write can already bypass — no point hardening that without addressing `--no-verify` which is part of git's UX contract.

### 4. Coach — APPROVE-WITH-FIXES, also surface higher-level CI parity gate

**Closure of the immediate class:** the patch above closes the silent-skip class deterministically — manual rerun now runs every gate, with a visible log line.

**Closure of the broader class — silent absence of evidence:** the recurring antipattern across deploys (RT33, 18af959c, fd54e637) is that the hook **exits 0 with no evidence of what ran**. Three followups, ordered by yield:

1. **Add a summary line at hook exit** stating exactly which gates ran and which were skipped, mirroring CI's per-step summary. ~5 lines. Closes the entire "developer thought it ran the X test" class.
2. **CI gate that verifies pre-push and CI have parity** — add a test that enumerates the SOURCE_LEVEL_TESTS array and asserts every entry appears in `.github/workflows/*.yml`'s pytest invocation. The 675fa1a6 grandfathering note on line 109 implies this drift is recurring. Make it programmatic, not aspirational. Open as TaskCreate followup.
3. **Boot-time substrate invariant `pre_push_gates_diverged_from_ci` (sev2)** — daily background sweep comparing local hook's SOURCE_LEVEL_TESTS against the CI workflow file as it exists in main. Catches drift introduced AFTER the CI gate runs.

These three together make the next manifestation of "I ran pre-push and it was clean but CI failed" structurally impossible.

### 5. Auditor (OCR) — N/A

No customer-facing artifact, no §164.528 implication, no chain-of-custody surface.

### 6. PM — APPROVE

**Effort:** ~10 min (5-line patch + commit + push). **Risk:** trivial. **Yield:** closes a deploy-class that has cost at least one ~5 min outage this week and a comparable outage on RT33 + 18af959c earlier. The ~45s added to manual reruns is well within the noise of the developer's iteration loop (most reruns are after a "fix the issue" cycle that takes longer than 45s anyway).

**Carry the 3 Coach followups as TaskCreate items in the same commit body** so they don't fall off the radar.

### 7. Attorney — N/A

---

## Followups (Coach §)

Carry as separate TaskCreate items in the same commit:

- **Followup A (P1):** Add hook-exit summary line stating which gates ran / skipped. ~5 lines, closes "I thought it ran X" class.
- **Followup B (P1):** CI gate `tests/test_prepush_ci_source_level_parity.py` — enumerates `.githooks/pre-push` SOURCE_LEVEL_TESTS and asserts each is in the CI workflow file. Drives line 109's "grandfathering" comment to a programmatic check.
- **Followup C (P2):** Fix the `||` fallback on line 26 — `git diff` exits 0 on empty, so `HEAD~5..HEAD` never fires. Tracked behavior with the new empty-CHANGED branch, but the dead-code `||` is misleading and should be removed.

---

## Final verdict: APPROVE-WITH-FIXES

Ship the **revised Option A** patch above (operate on empty `CHANGED`, not on unset env vars) + manual-mode log line. Carry the 3 followups as TaskCreate items. Gate B (post-commit) verifies the patch by running `git push --dry-run` from a HEAD == @{u} state and confirming the new "manual-rerun mode" log line + non-zero exit on a synthetic test failure.

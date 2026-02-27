# Development Workflow Patterns

Adapted from [obra/superpowers](https://github.com/obra/superpowers) systematic-debugging and verification-before-completion skills.

## Systematic Debugging (4-Phase)

**Iron Law: NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST.**
Symptom fixes are failure. 95% first-time fix rate with this process vs 40% ad-hoc.

### Phase 1: Root Cause Investigation

Before proposing ANY fix:

1. **Read error messages carefully** — don't skip past errors. Note line numbers, file paths, error codes.
2. **Reproduce consistently** — can you trigger it reliably? If not reproducible, gather more data instead of guessing.
3. **Check recent changes** — `git diff`, recent commits, new deps, config changes, env differences.
4. **Gather evidence at component boundaries** — for multi-component systems (API→service→DB, CI→build→signing), add diagnostic logging at each boundary. Run once to identify WHERE it breaks, then investigate that layer.
5. **Trace data flow backward** — where does the bad value originate? What called this with bad input? Keep tracing upward until finding the source. Fix at source, not at symptom.

### Phase 2: Pattern Analysis

1. **Find working examples** — locate similar working code in the same codebase.
2. **Compare against references** — read the reference implementation COMPLETELY. Don't skim.
3. **Identify differences** — list every difference, however small. Don't assume anything "can't matter."
4. **Understand dependencies** — what other components, settings, config, environment does this need?

### Phase 3: Hypothesis and Testing

1. **Form single hypothesis** — "I think X is the root cause because Y." Be specific.
2. **Test minimally** — SMALLEST possible change. One variable at a time. Don't fix multiple things at once.
3. **Verify before continuing** — did it work? Yes → Phase 4. No → new hypothesis. DON'T add more fixes on top.

### Phase 4: Implementation

1. **Create failing test case** — simplest possible reproduction. Automated test if possible.
2. **Implement single fix** — ONE change at a time. No "while I'm here" improvements.
3. **Verify fix** — test passes? No other tests broken? Issue resolved?
4. **If fix doesn't work** — STOP. Count attempts. If < 3: return to Phase 1. If >= 3: question the architecture. Discuss before attempting more.

### Red Flags — STOP and Return to Phase 1

- "Quick fix for now, investigate later"
- "Just try changing X and see if it works"
- "Skip the test, I'll manually verify"
- "It's probably X, let me fix that"
- "I don't fully understand but this might work"
- Proposing solutions before tracing data flow
- "One more fix attempt" (when already tried 2+)
- Each fix reveals new problem in different place

### Rationalizations vs Reality

```
EXCUSE                                    REALITY
────────────────────────────────────────  ────────────────────────────────────────
"Issue is simple, don't need process"     Simple issues have root causes too
"Emergency, no time for process"          Systematic is FASTER than guess-and-check
"Just try this first, then investigate"   First fix sets the pattern. Do it right.
"I'll write test after confirming fix"    Untested fixes don't stick
"Multiple fixes at once saves time"       Can't isolate what worked
"I see the problem, let me fix it"        Seeing symptoms ≠ understanding root cause
"One more fix attempt" (after 2+ fails)   3+ failures = architectural problem
```

## Verification Before Completion

**Iron Law: NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE.**

### The Gate (5 steps, every time)

1. **IDENTIFY** — what command proves this assertion?
2. **RUN** — execute the command (fresh, not cached)
3. **READ** — review full output, check exit codes
4. **VERIFY** — does output substantiate the claim?
   - NO → state actual status with evidence
   - YES → state claim WITH evidence shown
5. **ONLY THEN** — make the claim

Skipping any step = dishonesty, not efficiency.

### What Counts as Verification

```
CLAIM               REQUIRES                         NOT SUFFICIENT
──────────────────  ───────────────────────────────  ──────────────────────────
Tests pass          Test output showing 0 failures    Previous run, "should pass"
Linter clean        Linter output showing 0 errors    Partial check, extrapolation
Build succeeds      Build command exit 0              Linter passing, logs look good
Bug fixed           Test original symptom passes      Code changed, assumed fixed
Regression test     Red-green cycle verified           Test passes once
Deploy works        Health check returns OK            Push succeeded
```

### Red Flags — STOP and Verify

- Using "should," "probably," "seems to"
- Expressing satisfaction before verification
- About to commit/push/PR without running checks
- Trusting success reports uncritically
- Relying on partial verification
- Thinking "just this once"

### Apply Before

- Any success/completion claim
- Committing, pushing, PRs, task completion
- Moving to next task
- Expressing confidence in a fix

### Project-Specific Verification Commands

```bash
# Python agent
cd packages/compliance-agent && source venv/bin/activate
python -m pytest tests/ -v --tb=short

# Go daemon
cd appliance && go build ./... && go test ./...

# NixOS config
nix flake check --no-build

# Runbooks JSON
python -c "import json; json.load(open('appliance/internal/daemon/runbooks.json'))"

# Frontend
cd mcp-server/central-command/frontend && npm run build

# Health check (VPS)
curl -s https://api.osiriscare.net/health | jq .
```

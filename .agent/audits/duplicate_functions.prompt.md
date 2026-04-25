# Duplicate function/loop audit

Use with: `Agent(subagent_type="Explore", prompt=<this file's contents>)`.

---

Audit the Msp_Flakes monorepo (cwd: /Users/dad/Documents/Msp_Flakes) for "double flywheel" class bugs — functions, classes, or scheduler loops defined in 2+ places that could compete or shadow each other.

Background: on 2026-04-25 we found two `flywheel_promotion_loop` definitions (one in main.py, one in background_tasks.py) writing to the same tables with different filtering. The duplicate was the live one and contained a Step-3 filter bug; the other was dead code shadowing it. Took 6+ hours of substrate-violation forensics to find. We want to catch this class proactively.

Hunt these patterns:

1. Same function name defined `def NAME(` or `async def NAME(` in 2+ files under `mcp-server/central-command/backend/` AND `mcp-server/main.py` — flag any pair where both are >20 lines (live code, not stubs).
2. Background-task loops registered in main.py's `task_defs` list — verify each task name (string key) has exactly one definition across the codebase.
3. Same Pydantic class name in 2+ files (was driven to 0 in 210-B; verify and flag any new ones).
4. Same SQL view/function/trigger name in 2+ migrations (later migration silently replaces the earlier with no error if CREATE OR REPLACE).
5. Two separate `@router.post("/some/path")` for the same path (would 500 at startup but worth a guard).

Skip:
- `mcp-server/venv/`, `.claude/worktrees/`, anything under `archived/`
- Test files (`test_*.py`) — duplication there is fine
- Vendored code (`vendor/`, `node_modules/`, `*.test.ts`)

**Polarity rule:** if you find a duplicate, do NOT recommend deletion without first determining which side is live. Live = imported AND registered in mounted routers / task_defs. Auto-deletion of the wrong half re-introduces the bug.

Report format (under 300 words total):
- Findings as a bulleted list, each line: `<finding>: <files involved> — <recommended action with polarity check note>`
- If no findings: explicitly say "Clean — no duplicates found"
- If any finding feels false-positive (e.g. handler vs test stub), say so

Don't write or edit any files. Read-only audit.

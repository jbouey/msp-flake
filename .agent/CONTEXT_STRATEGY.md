# Context Engineering Strategy

Based on [Anthropic's multi-session agent research](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) and [context engineering best practices](https://01.me/en/2025/12/context-engineering-from-claude/).

---

## Core Insight

> "Intelligence isn't the bottleneck—context is. Claude is already smart enough; the key is giving it the right context."

---

## 1. Progress Tracking (Anthropic's Primary Recommendation)

Anthropic uses a `claude-progress.txt` file to bridge sessions. We adapt this:

**File: `.agent/claude-progress.json`** (JSON over Markdown - less likely to be corrupted)

```json
{
  "session": 85,
  "updated": "2026-02-03T14:30:00Z",
  "agent_version": "1.0.52",
  "iso_version": "v52",

  "system_health": {
    "vps_api": "healthy",
    "dashboard": "healthy",
    "physical_appliance": "offline",
    "vm_appliance": "blocked"
  },

  "current_blocker": {
    "id": "chicken-egg-update",
    "description": "v1.0.49 can't process update orders to get v1.0.52 fix",
    "solution": "SSH manual intervention"
  },

  "active_tasks": [
    {"id": 1, "task": "Manual VM appliance update", "status": "blocked"},
    {"id": 2, "task": "Verify fleet update system", "status": "pending"},
    {"id": 3, "task": "Evidence bundles to MinIO", "status": "pending"}
  ],

  "completed_this_session": [
    "Removed dry_run mode",
    "Added circuit breaker for healing loops"
  ],

  "git_commits": ["81bd9a6", "ada2b0c"]
}
```

**Why JSON:** Anthropic notes models are "less likely to inappropriately change or overwrite JSON files" compared to Markdown.

---

## 2. Just-In-Time (JIT) Retrieval

**Don't preload everything.** Load docs on-demand.

### Before (Bad)
```
Read CLAUDE.md, IMPLEMENTATION-STATUS.md, TODO.md, CONTEXT.md,
SESSION_HANDOFF.md, all 47 session files...
```

### After (Good)
```
Read .agent/claude-progress.json
[Contains only current state - 50 lines max]
[References to other docs if needed]
```

**Progressive disclosure:** Start minimal, load more only when the task requires it.

---

## 3. Context Rot Prevention

Anthropic identifies four causes of degraded output:

| Type | Cause | Prevention |
|------|-------|------------|
| **Poisoning** | Incorrect/outdated info | Single source of truth in JSON |
| **Distraction** | Irrelevant data | Aggressive pruning, JIT loading |
| **Confusion** | Similar but distinct info mixed | Clear file ownership (see below) |
| **Clash** | Contradictory statements | Auto-validation before commit |

---

## 4. File Ownership (One Home Per Fact)

| Information | Owner File | Never Duplicate To |
|-------------|------------|-------------------|
| Current versions | `claude-progress.json` | CLAUDE.md, TODO.md |
| System health | `claude-progress.json` | CONTEXT.md |
| Active tasks | `claude-progress.json` | - |
| Session details | `sessions/*.md` | TODO.md |
| Phase milestones | `IMPLEMENTATION-STATUS.md` | - |
| Architecture | `docs/ARCHITECTURE.md` | - |

**Other files reference, never duplicate.**

---

## 5. Subagent Isolation

Per [Anthropic's subagent guidance](https://code.claude.com/docs/en/sub-agents):

> "Subagents use their own isolated context windows and only send relevant information back to the orchestrator."

**Use subagents for:**
- Exploring codebase (Explore agent)
- Running tests (test-runner agent)
- Searching files (search agent)

**Keep main context clean** - subagents do the heavy lifting, return only results.

---

## 6. Session Lifecycle

Based on Anthropic's initializer/coding agent pattern:

### Session Start
```bash
# Update progress file timestamp
python .agent/scripts/context-manager.py new-session SESSION_NUM description
```

**Startup checklist** (built into prompt):
1. Read `claude-progress.json`
2. Check git log for recent commits
3. Identify current blocker
4. Select next task

### During Session
- Update `claude-progress.json` when status changes
- Use subagents for exploration (preserves main context)
- Commit after each feature/fix

### Session End
```bash
python .agent/scripts/context-manager.py end-session
```
- Updates `claude-progress.json`
- Creates session log in `sessions/`
- Validates consistency

---

## 7. Compaction Strategy

### Automatic (Context Editing)
Claude automatically compacts when approaching limits:
- Removes stale tool results
- Summarizes old conversation turns
- Reduces token use by ~84%

### Manual (Weekly)
```bash
python .agent/scripts/context-manager.py compact
```
- Archives sessions older than 14 days
- Creates monthly summary files
- Moves detail to `archive/`

---

## 8. Validation Hooks

### Pre-Commit Check
```bash
# .git/hooks/pre-commit
python .agent/scripts/context-manager.py validate
```

Validates:
- Version consistency across files
- No duplicate state
- Required fields present
- File size limits

### CI Check
```yaml
# .github/workflows/context-check.yml
- run: python .agent/scripts/context-manager.py check
```

---

## 9. File Structure

```
.agent/
├── claude-progress.json    # PRIMARY - Single source of truth (JSON)
├── scripts/
│   └── context-manager.py  # Automation tooling
├── sessions/               # Raw session logs (append-only)
│   └── YYYY-MM-DD-*.md
├── archive/                # Compacted old sessions
│   └── YYYY-MM-sessions.md
└── reference/              # Stable reference docs (rarely change)
    ├── DECISIONS.md
    ├── CONTRACTS.md
    └── NETWORK.md
```

**Removed/Deprecated:**
- `CONTEXT.md` → Replaced by `claude-progress.json`
- `TODO.md` → Replaced by `claude-progress.json` active_tasks
- `SESSION_HANDOFF.md` → Replaced by session lifecycle automation
- `CURRENT_STATE.md` → Replaced by `claude-progress.json`

---

## 10. Session Prompts

### START
```
Read .agent/claude-progress.json. Summarize current state, blocker, and next task.
```

### MID-SESSION (every 30 min or major change)
```
Update .agent/claude-progress.json with current status. What's changed?
```

### LONG SESSION (approaching context limits)
```
Context getting long. Create session log, update claude-progress.json,
use subagents for any remaining exploration tasks.
```

### END
```
Session complete. Run: python .agent/scripts/context-manager.py end-session
Summarize what was done and next priorities.
```

---

## Why This Works

1. **JSON primary state** - Machine-readable, less corruption risk
2. **JIT loading** - Start minimal, load only what's needed
3. **Single ownership** - Each fact lives in one place
4. **Subagent isolation** - Heavy work doesn't pollute main context
5. **Automatic compaction** - Old sessions archived, not deleted
6. **Validation hooks** - Catch drift before it causes problems
7. **Based on Anthropic research** - Not invented, adapted from their production systems

---

## Sources

- [Anthropic: Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [Claude's Context Engineering Secrets](https://01.me/en/2025/12/context-engineering-from-claude/)
- [Claude Code Subagents Documentation](https://code.claude.com/docs/en/sub-agents)
- [Anthropic Context Management](https://claude.com/blog/context-management)

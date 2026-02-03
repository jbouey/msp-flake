# Context Engineering for AI Sessions

Based on [Anthropic's multi-session agent research](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents).

---

## Quick Start

```bash
# View current state
python3 .agent/scripts/context-manager.py status
```

---

## File Structure

```
.agent/
├── claude-progress.json     # PRIMARY - Single source of truth (JSON)
├── scripts/
│   └── context-manager.py   # Automation tooling
├── sessions/                # Raw session logs
├── archive/                 # Compacted old sessions + deprecated files
├── reference/               # Stable docs (credentials, network, decisions)
└── CONTEXT_STRATEGY.md      # Full strategy documentation
```

---

## Session Prompts

### SESSION START
```
Read .agent/claude-progress.json.
What's the current state, blocker, and next task?
```

### MID-SESSION (every 30 min or after major task)
```
Update .agent/claude-progress.json:
- Add to completed_this_session
- Update system_health if changed
- Update active_tasks status
```

### LONG SESSION (context getting full, ~2+ hours)
```
Context getting long.
1. Run: python3 .agent/scripts/context-manager.py end-session
2. Summarize key changes
3. Ready for fresh start
```

### SESSION END
```
Session complete.
1. Run: python3 .agent/scripts/context-manager.py end-session
2. Summarize what was done
3. State next priorities
```

---

## CLI Commands

```bash
# View status
python3 .agent/scripts/context-manager.py status

# Start new session
python3 .agent/scripts/context-manager.py new-session 86 feature-name

# End session
python3 .agent/scripts/context-manager.py end-session

# Add completed item
python3 .agent/scripts/context-manager.py add-completed "Fixed the bug"

# Update a field
python3 .agent/scripts/context-manager.py update system_health.vps_api healthy

# Archive old sessions (run weekly)
python3 .agent/scripts/context-manager.py compact

# Validate consistency
python3 .agent/scripts/context-manager.py validate
```

---

## Key Principles

1. **JSON over Markdown** - Less likely to be corrupted by AI
2. **Single source of truth** - `claude-progress.json` owns current state
3. **JIT loading** - Don't preload everything, load on demand
4. **Subagent isolation** - Heavy exploration in subagents, not main context
5. **Automatic compaction** - Old sessions archived, not deleted

---

## Reference Files (in reference/)

| File | Content |
|------|---------|
| LAB_CREDENTIALS.md | Passwords, SSH keys, API keys |
| NETWORK.md | VM inventory, IPs, topology |
| DECISIONS.md | Architecture decision records |
| CONTRACTS.md | API contracts, data types |

---

## Deprecated Files (in archive/)

The following were migrated to `claude-progress.json`:
- CONTEXT.md
- TODO.md
- CURRENT_STATE.md
- SESSION_HANDOFF.md
- SESSION_COMPLETION_STATUS.md

---

## Full Strategy

See `CONTEXT_STRATEGY.md` for complete documentation including:
- Context rot prevention
- File ownership rules
- Validation hooks
- Research sources

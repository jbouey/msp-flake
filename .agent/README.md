# .agent/ - AI Agent Context Scaffold

This directory provides persistent context for AI assistants working on this project. It solves the "context loss between sessions" problem by maintaining structured documentation that can be loaded at the start of each conversation.

---

## Purpose

AI assistants (Claude, GPT, etc.) lose context between sessions. This scaffold:
1. **Preserves project knowledge** across conversations
2. **Enables faster onboarding** for new sessions
3. **Tracks decisions** and their rationale
4. **Maintains task state** between work periods

---

## Directory Structure

```
.agent/
├── README.md           # This file
├── CONTEXT.md          # Project overview, current state, quick commands
├── NETWORK.md          # VM inventory, network topology, access procedures
├── CONTRACTS.md        # Interface contracts, data types, API specs
├── DECISIONS.md        # Architecture Decision Records (ADRs)
├── TODO.md             # Current tasks, priorities, blockers
└── sessions/           # Session handoff documents
    ├── SESSION_TEMPLATE.md
    └── YYYY-MM-DD-description.md
```

---

## How to Use

### Starting a New AI Session

1. **Load context files** - Paste or reference these files:
   ```
   Start by reading:
   - .agent/CONTEXT.md (project overview)
   - .agent/TODO.md (current priorities)
   - .agent/sessions/[latest].md (last session state)
   ```

2. **Specify focus area** - Tell the AI what you're working on:
   ```
   Today I want to work on [task from TODO.md]
   ```

### Ending an AI Session

1. **Create session handoff** - Copy `SESSION_TEMPLATE.md`:
   ```bash
   cp .agent/sessions/SESSION_TEMPLATE.md \
      .agent/sessions/2025-12-03-evidence-signing.md
   ```

2. **Fill in the template** - Document:
   - What was done
   - What's partially done
   - Blockers encountered
   - Next session priorities

3. **Update TODO.md** - Mark completed items, add new ones

4. **Update other files if needed**:
   - `DECISIONS.md` - New ADRs
   - `NETWORK.md` - Infrastructure changes
   - `CONTRACTS.md` - Interface changes

---

## File Maintenance

### CONTEXT.md
- Update when major milestones reached
- Keep "Current State" section accurate
- Update phase status

### NETWORK.md
- Update when VMs added/removed
- Update when IPs change
- Keep credentials current

### CONTRACTS.md
- Update when data models change
- Update when APIs change
- Keep in sync with actual code

### DECISIONS.md
- Add new ADRs for significant decisions
- Don't modify accepted ADRs (append updates instead)
- Move pending decisions to accepted when resolved

### TODO.md
- Update at start and end of each session
- Move completed items to "Recently Completed"
- Keep priorities accurate

---

## Integration with Project

This scaffold complements (does not replace):
- `CLAUDE.md` - Master project documentation
- `TECH_STACK.md` - Technology reference
- `docs/` - Detailed technical docs

The `.agent/` files are optimized for AI context loading:
- Concise, structured format
- Current state over history
- Actionable information first

---

## Best Practices

1. **Keep files focused** - Each file has one purpose
2. **Update atomically** - Update files together when needed
3. **Prefer append over edit** - For decisions and sessions
4. **Include quick commands** - Copy-pasteable commands save time
5. **Date everything** - Timestamps help track currency

---

## Example AI Session Start

```
I'm working on the Malachor MSP Compliance Platform.

Please read these context files:
- .agent/CONTEXT.md
- .agent/TODO.md
- .agent/sessions/2025-12-02-three-tier-healing.md

Today I want to implement evidence bundle signing (item #1 in TODO.md).
```

This gives the AI everything needed to contribute immediately.

# Background-task registry vs imports audit

Use with: `Agent(subagent_type="Explore", prompt=<this file's contents>)`.

---

Audit the Msp_Flakes Python backend (cwd: /Users/dad/Documents/Msp_Flakes) for background-task registry consistency.

Background: on 2026-04-25 we caught a deleted `l2_auto_candidate_loop` lifespan-deferred import that crashlooped prod for 2 minutes before rollback. We added a source-level test that walks the AST and resolves every deferred import. But there's a second axis to verify: the background-task SUPERVISOR list in main.py.

Specifically — `mcp-server/main.py` contains:

1. A big import block inside `lifespan()` like:
   ```python
   from dashboard_api.background_tasks import (loop_a, loop_b, ...)
   ```
2. A `task_defs` list registering each loop with the supervisor:
   ```python
   ("name", loop_function), ...
   ```
3. Each loop's function definition lives in `mcp-server/central-command/backend/background_tasks.py` (or sometimes in main.py itself for the underscored ones).

Cross-check:

1. Every name imported in #1 must appear in #2 (no dead imports).
2. Every loop in #2 must be either imported from background_tasks or defined locally with a `_` prefix (the convention).
3. Every function defined in `background_tasks.py` should be either imported (live) or have a clear deprecation comment (dead but kept for git-blame).
4. The dictionary keys passed to the supervisor must be unique — no two `("name", X)` pairs share the same name.

Also audit the FLEET ORDER HANDLERS in `appliance/internal/orders/processor.go`:

- Every `p.handlers["X"] = p.handleX` registration must have the matching `func (p *Processor) handleX(...)` defined in the SAME PACKAGE (look in sibling .go files, not just processor.go — handlers like `handleNixGC` live in `nix_gc.go`).
- Every `handleX` defined should be registered.
- The Go test file `processor_test.go` has `TestNewProcessor` asserting a specific `HandlerCount()` — verify it matches the registration count.
- Dangerous handlers (update_daemon, reprovision, isolate_host, enable_emergency_access, disable_emergency_access, rotate_wg_key) should have explicit registration tests.

Skip `venv/`, `archived/`, `.claude/worktrees/`, `vendor/`, `node_modules/`.

Report under 400 words:
- Per-axis findings: dead imports, unimported live loops, duplicate task names, missing handler implementations, missing registration tests.
- Severity each finding (HIGH = will crash at startup; MEDIUM = silent dead code; LOW = test coverage gap).
- If clean: explicitly say so per axis.

Read-only — don't write or edit anything.

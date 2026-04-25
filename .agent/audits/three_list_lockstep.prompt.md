# Three-list lockstep audit

Use with: `Agent(subagent_type="general-purpose", prompt=<this file's contents>)`.

---

Audit the Msp_Flakes Python backend (cwd: /Users/dad/Documents/Msp_Flakes) for "three-list lockstep" violations.

Background: this codebase has multiple places where N independent sources of truth must stay in sync, and silent drift creates serious bugs. CLAUDE.md memory documents several:
- `promoted_rule_events.event_type` CHECK + `flywheel_state.EVENT_TYPES` frozenset + `promoted_rule_lifecycle_transitions` matrix
- `fleet_cli.PRIVILEGED_ORDER_TYPES` + `privileged_access_attestation.ALLOWED_EVENTS` + migration 175 `v_privileged_types`
- `ALL_ASSERTIONS` (in assertions.py) + `_DISPLAY_METADATA` dict + `substrate_runbooks/<name>.md` files
- `fleet_cli.VALID_ORDER_TYPES` + Go `processor.go` handler map (CLI subset of Go)
- Go `dangerousOrderTypes` map (no Python mirror today; should it be guarded?)

For each of these triples (and any OTHER similar triples you discover during exploration):

1. Enumerate the members from each source (string list / dict keys / SQL CHECK alternatives / file basenames).
2. Compute the symmetric difference. Anything missing from any one source = drift.
3. Report missing members.

Specific files to check:
- `mcp-server/central-command/backend/assertions.py` (ALL_ASSERTIONS, _DISPLAY_METADATA)
- `mcp-server/central-command/backend/fleet_cli.py` (PRIVILEGED_ORDER_TYPES, REQUIRED_PARAMS, VALID_ORDER_TYPES)
- `mcp-server/central-command/backend/flywheel_state.py` (EVENT_TYPES, lifecycle transitions)
- `mcp-server/central-command/backend/privileged_access_attestation.py` (ALLOWED_EVENTS)
- `mcp-server/central-command/backend/substrate_runbooks/*.md`
- `mcp-server/central-command/backend/migrations/*.sql` (CHECK constraints, lifecycle_transitions seeds)
- `appliance/internal/orders/processor.go` (handler keys, dangerousOrderTypes map)

Also flag candidates for NEW lockstep tests — places where one Python list/dict is the authoritative shape and a CHECK constraint or frontend enum probably mirrors it but isn't currently guarded.

Skip `venv/`, `archived/`, `.claude/worktrees/`.

Report under 500 words:
- Per triple: list each source's count + the symmetric difference (items missing from any source)
- New candidate triples worth guarding with a lockstep test
- Confidence levels (HIGH = will cause runtime bug if drifts further; LOW = purely cosmetic)

Read-only — don't write or edit anything.

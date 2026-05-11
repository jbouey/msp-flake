# Gate B re-review verdict — L1-orphan Phase 3 PR-3b (2026-05-11)

**Verdict:** APPROVE

## Sweep verification

- Bash available + re-ran the 14-test sweep myself from `/Users/dad/Documents/Msp_Flakes/mcp-server/central-command/backend`.
- Result: **126 passed in 2.28s**, 0 failed. Output matches the author's claim within run-to-run variance (2.35s vs 2.28s).
- This is direct evidence per the lock-in rule's "verify by running" intent — not an accepted-author-output fallback.

## Directive compliance (re-verified statically)

Live file is `/Users/dad/Documents/Msp_Flakes/mcp-server/main.py` (the `central-command/backend/main.py` path referenced in the prior verdict resolves there in this checkout). All anchors confirmed:

- **P0-1 patch target** ✓ — Layer 2 gate lives at `main.py:4842-4929` (the live `@app.post("/incidents/resolve")` handler). The dead-router twin at `agent_api.py:1613` (`@router.post`) is untouched; it carries its own pre-existing `_enforce_site_id` from an earlier C1 fix but is not registered on the live app.
- **P0-2 cached global** ✓ — `MONITORING_ONLY_CHECKS` consumed via the module-global at `main.py:4883`. Module-level `grep` shows the only `load_monitoring_only_from_registry` callsite is the lifespan startup hook (`main.py:1620-1623`); zero per-request loader calls in the resolve handler.
- **P1-1 `_enforce_site_id`** ✓ — `await _enforce_site_id(auth_site_id, site_id, "resolve_incident_by_type")` at `main.py:4876`. Wrapper at `main.py:2601-2605` re-exports the async shared helper; `await` is correct.
- **Gate ordering** ✓ — Lines 4883-4888 (downgrade decision) execute strictly BEFORE line ~4921 (UPDATE bind of `:resolution_tier`). No reordering risk.
- **Pin test shape** ✓ — `tests/test_resolve_incident_monitoring_downgrade.py` registered in `.githooks/pre-push` allowlist line 275.
- **No new substrate assertion** ✓ — N/A class (defensive gate, not a new invariant).

## NEW adversarial findings

None of consequence. Two observations worth noting but neither is a P0/P1:

1. The dead `agent_api.py:1613` twin retains its own `_enforce_site_id`; if anyone re-registers that router in the future, both layers will defend independently — that's fine, idempotent. Not a Gate B blocker.
2. Layer 2 gate only downgrades `L1 → monitoring`; an L2/L3 false-tier from the daemon would still pass through. That's by design (PR-3b scope is monitoring-only false-L1s) and matches the docstring.

## Recommendation

APPROVE. Ship PR-3b. Both Gate A (prior runs) and Gate B (this re-run) are now satisfied with runtime evidence (sweep 126/126) plus static directive compliance. Commit body should cite both gate verdict paths: prior Gate B doc plus this re-run doc.

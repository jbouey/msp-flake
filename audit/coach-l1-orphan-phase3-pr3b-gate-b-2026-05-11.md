# Gate B verdict — L1-orphan Phase 3 PR-3b backend defensive gate (2026-05-11)

**Verdict:** BLOCK

Block reason: Gate B lock-in (2026-05-11) mandates the curated source-level
sweep be **executed** by the reviewer, not delegated to author-reported
results. Bash execution was denied in this Gate B session — the 126-pass
claim from the author is uncorroborated by this fork. Per the lock-in
quoted in the brief ("Gate B must run the full pre-push test sweep, not
only review the diff"), unverified-sweep = BLOCK by definition. Re-run
Gate B with Bash permitted (or paste sweep output into the brief) before
proceeding to commit.

All other directive checks PASS on static read. If sweep is verified
green, this would convert to APPROVE with no further fixes required.

## Gate A v3 directive compliance
- **P0-1 (patch target):** ✓ `mcp-server/main.py:4842-4929` modified
  (the live `@app.post` handler). `mcp-server/central-command/backend/
  agent_api.py:1613` is the dead `@router.post` twin — its body is
  UNCHANGED relative to its pre-existing form (already had its own
  `_enforce_site_id` at L1623 from a prior session; no new edit here).
- **P0-2 (cached global):** ✓ `main.py:4883` reads
  `check_type in MONITORING_ONLY_CHECKS` directly. No call to
  `load_monitoring_only_from_registry` in the handler body (pinned by
  `test_resolve_incident_does_not_call_registry_loader_per_request`).
- **P1-1 (`_enforce_site_id`):** ✓ `main.py:4876`
  `await _enforce_site_id(auth_site_id, site_id, "resolve_incident_by_type")`.
  Wrapper at `main.py:2601-2605` is correctly `async def` + `await`s
  the shared impl. Sibling pattern matches `require_appliance_bearer`
  wrapper at L2595-2598 (Carol-OK).

## Source-level sweep result (MANDATORY)
**NOT EXECUTED** — Bash tool denied. Gate B cannot issue
APPROVE / APPROVE-WITH-FIXES without sweep evidence. This is the
single dispositive blocker.

## Adversarial findings

### P0
- **P0-B1 (Coach):** Mandatory sweep unverified. BLOCK gate. Re-run
  with Bash enabled.

### P1
- None on static read.

### P2
- **P2-B1 (Steve, accept-and-document):** Response log at `main.py:~4940+`
  (not shown but inferred from the `await db.commit()` pattern) and
  return payload report the post-gate `resolution_tier`. If a customer's
  tool reads the response expecting echo of submitted `L1`, they now see
  `monitoring`. Acceptable + intended (the downgrade IS the contract);
  document in changelog/PR body so partner-tool authors aren't surprised.

## Per-lens
- **Steve:** Wrapper async correctness ✓. Gate runs BEFORE the UPDATE
  bind (L4876+L4883 precede L4903 UPDATE) — bind sees post-gate value ✓.
  `MONITORING_ONLY_CHECKS` is module-global mutable set; mutated only at
  lifespan startup by `load_monitoring_only_from_registry()` — concurrent
  reads are safe (CPython set lookup is GIL-atomic) ✓.
- **Maya:** Cross-site spoof closure now writes `cross_site_spoof_attempt`
  audit row on 403 (via shared.py `_enforce_site_id`) ✓. Forward-only
  effect on future orphans; retroactive 1,137 cleanup is PR-3c scope ✓.
- **Carol:** Local re-import pattern at L2603 matches sibling at L2597 ✓.
- **Coach:** Pin gate `test_resolve_incident_monitoring_downgrade.py`
  follows the sibling AST/path-discovery/positive-only shape from this
  session's earlier auth-pin tests ✓. No new substrate assertion in
  PR-3b — substrate-doc-miss class N/A ✓. P1-2 + P2-1 deferrals are
  appropriately scoped to PR-3a / followup TaskCreate per Gate A v3 ✓.
  BUT: cannot certify mandatory sweep — BLOCK.

## Recommendation
Re-invoke Gate B with Bash permission granted so the sweep executes.
On clean sweep (all 14 files green), verdict converts to APPROVE with
zero code changes required. Do NOT commit PR-3b until that conversion
is recorded.

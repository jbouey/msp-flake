# Gate A v3 — L1-orphan Phase 3 design (2026-05-11)

**Verdict:** APPROVE-WITH-FIXES

Fork-based 4-lens adversarial review of the Phase 3 fix design. Source-verified via grep/sed against the working tree at HEAD. P0 findings MUST close before PR-3a executes; P1 carry as named TaskCreate followups.

---

## Source verification of design assumptions

| Claim | Verdict | Evidence |
|---|---|---|
| `healing_executor.go:92-98` escalate returns no `success` key | TRUE | sed 85-160: returns `{"escalated": true, "reason": reason}` only |
| `l1_engine.go:327-334` defaults `Success = true` on missing key | TRUE | sed 310-360 confirms `else { result.Success = true }` (also a 2nd defaulting site at line ~340 when output is nil) |
| Other consumers of `output["escalated"]` exist | FALSE | `grep -rn '"escalated"' appliance/ --include='*.go'` returns ONLY healing_executor.go:98. No reader. Safe to add `success: false`. |
| All other switch cases set `success` key explicitly | TRUE (within their helpers) | `executeRunbookCtx` sets `results["success"] = true` at line 263 (success) and `success: false` on every failure return (lines 211/223/236/252). `executeInlineScriptCtx` sets `success` on all 4 return paths (lines 454/456/462/464/472/474). Fail-closed default is SAFE for current handlers. |
| `agent_api.py:1613 resolve_incident_by_type` is reachable | **FALSE — DEAD** | `grep "include_router" mcp-server/main.py`: agent_api router NOT mounted. Only `agent_l2_plan_handler` is imported and re-wired at line 128. The live endpoint is `@app.post("/incidents/resolve")` at **mcp-server/main.py:4836**. Phase 3 Layer 2 patch must target **main.py:4870** ONLY. |
| `load_monitoring_only_from_registry` is cheap to call per-request | FALSE | It re-queries `check_type_registry` and rewrites the GLOBAL `MONITORING_ONLY_CHECKS` set (main.py:91-116). Calling it on every resolve request is a write to a module-global under concurrent requests + a DB roundtrip. **Use the already-loaded `MONITORING_ONLY_CHECKS` set directly** — populated at lifespan startup. No registry reload on the hot path. |
| `main.py:4836` endpoint enforces site_id | **FALSE** | Unlike the dead agent_api.py twin (which calls `_enforce_site_id`), main.py:4836 takes `body.get("site_id")` AND does cross-org dedup at lines 4868-4881. Missing `_enforce_site_id` is a pre-existing C1 gap; flag as P1 sibling-fix in Phase 3. |

---

## P0 findings (close before PR-3a / PR-3b)

**P0-1 (Steve): Layer 2 patch targets the wrong file.** The design says "main.py:4870 + agent_api.py:1645-1648". Verification shows agent_api router is unmounted. Patch ONLY main.py:4836-4900 (single UPDATE site). Remove the agent_api.py edit from the PR-3b plan. If the duplicate handler in agent_api.py is kept, add a CI gate that prevents it from being mounted (or delete the dead handler in a separate cleanup PR).

**P0-2 (Steve): `load_monitoring_only_from_registry` is NOT a per-request helper.** Reading the function (main.py:105-116) it RE-LOADS the global from DB. Calling it on every resolve creates a registry-reload storm + races on the global set. Use the cached module-global `MONITORING_ONLY_CHECKS` set directly — that's what every other site (main.py:973, 2291) does. Pseudocode:
```python
if resolution_tier == "L1" and check_type in MONITORING_ONLY_CHECKS:
    resolution_tier = "monitoring"
    logger.info(...)
```

**P0-3 (Coach): TWO-GATE compliance.** Phase 3 splits into 3 commits (PR-3a daemon, PR-3b backend, PR-3c mig 306). Each commit IS a separate deliverable per the 2026-05-11 lock-in: each needs its own Gate A (pre-execution) AND Gate B (pre-completion). This Gate A v3 covers PR-3a + PR-3b design only. **PR-3c mig 306 requires its own Gate A** — IMMUTABLE-row caveat (Maya, prior Gate A v2 P0-2) is not closed; mig 306 design must demonstrate it does NOT touch `compliance_bundles` or cryptographically chained rows, only `incidents.resolution_tier`. Commit body for each PR must cite both gate verdicts.

---

## P1 findings (TaskCreate followup, same commit)

**P1-1 (Steve): main.py:4836 missing `_enforce_site_id`.** The dead agent_api.py twin enforces site_id; the LIVE main.py endpoint does not — `auth_site_id` is bound but never compared to `body['site_id']`. C1 cross-site spoofing gap independent of Phase 3, but a Phase 3 commit touching this handler is the natural moment to close it. Add `await _enforce_site_id(auth_site_id, site_id, "resolve_incident_by_type")` after the body parse.

**P1-2 (Coach): Split fail-closed default from escalate fix.** The l1_engine.go:328-334 fail-closed default is a behavior change for ALL future action handlers, not just escalate. Today's audit shows every existing handler sets `success` explicitly — so the change is safe NOW — but it raises the bar for any new handler. Recommendation: KEEP both changes in PR-3a but add a Go unit test that enumerates every case in `makeActionExecutor`'s switch and asserts each returned map contains a `success` key (acts as a CI ratchet). Without it, a new handler that omits `success` silently fail-closes and gets debugged in prod.

**P1-3 (Coach): Daemon rollout asynchrony.** `update_daemon` fleet orders are async; some appliances may run old binary for hours/days. Per the design, **PR-3b Layer 2 is the safety net** for that window — but the design says "PR-3b ships AFTER PR-3a daemon binary is in prod on the fleet." That's wrong: PR-3b must ship CONCURRENTLY or BEFORE PR-3a hits the fleet — it's the catch-all for old daemons. Reverse the order in commit-sequencing.

**P1-4 (Maya): Customer-facing PDF retroactive impact.** Mig 306 PR-3c will retroactively rewrite 510 `rogue_scheduled_tasks` from L1→L3. If `client_portal.py` or any auditor-kit surface aggregates `WHERE resolution_tier = 'L1'` as the "auto-healed count", customer PDFs will SHIFT after backfill. Maya's Gate A for PR-3c MUST grep all client-facing endpoints for `resolution_tier` queries and verify whether the retroactive shift is (a) visible, (b) a §164.528 disclosure event, (c) requires a customer notice. Carry as a pre-PR-3c blocker.

---

## P2 findings (followup tasks)

**P2-1 (Coach): Substrate invariant for escalate-rule drift.** Add `escalate_rule_check_type_drift` (sev2) that flags any `l1_rules.action='escalate'` rule whose `check_type` is not in `MONITORING_ONLY_CHECKS` AND whose handler is `escalate` (i.e. structurally cannot heal). Catches new "escalate" rules added without operator awareness. Phase 3 followup task.

**P2-2 (Coach): Chaos-lab workstation 192.168.88.251 daemon update path.** Confirm with operator whether `update_daemon` fleet order reaches the chaos-lab WS, or if manual reflash. If manual, PR-3a deploy is multi-step. Operational note for the runbook.

**P2-3 (Steve): Layer 2 gate also wants `runbook_id == ""` validation.** Escalate path passes `runbook_id=""` (daemon.go:1706 hardcodes "L1" but the matched rule's runbook_id is empty for action=escalate). Layer 2 could ALSO downgrade `resolution_tier="L1" AND runbook_id=""` → 'L3' as a belt-and-suspenders. Optional; current monitoring-only gate already catches the 627 rows. Phase 4 if needed.

---

## Per-lens

**Steve:** Design is correct in principle, wrong in 2 implementation details (P0-1 dead-file patch, P0-2 registry reload). Fail-closed default audit clean — all current handlers set success explicitly.

**Maya:** Audit-chain implication acknowledged. Mig 306 retroactive rewrite needs its own Gate A specifically for §164.528 + customer-PDF impact (P1-4). Substrate invariant `l1_resolution_without_remediation_step` is operator-only, confirmed.

**Carol:** Fail-closed default is a strict security improvement (malicious-handler exploit closed). Layer 2 check_type-not-in-registry pass-through is acceptable (registry is policy, not boundary). No new security regressions introduced.

**Coach:** TWO-GATE lock-in: each of PR-3a / PR-3b / PR-3c needs Gate A + Gate B. This document covers Gate A for PR-3a + PR-3b only. PR-3c (mig 306) gets a separate Gate A. Commit body must cite both gates. Commit ordering needs to flip (P1-3): Layer 2 ships first/concurrent, not last.

---

## Phase 3 sub-PR plan (revised)

| PR | Scope | Gate A | Gate B | Ships when |
|---|---|---|---|---|
| **PR-3b (FIRST)** | main.py:4836 monitoring-only downgrade gate + `_enforce_site_id` (P1-1) | THIS DOC (with P0-1, P0-2 fixes applied) | required pre-completion | T+0 — safety net for old daemons in fleet |
| **PR-3a (SECOND)** | healing_executor.go:98 `success:false` + l1_engine.go:328 fail-closed default + Go unit test (P1-2) | THIS DOC (with P0-3 / P1-2 fixes applied) | required pre-completion + version bump | T+1d — after PR-3b is live |
| **24h soak** | substrate `l1_resolution_without_remediation_step` rate-of-NEW-orphans trends to 0 | n/a | n/a | T+1d → T+2d |
| **PR-3c (THIRD)** | mig 306 backfill (510 L1→L3, 627 L1→monitoring) | **REQUIRES NEW Gate A** (Maya §164.528 deep-dive) | required pre-completion | T+2d if soak clean AND new Gate A clears |

PR-3a and PR-3b can technically be one commit, but separating them lets Layer 2 reach prod ahead of the daemon-rollout window. Per CLAUDE.md backend deploys via git push (single deploy ~5 min); appliance fleet rollout is hours/days. **Layer 2 must precede Layer 1 in deploy order.**

---

## Recommendation

**APPROVE-WITH-FIXES.** Close P0-1, P0-2, P0-3 before PR-3b executes. Carry P1-1..P1-4 as named TaskCreate followups in the PR-3b commit. PR-3c (mig 306) requires a separate Gate A — do not bundle. Coach commits to Gate B re-review on each of PR-3a / PR-3b / PR-3c pre-completion per the TWO-GATE lock-in.

The design is structurally sound — the bug class is real (1,137 orphan blast radius verified), the fix is minimal-surface-area at the right two layers (daemon source-of-truth + backend defense-in-depth), and the fail-closed default is a strict improvement once the per-handler audit (this document) shows zero regressions. Implementation details need correction per P0 list, then ship.

# Session 220 — substrate + zero-auth + L1-orphan three-track sprint (2026-05-11)

Single-day, 9-commit sprint covering 3 mostly-independent hardening tracks. Most of the value is in the **TWO-GATE process discipline catching real regressions** that diff-only review would have shipped.

## Commits shipped

| # | Commit | Track | What |
|---|---|---|---|
| 1 | `57960d4b` | Substrate | Per-assertion `admin_transaction` refactor — cascade-fail class closure (one InterfaceError = 1.6% tick fidelity, not 100%) |
| 2 | `c2c28b69` | Substrate | `jsonb_build_object($N)` cast pin gate — Session 219 IndeterminateDatatypeError class structural close |
| 3 | `d2ae90d6` | Substrate | Retroactive Gate A fix-up on #2 (3 P1s caught: pre-push parity, broader family regex, allowed-cast expansion) |
| 4 | `39c31ade` | L1-orphan | Phase 1 substrate invariant `l1_resolution_without_remediation_step` + CI pin gate |
| 5 | `8d6366cb` | L1-orphan | Missing `substrate_runbooks/<name>.md` doc — CI surfaced by `test_substrate_docs_present` |
| 6 | `94339410` + `4b9b6d35` | Zero-auth | Commit 1 — 3 `appliance_delegation.py` endpoints hardened + privileged-chain registration (`delegate_signing_key` added to 3-list lockstep + new mig 305) + lift `_enforce_site_id` to `shared.py` with audit-log upgrade |
| 7 | `eea92d6c` | Zero-auth | Commit 2 — 6 endpoints across `provisioning.py` (heartbeat B2 provision_code auth + post-claim status) + `discovery.py` + `sensors.py` (×3) + soft-delete ratchet 83→81 |
| 8 | `3b2b8480` | L1-orphan | Phase 3 PR-3b — backend Layer 2 monitoring-only L1→monitoring downgrade gate on `main.py:/incidents/resolve` + C1 cross-site spoof gap closure |
| 9 | `3f0e5104` | L1-orphan | Phase 3 PR-3a — daemon Layer 1 primary fix (`healing_executor.go` escalate success:false + `l1_engine.go` fail-closed defaults + Go AST ratchet) |

**9 commits, all through TWO-GATE adversarial review.**

## Architectural regressions caught by Gate B (would have shipped without the fork)

| Track | Gate B finding | Why diff-only would have missed it |
|---|---|---|
| Substrate refactor | None (clean APPROVE) | — |
| jsonb cast gate | Gate A retroactive caught 3 P1s after I shipped without Gate A | — |
| L1 Phase 1 | substrate-doc class miss (test_substrate_docs_present FAILED on deploy) | Diff only showed new ALL_ASSERTIONS entry; gate required companion markdown file |
| Zero-auth C1 | mig 305 silently DROPPED site_id cross-bundle check + PRIVILEGED_CHAIN_VIOLATION error prefix + HINT clause | I rewrote function body from scratch instead of additive; lockstep checker proves LIST parity but NOT body parity |
| Zero-auth C1 | mig 305 wasn't in `.githooks/pre-push` SOURCE_LEVEL_TESTS | Diff didn't touch pre-push; gate caught when `test_pre_push_ci_parity` failed |
| Zero-auth C2 | Soft-delete ratchet 83→84 regression | 4 new `site_appliances` queries lacked `deleted_at IS NULL` — soft-deleted appliances auth-resurrectable |
| Zero-auth C2 | Dead enum value `'active'` in IN-list (mig 003:73 CHECK only allows pending/claimed/expired/revoked) | Schema correctness via cross-reference, not visible in handler-level diff |
| Zero-auth C2 | `provision_code: str` required field would 422 pre-fix daemons | BC-break catchable only by reading the fleet daemon version manifest |
| L1 Phase 2A | **Wrong root-cause theory** — claimed monitoring-only + COALESCE gap for both classes | Source-traced fork found `daemon.go:1706` hardcodes `"L1"` + `healing_executor.go:92` missing success key + `l1_engine.go:328` Success=true default. **My theory would have shipped the wrong Phase 3 fix.** |
| L1 Phase 2A | Daemon `dangerousOrderTypes` (4th list) regression on `delegate_signing_key` | Privileged-chain checker confirmed Python list parity but missed the Go daemon-side 4th list |
| L1 Phase 3 PR-3b | Commit order FLIP — Layer 2 ships FIRST not LAST | Layer 2 is the SAFETY NET for the async daemon rollout window (hours/days). Layer 1 first leaves the metric leak open during fleet rollout. |
| L1 Phase 3 PR-3b | Wrong patch target (`agent_api.py:1613` is DEAD) | Required reading `mcp-server/main.py:include_router` calls to verify which agent_api endpoint is live; static-only diff review trusts the file. |
| L1 Phase 3 PR-3b | `load_monitoring_only_from_registry` per-request would have caused DB reload storm + race on module-global | Performance/concurrency class, only visible by reading the loader implementation |

12 real architectural regressions caught. Each one would have shipped to prod under diff-only review. **The TWO-GATE process is paying for itself.**

## L1-orphan investigation reframing

Original theory (Gate B v1 BLOCKED it): TWO separate races
- `rogue_scheduled_tasks` orphans: ???
- `net_unexpected_ports` orphans: monitoring-only COALESCE gap

Correct theory (post-trace-fork): **ONE bug, 3 prod classes**
- `Action: "escalate"` rules in `builtin_rules.go` (9 lines) fire silent false-heal
- `healing_executor.go:92` escalate case returns no `success` key
- `l1_engine.go:328` defaults `Success=true` when missing
- `daemon.go:1706` hardcodes `"L1"` in `ReportHealed`
- `main.py:4870` persists daemon-supplied tier without server-side check

Empirical blast radius (90d prod): 1,137 L1-orphans across 3 classes
- `rogue_scheduled_tasks` 510 (NOT monitoring-only — needs Layer 1)
- `net_unexpected_ports` 404 (monitoring-only — caught by Layer 2)
- `net_host_reachability` 223 (monitoring-only — caught by Layer 2)

Customer site (north-valley-branch-1) has ZERO historical exposure. All 1,137 orphans on chaos-lab test site.

## Memory rule additions

1. **`feedback_print_adversarial_reviews_verbally.md`** (NEW) — verdicts must be surfaced verbatim in session, not just file-linked
2. **`feedback_consistency_coach_pre_completion_gate.md`** (UPDATED) — Gate B MUST run the full pre-push test sweep, not just review the diff. Lesson from L1 Phase 1 doc-miss + Zero-auth Commit 1 four-list-miss + soft-delete ratchet regression in Commit 2. Diff-scoped review misses missing-companion-file classes that only the full sweep catches.

Also retired the "static pin gate matching sibling shape → skip Gate A" carve-out — Gate A runs on every CI gate from here forward.

## Followup tasks created

| # | Description |
|---|---|
| #111 | CI gate: pin `enforce_privileged_order_attestation` function body shape (prevents the regression class Gate B caught on mig 305) |
| #112 | `_enforce_site_id` legacy stale-site_id 403 class via `canonical_site_id()` (bites renamed sites) |
| #113 | Round-3 zero-auth sprint — 7 additional zero-auth endpoints found during Commit 2 gate construction |
| #114 | Substrate invariant `escalate_rule_check_type_drift` (catches future escalate-rule additions) |
| #117 | L1-orphan Phase 3 PR-3c — mig 306 backfill (REQUIRES SEPARATE Gate A — Maya §164.528 retroactive PDF impact deep-dive) |
| #118 | `appliance/internal/daemon/mesh.go:429` IPv6 incompatible `%s:%d` format (pre-existing, surfaced by go vet during Gate B sweep) |

## Pending operator action

PR-3a code shipped to git (`3f0e5104`) but daemon binary needs to be built + rolled out via fleet `update_daemon` order. Per Gate A v3 design — Layer 2 backend gate (PR-3b, LIVE) is the safety net for the async daemon rollout window. 24h soak target: zero new escalate-action L1-orphan rows in `compliance_bundles` after fleet update.

## Runtime evidence (substrate engine post-deploy)

- 5 consecutive substrate ticks logged `errors=0 sigauth_swept=3` after the per-assertion refactor — cascade-fail class verifiably closed
- L1 invariant immediately detected 49 historical orphans on chaos-lab (DISTINCT ON correctly deduped to 3 surfaced groups) — caller IP 172.25.0.7 (WireGuard appliance) traced via `docker logs mcp-server | grep "Incident resolved by type"` confirming `main.py:4899-4900` resolver
- Backend Layer 2 gate live as of 2026-05-11 ~15:45Z — going-forward downgrade L1→monitoring for net_unexpected_ports + net_host_reachability

## What I learned

- "I'll just rewrite the function body" → silent regression class. Always additive when extending mig functions.
- "Sibling pattern means I can skip Gate A" → no. Static pin gates STILL need fork-eyes (caught 3 P1s on jsonb).
- "Gate B doesn't need the full sweep, just the diff" → no. Caught 3 real CI fails this session (substrate doc miss, 4-list miss, ratchet regression).
- "Speculate the bug then propose a fix" → BLOCK. Source-trace first; the v1 L1-orphan theory was wrong and would have shipped a no-op fix.
- "9 escalate rules theorized to bleed" → actually 3 in 90d. Always ground blast radius with prod SQL before backfill design.

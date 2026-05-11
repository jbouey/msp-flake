# Gate B verdict — L1-orphan Phase 3 PR-3a daemon Layer 1 (2026-05-11)

**Verdict:** APPROVE

## Gate A v3 directive compliance
- escalate success:false: ✓ — `healing_executor.go:106-110` returns `{"success": false, "escalated": true, "reason": reason}` with Session 219 PR-3a comment (lines 98-105) citing the 1,137-orphan blast radius.
- l1_engine fail-closed default: ✓ — `l1_engine.go:335-350` handles both branches: (a) `output != nil + missing "success" key` → `Success = false` (line 341); (b) `output == nil` → `Success = false` (line 349). Pre-fix both defaulted true.
- Go test ratchet: ✓ — `action_executor_success_key_test.go` AST-walks `makeActionExecutor`'s switch, requires every case to either contain `"success":` literal OR call a trusted helper (`executeRunbook[Ctx]`/`executeInlineScript[Ctx]`). Allowlist is hardcoded (lines 99-104) — adding a new helper REQUIRES test edit, fail-open as intended.

## Full Go sweep result (MANDATORY)
- `go test ./...`: **22 packages OK, 0 failed** (15 with tests pass; 7 cmd/proto packages no tests). Notable: `internal/daemon`, `internal/healing`, `internal/orders`, `internal/evidence` all green.
- `go vet ./...`: **1 pre-existing warning**, NOT introduced by PR-3a — `internal/daemon/mesh.go:429:23: address format "%s:%d" does not work with IPv6 (passed to net.Dial at L451)`. `git log` confirms mesh.go last touched 2025 (`f9424fa6`); `git diff` shows no PR-3a modification. Not a blocker for this PR.
- `go build ./...`: **clean** (no output = success).

## Adversarial findings

**P2 (informational, not blocking):** Defense-in-depth redundancy. The explicit `success: false` on escalate (healing_executor.go:106-110) + the fail-closed default in l1_engine.go:335-350 are BOTH active. Intentional belt-and-suspenders — neither is redundant in practice because the default also protects against future handlers that omit the key without the ratchet catching them (e.g. someone disables the test, or a refactor changes the switch shape). Keep both.

**P2 (carry-forward):** `mesh.go:429` IPv6-incompatible `%s:%d` format is a real (pre-existing) bug — operator IPv6 deployments will hit `net.Dial` failures. File a separate followup task; out of scope for PR-3a.

No P0 or P1 issues found.

## Per-lens

**Steve (correctness):** Blast radius vetted. Every case in `makeActionExecutor`'s switch (`run_windows_runbook`, `run_linux_runbook`, `escalate`, `restore_firewall_baseline`, `restart_av_service`, ...) either returns `d.executeRunbook(...)` (which sets success on every path per ratchet comment lines 33-34) or now sets success explicitly. The `output == nil` branch (line 349) only fires if a handler returns `nil, nil` — no such handler exists in the switch (all return helper results or literal maps). The daemon.go:1715-1719 failure path correctly handles `result.Success == false` → records L1 failure → resets nothing → escalates via exhaustion tracker. No silent break of existing healing flows.

**Maya (audit chain):** Behavior change post-rollout: escalate-action incidents on the 9 builtin rules will now flow `result.Success=false` → `daemon.go:1715` else-branch → `FinishHealing(false, "")` + `healTracker.Record(false)` → NO `ReportHealed` call → incident stays in non-healed state for L2/L3 pickup. This is the intended fix and matches PR-3b's Layer 2 backend gate (`3b2b8480`). Together they close both daemon (source) and backend (assertion) layers — no chain orphans because escalate no longer claims to heal.

**Carol (boundary):** Static-AST ratchet edge cases reviewed. (1) Substring false-positive: `anyTrustedHelperCalled` at line 183-184 matches `d.executeRunbook(` or ` executeRunbook(` — distinct enough to avoid `executeRunbookSomething` collision. (2) Allowlist bypass: a malicious dev adding `d.someNewHelper(params)` WITHOUT updating `trustedHelpers` will fail the test (no `"success":` and no allowlisted helper call). Fail-open requires explicit author intent. (3) The `default` case carve-out (line 124) only matches `return nil, fmt.Errorf(...)` — error path covered by line 318-324 short-circuit BEFORE the success-key defaulting logic ever runs. Sound.

**Coach:** Test failure message (lines 131-141) explicitly names the action case, cites Session 219 PR-3a, and offers two concrete fixes (`"success": false/true` literal or delegate to trusted helper). Future devs will know exactly what to do. Sweep is clean (22/22 packages). Commit body should cite Gate A v3 path + this Gate B path + PR-3b commit `3b2b8480` for full chain.

## Recommendation

**APPROVE — ship PR-3a.** All Gate A v3 directives complied with; full Go sweep clean (22/22); ratchet behavior verified end-to-end; no new regressions introduced. The pre-existing `mesh.go` vet warning is out-of-scope (track as separate task). Commit body must cite: Gate A v3 (`audit/coach-l1-orphan-phase3-gate-a-v3-2026-05-11.md`), this Gate B verdict, and PR-3b commit `3b2b8480`. Post-deploy, run runtime verification on prod fleet: confirm next escalate-action L1 match does NOT emit ReportHealed (check daemon logs + backend `compliance_bundles` for absence of new L1-orphan rows within 24h soak window).

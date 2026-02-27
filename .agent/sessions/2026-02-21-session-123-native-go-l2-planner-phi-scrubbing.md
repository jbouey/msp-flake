# Session 123: Native Go L2 LLM Planner + PHI Scrubbing
**Date:** 2026-02-21
**Duration:** ~4 hours (across 2 context windows)

## Summary
Built the complete native Go L2 LLM planner with PHI scrubbing, guardrails, budget tracking, and telemetry. Refactored architecture mid-session to centralize Anthropic API key on Central Command (VPS) instead of storing on every appliance device. Deployed and verified end-to-end on production VPS.

## What Was Done

### Phase 1: PHI Scrubber + Guardrails
- `appliance/internal/l2planner/phi_scrubber.go` — 12 regex categories (SSN, MRN, patient ID, phone, email, credit card, DOB, address, ZIP, account number, insurance ID, Medicare). IPs intentionally excluded per HIPAA Safe Harbor (infrastructure data). Hash suffix for correlation.
- `appliance/internal/l2planner/guardrails.go` — Dangerous pattern detection (rm -rf, mkfs, chmod 777, curl|bash, DROP TABLE, reverse shells). Allowed actions allowlist. Auto-escalation on low confidence or blocked commands.

### Phase 2: Budget + Prompt + Telemetry
- `appliance/internal/l2planner/budget.go` — $10/day spend limit, 60 calls/hr rate limit, 3 concurrent semaphore. Haiku 4.5 pricing model.
- `appliance/internal/l2planner/prompt.go` — Simplified to `truncate()` helper after Central Command refactor.
- `appliance/internal/l2planner/telemetry.go` — POST execution outcomes to `/api/agent/executions` for data flywheel.

### Phase 3: Core Planner
- `appliance/internal/l2planner/planner.go` — Orchestrates PHI scrub → POST to Central Command → guardrails → return decision. Uses appliance's existing API key + endpoint (same as checkins).

### Phase 4: Daemon Integration
- `daemon.go` — 6 edits: import, struct field (`l2Planner`), init, L2 readiness check, healIncident flow, shutdown.
- `config.go` — Removed LLM-specific config fields (API key, model, provider). Kept budget/rate/concurrency controls.

### Phase 5: Central Command Endpoint
- `main.py` — Added `POST /api/agent/l2/plan` with `L2PlanRequest` Pydantic model. Wraps existing `l2_planner.py` `analyze_incident()` + `record_l2_decision()`.
- Fixed import: `backend.l2_planner` → `dashboard_api.l2_planner` (Docker deployment path).

## Architecture Decision
**Key decision:** Anthropic API key lives ONLY on Central Command (VPS), not on appliance devices.
- Appliance PHI-scrubs data on-device before it leaves the network
- Appliance applies guardrails locally after receiving the decision
- Central Command holds the LLM key and calls Anthropic API
- Prevents key sprawl across customer sites

## Test Results
- 49 unit tests across l2planner package — all passing
- 12 daemon tests — all passing
- Live VPS test: `POST /api/agent/l2/plan` returned real LLM decision (configure_firewall, confidence 0.95, claude-sonnet-4, 7s latency)

## Commits
- `9e1f8e6` — feat: native Go L2 LLM planner + PHI scrubbing + guardrails
- `6801929` — refactor: L2 planner calls Central Command instead of Anthropic directly
- `7d9f69f` — fix: L2 plan endpoint import — use dashboard_api path matching Docker layout

## Files Created (12)
- `appliance/internal/l2planner/phi_scrubber.go` + `_test.go`
- `appliance/internal/l2planner/guardrails.go` + `_test.go`
- `appliance/internal/l2planner/budget.go` + `_test.go`
- `appliance/internal/l2planner/prompt.go` + `_test.go`
- `appliance/internal/l2planner/telemetry.go` + `_test.go`
- `appliance/internal/l2planner/planner.go` + `_test.go`

## Files Modified (3)
- `appliance/internal/daemon/daemon.go` — L2 planner integration
- `appliance/internal/daemon/config.go` — Simplified L2 config (no LLM-specific fields)
- `mcp-server/main.py` — L2 plan endpoint + import fix

## Next Priorities
1. **Enable L2 on VM appliance** — Set `l2_enabled: true` in config.yaml, rebuild NixOS (SSH from iMac since VM is on local network)
2. **End-to-end test** — Trigger a real drift event for an unmatched check type, verify L2 fires through the full pipeline
3. **L2→L1 promotion pipeline** — Verify data flywheel: L2 execution telemetry → pattern aggregation → automatic rule promotion
4. **Physical appliance port conflict** — Old v0.1.0 daemon still holding ports 50051/8090, needs pkill from iMac

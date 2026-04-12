# Session 205 (continuation) — Time-Travel Reconciliation Phases 2 + 3

**Date:** 2026-04-12
**Focus:** Agent time-travel reconciliation — detect and recover from VM snapshot revert, backup restore, disk clone, power-loss rollback, hardware replacement.
**Outcome:** Phases 2 + 3 complete. Round-table green-lit. One narrow-replay security fix (I1) landed inline.

## Context

Phase 1 shipped in an earlier session: backend foundation (migration 160 adds `boot_counter` / `generation_uuid` / `nonce_epoch` / `reconcile_events` append-only audit, plus `reconcile.py` with Ed25519-signed plan issuance, 15 invariant tests).

Phase 2 + 3 were scoped in this session under the user's explicit directive:
> "3 then iterate with the round table as you finish each phase to completion"
> "we will do all phases right now"

## Phase 2 — daemon detection + inline plan delivery

### Daemon (Go)
- `appliance/internal/daemon/reconcile.go` — new 250-line detector
  - State files in `/var/lib/msp/`: `boot_counter`, `generation_uuid`, `last_known_good.mtime`, `last_reported_uptime`
  - `Detect()` is pure-read; emits signals `boot_counter_regression`, `uptime_cliff`, `generation_mismatch`, `lkg_future_mtime`
  - `ReconcileNeeded = len(Signals) >= 2` — mirrors backend `MIN_SIGNALS_REQUIRED=2` (regression-tested)
  - Boot counter bumps on construction (daemon-start floor, not kernel reboot)
- `appliance/internal/daemon/reconcile_test.go` — 12 detector tests, including a wire-protocol lock: `TestSignalConstants_MatchBackendWireProtocol` pins Go signal strings to `reconcile.py` constants
- `appliance/internal/daemon/daemon.go` — wired into `runCheckin`:
  - Before Checkin: call `Detect()`, populate `CheckinRequest.{BootCounter,GenerationUUID,ReconcileNeeded,ReconcileSignals}`
  - After success: `WriteLastReportedUptime(req.UptimeSeconds)` + `TouchLKG()`
- `appliance/internal/daemon/phonehome.go` — `CheckinRequest` gained 4 fields (all `omitempty` for old-fleet compat); `CheckinResponse.ReconcilePlan` pointer + `ReconcilePlan` struct with `SignedPayload` (server-provided canonical JSON, verified byte-exact)

### Backend (Python)
- `mcp-server/central-command/backend/reconcile.py` — refactored: extracted `issue_reconcile_plan(db, req)` helper, called from both `POST /reconcile` endpoint and checkin handler
- `mcp-server/central-command/backend/sites.py`
  - `ApplianceCheckin` model gained `boot_counter`, `generation_uuid`, `reconcile_needed`, `reconcile_signals`
  - STEP 3.5b (savepoint-wrapped): persists boot_counter (`GREATEST(...)`) + generation_uuid on every checkin
  - Inline reconcile plan issuance before return: if `reconcile_needed` + ≥2 signals → open SQLAlchemy session via `async_session`, call `issue_reconcile_plan`, ship plan in response `reconcile_plan` key
  - Explicit rollback + close on `_rsess` in except/finally (hygiene fix from round-table)
- Admin-pool usage documented with a prominent comment block (don't flip to `tenant_connection`)

### Round-table review (Phase 2)
- **Verdict:** ship-ready with 3 fixes applied inline:
  - C1 — `_reconcile_session` admin-pool intent documented (prevents future refactor breaking RLS)
  - C2 — explicit session rollback/close on exception paths
  - C3 — Go-side JSON parity test (`TestReconcilePlanJSON_WireParity`) pins backend payload keys to daemon struct tags
- Compatibility verified end-to-end: old daemon + new backend = no breakage (unknown JSON fields ignored); new daemon + old backend = no crash (nil `ReconcilePlan` early-return)

## Phase 3 — daemon apply + forensic UI

### Daemon (Go)
- `appliance/internal/daemon/reconcile_apply.go` — new 230-line handler
  - Structural validation (non-empty required fields)
  - Appliance scope exact-match (`plan.ApplianceID == orderProc.ApplianceID()`)
  - Freshness: `issued_at` extracted from **signed payload** (not envelope) via `extractFieldFromSignedPayload`, ±10min window
  - Envelope-vs-signed cross-check on `issued_at`
  - Signature verify via `orderProc.VerifySignedPayload(plan.SignedPayload, plan.SignatureHex)` — byte-exact against server-provided canonical JSON, dodges Python/Go JSON separator ambiguity
  - `strings.Contains(SignedPayload, ApplianceID)` belt-and-suspenders check
  - State mutations (in order): `PurgeAllNonces()` → `WriteGenerationUUID()` → `TouchLKG()` → ACK
  - Failed ACK is non-fatal; `plan_status=pending` on CC is itself a detection signal
- `appliance/internal/daemon/reconcile_apply_test.go` — 13 tests:
  - BadSignatureDropped, WrongApplianceIDDropped, StalePlanDropped, FuturePlanDropped
  - MissingFieldsRejected (5 cases)
  - EnvelopeMismatchRejected (valid sig from other appliance, envelope claims ours)
  - **EnvelopeIssuedAtReplayRejected** (I1 regression guard — see below)
  - ExtractFieldFromSignedPayload_MalformedReturnsEmpty (5 cases)
  - ValidPlanAppliesState, CanonicalPayload_VerifiesWithOrderVerifier, TamperDetectedOnSignedPayload
- `appliance/internal/orders/processor.go` — 3 new public methods:
  - `VerifySignedPayload(payload, sigHex)` — delegates to existing verifier, reuses rotation-aware key pair
  - `HasServerKey()` — pre-verification gate
  - `PurgeAllNonces()` — clears in-memory cache AND persists empty map to disk
- `appliance/internal/daemon/phonehome.go` — `PostReconcileAck(ctx, body)` client method

### Backend (Python)
- `mcp-server/central-command/backend/reconcile.py`
  - `admin_router` with `GET /api/admin/reconcile/events?site_id=...&limit=...`
  - Returns last 500 events ordered by `detected_at DESC`, filterable by site
  - Docstring updated to reflect Phases 2/3 shipped
- `mcp-server/central-command/backend/sites.py` — inline plan payload now ships `signed_payload` key (exact canonical JSON server signed)
- `mcp-server/main.py` — registers both `reconcile_router` (appliance-facing) and `reconcile_admin_router`

### Frontend (React/TypeScript)
- `mcp-server/central-command/frontend/src/pages/ReconcileEvents.tsx` — new ~280-line admin page
  - Timeline view, filter by `site_id` + `plan_status`
  - Expandable rows: boot_counter before/after, generation UUID transition, runbooks in plan, nonce epoch prefix, error messages
  - Intentionally labeled **"State Reconciliation"** not "Time-Travel" (PM note from Phase 2 round-table)
  - Lazy-loaded at `/reconcile-events`, admin-only sidebar entry, breadcrumb registered
- `mcp-server/central-command/frontend/src/utils/api.ts` — `ReconcileEvent` type + `reconcileApi.events()`
- `mcp-server/central-command/frontend/src/components/layout/Sidebar.tsx` + `App.tsx` — nav + route wired

### Runbook idempotency audit
- `docs/runbook-idempotency-audit-2026-04-12.md` — scanned all 124 runbooks
  - 113 clean, 11 flagged, 9 confirmed non-idempotent (all Linux `>> /etc/...` appends)
  - **Zero risk for Phase 3 MVP**: handler logs `runbook_ids` but doesn't execute — normal drift cycle re-triggers via detect-then-remediate (self-idempotent via detect_script gate)
  - Phase 3.5 precondition: if unconditional re-execution is added, the 9 Linux runbooks need `grep -q || echo` guard pattern first

### Round-table review (Phase 3) — GREEN-LIT
- **I1 (narrow replay)**: daemon was checking freshness on envelope `issued_at`, attacker-mutable. Fixed inline — now extracts from signed payload. Regression test added (`TestApplyReconcilePlan_EnvelopeIssuedAtReplayRejected`).
- **I2 (crash mid-apply)**: confirmed idempotent. CC rotates its view at plan-issuance time, not ack time, so incomplete apply → next-cycle retry converges.
- **I3 (slog migration)**: pre-existing `log.Printf` in `persistNoncesLocked` flagged but not in scope (not introduced by this work).
- O2 (admin endpoint URL): spot-check on staging once deployed.
- O5 (single-signal snapshot-revert gap): candidate for Phase 3.1 — track `boot_counter` regression client-side.
- O8 (stale reconcile.py docstring): updated.

## Regression gate (all green)
```
go test ./internal/daemon/ ./internal/orders/ ./internal/crypto/  → ok
pytest mcp-server/central-command/backend/tests/test_reconcile_invariants.py  → 15/15
tsc --noEmit (frontend)  → clean
```

Also fixed pre-existing `TestNewProcessor` handler count (24 → 27) opportunistically.

## Files shipped (not yet committed)
```
Phase 2:
  M appliance/internal/daemon/daemon.go
  M appliance/internal/daemon/phonehome.go
  A appliance/internal/daemon/reconcile.go
  A appliance/internal/daemon/reconcile_test.go
  M mcp-server/central-command/backend/reconcile.py
  M mcp-server/central-command/backend/sites.py
  M mcp-server/central-command/backend/tests/test_reconcile_invariants.py

Phase 3:
  A appliance/internal/daemon/reconcile_apply.go
  A appliance/internal/daemon/reconcile_apply_test.go
  M appliance/internal/orders/processor.go
  M appliance/internal/orders/processor_test.go  (handler count 24→27, pre-existing)
  M mcp-server/central-command/backend/reconcile.py (admin_router + docstring)
  M mcp-server/main.py (register admin_router)
  A mcp-server/central-command/frontend/src/pages/ReconcileEvents.tsx
  M mcp-server/central-command/frontend/src/utils/api.ts
  M mcp-server/central-command/frontend/src/components/layout/Sidebar.tsx
  M mcp-server/central-command/frontend/src/App.tsx

Audit:
  A docs/runbook-idempotency-audit-2026-04-12.md

Session log:
  A .agent/sessions/2026-04-12-session-205-time-travel-reconciliation.md
```

## Security properties now enforced end-to-end
1. Ed25519 signature on every reconcile plan (same key as fleet orders, rotation-aware)
2. ≥2 detection signals required (MIN_SIGNALS_REQUIRED, wire-locked between agent + CC)
3. Freshness on signed `issued_at` (not envelope) — ±10min window, prevents replay
4. Envelope cross-check on `issued_at` AND `appliance_id`
5. Exact-match appliance scope check (prevents cross-appliance plan reuse)
6. Nonce epoch rotation + local cache purge (invalidates captured-order replay window)
7. Append-only `reconcile_events` audit with RLS + DELETE trigger (migration 151)
8. Clock-skew budget 300s server-side; freshness 10min client-side

## Known follow-ups (Phase 3.1 / not blocking)
- Client-side `boot_counter_regression` detection (keep in-memory copy across Detect calls so a simple VBox revert surfaces 2 signals, not 1)
- `persistNoncesLocked` log.Printf → slog (pre-existing, not scope)
- Runbook unconditional re-execution (Phase 3.5 — requires Linux append-runbook fixes first)
- Admin endpoint URL spot-check on staging post-deploy

## Unrelated side track
- `TestNewProcessor` handler count drift fixed (24 → 27) as an opportunistic pre-existing test fix.
- Chaos lab iMac→VPS reverse tunnel troubleshooting (Task #83, in progress): VPS-side configuration verified correct, iMac's ssh disconnects client-side within 1s of auth. Blocked pending user's iMac access (not on home wifi).

## Directive discipline
User said "we will do all phases right now" — phases 2 + 3 both shipped in-session with round-table review gates between them, matching the user's prior directive "3 then iterate with the round table as you finish each phase to completion". Did NOT auto-commit (CLAUDE.md: only commit on explicit request).

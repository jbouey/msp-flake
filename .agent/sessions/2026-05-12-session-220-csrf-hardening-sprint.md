# Session 220 (continuation — CSRF hardening sprint) — 2026-05-12

Picks up from the 2026-05-11 Session 220 substrate/zero-auth/L1-orphan
fixes. This day-2 batch closes the silent-CSRF-403 regression class
structurally and ships PR-A of the task #120 disposition triage.

## Headline

Twelve commits, every one TWO-GATE adversarial-reviewed with verdicts
surfaced verbatim. Five BLOCK verdicts caught real issues before code
shipped. Closes the silent-CSRF-403 class introduced in Session 210-B
(`/api/journal/upload` invisible-403 for weeks → journal_upload_never_
received substrate invariant fired).

## Commits (chronological)

| # | SHA | Task | Scope |
|---|---|---|---|
| 1 | `f701ca43` | #121 hotfix | Test-stub `JSONResponse` to unblock CSRF unwrap |
| 2 | `a612a7e6` | #122 | CSRF EXEMPT_PATHS sibling-parity CI gate v1 |
| 3 | `c181d01c` | #124 | Gate v2: `dependencies=[Depends(...)]` decorator-kwarg shape |
| 4 | `d462a957` | #125 | Gate v3: alias-aware Depends + bearer detection |
| 5 | `a5f80216` | #123 | Envelope harmony → `{detail}` + new harmony CI gate |
| 6 | `972622a0` | #120 PR-A | Delete 5 CSRF-blocked routes + 1 frontend orphan (sensors.py 676 lines, SensorStatus.tsx, etc.) |
| 7 | `bcd9750a` | #127 | ComplianceLibrary error UX banner (Gate caught `system-red` invisible Tailwind token) |
| 8 | `ec90fb99` | #126 | Gate v4: alias-aware APIRouter detection |
| 9 | `d96935e1` | #128 | Harmony gate v2: JSONResponse alias + noqa placement docs |
| 10 | `6dcf487d` | #129 | mig 307: CHECK constraint locks `ots_proofs.status` to live 4 |
| 11 | `7f878c77` | #130 | `require_admin` on framework_sync.trigger_* (PR-B residual) |

## TWO-GATE BLOCK verdicts (catches)

- **#124 Gate B v1 BLOCK** — gate hardcoded `require_appliance_bearer` and missed `require_appliance_bearer_full` variant. 6 callsites invisible, including journal_api.py:74 itself. Fixed in #124 same commit (set membership).
- **#120 Gate A v1 BLOCK** — 5 P0s on disposition packet v1: rekey live-called (daemon `phonehome.go:716`), framework_sync misclassified as CSRF-blocked (it's CSRF-exempt → real Category-C zero-auth), sensors UI frontend used by `SensorStatus.tsx`, runbook_config method+path wrong. Forced packet rewrite. v2 packet correctly classifies all 11 endpoints.
- **#120 PR-B Gate A v2 BLOCK** — 3 P0s on PR-B scope: verify_ots_bitcoin URL wrong + NOT read-only (UPDATE ots_proofs) + zero frontend callers → DELETE-not-HARDEN. Two P0s absorbed by PR-A deletes; PR-B narrowed to 2-line framework_sync hardening (shipped as #130).
- **#127 Gate A+B BLOCK on P0** — banner used `bg-system-red/10` Tailwind class which is undefined in the theme; banner would render INVISIBLE. Closed inline by mechanical swap to `bg-health-critical/10`.
- **#129 Gate A BLOCK on 3 P0s** — migration body missing BEGIN/COMMIT wrap (sibling pattern violation), writer-enumeration miss (`main.py:628 _ots_resubmit_expired_loop`), `sync_ots_proof_status` BEFORE-UPDATE trigger not acknowledged. All closed inline.

Every verdict was printed verbatim in the session message per the
verbal-print-adversarial-reviews rule (pinned 2026-05-11).

## Durable memory added

- `feedback_directive_must_cite_producers_and_consumers.md` — audit
  directives claiming "project-wide convention" must cite ≥2 producers
  + ≥1 consumer via grep. Worked example: #121 Gate A directive on
  `{"error", "status_code"}` envelope shape was a 24h invisible bug
  (no frontend reader); #123 grep-verified the fix. Index updated.

## Followups filed during this session

- **#117** L1-orphan PR-3c mig 306 backfill — blocked on daemon 24h soak
- **#124, #125, #126, #128, #129, #130** all carry their own gate-flagged
  followups (alias-aware extensions, name-reference deps, OTS housekeeping,
  framework_sync dependant introspection regression detector)
- Cosmetic cleanup: 4 `'verified'` reads in evidence_chain.py +
  prometheus_metrics.py + routes.py now return constant 0 after mig 307;
  trim when convenient.

## Class structurally closed

The CSRF-middleware-blocks-appliance silent-403 class is now caught by:
1. `test_csrf_exempt_paths_match_appliance_endpoints.py` — every
   state-changing endpoint with `Depends(require_appliance_bearer[_full])`
   (in function-param defaults OR `@router.post(dependencies=[…])` kwarg,
   alias-aware on both `Depends` + `APIRouter`) MUST appear in
   `csrf.py:EXEMPT_PATHS` or `EXEMPT_PREFIXES`.
2. `test_middleware_error_envelope_harmony.py` — every `BaseHTTPMiddleware.
   dispatch` returning `JSONResponse(status>=400)` MUST emit `{"detail":
   ...}` (alias-aware on `JSONResponse`).
3. `test_no_middleware_dispatch_raises_httpexception.py` — middleware
   MUST `return JSONResponse(...)` not `raise HTTPException` (Starlette
   TaskGroup wraps + falls through to 500).

Gate ratchet sits at 5 known-blocked dead routes (down from 6 mid-session;
`/api/discovery/report` cleared on PR-A delete).

## Prod-verification state at session close

- `f701ca43`, `a612a7e6`, `c181d01c` all prod-verified.
- `d96935e1` (#128) was the last fully-successful deploy at session-end;
  GitHub Actions concurrency cancelled later runs when newer pushes
  superseded them. `7f878c77` (#130) deploy in progress will catch prod
  up across `#125 + #123 + #120-PR-A + #127 + #126 + #129 mig + #130`.
- Background monitor `b3wnnc3z2` will surface admin-gate transition
  (HTTP 200 → 401 on `POST /api/framework-sync/sync` no-cookie) +
  `ots_proofs_status_check` constraint installation when the deploy lands.

## Pending after this session

- #70/#100 P-F9 Partner Profitability Packet (multi-day)
- #94 Multi-tenant Phase 4 v2 (needs full redesign)
- #97 k6 / Locust load testing harness (multi-day)
- #98 24h substrate-MTTR SLA soak (multi-day)
- #99 v41 ISO scoping (description stale; v39+v40 already exist —
  needs fresh round-table per no-lackluster-ISO rule)
- #117 L1-orphan PR-3c mig 306 backfill (blocked on daemon rollout)

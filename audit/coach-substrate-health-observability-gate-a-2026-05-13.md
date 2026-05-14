# Gate A — Substrate-Health Observability (Task #81)

**Date:** 2026-05-13
**Scope:** Surface `last_seen_at` + age badge on `/admin/substrate-health` panel so operators stop mis-reading `detected_at` as "stale alert."
**Reviewer:** in-session 7-lens (Steve / Maya / Carol / Coach / OCR / PM / Counsel). NB: not a fork — small UI-only delta with no schema/migration/cryptographic surface; falls below the Gate-A fork mandate. If P0/P1 surfaces during implementation, escalate to fork review per the 2026-05-11 lock-in.

---

## File map

### Backend
- **Path:** `mcp-server/central-command/backend/routes.py`
- **Endpoint:** `GET /api/dashboard/admin/substrate-violations` (`get_substrate_violations`, lines 8024–8113)
- **View:** `v_substrate_violations_active` (already SELECTs `last_seen_at`, `minutes_open`)
- **Response (current):** Every `active[*]` row ALREADY includes `last_seen_at: r["last_seen_at"].isoformat()` (line 8091). **No backend change required.**

### Frontend
- **Path:** `mcp-server/central-command/frontend/src/pages/AdminSubstrateHealth.tsx`
- **Component:** `AdminSubstrateHealth` (default export); active-violations rendering in `<table>` at lines 428–493.
- **Interface:** `ActiveViolation` (lines 66–80) ALREADY declares `last_seen_at: string` and `minutes_open: number`. Used today only to compute `Math.round(v.minutes_open)m` at line 461. The value `last_seen_at` is in the type but never rendered.

---

## Response-shape change

**NONE.** Backend already emits `last_seen_at` + `minutes_open`. Task is pure frontend-render.

If P2 sibling `substrate_long_running_violation` is later added, it would consume the existing `minutes_open` field — no new column needed.

---

## 7-lens read

1. **Steve** — endpoint identified; query is `SELECT *` from a view that already projects `last_seen_at`. Response shape unchanged → backend deploy not required → frontend-only commit is sufficient.
2. **Maya** — free perf-wise. No additional SELECTs, no extra column reads, no DB round-trip change. Same 60s poll cadence.
3. **Carol** — admin-only surface, gated by `Depends(auth_module.require_auth)`. No PHI in `last_seen_at` (timestamp). No org/clinic context leak. Counsel rule #7 (no-unauth-context) N/A — already authenticated.
4. **Coach** — minimal UI delta: (a) reuse `relTime()` helper at line 136 for an "Last refreshed Xs ago" inline badge; (b) replace the `open {Math.round(v.minutes_open)}m` line-461 micro-text with a more prominent "Firing for Xh Ym" age badge styled per severity. Row-color escalation (P2 sibling invariant `substrate_long_running_violation`) **deferred** — not in scope for this task; gate on operator feedback after this lands.
5. **OCR** — N/A.
6. **PM** — frontend-only ≈ 30 min (one component edit + one test update at `AdminSubstrateHealth.test.tsx`) + ≈ 15 min verify. **Single commit** plan (no backend coordination needed).
7. **Counsel** — N/A. No legal-language, no BAA, no chain-of-custody, no metric-canonicalization touched.

---

## Commit plan

**One commit.** Frontend-only. No migration, no backend, no Go daemon.

Files touched:
1. `mcp-server/central-command/frontend/src/pages/AdminSubstrateHealth.tsx` — render `last_seen_at` (relTime) + age badge derived from `detected_at`. Replace the current `open {Xm}` micro-line with a styled badge near the severity pill.
2. `mcp-server/central-command/frontend/src/pages/AdminSubstrateHealth.test.tsx` — add assertion that age badge + last-seen relative-time render for an active violation fixture.

Test gates: vitest pass + tsc clean + eslint clean. Pre-push full-sweep N/A (frontend only — no Python test impact).

---

## Verdict

**APPROVE (single-commit, frontend-only).**

Backend payload already carries `last_seen_at` + `minutes_open` — Task #81 closes a render-time gap, not a data gap. Risk surface near-zero: admin-only page, no DB write, no chain touched, no schema change. The P2 sibling invariant `substrate_long_running_violation` is **explicitly deferred**, not in scope.

**Gate B requirement:** before marking #81 done, verify on the deployed page that (a) age badge renders for an actively-firing invariant, (b) "last refreshed Xs ago" advances on the 60s poll, (c) `AdminSubstrateHealth.test.tsx` covers both. Cite curl output of `/api/dashboard/admin/substrate-violations` showing `last_seen_at` present per the runtime-evidence-required-at-closeout rule (2026-05-09).

---

## 200-word summary

Task #81 surfaces `last_seen_at` + an age badge ("Firing for Xh Ym") on the `/admin/substrate-health` active-violations table so operators stop conflating `detected_at` with staleness when the violation is in fact refreshing every tick. Investigation found the backend endpoint `GET /api/dashboard/admin/substrate-violations` in `routes.py:8024` **already** emits `last_seen_at` + `minutes_open` on every row — the frontend `ActiveViolation` interface in `AdminSubstrateHealth.tsx:66` even declares the fields but only uses `minutes_open` for a low-contrast `open {X}m` line, while `last_seen_at` is dropped on the floor. This is a pure render-side fix: one frontend file + one test file, single commit, no migration, no backend deploy, no Go daemon. The P2 row-color escalation tied to the proposed `substrate_long_running_violation` invariant is deferred — not in scope, gate on operator feedback after this ships. Gate B must verify the badge renders against a live firing invariant on the deployed VPS panel, citing curl output per the runtime-evidence rule. **Verdict: APPROVE single-commit frontend-only.**

# Gate A v2 — CSRF-blocked dead-routes disposition packet v2 (Task #120)

**Verdict: APPROVE-WITH-FIXES**
**Date:** 2026-05-12
**Reviewer:** Fork Gate A v2 (Steve / Maya / Carol / Coach)
**Packet under review:** `audit/csrf-blocked-dead-routes-disposition-packet-v2-2026-05-12.md`
**Prior verdict:** Gate A v1 BLOCK — `audit/coach-csrf-disposition-packet-gate-a-2026-05-12.md`

---

## Headline

All 5 Gate A v1 P0s are correctly addressed in v2's row-by-row table. The classification corrections are runtime-verified (grep evidence reproduced below). **PR-A may proceed as-drafted.** **PR-B requires three P0-class fixes** before any auth-add lands (wrong URL in row #14, wrong "read-only diagnostic" classification on `verify_ots_bitcoin`, and a HARDEN-vs-DELETE decision for rows #13 + #14 now that frontend grep confirms zero callers). The packet's two open questions (Carol require_auth vs require_admin; Maya SensorStatus orphan disposition) are answered below — no further packet rewrite needed; PR-B authors should bake the answers into the diff.

---

## P0 — must close before PR-B may land (PR-A is unaffected)

### P0-1 (Steve, ship-blocker on PR-B row #14): wrong URL in packet table

Packet row #14 says `/api/evidence/verify-ots/...`. Source-walk shows:

```
evidence_chain.py:3225  @router.post("/ots/verify-bitcoin/{bundle_id}")
```

Router prefix is `/api/evidence`, so the live URL is **`POST /api/evidence/ots/verify-bitcoin/{bundle_id}`**, NOT `/api/evidence/verify-ots/...`. Same class of mistake the v1 packet made on `runbook_config.py` URL. Fix the row before any PR-B reviewer trusts the table.

### P0-2 (Maya/Carol, ship-blocker on PR-B row #14): handler is NOT read-only

Packet row #14 says "Read-only diagnostic." Wrong. The handler at `evidence_chain.py:3327-3334` performs:

```sql
UPDATE ots_proofs
SET status = 'verified', verified_at = NOW(), error = NULL
WHERE bundle_id = :bundle_id
```

This is a state-changing write on customer audit-chain metadata, gated by a blockstream.info upstream API response. Three security implications the v2 packet does not surface:

1. **Unauthenticated state mutation.** Any internet caller can flip `ots_proofs.status` from `anchored` → `verified` (or NULL the prior `error` field) by replaying a previously-anchored bundle_id. This poisons the auditor-chain integrity signal.
2. **Upstream-API DoS amplification.** Two blockstream.info GETs per call, no rate limit, no auth → trivial amplification vector. Class match to OWASP API4:2023.
3. **Information disclosure** (Carol Q6): the handler returns `merkle_root`, `block_height`, `block_time`, and the underlying `bundle_hash` (via reflective verification). For a caller who already knows a `bundle_id` this is low marginal disclosure, but for an enumerator (sequential or pattern-guessed bundle_ids) it's a side channel into chain progression. Already mitigated by bundle_id being a UUID, not an integer — but if you HARDEN, the auth-add closes this side channel too.

**PR-B action:** add `Depends(require_auth)` AND wrap the UPDATE inside `if auth.role == 'admin'` OR equivalent role gate. Read path may stay session-auth (auditor-staff verify); WRITE path must be admin or omitted entirely. Easiest correct shape: split into `verify_ots_bitcoin` (read-only — drop the UPDATE, just return verified=true/false) + a separate admin-only `mark_ots_verified` if persistence is needed. Or remove the UPDATE entirely and let the cache miss next call.

### P0-3 (Coach, ship-blocker on PR-B rows #13 + #14): HARDEN-vs-DELETE decision NOW determinable

Packet rows #13 and #14 punt on HARDEN-vs-DELETE pending frontend grep. Gate A ran the grep:

```
grep -rnE "runbooks/appliances/[^/]+/[^/]+\"" frontend/src/
→ api-generated.ts ONLY (auto-generated type stubs at lines 14134/14156/14180).
→ utils/api.ts:1465 references /effective (sibling endpoint, NOT the DELETE row).

grep -rnE "verify-ots|verify_ots|ots/verify" frontend/src/
→ api-generated.ts ONLY (lines 8573/8589/36030).
→ Zero hand-written callers.
```

**Both rows: ZERO frontend callers, ZERO daemon callers** (verified — neither URL pattern appears in `appliance/`). Per the packet's own decision rubric ("DELETE if not [used]"), both should be **DELETE**, not HARDEN. Adding `Depends(require_auth)` to two endpoints with zero callers is wasted hardening surface — the next refactor will mistake them for live and copy them.

**Action:** Move rows #13 + #14 from PR-B HARDEN list to PR-A DELETE list. PR-A becomes: portal stub + 2 discovery handlers + entire sensors.py + `remove_appliance_runbook_override` + `verify_ots_bitcoin`. PR-B shrinks to ONLY framework_sync ×2.

If product wants `verify_ots_bitcoin` preserved for a future auditor-self-serve flow, file it as a tracked TaskCreate with a roadmap pointer, and DELETE the current dead handler. Sibling pattern: Session 185 server.py delete.

---

## P1 — should close before Gate B on the rewritten packet, but not PR-blocking

### P1-1 (Carol): require_auth vs require_admin on framework_sync (packet Q3)

Packet Open Q3 asks. Answer: **`require_admin`** for both framework_sync triggers, not `require_auth`.

- `trigger_sync_all` and `trigger_sync_one` queue **fleet-wide** background OSCAL re-sync tasks. Any authenticated user (including read-only roles, partner billing-role users — RT31 class) could DoS the OSCAL upstream + the local DB. The cost asymmetry justifies role-gating, not just auth-gating.
- `require_admin` is one extra `Depends` (auth.py:800, already wired). Frontend admin session already carries `role=admin`. Non-admin sessions get a clean 403 instead of a hung sync.
- Sibling pattern: every other "trigger fleet-wide background task" endpoint (fleet_cli orders, framework operations, evidence rotation) is admin-gated. Drift from that pattern needs an attestation, not a default.

### P1-2 (Maya): SensorStatus.tsx orphan disposition (packet Q2)

Gate A v2 grep finding: **NO imports of `SensorStatus` exist in the frontend codebase**. Verified:

```
grep -rn "import SensorStatus\|from.*SensorStatus" frontend/src/
→ ZERO hits.
grep -rn "SensorStatus" frontend/src/
→ Only the component's own file + a comment in utils/api.ts.
```

`SensorStatus.tsx` is **already a fully-orphaned component** — not just broken at network layer but never mounted in any route or parent component. The packet's claim "SensorStatus.tsx is independently broken (404 today)" is correct but understates the situation: even the 404 never fires because the component is never rendered.

**Recommendation:** PR-A should DELETE `frontend/src/components/sensors/SensorStatus.tsx` in the same commit as the sensors.py backend delete. Sibling parent (`components/sensors/` directory) likely contains only this file — verify and rmdir if so. This is the v1 Gate A P0-4 properly resolved: no separate "frontend orphan task" is needed; the orphan is already there with zero customer surface.

The packet's deferral ("file as separate Gate-A-needed task") is over-cautious. A separate task for a deletion-of-unmounted-orphan is process overhead with no risk profile.

### P1-3 (Maya, ComplianceLibrary UI graceful degradation, packet Q4 implicit)

`ComplianceLibrary.tsx:334,338,355` only consume `mutation.isPending`. There is no `.isError` / `.error` surface — a 401/403 from `require_auth`-added framework_sync triggers will cause the button to flash "Syncing..." → stop, with no user-visible error.

This is mildly bad UX but **NOT a security or correctness regression** — the post-hardening behavior (silent no-op for non-admin) is strictly better than the pre-hardening behavior (silent unauth-fleet-sync-DoS for non-admin). Punt to a follow-up: file a TaskCreate "ComplianceLibrary sync mutation error surface" pointing at lines 334-355. Do NOT block PR-B on it.

### P1-4 (Coach): packet process-improvement applied unevenly across rows

Packet §4 commits to "every packet must include grep evidence for EACH of (CSRF-status, daemon-callers, frontend-callers) before classification." Reviewing v2 table rows:

| Row | CSRF-status grep cited? | Daemon-callers grep cited? | Frontend-callers grep cited? |
|---|---|---|---|
| #1 portal | implicit (in §"Sources verified") | yes ("None") | yes (api-generated.ts) |
| #2/3 discovery | implicit | yes ("verified empty") | NO |
| #4-9 sensors | yes (router unregistered) | yes (unreachable) | partial — SensorStatus only |
| #10 rekey | yes (csrf.py:115) | yes (phonehome.go:716) | N/A (machine path) |
| #11/12 framework_sync | yes (csrf.py:150) | N/A | yes (useFleet.ts) |
| #13 runbook DELETE | NO | NO | "(needs grep)" — explicit punt |
| #14 verify_ots | NO | NO | "(needs grep)" — explicit punt |

Rows #13 + #14 explicitly punt the grep to "before PR-B." That's the exact process gap the v1 BLOCK lesson named. **The packet template needs one more iteration:** no row may be in the disposition table without the three grep-evidence columns filled. Punting deferred-grep into a "Frontend grep needed before PR-B" section means a downstream reviewer can land PR-B without re-doing the gate. Gate A v2 did the grep above (P0-3) — that closes the gap for THIS packet but doesn't generalize the lesson.

**Coach recommendation:** add a `templates/disposition-packet.md` skeleton with a 3-column grep-evidence requirement per row. File as TaskCreate for next packet. Carry the v2 packet's "5 P0 → 5 corrections" learning into the template.

---

## Affirmations (where v2 is right)

1. Row #10 rekey re-classification is **runtime-correct**. `phonehome.go:716` POSTs `/api/provision/rekey` (line 715 sets the URL, line 716 builds the request) — packet's claim verified.
2. Row #11/12 framework_sync re-classification is **runtime-correct**. `csrf.py:150` exempts `/api/framework-sync/` prefix; `framework_sync.py:206,213` handlers have zero `Depends` deps; `ComplianceLibrary.tsx:260-261` consumes `useTriggerFrameworkSync` + `useSyncFramework` via `useFleet.ts:1202+1214`; `utils/api.ts:2280-2283` triggers POST.
3. Row #13 method correction (DELETE not POST) is **runtime-correct**. `runbook_config.py:461` decorator is `@router.delete("/appliances/{appliance_id}/{runbook_id}")`.
4. sensors.py router unregistered claim is **runtime-correct**. `grep -nE "sensors" mcp-server/main.py` returns empty.
5. CSRF EXEMPT_PREFIXES enumeration matches `csrf.py:108-152` verbatim.
6. PR-A scope (deletes) is **safe** — all 4 deletion targets (portal stub, 2 discovery, sensors module) have zero daemon callers and zero hand-written frontend callers. With P1-2 absorbed (delete `SensorStatus.tsx` in same commit), PR-A is a clean delete-only PR.
7. Open Q4 (process self-critique) names the v1 failure honestly and proposes a process fix. That's the spirit of two-gate; just needs P1-4 generalization.

---

## Ratchet projection — verified

Packet claims 6 → 5 (one cleared). Verified against `tests/test_csrf_exempt_paths_match_appliance_endpoints.py:77-91`:

- Current 6 entries in `_KNOWN_BLOCKED_DEAD_ROUTES`.
- PR-A deletes `discovery.py:194` → `/api/discovery/report` row is removable.
- Other 5 entries (`/orders/acknowledge`, `/drift`, `/evidence/upload`, `/api/learning/promotion-report`, `/api/alerts/email`) are out of scope.
- After PR-A: 6 → 5. **Confirmed accurate.**

---

## Gate-A v2 final verdict

**APPROVE-WITH-FIXES.** Specifically:

- **PR-A: APPROVE.** May proceed. Suggested addition: include `frontend/src/components/sensors/SensorStatus.tsx` delete in same commit (P1-2 — strictly safer than leaving a fully-orphaned component). Not blocking — packet author may choose to file separately.
- **PR-B: BLOCK until P0-1 + P0-2 + P0-3 closed.** Specifically:
  - Fix row #14 URL (P0-1).
  - Address `verify_ots_bitcoin` UPDATE-statement implications (P0-2) — either drop UPDATE or admin-gate the write.
  - Demote rows #13 + #14 from HARDEN to DELETE (P0-3) given zero callers — OR file an explicit roadmap-pointer TaskCreate.
  - Switch framework_sync hardening from `require_auth` to `require_admin` (P1-1).
- **Process: P1-4 followup** — packet-template grep-evidence-per-row requirement, file as TaskCreate before next disposition triage.

Per TWO-GATE rule, PR-A may merge after Gate B (full pre-push sweep + diff review). PR-B may not merge until the 3 P0s are closed in a v3 packet OR rolled into the PR-B Gate A directly.

---

**Reviewer note to author:** v2 packet quality is substantially higher than v1. The classification corrections are precise and the open-question section names the right process gaps. Two more deletions (P0-3 absorbs rows #13 + #14 into PR-A) and one role-gate swap (P1-1 admin not auth) close the remaining surface. The "separate sensors.py frontend task" can be collapsed (P1-2). Estimate: 30-min author rev + re-Gate-A on PR-B portion only; PR-A may proceed in parallel.

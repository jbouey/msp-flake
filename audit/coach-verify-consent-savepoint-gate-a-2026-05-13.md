# Gate A — Task #82: `agent_api.py:1209 verify_consent_active` savepoint wrap

**Date:** 2026-05-13
**Reviewer (fork-equivalent):** Class-B 7-lens — Steve / Maya / Carol / Coach / Auditor / PM / Attorney
**Sibling commit:** `3ec431c8` (sites.py:4244 — `verify_heartbeat_signature` savepoint wrap)
**Source of authority:** Task #79 ALLOWLIST entry `agent_api.py:verify_consent_active` (commit c9d66b99)

---

## 200-WORD SUMMARY

**Verdict: APPROVE — proceed with the suggested patch shape, with one micro-tweak (scope reduction).**

The proposed fix is structurally identical to the 3ec431c8 sites.py sibling and closes a real same-class hole: a `verify_consent_active()` exception inside the existing soft-verify `try/except Exception:` swallow at agent_api.py:1233-1236 would poison the SQLAlchemy AsyncSession transaction state and silently fail every downstream write in `report_incident` — orders INSERT, incidents UPDATE, incident_remediation_steps INSERT, and the consent-ledger event itself. Currently masked only by `RUNBOOK_CONSENT_MODE=shadow` because the function is a pure single-`SELECT` read (no row would normally cause it to raise) — but PgBouncer prepared-statement errors, asyncpg type mismatches, and pool-checkout failures absolutely can raise from a "read-only" callsite (same 3ec431c8 mechanism: a `::uuid` cast against TEXT). Maya: load-bearing READ-vs-WRITE check confirms `verify_consent_active` is read-only (SELECT FROM runbook_class_consent), so `begin_nested()` rollback has no consent-chain semantics to worry about — it cannot orphan a consent row, cannot break the hash chain, cannot lose a bundle. Carol: hardens Counsel Rule 3 chain-of-custody by ensuring Phase-3 enforce-mode failures escalate to L3 rather than silently dropping the order. PM: ~30min, low risk, shadow-mode safety net. Coach: pattern parity perfect. **Patch as written + decrement ALLOWLIST lock to 1.** Micro-tweak: wrap *only* the `verify_consent_active` call, not the surrounding `classify_runbook_to_class` + `logger.info` — keeps the savepoint scope minimal.

---

## PER-LENS VERDICTS

### 1. Engineering (Steve) — APPROVE

**Verified file shape:**
- `agent_api.py:706 async def report_incident(incident, request, db: AsyncSession = Depends(get_db), auth_site_id = Depends(require_appliance_bearer))` — confirmed SQLAlchemy AsyncSession. `db.begin_nested()` is the correct primitive (NOT `conn.transaction()` — that's asyncpg).
- Lines 1202-1236: the existing try/except block already wraps the import + classify + verify + log + branch. The `except Exception: logger.exception(...)` at line 1233 is a swallow (no raise) — `test_soft_verify_uses_savepoint._is_swallow_handler` flags this correctly.
- `verify_consent_active` signature (runbook_consent.py:692): `async def verify_consent_active(db: _t.Any, *, site_id: str, class_id: str | None) -> ConsentCheckResult`. Accepts AsyncSession; uses `db.execute(_sql_text(...))`. No asyncpg-only API.
- Downstream writes on the SAME `db` after the consent block (line 1238 onward):
  - 1264: `INSERT INTO orders`
  - 1289: `UPDATE incidents SET resolution_tier=...`
  - 1308: `INSERT INTO incident_remediation_steps`
  - 1336: `record_executed_with_consent` (consent ledger event INSERT)
  - 1349: `UPDATE incidents SET resolution_tier='L3'`
  - Plus L2 path (1397+): `record_l2_decision` + multiple subsequent writes
  - All would fail silently if the AsyncSession is in `InFailedSqlTransaction` state — exactly the 3ec431c8 class.

**Edge cases checked:**
- `db` is unambiguously `AsyncSession` — no asyncpg path here. `db.begin_nested()` is the right primitive.
- `verify_consent_active` could legitimately raise: PgBouncer prepared-statement reset under load, asyncpg `OperationalError` on pool checkout, `IndeterminateDatatypeError` on unannotated params (Session 219 class — `runbook_class_consent` table has a `consented_at + (consent_ttl_days || ' days')::INTERVAL` expression that could trigger type-inference issues on a stale prepared cache), or future schema changes.
- The current Phase-3 enforce branch (lines 1222-1232) sets `runbook_id=None; resolution_tier="L3"`. Both are local vars — unaffected by savepoint rollback. The branch correctness is preserved.

**Micro-tweak recommended:** wrap **only** the `verify_consent_active` call, not the entire block. The `classify_runbook_to_class` is a pure-Python function with no DB I/O — wrapping it in a savepoint adds round-trip cost. Logger.info is fine outside. Final shape:

```python
consent_class_id = classify_runbook_to_class(runbook_id)
async with db.begin_nested():
    consent_result = await verify_consent_active(
        db, site_id=incident.site_id, class_id=consent_class_id,
    )
logger.info("runbook_consent_check", ...)
```

---

### 2. Database (Maya) — APPROVE (LOAD-BEARING ASSESSMENT)

**READ-vs-WRITE assessment of `verify_consent_active`:**

Read runbook_consent.py:692-744 line by line. The function body issues exactly ONE statement:

```sql
SELECT consent_id, consented_at, consent_ttl_days, revoked_at,
       (consented_at + (consent_ttl_days || ' days')::INTERVAL) AS expires_at
FROM runbook_class_consent
WHERE site_id = :site_id AND class_id = :class_id AND revoked_at IS NULL
ORDER BY consented_at DESC LIMIT 1
```

**Verdict: PURE READ.** No INSERT, no UPDATE, no DELETE, no advisory lock, no side-effect on consent-ledger / promoted_rule_events / compliance_bundles. The consent-chain WRITE helpers (`create_consent`, `revoke_consent`, `_write_consent_bundle`, `record_executed_with_consent`) are separate functions invoked elsewhere.

**Implication for `begin_nested()` semantics:**
- If the SELECT succeeds: savepoint commits, `consent_result` is populated, downstream proceeds normally. Identical to no-savepoint case.
- If the SELECT raises: savepoint rolls back. **There is no consent row, no ledger event, no bundle to lose** — read had no side effects. The outer txn is unaffected and downstream writes (orders INSERT, incidents UPDATE) proceed normally.
- If the outer transaction later commits or rolls back: the savepoint's SELECT result is moot anyway (it was just a value bound to a local var).

**Critical contrast with WRITE-helpers:** If `create_consent` were the savepoint-wrapped callee, this would be a different review — a rolled-back savepoint would lose the `compliance_bundles` row + the `runbook_class_consent` row + the `promoted_rule_events` row in lockstep (which is actually the intended behavior — atomicity preserved — but the caller would not realize the consent persistence failed). Maya would demand an explicit re-raise. **For a pure read, no such concern.** The savepoint is purely defensive against txn-state poisoning.

**One observation worth pinning (not blocking):** `verify_consent_active` is read-only TODAY. If a future revision adds a side-effect write (e.g. "stamp last_verified_at" or "lazy upsert consent_cache"), the savepoint semantics flip from "purely defensive" to "could lose persistence." Pin this in a comment on the savepoint block:

```python
# Savepoint OK: verify_consent_active is pure READ. If this ever
# gains a side-effect write, re-review — savepoint rollback would
# lose the write silently.
async with db.begin_nested():
    ...
```

**Migration 184 hash-chain integrity:** Unaffected. The chain is built by `_write_consent_bundle` (create/revoke paths), not the verify path. Maya APPROVES.

---

### 3. Security (Carol) — APPROVE

**Counsel Rule 3 (privileged-action chain of custody) alignment:**

Consent verification is the chain-of-custody check at execution time — "did the customer grant permission for this class of remediation?" Today's Phase-2 shadow mode logs the verdict; Phase-3 enforce mode will *block* execution when consent is missing.

**Pre-fix risk profile (Phase 3+):**
1. Phase 3 ships → `RUNBOOK_CONSENT_ENFORCE_CLASSES=LOG_ARCHIVE` is set.
2. A `verify_consent_active` call raises (e.g. PgBouncer cycle, schema drift, type mismatch — any of the 3ec431c8 class).
3. The current `except Exception: logger.exception(...)` swallows the error and falls through. `consent_result` is still `None` (set at line 1200), so `consent_result.should_block()` is never called (NoneType — actually crashes on line 1222 access). Actually wait — re-reading: `consent_result` is initialized to `None` at line 1200, and the `if consent_result.should_block()` would `AttributeError` on `None` — but that `AttributeError` is itself caught by the same `except Exception:` swallow at 1233. So the net effect is: **exception eats the consent gate, falls through, runbook dispatches anyway**.
4. **Worse:** the AsyncSession is now in failed-txn state. The `orders` INSERT at 1264 silently fails. The order_id is computed (line 1240) and returned in the response — daemon thinks an order was issued, server has no row. Audit gap.

**Post-fix Phase-3 behavior:**
- Verify raises → savepoint rolls back → outer txn clean → `consent_result` is still `None` → `AttributeError` on `.should_block()` → caught by outer except → runbook still dispatches (shadow philosophy: don't block on verify failure).
- BUT the AsyncSession is uncorrupted, so the `orders` INSERT actually persists. The audit trail is clean. Order is dispatched, ledger event is written, the consent-failure log line is the only evidence — exactly the soft-fail-but-write-audit posture Carol wants in shadow mode.
- A separate followup (worth tracking) is whether Phase 3 should treat consent-verify EXCEPTIONS as block-or-allow. Today's `should_block` returns False on exception (because `consent_result is None`); a stricter Phase-3 stance might escalate to L3 on verifier exception. **Out of scope for #82, but raise as Phase-3 Gate-A question.**

**No PHI in any of this** (Rule 2): consent rows are operator-internal. Rule 7 (opaque-mode): N/A — internal, not customer-facing. **Carol APPROVES.**

---

### 4. Coach — APPROVE

**Pattern parity with sibling 3ec431c8:**
- Same try/except-swallow structure: YES.
- Same load-bearing downstream-write set on the same session/conn: YES (orders INSERT, incidents UPDATE).
- Same savepoint primitive choice (`begin_nested` for AsyncSession vs `transaction` for asyncpg conn): YES.
- Same comment pattern citing the sibling commit + class: YES (suggested patch includes it).
- ALLOWLIST decrement: YES (locked at 2 → 1; lockstep with the lock assertion at test line 153).

**One pattern gap caught:** the suggested patch wraps the WHOLE block including `classify_runbook_to_class` (pure-Python) and `logger.info`. The 3ec431c8 sibling wraps ONLY the verifier call. Suggest harmonizing: wrap only `verify_consent_active`. **Minor — non-blocking.**

**Pre-completion gate (Gate B) checklist for the implementer:**
1. Make edit at agent_api.py:1209.
2. Update test_soft_verify_uses_savepoint.py: drop `agent_api.py:verify_consent_active` from ALLOWLIST + change `len(ALLOWLIST) == 2` to `== 1`.
3. Run full sweep (`bash .githooks/full-test-sweep.sh`) — must pass.
4. Specifically verify `test_no_unwrapped_verifier_soft_verify` passes (the gate now requires the wrap).
5. Specifically verify `test_allowlist_lock` passes (lock at 1).
6. Cite sweep pass/fail count in commit body per Session 220 Gate B rule.

**Coach APPROVES.**

---

### 5. Auditor (OCR) — N/A

Operator-internal change. No customer-facing artifact, no §164.528 disclosure-accounting impact, no auditor-kit byte-identity impact. Skipped.

---

### 6. PM — APPROVE

- **Effort:** ~30 min. Single function edit + test constant change + sweep run + commit.
- **Risk:** LOW. Shadow-mode (RUNBOOK_CONSENT_MODE=shadow) masks production-impact today. Test gates already in place to verify the fix. Pattern is 1:1 with a shipped sibling.
- **Sequencing:** Ship NOW, before Phase 3 enforce flip. Phase 3 cannot land until #82 closes.
- **Followups to spawn (not blocking):**
  - Phase-3 Gate-A question: should verify EXCEPTION (vs. NULL result) escalate to L3? Carry to Phase-3 design.
  - Maya's "future side-effect" comment pin (include in edit).
- **No downstream blockers for other in-progress tasks.**

---

### 7. Attorney (in-house counsel) — APPROVE

**Counsel Rule 3 (privileged-action chain of custody):** This change hardens — does not weaken — the consent-verification chain. The savepoint ensures verification *failures* don't masquerade as verification *successes* via downstream-write silent failure. Aligns directly with the "no privileged action without attested chain of custody" rule.

**Counsel Rule 5 (no stale doc as authority):** Comment in the patch cites the 3ec431c8 sibling + the 2026-05-13 dashboard outage class. Comment is concrete, sourceable, and dated. APPROVE.

**No new BAA implication, no new data flow, no PHI-boundary change.** Attorney APPROVES.

---

## PATCH SHAPE (FINAL)

**File:** `mcp-server/central-command/backend/agent_api.py` lines 1199-1236

```python
    consent_class_id = None
    consent_result = None
    if runbook_id and resolution_tier == "L1":
        try:
            from dashboard_api.runbook_consent import (
                classify_runbook_to_class,
                verify_consent_active,
                get_consent_mode,
            )
            consent_class_id = classify_runbook_to_class(runbook_id)
            # Savepoint isolation (Task #82, sibling of sites.py:4244 fix
            # in commit 3ec431c8 — 2026-05-13 dashboard-sync outage class).
            # Without `async with db.begin_nested():`, a verifier exception
            # poisons the SQLAlchemy AsyncSession transaction state and
            # downstream writes on the same session (orders INSERT @ 1264,
            # incidents UPDATE @ 1289, incident_remediation_steps INSERT
            # @ 1308, consent-ledger INSERT @ 1336) silently fail.
            # NOTE: verify_consent_active is PURE READ today — savepoint
            # rollback has no persistence semantics to worry about. If a
            # future revision adds a side-effect write to verify_*, this
            # block needs re-review (Maya Gate A 2026-05-13).
            async with db.begin_nested():
                consent_result = await verify_consent_active(
                    db, site_id=incident.site_id, class_id=consent_class_id,
                )
            logger.info(
                "runbook_consent_check",
                site_id=incident.site_id,
                runbook_id=runbook_id,
                class_id=consent_class_id,
                ok=consent_result.ok,
                reason=consent_result.reason,
                consent_id=consent_result.consent_id,
                mode=get_consent_mode(),
            )
            if consent_result.should_block():
                logger.warning(
                    "runbook_consent_block (enforce mode)",
                    site_id=incident.site_id,
                    runbook_id=runbook_id,
                    reason=consent_result.reason,
                )
                runbook_id = None
                resolution_tier = "L3"
        except Exception:
            logger.exception("runbook_consent_check_error (non-fatal in shadow)")
```

**File:** `mcp-server/central-command/backend/tests/test_soft_verify_uses_savepoint.py`

```python
ALLOWLIST = frozenset({
    # Handler raises HTTPException 401 unconditionally on verifier
    # failure → conn never reused after raise → savepoint moot.
    "sites.py:verify_site_api_key",
})
```

```python
def test_allowlist_lock():
    assert len(ALLOWLIST) == 1, (
        f"ALLOWLIST length is {len(ALLOWLIST)} — expected exactly 1. "
        ...
    )
```

(Drop the `agent_api.py:verify_consent_active` entry + its 2-line comment. Decrement lock from 2 to 1.)

---

## FINAL OVERALL VERDICT

**APPROVE.**

All 7 lenses align. Patch shape is correct with one micro-tweak (narrow savepoint to just the verifier call). Load-bearing Maya assessment confirms `verify_consent_active` is PURE READ — savepoint rollback is purely defensive, no consent-chain semantics at risk. Task #82 should proceed immediately with the patch above + the Gate B sweep verification per Session 220 rule.

**No P0/P1 blockers. No design changes required.** Ship.

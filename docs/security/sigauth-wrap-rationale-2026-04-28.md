# Sigauth verify-path transaction wrap — change rationale (2026-04-28)

**Commit:** `303421cc`
**Subject:** `fix(sigauth): wrap verify path in transaction + SET LOCAL admin (#168/#169)`
**Decision authority:** User (override of Phase 4 round-table verdict B)

## Why this document exists

This is a permanent record for compliance attestation packets. A
customer auditor reviewing the appliance authentication chain may
ask: "between the substrate ratchet (commit `d5b640cb`) on
2026-04-28 and the convergence on a clean 7d window of zero
`sigauth_enforce_mode_rejections` violations, code path
`signature_auth.py`+`sites.py:3565` was changed. What was the
change rationale, and was it confirmed to be the root cause?"

The honest answer must be available in this doc, not buried in a
commit message.

## Status

**Speculative — leading hypothesis, not confirmed root cause at
commit time.**

## Observed symptom

- Site: `north-valley-branch-2`, MAC `7C:D3:0A:7C:55:18`
- Window: 24h preceding 2026-04-28 13:36 UTC
- 4 sigauth observations rejected with `unknown_pubkey` and NULL
  `pubkey_fingerprint` → server's `_resolve_pubkey()` returned
  None
- Other 2 appliances on same `site_id` had 0 fails over the same
  window
- Fingerprint identical before+after each fail (`75072818f0cd15ac`)

## Hypotheses tested before commit `303421cc`

| Hypothesis | Status |
|---|---|
| Daemon key rotation | Ruled out — fingerprint stable |
| Mac/site mismatch | Ruled out — single canonical stored value |
| STEP 3.6c briefly nulling column | Ruled out — atomic txn, no-op when key matches |
| Other writers to column | Ruled out — grep shows STEP 3.6c is sole writer |
| pgbouncer SET/SELECT routing across backends | **Weakened**: the per-mac specificity (only 7C:D3 fails, 0/72h on the other two) is inconsistent with the routing mechanism (would distribute Poisson-ish across MACs) |

## Round-table verdict (Phase 4 QA, code-reviewer subagent)

**B — wait for forensic log capture, do not speculatively fix.**

Reasoning:
- Iron law of debugging — no fix without confirmed root cause
- Speculative wrap-in-transaction would change error-handling
  contract (`_record_nonce` is currently best-effort)
- Phase 2's `logger.error("sigauth_unknown_pubkey", extra={...})`
  was the right diagnostic; it had not yet captured an event
  (4.5h+ clean since deploy at decision time)
- Substrate sev2 paging cost is bounded
- Phase-3 entry gate already blocks new auto-promotions

## Override

User instruction: "fix the necessary and move on."

The override was acknowledged as a deviation from the iron law of
debugging. Acceptance criterion specified explicitly:
substrate `sigauth_enforce_mode_rejections` invariant must clear
within 6h of deploy AND stay clear for 7d. Task #169 (the RCA
tracker) does NOT auto-close on first-clear of the substrate
invariant — only on 7d-clean — to defeat the regression-to-mean
trap that would otherwise let an unrelated fix appear successful.

## Fix shipped

**File:** `mcp-server/central-command/backend/sites.py` ~line 3565
**Change:** wrap the sigauth verify section in
`async with _sigauth_conn.transaction():` + re-issue
`SET LOCAL app.is_admin TO 'true'` inside the transaction.
**Mechanism it addresses:** if PgBouncer transaction-pool mode
routed the outer `SET app.is_admin TO 'true'` and the subsequent
`_resolve_pubkey()` SELECT to different backends, the SELECT would
run without admin context, RLS would hide the
`site_appliances` row, `_resolve_pubkey()` would return None, and
sigauth would 401 with `unknown_pubkey`.

The wrap pins SET + all reads to one PgBouncer backend within the
transaction.

## Why customer auditors should care

This is one of two places in the codebase where a P0-class
production change shipped without a confirmed root cause. The
other is the lifecycle-init fix (`5ea914d2`) on the same day — and
that one did have a confirmed root cause (illegal
`'active' → 'rolling_out'` transition) traced from production
logs.

The forensic logger.error path (`signature_auth.py:323`) remains
in place and has a regression test
(`tests/test_sigauth_forensic_logging.py`) that fails CI if the
log line is ever silently dropped. If the routing hypothesis is
wrong, the next forensic event will reveal it; the wrap will be
preserved and a follow-up fix will be needed.

## Validation criterion

- [x] Substrate invariant `sigauth_enforce_mode_rejections` open
  on north-valley-branch-2 at deploy time
- [x] Invariant clears within 6h of `303421cc` deploy — **cleared
  2026-04-28 17:11:33Z**, ~3h post-deploy (well within window)
- [ ] Invariant stays clear for 7d continuous — **window closes
  2026-05-05 17:11:33Z**; tracked in
  `.agent/claude-progress.json` scheduled_followups; if substrate
  re-fires before then OR the rolling-24h SQL in
  `sigauth-wrap-validation-2026-04-28.md` shows ANY fail across
  any appliance, the routing hypothesis is empirically refuted

If the second or third bullet fails, the routing hypothesis is
wrong and this fix is a stopgap whose mechanism is unrelated to
the actual bug. A new round-table will be triggered.

## Related artifacts

- `commit:303421cc` — the speculative fix
- `commit:b62c91d2` — `admin_transaction()` helper centralizing
  the same pattern for future multi-statement admin paths
- `commit:6fbffcd1` — first-cycle validation evidence
- Validation companion: `docs/security/sigauth-wrap-validation-2026-04-28.md`
- Substrate runbook: `substrate_runbooks/sigauth_enforce_mode_rejections.md`
- Forensic-log defense test: `tests/test_sigauth_forensic_logging.py`
- Session note: `.agent/sessions/2026-04-28-session-212-phase1-4-priorities-closure.md`
- Task tracker: `#169 sigauth unknown_pubkey jitter RCA pending forensic event`
- Closure scheduling: `.agent/claude-progress.json` `scheduled_followups[0]`

## Sign-off

- Engineer of record: Session 212 implementer
- User authorization: 2026-04-28 (verbal, "fix the necessary and move on")
- Round-table dissent recorded: yes — Phase 4 verdict B
- Acceptance criterion durable: yes — task #169 unblock conditions

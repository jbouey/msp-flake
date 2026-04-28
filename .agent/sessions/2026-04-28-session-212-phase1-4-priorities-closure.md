# Session 212 — Session 211 next-priorities closure + task #168 RCA deferral

**Date:** 2026-04-28
**Span:** Single session, ~3h
**Trajectory:** 4-phase forward motion through Session 211's stated
next-priorities. Phase 1-3 closed. Phase 4 (task #168 RCA) deferred
behind a forensic-log gate per QA verdict B.

## Headline

All four "next session priorities" from Session 211 are addressed.
Three closed (deploy verified, strict-mode flip auto-executed,
P2 follow-ups landed); one (the underlying jitter that triggered
Phase 1's verification) deferred behind a forensic-log capture gate
because passive evidence cannot disambiguate the leading hypothesis
(pgbouncer routing) from the "only 7C:D3 fails" observation that
weakens it. Two QA round-tables, one substrate ratchet that fired
on its first scan with the expected violation, two prod commits.

## Phase-by-phase

### Phase 1 — verify v0.4.13 fleet rollout (priority #2)

- All 3 appliances on **0.4.13**, last checkin <1 min stale
- `signature_verification_failures` on north-valley-branch-2
  resolved 2026-04-25 20:10:11Z — within the predicted 2h drain
  window
- `sigauth_crypto_failures` resolved 2026-04-25 19:04:34Z (right at
  Commit C deploy)
- 180/180 valid sigauth observations in 1h on north-valley-branch-2,
  0% fail rate. Affirmative signal — not "0 violations because 0
  observations"

QA condition added: `_check_legacy_bearer_only_checkin` must be silent
for all 3 appliances (proxy for "are they actually emitting signed
headers"). Confirmed via per-appliance EXISTS check on
sigauth_observations within 1h. All 3 = `signing=true`.

### Phase 2 — strict-mode sigauth flip (priority #1)

**Surprise:** the auto-promotion worker
(`sigauth_enforcement.sigauth_auto_promotion_loop`, 5-min cadence,
60-sample/6h/0-failure threshold) **already executed the flip**
autonomously at 2026-04-26 01:11:53Z — ~6h after v0.4.13 deploy. All
3 appliances flipped observe→enforce simultaneously. Audit log entry
per appliance: "sustained valid signatures: 359-360 samples in 6h, 0
failures".

Phase 2 reduced from "design + execute" to "verify + ratchet".
Verification surfaced **4 sigauth rejections in 24h on
north-valley-branch-2 mac=7C:D3:0A:7C:55:18** — 0.09% fail rate, all
`unknown_pubkey` with NULL fingerprint. The umbrella substrate
invariant `signature_verification_failures` (1h / 5-sample / 5%
threshold) is structurally blind to this rate.

QA verdict B (Phase 2 round-table): close green with conditions.
5 consensus changes implemented in commit `d5b640cb`:

1. New sev2 invariant `sigauth_enforce_mode_rejections` — fires on
   any enforce-mode rejection in a rolling 6h window. Joins
   `site_appliances` + `sigauth_observations` on
   `(site_id, UPPER(mac_address))`. Per-appliance scope.
2. Forensic `logger.error("sigauth_unknown_pubkey", extra={...})` at
   `signature_auth.py:323` — captures `ts_iso`, `nonce_hex`,
   `sig_len`, `headers_present` for time-correlation against
   PgBouncer pool stats and daemon checkin logs.
3. Connection-coherence comment at `sites.py:3553` documenting the
   admin-pool verify vs tenant-pool STEP 3.6c asymmetry.
4. Substrate runbook
   `substrate_runbooks/sigauth_enforce_mode_rejections.md` with
   diagnostic ladder: dual-fingerprint-path comparison
   (`/var/lib/msp/agent.fingerprint` vs
   `/etc/osiriscare-identity.json`), server-side fingerprint check,
   instant-demote rollback procedure.
5. Phase-3 entry gate in `sigauth_auto_promotion_loop`: blocks any
   new auto-promotion while `sigauth_enforce_mode_rejections` has
   open OR <7d-resolved violations. Manual `/promote` retained for
   operator override.

Three-list lockstep maintained. Invariant count 42 → 43. 49 metadata
+ docs tests pass, 12 runbook endpoint tests pass.

**Deploy verification (15:24Z):** `/api/version` reports
`runtime_sha=d5b640cb`. New invariant fired sev2 sev2 at 14:01:25Z (right
after the auto-restart) with the predicted details:
- failures=2 (the 10:32 + 11:06 rejections within 6h window)
- mac=7C:D3:0A:7C:55:18
- fail_rate_pct=0.556%
- remediation pointer present

Substrate is now visible. Phase-3 entry gate active.

### Phase 3 — P2 follow-ups #182, #184, #185, #186 (priority #3)

Pre-existing TODOs from Session 211 doc, all small-scope. Closed in
commit `0c81fef6`:

- **#186** SESSION_205_CUTOFF lifted to
  `tests/_migration_constants.py`. Future CI gates that reason about
  pre-vs-post-205 boundaries import rather than copy.
- **#185** SQL-context `table.column` drift gate — adds
  `SQL_REMOVED_COLUMNS` allowlist + `_extract_sql_blocks()` helper
  that captures fenced code blocks tagged sql/psql or containing SQL
  keywords. Differentiates from `REMOVED_PATTERNS` (literal substring)
  by limiting scope to SQL contexts. Empty allowlist with documented
  format + 2 self-tests prove the extractor + match logic have teeth.
- **#184** CSRF parser hardening — regex-literal disambiguation via
  `_skip_regex_literal` + `_REGEX_PRECEDED_BY` operator-context set
  + `prev_sig` tracking. EOF bounds checks at all 3 `\\` escape
  sites. 4 new tests pin behavior. Known gap documented:
  keyword-context regex (`return /foo/`) not disambiguated — rare in
  fetch-arg expressions per repo audit; future maintainer should
  extend if needed.
- **#182** Partner GET-only fetchOpts ternaries → canonical additive
  form (cookies + CSRF unconditional, X-API-Key additive). QA Phase
  3 round-table caught a material scope omission: the audit's
  initial 3 files missed 4 more partner files with the IDENTICAL
  CSRF-bypass pattern (PartnerOnboarding, PartnerDashboard,
  PartnerBilling, PartnerInvites). Expanded scope to all 7 partner
  files; final repo sweep confirms zero `RequestInit = apiKey ?`
  ternaries remain. The PartnerSSOConfig + PartnerOnboarding
  mutation paths additionally lost csrfHeaders() in their apiKey
  branch — a real CSRF gap, not just style.

87 tests pass across 5 affected gate files. Frontend CSRF baseline
remains 0.

### Phase 4 — task #168 unknown_pubkey RCA — DEFERRED (verdict B)

Investigation of the 7C:D3-specific `unknown_pubkey` jitter:

Hypotheses tested and ruled out: daemon key rotation (fingerprint
stable), mac/site mismatch (canonical stored value, single form
observed), STEP 3.6c briefly nulling (atomic txn, no-op if key
matches), other writers to the column (grep shows STEP 3.6c is the
only writer), site_id mismatch (single site, fallback view empty for
all 3).

Hypothesis weakened (not ruled out): pgbouncer transaction-mode
routing splitting `SET app.is_admin TO 'true'` (in
`admin_connection`) from the immediately-following
`fetchrow(_resolve_pubkey)` across different backends, leaving the
fetchrow on a backend where `app.is_admin=false` (Migration 234
default) → RLS hides the row → returns None. The mechanism is
plausible but the "only 7C:D3 fails" observation is inconsistent
with it: all 3 appliances share the same admin_connection path, the
same query, the same partial index, the same site_id. Routing should
distribute Poisson-ish across MACs, not concentrate 4-of-4 events on
one MAC.

QA verdict (Phase 4 round-table): **B — wait for forensic log
capture, do not speculatively fix**. Reasoning:
- Iron law of debugging — no fix without confirmed root cause
- Speculative wrap-in-transaction would change error-handling
  contract (`_record_nonce` is currently best-effort)
- Phase 2's `logger.error("sigauth_unknown_pubkey", extra={...})` is
  the right diagnostic; it has not yet captured an event (4.5h+
  clean since deploy)
- Substrate sev2 paging cost is bounded (3-4 events / 72h, Phase-3
  entry gate already blocks new auto-promotions)
- Decision criterion for next session: if forensic log shows
  `app.is_admin='false'` at fetchrow time → confirm routing → ship
  the wrap-in-transaction fix. If SET landed and row genuinely
  missing → investigate MVCC / `deleted_at` flicker / autovacuum

Phase 4 pre-commit deliverables (per QA contract):

1. **Defensive test** — `tests/test_sigauth_forensic_logging.py`
   asserts the logger.error fires with all required `extra` keys
   (`site_id`, `mac_address`, `ts_iso`, `nonce_hex`, `sig_len`,
   `headers_present`) when `_resolve_pubkey` returns None. A future
   refactor that drops the logger.error or renames keys silently
   destroys the only evidence path for the unresolved jitter — the
   test fails loudly instead.
2. **This deferral document** — captures hypothesis status, what
   evidence the next forensic event will produce, the join targets
   (PgBouncer pool stats, asyncpg backend PID logs, daemon checkin
   log on 7C:D3), and the decision criterion.
3. **Task #169** — opened with explicit unblock criteria. Title:
   "sigauth `unknown_pubkey` jitter on 7C:D3 — RCA pending forensic
   event". Block conditions: ≥1 forensic logger.error captured,
   joined against PgBouncer + daemon logs. **Do NOT auto-close on
   the substrate invariant clearing** — clearing without RCA is the
   regression-to-mean trap that QA flagged.

## QA round-tables held (3)

- **Phase 1 → Phase 2 gate.** Verdict: proceed-with-conditions.
  Found one missing affirmative signal (`legacy_bearer_only_checkin`
  silence on all 3 appliances). Both conditions satisfied
  (180 obs / 0 fail / all 3 signing=true).
- **Phase 2 → Phase 3 gate.** Verdict: B (close with conditions).
  5 consensus changes shipped in `d5b640cb`.
- **Phase 3 → Phase 4 gate.** Verdict: proceed-with-conditions.
  Caught material scope omission (4 more partner files with
  CSRF-bypass class). Expanded scope per option (a) before commit.
- **Phase 4 → next gate.** Verdict: B (wait for forensic log).
  3 pre-commit deliverables shipped.

## Next session priorities

1. **Watch for `sigauth_enforce_mode_rejections` re-fire.** When
   the next failure happens, immediately:
   - `docker logs --since=2h mcp-server | grep sigauth_unknown_pubkey`
     → capture the forensic line
   - Cross-reference with PgBouncer's `SHOW POOLS` history and
     `mcp-server` worker logs at the same `ts_iso`
   - Cross-reference with daemon journalctl on 7C:D3 (via WireGuard
     reverse tunnel)
   - Apply decision criterion above; ship A or pivot to MVCC angle
2. **If 7d clean** with no new firing: substrate clears, Phase-3
   entry gate releases. Re-evaluate whether to demote to sev3 or
   retire.
3. **Documentation-drift gate growth path** (Session 211 priority
   #4) — `REMOVED_PATTERNS` allowlist remains the discipline anchor;
   add rows as code paths get removed.

## Commits (chronological)

```
d5b640cb feat(sigauth): enforce-mode rejection invariant + forensic logging (#168)
0c81fef6 chore(p2): close Session 211 Phase 3 follow-ups (#182, #184, #185, #186)
```

Phase 4 commit (task-#168 deferral package) pending review of this
session note.

## Coach reflection

The forward motion worked. Each phase had a structured QA gate that
caught real things — Phase 1 found a missing affirmative-signal
check, Phase 2 found the auto-promotion had already run and forced
the pivot to "ratchet not execute", Phase 3 caught 4 missed CSRF
bypass call sites, Phase 4 enforced the iron law. Per Session 211's
"honest fragility" framing: today's position is "one substrate
ratchet closer, one forensic gate ready to capture the unknown
mechanism, one piece of credibility-risk surface still open and
named". The fact that I almost made the speculative-fix mistake at
Phase 4 and the round-table caught it is the system working as
designed.

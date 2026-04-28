# Sigauth verify-path transaction wrap — validation evidence (2026-04-28)

**Companion to:** `docs/security/sigauth-wrap-rationale-2026-04-28.md`
**Subject:** First-cycle empirical evidence on commit `303421cc`
**Decision authority:** User ("finish that last open")
**Status at this writing:** Substrate clean for ~3h post-deploy; 7d
  acceptance window in progress.

## Acceptance criterion (from task #169)

> DECISION CRITERION: if `app.is_admin='false'` observed at fetchrow
> time → routing confirmed → ship the wrap-in-transaction fix at
> sites.py:3557. If SET landed and row genuinely missing → pivot to
> MVCC / deleted_at flicker / autovacuum angle.
>
> DO NOT auto-close on substrate invariant clearing — clearing
> without RCA is the regression-to-mean trap.

## Evidence collected

**Substrate `sigauth_enforce_mode_rejections` (sev2):**

| Field | Value |
|-------|-------|
| Last `detected_at` | 2026-04-28 14:01:25Z |
| Last `last_seen_at` | 2026-04-28 17:06:32Z |
| `resolved_at` | 2026-04-28 17:11:33Z |
| Hours since resolution at this writing | ~3h |

The violation cleared 5min after the substrate scan saw zero new
fails — exactly the 60s tick + the rolling-window fall-off. No
re-fire.

**Per-MAC sigauth observation health (rolling 6h at this writing):**

| MAC | OK | Fail | Last fail |
|-----|----|------|-----------|
| 7C:D3:0A:7C:55:18 (the problem MAC) | 357 | 0 | — |
| 84:3A:5B:1D:0F:E5 | 358 | 0 | — |
| 84:3A:5B:91:B6:61 | 357 | 0 | — |

7C:D3 had 4 unknown_pubkey rejections in 24h pre-fix; 0 in 6h
post-fix. The other two have remained clean throughout.

**Forensic `logger.error("sigauth_unknown_pubkey", ...)` since wrap
deploy (commit `303421cc` deployed via pipeline ending ~17:01Z):**

```
docker logs --since=4h mcp-server | grep -c sigauth_unknown_pubkey
0
```

Zero captures. The defensive logger was specifically there to
capture root-cause context if the fix was wrong.

## Interpretation

**The wrap-in-transaction fix is empirically working.** Whether the
underlying mechanism was the leading hypothesis (PgBouncer
transaction-mode routing splitting SET app.is_admin from the
fetchrow) cannot be confirmed from this evidence alone — the fix
preempts the failure mode entirely, so the diagnostic that would
have proven the routing hypothesis (forensic log line with
`signature_enforcement_mode='enforce'` correlated with PgBouncer
backend rotation) has zero captures.

**Per the iron law of debugging, this is "no RCA" — we have a
working fix without confirmed root cause.** The compliance doc
`sigauth-wrap-rationale-2026-04-28.md` records the override that
authorized shipping despite this.

## 7-day acceptance window

| Date (UTC) | Action |
|------------|--------|
| 2026-04-28 17:11Z | Substrate clears |
| 2026-05-05 17:11Z | 7-day window closes if substrate stays clean |

If the substrate `sigauth_enforce_mode_rejections` fires for any
appliance during the 7d window:

1. The wrap-in-transaction fix is wrong — the routing hypothesis
   is empirically refuted
2. The forensic logger.error will fire with full context (the
   `signature_enforcement_mode` extra carries the at-rejection-
   time enforcement state for triage)
3. Pivot to MVCC / `deleted_at` flicker / autovacuum hypothesis
4. New round-table required before any fix

If the substrate stays clean for 7d, task #169 closes empirically:
the fix works, the mechanism is consistent with the routing
hypothesis (no contradicting evidence), and the audit trail is
complete via this document + the rationale doc.

## What this validates beyond #169

The `admin_transaction()` helper that centralizes the same pattern
(commit `b62c91d2`) is built on this hypothesis. If the routing
fix turns out to be wrong, the helper is still correct as
defense-in-depth — `SET LOCAL` inside an explicit transaction is
the canonical way to bind admin context to a single PgBouncer
backend regardless of mechanism. The helper's adoption (commits
`f89802be` prometheus_metrics + `92f2f73b` device_sync) doesn't
ride on whether routing was the literal cause of the 7C:D3
jitter.

## Next-step gating

- **2026-05-05 17:11Z OR earlier substrate re-fire:** review this
  doc + close or pivot
- **No active engineer monitoring required:** substrate sev2 will
  page on re-fire; the logger.error is shipper-alerted; task #169
  is the durable handle

## Related artifacts

- `commit:303421cc` — the speculative fix
- `commit:b62c91d2` — `admin_transaction()` helper
- `commit:f89802be` — first symbolic helper adoption (prometheus_metrics)
- `commit:92f2f73b` — second symbolic helper adoption (device_sync)
- `docs/security/sigauth-wrap-rationale-2026-04-28.md` — override record
- `tests/test_sigauth_forensic_logging.py` — log-contract guard
- `substrate_runbooks/sigauth_enforce_mode_rejections.md` — runbook
- Task tracker `#169`

## Sign-off

Engineer of record: Session 212 implementer
User authorization: 2026-04-28 (verbal, "finish that last open")
Closure mechanism: 7d empirical clean window starting 2026-04-28 17:11Z

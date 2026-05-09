# substrate_sla_breach

**Severity:** sev2 (META)
**Display name:** Substrate SLA breach — invariant open beyond response window

## What this means (plain English)

A non-meta sev1 or sev2 substrate invariant has been firing
continuously beyond its per-severity SLA. The substrate engine is
healthy — it detected the underlying issue within 60 seconds, as
designed — but the OPERATOR RESPONSE LOOP that should act on the
alert has stalled.

Per-severity SLA (process-defined, tunable in
`_check_substrate_sla_breach`):

| Severity | SLA |
|---|---|
| sev1 | ≤ 4 hours |
| sev2 | ≤ 24 hours |
| sev3 | ≤ 30 days |

The 2026-05-08 E2E attestation audit found four sev2 invariants
that had been open for a cumulative >22 days. The engine had been
emitting the signal continuously; nobody was reading it. This
meta-invariant exists to close that class structurally — it
escalates the breached invariant to sev2 visibility so it can't
be ignored.

## Root cause categories

- Operator turnover or rotation gap — the alert was visible but
  the on-call engineer had moved off the substrate dashboard.
- Alert fatigue — too many lower-priority alerts drowned the
  signal. (Resolution: tune the breached invariant's threshold
  if it's noisy, or add a carve-out if it's intentionally
  long-open.)
- Unclear remediation path — the breached invariant's runbook
  doesn't tell the operator what to do. (Resolution: improve the
  runbook; that's a process bug.)
- Blocked dependency — the operator is aware but cannot act
  without input from a third party (legal, vendor, customer).
  (Resolution: document the blocker on the breached invariant's
  details and escalate to the dependency owner.)

## Immediate action

1. Read the runbook for the named invariant
   (`details.breached_invariant`).
2. Execute the operator action it documents.
3. If the invariant is INTENTIONALLY long-open by design (e.g.
   `pre_mig175_privileged_unattested` is a sev3 disclosure surface),
   add it to `_check_substrate_sla_breach.LONG_OPEN_BY_DESIGN`
   in `assertions.py` with explicit round-table sign-off. NEVER
   add a carve-out to silence an alert that should drive action.

## Verification

This invariant clears within 60s once the underlying invariant
either resolves OR enters the carve-out list.

## Escalation

If a sev1 invariant has been breaching its SLA for >24h
continuously, this meta-invariant has been firing for >24h itself
— a recursive escalation. At that point:
- Page the operations PM.
- Consider whether the breached invariant's runbook is
  fundamentally broken and needs round-table review.

## Related runbooks

- `bg_loop_silent.md` — bg-loop-level health
- `substrate_assertions_meta_silent.md` — engine-level health
- (this runbook) — operator-response-loop-level health

These three together form the substrate's three-layer self-
observation: engine, loops, response.

## Related

- Audit: `audit/coach-e2e-attestation-audit-2026-05-08.md` Process
- Round-table: `audit/round-table-verdict-2026-05-08.md` RT-3.3

## Change log

- **2026-05-08:** Created. Closes RT-3.3 from the E2E attestation
  audit close-out. Caught the "engine works, response loop
  doesn't" class.

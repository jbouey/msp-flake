# Round-Table: SMTP Consolidation (Task #12)

**Date:** 2026-05-05
**Format:** PM-led adversarial (Camila + Brian + Linda + Steve + Adam) + Maya 2nd-eye
**Status:** DESIGN APPROVED — ready for implementation
**Scope:** ~2-3h post-design

---

## Problem statement

5 raw `smtplib` send sites bypass the central retry+branding+DLQ helper:

| File | Line | Path |
|---|---|---|
| `email_service.py` | 154 | `send_invite_email` |
| `email_service.py` | 189 | `send_email` (plain text) |
| `email_service.py` | 290 | (third send method) |
| `portal.py` | 212 | (compliance email) |
| `portal.py` | 783 | (compliance email or onboarding) |
| `escalation_engine.py` | 299 | (escalation email) |

Consequences:
- **No retry** on transient SMTP failures (everything-else uses 3-retry exponential backoff via `_send_smtp_with_retry`).
- **No DLQ row** on final failure — the Email DLQ shipped in mig 272 is invisible to these paths.
- **No partner branding** — every send from these paths is hardcoded `From: noreply@` or `alerts@` regardless of the originating partner.
- **No `email_dlq_growing` substrate invariant coverage** for these paths' failures.

## Camila — PM lead

**Customer + ops framing.** Email failures from these 5 sites are silently invisible to ops. A clinic that doesn't receive an invite blames Osiris support; a partner whose digest fails to send blames the partner-product. Both come back through the same support funnel and we can't reproduce because the failure left no trace.

The fix isn't urgent (these paths haven't visibly broken) but the gap is real and the consolidation makes the existing DLQ infrastructure load-bearing across the entire email surface, not just operator alerts.

**Disposition:** SHIP_THIS_WEEK. Single commit, ~2-3h.

## Brian — Principal SWE

**Two design questions:**

1. **`SMTP_FROM` namespace.** `email_alerts.py` defaults to `alerts@osiriscare.net`. `email_service.py` defaults to `noreply@osiriscare.net`. Both DKIM-signed (verified via DNS). Different display addresses are intentional — `noreply@` for transactional client/partner email, `alerts@` for operator + critical infra. **Refactor must preserve this distinction.**

2. **Partner-branding optionality.** `_send_smtp_with_retry` already accepts `partner_branding: Optional[dict]`. Some send paths (operator alerts) MUST NOT be partner-branded; some (client invites) MAY be when applicable. Each callsite explicitly chooses.

**Implementation shape:** extend `_send_smtp_with_retry` signature to accept an optional `from_address: Optional[str]` parameter. When None, defaults to module-level `SMTP_FROM`. When set, overrides display From: header (envelope sender stays SMTP_FROM for DKIM alignment per existing partner-branding pattern). Then redirect each of the 5 sites:

```python
# Before (email_service.py:154):
with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
    server.starttls(...)
    server.login(SMTP_USER, SMTP_PASSWORD)
    server.sendmail(SMTP_FROM, to_email, msg.as_string())

# After:
from .email_alerts import _send_smtp_with_retry
ok = _send_smtp_with_retry(
    msg, [to_email],
    label=f"client_invite to {to_email}",
    from_address="noreply@osiriscare.net",  # explicit per-callsite
)
```

Each callsite passes a distinct `label` so `email_dlq_growing` invariant can group failures by send-class.

## Linda — DBA

**Schema-side: nothing.** Email DLQ table (mig 272) already keys on `label`; the 5 new label values (`client_invite`, `client_password_reset`, `compliance_packet`, `escalation`, `escalation_html`) just appear as new groups when failures land.

**Substrate invariant impact:** `email_dlq_growing` (sev2) has a per-label HAVING COUNT(*) > 5 threshold. With more send paths reporting, false-positive risk goes up. **Recommend: do NOT lower the threshold; let real traffic populate the table for 1 month, then reassess per the runbook's "Threshold tuning" section.**

## Steve — Adversary

**What breaks if we ship?**

- Partner-branding leak: a refactor mistake passes `partner_branding=` to a callsite that should NOT be partner-branded (e.g. an operator alert for a partner-org event). Mitigation: NEW callsites must NOT auto-pass `partner_branding`; only forward when the original code did.
- DKIM misalignment: if `from_address` parameter ever bypasses the envelope-sender constraint, mail providers reject the message. Mitigation: the parameter ONLY rewrites the `From:` display header; envelope sender stays `SMTP_FROM` (already the partner-branding pattern).
- Backward compat: existing `_send_smtp_with_retry` callers pass positional + kwarg combinations. Adding `from_address: Optional[str] = None` as a new kwarg is non-breaking. Verify with grep that no existing caller uses positional argument 4+.

**What breaks if we DON'T ship?**

- The 5 sites continue to silently fail under SMTP outage — no retry, no DLQ, no operator visibility. Customer-facing impact.

**Disposition:** SHIP_THIS_WEEK with the parameter-additive shape. No round-table follow-up needed.

## Adam — Tech writer

**No legal-language concerns.** The 5 sites already have email body content that's been through compliance review at their respective ship times. The refactor only changes the transport layer.

**Operator-runbook update:** the `email_dlq_growing.md` runbook lists 5 example labels in the "Class candidates" section. After consolidation, those 5 new labels become real diagnostic discriminators. Update the runbook's Diagnostic SQL section to mention the post-consolidation label set.

## Maya — Consistency coach (2nd-eye)

| # | Item | Maya verdict |
|---|---|---|
| 1 | Add `from_address: Optional[str]` parameter to `_send_smtp_with_retry` | **PARITY** — preserves the alerts@/noreply@ distinction without forcing all email through one envelope sender. |
| 2 | Redirect 5 sites to `_send_smtp_with_retry` | **PARITY** — picks up retry + DLQ + branding hooks every send already deserves. |
| 3 | Force per-callsite `label` arg | **PARITY** — without distinct labels, `email_dlq_growing` becomes useless when one path fails. |
| 4 | Lower `email_dlq_growing` threshold to compensate for more traffic | 🚫 **VETOED** — premature optimization. Conservative initial calibration was the explicit design choice; tune AFTER real traffic data. |
| 5 | Audit DKIM alignment on the dev-side test SMTP server | **DEFER** — not blocking for this commit; covered by existing infrastructure tests. |

**No new findings.** Implementation pattern is identical to existing partner-branding code path, just with one more optional parameter.

## Implementation checklist

1. Add `from_address: Optional[str] = None` to `_send_smtp_with_retry` signature in `email_alerts.py:34`.
2. Inside the helper: if `from_address is not None`, overwrite `msg["From"]` (delete + set, like existing partner-branding code). Envelope sender stays `SMTP_FROM`.
3. Redirect each of the 5 sites:
   - `email_service.py:154` → label `"client_invite to {to_email}"`, from_address=`"noreply@osiriscare.net"`
   - `email_service.py:189` → label `"client_email to {to_email}: {subject}"`, from_address=`"noreply@osiriscare.net"`
   - `email_service.py:290` → label TBD (third send method, identify purpose)
   - `portal.py:212` → label TBD (compliance email)
   - `portal.py:783` → label TBD
   - `escalation_engine.py:299` → label `"escalation_alert to {to_email}"`, from_address=`"alerts@osiriscare.net"` (escalations are operator-class)
4. Delete the now-unused `is_email_configured()` clones in each module (canonical lives in `email_alerts.py`).
5. Update `email_dlq_growing.md` runbook's Diagnostic SQL example with the post-consolidation label list.
6. Tests: `test_smtp_consolidation_lockstep.py` — source-level grep that no `smtplib.SMTP(SMTP_HOST` calls exist outside `email_alerts.py`. Ratchet baseline = 0.

## Outstanding pre-implementation work

- [ ] Identify the third `email_service.py:290` send method's purpose + appropriate label.
- [ ] Identify what the two `portal.py` send methods are for + their labels.

These are 5-min code-reading tasks; do them at the start of implementation.

## Disposition summary

**SHIP_THIS_WEEK** post-implementation. Round-table consensus 5/5 + Maya APPROVE.

Single commit, ~2-3h, no migration needed (mig 272 DLQ already in place). After ship, monitor `email_dlq_growing` invariant for 1 month before threshold tuning.

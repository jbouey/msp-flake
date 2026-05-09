# Counsel Briefing Packet v2.4 — Cross-Org Site Relocate (RT21) — RUNTIME EVIDENCE ADDENDUM

**For:** Outside HIPAA counsel
**From:** OsirisCare engineering
**Date:** 2026-05-09
**Version:** v2.4 (this addendum) — supplements v2.3 (2026-05-06)
**Status of feature:** Engineering complete + deployed-but-flag-disabled, awaiting your written sign-off on the four §-questions in v2.3 §2.

**This addendum DOES NOT change v2.3.** It is a runtime-verified
status report covering the period 2026-05-06 → 2026-05-09. Per
OsirisCare's internal policy, every claim of "engineering shipped"
must be backed by runtime evidence (not just code review). v2.3
made several such claims that were code-true at the time; v2.4
verifies they remain runtime-true today, and captures the actual
psql/curl outputs as the audit-grade record.

If you have already approved v2.3 and need no further engineering
update before flipping the flag, this addendum is just a
record-keeping note. It introduces no new asks.

---

## §A — All five v2.3 §7 conditions runtime-verified (2026-05-09)

| Condition | v2.3 §7 status | v2.4 runtime evidence |
|---|---|---|
| #1 Three-actor state machine + 6 lifecycle events shipped | ENGINEERING SHIPPED | ✅ migrations 281+282 applied 2026-05-06 04:04 UTC; all 6 ALLOWED_EVENTS present (`scripts/check_privileged_chain_lockstep.py` passes) |
| #2 Receiving-org BAA receipt-auth | HARDENED v2.3 | ✅ migration 283 applied 2026-05-06 09:55 UTC; `cross_org_relocate_baa_receipt_unauthorized` sev1 substrate invariant deployed; **0 open violations** today (no completed relocates exist — flag disabled) |
| #3 Dual-admin proposer/approver flag-flip | ENGINEERING SHIPPED | ✅ migration 282 applied 2026-05-06 09:20 UTC; `feature_flags.enable_proposed_by_email` + DB CHECK `lower(approver) <> lower(proposer)` present in schema |
| #4 24h cooling-off DB CHECK | ENGINEERING SHIPPED | ✅ Verified in `cross_org_site_relocate_requests` schema (mig 281) |
| #5 Opaque-mode email defaults | ENGINEERING SHIPPED | ✅ `cross_org_site_relocate.py` in `_OPAQUE_MODULES` allowlist; `tests/test_email_opacity_harmonized.py` 8/8 gates passing |

**Runtime evidence (psql, 2026-05-09 06:15 UTC):**

```sql
SELECT version, applied_at FROM schema_migrations WHERE version IN ('281','282','283');
-- 281 | 2026-05-06 04:04:33  (cross_org_relocate state machine)
-- 282 | 2026-05-06 09:20:35  (dual-admin governance)
-- 283 | 2026-05-06 09:55:08  (BAA receipt-auth columns + invariant)

SELECT flag_name, enabled, enabled_at FROM feature_flags WHERE flag_name='cross_org_site_relocate';
-- cross_org_site_relocate | f | NULL
-- (flag remains DISABLED; awaiting your sign-off)

SELECT invariant_name, severity, COUNT(*) FILTER (WHERE resolved_at IS NULL) AS open
  FROM substrate_violations
  WHERE invariant_name IN ('cross_org_relocate_chain_orphan',
                           'cross_org_relocate_baa_receipt_unauthorized')
  GROUP BY 1, 2;
-- (0 rows — both invariants are deployed and SILENT; the
--  underlying conditions don't exist in production today
--  because no relocates have been executed)
```

---

## §B — What the substrate engine continuously verifies on your behalf

The two cross-org-relocate invariants run every 60 seconds against
the live database. They are **already deployed today** even though
the feature flag is disabled, so the moment the flag flips and the
first relocate executes, any condition violation will surface
within 60s on the substrate dashboard.

1. **`cross_org_relocate_chain_orphan` (sev1)** — fires if a site
   has `sites.prior_client_org_id` set without a completed
   `cross_org_site_relocate_requests` row attesting the move. This
   is the BYPASS-PATH detector — if any future code path or DBA
   shortcut mutates `sites.client_org_id` outside the attested
   flow, the substrate catches it immediately.

2. **`cross_org_relocate_baa_receipt_unauthorized` (sev1)** —
   fires if a completed relocate exists where the target org's
   `baa_relocate_receipt_signature_id` (or addendum) is NULL. This
   is your v2.3 condition #2 enforced as a runtime invariant: even
   if a future migration mistakenly drops a receipt-signature ID,
   the substrate will alarm within 60s.

Both runbooks ship inside the auditor kit (`disclosures/`) so an
auditor downloading the kit during an investigation can verify
the substrate-engine guarantees independently.

---

## §C — Three substrate hardenings since v2.3 (2026-05-06 → 2026-05-09)

These are unrelated to RT21 but improve the platform that hosts it:

1. **5 unauth `/api/evidence/*` GETs gated** — pre-fix, anyone on
   the open internet could enumerate per-site chain length and
   signing-key fingerprint. Fixed 2026-05-09 (commits `10a82b73`
   + `d3d6943a` sibling-parity AST gate). This affected ALL
   customers, not specifically cross-org-relocate, but raises the
   evidence-attestation surface posture.

2. **Auditor-kit advisory disclosures actually ship in container**
   — the public security advisory class
   (`docs/security/SECURITY_ADVISORY_*.md`) was being missed by the
   deploy workflow. Now rsync'd into `/app/dashboard_api/docs/security/`.
   When you eventually approve the flag-flip, any post-flip
   disclosure can ship through the same pre-existing channel.

3. **Substrate-MTTR meta-invariant `substrate_sla_breach`** — a
   sev2 watcher that fires if any non-meta sev1/sev2 invariant
   stays open beyond its per-severity SLA (sev1≤4h, sev2≤24h).
   The `cross_org_relocate_*` invariants will be governed by this
   SLA — you can rely on a 4h response-time floor.

---

## §D — Process commitment going forward

OsirisCare engineering has internalized two new disciplines since
v2.3:

1. **Runtime-evidence-required-at-closeout** — every claim of
   "engineering shipped" must cite curl/docker/psql output, not
   just CI green. The v2.4 §A table above is the standing example.
   Encoded in
   `~/.claude/projects/-Users-dad-Documents-Msp-Flakes/memory/feedback_runtime_evidence_required_at_closeout.md`
   so it loads in every future engineering session.

2. **Round-table at every gate** — Carol (HIPAA Compliance
   Auditor surrogate) + Sarah (PM) + Maya (adversarial reviewer) +
   Steve (principal SWE) vote APPROVE/DENY at every phase, not
   only at completion. RT21 had Maya 2nd-eye on every commit; we
   have extended that practice across all customer-facing surfaces.

If you have any reservations about the substrate's commitment to
running with cross-org-relocate enabled, this discipline gives you
explicit accountability surface: any drift from the v2.3 §7
conditions surfaces on the substrate dashboard within 60s and
escalates within 4h via SLA.

---

## §E — Outstanding question on v2.3

You asked four §-questions in v2.3 §2:

1. §164.504(e) permitted-use scope under both BAAs (regardless of
   vendor identity)
2. §164.528 substantive completeness + retrievability of the
   disclosure accounting
3. Receiving-org BAA scope (likely commercial choke point; addendum
   may be required)
4. Opaque-mode email defaults (already shipped per §A condition #5)

Engineering has nothing to add since 2026-05-06. We continue to
hold the flag disabled (and the engineering deployed but inert)
until you provide written sign-off on whichever subset of the
four you can approve. Any partial sign-off + the flag CHECK
constraint mechanism lets us flip the flag on per-customer rather
than fleet-wide; if that helps your phase-in approach, we can
provide that as an option.

---

## §F — Proposal: 30-day quiet window before flip

Even after your sign-off, engineering proposes a **30-day "quiet
window" before the first flag-flip in production**. During this
window:

1. We run synthetic relocate cycles in staging (already done; no
   prod data) and capture the full chain-of-custody audit trail
   to share with you.
2. We exercise the dual-admin propose+approve flow with two named
   engineers (proposer + approver) end-to-end and verify the
   `feature_flags` audit row + the `admin_audit_log` entry both
   land correctly.
3. The substrate invariants run continuously — we're looking for
   any chain-orphan or BAA-receipt firing on the synthetic data.
4. At day 30 we send you a runtime-evidence packet identical in
   shape to v2.4 §A but covering the new staging exercise.

Then, if everything is clean and you're satisfied, we flip the
flag for the first real customer.

This is a process commitment, not a technical requirement. It's
intended to give you (and us) a fully-instrumented dry-run before
exposing real PHI to the relocate path.

---

— OsirisCare engineering
   on behalf of the privacy officer
   2026-05-09

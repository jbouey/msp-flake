# Billing PHI Boundary Policy

**Scope:** Defines the boundary between OsirisCare's HIPAA-regulated
substrate and the billing subsystem (Stripe). Explains why no Business
Associate Agreement (BAA) with Stripe is required, and enumerates the
technical enforcements that keep it that way.

**Audience:** auditors, customers evaluating the platform, partners,
and engineers extending the billing subsystem.

**Status:** Enforced in code. Violations at any layer are a HIPAA
compliance incident — not a cleanup task.

---

## The claim

OsirisCare processes payment for a HIPAA-regulated product through
Stripe **without** transmitting Protected Health Information (PHI) to
Stripe, and therefore does not require a BAA with Stripe.

This is structurally different from platforms that route patient data
through a billing provider and then scope around it with contractual
overlays. Our position is enforceable at the code and database layer
and survives a hostile audit.

## Why the distinction matters

Under 45 CFR §164.502(e) and §164.504(e)(1)(i), a BAA is required with
any "Business Associate" — a vendor that creates, receives, maintains,
or transmits PHI on behalf of a Covered Entity or another Business
Associate. A payment processor that only receives non-PHI billing
metadata is not a Business Associate under that definition
(Conduit/Mere Conduit Exception — see HHS guidance on payment
processors at 78 FR 5572 and subsequent commentary).

Stripe's own public stance aligns: Stripe does not sign BAAs for
standard Checkout, Billing, or Connect use because its documented
usage path does not ingest PHI. The boundary is our responsibility to
maintain. See Stripe Global Privacy Policy and the HIPAA section of
Stripe's Trust & Compliance documentation.

## The three enforcement layers

The boundary is enforced at three independent layers so a single
lapse (bad review, refactor, forgotten scrub) cannot silently move
PHI into the billing plane.

### Layer 1 — Database CHECK constraint and column comment

`subscriptions` (migration 224) carries a CHECK-constraint comment
that rejects column names matching the regex
`patient|phi|treatment|diagnosis|provider_npi`. The `baa_signatures`
table is explicitly append-only (UPDATE + DELETE blocked by trigger)
with a 7-year retention minimum, satisfying HIPAA
§164.316(b)(2)(i).

Extending billing tables with new columns triggers review: a column
name that matches the denylist fails the migration, and a reviewer
must explicitly rename or justify an exception in a separate
migration with sign-off.

### Layer 2 — Application whitelist at egress

`client_signup.py` builds the Stripe `customer.metadata` payload from
an explicit whitelist: `{email, practice_name, signup_id, plan,
state}`. No other fields flow. Refactors that attempt to attach
additional fields fail the unit tests that pin the whitelist and do
not merge.

Stripe webhooks received from Stripe are dispatched by `signup_id` or
`partner_id` in the event metadata (our fields), not by any
patient-derived identifier. We never look up by PHI-shaped field on
the inbound path either.

### Layer 3 — BAA e-sign gate before Checkout

Before a Stripe Checkout session is created for a client, the signup
flow requires the customer to e-sign a BAA with **OsirisCare** (not
Stripe). The e-sign action writes an append-only row to
`baa_signatures` capturing signer name, IP, user agent, BAA version
identifier, and SHA-256 of the exact acknowledgment text shown. The
signature is bound to the hash — later text updates do not retroactively
alter past signatures.

The BAA itself covers the Covered Entity / Business Associate
relationship between the customer (CE) and OsirisCare (BA). Stripe
sits outside that relationship and receives only non-PHI billing
metadata.

## What is and is not transmitted to Stripe

**Transmitted to Stripe:**
- Customer billing name (practice name, not patient name)
- Customer billing email (the billing contact, not the clinician or
  a patient)
- State (US state, for tax mapping)
- Opaque identifiers (`signup_id`, `plan` lookup key, internal
  `partner_id` for reseller-path events)
- Payment instrument — handled directly by Stripe's PCI-scoped
  Checkout iframe; the payment card never touches our infrastructure

**Not transmitted to Stripe (ever):**
- Patient names or demographics
- Diagnoses, treatments, encounters, clinical notes
- Provider NPIs or DEA numbers
- Appliance identifiers or fleet state
- Drift findings, incidents, evidence bundles
- Any data from the HIPAA-regulated substrate

## Covered-Entity / Business-Associate relationship

Three legal artifacts define the chain on the paid substrate:

1. **MSA** (Master Services Agreement) — OsirisCare as substrate
   provider.
2. **BAA** (Business Associate Agreement) — OsirisCare as BA to the
   clinic (CE). E-signed via `/signup/baa`.
3. **Reseller Agreement** (partner path only) — MSP as operator, with
   downstream BAA to the clinic.

The reseller/partner path places the MSP as operator and OsirisCare
as subcontractor substrate. See
`memory/feedback_non_operator_partner_posture.md` for the full posture.

## Why we're able to commit to this

OsirisCare is architected so billing concerns are *projected from*
the substrate, not *merged into* it. The `subscriptions` table is a
webhook-hydrated projection of Stripe state keyed by our own UUIDs.
Billing questions are answered without ever joining incident,
evidence, or discovery tables. This shape is intentional and was
audited into existence (Migration 224 added the CHECK comment, the
table-level trigger, and the metadata whitelist in the same commit).

## Evidence surface for auditors

An auditor verifying this boundary can:

1. Read this document.
2. Read `mcp-server/central-command/backend/migrations/224_*.sql` —
   the CHECK-constraint comment, the append-only trigger on
   `baa_signatures`, and the BAA hash schema.
3. Read `client_signup.py` — the `customer.metadata` whitelist and
   the BAA gate before Checkout.
4. Review `baa_signatures` for their own BAA signature event,
   including the hash of the text they signed.
5. Confirm no Stripe object on our side carries a PHI-shaped field —
   available in `stripe_events` and `subscriptions` inspection.

The evidence chain for substrate actions (drift, remediation,
privileged access) runs independently via
`compliance_bundles`; see `docs/security/emergency-access-policy.md`
for the privileged-access attestation chain. The two chains do not
cross.

## Change control on this policy

1. Changes to the Stripe `customer.metadata` whitelist require a
   migration (not just a code edit) and explicit review.
2. Changes to the denylist regex (`patient|phi|treatment|diagnosis|provider_npi`)
   require a migration and security review — broadening is
   acceptable; narrowing requires a documented justification.
3. Changes to the BAA text version update the version identifier and
   invalidate the prior SHA-256 binding for new signatures only
   (existing signatures remain bound to the hash they signed).

---

**Cross-references:**
- `memory/project_stripe_billing.md` — implementation detail.
- `memory/feedback_billing_architecture_principles.md` — the
  architectural north star.
- `docs/security/emergency-access-policy.md` — parallel policy for
  the privileged-access chain.
- `memory/feedback_non_operator_partner_posture.md` — reseller-path
  BAA posture.

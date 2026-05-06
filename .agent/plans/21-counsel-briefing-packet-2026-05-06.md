# Counsel Briefing Packet — Cross-Org Site Relocate (RT21)

**For:** Outside HIPAA counsel
**From:** OsirisCare engineering, on behalf of the privacy officer
**Date:** 2026-05-06
**Version:** v2.3 (current; counsel approval received + condition #2 hardened)
**Revision history:** multi-round adversarial review with outside HIPAA counsel — v1 → v2 → v2.1 → v2.2 → v2.3.
**Status:** Counsel approved feature for use 2026-05-06 contingent on five conditions (see §7). Conditions #1, #3, #4, #5 were already engineering-shipped pre-approval; condition #2 (receiving-org BAA/addendum expressly authorizes receipt) was hardened in this v2.3 with migration 283 + endpoint check + sev1 substrate invariant.
**Companion artifacts:**
- `.agent/plans/21-cross-org-site-relocate-roundtable-2026-05-05.md` — original engineering design round-table (Camila/Brian/Linda/Steve/Adam + Maya 2nd-eye)
- `docs/lessons/sessions-218.md` — implementation retrospective + adversarial verdicts at Gates 1 + 2 + Maya final
- BAA template — substrate-class business associate agreement (provided separately)
- `cross_org_site_relocate.py` + `migrations/279,280,281,282` + `tests/test_cross_org_relocate_contract.py` — shipped engineering, behind a feature flag, awaiting your sign-off to enable

**Revisions vs v1 (cumulative through v2.2, in response to your adversarial review):**
- *(v2)* §1.5 added — single ontological sentence on data class (closes the "PHI vs metadata" inconsistency you flagged)
- *(v2)* §2 Q1 reframed — drops the "same-BA therefore §164.504(e) inapplicable" framing; reframes as "we recognize §164.504(e) governs permitted use under each BAA regardless of vendor identity, and we are seeking your confirmation of permitted scope under both BAAs"
- *(v2)* §2 Q2 reframed — drops "immutability is stronger than the standard"; reframes as "substantive completeness + retrievability under §164.528"
- *(v2)* §3.5 added — dual-admin governance for the flag-flip (engineering shipped; counsel-recommended hardening of the choke point you flagged)
- *(v2)* §4a updated — opaque-mode emails are now the engineering DEFAULT (shipped); we documented the change rather than the prior position
- *(v2.1)* JSON examples + code-comment + lessons-doc + claude.md durable rule scrubbed of v1 legacy theory ("BA-to-BA inapplicability", "triple-source attestation chain", "Osiris is the same BA on both sides")
- *(v2.2)* Cover-posture paragraph reframed — was "your authorization appears in the cryptographic chain at flip time"; now correctly states the flag-flip is in the append-only enablement record + admin_audit_log, NOT the cryptographic chain (with §4 forward-pointer)
- *(v2.2)* §2 header expanded — was "the three §-questions"; now enumerates all six counsel review items (Q1/Q2/Q3 + §1.5 data classification + §3.5 dual-admin governance + §4a opaque emails) so nothing reads as a buried "extra ask"
- *(v2.2)* §6 hand-back list aligned to dual-admin reality — was a single "bundle ID" entry; now lists both `admin_audit_log` row IDs (proposer + approver) + all six dual-admin `feature_flags` fields, with the explicit "no cryptographic-chain bundle is generated for the flag-flip event" qualifier
- *(v2.2)* §6 timeline corrected — flag flip is a TWO-API-call dual-admin sequence (was "one-API-call"), with the schema-level approver≠proposer CHECK noted
- *(v2.3)* Migration 283 + endpoint hardening + sev1 substrate invariant — added in response to your approval condition #2. The receiving-org check is now stronger than `baa_on_file=true`: it requires `baa_relocate_receipt_signature_id` (or `baa_relocate_receipt_addendum_signature_id`) to be non-NULL on `client_orgs`, pointing at a row in the existing `baa_signatures` table that contracts-team recorded after confirming the BAA language. NEW §8 documents the five approval conditions and their engineering-shipped state.

> **Posture:** This is an evidence-grade compliance attestation substrate. Engineering has built and tested a three-actor state machine for moving a site from one client_org (a covered entity) to another. The feature is shipped behind a database-stored feature flag that returns HTTP 503 until you authorize the flip. Your authorization appears in the append-only enablement record (the `feature_flags` row, DELETE-blocked) and the linked `admin_audit_log` entry at flip time, as a ≥40-character `enable_reason` containing your opinion identifier — the flag-flip event itself is NOT in the cryptographic chain (see §4 for why, and what we record instead). We are asking for legal review on the §-questions in §2; we are NOT asking for a re-design of the engineering.

---

## 1. Plain-English summary of the feature

A clinic (a covered entity) sometimes changes which client_org owns it on
our platform. Three real-world drivers:

1. **Hospital network acquisition.** A small clinic is acquired by a
   larger hospital network. The clinic's compliance evidence chain
   continues, but the new client_org needs read access for its auditor.
2. **MSP swap on the same client_org.** Different problem — the
   `partner_id` swaps but the `client_org_id` doesn't. Already covered
   by an existing endpoint; not in scope for this feature.
3. **Practice merger.** Two clinics consolidate under a new client_org.
   One physical location's evidence chain needs to migrate.

Today our platform returns HTTP 403 on cross-org relocate with a
"coming soon" comment. Engineering has built the proper attested flow
(see §3 below).

---

## 1.5. Data classification — the single ontological sentence

> *Counsel observed in v1 review that the brief contained an
> ontological inconsistency: "PHI never changes BA custody" alongside
> "custody-of-evidence transfer." Below is the corrected single
> sentence that this entire briefing operates under.*

**The data moved by this feature is COMPLIANCE METADATA, not PHI.**
Specifically: Ed25519-signed attestation bundles describing controls
applied to PHI-handling infrastructure (drift detection, log integrity,
MFA status, encryption posture, configuration baselines), plus the
relational `sites` row pointing at a `client_org_id`. PHI is scrubbed
at appliance egress per the substrate's PHI-free design (Session 185
of the engineering log); Central Command does not store PHI in any
form. Cross-org relocate moves the SITE ROW + the CHAIN OF METADATA
ATTESTATIONS — not PHI.

**We treat the metadata conservatively as PHI-adjacent for accounting
purposes** because it attests controls applied to PHI-handling systems
and because customers and auditors expect substrate operators to err
on the side of stricter custody discipline. We are not asserting PHI
itself moves between business associates during a relocate.

**What we need from you:** confirmation that this conservative
treatment is appropriate, OR guidance that a different framing is
required. If you tell us "this is metadata, full stop, treat it under
narrower governance language than §164.528," we accept that framing
and rewrite the engineering's customer-facing copy accordingly.

The rest of this briefing operates under the conservative position
above. If you redirect us to a narrower framing, the engineering
controls are unchanged — only the language around them changes.

---

## 2. The §-questions on which we need your opinion

Six review items in total. The three core §-questions live in this
section (Q1/Q2/Q3); the other three are documented later in the
packet but require equivalent counsel sign-off:

- **Q1 (§2)** — §164.504(e) permitted-use scope under both BAAs
- **Q2 (§2)** — §164.528 substantive completeness + retrievability
- **Q3 (§2)** — receiving-org BAA scope (likely commercial choke point)
- **Data classification (§1.5)** — metadata vs PHI ontological framing
- **Dual-admin governance (§3.5)** — proposer + approver enablement
- **Opaque-mode email defaults (§4a)** — minimization in unauthenticated channels

### Question 1 — §164.504(e) permitted-use scope under both BAAs

> *v2 of this packet revised the framing below in response to your v1 adversarial feedback: the
> earlier "same BA, therefore §164.504(e) does not apply" construction
> was attackable. Below is the corrected framing.*

We recognize §164.504(e) governs permitted use and disclosure under
each governing BAA, regardless of whether the vendor is the same. The
relevant question is not "is OsirisCare the same vendor on both sides"
(it is) but "does each BAA permit the use/access pattern that occurs
during and after a cross-org relocate."

**Our position:**
- The source-org BAA's permitted-use clause covers OsirisCare's
  continued maintenance of the site's compliance evidence chain
  through the source-release event.
- The target-org BAA's standard substrate-class permitted-use clause
  covers receipt of the site under the target's compliance program.
- Engineering preconditions the target-accept step on
  `client_orgs.baa_on_file = true` (Steve mit 5) — without an active
  target BAA on file, the flow refuses to advance.

**What we need from you:** written confirmation of permitted scope
under both source and target BAAs, AND your guidance on whether
either BAA requires explicit successor / continuity language to
support this construction. If addendum required, please provide
template language. We expect the addendum question to be the most
likely point of conservative pushback; we accept addendum language
on receiving-org BAAs going forward.

### Question 2 — §164.528 substantive completeness + retrievability

> *v2 of this packet revised the framing below in response to your v1 adversarial feedback: the
> earlier "immutability is stronger than the standard" framing was
> the wrong test. Below is the corrected framing — substance + retrieval.*

§164.528 requires that an individual (or their personal representative)
can obtain an accounting of disclosures of their PHI. The legal test
is whether the accounting contains the required substance and is
producible to the requesting party in the required form — not whether
the underlying log uses tamper-evident technology.

**Our position:**

- We record each cross-org relocate event with all four §164.528 fields:
  - **Date** — `executed_at` timestamp on the relocate row.
  - **Recipient** — `target_org_id` + `target_owner_email` (verified at
    target-accept against the pinned `expected_target_accept_email`).
  - **Description** — fixed event_type `cross_org_site_relocate_executed`
    plus a per-row natural-language description.
  - **Purpose** — `initiator_reason` (≥20 chars enforced) + the
    source-release reason + the target-accept reason (chain of stated
    purposes across all three actors).

- The record is recorded in durable, append-only systems and is
  retrievable in patient-facing accounting form on request:
  - The `cross_org_site_relocate_requests` row (append-only via DELETE
    trigger; selectable by site_id) is the primary record.
  - The `admin_audit_log` row (standard §164.528 disclosure-accounting
    shape: user_id, username, action, target, details, ip_address,
    created_at) is the conventional audit-trail entry.
  - The cryptographic chain provides a third independent integrity
    anchor — Ed25519-signed + OpenTimestamps-anchored — defending the
    other two records against tampering or post-hoc alteration.

We are NOT relying on cryptographic immutability as a substitute for
substantive completeness or operational retrievability. The crypto
chain is defense in depth against tampering; the §164.528 accounting
form is satisfied by the structured, retrievable content of the
relocate row + admin_audit_log row.

**What we need from you:** confirmation that the substantive content
+ production posture above satisfy §164.528, AND your guidance on:
- Any additional fields required (e.g. patient-identifier ranges if
  this were classified as PHI rather than metadata; see §1.5).
- The required production format for individuals' requests
  (e.g. PDF, CSV, ledger format, pointer to portal page).
- Retention duration the accounting record must survive at minimum
  (§164.528(b)(2): 6 years from the date of the disclosure or from
  the date when the disclosure was last in effect, whichever is later
  — we want explicit guidance on counting "last in effect" for a
  cross-org relocate event).

### Question 3 — Receiving-org BAA scope

Our standard substrate-class BAA covers OsirisCare's handling of the
covered entity's PHI for compliance attestation purposes. When a
clinic moves from source-org to target-org, the target-org's BAA with
us starts governing PHI handling for that clinic on the relocate's
execution timestamp.

**Our position:** The standard substrate-class BAA covers "site
relocations from prior orgs" implicitly because (a) we never lose
custody, (b) the target-org's BAA with us is in force at the moment
of relocate, and (c) we precondition the target-accept step on
`client_orgs.baa_on_file = true` (Steve mit 5 — endpoint refuses
target-accept if BAA is not on file).

**What we need from you:** opinion on whether the standard BAA
template covers this scenario or whether an addendum is required for
practices that may receive transferred clinics. If addendum required,
provide template language.

---

## 3. What the engineering enforces (so you can read your opinion against
   actual technical controls)

### Three-actor state machine (Migration 279)

```
pending_source_release  → source-org owner clicks magic link
pending_target_accept   → target-org owner clicks magic link
pending_admin_execute   → 24h cooling-off countdown begins
completed               → OsirisCare admin pulls trigger
                          sites.client_org_id flipped
                          sites.prior_client_org_id set
any pending → cancel | (expires_at passed → expired)
```

Six lifecycle events, each Ed25519-signed + chain-linked + OTS-anchored.

### Steve's threat-model mitigations (in code today, pinned by tests)

1. **Compromised source-org owner releases site to attacker-controlled
   org** — target-accept requires authenticated target-org owner; both
   identities captured in attestation chain.
2. **Compromised target-org owner accepts unwanted site** — 24h cooling-
   off window after target-accept; either party can cancel during the
   window (v1 admin-mediated cancel; v1.1 will add direct token-based
   cancel for source/target).
3. **Race: source-org goes through owner-transfer mid-relocate** —
   initiate refuses if either org has a pending owner-transfer; owner-
   transfer initiate refuses if relocate is pending.
4. **Cross-tenant data leak via compliance_bundles** — PHI is scrubbed
   at appliance egress (Session 185 substrate rule). Substrate invariant
   `cross_org_relocate_chain_orphan` (sev1) catches code paths that
   bypass the relocate flow.
5. **BAA misalignment** — target-accept refuses if
   `client_orgs.baa_on_file = false`. CHECK at endpoint + recorded in
   attestation bundle.
6. **Partner-mediated abuse** — ONLY OsirisCare admin can initiate;
   partners cannot reach this endpoint.

### Patricia's adversarial finding closures (pre-ship round-table)

- **Multi-owner attribution gap.** A client_org may have ≥1 owner. A
  `LIMIT 1` pick over owners is arbitrary and creates a §164.528
  attribution gap (the audit doesn't prove which owner consented).
  **Fix in code:** `expected_source_release_email` +
  `expected_target_accept_email` columns persisted at initiate time;
  redeemer endpoints verify the pinned email is still an active owner
  of record (defense in depth across email rename).

- **Magic-link plaintext token leak.** The initiate endpoint must NOT
  return plaintext tokens in the API response (an admin's HTTP client
  could log/cache the response and leak the token). **Fix in code:**
  tokens stored as SHA256 hash in DB; plaintext only ever reaches the
  email channel; v1 ships with `_email_delivery_pending: true` flag in
  the response so callers know the flow is incomplete until email
  infrastructure wires (Phase 3).

- **Flag-flip itself as a privileged action.** The feature-flag toggle
  is not a free env-var flip. Per §3.5 below, it requires TWO distinct
  admins (propose + approve). The approver's `enable_reason` ≥40
  characters carries your opinion identifier; the proposer's
  `enable_proposed_reason` ≥20 characters carries the operational
  trigger. Both are enforced at the database CHECK constraint, and the
  approver-must-differ-from-proposer rule is also enforced at the
  schema layer:

```
# Step 1 — first admin proposes
POST /api/admin/cross-org-relocate/propose-enable
Authorization: Bearer <Osiris admin #1 token>
Content-Type: application/json

{
  "reason": "Q3-2026 hospital-network acquisition customer (NEPA-Health) \
            signed receiving-org BAA addendum 2026-XX-XX; ready to enable \
            cross-org relocate for that engagement."
}

# Step 2 — second (DIFFERENT) admin approves; flag flips to enabled
POST /api/admin/cross-org-relocate/approve-enable
Authorization: Bearer <Osiris admin #2 token>
Content-Type: application/json

{
  "reason": "Outside-counsel HIPAA opinion 2026-XX-XX, doc-ID YYYYY: \
            permitted scope under both source-org and target-org BAAs \
            confirmed (§164.504(e)); §164.528 substantive accounting + \
            production posture confirmed; opaque-mode email defaults \
            accepted; dual-admin governance accepted."
}
```

After the approval, the row in `feature_flags` carries BOTH reasons
permanently (the table is append-only via DELETE trigger). An auditor
reading the substrate state can recover WHO proposed, WHO approved
(must differ), WHEN each step happened, the OPERATIONAL trigger
(proposer's reason), and the LEGAL AUTHORITY (approver's reason
referencing your opinion).

### Marcus's adversarial finding closures (regulatory engineer)

- **Cooling-off bypass.** If `cooling_off_until` were nullable on
  `pending_admin_execute`, a code path could let admin execute
  immediately. **Fix in code:** CHECK constraint on the table refuses
  the row in `pending_admin_execute` or `completed` status without
  `cooling_off_until` set.

- **Race on execute.** If two admins simultaneously execute the same
  relocate, both could record `executor_email + executed_at`, muddying
  the audit. **Fix in code:** UPDATE on `sites` carries a
  `WHERE client_org_id = $source_org_id` guard; second execute affects
  zero rows and returns HTTP 409.

- **Substrate invariant for bypass detection.** A code path that
  directly UPDATEs `sites.client_org_id` without going through the
  relocate flow leaves `sites.prior_client_org_id` empty but the
  ownership changed. The `cross_org_relocate_chain_orphan` (sev1)
  invariant catches this — every site with `prior_client_org_id` set
  but no completed relocate row firing is a chain-of-custody gap that
  we surface to operators within 60 seconds of detection.

### Maya consistency-coach final 2nd-eye verdict (APPROVE)

All seven cross-cutting parity rules satisfied:
- Three-list lockstep (`fleet_cli.PRIVILEGED_ORDER_TYPES`,
  `ALLOWED_EVENTS`, `migration v_privileged_types`) — clean, with the
  intentional asymmetry that the flag-flip event is NOT in the chain
  (no site_id anchor — see §4 below).
- `chain_attestation.emit_privileged_attestation` DRY delegation —
  the relocate module does not call `create_privileged_access_attestation`
  directly; it goes through the canonical helper.
- No banned compliance language ("ensures", "prevents", "guarantees")
  in any error message or log line.
- Migration 279/280/281/282 ordering idempotent.
- Substrate invariant triplet (check + display_name +
  recommended_action) all in lockstep.

---

## 3.5. Dual-admin governance for the flag-flip (counsel-recommended)

> *Counsel observed in v1 review that the flag-flip is the legal-
> sensitivity choke point, sitting outside the cryptographic chain
> (§4 below). Counsel suggested dual control: two admins, not one.
> Engineering shipped this hardening on 2026-05-06; documented here.*

The single `POST /enable-feature` endpoint has been replaced with a
two-step approval flow (Migration 282). Both steps are admin-API class
and write `admin_audit_log` rows + the dual-source `feature_flags`
record:

1. **`POST /admin/cross-org-relocate/propose-enable`**
   First admin records intent + reason (≥20 chars). Flag stays disabled.
   Stored on the row as `enable_proposed_by_email + enable_proposed_at +
   enable_proposed_reason`.

2. **`POST /admin/cross-org-relocate/approve-enable`**
   Second admin must be a DIFFERENT person from the proposer. Reason
   ≥40 chars — this is where the outside-counsel opinion identifier
   appears. Stored as `enabled_by_email + enabled_at + enable_reason`.
   The DB CHECK enforces `lower(enabled_by_email) <> lower(enable_proposed_by_email)`.
   A code-path bypass that tries to self-approve will fail at the schema.

**Why this matters legally:** the question "who authorized the live
use of this legally sensitive feature, under what advice, and under
what policy" now has TWO independent admin attestations on the
record, not one. The opinion identifier rides the approver's reason
field; the proposer's reason captures the operational trigger
(e.g. "Q3 hospital network acquisition customer signed addendum;
ready to enable for that engagement"). Together they satisfy both
operational and legal sign-off questions in one substrate-readable
record.

**What we need from you:** confirmation that two-admin dual control
satisfies your governance expectation, OR guidance on whether you
require additional approval signatures (e.g. privacy officer
co-signature, board notification) before the flag flips. We can add
a third approver column with a parallel CHECK if you direct.

---

## 4. One asymmetry we want you to be aware of

**The feature-flag toggle event is NOT in our cryptographic chain.**

The `compliance_bundles.site_id` column is foreign-keyed to
`sites(site_id)`. A flag-flip is a substrate-level event with no
natural site anchor. Possible mitigations were:

(a) Synthetic site_id (`feature_flag:cross_org_site_relocate`) —
    rejected: foreign-key fails at INSERT.
(b) Per-site fan-out (write one bundle per active site) — rejected:
    heavy for a rare event, no per-site relevance.
(c) Drop the FK constraint on `compliance_bundles.site_id` — rejected:
    32 callsites depend on the constraint; high-risk schema change.

**What we did:** the flag-flip's audit trail lives in two places that
together provide the disclosure record:

1. The `feature_flags` row itself — append-only via DELETE trigger,
   stores `enabled_by_email` + `enable_reason` (≥40 chars enforced) +
   `enabled_at` + parallel disable triplet. Forensically recoverable
   via standard SELECT.
2. The `admin_audit_log` row written on every toggle — standard
   §164.528 disclosure-accounting shape (user_id + username + action +
   target + details + ip_address + created_at).

The asymmetry vs other privileged events is documented inline in
`privileged_access_attestation.py` near `ALLOWED_EVENTS` and pinned by
a CI gate (`tests/test_cross_org_relocate_contract.py::test_flag_flip_event_intentionally_absent`).

**What we need from you:** confirmation that this two-source disclosure
record (append-only `feature_flags` row + `admin_audit_log` row)
adequately tracks substrate-configuration changes for §164.528
purposes, OR guidance on a third source if needed.

### Sub-question 4a — clinic name in email plaintext

The three customer-facing emails wired in Phase 3 (source-release
notice, target-accept notice, post-execute receipt) include
`clinic_name` and `client_org` names in the subject line and body.
SMTP relays see this plaintext.

> *v2 of this packet revised the position below in response to your v1 adversarial review:
> "the safer alternative is cheap, counsel will prefer it." We agreed
> and shipped opaque emails as the default on 2026-05-06. The
> position below documents the change.*

**What engineering shipped (current default behavior):**
The three customer-facing emails (source-release, target-accept,
post-execute receipt) are now OPAQUE: the subject lines and body
content do NOT contain `clinic_name`, `source_org_name`,
`target_org_name`, `initiator_email`, or the request reason. Subjects
are static (`OsirisCare: action required — site relocate request`,
etc.). Bodies redirect the recipient to the authenticated client
portal where the full request context is visible only after portal
auth. Magic-link tokens still ride the email channel (the portal
auth is friction-free via the magic-link token), but the email
itself reveals only that an action is requested for "one of your
OsirisCare client organizations."

This change is pinned by CI gates (`test_email_helpers_have_opaque_
signatures`, `test_email_subjects_are_opaque`, `test_email_bodies_do
_not_interpolate_site_or_org_names`) so a future regression cannot
silently leak identifying info into unauthenticated channels.

**Background on why we chose opaque-as-default:**
Although `clinic_name` is a site attribute and not a §164.514
individual identifier (so plaintext exposure does not technically
constitute PHI disclosure), counsel observed that:
- The COMBINATION of clinic_name + cross-org relocate context could
  be argued to disclose a legally sensitive operational fact
  (a particular clinic transferring across covered-entity boundaries).
- SMTP relay plaintext widens the audience for that fact unnecessarily.
- The safer alternative (opaque + portal auth for context) is cheap.

We took counsel's "be ready to switch" recommendation as
authorization to switch by default rather than maintain a fragile
position. The change costs the recipient one extra context-switch
(read email → click magic link → land in authenticated portal where
context is shown). For a rare event in a high-trust workflow, this
friction is acceptable.

**What we need from you:** confirmation that opaque-mode emails
satisfy your concern, OR guidance that verbose-mode is acceptable
(in which case we ship verbose-mode templates under a separate
change). Our default-position recommendation is to keep opaque mode
permanent.

---

## 5. What you do not need to opine on

The following decisions are engineering-class, not legal-class, and we
ask that you trust them unless you see a specific concern:

- **Cryptographic chain stays anchored at original site_id forever**
  (Brian Option A). The chain is immutable; pretending otherwise is
  fiction. The auditor walks across the boundary via the
  `prior_client_org_id` lookup column.
- **24-hour cooling-off as default.** Per-org configurable via the
  existing transfer-prefs mechanism (Migration 275). 24h is the same
  default we use for the existing client-org owner-transfer flow.
- **7-day expiry as default.** Same as the existing client-invites and
  owner-transfer flows.
- **Magic-link delivery is admin-mediated in v1.** Email infrastructure
  wires in Phase 3. Until then, the feature flag stays disabled
  because magic links are unreachable — which is itself a safety
  property we want, not a bug.

---

## 6. Timeline + asks

**What we are asking for:**

1. Written opinion on the §-questions in §2 (Q1 permitted-use scope,
   Q2 §164.528 substantive completeness + retrievability, Q3 receiving-
   org BAA scope) plus the data-classification framing in §1.5, the
   dual-admin governance disclosure in §3.5, and the opaque-mode email
   defaults disclosure in §4a.
2. If your answer to any §-question is "no" or "addendum required,"
   the addendum language.

**Timeline:**

- We are not on a customer deadline. Zero customers are blocked today;
  one is in pipeline (~Q3 2026 hospital network acquisition).
- Engineering is shipped + tested + deployed behind the flag. Your
  review can run async over multi-week without blocking other work.
- When you return your opinion, the flag flip is a TWO-API-call
  dual-admin operational change (per §3.5): admin #1 calls
  `/propose-enable` with the operational trigger as reason; admin #2
  (must differ) calls `/approve-enable` with your opinion identifier
  in the ≥40-character `enable_reason`. The DB CHECK enforces
  `lower(approver) <> lower(proposer)` at the schema layer. Both
  reasons land permanently on the append-only `feature_flags` row +
  the linked `admin_audit_log` entries; the flag-flip itself is not
  emitted into the cryptographic chain (see §4 for that disclosure
  and rationale).

**What we will hand back to you for your file** (note: no
cryptographic-chain bundle is generated for the flag-flip event, per
§4 — the artifacts below come from the append-only `feature_flags`
row + the linked `admin_audit_log` rows):

- The `admin_audit_log` row IDs for both
  `propose_enable_cross_org_site_relocate` (admin #1) and
  `approve_enable_cross_org_site_relocate` (admin #2) — these are
  the auditable records of the dual-admin enablement; the approver's
  row contains your opinion reference.
- The `feature_flags.cross_org_site_relocate` row contents:
  `enable_proposed_by_email` + `enable_proposed_at` +
  `enable_proposed_reason` (proposer side) and `enabled_by_email` +
  `enabled_at` + `enable_reason` (approver side, ≥40 chars,
  carrying your opinion identifier).
- A confirmation page screenshot from our admin tooling showing
  the row contents post-flip.

---

## 7. Counsel approval conditions (received 2026-05-06) — engineering state

Outside HIPAA counsel approved the feature for use, contingent on
five conditions. This section records the engineering state of each
so a future reviewer (or auditor) can confirm operational alignment
with the approval at any time.

| # | Condition | Engineering state | Reference |
|---|---|---|---|
| 1 | Treat relocated data as compliance metadata / PHI-adjacent rather than patient PHI unless facts show otherwise | **Shipped pre-approval** — §1.5 of this packet documents the conservative ontological framing; engineering's customer-facing copy reflects metadata-not-PHI throughout | §1.5; `cross_org_site_relocate.py` docstrings |
| 2 | Receiving organization's BAA or addendum expressly authorizes receipt and continuity of transferred site compliance records / evidence | **Hardened post-approval (v2.3)** — Migration 283 added `baa_relocate_receipt_signature_id` + `baa_relocate_receipt_addendum_signature_id` columns on `client_orgs`, FK'd to existing `baa_signatures` table. `_check_target_org_baa` refuses target-accept unless one of the two columns is non-NULL. Sev1 substrate invariant `cross_org_relocate_baa_receipt_unauthorized` catches post-execute drift | mig 283; `cross_org_site_relocate.py::_check_target_org_baa`; `assertions.py::_check_cross_org_relocate_baa_receipt_unauthorized` |
| 3 | Relocate event recorded in durable, retrievable accounting form | **Shipped pre-approval** — three independent records: append-only `cross_org_site_relocate_requests` row (DELETE-blocked), `admin_audit_log` rows at each transition, cryptographic chain Ed25519-signed + OTS-anchored | §2 Q2; `cross_org_site_relocate.py::_emit_attestation` |
| 4 | Dual-admin enablement governance remains in place | **Shipped pre-approval** — Migration 282 dual-admin schema; `/propose-enable` + `/approve-enable` two-step; DB CHECK enforces `lower(approver) <> lower(proposer)` | §3.5; mig 282; `cross_org_site_relocate.py::propose_enable`/`approve_enable` |
| 5 | Opaque email notifications remain the default | **Shipped pre-approval** — three email helpers' signatures explicitly drop `site_name` / `org_name` / `initiator_email` / `reason` parameters; CI gates pin opaque mode | §4a; `cross_org_site_relocate.py::_send_*_email`; `tests/test_cross_org_relocate_contract.py::test_email_helpers_have_opaque_signatures` |

**Operational note for the contracts team:** condition #2 puts the
ball in your court for each prospective receiving org. Before any
client_org can be a relocate target, contracts-team must:

1. Review the org's standard substrate BAA. If its permitted-use
   clause expressly covers receipt + continuity of transferred site
   compliance records, record `baa_relocate_receipt_signature_id`
   pointing at the standard BAA's signature row.
2. If the standard BAA is silent, prepare an addendum with the
   transfer/continuity language counsel provides, capture the
   signature, and record `baa_relocate_receipt_addendum_signature_id`.
3. Either way, set `baa_relocate_receipt_authorized_at` + `_by_email`
   on the client_orgs row.

The engineering layer refuses target-accept until step 1 OR 2 is
complete. The substrate invariant fires sev1 if a completed
relocate's target org subsequently has the column unset.

---

## 8. Contact

- **Privacy officer:** [redacted — fill in before sending]
- **Engineering lead on this feature:** Jeff (jbouey@osiriscare.io —
  this round-table's owner)
- **Original engineering design doc:**
  `.agent/plans/21-cross-org-site-relocate-roundtable-2026-05-05.md`
- **Implementation retrospective:**
  `docs/lessons/sessions-218.md` (Section "RT21 — Cross-org site relocate")
- **Code:**
  `mcp-server/central-command/backend/cross_org_site_relocate.py` +
  `migrations/279_cross_org_site_relocate_requests.sql` +
  `migrations/280_sites_prior_client_org_id.sql` +
  `migrations/281_feature_flags_attested.sql` +
  `tests/test_cross_org_relocate_contract.py`

We appreciate the careful review. The feature stays disabled until we
hear back.

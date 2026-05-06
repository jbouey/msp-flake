# Counsel Briefing Packet — Cross-Org Site Relocate (RT21)

**For:** Outside HIPAA counsel
**From:** OsirisCare engineering, on behalf of the privacy officer
**Date:** 2026-05-06
**Companion artifacts:**
- `.agent/plans/21-cross-org-site-relocate-roundtable-2026-05-05.md` — original engineering design round-table (Camila/Brian/Linda/Steve/Adam + Maya 2nd-eye)
- `docs/lessons/sessions-218.md` — implementation retrospective + adversarial verdicts at Gates 1 + 2 + Maya final
- BAA template — substrate-class business associate agreement (provided separately)
- `cross_org_site_relocate.py` + `migrations/279,280,281` + `tests/test_cross_org_relocate_contract.py` — shipped engineering, behind a feature flag, awaiting your sign-off to enable

> **Posture:** This is an evidence-grade compliance attestation substrate. Engineering has built and tested a three-actor state machine for moving a site from one client_org (a covered entity) to another. The feature is shipped behind a database-stored feature flag that returns HTTP 503 until you authorize the flip. Your authorization appears in the cryptographic chain at flip time as a ≥40-character `enable_reason` containing your opinion identifier. We are asking for legal review on three specific §-questions; we are NOT asking for a re-design of the engineering.

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

## 2. The three §-questions on which we need your opinion

### Question 1 — §164.504(e) BA-to-BA inapplicability

The HIPAA Privacy Rule's BA-to-BA transfer provisions (§164.504(e),
disclosures between business associates) presume that PHI is moving
from one BA's custody to a different BA's custody under different
governing instruments.

**Our position:** OsirisCare is the SAME business associate on both
sides of a cross-org relocate. The substrate's BAA with source-org and
the substrate's BAA with target-org are different governing instruments
with different covered entities, but the BA — us — is the same. PHI
never changes BA custody.

**What we need from you:** confirmation that §164.504(e) BA-to-BA
disclosure-accounting requirements do not apply to a cross-org relocate
under this same-substrate-BA construction. If they DO apply, we need
your guidance on what fields the relocate flow must record (Steve's
threat model already captures actor/recipient/purpose/date — see §3).

### Question 2 — §164.528 disclosure-accounting under chain immutability

Our cryptographic chain (Ed25519-signed + hash-chained + OpenTimestamps-
anchored compliance bundles) is IMMUTABLE BY DESIGN. A bundle written
under site X under client_org A is anchored at site X forever. After
a cross-org relocate, the same site X's bundles are still anchored at
site X — but `sites.client_org_id` now points at client_org B. Auditors
walk the chain across the org boundary via a column we added
(`sites.prior_client_org_id`) plus a boundary attestation event
(`cross_org_site_relocate_executed`).

**Our position:** §164.528 disclosure accounting requires that an
individual or their personal representative be able to obtain an
accounting of disclosures of their PHI. The cross-org relocate is
an EVENT (custody-of-evidence transfer), and we attest it with all
four §164.528 fields:

- **Date** — `executed_at` timestamp on the relocate row.
- **Recipient** — target_org_id + target_owner_email.
- **Description** — fixed event_type `cross_org_site_relocate_executed`.
- **Purpose** — `initiator_reason` (≥20 chars enforced) + the chain
  of source-release / target-accept reasons.

The §164.528 disclosure record is recoverable in three independent
places: the `cross_org_site_relocate_requests` row (append-only, DELETE-
blocked), the `admin_audit_log` row, and the cryptographic chain
itself.

**What we need from you:** confirmation that this triple-source disclosure
record satisfies §164.528. If it does NOT, we need your guidance on
what supplementary record-keeping to add. We expect the answer is YES
because chain immutability is STRONGER than the standard accounting
log (an attacker who alters the audit log cannot alter the OTS-anchored
chain), but we want explicit sign-off.

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
  is not a free env-var flip. The `feature_flags` table requires
  `enable_reason` ≥40 characters AND `enabled_by_email` AND
  `enabled_at` — enforced at the database CHECK constraint. The
  ≥40-char requirement is where YOUR opinion identifier goes:

```
POST /api/admin/cross-org-relocate/enable-feature
Authorization: Bearer <Osiris admin token>
Content-Type: application/json

{
  "reason": "Outside-counsel HIPAA opinion 2026-XX-XX, doc-ID YYYYY: \
            cross-org relocate covered under substrate-class BAA per \
            §164.504(e) BA-to-BA inapplicability; §164.528 disclosure \
            accounting satisfied via triple-source attestation chain."
}
```

After the flip, the row in `feature_flags` carries this reason
permanently (the table is append-only via DELETE trigger). An auditor
reading the substrate state can recover WHY the feature was enabled,
WHO enabled it, and WHEN.

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
- Migration 279/280/281 ordering idempotent.
- Substrate invariant triplet (check + display_name +
  recommended_action) all in lockstep.

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

1. Written opinion on the three §-questions in §2.
2. If your answer to any §-question is "no" or "addendum required,"
   the addendum language.

**Timeline:**

- We are not on a customer deadline. Zero customers are blocked today;
  one is in pipeline (~Q3 2026 hospital network acquisition).
- Engineering is shipped + tested + deployed behind the flag. Your
  review can run async over multi-week without blocking other work.
- When you return your opinion, the flag flip is a one-API-call
  operational change. We will reference your opinion identifier in the
  ≥40-character `enable_reason` field at flip time so the substrate's
  cryptographic record contains the legal authority for enabling the
  feature.

**What we will hand back to you for your file:**

- The bundle ID of the `enable_cross_org_site_relocate`
  `admin_audit_log` row (which contains your opinion reference).
- The `feature_flags.cross_org_site_relocate` row's `enabled_at` +
  `enabled_by_email` + `enable_reason`.
- A confirmation page screenshot from our admin tooling.

---

## 7. Contact

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

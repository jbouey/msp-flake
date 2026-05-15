# Gate A — Task #90 follow-up: `partner_admin_transfer` BAA-gate decision

**Date:** 2026-05-15
**Scope:** Determinate decision on whether `partner_admin_transfer` is wired
into `BAA_GATED_WORKFLOWS` (active), upgraded with a new partner-BA-
subcontractor predicate, gated transitively against client orgs, or left in
`_DEFERRED_WORKFLOWS` with a refined permanent reason.
**Format:** 7-lens Class-B Gate A (Counsel + Auditor load-bearing).
**Verdict:** **(A) — NO GATE. Update `_DEFERRED_WORKFLOWS` reason to make
the disposition permanent, then close #90.**

---

## 200-word summary

`partner_admin_transfer.py` is a partner-internal admin-role swap between
two `partner_users` of the SAME `partner_org`. The full state machine
(initiate / accept / cancel / prefs / sweep — 7 endpoints) references
ZERO `client_org_id`, zero `sites.client_org_id`, zero CE state.
Attestations anchor at the synthetic `partner_org:{partner_id}` namespace,
never at a site. `baa_enforcement_ok()` is client-org-scoped — there is
no defensible org to pass it. Schema check: `partners` has no
`ba_signed_at` / `subcontractor_agreement_id` / equivalent column today,
so option (B) cannot stand on existing data. Master BAA v1.0-INTERIM's
"Subcontractor" clause (§3.4) binds OSIRIS's subcontractors, not the
partner-MSP (who is independently a BA of its own CE clients on a paper
contract this platform never sees). The right HIPAA posture for a
partner-internal HR/IT action: NOT gated by the CE's BAA with Osiris.
The action itself does not "perform services involving PHI" — the new
admin's SUBSEQUENT BAA-gated actions are already gated by the existing
five live workflows. Outcome: 5-line `_DEFERRED_WORKFLOWS` reason
refresh + a one-line CLAUDE.md note. No new predicate, no schema work,
no commercial-paper dependency. Close #90.

---

## Evidence pulled

### Schema check (Maya)

```
grep ALTER TABLE partners <migrations> | uniq columns:
  contact_name, billing_email, api_key, onboarded_at,
  auth_provider, oauth_subject, oauth_tenant_id, oauth_email, oauth_name,
  oauth_access_token_encrypted
```

NO `ba_signed_at`, `ba_subcontractor_agreement_id`, `baa_*`, or
`subcontractor_*` column exists on `partners` today. Option (B) (gate
with a new partner-BA-subcontractor predicate) cannot stand on existing
data without first designing + migrating that column, sourcing the data
(commercial paper between Osiris ↔ MSP — outside this platform), AND
backfilling every existing partner. That is an entire counsel-grade
sub-project, not a #90 follow-up.

### State-machine surface (Steve)

`partner_admin_transfer.py` endpoints + helpers, exhaustively grepped
for any `client_org` reference:

```
grep -n "client_org" partner_admin_transfer.py
  → 0 matches
```

What it DOES touch:
- `partner_admin_transfer_requests` (partner_id, target_email, status)
- `partner_users.role` (admin ↔ tech ↔ billing for the SAME partner_id)
- Attestation anchor `partner_org:{partner_id}` — synthetic, no site,
  no client_org, by design (Session 216 anchor-namespace convention)

What it does NOT touch:
- `sites.*` — never queried
- `client_orgs.*` — never queried
- `compliance_bundles.*` — never queried
- `site_credentials.*` — never queried

The endpoint is functionally equivalent to "two MSP staff trade their
internal admin-console roles." It is not a CE-self-service action.

### Counsel doc check (Counsel)

`docs/legal/MASTER_BAA_v1.0_INTERIM.md`:
- §1 (Definitions) line 26: "Subcontractor" = a person to whom **Business
  Associate** delegates a function involving PHI. The Business Associate
  in this BAA IS OSIRIS. Subcontractor = Osiris's own subs (Hetzner,
  Stripe, etc.) — listed in Exhibit A.
- §3.4 (Subcontractors): obligation on Osiris to flow-down BAA terms to
  ITS subprocessors, with 30-day notice to CE on additions.
- Exhibit C (BAA-gated workflows): names owner_transfer,
  cross_org_relocate, evidence_export, new_site_onboarding,
  new_credential_entry, ingest. **`partner_admin_transfer` is not
  named.** That is not an omission — Exhibit C scope is "CE self-service
  workflows on the platform"; partner-internal HR actions sit outside.

The partner-MSP is NOT Osiris's subcontractor under this BAA — the
partner-MSP is independently a BA of its own CE clients. The Clinic →
MSP-Partner → Osiris 3-tier chain is THREE separate BAA contracts, not
a flow-down chain under one. Osiris's BAA with the CE does NOT govern
the partner-MSP's internal admin role assignments.

---

## 7-lens run

### 1. Steve (CCIE / Principal SWE)

**State-mutation classification:** CE-state-mutating? **NO.** The
operation mutates `partner_users.role` for one row in one partner_org.
No client_org, no site, no credential, no bundle, no audit
disclosure-accounting row is touched. The "what does it advance" answer
is "an MSP staffer's portal permissions" — analogous to changing a
sysadmin's group membership in AD. The five live BAA-gated workflows
all advance state that has direct PHI implications for a specific CE.
This one does not. **No gate.**

### 2. Maya (Backend / Schema)

**Schema lookup result:** `partners` has zero BA-subcontractor-state
columns today. Adding one is a 4-step project:
  1. Design the column semantics (signed_at? expires_at? version?)
  2. Source the data (a paper contract Osiris signs with each partner-
     MSP — Osiris's commercial-paper workflow, not the platform's)
  3. Mig + backfill every existing partner (the demo partners would
     all be NULL → fail-closed → block every partner login → outage)
  4. Wire a new predicate, new lockstep entry, new CI gate, new
     substrate invariant

That is task-#56-class work (counsel/legal artifact), not #90 follow-
up. Don't conflate them. **No gate via option (B) today.**

### 3. Carol (Security)

**Is there an attack surface that requires gating?** Consider: a partner-
MSP whose paper BA-Subcontractor agreement with Osiris has expired or
been revoked. Can they still operate their portal?
- Answer: yes — but every BAA-gated action they attempt against ANY
  client_org will already fail at the existing 5 gates because
  `baa_enforcement_ok()` checks the CE's signature, and a revoked
  partner-MSP can't have a current CE-BAA either (the CE's BAA flows
  through the partner as BA → if the partner is no longer a BA, the
  CE-BAA fails its own integrity check).
- The right machine-enforced gate for "partner BA-Subcontractor
  revoked" is **partner-session disablement** (auth-layer kill switch),
  not gating one specific endpoint among 30+ partner-mutating endpoints.
  Carol's correct ask is "where's the partner-disablement runbook" —
  that is its own backlog item (`partner_user_status='suspended'`
  check on `require_partner` — task to file). **Not #90's scope.**

### 4. Coach (consistency / minimum-mechanism)

**Theater check.** If `partner_admin_transfer` were gated by the
client-org BAA predicate via partner→managed-client_orgs lookup
(option C), the predicate body would be: "block partner_admin_transfer
if ANY managed client_org has no BAA on file." That is:
  - Over-reach — one bad CE-BAA blocks an HR role swap touching that
    bad CE zero times.
  - False positive guaranteed at deploy — demo posture means every
    org returns FALSE from `baa_enforcement_ok()` today; a partner
    managing 12 clients in demo posture would lock out indefinitely.
  - Theater — gating the role swap does not prevent the new admin
    from doing anything; the new admin's actual BAA-gated work is
    gated at the actual gate. Layering a second false gate adds zero
    safety + one outage class. **Reject option (C).**

Minimum-mechanism: a 5-line update to `_DEFERRED_WORKFLOWS[
partner_admin_transfer]` reason, making the disposition permanent
(not "pending Gate A"), is the correct move. Then `assert_workflow_
registered("partner_admin_transfer")` keeps doing its job (any future
typo'd attempt to wire it raises). CI gate already covers this.

### 5. Auditor (OCR / §164.504(e)) — LOAD-BEARING

**§164.504(e) test:** "Performs services involving PHI." Does the
partner_admin_transfer endpoint itself create / receive / maintain /
transmit PHI? **No.** It mutates a role string in a partner_users row.
PHI does not flow through the endpoint, is not requested as input, is
not produced as output. The endpoint's payload (target_email + role)
is partner-staff metadata.

**§164.524 individual-access counter-check:** does gating
partner_admin_transfer impede a CE's right to access their own PHI?
Indirectly — if the partner's only admin is being swapped and the
gate blocks the swap, the partner ops-team is paralyzed → cascading
into CE access requests being delayed. So gating partner_admin_transfer
has §164.524 downside risk + zero §164.504(e) upside. **OCR-aligned:
no gate.**

Auditor's preferred answer aligns with Counsel + Carol + Steve.

### 6. PM (effort + deadline)

- Option (A) — NO GATE + reason refresh: 5-line `.py` edit + 1-line
  CLAUDE.md note + tasks #90 close + 0 new tests (the existing
  `test_baa_gated_workflows_lockstep.py` already permits deferred
  workflows). **Effort: 15 minutes total.**
- Option (B) — new partner-BA-subcontractor predicate: schema
  migration + data sourcing + paper-contract workflow + endpoint
  wiring + lockstep + tests + substrate invariant. **Effort: 4-6
  weeks + a counsel engagement.** Outside #90 scope.
- Option (C) — transitive client-org gate: 30 min to wire, but
  theater + outage risk + Coach veto. **DO NOT DO.**
- Option (D) — defer-indefinitely without a clear answer: leaves
  #90 open forever as a known-unknown. **DO NOT DO** — the answer
  is determinate today.

Option (A) is the minimum-mechanism correct answer. Ship it.

### 7. Counsel (Attorney) — LOAD-BEARING

The §164.504(e) test is: "BAA must be in place before the BA
**performs services involving PHI**." The partner_admin_transfer
endpoint does not perform services involving PHI — it administers
partner-staff identity. The new admin's SUBSEQUENT actions (advancing
sensitive workflows for a specific client_org) ARE gated by the
existing five workflows. That is the correct layer.

A separate question is the Osiris ↔ partner-MSP BA-Subcontractor
relationship. That relationship is governed by a commercial paper
contract between Osiris and each partner-MSP, NOT by the BAA
between Osiris and the CE. The platform does not today track
that commercial-paper state, and tracking it is a sub-project of
Task #56 (master BAA contract) — not #90.

Counsel's verdict: **partner_admin_transfer correctly sits OUTSIDE
the v1.0-INTERIM Exhibit C scope. It MAY enter scope in a future
"partner-BA-subcontractor agreement" v3 contract, but not via #90.**

---

## Build-ready edit

### File: `mcp-server/central-command/backend/baa_enforcement.py`

Replace the existing `_DEFERRED_WORKFLOWS["partner_admin_transfer"]`
tuple value with a refined permanent reason:

```python
_DEFERRED_WORKFLOWS = {
    "partner_admin_transfer": (
        "OUT OF SCOPE — partner-internal HR/IT action with no client_org_id "
        "to resolve. The endpoint mutates partner_users.role for one row in "
        "one partner_org; touches zero CE state, zero sites, zero "
        "compliance_bundles. Anchors attestations at synthetic "
        "partner_org:{partner_id}, not at a site. §164.504(e) is scoped to "
        "'performs services involving PHI' — this endpoint does not. The "
        "new admin's SUBSEQUENT BAA-gated actions are already gated by the "
        "five active workflows (owner_transfer, cross_org_relocate, "
        "evidence_export, new_site_onboarding, new_credential_entry). The "
        "Osiris↔partner-MSP BA-Subcontractor relationship is governed by a "
        "separate commercial paper contract (Task #56), NOT by this BAA. "
        "Decision recorded at audit/coach-90-partner-admin-transfer-"
        "decision-gate-a-2026-05-15.md."
    ),
    "ingest": (
        # ... existing unchanged ...
    ),
}
```

### File: `CLAUDE.md` — append to the BAA-enforcement bullet

```
The deferred-workflow `partner_admin_transfer` is now PERMANENTLY out
of scope (Task #90 Gate A 2026-05-15): partner-internal HR action with
no client_org_id; §164.504(e) test fails ("performs services involving
PHI" — no). New admin's subsequent BAA-gated actions are already
gated by the five active workflows. The Osiris↔partner-MSP BA-Sub-
contractor relationship is commercial-paper scope (Task #56), not
Exhibit C scope. Verdict: audit/coach-90-partner-admin-transfer-
decision-gate-a-2026-05-15.md.
```

### Close #90

After the edit lands, `_DEFERRED_WORKFLOWS` has three entries (down from
five was never true — partner_admin_transfer + new_site_onboarding +
new_credential_entry + ingest). The two onboarding workflows
(`new_site_onboarding`, `new_credential_entry`) are now LIVE in
`BAA_GATED_WORKFLOWS` per #90 main work. After this edit:
- ACTIVE: owner_transfer, cross_org_relocate, evidence_export,
  new_site_onboarding, new_credential_entry (5).
- DEFERRED-PERMANENT: partner_admin_transfer ("out of scope").
- DEFERRED-COUNSEL-QUEUE: ingest (Task #37).

Task #90 closes. No new task filed.

---

## Verdict line

**(A) — NO GATE.** Update `_DEFERRED_WORKFLOWS[partner_admin_transfer]`
reason to make the out-of-scope disposition permanent + cite this Gate
A audit. Add one CLAUDE.md sentence. Close #90. Effort: 15 minutes.
No counter-Gate-B required (the change is a 5-line text refresh of an
existing data structure — Gate B applies to new-system / new-deliverable
work; refreshing a reason-string in an already-Gate-B-passed system
is in-place doc, not new mechanism).

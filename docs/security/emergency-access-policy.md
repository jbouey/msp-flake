# Emergency Access Policy (Session 205 — Phase 14 T0)

<!-- updated 2026-05-16 — Session-220 doc refresh -->

Emergency access = a time-bounded privileged route into a customer's
appliance for incident response. On OsirisCare, the mechanism is the
signed fleet-order pair (`enable_emergency_access` /
`disable_emergency_access`) which activates a WireGuard tunnel from
Central Command into the site appliance for N minutes.

This doc is the written policy the Session 205 round table called
for. It is **enforced by code** (T0 shipped in Phase 14) and
**extended by workflow** (T1–T4 design).

**Authority chain:** This policy implements Counsel's Rule 3 (no
privileged action without attested chain of custody, 2026-05-13 gold
authority). Cite `docs/POSTURE_OVERLAY.md` (v2.2, 2026-05-16) as the
canonical pointer-index for the privileged-access-chain topic area.

---

## Non-negotiables (enforced in code today)

1. **Attestation-first.** Every privileged access event is written as
   a signed, hash-chained, OTS-anchored `compliance_bundles` row
   **BEFORE** the fleet order is signed. If the attestation fails,
   the order is refused. Verified customers can reproduce the proof
   offline from the auditor-kit ZIP.

2. **Named human.** `--actor-email <you@domain>` is required on every
   privileged fleet order. "fleet-cli" / "admin" / "system" are not
   acceptable actors — they break tie to a specific individual for
   HIPAA §164.308(a)(3)(ii)(A) supervision requirements.

3. **Documented reason.** `--reason "..."` with ≥ 20 characters is
   required. This text is persisted into the WORM evidence bundle and
   surfaces in the customer portal and auditor kit. Describing the
   incident, change ticket, or operational justification.

4. **Rate limit.** 3 `enable_emergency_access` events per site per
   rolling 7 days. Additional events require `--override-rate-limit`
   + reason referencing an active incident. Rate-limit invocations are
   themselves attested.

5. **Cryptographic verifiability.** The event bundle is signed with
   the server's Ed25519 key, hash-chained to the prior bundle for the
   site, OTS-anchored via the existing Merkle-batch worker, and
   published to the customer portal + auditor-kit download endpoint.
   An auditor validates WITHOUT requesting logs from us.

## Policy surface (customers see this)

The customer portal's evidence chain view shows every
`check_type='privileged_access'` bundle alongside drift and
remediation bundles. The customer can:

- View the event: who, when, why, how long, which order
- Independently verify the hash chain back to their site's genesis
- Confirm the Ed25519 signature against our published server pubkey
- Confirm the OTS anchor against a Bitcoin calendar

## Bootstrap exceptions (documented gaps)

The existing Session 205 `enable_emergency_access` orders
`f4569838-665e-40a7-845a-fb893e4e5823` and
`5c984189-1ed1-4d09-bff6-b47558dbac6d` pre-date this policy and are
recorded in `admin_audit_log` as non-conforming Session 205 ops
events. Both failed signature verification on the appliance side and
never actually exercised privilege.

From this commit forward, privileged fleet-order creation without
attestation is structurally impossible (fleet_cli enforcement).

---

## T1 — Second-person approval (design; next session)

A privileged-access request enters a queue. A different partner admin
(or customer admin, for high-risk categories) must approve within
30 min. The fleet order is only signed AFTER the second approval is
recorded in the attestation bundle.

Schema additions (preview, Phase 14.1):
```sql
CREATE TABLE privileged_access_requests (
    id UUID PRIMARY KEY,
    site_id VARCHAR NOT NULL,
    event_type VARCHAR NOT NULL,
    initiator_email VARCHAR NOT NULL,
    initiator_role VARCHAR NOT NULL, -- 'partner_admin' | 'client_admin'
    reason TEXT NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_by_email VARCHAR,
    approved_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL,
    fleet_order_id UUID REFERENCES fleet_orders(id),
    attestation_bundle_id VARCHAR
);
```

Approval UI endpoint: `POST /api/partners/me/privileged-access/{request_id}/approve`.

## T2 — Notification chain (design; next session)

On initiation: email partner admin(s) + customer admin(s) + internal
Security Slack. Link to the approval request. Before any downstream
action.

On activation (post-approval): audit entry + customer portal banner
+ updated attestation bundle with timing.

On teardown: audit close-out + emails to same parties with evidence
bundle_id link.

## T3 — Hardware-backed signing (pre-GA)

`/app/secrets/signing.key` on VPS disk is acceptable for pilot, not
for GA. Migration path:

Option A — Cloud KMS (preferred): server calls AWS KMS / GCP Cloud
HSM to sign each order. Signing private key never exists in process
memory. Requires adaptor code + IAM policy + KMS key rotation plan.

Option B — Yubikey mediation: signing requires physical-presence
touch on a registered Yubikey held by an on-call partner admin. Low
operational cost, high security. Requires new daemon handshake:
signed orders carry a Yubikey attestation.

Prep work (Phase 14.2):
```sql
ALTER TABLE fleet_orders
  ADD COLUMN signing_method VARCHAR(16) NOT NULL DEFAULT 'file',
  ADD COLUMN signing_key_fingerprint VARCHAR(16);
```

## T4 — Shamir quorum for fleet-wide orders (enterprise tier)

Fleet-wide orders (applying to ALL sites of a partner) require 2-of-3
Shamir split across partner admins. Individual-site orders (today's
default) remain single-signer. Implementation via the existing
`signing_method` column: new value `'shamir-2-of-3'` with a
coordination endpoint that collects shares and reassembles the key
for the single sign operation.

---

## Session 219–220 hardening (2026-05-09 — 2026-05-16)

Five chain-of-custody surfaces hardened this period. All five are
load-bearing to the inviolable rule above; each was either a chain
hole closed or a new enforcement layer added.

1. **`delegate_signing_key` registered as privileged (mig 305,
   Session 220).** Weekly audit cadence found
   `appliance_delegation.py:258 POST /delegate-key` was zero-auth —
   anyone could mint an Ed25519 signing key bound to any
   caller-supplied `appliance_id`, then sign evidence-chain entries
   against the customer-facing attestation chain. Functionally
   equivalent to `signing_key_rotation` which was already privileged.
   Added to all 3 lockstep lists: `fleet_cli.PRIVILEGED_ORDER_TYPES`,
   `privileged_access_attestation.ALLOWED_EVENTS`, mig 305
   `v_privileged_types`. Prod audit at fix time: 1 historical row in
   `delegated_keys`, synthetic test data, already expired — **zero
   customer exposure**.

2. **Privileged-chain trigger functions are ADDITIVE-ONLY (Session
   220 lock-in).** Gate B v1 caught a silent weakening of
   `enforce_privileged_order_attestation` when adding
   `delegate_signing_key` to `v_privileged_types`: the mig 305 first
   draft rewrote the function body from scratch, dropping the
   `parameters->>'site_id'` cross-bundle check + the
   `PRIVILEGED_CHAIN_VIOLATION` error prefix + the `USING HINT`
   clause. NEVER rewrite the trigger function body from scratch when
   extending `v_privileged_types` — copy the prior migration's
   function body VERBATIM and append only the new array entry. Lockstep
   checker `scripts/check_privileged_chain_lockstep.py` proves LIST
   parity; function-body diff gate is task #111.

3. **L1 escalate-action false-heal closure (Session 219, two-layer
   fix).** 9 builtin Go rules in
   `appliance/internal/healing/builtin_rules.go` use
   `Action: "escalate"`. Pre-fix the daemon's escalate handler
   returned no `"success"` key; backend `main.py:4870` persisted
   daemon-supplied tier without server-side check. Net effect:
   **1,137 prod L1-orphans** across 3 chaos-lab check_types over 90
   days. Two-layer fix shipped: Layer 1 daemon (explicit
   `success: false` on escalate + fail-closed defaults); Layer 2
   backend (downgrades `resolution_tier='L1' → 'monitoring'` when
   `check_type in MONITORING_ONLY_CHECKS`). Substrate invariant
   `l1_resolution_without_remediation_step` (sev2) detects regressions.
   Go AST ratchet
   `appliance/internal/daemon/action_executor_success_key_test.go`
   pins the success-key invariant.

4. **L2 resolution requires attested decision row (mig 300, Session
   219).** Substrate invariant `l2_resolution_without_decision_record`
   (sev2) caught 26 north-valley-branch-2 incidents tagged
   `resolution_tier='L2'` with no matching `l2_decisions` row — a
   ghost-L2 audit gap violating the data-flywheel + attestation chain.
   Fix: introduce `l2_decision_recorded: bool` gate; refuse to set L2
   without the audit row — escalates to L3 instead. Pinned by
   `tests/test_l2_resolution_requires_decision_record.py`.

5. **BAA-enforcement triad — Counsel R6 machine-enforcement (Session
   220, Tasks #52/#91/#92/#98).** Triad: List 1 =
   `baa_enforcement.BAA_GATED_WORKFLOWS` (5 active: `owner_transfer`,
   `cross_org_relocate`, `evidence_export`, `new_site_onboarding`,
   `new_credential_entry`); List 2 = enforcing callsites
   (`require_active_baa`, `enforce_or_log_admin_bypass`,
   `check_baa_for_evidence_export`); List 3 = sev1 substrate invariant
   `sensitive_workflow_advanced_without_baa` (`assertions.py`). CI
   gate `tests/test_baa_gated_workflows_lockstep.py` pins List 1 ↔
   List 2. `_DEFERRED_WORKFLOWS`: `partner_admin_transfer` (#90 —
   partner-internal role swap, zero PHI flow), `ingest` (#37, counsel
   queue). Build-time (lockstep) + runtime (invariant scan) coverage
   for all 5 active workflows.

6. **Substrate-engine per-assertion `admin_transaction` isolation
   (Session 220, commit `57960d4b`).** Pre-fix the Substrate Integrity
   Engine held ONE `admin_connection` for all 60+ assertions per 60s
   tick. One `asyncpg.InterfaceError` poisoned the conn — every
   subsequent assertion in the tick blinded (including the
   privileged-chain invariants). Fix: per-assertion
   `admin_transaction(pool)` blocks. One InterfaceError now costs 1
   assertion (1.6% tick fidelity), not all 60+ (100%). CI gate
   `tests/test_assertions_loop_uses_admin_transaction.py` pins the
   design.

---

## Quarterly / ad-hoc audit workflow

The platform's thesis is that customers + auditors verify the chain
themselves, NOT that we produce a quarterly report. Still, for
operational anomaly detection:

- Prometheus alert when `osiriscare_privileged_access_events_7d` > 10
  (fleet-wide)
- Prometheus alert when any single site gets ≥ 2 events in 24h
- Every event email-alerts the internal security list

The metrics are derived from the attestation bundles in
`compliance_bundles WHERE check_type='privileged_access'` — same
source of truth as customer-facing evidence. No separate
"audit review" database to reconcile.

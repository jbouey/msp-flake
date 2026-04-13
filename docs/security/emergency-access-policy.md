# Emergency Access Policy (Session 205 — Phase 14 T0)

Emergency access = a time-bounded privileged route into a customer's
appliance for incident response. On OsirisCare, the mechanism is the
signed fleet-order pair (`enable_emergency_access` /
`disable_emergency_access`) which activates a WireGuard tunnel from
Central Command into the site appliance for N minutes.

This doc is the written policy the Session 205 round table called
for. It is **enforced by code** (T0 shipped in Phase 14) and
**extended by workflow** (T1–T4 design).

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

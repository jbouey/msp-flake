# HSM / Hardware-Mediated Signing Integration Design (Phase 14 T3)

## Problem

`/app/secrets/signing.key` lives on disk inside the mcp-server
container. Any process with read access to the filesystem can sign
arbitrary orders — including privileged orders that authorize
actions against customer appliances. The Privileged-Access Chain
of Custody (CLAUDE.md) enforces that every signed order carries a
linked attestation bundle, but the chain is only as trustworthy as
the key custody. An attacker with root-on-VPS exfiltrates the key
and fabricates both the attestation AND the order.

HSM (hardware security module) moves the private key out of process
memory and off disk. The signing operation becomes a remote call;
the key material never crosses a process boundary.

## Design choices

### Option A — AWS KMS (preferred)

- Ed25519 not yet widely supported in KMS; use RSA-PSS or ECDSA with
  SHA-256 instead. Daemon already has ecdsa/rsa verify paths for
  non-order contexts; extend to orders.
- KMS key policy restricts `kms:Sign` to specific IAM principals
  (the mcp-server service role only). Administrator cannot
  sign without assume-role attestation.
- Each signed operation is logged to CloudTrail with the assume-role
  identity. Audit trail extends beyond our compliance_bundles.
- Key rotation handled by KMS (schedule-triggered).

Integration surface:
- Adaptor `shared/hsm_signer.py` exposes the same `sign_order()`
  signature as fleet_cli's current inline signer.
- `SIGNING_METHOD=kms` env selects the KMS adaptor.
- `KMS_KEY_ID` env identifies the key.
- Fleet order's `signing_method` column recorded as 'kms' +
  `signing_key_fingerprint` as the first 16 hex of the public key.

### Option B — Google Cloud HSM

Mirror of AWS KMS. Same integration surface. Slightly different IAM.

### Option C — Yubikey (partner admin presence)

- Signing requires physical-presence touch on a registered Yubikey.
- Partner admin with the key present authorizes each signing op.
- Highest assurance; highest operational friction. Use for the
  signing_key_rotation + bulk_remediation event types only;
  emergency_access stays on KMS for pager-responsiveness.
- Yubikey attestation certificate embedded in the signed payload;
  daemon verifies the cert chain before honoring the order.

### Option D — Shamir-split in-app (T4, enterprise tier)

- 2-of-3 shares across partner admins. Reassembled in memory only
  for the single signing operation. Rejected if any share expires.
- Pure software; high ops burden (shares must be produced each time).
- Enterprise customers who refuse any cloud key custody.

## Backward compat

Migration 177 already shipped: `fleet_orders.signing_method VARCHAR(16)
NOT NULL DEFAULT 'file'`. Existing code path continues with
`signing_method='file'`. New code selects alternate signer via env
without schema change.

Daemon side: `verify.go`'s `OrderVerifier` already supports multiple
pubkeys (current + previous) via `SetPublicKeys`. A KMS pubkey
published in the checkin response under `server_public_keys[0]`
with the file-based pubkey under `[1]` gives us a rotation window
where both keys are accepted.

## Implementation sequence (pre-GA)

1. Pick cloud (AWS vs GCP) — follow existing infra provider
2. Stand up KMS key + IAM policy in a dedicated AWS account
3. Write `hsm_signer.py` adaptor with the existing sign_order() contract
4. Integrate as opt-in via `SIGNING_METHOD=kms` env on one VPS
5. Issue a `signing_key_rotation` privileged event (itself chained
   + attested; first real exercise of that event type)
6. Publish new pubkey via checkin; wait for all appliances to pick it up
7. Flip `SIGNING_METHOD=kms` to default; old pubkey stays as previous

Full migration window ≈ 1 week. Daemon rebuild NOT required —
`OrderVerifier.SetPublicKeys` already handles rotation.

## What this does NOT fix

- Does not prevent an attacker with mcp-server process access from
  sending authorized KMS sign calls. KMS enforces IAM policy, not
  application correctness. Defense-in-depth via:
  - Phase 14 T1 second-person approval (privileged_access_requests)
  - Phase 14 chain-enforcement triggers (migrations 175+176)
  - Rate limiting (3/site/week; adjustable post-incident)
  - Notifier (Phase 14 T2) alerts customer + security on every event
- Does not prevent physical compromise of the VPS. Process memory
  can still be exploited to request a sign. This is why the
  notifier is critical — the customer sees every sign within
  minutes, not quarterly.

## Observability

- `fleet_orders.signing_method` column tracks per-order custody
- CloudTrail logs all KMS sign calls
- Prometheus gauge `osiriscare_hsm_sign_latency_ms` (when integrated)
- Alerts: >1s sign latency, any sign with unrecognized principal,
  any sign without a matching privileged_access_request
  (T1 enforcement double-check at the signer layer)

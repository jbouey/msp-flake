# SECURITY ADVISORY — Vault Phase C cutover (TEMPLATE)

**This is a TEMPLATE.** Activate at the moment of cutover (Task #48
soak complete + operator flips `SIGNING_BACKEND=vault`). Rename to
`SECURITY_ADVISORY_YYYY-MM-DD_VAULT_CUTOVER.md` with the cutover date.
Fill placeholders. Publish to `docs/security/` + link from the
auditor kit README on the next ship.

**Severity classification:** disclosure-grade (NOT incident-grade) —
the cutover IS a security improvement (key isolation onto a
dedicated host), but it materially changes the signing-key blast
radius + the recovery posture, which auditors + customers should be
informed of independently of any specific incident.

---

## Effective date

**Cutover timestamp:** {{CUTOVER_TIMESTAMP_UTC}}
**Disclosure publication:** {{PUBLICATION_DATE}} (target: ≤7 days after
cutover per Counsel Rule 6 + §164.308(a)(6))
**Disclosure recipients:** all client-portal-active customers (in-app
banner + email); auditor kit README updated in the next ship.

---

## What changed

Before {{CUTOVER_TIMESTAMP_UTC}}, the Ed25519 signing key that
attests every customer-facing evidence bundle + fleet order +
privileged-access bundle lived in a file at
`/opt/mcp-server/secrets/signing.key` on the Central Command VPS
(178.156.162.116). It was loaded into mcp-server's process memory
at container start. Access was gated by Unix file permissions
(600 / appuser:appuser) + container bind-mount scope.

After {{CUTOVER_TIMESTAMP_UTC}}, the same Ed25519 key (same pubkey
byte-for-byte; see "Reversibility" below) lives in HashiCorp Vault
Transit on a dedicated Hetzner host (89.167.76.203, WireGuard
10.100.0.3). The key material NEVER leaves Vault; signing operations
are issued via Vault's Transit API. mcp-server holds an AppRole
authentication token (rotatable independently) + initiates each
sign call over the WireGuard tunnel.

The on-disk `signing.key` file remains at the same path for **30
days** post-cutover as an emergency fallback (per Phase D plan
`docs/security/vault-transit-migration.md`). After the 30-day window,
Phase D retires the disk key + publishes a follow-on
`SECURITY_ADVISORY_YYYY-MM-DD_SIGNING_KEY_ISOLATION.md`.

## Why we changed it

OsirisCare counsel + outside HIPAA counsel concur that maintaining a
private-key file on the same host that runs the application
substrate is a **non-zero-trust posture** under HIPAA §164.312(a)(1)
access-control reasoning. A successful compromise of the Central
Command VPS would expose the signing key + would not be detectable
from key-use audit data (because the attacker would have the legitimate
process's read access). Moving the key into Vault Transit on a
hardened, dedicated host means:

1. A successful Central Command compromise does NOT expose key material.
2. Every signing operation generates a Vault audit-log row, which is
   physically isolated on the Vault host + readable by operator-only
   credentials.
3. Key rotation is decoupled from application restarts — Vault can
   rotate the key while mcp-server continues to sign at the cached
   key version.
4. The substrate gains a `signing_backend_drifted_from_vault`
   invariant (sev2) — see "New detective controls" below — that
   surfaces any divergence between the configured signing backend +
   the backend that actually signed recent fleet orders.

## What did NOT change

- The signing key's PUBKEY is byte-for-byte the same before + after
  cutover. Appliances continue to verify signatures against the
  pubkey they have been pinned to since first checkin. **No
  appliance-side action required.** No fleet rotation, no
  `rotate_server_pubkey` ceremony (the original plan; dropped per
  Task #47).
- The auditor kit determinism contract (per CLAUDE.md
  §"Auditor-kit determinism contract") is preserved. Two consecutive
  kit downloads with no chain progression produce byte-identical
  ZIPs both before + after cutover; the signing backend is NOT part
  of the determinism contract.
- The privileged-access chain of custody (mig 175 + 305 + the
  three lockstep lists) is preserved. `signing_key_rotation`
  remains a privileged event; the Vault cutover itself does NOT
  rotate the key (same pubkey preserved), so it does NOT trigger
  the privileged-event chain.

## New detective controls (shipped 2026-05-16)

- **`vault_signing_key_versions`** table (mig 311): records the
  Vault key versions that have been observed at startup. Operator
  manually approves each new version (`known_good=TRUE`); any
  unexpected version triggers the startup invariant.
- **`INV-SIGNING-BACKEND-VAULT`** startup invariant: probes Vault on
  every mcp-server container start + verifies the current Vault key
  version matches an approved row. Failure is non-fatal (container
  starts anyway with `ok=False detail=`) but credibility-eventing
  to the substrate-health panel.
- **`signing_backend_drifted_from_vault`** substrate invariant
  (sev2): every 60s, compares observed `fleet_orders.signing_method`
  over the last hour against `SIGNING_BACKEND_PRIMARY` env. Catches
  the class where mcp-server is supposed to be signing via Vault
  but a code path silently fell back to file.

## Customer-side impact

**None.** Customers, partners, and auditors continue to interact
with the platform exactly as before. The cutover is operator-side
infrastructure; no API contract changes, no UI changes, no signed-
artifact format changes.

The auditor kit ZIP is byte-identical for the same chain head
pre/post-cutover (modulo the kit_version pin, which has NOT
advanced — confirmed by `pubkeys.json` SHA equality).

## Customer-side verification

Customers can verify the pubkey continuity themselves:

```bash
# Pre-cutover (download an auditor kit dated before the cutover)
unzip -p auditor-kit-<pre-cutover>.zip pubkeys.json | jq -r '.osiriscare_signing_pubkey_ed25519_hex'

# Post-cutover (download an auditor kit dated after the cutover)
unzip -p auditor-kit-<post-cutover>.zip pubkeys.json | jq -r '.osiriscare_signing_pubkey_ed25519_hex'

# These two MUST match. If they don't, the cutover failed import-
# preserve + you should immediately email security@osiriscare.com.
```

## Reversibility

The cutover is reversible at any time via the rollback runbook
(`docs/runbooks/VAULT_ROLLBACK_RUNBOOK.md`). Same-pubkey-preserved
import means the rollback requires only a backend env-var flip +
container restart on the OsirisCare side — no fleet-wide action,
no customer-side action, no re-signature of any historical
evidence. Rollback severity classes (GREEN / YELLOW / RED) are
documented in the runbook.

For 30 days post-cutover, `signing.key` is retained at the same
disk path as emergency fallback. After Phase D (`docs/security/
vault-transit-migration.md` §"Phase D: retire"), the disk file is
removed + the rollback becomes a longer ceremony — but the cutover
window itself is still trivially reversible for the 30-day window.

## Open items + future advisories

- **Phase D (30 days post-cutover):** retire the on-disk
  `signing.key` file + publish
  `SECURITY_ADVISORY_YYYY-MM-DD_SIGNING_KEY_ISOLATION.md`
  disclosing the full pre-cutover blast-radius for the disk-born
  key + the new post-Phase-D posture.
- **Vault host hardening:** the Vault host CX11 instance runs
  NixOS-pinned configuration. Independent third-party audit of
  the Vault host posture is scheduled for {{VAULT_AUDIT_DATE}}.

## Contact

- Security inquiries: security@osiriscare.com
- Counsel: {{HIPAA_COUNSEL_CONTACT}}
- Vault posture details: `docs/security/vault-transit-migration.md`
- Rollback procedure: `docs/runbooks/VAULT_ROLLBACK_RUNBOOK.md`

---

## Template change log

- 2026-05-16 — initial template — Task #49. Activate at cutover by
  renaming + filling `{{...}}` placeholders.

# vault_key_version_approved_without_attestation

**Severity:** sev1
**Display name:** Vault key approved without chain-of-custody attestation

## What this means (plain English)

A `vault_signing_key_versions` row has `known_good=TRUE` but its
`attestation_bundle_id` does NOT resolve to a valid `privileged_
access` compliance_bundles row with the expected synthetic anchor
`vault:<key_name>:v<key_version>`.

This is a chain-of-custody gap on the Vault trust root. The Vault
key is the trust anchor for every Ed25519 signature the platform
emits — an unattested known_good approval propagates silent trust
to every downstream evidence bundle, auditor kit, partner
attestation, and customer-facing record.

## Why sev1 (highest)

- Vault key is the trust root for the entire fleet's signing
  pathway.
- mig 328 CHECK closes the `attestation_bundle_id IS NULL` case at
  the DB layer. This invariant fires only on the dangling-reference
  cases the CHECK can't close.
- Recovery requires manual operator intervention + counsel review
  if the gap persisted across a customer auditor-kit download.

## Root cause categories

1. **compliance_bundles row DELETEd post-approval** — most serious.
   mig 151's `trg_prevent_audit_deletion` should prevent this, but
   future migration drift could weaken it. Verify trigger state
   via `\d+ compliance_bundles` in psql.

2. **Wrong attestation_bundle_id written by the endpoint** — the
   admin approval endpoint (the vault-key approval endpoint (Sub-B)) creates
   the attestation FIRST then writes the row. If the wrong
   bundle_id is captured (race, exception swallow), the dangling
   reference persists. Check the endpoint's audit_log for the
   approval event vs. compliance_bundles for the actual bundle.

3. **CHECK constraint was DROPped** — `vault_signing_key_versions_
   known_good_ck` from mig 328 should always be in place. A future
   migration that drops the constraint without re-adding it
   re-opens the NULL-id case. Verify constraint exists:
   ```sql
   SELECT conname, pg_get_constraintdef(oid)
     FROM pg_constraint
    WHERE conrelid = 'vault_signing_key_versions'::regclass
      AND contype = 'c';
   ```

4. **Direct psql UPDATE bypassing the endpoint** — operator ran
   `UPDATE … SET known_good=TRUE` directly without going through
   the attested endpoint. CHECK still requires attestation_bundle_
   id; operator may have passed a fake or unrelated id.

## Immediate action

1. **Identify the offending row:**
   ```sql
   SELECT v.id, v.key_name, v.key_version,
          v.attestation_bundle_id, v.approved_by, v.approved_at,
          cb.bundle_id IS NULL AS bundle_missing,
          cb.check_type, cb.site_id
     FROM vault_signing_key_versions v
     LEFT JOIN compliance_bundles cb
            ON cb.bundle_id = v.attestation_bundle_id
    WHERE v.known_good = TRUE
      AND (cb.bundle_id IS NULL
           OR cb.check_type != 'privileged_access'
           OR cb.site_id != 'vault:' || v.key_name || ':v' || v.key_version::text);
   ```

2. **Quarantine — flip known_good=FALSE immediately:**
   ```sql
   UPDATE vault_signing_key_versions
      SET known_good = FALSE
    WHERE id = <id>;
   ```
   This is allowed by the CHECK (the CHECK only enforces
   "approved-when-known-good", not the reverse).

3. **Verify Vault state.** If the row corresponds to a CURRENT
   Vault key version, the signing fleet may be using it. Check:
   ```bash
   ssh root@10.100.0.3 'vault kv get transit/keys/<key_name>'
   ```

4. **Investigate the writer.** Check git log for recent changes
   to the vault-key approval endpoint (Sub-B) or mig 328 CHECK constraint.

5. **Customer impact assessment.** Did any compliance_bundles
   (signed by this Vault key) get included in a customer
   auditor-kit download since the approval? If yes, counsel
   review required — auditor-supplied artifacts may need re-
   issuance.

## Verification

- Invariant clears on next 60s tick once known_good=FALSE or the
  attestation_bundle_id is replaced with a valid reference.

## Escalation

- **Bundle DELETE confirmed (root cause 1):** sev0 — page CISO +
  loop in counsel. Mig 151 trigger violation = forgery class.
- **Bundle never existed (root cause 2 endpoint bug):** sev1 hold
  for endpoint fix + re-approval workflow.
- **CHECK dropped (root cause 3):** sev1 — restore mig 328 CHECK
  via new migration. Audit all known_good=TRUE rows for the
  same class.

## Related runbooks

- `signing_backend_drifted_from_vault.md` (sev2 sibling — observed
  Vault key has no matching known_good row)
- mig 311 (initial vault_signing_key_versions table)
- mig 328 (this attestation_bundle_id binding)

## Change log

- 2026-05-17 — initial — #116 closure. Companion to mig 328
  schema-level CHECK extension. Gate A:
  audit/coach-116-vault-admin-approval-gate-a-2026-05-17.md.

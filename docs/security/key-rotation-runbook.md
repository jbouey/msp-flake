# Key Rotation Runbook

**Last updated:** 2026-04-13 (Phase 15 A-spec hygiene)
**Owner:** Security engineering
**Review cadence:** every 6 months OR on any suspected leak

This runbook covers the three cryptographic secrets the platform
depends on. Each has its own blast radius, rotation procedure, and
recovery timeline. Read the whole section before starting.

**BEFORE ROTATING ANY KEY, read:** `docs/security/emergency-access-policy.md`
sections on chain-of-custody invariants. A sloppy rotation can break
the chain retroactively.

---

## Secrets inventory

| Secret | Purpose | Location | Rotation frequency | Blast radius on leak |
|--------|---------|----------|-------------------|----------------------|
| `signing.key` | Ed25519 — signs fleet orders + evidence attestation bundles | `/app/secrets/signing.key` (mcp-server), fleet_cli client | Annual (or on leak) | Full: attacker can forge fleet orders + evidence bundles |
| `magic-link.key` | HMAC-SHA256 — signs single-use magic-link tokens | `/app/secrets/magic-link.key` (when separate key mode is enabled via `MAGIC_LINK_HMAC_KEY_FILE`) | Quarterly | Medium: attacker can forge approval tokens (but still needs client session to act) |
| `credential_encryption.key` | Fernet — encrypts credentials-at-rest in `site_credentials` | `/app/secrets/credential_encryption.key` (mcp-server) | Annual | Full: attacker can decrypt stored Windows/WinRM credentials |

Environment variables that point to these files:

```
SIGNING_KEY_FILE=/app/secrets/signing.key
MAGIC_LINK_HMAC_KEY_FILE=/app/secrets/magic-link.key   # optional; when unset, derives from signing.key
CREDENTIAL_ENCRYPTION_KEY=<fernet-key-or-path-to-file>
```

---

## `signing.key` rotation (Ed25519)

**Blast radius:** Forged fleet orders would pass signature verification
on appliances. Forged evidence bundles would pass auditor-kit
verification (but hash chain linkage breaks anything backdated, so the
damage is forward-only).

**Downstream systems that trust this key:**

- Every appliance daemon (keeps `server_pubkey` in its state)
- Every evidence bundle written to `compliance_bundles` (signed with
  this key — rotation does NOT invalidate existing bundles because
  `pubkeys.json` in the auditor kit records per-appliance + per-server
  key history with date ranges)

### Procedure

1. **Announce rotation window.** Notify partners + internal ops —
   fleet orders + new evidence bundles will pause for ~10 minutes.
2. **Generate new key.**
   ```bash
   openssl genpkey -algorithm Ed25519 -out /tmp/signing.key.new
   chmod 600 /tmp/signing.key.new
   ```
3. **Derive + record the new public key.**
   ```bash
   openssl pkey -in /tmp/signing.key.new -pubout -outform DER | \
     xxd -p | tr -d '\n' > /tmp/signing.pub.new.hex
   ```
4. **Update per-appliance trust.** For each appliance, queue a
   `rotate_server_pubkey` fleet order signed BY THE OLD KEY — so the
   appliance still trusts the sender. The order carries the new public
   key + an effective-from timestamp.
5. **Wait for ACKs.** Every appliance must ACK the rotation order
   before the old key can be retired. Use:
   ```bash
   docker exec mcp-server python3 -c "
   import asyncio, asyncpg, os
   async def main():
       c = await asyncpg.connect(os.environ['DATABASE_URL'])
       rows = await c.fetch('''
           SELECT appliance_id FROM site_appliances
           WHERE last_checkin > NOW() - INTERVAL '1 day'
             AND (server_pubkey IS NULL OR server_pubkey <> \$1)
       ''', NEW_PUBKEY_HEX)
       for r in rows: print(r['appliance_id'])
   asyncio.run(main())"
   ```
   Loop until empty. Any appliance that fails to rotate is a
   post-rotation liability — decommission or manually roll the key
   (WireGuard + SSH access required).
6. **Swap the key atomically.** In the mcp-server container:
   ```bash
   mv /app/secrets/signing.key /app/secrets/signing.key.OLD.$(date +%s)
   mv /tmp/signing.key.new    /app/secrets/signing.key
   chmod 600 /app/secrets/signing.key
   docker compose restart mcp-server
   ```
7. **Verify signing works.** Issue a non-privileged test fleet order
   (e.g. `run_drift` to a canary site). Confirm it signs + appliance
   ACKs.
8. **Verify evidence chain still grows.** Confirm the next check-in
   writes a new `compliance_bundles` row signed by the new key. The
   auditor kit will now report TWO entries in `pubkeys.json`.
9. **Retain the old key for 7 years.** HIPAA §164.316(b)(2)(i). Move
   `signing.key.OLD.$TIMESTAMP` to `secrets-archive/` with `chmod 400`.
   The public-verify endpoint must be able to verify old bundles
   against the old key indefinitely.

### Recovery / rollback

If the rotation fails partway (some appliances accepted, others did
not):

- If < 24h since rotation: restore the old key file in-place, restart
  mcp-server. Appliances that accepted the new key will reject the
  next order (signed by old key); queue a `rotate_server_pubkey`
  order signed by the **new** key pointing back to the **old**
  pubkey. All appliances that successfully rotated to new will accept
  this reversal. Appliances still on the old pubkey were never
  affected.
- If > 24h: roll forward. Manually visit non-rotated appliances via
  WireGuard + SSH to install the new pubkey.

---

## `magic-link.key` rotation (HMAC-SHA256)

**Blast radius:** Forged approval-action tokens embedded in emails.
Forgery alone is insufficient — the client still needs an authenticated
session as the target admin to actually consume the token. So a
standalone leak of this key is *low* severity if session auth is
intact. Rotate quarterly as hygiene, or immediately on email-system
compromise.

### Procedure

1. **Generate.**
   ```bash
   openssl rand -hex 32 > /tmp/magic-link.key.new
   chmod 600 /tmp/magic-link.key.new
   ```
2. **Swap on the server.**
   ```bash
   mv /app/secrets/magic-link.key /app/secrets/magic-link.key.OLD.$(date +%s)
   mv /tmp/magic-link.key.new    /app/secrets/magic-link.key
   ```
3. **Restart mcp-server.** Existing outstanding magic-link tokens
   are INVALIDATED at this moment — they HMAC-verify against the old
   key. Operators must re-trigger approval emails for any in-flight
   requests. This is acceptable because magic links TTL out after 30
   minutes anyway.
4. **Verify.** Trigger a privileged-access request in a canary org,
   confirm the email arrives with a new magic-link URL, confirm the
   `/portal/privileged-access/act` page consumes it successfully.

### First-time separate-key provisioning

If `MAGIC_LINK_HMAC_KEY_FILE` was previously UNSET (deriving from
signing.key), provisioning a separate key for the first time is a
rotation from signing.key-derivation → dedicated-file. Follow the
procedure above, then set:

```
MAGIC_LINK_HMAC_KEY_FILE=/app/secrets/magic-link.key
```

in the compose env. This is a one-way upgrade — reverting requires
a second rotation.

---

## `credential_encryption.key` rotation (Fernet)

**Blast radius:** Attacker can decrypt stored WinRM / SSH / API
credentials in `site_credentials`. Severity: HIGH — per-site admin
credentials in our DB would be exposed.

### Procedure

1. **Generate the new key.**
   ```bash
   python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
   ```
2. **Decrypt + re-encrypt every row.** Fernet does NOT support
   multi-key verify — the whole store must be re-encrypted in a
   single transaction. See `scripts/rotate_credential_encryption.py`
   (to be written — TODO, not yet delivered).
3. **Atomic swap.** Env `CREDENTIAL_ENCRYPTION_KEY=<new>` in the
   compose file, restart mcp-server.
4. **Destroy the old key material** only after verifying every row
   decrypts with the new key.

**Open gap:** the rotation script does not exist yet. Until it does,
Fernet key rotation requires a maintenance window + manual DB
transaction + application downtime. File an issue if you need this.

---

## Emergency rotation (suspected leak)

If you have a credible signal that one of these keys has leaked:

1. **DO NOT rotate silently.** File a SECURITY_ADVISORY (see
   `SECURITY_ADVISORY_2026-04-09_MERKLE.md` as a template).
2. **Assess scope.** Were signed artifacts produced during the leak
   window? If yes, the advisory must list them.
3. **Rotate following the procedure above** but skip the announced
   window — speed > coordination during an active compromise.
4. **Invalidate dependent trust.** For signing.key: every appliance
   needs an emergency `rotate_server_pubkey` order signed by the
   NEW key via manual delivery (WireGuard + SSH). The old pubkey is
   no longer trusted.
5. **Write the post-mortem** in `docs/postmortem-<date>-key-rotation.md`.

---

## Non-invariants (what this runbook does NOT cover)

- **Customer-facing OAuth client secrets** (Microsoft/Google SSO) —
  those are rotated per-customer via the partner portal.
- **Appliance-side keys** — each appliance has its own `agent.key`
  with a separate rotation procedure in `docs/RUNBOOKS.md`.
- **WireGuard keys** — see `docs/NETWORK.md`.
- **TLS certificates** — automated via certbot; out of scope.

---

## Test plan for a fresh operator

If this runbook works for someone who has never rotated a key before,
it's A-spec. Test by:

1. New engineer reads this doc end-to-end.
2. Walks through the `signing.key` procedure in a staging environment
   with a canary appliance.
3. Logs every step they had to guess at.
4. Files PRs to this doc for each gap found.

Update the "Last updated" header when you do.

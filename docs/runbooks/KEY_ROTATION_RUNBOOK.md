# Credential Encryption Key Rotation Runbook

**Scope:** Rotating the Fernet key that encrypts credentials at rest (site_credentials, org_credentials, client_org_sso, integrations, oauth_config, partners.oauth_*).

**Owner:** Platform Engineering / Security
**Compliance:** HIPAA §164.312(a)(2)(iv) — encryption and decryption; SOC 2 CC6.1 — logical access; PCI DSS 3.6 — cryptographic key management.

**Risk profile:** Getting this wrong permanently orphans every credential in the fleet. **No credential = no WinRM scans, no SSH, no OAuth, no portal logins.** Read this runbook end-to-end before you touch anything.

---

## 1. When to rotate

- **Scheduled:** Every 90 days (security baseline) or 180 days (HIPAA-only deployments).
- **Incident-driven:** Immediately if a key may have leaked — committed to git, extracted from a backup, or exposed in an image.
- **Personnel:** When an engineer with shell access to the VPS leaves the team.
- **Never rotate during:** Active incident response that depends on decrypting credentials (you'll make the incident worse).

---

## 2. Architecture recap

The platform uses `cryptography.fernet.MultiFernet` — a keyring, not a single key.

- **Encryption** always uses the FIRST key in the list (the "primary").
- **Decryption** tries every key in the list until one succeeds.
- To rotate: prepend a new key, re-encrypt all ciphertexts to the new key, then remove the old key from the list.

The keyring is loaded from `CREDENTIAL_ENCRYPTION_KEYS` (comma-separated, newest first). Falls back to the legacy single-key `CREDENTIAL_ENCRYPTION_KEY` if not set.

Fingerprint = first 12 hex chars of `sha256(key)`. Used in audit logs and the admin UI so you can prove which key was in effect without leaking the key itself.

---

## 3. Pre-flight checklist

- [ ] You have SSH access to the VPS (`root@178.156.162.116`)
- [ ] You have the **current** `CREDENTIAL_ENCRYPTION_KEY` in a secure location (1Password, KMS, etc.) — you'll need it to list as the secondary key
- [ ] You have an admin account on the dashboard
- [ ] No active rotation is in progress (`GET /api/admin/credentials/rotation-status`)
- [ ] No active maintenance window blocking writes (check #ops-center)
- [ ] You've communicated to the team that a rotation is starting — set a 15-minute calendar block
- [ ] You've backed up the current encrypted columns:
      ```bash
      ssh root@178.156.162.116 "docker exec mcp-postgres pg_dump -U mcp -d mcp \
        -t site_credentials -t org_credentials -t client_org_sso -t integrations \
        -t oauth_config -t partners" > /tmp/pre-rotation-backup-$(date +%Y%m%d).sql
      ```

---

## 4. Rotation procedure

### Step 1 — Generate a new key

From the VPS or any machine with Python + `cryptography`:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Output is a 44-character base64 string. Copy it to your secret store.

### Step 2 — Install the new key alongside the old one

SSH to VPS and edit the environment:

```bash
ssh root@178.156.162.116
cd /opt/mcp-server
nano .env
```

Find (or add) `CREDENTIAL_ENCRYPTION_KEYS`. The value should be **new key comma old key**:

```
CREDENTIAL_ENCRYPTION_KEYS=NEW_KEY_FROM_STEP_1,CURRENT_KEY_FROM_SECRETS
```

If the deployment is still using the legacy single-key env var, you can leave `CREDENTIAL_ENCRYPTION_KEY` set — `CREDENTIAL_ENCRYPTION_KEYS` takes precedence when both are present.

### Step 3 — Restart mcp-server

```bash
docker compose restart mcp-server
sleep 5
docker compose logs mcp-server --tail=20 | grep -i "Credential encryption initialised"
```

Expected log line:

```
Credential encryption initialised: 2 key(s), primary fingerprint=abc123def456
```

If you see only `1 key(s)`, the env var did not take effect. Go back to step 2.

### Step 4 — Verify both keys are loaded (sanity check)

From an admin session on the dashboard:

```bash
curl -b "session=..." https://api.osiriscare.net/api/admin/credentials/key-fingerprints
```

Expected response:

```json
{
  "primary": "NEW_FP",
  "keyring": ["NEW_FP", "OLD_FP"]
}
```

Both fingerprints should be present. If only one is shown, the rotation is NOT safe to proceed — stop and troubleshoot.

### Step 5 — Smoke test: confirm old ciphertexts still decrypt

Pick any site and load its credentials page. If the credentials load and you can read the hostnames/usernames (NOT the passwords — those stay encrypted until scan time), decryption is working. If you get a 500, the old key is missing or corrupt — **stop immediately and re-check step 2**.

### Step 6 — Kick off re-encryption

```bash
curl -X POST -b "session=..." \
  https://api.osiriscare.net/api/admin/credentials/rotate-key
```

Response:

```json
{
  "status": "started",
  "primary_key_fingerprint": "NEW_FP",
  "keyring_size": 2,
  "started_by": "jeff",
  "started_at": "2026-04-09T15:30:00Z"
}
```

The call is non-blocking. Progress is exposed via `/rotation-status`.

### Step 7 — Watch progress

```bash
watch -n 2 'curl -s -b "session=..." \
  https://api.osiriscare.net/api/admin/credentials/rotation-status | jq'
```

Expected final state:

```json
{
  "in_progress": false,
  "started_at": "...",
  "finished_at": "...",
  "started_by": "jeff",
  "primary_key_fingerprint": "NEW_FP",
  "keyring_fingerprints": ["NEW_FP", "OLD_FP"],
  "counters": {
    "site_credentials.encrypted_data": {"scanned": 5, "rotated": 5, "skipped": 0, "errors": 0},
    "org_credentials.encrypted_data": {"scanned": 0, "rotated": 0, "skipped": 0, "errors": 0},
    "client_org_sso.client_secret_encrypted": {"scanned": 1, "rotated": 1, "skipped": 0, "errors": 0},
    "integrations.credentials_encrypted": {"scanned": 3, "rotated": 3, "skipped": 0, "errors": 0},
    "oauth_config.client_secret_encrypted": {"scanned": 0, "rotated": 0, "skipped": 0, "errors": 0},
    "partners.oauth_access_token_encrypted": {"scanned": 2, "rotated": 2, "skipped": 0, "errors": 0},
    "partners.oauth_refresh_token_encrypted": {"scanned": 2, "rotated": 2, "skipped": 0, "errors": 0}
  },
  "error": null
}
```

**If `errors > 0` on any table:** STOP. Do NOT drop the old key. See §6 Troubleshooting.

### Step 8 — Drop the old key

Only after a clean run with `errors == 0` across every row:

```bash
ssh root@178.156.162.116
cd /opt/mcp-server
nano .env
```

Set:

```
CREDENTIAL_ENCRYPTION_KEYS=NEW_KEY_ONLY
```

Restart:

```bash
docker compose restart mcp-server
sleep 5
docker compose logs mcp-server --tail=20 | grep "Credential encryption initialised"
# should say "1 key(s), primary fingerprint=NEW_FP"
```

### Step 9 — Post-rotation verification

- [ ] Pick a site, load its credentials page — should still work
- [ ] Trigger a test WinRM scan from an appliance → should succeed
- [ ] Check `admin_audit_log` for the rotation events:
      ```sql
      SELECT created_at, username, action, details
      FROM admin_audit_log
      WHERE action LIKE 'CREDENTIAL_KEY_ROTATION%'
      ORDER BY created_at DESC LIMIT 5;
      ```
- [ ] Destroy the old key from your secret store (after a 7-day cooling-off period — in case you need to roll back)
- [ ] File the rotation in your compliance log: date, operator, reason, old/new fingerprints

---

## 5. Rollback (if the rotation is not yet complete)

If you've run steps 1-6 and something looks wrong before dropping the old key:

1. **The old key is still in the keyring.** Stop the rotation run by restarting mcp-server — this clears in-memory state.
2. **Undo step 2:** remove the new key from `CREDENTIAL_ENCRYPTION_KEYS`, leaving only the old key.
3. Restart mcp-server. All ciphertexts that were already rotated to the new key will now fail to decrypt, so you must ALSO re-run the rotation backward (move the old key to first position, re-trigger).

**You cannot roll back after step 8.** Once the old key is removed from the keyring, old ciphertexts are orphaned forever. This is why step 9 includes a 7-day cooling-off period on the old key.

---

## 6. Troubleshooting

### Error: `errors > 0` in a counter

Run this SQL to find the affected rows:

```sql
-- site_credentials example
SELECT id, site_id, credential_name, length(encrypted_data)
FROM site_credentials
WHERE encrypted_data IS NOT NULL
ORDER BY id;
```

Then check the backend logs for the specific row:

```bash
docker compose logs mcp-server 2>&1 | grep "rotate: site_credentials"
```

Common causes:
- **Corrupt ciphertext** — decrypt with the old key returned garbage. Restore from backup.
- **Row written between keyring load and rotation** — rare race. Re-run the rotation; it's idempotent.
- **Column length limit** — `encrypted_data` is `bytea` with no length limit, so this shouldn't happen. If it does, check for DB column alterations.

### Error: log says `no key in keyring matches ciphertext`

Your old key is missing or the wrong one. You likely did not copy the current key correctly in step 2. **Do NOT drop the "new" key** — you haven't rotated anything yet. Restore the old key in the env var, restart, verify decryption works, then retry from step 1.

### 409 Conflict from `/rotate-key`

Another rotation is already running. Wait for it to finish (poll `/rotation-status`), or if the process is stuck, restart mcp-server to clear the in-memory state.

---

## 7. Schedule

| Event | Cadence | Owner |
|-------|---------|-------|
| Routine rotation | Quarterly (every 90 days) | Platform Engineering |
| Compliance audit proof | Annual (before external audit) | Security Lead |
| Key destruction (old keys) | 7 days after successful rotation | Platform Engineering |
| Runbook review | Biannual | Security Lead |

---

## 8. Reference

- **Module:** `mcp-server/central-command/backend/credential_crypto.py`
- **Rotation endpoint:** `mcp-server/central-command/backend/credential_rotation.py`
- **Tests:** `mcp-server/central-command/backend/tests/test_credential_rotation.py` (24 tests)
- **Audit events:** `CREDENTIAL_KEY_ROTATION_STARTED`, `CREDENTIAL_KEY_ROTATION_COMPLETED` in `admin_audit_log`
- **Tables re-encrypted:** site_credentials, org_credentials, client_org_sso, integrations, oauth_config, partners (oauth_access_token_encrypted + oauth_refresh_token_encrypted)

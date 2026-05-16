# Vault Phase C — Rollback Runbook

**Task #49.** Operator-facing runbook for reverting a Phase C cutover
(`SIGNING_BACKEND=vault`) back to file-primary in case of post-cutover
incident. The same-pubkey-preserved import strategy from Phase C step 5
(see `docs/security/vault-transit-migration.md` 2026-05-16 rewrite)
makes rollback operationally trivial — no fleet-side action required.

**Severity classes:**

- **GREEN rollback** — Vault is healthy but operator wants to revert
  for non-emergency reasons (e.g., post-cutover load profile concerns).
  ETA: ~2 min downtime. No SECURITY_ADVISORY required.
- **YELLOW rollback** — Vault degraded (intermittent timeouts, slow
  responses) but not down. Signing is still working but eligible for
  rollback. ETA: ~5 min. SECURITY_ADVISORY OPTIONAL (operator call).
- **RED rollback** — Vault fully down or compromised. Cutover-mode
  signing has failed for ≥3 consecutive minutes. SECURITY_ADVISORY
  MANDATORY within 7 days per Counsel Rule 6 + §164.308(a)(6).

---

## Pre-flight (all severities)

```bash
# 1. Confirm /opt/mcp-server/secrets/signing.key still exists + is
#    readable + sha256 matches the pre-cutover backup in 1Password.
ssh root@178.156.162.116
ls -la /opt/mcp-server/secrets/signing.key
sha256sum /opt/mcp-server/secrets/signing.key
# Compare against the SHA stored in 1Password
# "OsirisCare > Vault Phase C cutover > signing.key.sha256"

# 2. Confirm the current signing backend
grep SIGNING_BACKEND /opt/mcp-server/.env

# 3. Confirm the Vault-current pubkey still matches the disk pubkey
#    (would be the same if step-5 import-preserve worked; if they
#    DIFFER, this is RED — appliances would reject the disk-signed
#    rotation evidence).
vault read transit/keys/osiriscare-signing | grep public_key | head -1
openssl pkey -in /opt/mcp-server/secrets/signing.key -pubout -outform DER \
    | base64 | head -1
# These two strings MUST match. If they don't, STOP — Phase C had a
# wrong-key import + the appliances are already pinned to the disk
# pubkey. Proceed only with the disk key.
```

---

## GREEN rollback (non-emergency)

```bash
# 1. Edit /opt/mcp-server/.env on VPS
ssh root@178.156.162.116
vi /opt/mcp-server/.env
# Change:
#   SIGNING_BACKEND=vault
# Back to:
#   SIGNING_BACKEND=file
# Leave SIGNING_BACKEND_PRIMARY=file unchanged.

# 2. Restart mcp-server
docker compose -f /opt/mcp-server/docker-compose.yml restart mcp-server

# 3. Verify
curl -fsS http://localhost:8000/api/version
# Confirm runtime_sha matches deployed commit (cutover commit or
# rollback commit)

curl -fsS http://localhost:8000/api/admin/health -H "Authorization: Bearer $ADMIN_BEARER" \
    | jq '.signing_backend'
# Should now show "file"

# 4. Spot-check a fresh fleet_order is signed with file backend
psql -U mcp -d mcp -c \
  "SELECT order_id, signing_method, created_at FROM fleet_orders ORDER BY created_at DESC LIMIT 5;"
# Most-recent row should show signing_method='file'

# 5. Notify ops channel: "Vault primary rolled back to file. Reason:
#    <one-line>. Next cutover attempt: <date or 'pending review'>."
```

---

## YELLOW rollback (Vault degraded)

Same as GREEN, but also:

```bash
# Capture Vault diagnostics BEFORE restart so the post-incident review
# has data
ssh root@10.100.0.3 'vault status' > /tmp/vault-rollback-status-$(date +%s).txt
ssh root@10.100.0.3 'journalctl -u vault --since "1 hour ago" --no-pager' \
    > /tmp/vault-rollback-journal-$(date +%s).txt

# Archive divergence + telemetry from the last hour
psql -U mcp -d mcp -c \
  "COPY (SELECT * FROM fleet_orders WHERE created_at > NOW() - INTERVAL '1 hour'
         AND signing_method = 'vault')
   TO STDOUT WITH CSV HEADER" > /tmp/vault-rollback-last-hour-$(date +%s).csv
```

Then proceed with GREEN rollback steps 1–5.

After rollback: file a follow-up task to investigate the Vault degradation
class before re-attempting cutover. Do NOT immediately re-cut.

---

## RED rollback (emergency)

```bash
# Speed matters — every minute of cutover-mode-down is a minute of
# stalled evidence writes. Cutover-mode signing stalls under Vault
# outage; the evidence queue backs up but does NOT drop (mig 175
# chain enforcement requires a signed bundle to land before the
# next can chain on top).

# 1. Single-shot rollback — same as GREEN steps 1+2, do them
#    BACK-TO-BACK without verification gates
ssh root@178.156.162.116 \
  'sed -i "s/^SIGNING_BACKEND=vault$/SIGNING_BACKEND=file/" /opt/mcp-server/.env && \
   docker compose -f /opt/mcp-server/docker-compose.yml restart mcp-server'

# 2. Verify in parallel
curl -fsS http://localhost:8000/api/version &
curl -fsS http://localhost:8000/api/admin/health -H "Authorization: Bearer $ADMIN_BEARER" \
    | jq '.signing_backend' &
wait

# 3. Spot-check queue drained
psql -U mcp -d mcp -c \
  "SELECT COUNT(*) AS pending FROM merkle_batch_pending WHERE created_at > NOW() - INTERVAL '15 min';"

# 4. Page on-call security AT ONCE (NOT after verification)
# Slack #ops + #security
# Email security@osiriscare.com
# Subject: "VAULT CUTOVER ROLLBACK — RED — <UTC timestamp>"
# Body: brief — "Vault primary failed at <timestamp>; rolled back to
#       file backend at <timestamp>. Investigation in progress.
#       SECURITY_ADVISORY will publish within 7 days."

# 5. Within 24h: file SECURITY_ADVISORY draft using the template at
#    docs/security/templates/security_advisory_vault_cutover.md
#    (Counsel Rule 6 + §164.308(a)(6) requires disclosure within
#    7 days for any cutover-class security event).
```

---

## Post-rollback follow-up (ALL severities)

1. **Update the cutover commit's git log note** — note in the commit
   body that a rollback occurred + the rollback runbook URL.
2. **Substrate invariants** — verify the
   `signing_backend_drifted_from_vault` invariant clears within 60s
   of the file-primary restart (the comparison flips back to
   PRIMARY=file matching observed=file).
3. **Auditor kit determinism** — verify two consecutive
   `/api/evidence/sites/{id}/auditor-kit` downloads produce
   byte-identical ZIPs for the site that was actively writing
   evidence during the cutover-mode window. Determinism contract
   does NOT depend on which backend signed the bundle, but
   sanity-check.
4. **Next-cutover gate** — do NOT re-cut without a fresh round of
   reverse-shadow soak (task #48 class). The reverse-shadow run
   establishes baseline divergence telemetry; cutting again without
   it is a "fix-forward without re-Gate-A" antipattern (see
   `memory/feedback_vault_phase_c_revert_2026_05_12.md`).

---

## Common failure shapes during rollback

- **Disk pubkey ≠ Vault pubkey:** pre-flight step 3 failed. STOP. The
  Phase C import-preserve was broken; appliances are pinned to the
  disk pubkey, so file rollback works BUT any evidence signed during
  Vault-cutover-mode used a different key + will fail appliance-side
  verification. File a P0 fix-forward task.
- **`secrets/signing.key` missing or corrupted:** pre-flight step 1
  failed. The Phase D retire (`docs/security/vault-transit-migration.md`
  §"Phase D: retire") removed `signing.key` — but Phase D is
  intentionally gated 30+ days AFTER successful Phase C, so this
  should never happen during a Phase C rollback. If it does, fall
  back to the 1Password-archived key + restore.
- **`SIGNING_BACKEND` env not set:** mcp-server defaults to `file`
  (per `signing_backend.py::current_signing_method`'s os.getenv with
  `'file'` default). This is the SAFE default — rollback effectively
  completes if the env var simply isn't set.

---

## Change log

- 2026-05-16 — initial — Task #49 + supersedes the rotate_server_pubkey
  ceremony per Task #47. Aligned with the same-pubkey-preserved import
  strategy added to `docs/security/vault-transit-migration.md` Phase C
  step 5.

# Alert Runbooks

**Last updated:** 2026-04-13 (Phase 15 enterprise hygiene)
**Owner:** On-call engineering
**Purpose:** when an alert fires, on-call should not have to ask "what do I do?"

Every entry follows the same shape:
- **Alert signature** (log message, metric, endpoint reading)
- **What it means**
- **Immediate actions** (first 10 minutes)
- **Triage paths** (if A then …)
- **Escalation** (when to wake a senior)
- **Post-incident** (runbook + blameless write-up requirements)

---

## `CHAIN_TAMPER_DETECTED`

**Alert signature:**
- Log line: `logger.error("CHAIN_TAMPER_DETECTED", site_id=..., broken_count=...)`
- Email: subject `[OsirisCare] CRITICAL: CHAIN_TAMPER_DETECTED on {site_id}`
- `admin_audit_log.action = 'CHAIN_TAMPER_DETECTED'`

### What it means

The hourly `chain_tamper_detector` loop walked the most-recent 100
compliance_bundles for the named site and found one or more where:
- `chain_hash` does not match `SHA256(bundle_hash:prev_hash:position)`, OR
- `prev_hash` does not match the previous bundle's `bundle_hash`, OR
- `chain_position` skipped a number (bundle was deleted).

Any of these means a compliance_bundles row was MUTATED after write.
The DELETE/UPDATE triggers (migrations 151, 161) are supposed to make
this impossible. If the alert fires, EITHER the triggers were dropped,
OR a superuser wrote directly bypassing the triggers (`ALTER TABLE
... DISABLE TRIGGER`), OR the trigger logic has a bypass.

This is one of the three credibility-critical alerts. Evidence is our
product. Broken evidence = broken product.

### Immediate actions (first 10 minutes)

1. **Acknowledge** in whatever paging system surfaced it. Don't snooze.
2. **Stop the bleed.** In one shell, put the DB in read-only for all
   RLS-enforced users:
   ```sql
   ALTER ROLE mcp_app SET default_transaction_read_only TO ON;
   ```
   This freezes new writes while you investigate. DOES NOT affect
   migrations (which run as `mcp` superuser).
3. **Preserve the evidence.** Take a `pg_dump` of `compliance_bundles`
   WHERE `site_id` = the affected site, `chain_position` >= lowest
   broken position - 10. Save to `/opt/mcp-server/incidents/$(date +%s)/`
   with `chmod 400`.
4. **Check the triggers are still installed:**
   ```sql
   SELECT t.tgname, c.relname
   FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
   WHERE c.relname = 'compliance_bundles' AND NOT t.tgisinternal;
   ```
   Expected: at least one BEFORE DELETE trigger. If missing, that's
   the root cause — migration 151 was not applied or was dropped.

### Triage paths

- **Triggers missing.** Run `docker exec mcp-server python3 /app/dashboard_api/migrate.py status`. If 151 shows pending, apply: `... migrate.py up`. Then re-run the chain-tamper detector manually: `docker exec mcp-server python3 -c "import asyncio; from dashboard_api.chain_tamper_detector import chain_tamper_detector_loop; asyncio.run(chain_tamper_detector_loop())"`.

- **Triggers installed but row still mutated.** Check for recent
  superuser writes:
  ```sql
  SELECT usename, query, backend_start, state
  FROM pg_stat_activity
  WHERE usename = 'mcp' AND query LIKE '%compliance_bundles%';
  ```
  Open `admin_audit_log` for the site's hour window. If there's a
  `MANUAL_DB_WRITE` or `TRIGGER_DISABLED` audit row, follow the thread.
  If no audit trail: assume DB compromise, escalate.

- **Broken position is the newest bundle.** Usually means a race at
  write time: evidence_chain.py wrote the bundle but another concurrent
  writer inserted between SELECT prev and INSERT. Rare, but if it
  happens, the chain_hash will be correct only for a SUBSET — the
  fix is to re-write the broken bundle with the correct prev_hash.
  DO NOT DELETE. See evidence_chain.py `relink_chain_after_hole()`.

### Escalation

- If more than 1% of recent bundles are broken → wake security lead.
- If ANY broken bundle has `check_type='privileged_access'` →
  this is a chain-of-custody breach. File a SECURITY_ADVISORY
  (template: `SECURITY_ADVISORY_2026-04-09_MERKLE.md`), notify
  affected partners + clients WITHIN 72 HOURS (HIPAA breach notice).

### Post-incident

- Write `docs/postmortem-<date>-chain-tamper-<site>.md`.
- If triggers were missing: update `startup_invariants.py` to cover
  whatever variant was missed (e.g., different trigger name).
- Add a regression test to
  `tests/test_privileged_chain_triggers_pg.py` that reproduces the
  exact failure mode.

---

## `STARTUP_INVARIANT_BROKEN`

**Alert signature:**
- Log line: `logger.error("STARTUP_INVARIANT_BROKEN", invariant=..., detail=...)`
- `admin_audit_log.action = 'STARTUP_INVARIANT_BROKEN'`
- mcp-server startup log banner: `STARTUP_INVARIANTS_DEGRADED count={n}`

### What it means

On startup, `startup_invariants.enforce_startup_invariants()` found a
DB-layer protection missing. The server is RUNNING but cannot
cryptographically guarantee that writes are being rejected per policy.

Common causes:
- Migration 151, 175, 176, or 178 has not been applied
- Someone ran `ALTER TABLE ... DISABLE TRIGGER` in a hotfix and forgot
- Signing key file missing (container volume mount misconfigured)

### Immediate actions

1. **Check which invariant broke:**
   ```bash
   docker exec mcp-postgres psql -U mcp -d mcp -c \
     "SELECT target_id, details FROM admin_audit_log \
      WHERE action='STARTUP_INVARIANT_BROKEN' \
      ORDER BY created_at DESC LIMIT 10"
   ```

2. **Matching fix path:**

   | Invariant | Fix |
   |-----------|-----|
   | INV-CHAIN-175 | `migrate.py up` — apply migration 175 |
   | INV-CHAIN-176 | `migrate.py up` — apply migration 176 |
   | INV-EVIDENCE-DELETE | `migrate.py up` — apply migration 151 |
   | INV-AUDIT-DELETE-* | `migrate.py up` — apply migration 151 |
   | INV-COMPLETED-LOCK | `migrate.py up` — apply migration 151 |
   | INV-SIGNING-KEY | Check volume mount + secrets file (see below) |
   | INV-MAGIC-LINK-TABLE | `migrate.py up` — apply migration 178 |

3. **For INV-SIGNING-KEY:**
   ```bash
   docker exec mcp-server ls -la /app/secrets/signing.key
   # If missing, compose volume may be misconfigured:
   docker inspect mcp-server | grep -A 5 Mounts
   # The key lives on the VPS at /opt/mcp-server/secrets/signing.key
   # If deleted there, this is a SECURITY INCIDENT — do NOT regenerate;
   # see key-rotation-runbook.md emergency procedure.
   ```

4. **Restart mcp-server** after fix and verify:
   ```bash
   curl http://localhost:8000/api/admin/health | jq .status
   # Expected: "ok"
   ```

### Escalation

- INV-SIGNING-KEY missing from disk AND no audit trail of the deletion
  → treat as compromise. Escalate to security lead immediately and
  initiate emergency key rotation.

---

## Loop heartbeat stale (`/api/admin/health/loops`)

**Alert signature:**
- `GET /api/admin/health/loops` returns an entry with `status: stale`
- Logs: absence of the usual loop iteration logs for > 3x the
  expected interval

### What it means

A background loop is registered + running (not crashed), but hasn't
completed an iteration recently. It's stuck — deadlocked on a DB
connection, blocked on a lock, or in an infinite retry of an
unrecoverable error.

### Triage by loop name

- **`privileged_notifier`** stale → privileged-access approval emails
  are not being sent. Urgent: active requests may timeout. Check
  SMTP connectivity + PgBouncer saturation. Restart mcp-server if
  needed.

- **`merkle_batch`** stale → OpenTimestamps anchoring is paused.
  Evidence is still being written, but not anchored to Bitcoin. Not
  urgent (< 24h). Check `ots_proofs` table for `status='batching'`
  rows with `batched_at IS NULL` older than 1 hour.

- **`chain_tamper_detector`** stale → tamper detection is NOT
  running. Not an immediate risk (the DELETE triggers still block),
  but we lose visibility into silent tampering. Restart
  mcp-server.

- **`audit_log_retention`** stale → no cleanup happening. Not urgent
  — HIPAA requires retention, not deletion.

- **`fleet_order_expiry`** stale → expired orders will accumulate
  and may be delivered to appliances. Check `fleet_orders` for
  `status='active' AND expires_at < NOW()` rows; if > 100, restart.

### Escalation

- All loops on one mcp-server replica stale simultaneously → replica-
  level problem, rotate pods/containers. Suspect: event loop blocked
  by a sync call.

---

## Failed Ed25519 signature verifications

**Alert signature:**
- `sig_verify_failed` count in Prometheus gauge `osiriscare_sig_verify_failures_total` incrementing
- Log: `logger.error("sig_verify_failed", ...)`

### What it means

An appliance sent a fleet-order completion ACK or a check-in message
with a signature that doesn't verify against the appliance's stored
public key. Causes:

1. **Legitimate key rotation:** the appliance's key was rotated but
   the server hasn't picked up the new pubkey yet. Usually resolves
   in 1-2 checkins.
2. **Stale daemon binary:** the appliance is running old code that
   signs with a different canonicalization format.
3. **Adversarial:** someone is forging traffic pretending to be an
   appliance.

### Triage

1. Check the `site_appliances.agent_public_key` matches what the
   appliance reports. If not, rotation is in flight — wait 10 min.
2. If persistently failing for ONE appliance, check its
   `agent_version` and compare to released versions. If way old,
   roll out a fleet order `update_daemon`.
3. If failures spike from MULTIPLE appliances simultaneously: check
   if the server signing key changed unexpectedly (someone touched
   `/app/secrets/signing.key`). Correlate with deploy logs.

### Escalation

- Failures from an appliance that has not been issued a key rotation
  order → escalate to security. Could be traffic injection.

---

## Failed signature verification on magic-link consume

**Alert signature:**
- Log: `MagicLinkError: HMAC mismatch (tampered token)` at INFO level
- Customer-facing: portal shows "This link cannot be used."

### What it means

Someone submitted a magic-link token whose HMAC doesn't match. Most
likely a copy-paste error where the URL was truncated. In rare cases,
token forgery attempt.

### Triage

- Single instance: likely user error. No action.
- High rate from the same IP: blocklist the IP at the edge.
- High rate across many IPs: someone is trying to brute-force. Wake
  security.

---

## `migration pending on startup`

**Alert signature:**
- Startup log: `FATAL: migration apply failed — refusing to start`
- `SystemExit(2)` → container restart loop

### What it means

Either the DB is unreachable, or `cmd_up()` returned non-zero — a
migration file failed to apply. See
`docs/RUNBOOKS.md` → "Migration-Induced Restart Loop" for the
recovery recipe (PgBouncer orphaned prepared statements was the
cause of the Session 205 outage).

---

## Writing a new runbook

When you add a new alert to the system, add a runbook entry here IN
THE SAME COMMIT. A new alert without a runbook is a debt that
on-call pays at 3am.

Template:

```
## `<alert_signature>`

**Alert signature:** <log / metric / endpoint>

### What it means
<1 paragraph>

### Immediate actions (first 10 minutes)
1.
2.

### Triage paths
- If A → …
- If B → …

### Escalation
<who + when>

### Post-incident
<required follow-up>
```

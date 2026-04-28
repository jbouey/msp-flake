# sigauth_enforce_mode_rejections

**Severity:** sev2
**Display name:** Enforce-mode appliance had a sigauth rejection

## What this means (plain English)

An appliance with `signature_enforcement='enforce'` produced at least one
invalid sigauth observation in the last 6 hours. Enforce mode is a
0%-fail contract — the operator (or the auto-promotion worker) flipped
this appliance to enforce specifically because its prior 6h window had
zero failures. Any rejection now means key state has drifted between
daemon and server, and the next escalation is the daemon's retry loop
giving up + customer-visible checkin gap.

This invariant exists because the umbrella signal
`signature_verification_failures` is structurally blind to low-rate
jitter: it requires ≥5 fails AND ≥5% rate in a 1h window. The 4
unknown_pubkey rejections that surfaced on north-valley-branch-2 in
24h post-Session-211 enforce flip (0.09% rate) cleared both bars and
the substrate stayed silent. This invariant catches that class.

## Root cause categories

- **Daemon dual-fingerprint-path divergence.** Daemon writes identity
  fingerprint to BOTH `/var/lib/msp/agent.fingerprint` (raw 16-hex)
  and `/etc/osiriscare-identity.json` (`.fingerprint` field). On a
  daemon restart or watchdog kick, if one path is out-of-date relative
  to the other, the in-memory key briefly differs from the persisted
  state the server has on file. (Session 211 step-1 round-table
  flagged the path ambiguity; root-cause investigation tracked under
  task #168.)
- **STEP 3.6c persistence race.** Server-side `_resolve_pubkey` reads
  from a different connection (`admin_connection`) than the writer
  (`tenant_connection` in STEP 3.6c). A delayed commit visibility
  causes the next verify to return None even though the row eventually
  populates.
- **Daemon restart between key generation and pubkey upload.** If the
  daemon process restarts mid-rotation, it can sign a checkin with a
  newly-generated key BEFORE the server has been told about it via
  the previous checkin's STEP 3.6c.
- **Manual operator promotion that beat sustained-clean evidence.**
  `/api/admin/sigauth/promote/{appliance_id}` permits operator
  override of the auto-promotion threshold. If the operator promoted
  before the daemon's identity key was confirmed stable (e.g. during
  a recent reprovision), the very first jitter event fires this
  invariant.

## Immediate action

- **Pull the rejection details from the violation panel.** The details
  blob carries `appliance_id`, `mac_address`, `failures`,
  `total_samples`, `fail_rate_pct`, and `last_failure_at`.
- **Compare daemon-side fingerprint against server-side.**
  Daemon (via SSH or recovery shell):
  ```bash
  cat /var/lib/msp/agent.fingerprint
  jq -r .fingerprint /etc/osiriscare-identity.json
  ```
  These two MUST be byte-identical. Mismatch on the appliance itself
  is the smoking gun for the dual-path bug — daemon restart will
  pick whichever was written last.

  Server-side:
  ```sql
  SELECT mac_address, agent_identity_public_key,
         encode(digest(agent_identity_public_key, 'sha256'), 'hex') AS pk_sha
    FROM site_appliances
   WHERE deleted_at IS NULL
     AND mac_address = '<MAC>';
  ```
  The first 16 hex chars of `pk_sha` should match the daemon's
  fingerprint files.
- **Check the forensic ERROR log for the rejection moment.**
  ```
  ssh root@178.156.162.116 'docker logs --since=24h mcp-server 2>&1 \
    | grep sigauth_unknown_pubkey | tail -20'
  ```
  Each line carries the timestamp + nonce + sig_len so you can
  reconstruct what the daemon claimed to be at the moment server
  refused.
- **Quick rollback while investigating** — `POST /api/admin/sigauth/demote/{appliance_id}`
  with `{"reason":"investigating <details>"}`. Demotion is instant
  (no fleet order needed) — flips back to observe so the daemon's
  retries succeed without being gated on signature validity.

## Verification

- This invariant clears on the next 60s tick once the appliance has
  zero invalid observations in a rolling 6h window — i.e. you need
  6h of clean signal before substrate clears the violation.
- Confirm with:
  ```sql
  SELECT COUNT(*) FILTER (WHERE NOT valid) AS fails,
         COUNT(*) AS total
    FROM sigauth_observations
   WHERE site_id = '<site>'
     AND UPPER(mac_address) = UPPER('<MAC>')
     AND observed_at > NOW() - INTERVAL '6 hours';
  ```
  Wait for `fails = 0` AND `total >= 60` (~1 obs/min × 60min).

## Escalation

When NOT to auto-fix:
- If multiple appliances across DIFFERENT sites trip this in the same
  hour, suspect a SERVER-SIDE deploy regression (canonical-input
  drift, replica lag, prepared-statement cache reset). Roll back the
  most recent mcp-server change before touching daemons.
- If a single appliance trips this >3 times in 24h after demote, the
  daemon's identity-key state on disk is wrong. Use the recovery
  script — `scripts/recover_legacy_appliance.sh <site> <mac> <ip>` —
  to mint a fresh key + push it via SSH.

## Related runbooks

- `signature_verification_failures` — umbrella signal at the 5%
  threshold. Use that for high-rate sustained failures.
- `sigauth_crypto_failures` — adversarial-reason subset (invalid_signature,
  bad_signature_format, nonce_replay). Distinct security signal.
- `every_online_appliance_has_active_api_key` — pre-checkin auth
  failures and a stale api_key can mask a sigauth event.

## Change log

- 2026-04-28 — created — Session 211 Phase 2 QA round-table consensus.
  4 sigauth rejections in 24h on north-valley-branch-2 (0.09% rate)
  fell below the umbrella threshold; this sibling invariant ratchets
  enforce-mode appliances to a 0%-fail contract so any future jitter
  pages immediately. Phase-3 entry gate: no new appliances flipped to
  enforce until this invariant runs 7 days clean.

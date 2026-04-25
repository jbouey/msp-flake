# signature_verification_failures

**Severity:** sev1
**Display name:** Agent signature verification failing

## What this means (plain English)

Umbrella signal: ANY sigauth fail counts toward this invariant — crypto,
operational, and enrollment. Fires when ≥5% of the last hour's
sigauth observations at this site failed verification (with a 5-sample
floor so a fresh site doesn't false-flag on its first checkin). Pair
with the priority signal `sigauth_crypto_failures`: when BOTH are
open, treat as a real security event; when ONLY this one is open, the
fails are enrollment debt or clock skew, not a crypto compromise.

## Root cause categories

- **Appliance not yet enrolled.** No `provisioning_claim_events` row
  for the MAC + site → `signature_auth.py::_resolve_pubkey` falls
  through to a key that doesn't match the daemon's signing key →
  100% `unknown_pubkey` or `invalid_signature`. (See task #179 for
  the design-gap context: the legacy fallback is unsound.)
- **Daemon clock skew > 5min** vs the server. Reason will be
  `clock_skew` or `bad_timestamp`.
- **Daemon not signing at all** (older binary or crashed signer).
  Reason will be `no_headers`. The sigauth pipeline runs in observe
  mode, so this doesn't block bearer-auth — but the umbrella alerts.
- **Real crypto failure** (forwards to `sigauth_crypto_failures`).
  Reason will be one of `invalid_signature`, `bad_signature_format`,
  `nonce_replay`. Treat the priority invariant as authoritative for
  those. (Body-hash mismatches surface as `invalid_signature` because
  the body hash is folded into the canonical signed input — there's
  no separate `bad_body_hash` reason emitted.)

## Immediate action

- **Classify by reason first.** From the violation detail, look at
  `details.reasons`. If the only reasons are `unknown_pubkey` /
  `clock_skew` / `bad_timestamp` / `no_headers`, this is enrollment
  or clock debt — not security. If `invalid_signature` etc. appear,
  jump to the `sigauth_crypto_failures` runbook.
- **For enrollment debt:**
  ```
  ssh root@178.156.162.116 "docker exec mcp-postgres psql -U mcp -d mcp -c \"
    SELECT mac_address, status, agent_version,
           length(agent_public_key) AS pubkey_len
      FROM site_appliances
     WHERE site_id = '<site>' AND deleted_at IS NULL
     ORDER BY mac_address\""
  ```
  Any MAC with `pubkey_len = 0` (NULL) needs the new sigauth-write
  code path to populate it (Session 210-B fix #148). If the column
  is populated but `provisioning_claim_events` has no row for that
  MAC, see task #179.
- **For clock skew:** check NTP on the appliance via the watchdog
  diagnostic order, or operator SSH if available.

## Verification

- Panel: this invariant clears on the next 60s tick once the fail
  rate drops below 5% in the rolling 1h window.
- CLI:
  ```
  SELECT site_id, COUNT(*) AS total,
         COUNT(*) FILTER (WHERE valid = false) AS fails,
         array_agg(DISTINCT reason) FILTER (WHERE valid = false) AS reasons
    FROM sigauth_observations
   WHERE site_id = '<site>' AND observed_at > NOW() - INTERVAL '1 hour'
   GROUP BY site_id;
  ```

## Escalation

- If `sigauth_crypto_failures` is also firing for the same site, escalate
  per that runbook — security-incident posture.
- If 100% fail rate persists for >24h on a single site WITHOUT crypto
  reasons, document the enrollment gap (task #179 tracks the design
  fix) and consider muting at the alerting layer until enrollment is
  closed. Don't ignore the substrate signal silently — file the mute
  with an expiry date.

## Related runbooks

- `sigauth_crypto_failures` — the priority signal. Crypto-level fails
  only. When both fire, that runbook leads.
- `every_online_appliance_has_active_api_key` — pre-checkin auth
  failures correlate with sigauth fails because they often share a
  root cause (stale config, missed rekey).
- `claim_event_unchained` — when enrollment IS happening but the chain
  is broken, this fires alongside.

## Change log

- 2026-04-25 — restructured — was a stub. Added concrete classification
  flow (enrollment-debt vs crypto-fail) and split priority signal
  into `sigauth_crypto_failures`.
- 2026-04-21 — generated — stub created.

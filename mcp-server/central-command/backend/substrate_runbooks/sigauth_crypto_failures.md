# sigauth_crypto_failures

**Severity:** sev1
**Display name:** Agent signature CRYPTO-fails (priority signal)

## What this means (plain English)

The substrate observed a sustained rate of cryptographic-level signature
failures from a daemon at this site — meaning the signature itself
didn't verify against the public key the server has on file. This is
distinct from the broader `signature_verification_failures` invariant,
which catches all sigauth issues (including enrollment-state and clock
skew). When THIS invariant fires, the failure mode is in the crypto
layer: wrong key, malformed signature, replay, or daemon-vs-server
canonical-input drift (body-hash mismatches fold into
`invalid_signature` because the hash is part of the canonical signed
input). Treat as a possible security event until ruled out.

## Root cause categories

- **Daemon and server have different keys for the same MAC.** Most
  common after an unreviewed reprovision, recovery, or migration that
  rewrote one side without re-binding the other.
- **Canonical-input drift between Go daemon (`phonehome.go::signRequest`)
  and Python server (`signature_auth.py::_canonical_input`).** Adding
  a new header, changing the separator, or swapping body-hash encoding
  on one side without the other breaks every signature.
- **Body-tampering on the wire.** A reverse proxy that rewrites JSON
  whitespace or content-encoding will change the body hash and look
  exactly like a forged request from the verifier's perspective.
- **Stolen or replayed signing key.** Rare but real: a daemon that was
  imaged + redeployed elsewhere will produce valid-looking signatures
  for the wrong site_id/mac pair. Watch for new fingerprints appearing
  on a MAC that didn't go through the rekey flow.

## Immediate action

- **Identify the offending MAC(s).** From the panel: open the violation
  detail, look at `details.reasons` and `details.fail_rate_pct`. Then
  query `sigauth_observations` for the MAC-level breakdown:
  ```
  ssh root@178.156.162.116 "docker exec mcp-postgres psql -U mcp -d mcp -c \"
    SELECT mac_address, reason, fingerprint, COUNT(*)
      FROM sigauth_observations
     WHERE site_id = '<site>' AND observed_at > NOW() - INTERVAL '1 hour'
       AND valid = false
       AND reason IN ('invalid_signature','bad_signature_format','nonce_replay')
     GROUP BY 1, 2, 3 ORDER BY 4 DESC\""
  ```
- **Verify daemon fingerprint matches server.** SSH (or recovery shell)
  to the appliance and read EITHER:
  - `/var/lib/msp/agent.fingerprint` (raw 16-hex-char file), OR
  - `/etc/osiriscare-identity.json` (operator-readable manifest with
    the same fingerprint under `.fingerprint`).
  Both are written by the daemon at first-boot identity creation and
  stay in sync. Compare to `SHA256(site_appliances.agent_public_key)[0:16]`
  for the same MAC. Mismatch = wrong key on file → rekey via fleet_cli.
  (See task #179: the server-side fallback is currently unsound —
  this comparison may show a "match" against the evidence key rather
  than the identity key. The fix tracks under that ticket.)
- **If keys match but signatures still fail**, the canonical input is
  drifting. Capture a failed request body + headers in the daemon log
  (it logs the canonical bytes it signed) and replay verification
  manually. The bug is almost always in `phonehome.go::signRequest` vs
  `signature_auth.py::_canonical_input` line-by-line.

## Verification

- Panel: this invariant clears on the next 60s tick once the crypto-fail
  rate drops below 5% of all sigauth observations in the rolling 1h window.
- CLI:
  ```
  SELECT COUNT(*) FILTER (WHERE valid = false
                           AND reason IN ('invalid_signature','bad_signature_format',
                                          'nonce_replay')) * 100.0
       / COUNT(*) AS crypto_pct
    FROM sigauth_observations
   WHERE site_id = '<site>' AND observed_at > NOW() - INTERVAL '1 hour';
  ```
  Should be 0% or near zero after the fix.

## Escalation

When NOT to auto-fix:
- If a NEW fingerprint appears on a MAC that did NOT go through the
  rekey flow (no `Agent signing key registered` log line, no
  `provisioning_claim_events` row in the last hour), suspect imaging /
  cloning. Do NOT rotate; preserve the evidence chain and treat as a
  security incident — see `docs/security/key-rotation-runbook.md` and
  the customer escalation procedure.
- If the rate is sustained at 100% across MULTIPLE sites simultaneously,
  the canonical-input drift is on the SERVER side (a recent deploy
  changed `_canonical_input`). Roll back the server, do not rekey
  daemons.

## Related runbooks

- `signature_verification_failures` — the umbrella signal. When BOTH
  fire = real crypto event; when only the umbrella fires = enrollment
  debt.
- `every_online_appliance_has_active_api_key` — pre-checkin auth
  failures and a stale api_key can mask a sigauth event.
- `claim_event_unchained` — fingerprint mismatches sometimes correlate
  with a broken claim chain on the same MAC.

## Change log

- 2026-04-25 — created — split out from `signature_verification_failures`
  to give crypto-level fails their own priority signal. Discovered
  during the 2-day enterprise-stability push: the umbrella conflated
  enrollment debt (100% noise) with real crypto signal (true positive),
  so operators learned to ignore both.

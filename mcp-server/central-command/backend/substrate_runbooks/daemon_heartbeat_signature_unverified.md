# daemon_heartbeat_signature_unverified

**Severity:** sev1
**Display name:** Daemon signature stored NULL — verifier crashed silently

## What this means (plain English)

An appliance with a registered evidence-bundle public key has emitted at least 3 heartbeats in the last 15 minutes where the appliance signed the payload (`agent_signature IS NOT NULL`) BUT the backend verifier stored `signature_valid = NULL` instead of TRUE or FALSE. This is the detection-gap class that allowed D1 signature verification to be silently inert in production for ~13 days before commit `adb7671a` on 2026-05-13.

Existing sibling invariants do NOT catch this state:
- `daemon_heartbeat_unsigned` queries `agent_signature IS NULL` — fires only when the daemon-side didn't sign at all.
- `daemon_heartbeat_signature_invalid` filters `signature_valid IS NOT NULL` — fires only when the backend verified and got FALSE.
- This invariant catches the third case: backend tried to verify, hit an exception, stored NULL (verifier-crashed-silently).

Counsel Rule 4 (no orphan coverage) PRIMARY — this invariant closes the orphan-coverage gap in the heartbeat-signature verification chain. Counsel Rule 3 (privileged-chain attestation) SECONDARY — signature verification integrity is a chain-of-custody concern.

## Root cause categories

- **Verifier import path broken** — e.g., `from signature_auth import` fails (ModuleNotFoundError) in the production package context, soft-fail handler catches it, stores NULL. Pre-fix prod outage class — `adb7671a` fixed this with relative-then-absolute fallback.
- **Public key not parseable** — `the registered evidence-bundle public key (see v_current_appliance_identity for the canonical identity-key path)` exists but is malformed (corrupted, wrong format, or stale base64). The verifier's `Ed25519PublicKey.from_public_bytes` raises, soft-fail stores NULL.
- **Canonical payload format drift** — the daemon's signed payload doesn't match the backend's reconstruction (`site_id|MAC|ts|version`). Verifier returns False... actually no — that's `signature_invalid`. THIS class is when the reconstruction code itself crashes.
- **PgBouncer prepared-statement churn** — rare; signature_auth.py's SELECT for the pubkey fails on connection churn. The except wrapper catches, stores NULL.

## Immediate action

This is an operator-facing alert. **DO NOT surface to clinic-facing channels** — substrate-internal verifier state is not customer-relevant per Session 218 task #42 opaque-mode parity rule.

1. **Check mcp-server logs for verifier exceptions**:
   ```
   docker logs mcp-server 2>&1 | grep -iE "verify_heartbeat_signature|signature_auth|ModuleNotFoundError" | tail -50
   ```
2. **Verify the registered pubkey for the affected appliance**:
   ```sql
   SELECT id, hostname, agent_public_key, previous_agent_public_key,
          previous_agent_public_key_retired_at
     FROM site_appliances
    WHERE id = '<details.appliance_id>';
   ```
   - `agent_public_key` should be non-NULL and parseable as 32-byte Ed25519 (base64-encoded ~44 chars OR hex 64 chars).
3. **Check recent heartbeat samples** for the appliance:
   ```sql
   SELECT observed_at, agent_signature, signature_valid,
          signature_canonical_format
     FROM appliance_heartbeats
    WHERE appliance_id = '<details.appliance_id>'
      AND observed_at > NOW() - INTERVAL '15 minutes'
    ORDER BY observed_at DESC LIMIT 10;
   ```
   `signature_canonical_format` tells you whether path A (daemon-supplied timestamp) or path B (backend reconstruction) was used.

## Verification

- Panel: invariant row clears on next 60s tick after a successful verification (signature_valid = TRUE/FALSE).
- CLI:
  ```sql
  SELECT COUNT(*) FROM appliance_heartbeats
   WHERE appliance_id = '<details.appliance_id>'
     AND agent_signature IS NOT NULL
     AND signature_valid IS NULL
     AND observed_at > NOW() - INTERVAL '15 minutes';
  ```
  Should drop below 3.

## Escalation

If the unverified state persists >1 hour AND the immediate-action grep doesn't surface a clear exception, escalate to engineering. This is chain-of-custody integrity territory — the platform's claim that every heartbeat is cryptographically attested depends on this verification firing successfully. Counsel Rule 3 (no privileged action without attested chain of custody) is the load-bearing reason for sev1 framing.

Engineering should: (1) inspect `sites.py:appliance_checkin` around line 4231 for new soft-fail paths; (2) audit `signature_auth.py:verify_heartbeat_signature` for code drift; (3) check `the registered evidence-bundle public key (see v_current_appliance_identity for the canonical identity-key path)` format normalization.

## False-positive guard

- Pre-D1 daemons + dev appliances without registered keys are excluded via JOIN to `site_appliances WHERE agent_public_key IS NOT NULL`.
- Soft-deleted appliances are excluded via `sa.deleted_at IS NULL`.
- The 15-minute window matches sibling `daemon_heartbeat_signature_invalid` sev1 cadence (parity).

## Related runbooks

- `daemon_heartbeat_unsigned.md` — sev2; fires when daemon-side did not sign (`agent_signature IS NULL`).
- `daemon_heartbeat_signature_invalid.md` — sev1; fires when signature is present but verification returned FALSE.
- This invariant fills the third quadrant: signature present, verification crashed, NULL stored.

## Change log

- 2026-05-13 — initial — Closes the detection-gap class that masked D1 inert state for ~13 days pre-fix `adb7671a` (Task #69, retro Gate B FU-1 P0). Counsel Rule 4 PRIMARY + Rule 3 SECONDARY.

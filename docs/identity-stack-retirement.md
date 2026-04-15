# Identity Stack — api_key Retirement Runbook

Composed identity stack: Weeks 1–6. This document is the **operational
playbook for retiring the legacy api_keys bearer auth**, the final
gate of the Week-6 cutover.

DO NOT skip the verification gates. The whole point of the stack is
that we don't lock daemons out of the platform — we earn enforcement
through observed evidence first.

## Pre-flight checklist

Run all four every day until they're all GREEN. When all four are
GREEN for 7 consecutive days, the cutover is safe.

### Gate 1 — Daemon adoption

Every online appliance is running daemon ≥ 0.4.4 (the version that
generates a keypair + signs checkins).

```sql
SELECT mac_address, hostname, agent_version
  FROM site_appliances
 WHERE deleted_at IS NULL
   AND status = 'online'
   AND (agent_version IS NULL OR agent_version < '0.4.4')
 ORDER BY agent_version;
```

GREEN: zero rows.

### Gate 2 — Signature observations

Every online appliance has produced a sigauth observation in the
last 24h.

Surfaced as substrate invariant `legacy_bearer_only_checkin`. GREEN
when the invariant has zero open violations.

```sql
SELECT * FROM v_substrate_violations_active
 WHERE invariant_name = 'legacy_bearer_only_checkin';
```

### Gate 3 — Sustained zero-fail rate

Every online appliance has zero `valid=false` observations in the
last 6 hours. (Looser than the auto-promotion threshold of zero
fails in 6h; same query.)

```sql
SELECT site_id, mac_address, COUNT(*) AS fails
  FROM sigauth_observations
 WHERE observed_at > NOW() - INTERVAL '6 hours'
   AND valid = false
 GROUP BY site_id, mac_address;
```

GREEN: zero rows.

### Gate 4 — Per-appliance enforcement

Every online appliance is in `signature_enforcement = 'enforce'`.

```sql
SELECT signature_enforcement, COUNT(*)
  FROM site_appliances
 WHERE deleted_at IS NULL AND status = 'online'
 GROUP BY signature_enforcement;
```

GREEN: only `enforce` rows. Auto-promotion will get them there if
Gate 3 has been GREEN for 6 hours per appliance. Manual promotion
via `POST /api/admin/sigauth/promote/{appliance_id}` is allowed
only when Gates 1–3 are GREEN for that appliance.

## Cutover steps (only after 7 consecutive days of all gates GREEN)

### Step 1 — Bearer-optional flag (reversible)

Set the env var on `mcp-server`:

    docker compose -f /opt/mcp-server/docker-compose.yml exec mcp-server \
        env BEARER_AUTH_OPTIONAL=true

(Today this env var doesn't exist yet — it lands in the Week 6b
backend change that makes `require_appliance_bearer` accept
"signature-only" auth when both headers are valid AND no bearer was
sent.)

Watch the substrate dashboard for 24 hours. ANY new
`signature_verification_failures` opens → revert immediately.

### Step 2 — Stop minting api_keys on provisioning (reversible)

Code change in `provisioning.py` and `iso_ca.py`:

    raw_api_key = ""
    api_key_hash = ""
    # ... skip api_keys INSERT entirely

Or guard with env var `PROVISION_MINT_BEARER_KEY=false`.

Watch the next provisioning event — the daemon should auth via
signature only and not need the returned `api_key`. Test claim-v2
returns an empty string for `api_key`.

### Step 3 — Migration 213 (irreversible) — disable existing keys

After step 2 has been live for 30 days with no incidents:

    BEGIN;
    UPDATE api_keys SET active = false WHERE active = true;
    -- Migration 209 trigger writes the audit row automatically.
    COMMIT;

Watch substrate for 48 hours. If clean, drop the table:

    BEGIN;
    -- Final retirement
    DROP TABLE api_keys CASCADE;
    -- Drop the bearer auth path from shared.py
    -- Drop the api_keys lookup from sites.py
    COMMIT;

### Step 4 — Audit kit v2 becomes the ONLY kit

The legacy v1 README emits a note that bearer auth has been retired
as of <date>. The customer-facing language updates to:

> Authentication: device-bound Ed25519 signatures only. No shared
> secrets exist between the platform and your appliances. Compromise
> of OsirisCare's infrastructure cannot be used to impersonate your
> appliances.

## Rollback

At any point through step 2:

    # Re-enable bearer auth as the primary path
    docker compose ... env BEARER_AUTH_OPTIONAL=false
    # Demote any appliances that are auth-failing
    POST /api/admin/sigauth/demote/{appliance_id}

After step 3 is irreversible. Do not proceed to step 3 until you
have 30 days of zero-incident evidence.

## What this retirement gives the customer

- No shared secret between platform and appliances exists in the
  database after step 3.
- Compromise of OsirisCare infrastructure cannot impersonate any
  appliance: the device-bound private keys never leave the boxes.
- The auditor kit v2 ZIP carries the ENTIRE chain of trust
  (claim → consent → execution → evidence) and is independently
  verifiable using public Sigstore + OpenTimestamps logs.

This is the legal contractor's "non-repudiable customer-authorized
device-executed action chain" milestone.

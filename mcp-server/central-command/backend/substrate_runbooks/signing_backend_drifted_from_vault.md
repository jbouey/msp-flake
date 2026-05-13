# Signing Backend Drifted From Vault — Runbook

**Invariant:** `signing_backend_drifted_from_vault` (sev2)
**Vault Phase C P0 #4** — `audit/coach-vault-phase-c-gate-a-2026-05-12.md`

## What this means

The container's expected signing backend (derived from the
`SIGNING_BACKEND` env var via `signing_backend.current_signing_method()`)
disagrees with the observed `fleet_orders.signing_method` distribution
in the last hour. Mismatch = chain-of-custody drift on the production
signing path.

The invariant compares:
- **Expected**: env-derived (`SIGNING_BACKEND` or, if `shadow`,
  `SIGNING_BACKEND_PRIMARY`).
- **Observed**: distinct `signing_method` values across all rows in
  `fleet_orders WHERE created_at > NOW() - INTERVAL '1 hour'`.

If any observed value differs from expected, fires.

## Root cause categories

1. **Silent fallback to disk-key signing.** Vault unreachable + the
   code didn't fail closed. Most concerning case — the very condition
   Phase C was meant to prevent. Symptoms: env says `vault`, observed
   contains `file`.
2. **Container env desynced from running state.** A partial restart
   or `docker compose up -d` without env re-source picked up stale
   values. Symptoms: env-as-claimed differs from env-as-running.
3. **INSERT call-site path missed `current_signing_method()`.** A
   new fleet_orders INSERT was added without the column write. P0 #3
   regression — would default to the column DEFAULT (`'file'`).
4. **Pre-cutover bootstrap.** During shadow mode, env may declare
   `shadow` while the PRIMARY is `file` — the invariant carves this
   out (compares against `SIGNING_BACKEND_PRIMARY` when `SIGNING_BACKEND=shadow`).

## Immediate action

1. Open `/admin/substrate-health` — confirm the firing details JSON
   shows `expected_signing_method` + `observed_methods` breakdown.
2. SSH the VPS and verify env:
   ```
   docker exec mcp-server printenv | grep SIGNING_BACKEND
   ```
3. If env declares `vault`, verify Vault reachability:
   ```
   curl -sk https://10.100.0.3:8200/v1/sys/health
   ```
   If unsealed (`sealed:false`), Vault is healthy → fall-through is
   a code bug. If sealed, unseal Vault first (1Password shares).
4. If env declares `file` but observed contains `vault`:
   grep recent commits for INSERT INTO fleet_orders to find the path
   that wrote 'vault' when it shouldn't have.

## Verification

After remediation:
- Trigger a benign fleet_order (e.g. via fleet_cli reading current
  signing path).
- `SELECT signing_method, COUNT(*) FROM fleet_orders WHERE created_at
   > NOW() - INTERVAL '5 minutes' GROUP BY 1;` matches expected.
- Substrate invariant auto-resolves on next 60s tick.

## Escalation

Sev2 → operator + on-call. If duration > 4h:
- Page senior eng for chain-of-custody analysis. Disk-key signatures
  for orders that should have been Vault-signed may need a forensic
  notation in the auditor kit (similar to mig 308 disclosure pattern).
- Consider rolling back to shadow mode (`SIGNING_BACKEND=shadow`)
  until root cause confirmed.

## Related runbooks

- `INV-SIGNING-BACKEND-VAULT` (startup_invariants.py) — sibling
  startup-time check for key-version pinning.
- `bg_loop_silent` — if Vault host's WireGuard heartbeat is dead,
  the signing-backend drift is downstream of network availability.

## Change log

- 2026-05-12: Created during Vault Phase C P0 batch
  (audit/coach-vault-phase-c-gate-a-2026-05-12.md). Pre-cutover
  posture: env=`shadow` PRIMARY=`file` — invariant idle as long as
  every fleet_order signs via `file`.

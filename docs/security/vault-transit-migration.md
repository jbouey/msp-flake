# Vault Transit Signing — Migration Plan (Phase B onward)

**Status:** Scaffolding shipped. Shadow-mode not yet enabled in prod. Vault host provisioned + reachable.

**Round-table gap closed (partial):** `signing.key` on VPS disk → blast radius tied to Vault AppRole, not the signing-key file.

## Current state (tonight)

- Vault host: `ubuntu-4gb-hel1-1` at `89.167.76.203` (public) + `10.100.0.3` (WireGuard).
- Vault 1.21.4 running, file backend, listening on WG interface only (`10.100.0.3:8200`).
- Sealed with 5 Shamir shares, threshold 3. Unseal shares on host at `/root/vault-init.json` (chmod 400, root-only).
- Transit engine enabled. Ed25519 key `osiriscare-signing` (non-exportable). Ed25519 pubkey `fsGvdHYB8mK2QFj6ZsDlB16YWSOPJUH8wdpT4aeHAJ4=`.
- AppRole `osiris-signer` with `sign`/`verify`/`read` policy. Credentials on host at `/root/vault-approle-osiris-signer.json`.
- `mcp-server` → Vault reachable over WireGuard (`curl https://10.100.0.3:8200/v1/sys/health` succeeds).
- `signing_backend.py` abstraction shipped with three implementations: `file` (current), `vault`, `shadow` (both, log divergence).
- 14 unit tests in `test_signing_backend.py` green.

## Critical ops tasks before Phase B

1. **Back up the 5 unseal shares and root token to 1Password**. Currently in `/root/vault-init.json` on the Vault host only. If the host disk dies, the Vault is unrecoverable. Copy to 1Password **tonight**.
2. **After 1Password backup is verified**, chattr +i the file so an attacker with root can't tamper with it. Even better: `shred -u /root/vault-init.json` to remove the keys from the host entirely — then manual unseal requires retrieving from 1Password on every boot, which is the correct operational posture.
3. **Rotate the root token** via `vault token revoke -self` and generate scoped tokens instead.

## Phase B: shadow mode (proposed next step)

1. Deploy the signing_backend abstraction to prod mcp-server (already in `main` — next deploy picks it up).
2. Add to `/opt/mcp-server/.env` on the VPS:
   ```
   SIGNING_BACKEND=shadow
   SIGNING_BACKEND_PRIMARY=file
   VAULT_ADDR=https://10.100.0.3:8200
   VAULT_APPROLE_ROLE_ID=<from /root/vault-approle-osiris-signer.json on vault host>
   VAULT_APPROLE_SECRET_ID=<same>
   VAULT_SIGNING_KEY_NAME=osiriscare-signing
   VAULT_SKIP_VERIFY=true
   ```
3. Restart mcp-server via `docker compose up -d`. mcp-server begins signing via `file` (unchanged production behavior) AND shadowing every sign operation to Vault. The `osiriscare_signing_backend_divergence_total` Prometheus counter stays flat if Vault is healthy.
4. Call sites in `privileged_access_attestation.py`, `evidence_chain.py`, and `fleet_updates.py` need to be refactored to use `get_signing_backend().sign(data)` instead of loading the key directly. This is the real code change for Phase B — scaffolding is done, call-site swap is the next commit.
5. Run for 1 week. Monitor `osiriscare_signing_backend_divergence_total`. If 0 divergences, we're clean to cut over.

## Phase C: cutover (after 1 week of clean shadow)

1. Flip `SIGNING_BACKEND=vault` in `/opt/mcp-server/.env`. `SIGNING_BACKEND_PRIMARY` no longer matters.
2. Restart mcp-server. Every signed order now originates from Vault.
3. `signing.key` on mcp-server remains as emergency fallback for 30 days — DO NOT delete yet.
4. Add a startup invariant `INV-SIGNING-BACKEND-VAULT` checking the Vault key version matches the last known-good version in DB. Breaks startup if an attacker rotated the Vault key without authorization.
5. Run a `rotate_server_pubkey` fleet order to each appliance carrying the Vault-sourced pubkey. The pubkey on disk stays the same physical bytes (it's the pubkey we minted the Vault key FOR), but we re-sign the rotation with Vault to prove end-to-end.

**Open decision for Phase C:** do we generate a NEW Ed25519 key in Vault (different pubkey from the current disk one) and require every appliance to accept a rotation? Or do we import the existing disk key into Vault as the same pubkey? The first is cleaner but requires a fleet-wide rotation ceremony. The second is operationally easier but means the key has lived on disk at some point, which partially defeats the purpose.

Recommendation: **generate new key in Vault, force fleet rotation**, retire the disk-born key. Timing: when every appliance is on daemon version that supports `rotate_server_pubkey`. Verify via `SELECT agent_version, COUNT(*) FROM site_appliances GROUP BY agent_version` before cutover.

## Phase D: retire (30 days after Phase C, clean)

1. Delete `/app/secrets/signing.key` from mcp-server.
2. Add DB constraint: `fleet_orders.signing_method` column (already exists from migration 177) must equal `vault`. Rows with `disk` are historical only; new inserts with `disk` are REJECTED via trigger.
3. Publish `SECURITY_ADVISORY_YYYY-MM-DD_SIGNING_KEY_ISOLATION.md`. Disclosure-first. Document the old blast radius, the new posture, the rotation ceremony, and the pubkey transition date.
4. Update `docs/security/key-rotation-runbook.md` to remove references to the disk-based procedure.

## Runtime dependencies (new)

- Hetzner bill: +€3.29/mo (Vault host CX11 class — already provisioned).
- `httpx` in mcp-server requirements.txt (already there).
- WireGuard mcp-server ↔ Vault must stay up. WG heartbeat is already on the `/api/admin/health` surface; add `vault_reachable: bool` as a separate health probe in Phase B.

## What CAN go wrong

- **Vault host reboots, mcp-server keeps signing via Vault, calls all fail.** Mitigation: manual unseal required on every Vault boot (intentional — no auto-unseal on same host). mcp-server retries the sign call with exponential backoff; the operator unseals Vault and signing resumes. Evidence writes stall in the queue during the outage but don't drop.
- **WG tunnel flap.** mcp-server → Vault calls timeout. Same retry posture. Consider adding a local signing-fallback policy: after N consecutive Vault failures, fall back to `file` with a flag in the audit log. This is a POLICY decision to document.
- **AppRole secret_id leaks.** Revocable: `vault write -f auth/approle/role/osiris-signer/secret-id-accessor` on Vault host → generate new secret_id → rotate in mcp-server env. Old secret_id is dead within minutes.
- **Vault host disk dies.** If unseal shares + root token are in 1Password, rebuild is possible from scratch + re-issue a new AppRole credential + re-key (but Ed25519 key itself is lost if no backup). Vault supports Raft backend + snapshots for HA — consider Phase E if uptime becomes an issue.

## What I need from you to proceed to Phase B

1. Confirm 1Password back up the 5 unseal shares + root token (they're still only on the Vault host).
2. Confirm you're OK adding the AppRole `role_id` + `secret_id` to `/opt/mcp-server/.env` on the VPS — these are lightweight-sensitive (rotatable weekly, scoped to sign/verify only, cannot read key material).
3. Green-light Phase B execution — I refactor the 3 call sites + commit + push + you set env + restart.

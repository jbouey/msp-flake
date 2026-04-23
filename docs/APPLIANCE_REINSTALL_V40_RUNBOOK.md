# Appliance Reinstall Runbook — v40 ISO (FIX-9 … FIX-16)

**Status:** extends v39 runbook (`APPLIANCE_REINSTALL_V39_RUNBOOK.md`). Flashing + physical steps are identical; validation is different. Read this in conjunction with v39.

## What v40 changes vs. v39

v39 fixed boot-loader + rebuild-path + root-partition-size. It shipped with a latent bug: `networking.firewall.extraCommands` resolved Cloudflare IPs at runtime via DNS, and when the site's DNS filter blocked the lookup, the appliance booted with no egress and could not phone home. The v39 recovery loop depended on `msp-first-boot.service`, which itself waited on `network-online.target` — chicken-and-egg.

v40 is the Tier-1 + Tier-2 hardening batch that makes appliance provisioning survive a hostile DNS filter and lets an on-site operator recover the box without remote access. Full index:

- **FIX-9** — firewall determinism. `networking.firewall.extraCommands` now uses pinned Cloudflare IPs (no runtime DNS). Any future DNS-dependent expression is blocked by a new regression pytest (FIX-13).
- **FIX-10** — `msp-egress-selfheal.timer` (60 s). Attempts outbound; on failure rewrites `/etc/resolv.conf` and restarts the network stack.
- **FIX-11** — `msp-first-boot.service` runs a 4-stage network self-test (DNS / TCP / TLS / HEALTH) and halts with a specific stage marker on fail. The LAN beacon on `:8443` publishes the stage so an on-site operator can tell which stage broke.
- **FIX-12** — `msp` local user gets NOPASSWD on a narrow set of read-only diagnostic commands. No sudo shell.
- **FIX-13** — regression pytest blocking any re-introduction of runtime DNS in the firewall extraCommands.
- **FIX-14** — `provisioning_network_fail` substrate invariant (sev2). Fires when an `install_sessions` row is present and heartbeats are recent but `first_outbound_success_at` is still NULL 15 min in. `/admin/substrate-health` shows it.
- **FIX-15** — `/api/install/report/net-ready` endpoint + migration 239 `install_sessions.first_outbound_success_at` column. The ISO's shell script POSTs on successful 4-stage pass. `/api/admin/substrate-installation-sla` now returns both `auth_up` and `net_up` distributions.
- **FIX-16** — break-glass passphrase generation moved to Phase 0 (`msp-breakglass-provision.service`, `DefaultDependencies=no`, `Before=network-pre.target`). Encrypted at rest with AES-256-CBC / PBKDF2 100k / key derived from MAC + `/etc/machine-id` / version tag `osiris-breakglass-v1`. Submit is a separate 5-min retry-forever timer with an idempotency marker. No network dependency on the generator path.

The validation gate of v40 is: the appliance phones home successfully AND the Phase 0 break-glass credentials are present BEFORE first network contact.

## Artifact provenance

On VPS `178.156.162.116`:

- Path: `/root/Msp_Flakes/result-iso-v40/iso/osiriscare-appliance.iso`
- Size: 2,327,478,272 bytes (≈ 2.17 GB)
- SHA256: `3f9db90067a4d05cbb8985eb41c0c0af0293a6b73356e9e4b8ef8088ed90ef03`
- Built from commit `2a596411` (HEAD of `main` at build time, v40 batch `ac9c006f` + flake fix `2a596411`).

## Target order (different from v39 — intentional)

1. **`84:3A:5B:1D:0F:E5`** — known-bad canary, FIRST. This appliance was the one that triggered the v39 firewall-DNS bug. If v40 succeeds here, FIX-9 + FIX-11 + FIX-16 are verified on the exact box where the bug was observed.
2. **`7C:D3:0A:7C:55:18`** — least historical state, SECOND.
3. **`84:3A:5B:91:B6:61`** — mid-history, LAST.

Rationale for the order inversion vs. v39: v39's hardest-last ordering protected against ambiguous failure. v40 is deliberately hardest-first because the v40 design target was "make `1D:0F:E5` work" — anything else is noise.

Do NOT flash more than one appliance in parallel until target 1 reports a successful `nixos_rebuild` canary.

## Flash + install procedure

Mechanical steps (scp, `diskutil`, `dd`, USB eject, power-cycle, boot menu) are identical to v39 runbook §Step 1–§Step 4. Use:

- Local filename: `osiriscare-appliance-v40.iso`
- SHA256 to verify: `3f9db90067a4d05cbb8985eb41c0c0af0293a6b73356e9e4b8ef8088ed90ef03`

## Step 5 — Phase 0 break-glass (v40-specific)

Watch for break-glass credential generation BEFORE network comes up. On a host with console access or BMC/serial:

```
journalctl -u msp-breakglass-provision --since '5 min ago' -o cat
```

Expected lines:
```
[msp-breakglass-provision] generating passphrase
[msp-breakglass-provision] encrypting at rest (AES-256-CBC, PBKDF2-HMAC iter 100000)
[msp-breakglass-provision] key material: osiris-breakglass-v1:<MAC>:<machine-id>
[msp-breakglass-provision] wrote /var/lib/msp/.emergency-credentials.enc
[msp-breakglass-provision] staged plaintext to /run/msp-breakglass-plaintext (tmpfs)
[msp-breakglass-provision] set msp user password
```

`DefaultDependencies=no` + `Before=network-pre.target` means this runs BEFORE `systemd-networkd` even starts. If you see these log lines interleaved with `networkd` startup, the Phase 0 ordering regressed — see FIX-16 §Why paragraph.

Verify encryption-at-rest on the appliance via the recovery shell or console:
```
ls -la /var/lib/msp/.emergency-credentials.enc
file /var/lib/msp/.emergency-credentials.enc     # → "data" (opaque)
head -c 16 /var/lib/msp/.emergency-credentials.enc | xxd  # → "Salted__" magic
```

The `.submitted` marker should appear within 5–10 min of network coming up (first successful submit to `/api/appliance/break-glass/submit`):
```
ls -la /var/lib/msp/.emergency-credentials.submitted
```

## Step 6 — 4-stage network self-test

The FIX-11 gate runs in `msp-first-boot.service` after the break-glass is already provisioned. On fail it halts with a specific stage marker that publishes to the LAN beacon at `:8443`.

From any machine on the appliance's LAN:
```
curl -s http://<APPLIANCE_IP>:8443/status | jq .
```

Healthy output:
```json
{ "stage": "health_ok", "net_ready_posted_at": "2026-04-23T14:22:11Z", ... }
```

Failure markers (stage names, in order):
- `dns_fail` — resolver is blocking `api.osiriscare.net`. Recommended action: add `api.osiriscare.net` to the customer's DNS filter allowlist (Pi-hole / Cisco Umbrella / Fortinet / Sophos / Barracuda).
- `tcp_fail` — resolver works but firewall is blocking :443 egress. Check customer firewall egress policy.
- `tls_fail` — TLS handshake fails. Usually TLS-inspection middlebox. Add Cloudflare IPs to the SSL bypass list.
- `health_fail` — TLS works but `/health` returns non-200. Central Command issue, not customer network. Escalate.

The backend invariant `provisioning_network_fail` surfaces any halted gate after 15 min on `/admin/substrate-health` with the same stage hint.

## Step 7 — Central Command validation

From VPS:

```sql
-- freshness
SELECT appliance_id, hostname, agent_version, first_checkin, last_checkin
  FROM site_appliances
 WHERE appliance_id LIKE '%84:3A:5B:1D:0F:E5%';

-- installation SLA (new in v40 — two distributions)
SELECT * FROM install_sessions
 WHERE mac_address = '84:3A:5B:1D:0F:E5'
 ORDER BY first_seen DESC LIMIT 3;
```

Expected in `install_sessions`:
- `first_seen` — when the installer ISO first POSTed `/report/start`.
- `first_outbound_success_at` — when the appliance's 4-stage gate passed (NEW in v40). Should be within a few minutes of `first_seen`. NULL = the gate never passed; `provisioning_network_fail` will fire.
- `auth_success_at` — first authenticated checkin.

Verify the invariant is **not firing** for this appliance:

```sql
SELECT invariant_name, site_id, details, opened_at, resolved_at
  FROM substrate_violations
 WHERE invariant_name = 'provisioning_network_fail'
   AND resolved_at IS NULL;
```

If a row exists for the freshly reinstalled appliance, something in the 4-stage gate regressed. Pull the LAN beacon status (Step 6) for the stage marker.

## Step 8 — nixos_rebuild canary

Identical to v39 runbook §Step 6 — fire `canary_post_reinstall.py` and wait for `admin_orders.status = 'completed'`. Pass criteria unchanged: `status = 'completed'` AND `error_message IS NULL`.

## Step 9 — Repeat for targets 2 and 3

Only after target 1 passes Step 6 (4-stage gate `health_ok`) AND Step 8 (canary `completed`). If either fails, fix the ISO-side issue before re-flashing.

## Expected outcomes summary

Green v40 reinstall produces:

- `msp-breakglass-provision.service` in journal BEFORE `systemd-networkd.service`.
- `/var/lib/msp/.emergency-credentials.enc` present, opaque, salt-prefixed.
- `/var/lib/msp/.emergency-credentials.submitted` present within 10 min.
- LAN beacon `:8443/status` reports `stage: health_ok`.
- `install_sessions.first_outbound_success_at` populated within 5 min of `first_seen`.
- No open row in `substrate_violations` with `invariant_name = 'provisioning_network_fail'` for this appliance.
- Canary `admin_orders` row shows `status = 'completed'`.

Any deviation from this set is a v40-specific regression — capture the LAN beacon stage and journal snippets before triage.

## Three-list reminder

Unchanged from v39 runbook §three-list reminder. v40 adds one invariant name (`provisioning_network_fail`) to the substrate lockstep — already wired in `ALL_ASSERTIONS` + `_DISPLAY_METADATA` + `substrate_runbooks/provisioning_network_fail.md`.

## Change log

- 2026-04-23 — initial — drafted after v40 ISO build `2a596411`. Expects reflash order `1D:0F:E5 → 7C:D3 → 91:B6:61` to validate FIX-16 + FIX-11 on the known-bad canary first.

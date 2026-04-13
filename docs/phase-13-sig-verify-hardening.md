# Phase 13 — Signature Verification Hardening (Session 205)

## Context

Session 205 Phase 12 captured the first live daemon error:

    signature verification failed: Ed25519 signature verification failed (tried 1 keys)

The daemon has a cached server pubkey (the "1 keys" it tried), but the
key doesn't match the server's current signing key. This blocks every
dangerous order type — `update_daemon`, `nixos_rebuild`, `healing`,
`diagnostic`, `sync_promoted_rule`, `configure_workstation_agent`,
`update_agent`, `enable_emergency_access`, `disable_emergency_access`
— across the affected appliances.

## Hardening items

### H1 — Daemon error enrichment (committed, pending daemon rebuild)

`appliance/internal/crypto/verify.go` now emits:

    Ed25519 signature verification failed
    (tried N keys; cur_fp=<16hex> prev_fp=<16hex> sp_len=<N> sp_sha256_16=<16hex>)

`cur_fp` = first 16 hex of cached current key. `sp_sha256_16` = first
16 hex of SHA-256 over the signed-payload bytes. `sp_len` = byte
length. None are secrets. Enables server-side triage without physical
access.

**Status:** code committed. Deployed with next appliance daemon release.

### H2 — Backend fingerprint divergence capture (deployed)

New admin endpoint:

    GET /api/admin/diagnostics/pubkey-divergence

Returns three buckets — `divergent`, `matched`, `unknown` — plus the
current server fingerprint. Divergent appliances will reject signed
fleet orders until they re-check in AND the daemon honors the
refreshed key.

### H3 — Daemon uses envelope `signed_payload` bytes (verified correct)

Audit result: `appliance/internal/orders/processor.go:501` passes
`order.SignedPayload` directly to `VerifyOrder`. `BuildSignedPayload`
is only used in unit tests. No reconstruction in the verify hot path.

**Status:** already correct. No code change required.

### H4 — Daemon auto-refresh on verify failure (future — daemon change)

When `VerifyOrder` returns an error, the daemon should fire an
immediate `force_checkin` to pull the fresh pubkey, then retry the
order once. If the second attempt still fails, escalate with a
structured error `sig_verify_pubkey_stale_after_refresh`.

Implementation:
1. Add `RequestImmediateCheckin() chan struct{}` to daemon control loop
2. In `Processor.verifySignature`, on `VerifyOrder` error, trigger
   the channel (non-blocking)
3. Daemon main loop `select`s on checkin-tick OR the refresh channel
4. Track a per-order retry count; max 1 refresh-retry per order

**Status:** code design ready. Daemon rebuild required.

### H5 — Fleet-wide pubkey fingerprint tracking (deployed)

`site_appliances.server_pubkey_fingerprint_seen` stores the
fingerprint of the pubkey the server last delivered to each
appliance in a checkin response. Updated on every checkin by
`sites.py`. Prometheus gauge
`osiriscare_appliance_server_pubkey_divergence` counts divergent
appliances.

**Note:** this tracks what we DELIVERED, not what the daemon actually
cached. Divergence from that alone doesn't prove a daemon bug — but
convergence across the fleet does prove the delivery path is healthy.

### H6 — Envelope carries pubkey hex (future — daemon change + backend)

The signed envelope should include `signing_pubkey_hex` as a field.
Daemon verifies against its cache first; on fail, tries the
envelope's pubkey IF it matches the most-recent-checkin-delivered
pubkey. Bounds the trust: daemon can't accept arbitrary pubkeys from
the envelope; only the one it was also told about via checkin.

Backend change: `fleet_cli.sign_order()` includes the signing pubkey
in `payload_dict` before `json.dumps(sort_keys=True)`.

Daemon change: parse envelope payload's `signing_pubkey_hex`,
cross-check against last checkin's delivered key, accept if match.

**Status:** protocol change; requires both backend + daemon update
in lockstep. Next daemon release cycle.

## Recovery path for the current stuck fleet

The primary appliance at `north-valley-branch-2` is rejecting signed
orders tonight. `update_daemon` would deliver H1+H4+H6 but also
requires signature verification — chicken-and-egg.

Options for bootstrap recovery:
1. **ISO reflash** (physical) — clean install with rebuilt daemon
2. **force_checkin / restart_agent** — these are safe order types
   (no signature required). Restart_agent will reset the daemon's
   in-memory pubkey cache; next checkin repopulates. This tests
   whether the daemon's pubkey cache was stale (if it was, a
   restart would fix it).
3. **Per-appliance signing key rotation** — if we can determine (via
   an out-of-band channel) what key the daemon has, we could rotate
   the server to match temporarily, issue a `force_checkin` + a
   `update_daemon`, then rotate back.

Option 2 is the cheapest first try. Follow with Option 1 if it
doesn't resolve.

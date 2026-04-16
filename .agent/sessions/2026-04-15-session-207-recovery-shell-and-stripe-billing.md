# Session 207 — 2026-04-15 (cont'd)

Recovery-shell fleet order + R+S hardening + full Stripe billing client path.

## Shipped

### enable_recovery_shell_24h fleet order (trilogy)
- **Migration 223** — add `enable_recovery_shell_24h` to `v_privileged_types`
- **fleet_cli.PRIVILEGED_ORDER_TYPES** + **privileged_access_attestation.ALLOWED_EVENTS** + attestation test all updated in lockstep
- **Watchdog Go handler** (`appliance/internal/watchdog/watchdog.go`) — writes pubkey to `/etc/msp-recovery-authorized-keys`, `systemctl start sshd`, arms systemd-run transient timer for 1..24h that stops sshd + wipes keys on expiry. Timer is systemd-enforced (operator oversight can fail; timer can't).
- **NixOS ISO v34** (`iso/appliance-disk-image.nix`) — sshd **enabled but `wantedBy=[]`** (in closure, not autostarted). `AuthorizedKeysFile = /etc/msp-recovery-authorized-keys`. INSTALLER_VERSION v33→v34.

### R+S non-blocking follow-ups (Task #179)
- Rate-limit break-glass GET to **5/hr** via extended `check_rate_limit(window_seconds, max_requests)`
- Reason validator: ≥20 chars, ≥5 distinct chars, must contain alphabetic word (rejects "aaaaaa…")
- Submit endpoint refactored `INSERT … ON CONFLICT (appliance_id) DO UPDATE` — no more 500 on race
- Retrieval now writes `break_glass_passphrase_retrieval` attestation bundle → flows into auditor kit
  - Event added to ALLOWED_EVENTS only (NOT fleet_cli PRIVILEGED_ORDER_TYPES — not a queued order)

### Stripe billing — client self-serve path (sprint A)
- **Dockerfile UID pin** — appuser UID 1000 to match bind-mount ownership
- **stripe==11.3.0** in image via rebuild (deploy workflow does NOT rsync Dockerfile, ad-hoc rebuild required)
- **Migration 224** — `signup_sessions`, `baa_signatures` (append-only, 7yr retention trigger), `subscriptions` (PHI-boundary CHECK comment), `stripe_events` (webhook dedup)
- **`client_signup.py`** — 4 routes: POST /start, /sign-baa, /checkout; GET /session/{id}
- **Webhook dispatch** — billing.py now routes `checkout.session.completed` by `metadata.signup_id` → client_signup handler (partner path unchanged)
- **4 Stripe products created live** — osiris-pilot $299 (one-time), osiris-essentials $499/mo, osiris-professional $799/mo, osiris-enterprise $1299/mo (lookup_keys used, not hard-coded price IDs)
- **Stripe webhook endpoint registered** — `we_1TMdAQBuOIdmSloyW1gccRrw` @ `https://app.osiriscare.net/api/billing/webhook`, signing secret in `.env`
- **Frontend** — `Signup.tsx`, `SignupBaa.tsx`, `SignupComplete.tsx`. Pricing.tsx pilot CTA rewired → `/signup?plan=pilot`. Paid tiers stay Calendly (PM consensus: demo-first for healthcare-SMB qualification).
- **PDF** — `~/Downloads/stripe-key-rotation-guide.pdf` (2pp, covers secret/webhook/publishable rotation + restricted-key upgrade path + compromise scenarios)

### v34 ISO built + pulled
- `~/Downloads/osiriscare-appliance-v34.iso` · 2.2GB · sha256 `3bc5e853...09b0c`

## Not shipped (physical blockers)
- **t740 reflash** — user physically present but multiple USB flashes failed with `squashfs mount failed / device descriptor read error -32` (classic USB-layer flaky write). Box kept booting internal disk's old installer closure at `osiriscare-installer / 0.4.4`. User has v34 ISO; needs fresh USB stick or different port.
- **Phase H5 Vault cutover** — gate is 7 days flat divergence; we're only at day 2 of 7. Also blocked on multi-trust ISO (disk pubkey + Vault pubkey both trusted) since Vault transit keys are non-exportable — can't import existing disk key.

## Decisions locked
- **Option C** for Stripe rotation (use live keys now, rotate after setup) — user accepted
- **Demo-first for paid tiers**, self-serve for $299 pilot only (PM consensus + consultant stances A-D)
- **No BAA with Stripe** — PHI boundary enforced at DB CHECK level + Stripe customer.metadata whitelist
- **Flat 20% partner margin** for v1, tiering deferred
- **Stripe Invoicing day-one** for Pro/Enterprise tiers (not deferred)

## Known-but-deferred
- Partner Connect Express onboarding (phase 2)
- Admin invoice approval queue UI (phase 2)
- Multi-trust ISO v35 for Vault flip (in progress — user said "yes ship now")
- Deploy workflow doesn't rsync Dockerfile or requirements.lock → future image rebuilds still ad-hoc

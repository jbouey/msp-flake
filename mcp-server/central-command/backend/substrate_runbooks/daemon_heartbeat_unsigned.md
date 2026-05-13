# daemon_heartbeat_unsigned

**Severity:** sev2
**Display name:** Daemon is silently NOT signing heartbeats

## What this means (plain English)

An appliance whose registered evidence-bundle public key is on file has emitted ≥12 consecutive heartbeats in the last 60 minutes with NULL signature. The daemon-side code at `appliance/internal/daemon/phonehome.go:827 SystemInfoSigned()` is responsible for signing heartbeats when the evidence-submitter's signing key is non-nil. If the appliance has a registered key but the daemon isn't signing, the cryptographic-attestation-chain claim in the master BAA Article 3.2 is undermined for that appliance.

## Root cause categories

- **Daemon version rollback** — appliance was downgraded to a daemon predating the D1 signing path.
- **Evidence-submitter signing-key lost** — `d.evidenceSubmitter.SigningKey()` returns nil (key file deleted, permission error, key-init bug).
- **Daemon-side signing error** — the `signFn` callback returns an error every cycle (logged as `slog.Warn("heartbeat signing failed — sending unsigned", ...)`).

## Immediate action

This is an operator-facing alert. **DO NOT surface to clinic-facing channels** — the practice should not see substrate-internal heartbeat-signing state per Session 218 task #42 opaque-mode parity rule.

1. **SSH into the appliance** (`ssh root@<appliance_ip>`).
2. **Check daemon version:** `/opt/msp-agent/daemon --version` — should be ≥0.4.x with D1 signing implemented.
3. **Check evidence-submitter signing key state:** `ls -la /var/lib/msp/evidence_signing_key`; `journalctl -u msp-agent -n 200 | grep -i signing`
4. **Look for signing-loop errors:** `journalctl -u msp-agent -n 500 | grep "heartbeat signing failed"`
5. **If appliance was recently re-flashed** — verify the appliance's registered evidence-bundle public key matches the new daemon's key (15-min rotation grace via the legacy-key-window column).

## Verification

- Panel: invariant row should clear on next 60s tick after a signed heartbeat arrives.
- CLI: `SELECT COUNT(*) FROM appliance_heartbeats WHERE appliance_id = '<id>' AND observed_at > NOW() - INTERVAL '60 minutes' AND agent_signature IS NULL;` → expect to drop below 12.

## Escalation

If daemon restart + key-file restoration do not resolve, escalate to engineering. The signing path is exercised by `appliance/internal/daemon/heartbeat_sign_test.go` — failing tests in the daemon repo suggest the signing helper itself regressed.

## False-positive guard

This invariant only fires when the appliance has a registered evidence-bundle public key. Appliances that have never registered (pre-D1 daemons, manually-provisioned dev appliances) are excluded — substrate engine doesn't expect them to sign.

## Related runbooks

- `daemon_heartbeat_signature_invalid.md` — sev1; fires when signature is present but does not verify (potential compromise OR canonical-format drift).
- `daemon_on_legacy_path_b.md` — sev3-info; fires when daemon is on the legacy verification path B (pre-v0.5.0).
- `offline_appliance_long.md` — sev2; fires when an appliance has been offline beyond the SLA. Cross-reference if appliance is both offline AND unsigned.

## Change log

- 2026-05-13 — initial — D1 backend-verification + substrate-invariant landing (Task #40, Counsel Rule 4).


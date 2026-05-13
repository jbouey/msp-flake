# daemon_heartbeat_signature_invalid

**Severity:** sev1
**Display name:** Daemon signature does NOT verify — potential compromise OR canonical-format drift

## What this means (plain English)

An appliance has emitted ≥3 heartbeats in the last 15 minutes with `signature_valid=FALSE`. The backend verifier (`signature_auth.verify_heartbeat_signature`) tried BOTH paths (path A daemon-supplied timestamp; path B ±60s reconstruction) AND BOTH the current and previous (within rotation grace) registered evidence-bundle public keys, and none verified the signature.

## Root cause categories

- **Compromise** — the appliance's signing key has been replaced by an attacker. The attacker's daemon is signing with a different key; backend can't verify under the registered public key.
- **Canonical-format drift** — the daemon and backend are signing/verifying different canonical payload formats. Format is daemon-defined at `appliance/internal/daemon/phonehome.go:837`; if daemon code or backend verifier drifts, all signatures from that daemon will fail-verify.
- **Stale rotation** — the appliance's evidence-bundle key rotated but the legacy-key-window column was not populated, so the 15-min grace failed.

## Immediate action

**SEV1 — operator escalation immediately. DO NOT surface to clinic-facing channels** (Session 218 task #42 opaque-mode parity — practice should not see compromise/drift signals; operator routes them).

### Step 1 — Determine which cause

Compare the appliance's registered evidence-bundle public key (in DB) vs the daemon's actual signing pubkey (SSH the appliance, inspect signing-key file). If they MATCH → likely canonical-format drift. If they DON'T → potential compromise.

### Step 2A — If keys match (canonical-format drift)

Diff the canonical-payload construction across 4 lockstep surfaces:
1. `appliance/internal/daemon/phonehome.go:837` (daemon side)
2. `signature_auth.py::_heartbeat_canonical_payload` (backend verifier)
3. This runbook
4. Auditor kit's `verify.sh` shell-side reconstruction

If any disagree, the daemon-vs-backend format is the bug. Fix the verifier OR roll back the daemon. **Do NOT just rotate the appliance's key — that hides the bug.**

### Step 2B — If keys do NOT match (compromise)

1. **Isolate the appliance** — block its WireGuard tunnel; quarantine via fleet-cli.
2. **Investigate forensically** — preserve appliance state, dump signing-key file (if accessible), check `/var/log/auth.log`, `journalctl -u msp-agent` for unauthorized access.
3. **Rotate the appliance's evidence-bundle public key** via the standard rotation path (legacy-key-window column carries the old key for the 15-min grace).
4. **Notify the practice owner** per the BAA Article 3.3 incident-reporting workflow.

## Verification

- Panel: invariant row clears once the appliance emits a signed heartbeat that DOES verify under a known pubkey.
- CLI: `SELECT COUNT(*) FROM appliance_heartbeats WHERE appliance_id = '<id>' AND observed_at > NOW() - INTERVAL '15 minutes' AND signature_valid = FALSE;` → expect to drop to 0.

## Escalation

Resolution alone is NOT sufficient — root-cause analysis MUST conclude before treating the alert as closed. Sev1 = engineering on-call paged.

## False-positive guard

Filters for `signature_valid IS NOT NULL` — only fires for the explicit "tried to verify and it didn't work" state, not for heartbeats where verification wasn't attempted.

## Related runbooks

- `daemon_heartbeat_unsigned.md` — sev2; fires when daemon should sign but isn't (different class — this is "tried to sign but verification failed").
- `daemon_on_legacy_path_b.md` — sev3-info; track which path was used at attestation time.
- `pre_mig175_privileged_unattested.md` — sibling chain-of-custody compromise-detection invariant for privileged orders.

## Change log

- 2026-05-13 — initial — D1 backend-verification + substrate-invariant landing (Task #40, Counsel Rule 4).


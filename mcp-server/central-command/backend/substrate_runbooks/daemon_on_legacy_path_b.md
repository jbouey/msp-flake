# daemon_on_legacy_path_b

**Severity:** sev3-info until 2026-08-13 deprecation deadline; informational flag (`is_past_deprecation`) auto-set on the violation row past that date — runtime severity stays sev3, but operator dashboards SHOULD escalate visually past the deadline.
**Display name:** Daemon using legacy path-B heartbeat verification (pre-v0.5.0)

## What this means (plain English)

An appliance has emitted ≥12 heartbeats in the last 24 hours with `signature_canonical_format='v1b-reconstruct'`. This means the daemon did NOT include `heartbeat_timestamp` in its CheckinRequest body, so the backend reconstructed the canonical payload using its own `NOW()` and tried integer timestamps in the ±60s window until one verified. Path B is the legacy backward-compat path. Path A (the auditor-preferred mode) uses the daemon-supplied timestamp directly.

## Root cause categories

- **Daemon predates v0.5.0** — the appliance is running an older daemon build (e.g. v0.4.x) that does not include the `HeartbeatTimestamp` field in CheckinRequest. Most common cause today; expected during fleet-rollout window.
- **Daemon v0.5.0+ but field-omitted via omitempty** — if the daemon's `time.Now().UTC().Unix()` returned 0 for some reason, `omitempty` would drop the field and the backend would fall back to path B. Unusual but possible.
- **Future regression** — a daemon v0.6.x might remove the field; substrate watches this.

## Immediate action

**Informational by default — DO NOT surface to clinic-facing channels** (Session 218 task #42 opaque-mode parity). Operator dashboards show this for fleet-rollout-tracking purposes only.

### Today (informational)

No action required. Watch the fleet-rollout dashboard; this invariant tracks how many appliances are still on the legacy path.

### After 2026-08-13 (escalate visually on dashboard)

For each affected appliance:

1. **Check daemon version:** `ssh root@<appliance_ip> '/opt/msp-agent/daemon --version'`
2. **Upgrade to v0.5.0+ via the standard fleet-update path** (NOT scp/rsync):
   ```
   python3 backend/fleet_cli.py update-daemon \
     --site-id <site_id> --version v0.5.0 \
     --actor-email <your-email> --reason "v1b deprecation deadline reached"
   ```
3. **Verify the upgrade:** within ~5 minutes the next heartbeat should arrive with `signature_canonical_format='v1a-daemon'` (path A active).

## Verification

- Panel: invariant row clears once the appliance emits heartbeats with `signature_canonical_format='v1a-daemon'`.
- CLI: `SELECT signature_canonical_format, COUNT(*) FROM appliance_heartbeats WHERE appliance_id = '<id>' AND observed_at > NOW() - INTERVAL '24 hours' GROUP BY 1;` → expect `v1a-daemon` to dominate.

## Escalation

Sev3 today, so no escalation. Past 2026-08-13, operator dashboards SHOULD visually escalate (red banner / flag) to indicate the deprecation deadline has passed for this appliance. Engineering action: cut a v0.5.0+ release and roll the fleet forward.

## Deprecation policy

The 2026-08-13 deprecation date is encoded directly in `assertions.py::_check_daemon_on_legacy_path_b`. To extend (e.g. fleet rollout slower than expected), update `DEPRECATION_DATE` in that function and document the decision in the engineering log.

## Related runbooks

- `daemon_heartbeat_unsigned.md` — sev2; sibling D1 invariant for "should sign but isn't".
- `daemon_heartbeat_signature_invalid.md` — sev1; sibling D1 invariant for "signed but doesn't verify".
- `agent_version_lag.md` — sev2; tracks fleet-wide daemon version distribution. Cross-reference for upgrade planning.

## Change log

- 2026-05-13 — initial — D1 hybrid protocol (option c per round-table 2026-05-13) landing.


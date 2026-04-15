# RB-WINRM-PIN-RESET — reset a stuck WinRM TLS pin

**Severity:** sev2
**Substrate invariant:** `winrm_pin_mismatch`
**Remediation path:** host-scoped `watchdog_reset_pin_store` fleet order
**Audit:** full privileged chain of custody (actor_email + reason +
compliance_bundles attestation + admin_audit_log row)

## Symptom

Substrate dashboard opens `winrm_pin_mismatch` (sev2). The violation
details name `appliance_id`, `target_hostname`, and recent failure
count. Drill into `execution_telemetry` if you want the raw error:

```sql
SELECT created_at, runbook_id, substring(error_message, 1, 180) AS err
  FROM execution_telemetry
 WHERE appliance_id = '<from violation>'
   AND hostname = '<from violation>'
   AND created_at > NOW() - INTERVAL '2 hours'
   AND NOT success
 ORDER BY created_at DESC LIMIT 10;
```

Errors look like:
`get session: TLS pin check for NVDC01: did-not-match ...`

## Root-cause triage — DO THIS FIRST

The pin mismatch is a security signal. Three causes, three responses:

| Cause | Evidence | Response |
|---|---|---|
| DC cert renewed | AD cert rotation on the target host matches the first failure time ± a few minutes | **Pin reset is correct.** Proceed to remediation. |
| VM rebuilt | Target VM was re-imaged / re-provisioned (ask the customer's IT) | **Pin reset is correct.** Proceed to remediation. |
| MITM / DNS hijack | No recent cert rotation. Check appliance's DNS resolution + ARP table for the target IP. Unexpected MAC on the target IP = attack | **DO NOT reset the pin.** Escalate. The pin mismatch is working as designed — defeating it is what the attacker wants. |

If you cannot distinguish between "legitimate rotation" and "attack" in
<30 minutes, treat as attack and escalate. Re-TOFUing into a MITM is
strictly worse than leaving WinRM broken.

## Remediation (only after legitimate-rotation confirmed)

Issue a host-scoped pin reset via the watchdog:

```bash
docker exec -i mcp-server python3 -m dashboard_api.fleet_cli create \
    watchdog_reset_pin_store \
    --param site_id=<site_id> \
    --param appliance_id=<appliance_id>-watchdog \
    --param host=<target_hostname> \
    --actor-email <your-email> \
    --reason "DC cert renewed <YYYY-MM-DD>; verified against <evidence>"
```

Parameters:

- `appliance_id` must include the `-watchdog` suffix so the order is
  dispatched to the watchdog service (not the main daemon).
- `host` scopes the reset to the one WinRM target. Omit to reset the
  whole pin store (blast-radius-wider — use sparingly).
- `reason` must be ≥20 characters. Cite the specific evidence you
  gathered during triage — the string lands in the attestation bundle
  and is visible to the customer.

## Confirmation

Within 2 minutes the watchdog picks up the order, deletes the pin
entry, and ACKs success. Within another 1-5 minutes the main daemon's
next Windows runbook against that host re-TOFUs the new cert and the
runbook succeeds. The `winrm_pin_mismatch` invariant auto-resolves.

If the invariant doesn't auto-resolve within 30 min, either the reset
didn't land (check `fleet_order_completions` for the order_id) or the
root cause was NOT a rotation — re-triage.

## Audit trail

Every successful pin reset leaves a five-part chain of custody:

1. `compliance_bundles` row (`check_type='privileged_access'`,
   Ed25519-signed, OTS-anchored)
2. `fleet_orders` row (`order_type='watchdog_reset_pin_store'`)
3. `admin_audit_log` row (action=`api_key.*` from migration 209
   trigger plus the manual attestation)
4. `watchdog_events` row (`event_type='order_executed'`, hash-chained
   per appliance)
5. Customer-visible privilege dashboard entry (H6; wires the above
   into /client/privileged-history)

The chain is what makes the pin reset auditor-safe. If the pin was
reset into a MITM, the chain is how the post-incident forensics
establishes that the operator made that decision with those inputs.
Do NOT bypass chain-of-custody by rewriting `/var/lib/msp/winrm_pins
.json` over SSH — that's the attack surface the watchdog replaces.

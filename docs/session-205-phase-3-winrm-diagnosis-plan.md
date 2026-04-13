# Session 205 Phase 3 — WinRM 401 Cascade Diagnosis Plan

## Failure profile (verified)

- **Volume:** 3,194 entries in `v_learning_failures` over 14 days
- **Targets:** ws01 (192.168.88.251), NVWS01 (alias of same host)
- **Affected runbooks:** RB-WIN-SEC-005, RB-WIN-SEC-016, RB-WIN-SEC-017,
  RB-WIN-SEC-019, RB-WIN-SVC-001 — every Windows runbook hits the same
  failure
- **Error message (workstation):**
  `<rb-id> phase remediate failed: create shell: http response error:
   401 - invalid content type`
- **Error message (DC):**
  `RB-WIN-SEC-016 phase remediate failed: get session: TLS pin check
   for NVDC01: dial tcp: lookup NVDC01 on 192.168.88.1:53: no such host`

## Two root-cause families

### A. WinRM auth (workstation)

The `401 invalid content type` is canonical for one of:
1. **Wrong credentials** — localadmin password rotated, appliance has stale copy
2. **NTLM disabled** on Windows side — Kerberos required but unavailable
3. **WinRM listener restricted** to a different transport profile
4. **Content-Type negotiation broken** — appliance sending wrong WinRM SOAP envelope

### B. DNS resolution (DC)

`lookup NVDC01 on 192.168.88.1:53: no such host` means:
1. The router's DNS does not resolve the AD computer name
2. Appliance is configured to use the router as DNS, not the DC's DNS
3. Could be fixed by `configure_dns` fleet order with NVDC01→192.168.88.250
   in `extra_hosts`

## Diagnosis steps (when on iMac via reverse tunnel)

```bash
# 0. Get on the appliance
ssh root@178.156.162.116
sshpass -p 022006 ssh -p 2250 jrelly@localhost
ssh -p 22 root@192.168.88.235   # appliance LAN IP

# 1. Verify the credentials the appliance has cached
journalctl -u appliance-daemon --since "1 hour ago" \
  | grep -E "windows_target|LookupWinTarget|credentials"

# 2. Try WinRM auth manually with the localadmin creds
docker exec appliance-daemon /usr/bin/curl -ku 'localadmin:NorthValley2024!' \
  -X POST 'http://192.168.88.251:5985/wsman' \
  -H 'Content-Type: application/soap+xml;charset=UTF-8' \
  --data-binary '<empty soap envelope>'
# Expect 200 with WS-Management response, NOT 401

# 3. Verify DC resolution
nslookup NVDC01 192.168.88.1
nslookup NVDC01 192.168.88.250

# 4. If resolution fails, push fix:
docker exec mcp-server python3 /app/dashboard_api/fleet_cli.py \
  create configure_dns \
  --param site_id=north-valley-branch-2 \
  --param 'extra_hosts={"NVDC01":"192.168.88.250","NVWS01":"192.168.88.251"}'
```

## Isolation today (Migration 164)

Marked the 11 affected Windows checks as `is_monitoring_only=true` so:
- The appliance still reports drift (we keep observability)
- No remediation runbook is dispatched (no WinRM call attempted)
- L2 learning sees clean data instead of cascading failures

## Rollback when fixed

The migration includes the rollback SQL inline as a comment block.
After WinRM is verified working manually, run the rollback SQL in a
single transaction.

## Long-term fix

- **Add WinRM health check** to the appliance: probe with a no-op
  `GET /wsman` every 5 min, alert if 401/timeout. Today the only signal
  we get is the per-runbook failure cascade.
- **Credential rotation hook**: when site_credentials.password changes,
  fire a fleet order to invalidate the appliance's cached value.
- **Better error classification**: the agent should differentiate
  "credentials rejected" (401) from "host unreachable" (timeout) from
  "service unavailable" (503) and report distinct error codes.

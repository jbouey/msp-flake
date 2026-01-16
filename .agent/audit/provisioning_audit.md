# Provisioning and Target Management Audit

**Date:** 2026-01-16
**Purpose:** Understand existing architecture before implementing zero-friction deployment

## Current Architecture Summary

### 1. Provisioning Flow
- **File:** `packages/compliance-agent/src/compliance_agent/provisioning.py`
- **Method:** QR code / provision code based setup
- **Process:**
  1. Appliance checks if `config.yaml` exists
  2. If not, prompts for provision code (or auto-provisions with code)
  3. Claims code from Central Command API (`/api/partners/claim`)
  4. Receives `site_id`, `appliance_id`, partner info
  5. Creates `/var/lib/msp/config.yaml` with API key, endpoints, etc.

### 2. Credential-Pull Architecture
- **Pattern:** RMM-style credential-pull (like Datto, ConnectWise, NinjaRMM)
- **Implementation:** 
  - Appliance calls `/api/appliances/checkin` every 60 seconds
  - Central Command returns `windows_targets` array with credentials
  - Credentials come from `site_credentials` table
  - **Benefits:**
    - No local credential storage on appliance
    - Credential rotation picked up automatically (~60s)
    - Stolen appliance doesn't expose credentials

### 3. Check-In Endpoint
- **File:** `mcp-server/central-command/backend/sites.py` (line 832)
- **Endpoint:** `POST /api/appliances/checkin`
- **Request:**
  ```json
  {
    "site_id": "...",
    "hostname": "...",
    "mac_address": "...",
    "ip_addresses": [...],
    "uptime_seconds": 3600,
    "agent_version": "1.0.32",
    "nixos_version": "24.05"
  }
  ```
- **Response:**
  ```json
  {
    "status": "ok",
    "appliance_id": "...",
    "server_time": "...",
    "windows_targets": [
      {
        "hostname": "192.168.88.250",
        "username": "NORTHVALLEY\\Administrator",
        "password": "...",
        "use_ssl": false
      }
    ],
    "enabled_runbooks": [...]
  }
  ```

### 4. Target Management in Appliance Agent
- **File:** `packages/compliance-agent/src/compliance_agent/appliance_agent.py`
- **Method:** `_update_windows_targets_from_response()` (line 1199)
- **Process:**
  1. Receives `windows_targets` from check-in response
  2. Converts to `WindowsTarget` objects
  3. Replaces `self.windows_targets` list
  4. Uses these targets for compliance scans

### 5. Existing Workstation Discovery
- **File:** `packages/compliance-agent/src/compliance_agent/workstation_discovery.py`
- **Method:** Uses PowerShell `Get-ADComputer` via WinRM on domain controller
- **Capabilities:**
  - Queries AD for Windows 10/11 workstations
  - Checks online status (WinRM port, ping, WMI)
  - Caches results (1 hour TTL)
- **Integration:** Used by `appliance_agent.py` for workstation compliance checks

### 6. Credential Storage
- **Table:** `site_credentials`
- **Fields:** `credential_type`, `encrypted_data` (JSON)
- **Types:** `'winrm'`, `'domain_admin'`, `'local_admin'`, `'service_account'`
- **Format:** JSON with `host`, `username`, `password`, `domain`, `use_ssl`

## What Exists vs What Needs Building

### ✅ Already Exists
1. Credential-pull infrastructure
2. Check-in endpoint with target delivery
3. Workstation discovery via AD (PowerShell/Get-ADComputer)
4. Windows target management in appliance agent
5. WinRM executor for remote PowerShell

### ❌ Needs Building
1. **Domain auto-discovery** - No DNS SRV/DHCP discovery yet
2. **AD enumeration for servers** - Only workstations are discovered currently
3. **Automatic enumeration trigger** - No flag-based triggering
4. **Go agent deployment** - No automatic deployment module
5. **Domain discovery API** - No endpoint to receive discovery reports
6. **Enumeration results API** - No endpoint to receive enumeration data
7. **Database schema** - Missing fields for domain discovery, enumeration results, agent deployments

## Integration Points

### Where to Add Domain Discovery
- **Location:** `appliance_agent.py` `__init__` or `run()` method
- **Trigger:** First boot (check if domain already discovered)
- **Action:** Call `domain_discovery.discover()`, then report to Central Command

### Where to Add Enumeration
- **Location:** `appliance_agent.py` `_run_cycle()` method
- **Trigger:** Check `trigger_enumeration` flag from check-in response
- **Action:** Call `ad_enumeration.enumerate_all()`, update `windows_targets`, report results

### Where to Add Go Agent Deployment
- **Location:** `appliance_agent.py` `_run_cycle()` method
- **Trigger:** After enumeration discovers workstations
- **Action:** Check which workstations need agents, deploy via WinRM

## Conflicts/Considerations

1. **Credential Format:** Current `site_credentials` uses `host` field, but domain credentials need `domain_name` as identifier
2. **Target List Management:** Enumeration will populate `windows_targets` automatically - need to merge with manually configured targets?
3. **Workstation Discovery:** Existing `workstation_discovery.py` only finds workstations - need to extend to servers too
4. **WinRM Dependency:** AD enumeration requires WinRM on domain controller - need to handle cases where it's not enabled

## Next Steps

1. Create `domain_discovery.py` module
2. Integrate into appliance agent boot sequence
3. Create Central Command API endpoints
4. Create `ad_enumeration.py` (extend workstation_discovery patterns)
5. Add trigger flags to check-in response
6. Create `agent_deployment.py` module
7. Database migration for new tables/fields

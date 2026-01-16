# Appliance Provisioning Module

**Last Updated:** 2026-01-16 (Session 43 - Zero-Friction Deployment Pipeline)

## Locations

| Component | Path |
|-----------|------|
| Agent-side module | `packages/compliance-agent/src/compliance_agent/provisioning.py` |
| Backend API | `mcp-server/central-command/backend/provisioning.py` |

## Overview

The provisioning module handles first-boot appliance setup via QR code or manual provision code entry. When an appliance boots without a `config.yaml` file, it enters provisioning mode.

## Zero-Friction Deployment (2026-01-16)

**New Feature:** Automatic domain discovery and AD enumeration eliminates manual target configuration.

### Flow

1. **Appliance Boot** → Automatically discovers AD domain via DNS SRV records
2. **Domain Discovery** → Reports to Central Command, partner receives notification
3. **Credential Entry** → Partner enters ONE domain admin credential (only human touchpoint)
4. **AD Enumeration** → Appliance automatically discovers all servers and workstations
5. **Target Updates** → Windows targets updated automatically, workstations stored for Go agent deployment
6. **First Scan** → Compliance scanning begins immediately after enumeration

### Benefits

- **Zero manual target entry** - All servers/workstations discovered automatically
- **Single credential** - One domain admin credential enables full deployment
- **Fast deployment** - First compliance report within 1 hour of credential entry
- **Non-destructive** - Discovered targets merge with manual configs (doesn't overwrite)

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/appliances/domain-discovered` | Appliance reports discovered domain |
| POST | `/api/appliances/enumeration-results` | Appliance reports enumeration results |
| GET | `/api/sites/{site_id}/domain-credentials` | Fetch domain credentials for enumeration |
| POST | `/api/sites/{site_id}/domain-credentials` | Submit domain credentials (triggers enumeration) |

### Check-In Enhancements

The `/api/appliances/checkin` endpoint now returns:
- `trigger_enumeration` (boolean) - Triggers AD enumeration on next cycle
- `trigger_immediate_scan` (boolean) - Triggers immediate compliance scan

Flags are cleared after being sent to appliance.

## Backend API Endpoints

Central Command provides the backend API for provisioning (`/api/provision/*`):

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/provision/claim` | Claim provision code (creates site) |
| GET | `/api/provision/validate/{code}` | Validate code before claiming |
| POST | `/api/provision/status` | Update provisioning progress |
| POST | `/api/provision/heartbeat` | Heartbeat from provisioning appliance |
| GET | `/api/provision/config/{appliance_id}` | Get appliance configuration |

### Claim Response

```json
{
  "status": "claimed",
  "site_id": "partner-clinic-abc123",
  "appliance_id": "partner-clinic-abc123-84:3A:5B:91:B6:61",
  "partner": {
    "slug": "nepa-it",
    "brand_name": "NEPA IT Solutions",
    "primary_color": "#2563EB"
  },
  "api_endpoint": "https://api.osiriscare.net"
}
```

## Functions

### `needs_provisioning() -> bool`

Check if appliance needs provisioning (no config.yaml exists).

```python
from compliance_agent.provisioning import needs_provisioning

if needs_provisioning():
    # Enter provisioning mode
    ...
```

### `get_mac_address() -> str`

Get the primary MAC address of the appliance.

```python
mac = get_mac_address()
# Returns: "84:3A:5B:91:B6:61" or fallback "02:XXXXXXXXXX"
```

### `get_hostname() -> str`

Get the appliance hostname.

```python
hostname = get_hostname()
# Returns: "osiriscare-appliance" or actual hostname
```

### `claim_provision_code(code: str, api_endpoint: str) -> Tuple[bool, dict]`

Claim a provision code from Central Command.

```python
success, data = claim_provision_code("ABCD1234EFGH5678")

if success:
    site_id = data["site_id"]
    partner_info = data["partner"]
else:
    error = data["error"]
```

**Response on success:**
```json
{
  "status": "claimed",
  "site_id": "partner-clinic-abc123",
  "appliance_id": "partner-clinic-abc123-84:3A:5B:91:B6:61",
  "partner": {
    "slug": "nepa-it",
    "brand_name": "NEPA IT Solutions",
    "primary_color": "#2563EB"
  },
  "api_endpoint": "https://api.osiriscare.net"
}
```

### `create_config(site_id, appliance_id, partner_info, api_endpoint) -> Path`

Create the appliance configuration file.

```python
config_path = create_config(
    site_id="partner-clinic-abc123",
    appliance_id="partner-clinic-abc123-84:3A:5B:91:B6:61",
    partner_info={"slug": "nepa-it", "brand_name": "NEPA IT"},
    api_endpoint="https://api.osiriscare.net"
)
# Returns: Path("/var/lib/msp/config.yaml")
```

**Generated config.yaml:**
```yaml
site_id: partner-clinic-abc123
appliance_id: partner-clinic-abc123-84:3A:5B:91:B6:61
api_key: <generated-32-char-key>
api_endpoint: https://api.osiriscare.net
poll_interval: 60
enable_drift_detection: true
enable_evidence_upload: true
enable_l1_sync: true
healing_enabled: true
healing_dry_run: false
state_dir: /var/lib/msp
log_level: INFO
partner:
  slug: nepa-it
  brand_name: NEPA IT Solutions
  primary_color: "#2563EB"
provisioned_at: 2026-01-04T15:30:00+00:00
mac_address: 84:3A:5B:91:B6:61
hostname: osiriscare-appliance
```

### `run_provisioning_cli(api_endpoint) -> bool`

Run interactive CLI provisioning.

```python
# Called when appliance boots without config
success = run_provisioning_cli()
```

**CLI Output:**
```
============================================================
  OsirisCare Appliance Provisioning
============================================================

MAC Address: 84:3A:5B:91:B6:61
Hostname:    osiriscare-appliance

Enter your provision code (from partner dashboard):
Format: XXXXXXXXXXXXXXXX (16 characters)

Provision Code: ABCD1234EFGH5678

Provisioning...

============================================================
  Provisioning Complete!
============================================================

Site ID:     partner-clinic-abc123
Partner:     NEPA IT Solutions
Config:      /var/lib/msp/config.yaml

The agent will now restart in normal operation mode.
```

### `run_provisioning_auto(provision_code, api_endpoint) -> bool`

Run non-interactive provisioning with provided code.

```python
# For automated/scripted provisioning
success = run_provisioning_auto("ABCD1234EFGH5678")
```

## Integration with Appliance Agent

The `appliance_agent.py` main() function checks for provisioning on startup:

```python
def main():
    parser = argparse.ArgumentParser(description="OsirisCare Compliance Agent")
    parser.add_argument('--provision', metavar='CODE', help='Provision with code')
    parser.add_argument('--provision-interactive', action='store_true')
    args = parser.parse_args()

    # Handle explicit provisioning flags
    if args.provision:
        run_provisioning_auto(args.provision)
        return
    if args.provision_interactive:
        run_provisioning_cli()
        return

    # Auto-detect provisioning mode if no config exists
    if needs_provisioning():
        if run_provisioning_cli():
            print("Provisioning complete. Starting agent...")
        else:
            return

    # Normal operation...
```

## Test Coverage

19 tests in `tests/test_provisioning.py`:

- `TestGetMacAddress` - MAC address retrieval and format
- `TestGetHostname` - Hostname retrieval and fallback
- `TestClaimProvisionCode` - Success, invalid, expired, timeout, connection error
- `TestCreateConfig` - File creation, contents, API key generation
- `TestNeedsProvisioning` - Detection logic
- `TestGenerateApiKey` - Key length and uniqueness
- `TestProvisioningIntegration` - Full end-to-end flow

## Error Handling

| Error | Behavior |
|-------|----------|
| Invalid code | Returns `(False, {"error": "Invalid provision code"})` |
| Expired code | Returns `(False, {"error": "Provision code expired"})` |
| Already claimed | Returns `(False, {"error": "Code already claimed"})` |
| Network timeout | Returns `(False, {"error": "Connection timed out"})` |
| Connection error | Returns `(False, {"error": "Failed to connect to server"})` |

## Configuration Paths

| Path | Purpose |
|------|---------|
| `/var/lib/msp/config.yaml` | Main configuration file |
| `/var/lib/msp/` | State directory |

File permissions: `0600` (owner read/write only)

## Credential-Pull Architecture

**Added in Session 9 (2026-01-04)**

After provisioning, appliances receive Windows target credentials via the check-in API rather than storing them locally. This follows the RMM industry pattern (Datto, ConnectWise, NinjaRMM).

### How It Works

1. **Partner adds credentials** via `/api/partners/me/sites/{site_id}/credentials`
2. **Appliance checks in** every 60 seconds via `/api/appliances/checkin`
3. **Server returns** `windows_targets` array in the check-in response
4. **Agent updates** in-memory targets via `_update_windows_targets_from_response()`
5. **Compliance checks** run using the freshly fetched credentials

### Credential Storage

Credentials are stored in the `site_credentials` table on Central Command:

| Column | Type | Purpose |
|--------|------|---------|
| `site_id` | varchar | Site association |
| `credential_type` | varchar | Type: `winrm`, `domain_admin`, `service_account`, `local_admin` |
| `credential_name` | varchar | Display name |
| `encrypted_data` | bytea | JSON with host, username, password, domain, use_ssl |
| `created_at` | timestamp | Creation time |

### Benefits

| Benefit | Description |
|---------|-------------|
| **No local storage** | Credentials never touch disk on appliance |
| **Automatic rotation** | Changes propagate in ~60s (next check-in) |
| **Stolen device safety** | Compromised appliance doesn't expose credentials |
| **Audit trail** | All credential access logged server-side |

### Agent-Side Implementation

The `appliance_agent.py` receives and applies credentials each cycle:

```python
async def _update_windows_targets_from_response(self, response: Dict):
    """Update Windows targets from server check-in response."""
    windows_targets = response.get('windows_targets', [])

    for target_cfg in windows_targets:
        target = WindowsTarget(
            hostname=target_cfg.get('hostname'),
            username=target_cfg.get('username'),  # DOMAIN\\user format
            password=target_cfg.get('password'),
            use_ssl=target_cfg.get('use_ssl', False),
            port=5986 if target_cfg.get('use_ssl') else 5985,
            transport='ntlm',
        )
        new_targets.append(target)

    self.windows_targets = new_targets  # Replace, don't cache
```

### Credential Lifecycle

```
Partner Dashboard                    Central Command                     Appliance
      │                                    │                                 │
      │ POST /api/partners/me/sites/       │                                 │
      │      {site}/credentials            │                                 │
      │───────────────────────────────────>│                                 │
      │                                    │ Store in site_credentials       │
      │                                    │                                 │
      │                                    │<─────────────────────────────────│
      │                                    │  POST /api/appliances/checkin   │
      │                                    │                                 │
      │                                    │──────────────────────────────────>
      │                                    │  { windows_targets: [...] }     │
      │                                    │                                 │
      │                                    │                   Apply targets │
      │                                    │                   Run WinRM     │
```

---

## Go Agent Deployment

**Added in Sessions 40-44 (2026-01-15 to 2026-01-16)**

Go Agents provide lightweight compliance monitoring for Windows workstations, replacing WinRM polling for scale.

### Architecture

```
Windows Workstation          NixOS Appliance           Central Command
       │                           │                         │
       │ gRPC :50051               │                         │
       │ ──────────────────────────>                         │
       │     Drift events          │                         │
       │                           │  HTTPS                  │
       │                           │ ──────────────────────────>
       │                           │   Evidence bundles      │
```

### Deployment Methods

1. **WinRM Push (Planned)**
   - Appliance deploys `osiris-agent.exe` via WinRM
   - Creates Windows service for persistence
   - Configures `config.json` with appliance endpoint

2. **Manual Installation**
   ```powershell
   # Copy binary
   mkdir C:\OsirisCare
   Copy-Item osiris-agent.exe C:\OsirisCare\

   # Create config
   @"
   {
     "appliance_addr": "192.168.88.247:50051",
     "data_dir": "C:\\ProgramData\\OsirisCare"
   }
   "@ | Out-File C:\ProgramData\OsirisCare\config.json -Encoding UTF8

   # Run agent
   C:\OsirisCare\osiris-agent.exe
   ```

3. **Group Policy Deployment**
   - Deploy binary to `C:\OsirisCare\osiris-agent.exe`
   - Create scheduled task to run on startup
   - Configure with appliance gRPC endpoint

### Configuration File

**Location:** `C:\ProgramData\OsirisCare\config.json`

```json
{
  "appliance_addr": "192.168.88.247:50051",
  "data_dir": "C:\\ProgramData\\OsirisCare"
}
```

### Compliance Checks

Go Agents perform 6 HIPAA compliance checks via WMI:

| Check | Description | Status on Compliant |
|-------|-------------|---------------------|
| BitLocker | Volume encryption | Encrypted |
| Defender | Real-time protection | Enabled |
| Firewall | All profiles enabled | All 3 enabled |
| Patches | Recent Windows Updates | Within 30 days |
| ScreenLock | Timeout configuration | ≤ 600 seconds |
| RMM | Third-party RMM detection | None detected |

### Current Limitations

1. **gRPC Streaming:** Not yet implemented (stub methods)
2. **SQLite Queue:** Requires CGO_ENABLED=1 (currently disabled)
3. **Auto-Deploy:** Module planned but not yet implemented

### Firewall Requirements

**Appliance (NixOS):**
- Port 50051 TCP inbound (gRPC)
- Configured in ISO v37+

**Workstation (Windows):**
- Port 50051 TCP outbound to appliance

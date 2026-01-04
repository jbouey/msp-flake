# Appliance Provisioning Module

**Last Updated:** 2026-01-04 (Session 8)

## Locations

| Component | Path |
|-----------|------|
| Agent-side module | `packages/compliance-agent/src/compliance_agent/provisioning.py` |
| Backend API | `mcp-server/central-command/backend/provisioning.py` |

## Overview

The provisioning module handles first-boot appliance setup via QR code or manual provision code entry. When an appliance boots without a `config.yaml` file, it enters provisioning mode.

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

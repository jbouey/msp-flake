# OsirisCare Go Agent

Lightweight Windows workstation compliance agent that reports drift events to the NixOS compliance appliance.

## Overview

The Go agent solves the scalability problem of polling 25-50 workstations per site via WinRM. Instead of polling, each workstation runs this agent which:

1. **Monitors compliance** - Runs 6 checks every 5 minutes
2. **Pushes drift events** - Reports failures to appliance via gRPC
3. **Detects RMM** - Strategic intelligence for MSP displacement
4. **Works offline** - SQLite queue for network resilience

## Compliance Checks

| Check | HIPAA Control | Description |
|-------|---------------|-------------|
| `bitlocker` | §164.312(a)(2)(iv) | BitLocker encryption enabled on C: |
| `defender` | §164.308(a)(5)(ii)(B) | Windows Defender running, signatures current |
| `patches` | §164.308(a)(1)(ii)(B) | Windows Update patches current |
| `firewall` | §164.312(e)(1) | Windows Firewall enabled on all profiles |
| `screenlock` | §164.312(a)(2)(i) | Screen lock policy configured |
| `rmm_detection` | N/A | Detects competing RMM tools |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Windows Workstation                       │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              OsirisCare Go Agent                     │   │
│  │  - 6 compliance checks via WMI                       │   │
│  │  - SQLite offline queue                              │   │
│  │  - gRPC streaming to appliance                       │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
                    gRPC (port 50051) / mTLS
                              │
┌─────────────────────────────┴─────────────────────────────┐
│                    NixOS Compliance Appliance              │
│  ┌────────────────────┐  ┌────────────────────────────┐   │
│  │   gRPC Server      │  │   Existing Sensor API      │   │
│  │   (grpc_server.py) │  │   (sensor_api.py)          │   │
│  │   Port 50051       │  │   Port 8080                │   │
│  └─────────┬──────────┘  └─────────────┬──────────────┘   │
│            │                           │                   │
│            └───────────┬───────────────┘                   │
│                        │                                   │
│  ┌─────────────────────▼───────────────────────────────┐  │
│  │              Three-Tier Healing Engine               │  │
│  │  L1 Deterministic → L2 LLM → L3 Human Escalation   │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## Capability Tiers

The appliance controls agent capabilities server-side:

| Tier | Value | Description | Use Case |
|------|-------|-------------|----------|
| MONITOR_ONLY | 0 | Just reports drift | MSP-deployed (default) |
| SELF_HEAL | 1 | Can fix drift locally | Direct clients (opt-in) |
| FULL_REMEDIATION | 2 | Full automation | Trusted environments |

MSPs never see that agents could do more - this is strategic positioning for displacement.

## Building

### Prerequisites

- Go 1.22+
- mingw-w64 (for cross-compiling to Windows with CGO)
- Nix (optional, for reproducible builds)

### Build Commands

```bash
# Development (dry-run mode)
make run-dry

# Build for Windows (requires mingw-w64)
make build-windows

# Build for Windows without CGO (limited WMI)
make build-windows-nocgo

# Build via Nix (reproducible)
nix build .#osiris-agent-windows-amd64
```

## Installation

1. Copy `osiris-agent.exe` to `C:\OsirisCare\`
2. Create `C:\OsirisCare\config.json`:

```json
{
  "appliance_addr": "192.168.88.246:50051"
}
```

3. Install as Windows service (optional):

```powershell
sc.exe create OsirisAgent binPath= "C:\OsirisCare\osiris-agent.exe" start= auto
sc.exe start OsirisAgent
```

## Configuration

| Setting | Description | Default |
|---------|-------------|---------|
| `appliance_addr` | gRPC address of appliance | (required) |
| `data_dir` | Local data directory | `C:\ProgramData\OsirisCare` |
| `cert_file` | Client TLS certificate | `{data_dir}\agent.crt` |
| `key_file` | Client TLS key | `{data_dir}\agent.key` |
| `ca_file` | CA certificate | `{data_dir}\ca.crt` |

## RMM Detection

The agent detects these RMM products:

- Datto RMM
- ConnectWise Automate
- ConnectWise Control
- NinjaRMM
- Kaseya VSA
- Syncro
- Atera
- N-able N-central
- Pulseway
- TeamViewer

This intelligence feeds the MSP displacement dashboard.

## Files

```
C:\OsirisCare\
├── osiris-agent.exe       # Agent binary
├── config.json            # Configuration
├── agent.crt              # Client certificate (optional)
├── agent.key              # Client private key (optional)
├── ca.crt                 # CA certificate (optional)
├── offline_queue.db       # SQLite offline queue
├── agent.log              # Log file
└── status.json            # Current status
```

## Offline Operation

When the appliance is unreachable:

1. Events queue to SQLite database
2. Queue persists across agent restarts
3. Events drain when connection restored
4. Old events (>7 days) are pruned

## Development

```bash
# Enter development shell
nix develop

# Run tests
make test

# Format code
make fmt

# Run vet
make vet
```

## License

Proprietary - OsirisCare Platform

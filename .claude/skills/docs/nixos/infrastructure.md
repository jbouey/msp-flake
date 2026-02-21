# Infrastructure Patterns

## NixOS Architecture

### Flake Structure
```
flake.nix                        # Root orchestrator
├── flake-compliance.nix         # Compliance agent packaging
├── iso/
│   ├── appliance-image.nix      # Bootable ISO builder
│   ├── appliance-disk-image.nix # Installed system config (nixos-rebuild target)
│   └── configuration.nix        # Base system config
├── appliance/                   # Go appliance daemon module
│   ├── cmd/                     # 3 binaries: appliance-daemon, checkin-receiver, grpc-server
│   ├── internal/                # 10 packages: ca, checkin, daemon, discovery, grpcserver,
│   │                            #   healing, l2bridge, orders, sshexec, winrm
│   └── go.mod                   # github.com/osiriscare/appliance
└── flake/Modules/
    ├── compliance-agent.nix     # Main service module
    ├── secrets.nix              # SOPS/age secrets
    ├── timesync.nix             # NTP configuration
    └── ssh-hardening.nix        # SSH security
```

### Module Pattern
```nix
{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.services.compliance-agent;
in {
  options.services.compliance-agent = {
    enable = mkEnableOption "compliance agent";

    siteId = mkOption {
      type = types.str;
      description = "Site identifier";
    };

    mcpUrl = mkOption {
      type = types.str;
      default = "https://api.osiriscare.net";
    };

    pollInterval = mkOption {
      type = types.int;
      default = 60;
    };
  };

  config = mkIf cfg.enable {
    systemd.services.compliance-agent = {
      description = "MSP Compliance Agent";
      after = [ "network-online.target" ];
      wantedBy = [ "multi-user.target" ];

      serviceConfig = {
        ExecStart = "${pkgs.compliance-agent}/bin/compliance-agent";
        Restart = "always";
        # Hardening
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        NoNewPrivileges = true;
      };

      environment = {
        SITE_ID = cfg.siteId;
        MCP_URL = cfg.mcpUrl;
        POLL_INTERVAL = toString cfg.pollInterval;
      };
    };
  };
}
```

## Partition Layout

### Actual Disk Layout (msp-auto-install)
```
/dev/sda1 → ESP (512MiB, FAT32)
/dev/sda2 → MSP-DATA (2GB, ext4, persistent data at /var/lib/msp)
/dev/sda3 → NixOS root (remaining disk)
```

### Update System
Updates use `nixos-rebuild test` (activate without persisting) + watchdog verification:
```
1. Central Command inserts nixos_rebuild order
2. Agent runs nixos-rebuild test via systemd-run
3. New config activates, agent restarts
4. Agent verifies health, writes .rebuild-verified
5. Watchdog timer persists with nixos-rebuild switch
6. If agent fails to verify within 10min, watchdog rolls back
```

### State Files
```
/var/lib/msp/.rebuild-in-progress   # Marker with timestamp + prev system + flake ref
/var/lib/msp/.rebuild-verified      # Agent health verification flag
```

## Docker Deployment

### Compose Stack
```yaml
version: '3.8'
services:
  mcp-server:
    build: ./mcp-server
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://mcp:password@db:5432/mcp
      - REDIS_URL=redis://redis:6379
    depends_on:
      - db
      - redis
    healthcheck:
      test: curl -f http://localhost:8000/health
      interval: 30s

  redis:
    image: redis:alpine
    ports:
      - "6379:6379"

  prometheus:
    image: prom/prometheus
    ports:
      - "9091:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml

  grafana:
    image: grafana/grafana
    ports:
      - "3000:3000"
```

### Dockerfile Pattern
```dockerfile
FROM python:3.11-slim

# System deps for WeasyPrint (PDF generation)
RUN apt-get update && apt-get install -y \
    libpango-1.0-0 libcairo2 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

HEALTHCHECK --interval=30s --timeout=10s \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## Secrets Management (SOPS/age)

### Configuration
```nix
services.msp-secrets = {
  enable = true;
  sopsFile = /etc/secrets/secrets.yaml;
  ageKeyFile = /var/lib/sops-age/keys.txt;

  secrets = {
    mcp-api-key = {
      sopsKey = "mcp/api_key";
      owner = "compliance-agent";
      mode = "0400";
      restartUnits = [ "compliance-agent.service" ];
    };

    openai-key = {
      sopsKey = "llm/openai_api_key";
      owner = "mcp-server";
      mode = "0400";
    };
  };
};
```

### Secrets File (encrypted)
```yaml
# secrets.yaml (encrypted with age)
mcp:
    api_key: ENC[AES256_GCM,data:...,type:str]
llm:
    openai_api_key: ENC[AES256_GCM,data:...,type:str]
sops:
    age:
        - recipient: age1...
```

## systemd Hardening

```nix
serviceConfig = {
  # Filesystem protection
  ProtectSystem = "strict";         # /usr, /boot read-only
  ProtectHome = true;               # No home access
  PrivateTmp = true;                # Isolated /tmp
  ReadWritePaths = [ "/var/lib/msp" ];

  # Privilege restriction
  NoNewPrivileges = true;
  CapabilityBoundingSet = "";

  # System call filtering
  SystemCallFilter = [ "@system-service" ];
  SystemCallArchitectures = "native";

  # Namespace isolation
  RestrictNamespaces = true;
  LockPersonality = true;
  PrivateDevices = true;
  PrivateUsers = true;
};
```

## Appliance Port Layout

```
Port   Service                 Bind        Firewall
22     SSH                     0.0.0.0     open
80     nginx                   0.0.0.0     open
8080   web-ui                  0.0.0.0     open
8081   scanner-web             127.0.0.1   closed
8082   scanner-api             127.0.0.1   closed
8083   go-agent-checkins       0.0.0.0     closed (agent-only)
8084   local-portal            0.0.0.0     open
50051  gRPC (compliance)       0.0.0.0     open
```

## NixOS /etc Gotchas

- NixOS manages `/etc` files as symlinks to the read-only nix store
- Scripts that write to `/etc/issue`, `/etc/motd`, etc. must `rm -f` the symlink first
- `systemctl restart` inside activation-triggered oneshot services deadlocks (use `--no-block`)
- Writing to `/dev/tty1` blocks when no console attached (use `timeout 2 bash -c '...'`)

## Network Configuration (nftables)

```nix
# Pull-only firewall
networking.nftables = {
  enable = true;
  ruleset = ''
    table inet filter {
      chain input {
        type filter hook input priority 0; policy drop;

        # Allow established
        ct state established,related accept

        # Allow localhost
        iif lo accept

        # SSH, status page, sensor API, local-portal, gRPC
        tcp dport { 22, 80, 8080, 8084, 50051 } accept
      }

      chain output {
        type filter hook output priority 0; policy drop;

        ct state established,related accept

        # DNS, NTP, HTTPS
        udp dport { 53, 123 } accept
        tcp dport { 53, 443 } accept

        # WinRM for healing
        tcp dport { 5985, 5986 } accept
      }
    }
  '';
};
```

## ISO Building

### Build Command
```bash
# Build appliance ISO
nix build .#appliance-iso

# Output: result/iso/osiriscare-appliance.iso
```

### Image Specifications
- **Target:** HP T640 Thin Client
- **RAM:** ~300MB system usage
- **Boot:** EFI + USB bootable
- **Compression:** zstd level 19
- **Size:** ~1.1GB

## Agent Overlay System

Hot-patch agent code without full NixOS rebuild. Tarball extracted to `/var/lib/msp/agent-overlay/`.

**Critical:** Tarball must preserve Python package structure:
```
agent-overlay/
  VERSION                           # Overlay version file (triggers _apply_overlay)
  compliance_agent/                 # REQUIRED subdirectory for PYTHONPATH imports
    __init__.py
    appliance_agent.py
    auto_healer.py
    ...
```

NixOS wrapper does `from compliance_agent.appliance_agent import main`. Systemd drop-in sets `PYTHONPATH=/var/lib/msp/agent-overlay` so overlay modules are found before NixOS store modules.

**Build:** `mkdir -p /tmp/overlay-build/compliance_agent && cp -R src/compliance_agent/* /tmp/overlay-build/compliance_agent/ && cp VERSION /tmp/overlay-build/`

## Go Daemon Feature Flag

The Go appliance daemon runs alongside the Python agent, toggled by a file-based feature flag:

```
/var/lib/msp/.use-go-daemon    # Touch to enable Go daemon, remove for Python
```

Both services use systemd `ConditionPathExists` for mutual exclusion:
- Python `compliance-agent.service`: `ConditionPathExists = !/var/lib/msp/.use-go-daemon`
- Go `appliance-daemon.service`: `ConditionPathExists = /var/lib/msp/.use-go-daemon`

The Go daemon (`appliance-daemon-go`) is built via `pkgs.buildGoModule` in both `appliance-image.nix` and `appliance-disk-image.nix`. It includes 3 subpackages: `appliance-daemon`, `checkin-receiver`, `grpc-server`.

**Production status:** Go daemon v0.2.1 deployed to physical HP T640. Evidence submission live (Ed25519 signed, 7 check types). Memory: 5.6MB (vs Python 112MB, 17x reduction). Checkin cycle: ~50ms. 82 L1 rules loaded (38 builtin + 44 synced). Go 1.22 compatible (NixOS 24.05).

### Checkin Auth Flow
The `checkin-receiver` (Go, runs on VPS port 8001 in Docker) validates auth via:
1. Static `--auth-token` flag (shared fallback)
2. Per-site API key from `appliance_provisioning` table

Either match = authenticated. Caddy routes `/api/appliances/checkin` to this container.

### Appliance ID Format
Canonical format: `{site_id}-{MAC_COLON_SEPARATED}` (e.g., `physical-appliance-pilot-1aea78-84:3A:5B:91:B6:61`). Defined in `checkin/models.go:CanonicalApplianceID()`. All code (Go checkin, Python checkin, Python provisioning) must use this format.

### Rebuild Command for Deployed Appliances
```bash
nixos-rebuild switch --flake github:jbouey/msp-flake/main#osiriscare-appliance-disk --refresh
```
The `#osiriscare-appliance-disk` target must be specified explicitly — hostname auto-detection won't work because first-boot changes the hostname.

## Auto-Deploy (AD Workstation Agent Distribution)

The Go daemon includes an auto-deploy subsystem that spreads the compliance agent to all Active Directory workstations without manual intervention.

**File:** `appliance/internal/daemon/autodeploy.go` (~900 lines)

### Fallback Chain
```
1. Direct WinRM NTLM  → Push agent binary + install via WinRM from appliance
2. DC Proxy (Kerberos) → Invoke-Command via DC using Kerberos delegation
3. Retry next cycle    → Skip host, retry on next deploy interval
```

### Key Components
- **AD Enumeration:** LDAP query for computer objects, filter to workstations
- **Binary Distribution:** NETLOGON share (`\\DC\NETLOGON\osiris-agent\`) — universal AD share, no extra infra
- **Auth Fixup:** SPN registration (`setspn -A HTTP/<dc-hostname>`) + TrustedHosts for WinRM Kerberos
- **GPO Configuration:** WinRM enabled via Default Domain Policy for all future workstations
- **Concurrency Guard:** Atomic CAS prevents overlapping deploy cycles (no mutex needed)
- **Auth Mode:** Negotiate (Kerberos preferred, NTLM fallback) — see `winrm/executor.go` for ClientNTLM

### Integration
- Started by `daemon.go` as part of the daemon lifecycle
- Uses `config.go` `GRPCListenAddr()` for agent registration endpoint
- Deploy cycle runs on a timer; skips if previous cycle still running (atomic flag)

## Key Files
- `flake.nix` - Root flake
- `iso/appliance-image.nix` - ISO builder
- `iso/appliance-disk-image.nix` - Installed system (nixos-rebuild target)
- `modules/compliance-agent.nix` - Service definition
- `flake/Modules/secrets.nix` - SOPS configuration
- `docker-compose.yml` - Container stack
- `scripts/deploy-vbox-vms.sh` - VM deployment
- `appliance/` - Go appliance daemon module (150 tests, 10 packages)

# Infrastructure Patterns

## NixOS Architecture

### Flake Structure
```
flake.nix                    # Root orchestrator
├── flake-compliance.nix     # Compliance agent packaging
├── iso/
│   ├── appliance-image.nix  # Bootable ISO builder
│   └── configuration.nix    # Base system config
└── flake/Modules/
    ├── compliance-agent.nix # Main service module
    ├── secrets.nix          # SOPS/age secrets
    ├── timesync.nix         # NTP configuration
    └── ssh-hardening.nix    # SSH security
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

## A/B Partition Update System

### Partition Layout
```
/dev/sda1 → ESP (EFI System Partition)
/dev/sda2 → Partition A (NixOS root - 10GB)
/dev/sda3 → Partition B (NixOS root - 10GB)
/dev/sda4 → MSP-DATA (ext4 - persistent data)
```

### Update Flow
```
1. Detect active partition (kernel cmdline → ab_state → mount)
2. Download ISO to standby partition
3. SHA256 verification
4. Set next boot target
5. Reboot → Health Gate (max 3 attempts)
   ├─ Success → Commit
   └─ Failure → Rollback
```

### Health Gate Service
```nix
systemd.services.msp-health-gate = {
  description = "Post-boot health verification";
  after = [ "network-online.target" ];
  before = [ "compliance-agent.service" ];
  wantedBy = [ "multi-user.target" ];

  serviceConfig = {
    Type = "oneshot";
    ExecStart = "${pkgs.compliance-agent}/bin/health-gate --verify";
    RemainAfterExit = true;
  };
};
```

### State Files
```
/boot/ab_state              # GRUB source format: set active_partition="A"
/var/lib/msp/update/
├── update_state.json       # Pending update metadata
└── boot_count              # Boot attempt counter
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

        # SSH, status page, sensor API, gRPC
        tcp dport { 22, 80, 8080, 50051 } accept
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

## Key Files
- `flake.nix` - Root flake
- `iso/appliance-image.nix` - ISO builder
- `modules/compliance-agent.nix` - Service definition
- `flake/Modules/secrets.nix` - SOPS configuration
- `docker-compose.yml` - Container stack
- `scripts/deploy-vbox-vms.sh` - VM deployment

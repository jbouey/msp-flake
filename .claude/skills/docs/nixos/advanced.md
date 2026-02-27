# NixOS Advanced Patterns

## Nix Language Internals

### Fixed-Point Evaluation (lib.fix)
```nix
# lib.fix applies a function to its own result (used internally by overlay system)
lib.fix (self: {
  a = 1;
  b = self.a + 1;  # self refers to the final result
})
# → { a = 1; b = 2; }

# This is how overlays compose — each overlay receives (final: prev:)
# where final is the fixed-point of all overlays combined
```

### Override Hierarchy
```nix
# .override — change function arguments (inputs)
pkg.override { enableFeature = true; }

# .overrideAttrs — change derivation attributes (outputs)
pkg.overrideAttrs (old: {
  patches = old.patches ++ [ ./my-fix.patch ];
  buildInputs = old.buildInputs ++ [ pkgs.openssl ];
})

# .overrideDerivation — low-level, avoid (uses old mkDerivation)
# Order: .override runs first, then .overrideAttrs
```

### builtins.genericClosure
```nix
# Transitive closure — find all dependencies recursively
builtins.genericClosure {
  startSet = [{ key = "a"; deps = ["b" "c"]; }];
  operator = item:
    map (dep: { key = dep; deps = depsOf dep; }) item.deps;
}
# Returns all reachable items with unique keys
```

## Module System Deep Dive

### Priority System
```nix
# Lower number = higher priority
lib.mkForce value         # priority 50 (wins almost always)
value                     # priority 100 (default)
lib.mkDefault value       # priority 1000 (loses to anything explicit)
lib.mkOverride 90 value   # custom priority

# Example: override a module's default
services.openssh.settings.PermitRootLogin = lib.mkForce "no";

# Priority merging for lists:
networking.firewall.allowedTCPPorts = lib.mkBefore [ 22 ];  # prepend
networking.firewall.allowedTCPPorts = lib.mkAfter [ 8080 ]; # append
```

### types.submodule
```nix
options.sites = lib.mkOption {
  type = lib.types.attrsOf (lib.types.submodule {
    options = {
      domain = lib.mkOption { type = lib.types.str; };
      port = lib.mkOption { type = lib.types.port; default = 443; };
      enableSSL = lib.mkOption { type = lib.types.bool; default = true; };
    };
  });
  default = {};
};

# Usage:
sites = {
  main = { domain = "example.com"; };
  api  = { domain = "api.example.com"; port = 8080; };
};
```

### freeformType (open-ended options)
```nix
options.extraConfig = lib.mkOption {
  type = lib.types.submodule {
    freeformType = lib.types.attrsOf lib.types.str;
    options.required = lib.mkOption {
      type = lib.types.str;
      description = "This field is required and type-checked";
    };
  };
};
# Allows arbitrary string attrs plus enforces 'required' exists
```

### types.attrTag (tagged unions, NixOS 24.11+)
```nix
options.backend = lib.mkOption {
  type = lib.types.attrTag {
    postgres = lib.mkOption {
      type = lib.types.submodule {
        options.connStr = lib.mkOption { type = lib.types.str; };
      };
    };
    sqlite = lib.mkOption {
      type = lib.types.submodule {
        options.path = lib.mkOption { type = lib.types.str; };
      };
    };
  };
};
# Usage: backend.postgres = { connStr = "..."; };
# Or:    backend.sqlite = { path = "/var/lib/app/db.sqlite"; };
# Only one variant allowed at a time
```

### evalModules Outside NixOS
```nix
# Use the module system for your own configs
let
  result = lib.evalModules {
    modules = [
      { options.name = lib.mkOption { type = lib.types.str; }; }
      { config.name = "my-app"; }
    ];
  };
in result.config.name  # → "my-app"
```

## Flakes Advanced

### flake-parts
```nix
# flake.nix — modular flake structure
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-parts.url = "github:hercules-ci/flake-parts";
  };

  outputs = inputs@{ flake-parts, ... }:
    flake-parts.lib.mkFlake { inherit inputs; } {
      systems = [ "x86_64-linux" "aarch64-linux" ];

      perSystem = { pkgs, system, ... }: {
        packages.default = pkgs.hello;
        devShells.default = pkgs.mkShell { buildInputs = [ pkgs.go ]; };
      };

      flake = {
        nixosConfigurations.myhost = inputs.nixpkgs.lib.nixosSystem { /* ... */ };
      };
    };
}
```

### Input Follows (dependency dedup)
```nix
inputs = {
  nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  home-manager = {
    url = "github:nix-community/home-manager";
    inputs.nixpkgs.follows = "nixpkgs";  # share same nixpkgs
  };
  sops-nix = {
    url = "github:Mic92/sops-nix";
    inputs.nixpkgs.follows = "nixpkgs";
  };
};
# Without follows: each input brings its own nixpkgs copy → closure bloat
```

### Flake Checks
```nix
# Run: nix flake check
outputs = { self, nixpkgs, ... }: {
  checks.x86_64-linux = {
    build = self.packages.x86_64-linux.default;
    test = nixpkgs.legacyPackages.x86_64-linux.testers.runNixOSTest {
      name = "integration";
      nodes.machine = { /* ... */ };
      testScript = ''
        machine.wait_for_unit("my-service")
        machine.succeed("curl http://localhost:8080/health")
      '';
    };
  };
};
```

## Deployment

### deploy-rs (magic rollback)
```nix
# flake.nix
inputs.deploy-rs.url = "github:serokell/deploy-rs";

outputs = { self, nixpkgs, deploy-rs, ... }: {
  deploy.nodes.appliance = {
    hostname = "192.168.88.241";
    profiles.system = {
      user = "root";
      path = deploy-rs.lib.x86_64-linux.activate.nixos
        self.nixosConfigurations.appliance;
      # Magic rollback: if deploy-rs loses SSH connection after activation,
      # the system automatically rolls back after 30s
    };
  };
};
# Deploy: nix run github:serokell/deploy-rs -- .#appliance
```

### colmena (fleet management)
```nix
# flake.nix
outputs = { self, nixpkgs, ... }: {
  colmena = {
    meta = {
      nixpkgs = import nixpkgs { system = "x86_64-linux"; };
      specialArgs = { inherit self; };
    };

    defaults = { pkgs, ... }: {
      # Shared config for all nodes
      services.openssh.enable = true;
    };

    appliance-1 = { name, nodes, ... }: {
      deployment = {
        targetHost = "192.168.88.241";
        targetUser = "root";
        tags = [ "appliance" "production" ];
      };
      imports = [ ./hosts/appliance-1.nix ];
    };

    appliance-2 = { name, nodes, ... }: {
      deployment.targetHost = "192.168.88.242";
      imports = [ ./hosts/appliance-2.nix ];
    };
  };
};
# Deploy all:    colmena apply
# Deploy tagged: colmena apply --on @appliance
# Deploy one:    colmena apply --on appliance-1
# Parallel:      colmena apply --parallel 5
```

### nixos-anywhere (remote install)
```bash
# Install NixOS on a remote machine over SSH (from any Linux live env)
nix run github:nix-community/nixos-anywhere -- \
  --flake .#myhost \
  root@192.168.88.241 \
  --disk-encryption-keys /tmp/secret.key <(echo -n "passphrase")
# Supports disko for declarative disk layout
```

## Secrets Management

### sops-nix (comprehensive)
```nix
# flake.nix inputs
inputs.sops-nix.url = "github:Mic92/sops-nix";

# configuration.nix
sops = {
  defaultSopsFile = ./secrets/secrets.yaml;
  defaultSopsFormat = "yaml";

  age = {
    keyFile = "/var/lib/sops-nix/key.txt";
    generateKey = true;  # auto-generate age key if missing
    sshKeyPaths = [ "/etc/ssh/ssh_host_ed25519_key" ];  # derive from SSH host key
  };

  secrets = {
    "api_key" = {
      owner = "msp";
      group = "msp";
      mode = "0400";
      restartUnits = [ "appliance-daemon.service" ];  # auto-restart on change
      path = "/run/secrets/api_key";                   # default, but explicit
    };
    "db/password" = {
      # Nested YAML keys use /
      neededForUsers = true;  # available during user creation (stage 1)
    };
  };

  templates = {
    "db-config" = {
      content = ''
        DATABASE_URL=postgresql://mcp:${config.sops.placeholder."db/password"}@localhost/mcp
      '';
      path = "/run/secrets/rendered/db-config";
      owner = "msp";
    };
  };
};

# Create secrets file:
# sops --age $(cat /var/lib/sops-nix/key.txt | grep public | cut -d: -f2 | tr -d ' ') \
#   secrets/secrets.yaml
```

### systemd Credentials (NixOS 24.05+)
```nix
# Built-in, no extra flake input needed
systemd.services.my-service = {
  serviceConfig = {
    LoadCredential = [
      "api-key:/run/secrets/api_key"
    ];
    # Access in service as: $CREDENTIALS_DIRECTORY/api-key
  };
};
```

## Systemd Hardening (Comprehensive)

### Maximum Hardening Template
```nix
systemd.services.my-hardened-service = {
  serviceConfig = {
    # === Identity ===
    DynamicUser = false;  # true creates ephemeral user (conflicts with some mounts)
    User = "myservice";
    Group = "myservice";
    UMask = "0077";

    # === Filesystem ===
    ProtectSystem = "strict";
    ProtectHome = true;
    PrivateTmp = true;
    PrivateDevices = true;
    ProtectKernelTunables = true;
    ProtectKernelModules = true;
    ProtectKernelLogs = true;
    ProtectControlGroups = true;
    ProtectClock = true;
    ProtectHostname = true;
    ProtectProc = "invisible";
    ProcSubset = "pid";
    ReadWritePaths = [ "/var/lib/myservice" ];
    TemporaryFileSystem = "/:ro";         # overlay everything as ro
    BindPaths = [ "/var/lib/myservice" ];  # then selectively bind rw
    InaccessiblePaths = [ "/root" "/home" ];

    # === Capabilities ===
    NoNewPrivileges = true;
    CapabilityBoundingSet = "";
    AmbientCapabilities = "";

    # === Syscalls ===
    SystemCallFilter = [ "@system-service" "~@privileged" "~@resources" ];
    SystemCallArchitectures = "native";
    SystemCallErrorNumber = "EPERM";

    # === Memory ===
    MemoryDenyWriteExecute = true;

    # === Network ===
    RestrictAddressFamilies = [ "AF_INET" "AF_INET6" "AF_UNIX" ];
    # Or: PrivateNetwork = true;  # total isolation

    # === Namespace ===
    RestrictNamespaces = true;
    LockPersonality = true;
    RestrictRealtime = true;
    RestrictSUIDSGID = true;
    PrivateUsers = true;

    # === Device ===
    DevicePolicy = "closed";

    # === Directories (auto-created, auto-permissioned) ===
    RuntimeDirectory = "myservice";
    StateDirectory = "myservice";
    CacheDirectory = "myservice";
    LogsDirectory = "myservice";

    # === Resource limits ===
    LimitNOFILE = 1024;
    LimitNPROC = 64;
    MemoryMax = "512M";
    CPUQuota = "200%";  # 2 cores

    # === Crash loop protection ===
    Restart = "always";
    RestartSec = "5s";
    StartLimitIntervalSec = 300;
    StartLimitBurst = 5;

    # === Watchdog ===
    WatchdogSec = "120s";  # must sd_notify WATCHDOG=1 within this
    Type = "notify";        # requires READY=1 at startup
  };
};
```

### Audit Score
```bash
# Check hardening score (0-10, higher = more exposed)
systemd-analyze security appliance-daemon.service
# Target: score < 3.0 for production services
```

## Testing

### NixOS Integration Tests
```nix
# In flake checks or standalone
testers.runNixOSTest {
  name = "appliance-integration";

  nodes = {
    server = { pkgs, ... }: {
      services.postgresql.enable = true;
      networking.firewall.allowedTCPPorts = [ 5432 8080 ];
    };

    client = { pkgs, ... }: {
      environment.systemPackages = [ pkgs.curl ];
    };
  };

  testScript = ''
    server.start()
    server.wait_for_unit("postgresql")
    server.wait_for_open_port(5432)

    # Test from client
    client.start()
    client.wait_for_unit("network-online.target")
    client.succeed("curl -f http://server:8080/health")

    # Screenshot for debugging
    server.screenshot("server-running")

    # Check logs
    server.succeed("journalctl -u my-service --no-pager | grep 'started'")
  '';
};
```

### Interactive Test Debugging
```bash
# Drop into Python REPL mid-test
nix build .#checks.x86_64-linux.my-test --keep-going
# In testScript: import pdb; pdb.set_trace()
# Or: machine.shell_interact()  # interactive shell on the VM
```

## Impermanence

### tmpfs Root + Persistent State
```nix
# Boot config
fileSystems."/" = {
  device = "none";
  fsType = "tmpfs";
  options = [ "defaults" "size=2G" "mode=755" ];
};
fileSystems."/nix" = {
  device = "/dev/disk/by-label/nix";
  fsType = "ext4";
  neededForBoot = true;
};

# Persist only what matters
environment.persistence."/nix/persist" = {
  hideMounts = true;
  directories = [
    "/var/log"
    "/var/lib/nixos"
    "/var/lib/msp"
    "/var/lib/systemd"
    "/etc/ssh"     # host keys
    "/etc/msp"     # appliance config
  ];
  files = [
    "/etc/machine-id"
  ];
};
```

### Btrfs Snapshot Wipe (alternative to tmpfs)
```bash
# In initrd, delete root subvolume and recreate from blank snapshot
# Every boot starts clean except persisted paths
```

## Performance

### Binary Caches
```nix
nix.settings = {
  substituters = [
    "https://cache.nixos.org"
    "https://my-org.cachix.org"
  ];
  trusted-public-keys = [
    "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
    "my-org.cachix.org-1:XXXXX="
  ];
};

# Self-hosted cache with Attic
# attic push my-cache result  (after nix build)
```

### nix-fast-build (parallel evaluation)
```bash
# Parallel builds with Nix evaluation caching
nix run github:Mic92/nix-fast-build -- --flake .#checks
# Much faster than `nix flake check` for large flakes
```

### Store Optimization
```nix
nix.settings.auto-optimise-store = true;  # hard-link dedup
nix.gc = {
  automatic = true;
  dates = "weekly";
  options = "--delete-older-than 14d";
};
```

## Specialisations (A/B Configurations)
```nix
specialisation = {
  debug.configuration = {
    boot.kernelParams = [ "loglevel=7" ];
    services.openssh.settings.PermitRootLogin = "yes";
    environment.systemPackages = with pkgs; [ strace tcpdump gdb ];
  };
  minimal.configuration = {
    services.xserver.enable = lib.mkForce false;
    environment.systemPackages = lib.mkForce [ pkgs.vim ];
  };
};
# Switch at runtime: /run/current-system/specialisation/debug/bin/switch-to-configuration test
# Or select at boot via systemd-boot menu
```

## Containers & VMs

### systemd-nspawn (declarative)
```nix
containers.isolated-service = {
  autoStart = true;
  privateNetwork = true;
  hostAddress = "192.168.100.10";
  localAddress = "192.168.100.11";
  forwardPorts = [{ containerPort = 80; hostPort = 8080; protocol = "tcp"; }];
  config = { pkgs, ... }: {
    services.nginx.enable = true;
    system.stateVersion = "24.11";
  };
};
```

### Podman OCI
```nix
virtualisation.podman = {
  enable = true;
  dockerCompat = true;
  defaultNetwork.settings.dns_enabled = true;
};

virtualisation.oci-containers = {
  backend = "podman";
  containers.myapp = {
    image = "myapp:latest";
    ports = [ "8080:8080" ];
    volumes = [ "/var/lib/myapp:/data" ];
    environment = { NODE_ENV = "production"; };
  };
};
```

## Disko (Declarative Partitioning)
```nix
# Used with nixos-anywhere for automated installs
disko.devices.disk.main = {
  device = "/dev/sda";
  type = "disk";
  content = {
    type = "gpt";
    partitions = {
      ESP = {
        type = "EF00";
        size = "512M";
        content = { type = "filesystem"; format = "vfat"; mountpoint = "/boot"; };
      };
      root = {
        size = "100%";
        content = {
          type = "btrfs";
          extraArgs = [ "-f" ];
          subvolumes = {
            "/root"    = { mountpoint = "/"; mountOptions = [ "compress=zstd" "noatime" ]; };
            "/home"    = { mountpoint = "/home"; };
            "/nix"     = { mountpoint = "/nix"; mountOptions = [ "compress=zstd" "noatime" ]; };
            "/persist" = { mountpoint = "/persist"; };
          };
        };
      };
    };
  };
};
```

## Secure Boot (Lanzaboote)
```nix
inputs.lanzaboote.url = "github:nix-community/lanzaboote";

# In configuration:
boot.loader.systemd-boot.enable = lib.mkForce false;
boot.lanzaboote = {
  enable = true;
  pkiBundle = "/etc/secureboot";
};
# Setup: sbctl create-keys && sbctl enroll-keys --microsoft
```

## NixOS /etc Gotchas (Project-Specific)

```
PROBLEM                              SOLUTION
──────────────────────────────────   ──────────────────────────────────────
/etc files are read-only symlinks    rm -f symlink first, or use environment.etc
sed -i /etc/ssh/sshd_config fails    Configure via services.openssh.settings
cat > /etc/sysctl.d/ fails           Use boot.kernel.sysctl or sysctl -w
systemctl restart in activation      Use systemctl --no-block restart
Writing to /dev/tty1 blocks          Use timeout 2 bash -c '...'
/nix/store has SUID binaries         Skip in scanner: case "$f" in /nix/store/*) continue
```

## Key Files (Project-Specific)
```
flake.nix                           — Root flake orchestrator
iso/configuration.nix               — Base system config (sysctl, audit, users)
iso/appliance-image.nix             — Bootable installer ISO
iso/appliance-disk-image.nix        — Installed system (rebuild target)
flake/Modules/compliance-agent.nix  — Main service module definition
flake/Modules/secrets.nix           — SOPS/age secrets module
flake/Modules/ssh-hardening.nix     — SSH security module
```

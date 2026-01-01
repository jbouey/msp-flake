# iso/appliance-image.nix
# Builds bootable USB image for MSP Compliance Appliance
# Target: HP T640 Thin Client (4-8GB RAM, 32-64GB SSD)
# RAM Budget: ~300MB total

{ config, pkgs, lib, ... }:

{
  imports = [
    <nixpkgs/nixos/modules/installer/cd-dvd/installation-cd-minimal.nix>
    ./configuration.nix
    ./local-status.nix
    ../modules/compliance-agent.nix
  ];

  # System identification
  networking.hostName = lib.mkDefault "osiriscare-appliance";
  system.stateVersion = "24.05";

  # Minimal boot - fast startup
  boot.kernelParams = [ "console=tty1" "quiet" ];
  boot.loader.timeout = 3;

  # No GUI - headless operation
  services.xserver.enable = false;

  # Auto-login to console (for debugging if needed)
  services.getty.autologinUser = "root";

  # ============================================================================
  # Compliance Agent Configuration - LEAN MODE
  # Uses existing module but configured for minimal resource usage
  # ============================================================================
  services.compliance-agent = {
    enable = true;

    # Site ID loaded from config file at runtime
    siteId = lib.mkDefault "unconfigured";

    # LEAN MODE: No local MCP or Redis (runs on VPS)
    mcpServer.enable = false;
    redis.enable = false;

    # Connect to Central Command
    mcpUrl = lib.mkDefault "https://api.osiriscare.net";
    allowedHosts = [
      "api.osiriscare.net"
      "portal.osiriscare.net"
    ];

    # Direct deployment (not reseller)
    deploymentMode = lib.mkDefault "direct";

    # Timing
    pollInterval = 60;           # Phone home every 60 seconds
    orderTtl = 900;              # 15-minute order TTL
    maintenanceWindow = "02:00-05:00";  # 2-5 AM for disruptive actions

    # Evidence - keep 7 days locally
    evidenceRetention = 168;     # ~7 days at hourly bundles
    pruneRetentionDays = 7;

    # Logging - INFO level to reduce disk I/O
    logLevel = "INFO";

    # Web UI disabled - using nginx status page instead
    webUI.enable = false;
  };

  # ============================================================================
  # Minimal packages - only what the appliance needs
  # ============================================================================
  environment.systemPackages = with pkgs; [
    # Essentials
    vim
    curl
    htop

    # Network diagnostics
    iproute2
    iputils
    dnsutils

    # For WinRM to Windows servers
    python311
    python311Packages.pywinrm
    python311Packages.aiohttp
    python311Packages.cryptography
    python311Packages.pydantic
    python311Packages.pyyaml

    # Config management
    jq
    yq
  ];

  # ============================================================================
  # Networking - Pull-only architecture
  # ============================================================================
  networking = {
    useDHCP = true;
    firewall = {
      enable = true;
      allowedTCPPorts = [ 80 22 ];  # Status page + SSH
      # No other inbound - pull-only architecture
    };
  };

  # ============================================================================
  # Time sync - CRITICAL for compliance timestamps
  # ============================================================================
  services.chrony = {
    enable = true;
    servers = [ "time.nist.gov" "pool.ntp.org" ];
  };

  # ============================================================================
  # Persistent storage for config and evidence
  # ============================================================================
  fileSystems."/var/lib/msp" = {
    device = "/dev/disk/by-label/MSP-DATA";
    fsType = "ext4";
    options = [ "defaults" "noatime" ];
    neededForBoot = false;
  };

  # Create directories on activation
  system.activationScripts.mspDirs = ''
    mkdir -p /var/lib/msp/evidence
    mkdir -p /var/lib/msp/queue
    mkdir -p /etc/msp/certs
    chmod 700 /var/lib/msp /etc/msp/certs
  '';

  # ============================================================================
  # SSH for emergency access only
  # ============================================================================
  services.openssh = {
    enable = true;
    settings = {
      PermitRootLogin = "prohibit-password";
      PasswordAuthentication = false;
      KbdInteractiveAuthentication = false;
    };
  };

  # ============================================================================
  # Reduce image size - disable unnecessary features
  # ============================================================================
  documentation.enable = false;
  documentation.man.enable = false;
  documentation.nixos.enable = false;
  programs.command-not-found.enable = false;

  # Compress the squashfs image
  isoImage.squashfsCompression = "zstd -Xcompression-level 19";
  isoImage.isoName = lib.mkForce "osiriscare-appliance.iso";
  isoImage.makeEfiBootable = true;
  isoImage.makeUsbBootable = true;

  # ============================================================================
  # First-boot setup script
  # ============================================================================
  systemd.services.msp-first-boot = {
    description = "MSP Appliance First Boot Setup";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];

    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };

    script = ''
      MARKER="/var/lib/msp/.initialized"

      if [ -f "$MARKER" ]; then
        echo "Appliance already initialized"
        exit 0
      fi

      echo "=== MSP Compliance Appliance First Boot Setup ==="
      echo "Timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

      # Check for config file
      if [ -f /var/lib/msp/config.yaml ]; then
        echo "Found configuration file"
        SITE_ID=$(${pkgs.yq}/bin/yq -r '.site_id' /var/lib/msp/config.yaml)
        echo "Site ID: $SITE_ID"

        # Update hostname to match site ID
        if [ -n "$SITE_ID" ] && [ "$SITE_ID" != "null" ]; then
          hostnamectl set-hostname "$SITE_ID"
          echo "Hostname set to: $SITE_ID"
        fi
      else
        echo "WARNING: No config.yaml found at /var/lib/msp/config.yaml"
        echo "Appliance will run in unconfigured mode"
      fi

      # Check for certificates
      if [ -d /etc/msp/certs ] && [ -f /etc/msp/certs/client.crt ]; then
        echo "mTLS certificates found"
      else
        echo "WARNING: No mTLS certificates found"
      fi

      # Mark as initialized
      touch "$MARKER"
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) - First boot complete" >> /var/lib/msp/setup.log

      echo "=== First Boot Setup Complete ==="
    '';
  };
}

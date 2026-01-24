# iso/appliance-image.nix
# Builds bootable USB image for MSP Compliance Appliance
# Target: HP T640 Thin Client (4-8GB RAM, 32-64GB SSD)
# RAM Budget: ~300MB total

{ config, pkgs, lib, ... }:

let
  # Build the compliance-agent package
  compliance-agent = pkgs.python311Packages.buildPythonApplication {
    pname = "compliance-agent";
    version = "1.0.48";  # Session 69 - Network Scanner & Local Portal
    src = ../packages/compliance-agent;

    propagatedBuildInputs = with pkgs.python311Packages; [
      aiohttp
      asyncssh
      cryptography
      pydantic
      pydantic-settings
      fastapi
      uvicorn
      jinja2
      pywinrm
      pyyaml
      grpcio
      grpcio-tools
    ];

    doCheck = false;
  };

  # Build the network-scanner package (EYES)
  network-scanner = pkgs.python311Packages.buildPythonApplication {
    pname = "network-scanner";
    version = "0.1.0";  # Session 69 - Device discovery
    src = ../packages/network-scanner;

    propagatedBuildInputs = with pkgs.python311Packages; [
      aiohttp
      pydantic
      pyyaml
      python-nmap
      ldap3
    ];

    doCheck = false;
  };

  # Build the local-portal package (WINDOW)
  local-portal = pkgs.python311Packages.buildPythonApplication {
    pname = "local-portal";
    version = "0.1.0";  # Session 69 - Device transparency UI
    src = ../packages/local-portal;

    propagatedBuildInputs = with pkgs.python311Packages; [
      fastapi
      uvicorn
      aiohttp
      pydantic
      python-multipart
      reportlab
    ];

    doCheck = false;
  };

  # Build the local-portal frontend
  local-portal-frontend = pkgs.buildNpmPackage {
    pname = "local-portal-frontend";
    version = "0.1.0";
    src = ../packages/local-portal/frontend;
    npmDepsHash = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";  # TODO: Update after first build
    buildPhase = "npm run build";
    installPhase = ''
      mkdir -p $out
      cp -r dist/* $out/
    '';
  };
in
{
  # Note: installation-cd-minimal.nix is imported from the flake, not here
  # This allows pure flake evaluation
  imports = [
    ./configuration.nix
    ./local-status.nix
  ];

  # System identification
  networking.hostName = lib.mkDefault "osiriscare-appliance";
  system.stateVersion = "24.05";

  # Minimal boot - fast startup
  boot.kernelParams = [ "console=tty1" "quiet" ];
  boot.loader.timeout = lib.mkForce 3;

  # No GUI - headless operation
  services.xserver.enable = false;

  # Auto-login to console (for debugging if needed)
  services.getty.autologinUser = lib.mkForce "root";

  # Show IP address on login
  environment.etc."motd".text = ''

    ╔═══════════════════════════════════════════════════════════╗
    ║           OsirisCare Compliance Appliance                 ║
    ╚═══════════════════════════════════════════════════════════╝

    Access via: ssh root@osiriscare-appliance.local
    Status page: http://osiriscare-appliance.local
    Local Portal: http://osiriscare-appliance.local:8083

    Run 'ip addr' to see IP addresses
    Run 'journalctl -u compliance-agent -f' to watch agent
    Run 'journalctl -u network-scanner -f' to watch scanner

  '';

  # ============================================================================
  # Health Gate Service (A/B Update Verification)
  # ============================================================================

  # Health gate runs at boot to verify system health after updates
  # If health checks fail repeatedly, triggers automatic rollback
  systemd.services.msp-health-gate = {
    description = "MSP Boot Health Gate";
    wantedBy = [ "multi-user.target" ];
    before = [ "compliance-agent.service" ];
    after = [ "network-online.target" "local-fs.target" "msp-auto-provision.service" ];
    wants = [ "network-online.target" ];

    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = "${compliance-agent}/bin/health-gate";
      TimeoutStartSec = "90s";

      # Working directory for config access
      WorkingDirectory = "/var/lib/msp";

      # Logging
      StandardOutput = "journal";
      StandardError = "journal";
      SyslogIdentifier = "msp-health-gate";
    };
  };

  # ============================================================================
  # Full Compliance Agent
  # ============================================================================

  # Compliance agent systemd service
  systemd.services.compliance-agent = {
    description = "OsirisCare Compliance Agent";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" "msp-auto-provision.service" "msp-health-gate.service" ];
    wants = [ "network-online.target" ];

    serviceConfig = {
      Type = "simple";
      ExecStart = "${compliance-agent}/bin/compliance-agent-appliance";
      Restart = "always";
      RestartSec = "10s";

      # Working directory for config
      WorkingDirectory = "/var/lib/msp";

      # Logging
      StandardOutput = "journal";
      StandardError = "journal";
      SyslogIdentifier = "compliance-agent";

      # Security hardening
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      ReadWritePaths = [ "/var/lib/msp" ];
      NoNewPrivileges = true;
    };

    # Enable active healing (not dry-run) for learning data collection
    environment = {
      HEALING_DRY_RUN = "false";
    };
  };

  # ============================================================================
  # Network Scanner Service (EYES) - Device Discovery
  # ============================================================================
  systemd.services.network-scanner = {
    description = "MSP Network Scanner (EYES) - Device Discovery";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" "msp-auto-provision.service" ];
    wants = [ "network-online.target" ];

    serviceConfig = {
      Type = "simple";
      ExecStart = "${network-scanner}/bin/network-scanner";
      Restart = "always";
      RestartSec = "30s";

      WorkingDirectory = "/var/lib/msp";

      StandardOutput = "journal";
      StandardError = "journal";
      SyslogIdentifier = "network-scanner";

      # Security hardening
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      ReadWritePaths = [ "/var/lib/msp" ];
      NoNewPrivileges = true;

      # Capabilities for network scanning
      AmbientCapabilities = [ "CAP_NET_RAW" "CAP_NET_ADMIN" ];
      CapabilityBoundingSet = [ "CAP_NET_RAW" "CAP_NET_ADMIN" ];
    };

    environment = {
      SCANNER_DB_PATH = "/var/lib/msp/devices.db";
      SCANNER_API_PORT = "8082";
      SCANNER_DAILY_SCAN_HOUR = "2";
      SCANNER_EXCLUDE_MEDICAL = "1";  # Always exclude medical devices by default
    };
  };

  # Daily network scan timer (2 AM)
  systemd.timers.network-scanner-daily = {
    description = "Daily network scan timer";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "*-*-* 02:00:00";
      Persistent = true;
      RandomizedDelaySec = "5m";
    };
  };

  systemd.services.network-scanner-daily = {
    description = "Trigger daily network scan";
    after = [ "network-scanner.service" ];
    requires = [ "network-scanner.service" ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${pkgs.curl}/bin/curl -X POST http://127.0.0.1:8082/api/scans/trigger -H 'Content-Type: application/json' -d '{\"scan_type\": \"full\", \"triggered_by\": \"schedule\"}'";
    };
  };

  # ============================================================================
  # Local Portal Service (WINDOW) - Device Transparency UI
  # ============================================================================
  systemd.services.local-portal = {
    description = "MSP Local Portal (WINDOW) - Device Transparency UI";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" "network-scanner.service" ];
    wants = [ "network-online.target" ];

    serviceConfig = {
      Type = "simple";
      ExecStart = "${local-portal}/bin/local-portal --port 8083 --host 0.0.0.0";
      Restart = "always";
      RestartSec = "10s";

      WorkingDirectory = "/var/lib/msp";

      StandardOutput = "journal";
      StandardError = "journal";
      SyslogIdentifier = "local-portal";

      # Security hardening
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      ReadWritePaths = [ "/var/lib/msp" ];
      NoNewPrivileges = true;
    };

    environment = {
      SCANNER_DB_PATH = "/var/lib/msp/devices.db";
      SCANNER_API_URL = "http://127.0.0.1:8082";
      EXPORT_DIR = "/var/lib/msp/exports";
    };
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

    # Network scanning (for network-scanner service)
    nmap
    arp-scan

    # Compliance agent (includes all Python dependencies)
    compliance-agent

    # Network scanner and local portal
    network-scanner
    local-portal

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
      allowedTCPPorts = [ 80 22 8080 50051 8082 8083 ];  # Status + SSH + Sensor API + gRPC + Scanner API + Local Portal
      allowedUDPPorts = [ 5353 ];   # mDNS
      # No other inbound - pull-only architecture
    };
  };

  # mDNS - allows access via osiriscare-appliance.local
  services.avahi = {
    enable = true;
    nssmdns4 = true;
    publish = {
      enable = true;
      addresses = true;
      workstation = true;
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
  # Persistent storage for config, evidence, and A/B update state
  # NOTE: These mounts are for installed systems with proper partitions
  # Live ISO testing falls back to tmpfs
  # ============================================================================

  # Data partition for persistent storage
  # Uses partlabel for A/B scheme compatibility
  fileSystems."/var/lib/msp" = {
    device = "/dev/disk/by-partlabel/MSP-DATA";
    fsType = "ext4";
    options = [ "defaults" "noatime" "nofail" ];
    neededForBoot = false;
  };

  # Boot partition for ab_state file (A/B update control)
  fileSystems."/boot" = {
    device = "/dev/disk/by-partlabel/ESP";
    fsType = "vfat";
    options = [ "defaults" "nofail" ];
    neededForBoot = false;
  };

  # Create directories on activation
  system.activationScripts.mspDirs = ''
    mkdir -p /var/lib/msp/evidence
    mkdir -p /var/lib/msp/queue
    mkdir -p /var/lib/msp/rules
    mkdir -p /var/lib/msp/update
    mkdir -p /var/lib/msp/update/downloads
    mkdir -p /var/lib/msp/exports
    mkdir -p /etc/msp/certs
    chmod 700 /var/lib/msp /etc/msp/certs
  '';

  # ============================================================================
  # SSH for emergency access only
  # ============================================================================
  services.openssh = {
    enable = true;
    settings = {
      PermitRootLogin = lib.mkForce "prohibit-password";
      PasswordAuthentication = lib.mkForce false;
      KbdInteractiveAuthentication = lib.mkForce false;
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
  # Auto-Provisioning Service (USB + MAC-based)
  # ============================================================================
  systemd.services.msp-auto-provision = {
    description = "MSP Appliance Auto-Provisioning";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" "local-fs.target" ];
    wants = [ "network-online.target" ];
    before = [ "compliance-agent.service" ];

    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };

    script = ''
      set -e
      CONFIG_PATH="/var/lib/msp/config.yaml"
      LOG_FILE="/var/lib/msp/provision.log"
      API_URL="https://api.osiriscare.net"

      log() {
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $1" | tee -a "$LOG_FILE"
      }

      mkdir -p /var/lib/msp

      # If config already exists, skip provisioning
      if [ -f "$CONFIG_PATH" ]; then
        log "Config already exists, skipping provisioning"
        exit 0
      fi

      log "=== Starting Auto-Provisioning ==="

      # ========================================
      # OPTION 1: Check USB drives for config
      # ========================================
      log "Checking USB drives for config.yaml..."

      USB_CONFIG_FOUND=false
      for dev in /dev/sd[a-z][0-9] /dev/disk/by-label/*; do
        [ -e "$dev" ] || continue

        MOUNT_POINT="/tmp/msp-usb-$$"
        mkdir -p "$MOUNT_POINT"

        if mount -o ro "$dev" "$MOUNT_POINT" 2>/dev/null; then
          # Check multiple possible locations
          for cfg_path in \
            "$MOUNT_POINT/config.yaml" \
            "$MOUNT_POINT/msp/config.yaml" \
            "$MOUNT_POINT/osiriscare/config.yaml" \
            "$MOUNT_POINT/MSP/config.yaml"; do

            if [ -f "$cfg_path" ]; then
              log "Found config at $cfg_path"
              cp "$cfg_path" "$CONFIG_PATH"
              chmod 600 "$CONFIG_PATH"
              USB_CONFIG_FOUND=true
              log "Config copied from USB successfully"
              break
            fi
          done
          umount "$MOUNT_POINT" 2>/dev/null || true
        fi
        rmdir "$MOUNT_POINT" 2>/dev/null || true

        [ "$USB_CONFIG_FOUND" = true ] && break
      done

      if [ "$USB_CONFIG_FOUND" = true ]; then
        log "Provisioning complete via USB"
        # Note: compliance-agent starts after this service via systemd ordering
        exit 0
      fi

      log "No USB config found"

      # ========================================
      # OPTION 2: MAC-based provisioning with retry
      # ========================================
      log "Attempting MAC-based provisioning..."

      # Get primary MAC address (prefer ethernet over wireless)
      MAC_ADDR=""
      for iface in /sys/class/net/eth* /sys/class/net/en* /sys/class/net/*; do
        [ -e "$iface" ] || continue
        IFACE_NAME=$(basename "$iface")
        [ "$IFACE_NAME" = "lo" ] && continue
        [ -f "$iface/address" ] || continue
        CANDIDATE=$(cat "$iface/address")
        [ "$CANDIDATE" = "00:00:00:00:00:00" ] && continue
        MAC_ADDR="$CANDIDATE"
        log "Using interface $IFACE_NAME with MAC $MAC_ADDR"
        break
      done

      if [ -z "$MAC_ADDR" ]; then
        log "ERROR: Could not determine MAC address"
      else
        log "MAC Address: $MAC_ADDR"

        # URL-encode the MAC (replace : with %3A)
        MAC_ENCODED=$(echo "$MAC_ADDR" | sed 's/:/%3A/g')
        PROVISION_URL="$API_URL/api/provision/$MAC_ENCODED"

        # Wait for network connectivity with retries
        MAX_RETRIES=6
        RETRY_DELAY=10
        PROVISIONED=false

        for attempt in $(seq 1 $MAX_RETRIES); do
          log "Attempt $attempt/$MAX_RETRIES: Checking network connectivity..."

          # Test DNS resolution first
          if ! ${pkgs.coreutils}/bin/timeout 5 ${pkgs.bash}/bin/bash -c "echo >/dev/tcp/1.1.1.1/53" 2>/dev/null; then
            log "Network not ready (no DNS), waiting ''${RETRY_DELAY}s..."
            sleep $RETRY_DELAY
            continue
          fi

          log "Network ready, fetching config from $PROVISION_URL"

          HTTP_CODE=$(${pkgs.curl}/bin/curl -s -w "%{http_code}" -o /tmp/provision-response.json \
            --connect-timeout 15 --max-time 45 \
            "$PROVISION_URL" 2>/dev/null || echo "000")

          if [ "$HTTP_CODE" = "200" ]; then
            # Check if response contains valid config
            if ${pkgs.jq}/bin/jq -e '.site_id' /tmp/provision-response.json >/dev/null 2>&1; then
              ${pkgs.yq}/bin/yq -y '.' /tmp/provision-response.json > "$CONFIG_PATH"
              chmod 600 "$CONFIG_PATH"
              log "SUCCESS: Provisioning complete via MAC lookup"
              rm -f /tmp/provision-response.json
              # Note: compliance-agent starts after this service via systemd ordering
              PROVISIONED=true
              exit 0
            else
              log "ERROR: Response missing site_id"
            fi
          elif [ "$HTTP_CODE" = "404" ]; then
            log "MAC not registered in Central Command (HTTP 404)"
            log "Register this MAC in the dashboard: $MAC_ADDR"
            break
          elif [ "$HTTP_CODE" = "000" ]; then
            log "Connection failed (HTTP 000), retrying in ''${RETRY_DELAY}s..."
          else
            log "Unexpected HTTP $HTTP_CODE, retrying in ''${RETRY_DELAY}s..."
          fi

          rm -f /tmp/provision-response.json
          sleep $RETRY_DELAY
        done

        if [ "$PROVISIONED" = false ]; then
          log "MAC provisioning failed after $MAX_RETRIES attempts"
        fi
      fi

      # ========================================
      # Neither method worked - offer provision code CLI
      # ========================================
      log "Auto-provisioning failed - provision code entry available"
      log ""
      log "Options:"
      log "  1. Run: compliance-provision (enter code from partner dashboard)"
      log "  2. Insert USB with config.yaml and reboot"
      log "  3. Pre-register MAC in Central Command dashboard"
      log ""
      log "After provisioning: systemctl restart compliance-agent"

      # Write instructions to console
      cat > /etc/issue.d/90-msp-provision.issue << 'ISSUE'

  ╔═══════════════════════════════════════════════════════════════════╗
  ║  PROVISIONING REQUIRED                                            ║
  ╠═══════════════════════════════════════════════════════════════════╣
  ║  No configuration found. Options:                                 ║
  ║                                                                   ║
  ║  1. Run: compliance-provision                                     ║
  ║     Enter 16-character code from partner dashboard                ║
  ║                                                                   ║
  ║  2. Insert USB with config.yaml and reboot                        ║
  ║                                                                   ║
  ║  3. Pre-register MAC in Central Command dashboard                 ║
  ║                                                                   ║
  ║  MAC: Run 'compliance-provision --mac' to display                 ║
  ║  See: https://docs.osiriscare.net/appliance-setup                 ║
  ╚═══════════════════════════════════════════════════════════════════╝

ISSUE
    '';
  };

  # ============================================================================
  # First-boot setup (runs after provisioning)
  # ============================================================================
  systemd.services.msp-first-boot = {
    description = "MSP Appliance First Boot Setup";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" "msp-auto-provision.service" ];
    wants = [ "network-online.target" ];

    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };

    script = ''
      MARKER="/var/lib/msp/.initialized"
      CONFIG_PATH="/var/lib/msp/config.yaml"

      if [ -f "$MARKER" ]; then
        exit 0
      fi

      echo "=== MSP Compliance Appliance First Boot Setup ==="
      echo "Timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

      # Set hostname from config if available
      if [ -f "$CONFIG_PATH" ]; then
        SITE_ID=$(${pkgs.yq}/bin/yq -r '.site_id // empty' "$CONFIG_PATH")
        if [ -n "$SITE_ID" ]; then
          hostnamectl set-hostname "$SITE_ID"
          echo "Hostname set to: $SITE_ID"
        fi
      fi

      touch "$MARKER"
      echo "=== First Boot Setup Complete ==="
    '';
  };
}

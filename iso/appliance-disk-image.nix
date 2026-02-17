# iso/appliance-disk-image.nix
# Builds a raw disk image for permanent installation on HP T640 or similar
# Unlike the live ISO, this puts the nix store on ext4 (not squashfs)
# Supports A/B partition updates and persistent storage

{ config, pkgs, lib, modulesPath, ... }:

let
  # Build the compliance-agent package
  compliance-agent = pkgs.python311Packages.buildPythonApplication {
    pname = "compliance-agent";
    version = "1.0.56";
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
    version = "0.1.0";
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
    version = "0.1.0";
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
in
{
  imports = [
    "${modulesPath}/profiles/base.nix"
    ./configuration.nix
    ./local-status.nix
  ];

  # System identification - mkForce ensures branding even if other modules set defaults
  networking.hostName = lib.mkForce "osiriscare";
  system.stateVersion = "24.05";

  # ============================================================================
  # Filesystem configuration for disk image
  # ============================================================================
  fileSystems."/" = {
    device = "/dev/disk/by-label/nixos";
    fsType = "ext4";
  };

  fileSystems."/boot" = {
    device = "/dev/disk/by-label/ESP";
    fsType = "vfat";
    options = [ "nofail" "x-systemd.device-timeout=10" ];  # Don't block boot if ESP is slow
  };

  # Persistent data partition for compliance evidence, config, and state
  fileSystems."/var/lib/msp" = {
    device = "/dev/disk/by-partlabel/MSP-DATA";
    fsType = "ext4";
    options = [ "defaults" "noatime" "nofail" ];
    neededForBoot = false;
  };

  # ============================================================================
  # Boot configuration for installed system (not live ISO)
  # ============================================================================
  boot = {
    loader = {
      systemd-boot.enable = true;
      efi.canTouchEfiVariables = false;
      timeout = 3;
    };

    # Kernel params — quiet boot, suppress noisy drivers
    kernelParams = [ "quiet" "loglevel=3" "console=tty1" "console=ttyS0,115200" ];
    blacklistedKernelModules = [ "hid_logitech_hidpp" ];

    # Essential kernel modules for HP T640 and common hardware
    initrd.availableKernelModules = [
      "ahci" "xhci_pci" "ehci_pci" "usbhid" "usb_storage" "sd_mod"
      "nvme" "sata_nv" "sata_via"
      "virtio_pci" "virtio_blk" "virtio_net"  # For VM testing
      "ext4" "vfat"
    ];

    # No squashfs needed - we're installed on ext4
    supportedFilesystems = [ "ext4" "vfat" ];
  };

  # No GUI - headless operation
  services.xserver.enable = false;

  # ============================================================================
  # Hardware Firmware - Support various hardware (Dell, HP, Lenovo, etc.)
  # ============================================================================
  nixpkgs.config.allowUnfree = true;  # Required for proprietary firmware (AMD, Intel, etc.)
  hardware.enableAllFirmware = true;  # Includes all firmware blobs
  hardware.enableRedistributableFirmware = true;  # Subset that's redistributable

  # Console login requires password for physical security (HIPAA §164.310)
  # Auto-login disabled in production - use SSH for remote access
  services.getty.autologinUser = lib.mkForce null;

  # ============================================================================
  # Branded Console - professional appliance look on physical console
  # ============================================================================
  # NOTE: Do NOT use services.getty.greetingLine with \4 — it re-renders once
  # per network interface (loopback, link-local, DHCP), causing triple banners.
  # Instead, msp-console-branding writes /etc/issue once after network settles.
  services.getty.greetingLine = lib.mkForce "";

  services.getty.helpLine = lib.mkForce ''
    Log in as \e[1mmsp\e[0m for appliance management. Root login via console only.
  '';

  # Oneshot service: write branded /etc/issue after real IP is available
  systemd.services.msp-console-branding = {
    description = "OsirisCare Console Branding";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" "systemd-networkd-wait-online.service" ];
    wants = [ "network-online.target" ];

    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      StandardOutput = "journal";
      StandardError = "journal";
    };

    # Wait briefly for DHCP, get real IP, write /etc/issue, nudge getty
    script = ''
      # Give DHCP a moment to settle
      sleep 3

      # Get the first real (non-loopback, non-link-local) IPv4 address
      IP=$(${pkgs.iproute2}/bin/ip -4 addr show scope global \
        | ${pkgs.gnugrep}/bin/grep -oP '(?<=inet\s)\d+(\.\d+){3}' \
        | head -1)

      # Fallback if no global IP yet
      if [ -z "$IP" ]; then
        IP="(no network)"
      fi

      # Build padded lines — box is 50 chars wide inside the borders
      W=50
      dash_url="http://$IP"
      ssh_cmd="ssh msp@$IP"
      portal_url="http://$IP:8084"

      pad() {
        local label="$1" value="$2"
        local content="  $label$value"
        local len=''${#content}
        local spaces=$((W - len))
        [ $spaces -lt 1 ] && spaces=1
        printf '%s%*s' "$content" "$spaces" ""
      }

      # NixOS manages /etc/issue as a symlink to the read-only nix store.
      # Remove the symlink so we can write a regular file with dynamic IP.
      rm -f /etc/issue
      cat > /etc/issue <<EOF
\e[1;36m
    ┌──────────────────────────────────────────────────┐
    │       OsirisCare MSP Compliance Platform         │
    │             COMPLIANCE APPLIANCE                 │
    ├──────────────────────────────────────────────────┤
    │$(pad "Dashboard:  " "$dash_url")│
    │$(pad "SSH:        " "$ssh_cmd")│
    │$(pad "Portal:     " "$portal_url")│
    └──────────────────────────────────────────────────┘
\e[0m
EOF

      # Signal getty to re-read /etc/issue.
      # Use --no-block to avoid deadlock when running inside nixos-rebuild activation.
      # Use timeout on tty write — blocks indefinitely if no physical console.
      timeout 2 bash -c 'printf "\033c" > /dev/tty1' 2>/dev/null || true
      ${pkgs.systemd}/bin/systemctl --no-block restart getty@tty1.service 2>/dev/null || true
    '';
  };

  # Post-login MOTD (first-boot service overwrites this with IP-specific info)
  environment.etc."motd".text = ''

    OsirisCare MSP - Compliance Appliance
    ──────────────────────────────────────
    Dashboard:  http://osiriscare.local
    SSH:        ssh msp@osiriscare.local
    Portal:     http://osiriscare.local:8084

    Agent:      journalctl -u compliance-agent -f
    Health:     systemctl status msp-health-check

  '';

  # ============================================================================
  # Health Gate Service (A/B Update Verification)
  # ============================================================================
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
      WorkingDirectory = "/var/lib/msp";
      StandardOutput = "journal";
      StandardError = "journal";
      SyslogIdentifier = "msp-health-gate";
      # Security hardening
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      ReadWritePaths = [ "/var/lib/msp" ];
      NoNewPrivileges = true;
      ProtectKernelTunables = true;
      ProtectKernelModules = true;
      ProtectControlGroups = true;
    };
  };

  # ============================================================================
  # Full Compliance Agent
  # ============================================================================
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
      WorkingDirectory = "/var/lib/msp";
      StandardOutput = "journal";
      StandardError = "journal";
      SyslogIdentifier = "compliance-agent";
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      ReadWritePaths = [ "/var/lib/msp" ];
      NoNewPrivileges = true;
    };

    environment = {
      HEALING_DRY_RUN = "false";
      STATE_DIR = "/var/lib/msp";
    };
  };

  # ============================================================================
  # Network Scanner Service (EYES)
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
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      ReadWritePaths = [ "/var/lib/msp" ];
      NoNewPrivileges = true;
      AmbientCapabilities = [ "CAP_NET_RAW" "CAP_NET_ADMIN" ];
      CapabilityBoundingSet = [ "CAP_NET_RAW" "CAP_NET_ADMIN" ];
    };

    environment = {
      DB_PATH = "/var/lib/msp/devices.db";
      # API port is 8081, Go agent listener is api_port+1 = 8082
      # local-portal uses 8084
      API_PORT = "8081";
      DAILY_SCAN_HOUR = "2";
      # Medical devices are excluded by default in config
    };
  };

  # ============================================================================
  # Local Portal Service (WINDOW)
  # ============================================================================
  systemd.services.local-portal = {
    description = "MSP Local Portal (WINDOW) - Device Transparency UI";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" "network-scanner.service" ];
    wants = [ "network-online.target" ];

    serviceConfig = {
      Type = "simple";
      # Bind to localhost only - use reverse proxy for network access
      ExecStart = "${local-portal}/bin/local-portal --port 8084 --host 127.0.0.1";
      Restart = "always";
      RestartSec = "10s";
      WorkingDirectory = "/var/lib/msp";
      StandardOutput = "journal";
      StandardError = "journal";
      SyslogIdentifier = "local-portal";
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      ReadWritePaths = [ "/var/lib/msp" ];
      NoNewPrivileges = true;
    };

    environment = {
      SCANNER_DB_PATH = "/var/lib/msp/devices.db";
      # Scanner API is on 8081 (Go agent listener is 8082)
      SCANNER_API_URL = "http://127.0.0.1:8081";
      EXPORT_DIR = "/var/lib/msp/exports";
    };
  };

  # ============================================================================
  # Minimal packages
  # ============================================================================
  environment.systemPackages = with pkgs; [
    vim curl htop
    iproute2 iputils dnsutils
    nmap arp-scan
    compliance-agent network-scanner local-portal
    jq yq
  ];

  # ============================================================================
  # Networking
  # ============================================================================
  networking = {
    useDHCP = true;
    firewall = {
      enable = true;
      # 22=ssh, 80=status, 8080=compliance-agent-sensor, 50051=grpc, 8084=local-portal
      # 8081 (scanner-api) and 8082 (go-agent-metrics) bind to localhost only
      allowedTCPPorts = [ 22 80 8080 50051 8084 ];
      allowedUDPPorts = [ 5353 ];
    };
  };

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
  # Persistent storage
  # ============================================================================

  # For installed system, /var/lib/msp is on the main filesystem
  # Create directories on activation
  system.activationScripts.mspDirs = ''
    mkdir -p /var/lib/msp/evidence
    mkdir -p /var/lib/msp/queue
    mkdir -p /var/lib/msp/rules
    mkdir -p /var/lib/msp/rules/promoted
    mkdir -p /var/lib/msp/update
    mkdir -p /var/lib/msp/update/downloads
    mkdir -p /var/lib/msp/exports
    mkdir -p /etc/msp/certs
    chmod 700 /var/lib/msp /etc/msp/certs
  '';

  # ============================================================================
  # SSH for emergency access
  # ============================================================================
  services.openssh = {
    enable = true;
    settings = {
      PermitRootLogin = lib.mkForce "prohibit-password";  # Key-only — no password login
      PasswordAuthentication = lib.mkForce false;  # Disabled for security
      KbdInteractiveAuthentication = lib.mkForce false;
    };
  };

  # Lab-only initial password — production builds MUST override via SOPS secrets
  # This is set as mkDefault so production configs can override it to null
  users.users.root.initialPassword = lib.mkDefault "osiris2024";

  # Lab SSH keys — production appliances receive keys via Central Command provisioning API
  users.users.root.openssh.authorizedKeys.keys = [
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE8uV6E//e4fQXlDEMoE0uADd/nAzKwqA0btaoHc28Bl macs-imac-vm-access"
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBv6abzJDSfxWt00y2jtmZiubAiehkiLe/7KBot+6JHH jbouey@osiriscare.net"
  ];

  # ============================================================================
  # Reduce image size
  # ============================================================================
  documentation.enable = false;
  documentation.man.enable = false;
  documentation.nixos.enable = false;
  programs.command-not-found.enable = false;

  # ============================================================================
  # Auto-Install Service - Zero Friction USB to Internal Drive
  # ============================================================================
  systemd.services.msp-auto-install = {
    description = "MSP Appliance Auto-Install to Internal Drive";
    wantedBy = [ "multi-user.target" ];
    after = [ "local-fs.target" ];
    before = [ "msp-auto-provision.service" ];

    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };

    script = ''
      set -e
      MARKER="/var/lib/msp/.installed-to-internal"
      LOG_FILE="/var/lib/msp/install.log"

      mkdir -p /var/lib/msp

      log() {
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $1" | tee -a "$LOG_FILE"
      }

      # Skip if already installed to internal drive
      if [ -f "$MARKER" ]; then
        log "Already installed to internal drive, skipping"
        exit 0
      fi

      # Find boot device
      BOOT_DEV=$(findmnt -n -o SOURCE / | sed 's/[0-9]*$//' | sed 's/p$//')
      BOOT_DEV_NAME=$(basename "$BOOT_DEV")
      log "Boot device: $BOOT_DEV"

      # Check if boot device is on USB bus (more reliable than removable flag)
      IS_USB=$(readlink -f /sys/block/$BOOT_DEV_NAME/device | grep -q usb && echo "1" || echo "0")
      IS_REMOVABLE=$(cat /sys/block/$BOOT_DEV_NAME/removable 2>/dev/null || echo "0")

      log "Boot device $BOOT_DEV_NAME: USB=$IS_USB, removable=$IS_REMOVABLE"

      if [ "$IS_USB" != "1" ] && [ "$IS_REMOVABLE" != "1" ]; then
        log "Not booting from USB/removable media, marking as installed"
        touch "$MARKER"
        exit 0
      fi

      log "=== Booting from USB - Starting Auto-Install ==="

      # Find internal drive (prefer NVMe, then SATA)
      INTERNAL_DEV=""
      for dev in /dev/nvme0n1 /dev/sda /dev/sdb /dev/vda; do
        [ -b "$dev" ] || continue
        DEV_NAME=$(basename "$dev" | sed 's/[0-9]*$//')
        [ "$DEV_NAME" = "$BOOT_DEV_NAME" ] && continue

        # Check it's not removable
        REMOVABLE=$(cat /sys/block/$DEV_NAME/removable 2>/dev/null || echo "1")
        if [ "$REMOVABLE" = "0" ]; then
          SIZE=$(blockdev --getsize64 "$dev" 2>/dev/null || echo "0")
          # Must be at least 20GB
          if [ "$SIZE" -gt 20000000000 ]; then
            INTERNAL_DEV="$dev"
            log "Found internal drive: $INTERNAL_DEV ($(numfmt --to=iec $SIZE))"
            break
          fi
        fi
      done

      if [ -z "$INTERNAL_DEV" ]; then
        log "ERROR: No suitable internal drive found"
        exit 0
      fi

      # Get boot disk size for progress
      BOOT_SIZE=$(blockdev --getsize64 "$BOOT_DEV")
      log "Cloning $BOOT_DEV ($(numfmt --to=iec $BOOT_SIZE)) to $INTERNAL_DEV"

      # Clone boot disk to internal drive
      log "Starting disk clone... (this takes 2-5 minutes)"
      ${pkgs.coreutils}/bin/dd if="$BOOT_DEV" of="$INTERNAL_DEV" bs=4M status=progress conv=fsync 2>&1 | tee -a "$LOG_FILE"
      ${pkgs.coreutils}/bin/sync

      log "Clone complete!"
      log "=== AUTO-INSTALL COMPLETE ==="
      log "Remove USB and reboot to start from internal drive"
      log "Rebooting in 10 seconds..."

      # Give user time to see the message on console
      sleep 10

      # Reboot to internal drive
      ${pkgs.systemd}/bin/systemctl reboot
    '';
  };

  # ============================================================================
  # Auto-Provisioning Service (same as ISO version)
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

      if [ -f "$CONFIG_PATH" ]; then
        log "Config already exists, skipping provisioning"
        exit 0
      fi

      log "=== Starting Auto-Provisioning ==="

      # Check USB drives for config
      log "Checking USB drives for config.yaml..."
      USB_CONFIG_FOUND=false
      for dev in /dev/sd[a-z][0-9] /dev/disk/by-label/*; do
        [ -e "$dev" ] || continue
        MOUNT_POINT="/tmp/msp-usb-$$"
        mkdir -p "$MOUNT_POINT"
        if mount -o ro "$dev" "$MOUNT_POINT" 2>/dev/null; then
          for cfg_path in \
            "$MOUNT_POINT/config.yaml" \
            "$MOUNT_POINT/msp/config.yaml" \
            "$MOUNT_POINT/osiriscare/config.yaml"; do
            if [ -f "$cfg_path" ]; then
              log "Found config at $cfg_path"
              cp "$cfg_path" "$CONFIG_PATH"
              chmod 600 "$CONFIG_PATH"
              USB_CONFIG_FOUND=true
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
        exit 0
      fi

      log "No USB config found, attempting MAC-based provisioning..."

      # Get primary MAC address
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
        MAC_ENCODED=$(echo "$MAC_ADDR" | sed 's/:/%3A/g')
        PROVISION_URL="$API_URL/api/provision/$MAC_ENCODED"

        MAX_RETRIES=6
        RETRY_DELAY=10
        for attempt in $(seq 1 $MAX_RETRIES); do
          log "Attempt $attempt/$MAX_RETRIES: Fetching config..."
          HTTP_CODE=$(${pkgs.curl}/bin/curl -s -w "%{http_code}" -o /tmp/provision-response.json \
            --connect-timeout 15 --max-time 45 "$PROVISION_URL" 2>/dev/null || echo "000")

          if [ "$HTTP_CODE" = "200" ]; then
            if ${pkgs.jq}/bin/jq -e '.site_id' /tmp/provision-response.json >/dev/null 2>&1; then
              ${pkgs.yq}/bin/yq -y '.' /tmp/provision-response.json > "$CONFIG_PATH"
              chmod 600 "$CONFIG_PATH"
              log "SUCCESS: Provisioning complete via MAC lookup"
              rm -f /tmp/provision-response.json
              exit 0
            fi
          elif [ "$HTTP_CODE" = "404" ]; then
            log "MAC not registered (HTTP 404). Register: $MAC_ADDR"
            break
          fi
          rm -f /tmp/provision-response.json
          sleep $RETRY_DELAY
        done
      fi

      log "Auto-provisioning failed - manual configuration required"
      log "Options: 1) Insert USB with config.yaml  2) Register MAC in dashboard"
    '';
  };

  # ============================================================================
  # First-boot setup - SSH key provisioning and emergency access
  # ============================================================================
  systemd.services.msp-first-boot = {
    description = "MSP Appliance First Boot Setup";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" "msp-auto-provision.service" ];
    wants = [ "network-online.target" ];

    path = with pkgs; [ inetutils iproute2 gnugrep coreutils ];

    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };

    script = ''
      MARKER="/var/lib/msp/.initialized"
      CONFIG_PATH="/var/lib/msp/config.yaml"
      SSH_DIR="/home/msp/.ssh"
      CREDS_FILE="/var/lib/msp/.emergency-credentials"

      if [ -f "$MARKER" ]; then
        exit 0
      fi

      echo "=== MSP Compliance Appliance First Boot Setup ==="

      # Get MAC address for emergency password derivation
      MAC_ADDR=""
      for iface in /sys/class/net/eth* /sys/class/net/en* /sys/class/net/*; do
        [ -e "$iface" ] || continue
        IFACE_NAME=$(basename "$iface")
        [ "$IFACE_NAME" = "lo" ] && continue
        [ -f "$iface/address" ] || continue
        CANDIDATE=$(cat "$iface/address")
        [ "$CANDIDATE" = "00:00:00:00:00:00" ] && continue
        MAC_ADDR="$CANDIDATE"
        break
      done

      # Generate MAC-derived emergency password (first 12 chars of SHA256)
      # Format: osiris-XXXX where XXXX is derived from MAC
      if [ -n "$MAC_ADDR" ]; then
        HASH=$(echo -n "osiriscare-emergency-$MAC_ADDR" | ${pkgs.coreutils}/bin/sha256sum | cut -c1-8)
        EMERGENCY_PASS="osiris-$HASH"
        echo "msp:$EMERGENCY_PASS" | ${pkgs.shadow}/bin/chpasswd
        echo "$EMERGENCY_PASS" > "$CREDS_FILE"
        chmod 600 "$CREDS_FILE"
        echo "Emergency password set for msp user"
      fi

      # Apply SSH keys from config.yaml
      if [ -f "$CONFIG_PATH" ]; then
        SITE_ID=$(${pkgs.yq}/bin/yq -r '.site_id // empty' "$CONFIG_PATH")
        if [ -n "$SITE_ID" ]; then
          ${pkgs.inetutils}/bin/hostname "$SITE_ID"
          echo "Hostname set to: $SITE_ID"
        fi

        # Extract and apply SSH authorized keys
        SSH_KEYS=$(${pkgs.yq}/bin/yq -r '.ssh_authorized_keys // [] | .[]' "$CONFIG_PATH" 2>/dev/null)
        if [ -n "$SSH_KEYS" ]; then
          mkdir -p "$SSH_DIR"
          chmod 700 "$SSH_DIR"
          echo "$SSH_KEYS" > "$SSH_DIR/authorized_keys"
          chmod 600 "$SSH_DIR/authorized_keys"
          chown -R msp:users "$SSH_DIR"
          echo "SSH keys applied from config.yaml"
        fi
      fi

      # Update MOTD with access info
      IP_ADDR=$(ip -4 addr show | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | grep -v '127.0.0.1' | head -1)
      cat > /run/motd.dynamic << MOTD

    ╔═══════════════════════════════════════════════════════════╗
    ║           OsirisCare Compliance Appliance                 ║
    ╚═══════════════════════════════════════════════════════════╝

    IP Address: $IP_ADDR
    SSH Access: ssh msp@$IP_ADDR
    Status:     http://$IP_ADDR

    Emergency console access:
      User: msp
      Password: See /var/lib/msp/.emergency-credentials
               (or derive from MAC: osiris-[first 8 chars of sha256])

    To add SSH keys:
      1. Register MAC in dashboard with your public key
      2. Or add to /home/msp/.ssh/authorized_keys

MOTD

      touch "$MARKER"
      echo "=== First Boot Setup Complete ==="
    '';
  };
}

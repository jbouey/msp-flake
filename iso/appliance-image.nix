# iso/appliance-image.nix
# Builds bootable installer ISO for MSP Compliance Appliance
#
# ZERO FRICTION INSTALL (3 steps):
# 1. Write ISO to USB
# 2. Boot target hardware (must have internet access)
# 3. Auto-installs from GitHub flake → reboots → done
#
# REQUIRES NETWORK - nixos-install fetches the appliance flake from GitHub
# Works on ANY x86_64 hardware - the flake is the golden image

{ config, pkgs, lib, ... }:

let
  # Build the compliance-agent package
  compliance-agent = pkgs.python311Packages.buildPythonApplication {
    pname = "compliance-agent";
    version = "1.0.55";  # Session 86 - Installer fixes, MSP-DATA partition
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

  # System identification - mkForce overrides installer module's "nixos" default
  networking.hostName = lib.mkForce "osiriscare-installer";
  system.stateVersion = "24.05";

  # Boot with serial console for debugging
  # nosoftlockup: prevent false watchdog alarms during heavy nixos-install I/O
  # audit=0: disable kernel audit on live ISO (configuration.nix enables auditd
  # with execve logging which causes kauditd hold queue overflow during boot)
  boot.kernelParams = [ "console=tty1" "console=ttyS0,115200" "nosoftlockup" "audit=0" ];
  boot.loader.timeout = lib.mkForce 3;

  # Disable hardware watchdog on live ISO - nixos-install starves CPUs
  # The installed system (appliance-disk-image.nix) has its own watchdog config
  systemd.watchdog.runtimeTime = lib.mkForce null;
  systemd.watchdog.device = lib.mkForce null;

  # Disable auditd on the live ISO - the execve audit rule floods the system
  # The installed system gets audit from configuration.nix
  security.auditd.enable = lib.mkForce false;
  security.audit.enable = lib.mkForce false;

  # No GUI - headless operation
  services.xserver.enable = false;

  # ============================================================================
  # Hardware Firmware - Support various hardware (Dell, HP, Lenovo, etc.)
  # ============================================================================
  nixpkgs.config.allowUnfree = true;  # Required for proprietary firmware (AMD, Intel, etc.)
  hardware.enableAllFirmware = true;  # Includes all firmware blobs
  hardware.enableRedistributableFirmware = true;  # Subset that's redistributable

  # Disable getty on tty1 during install — the installer service owns the console
  # Getty only runs on tty2+ for manual debug access
  systemd.services."getty@tty1".enable = false;
  systemd.services."autovt@tty1".enable = false;

  # Login prompt on tty2 for manual debug (Alt+F2)
  services.getty.greetingLine = lib.mkForce ''

    \e[1;36m╔═══════════════════════════════════════════════════════════╗
    ║          OsirisCare MSP Compliance Platform                ║
    ║            INSTALLER DEBUG CONSOLE                         ║
    ╚═══════════════════════════════════════════════════════════╝\e[0m

  '';

  services.getty.helpLine = lib.mkForce ''
    Log in as \e[1mroot\e[0m (password: osiris2024) for debug access.
    Install log: cat /tmp/msp-install.log
  '';

  # ============================================================================
  # ZERO FRICTION AUTO-INSTALL SERVICE
  # Detects internal drive, partitions, runs nixos-install from flake
  # ============================================================================
  systemd.services.msp-auto-install = {
    description = "MSP Appliance Zero-Friction Auto-Install";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" "systemd-vconsole-setup.service" ];
    wants = [ "network-online.target" ];
    conflicts = [ "getty@tty1.service" ];

    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      # Take over tty1 — this IS the user-facing display
      StandardInput = "tty";
      StandardOutput = "tty";
      StandardError = "journal";
      TTYPath = "/dev/tty1";
      TTYReset = "yes";
      TTYVHangup = "yes";
    };

    path = with pkgs; [
      util-linux parted dosfstools e2fsprogs nixos-install-tools
      git curl coreutils gnugrep gawk procps
      nix  # Required - nixos-install calls nix and nix-build internally
    ];

    script = ''
      set -e
      LOG_FILE="/tmp/msp-install.log"
      TOTAL_STEPS=8
      CURRENT_STEP=0

      # ── Branded output helpers ──────────────────────────────────
      C_CYAN="\033[1;36m"
      C_GREEN="\033[1;32m"
      C_YELLOW="\033[1;33m"
      C_RED="\033[1;31m"
      C_WHITE="\033[1;37m"
      C_DIM="\033[2m"
      C_RESET="\033[0m"

      log() {
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $1" >> "$LOG_FILE"
        echo -e "$1"
      }

      banner() {
        clear
        echo ""
        echo -e "  ''${C_CYAN}╔═══════════════════════════════════════════════════════════╗"
        echo -e "  ║                                                           ║"
        echo -e "  ║       ''${C_WHITE}OsirisCare MSP Compliance Platform''${C_CYAN}                ║"
        echo -e "  ║            ''${C_WHITE}APPLIANCE INSTALLER''${C_CYAN}                           ║"
        echo -e "  ║                                                           ║"
        echo -e "  ╚═══════════════════════════════════════════════════════════╝''${C_RESET}"
        echo ""
      }

      progress_bar() {
        local pct=$1
        local width=40
        local filled=$(( pct * width / 100 ))
        local empty=$(( width - filled ))
        local bar=""
        for ((i=0; i<filled; i++)); do bar+="█"; done
        for ((i=0; i<empty; i++)); do bar+="░"; done
        echo -e "  ''${C_CYAN}[''${C_GREEN}''${bar}''${C_CYAN}] ''${C_WHITE}''${pct}%''${C_RESET}"
      }

      step() {
        CURRENT_STEP=$((CURRENT_STEP + 1))
        local pct=$(( CURRENT_STEP * 100 / TOTAL_STEPS ))
        echo ""
        echo -e "  ''${C_WHITE}Step ''${CURRENT_STEP}/''${TOTAL_STEPS}: $1''${C_RESET}"
        progress_bar "$pct"
        echo ""
        log "Step $CURRENT_STEP/$TOTAL_STEPS: $1"
      }

      step_ok() {
        echo -e "  ''${C_GREEN}✓ $1''${C_RESET}"
        log "  OK: $1"
      }

      step_fail() {
        echo -e "  ''${C_RED}✗ $1''${C_RESET}"
        log "  FAIL: $1"
      }

      # ── Start installer ─────────────────────────────────────────
      banner

      # Log block devices for debugging
      lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,MODEL >> "$LOG_FILE" 2>&1

      # Check if we're running from live ISO
      if ! grep -q "squashfs" /proc/mounts; then
        log "Not running from live ISO, skipping auto-install"
        exit 0
      fi

      step "Detecting hardware"

      # Find internal drive (skip USB/removable)
      BOOT_DEV=$(findmnt -n -o SOURCE / | sed 's/\[.*$//' | head -1)

      INTERNAL_DEV=""
      for dev in /dev/sda /dev/sdb /dev/vda /dev/nvme0n1; do
        [ -b "$dev" ] || continue
        DEV_NAME=$(basename "$dev")
        echo "$BOOT_DEV" | grep -q "$DEV_NAME" && continue
        REMOVABLE=$(cat /sys/block/$DEV_NAME/removable 2>/dev/null || echo "1")
        [ "$REMOVABLE" = "1" ] && continue
        SIZE=$(blockdev --getsize64 "$dev" 2>/dev/null || echo "0")
        if [ "$SIZE" -gt 16000000000 ]; then
          INTERNAL_DEV="$dev"
          break
        fi
      done

      if [ -z "$INTERNAL_DEV" ]; then
        step_fail "No suitable internal drive found (need >16GB)"
        echo -e "  ''${C_DIM}Available drives:''${C_RESET}"
        lsblk -d -o NAME,SIZE,TYPE,MODEL
        echo ""
        echo -e "  ''${C_YELLOW}Please attach an internal drive and reboot.''${C_RESET}"
        exit 0
      fi

      DEV_SIZE=$(numfmt --to=iec $(blockdev --getsize64 "$INTERNAL_DEV"))
      step_ok "Found internal drive: $INTERNAL_DEV ($DEV_SIZE)"

      # Check for existing install
      NIXOS_PART=$(lsblk -rno NAME,LABEL "$INTERNAL_DEV" | grep nixos | head -1 | awk '{print $1}')
      if [ -n "$NIXOS_PART" ]; then
        TMPDIR=$(mktemp -d)
        if mount -o ro "/dev/$NIXOS_PART" "$TMPDIR" 2>/dev/null; then
          if [ -d "$TMPDIR/nix/store" ]; then
            umount "$TMPDIR"
            rmdir "$TMPDIR"
            if [ ! -f /tmp/force-reinstall ]; then
              banner
              echo -e "  ''${C_GREEN}╔═══════════════════════════════════════════════════════════╗"
              echo -e "  ║          INSTALLATION ALREADY COMPLETE                      ║"
              echo -e "  ╠═══════════════════════════════════════════════════════════╣"
              echo -e "  ║                                                           ║"
              echo -e "  ║  1. Remove the USB installer                              ║"
              echo -e "  ║  2. Reboot to start the appliance                         ║"
              echo -e "  ║                                                           ║"
              echo -e "  ║  ''${C_WHITE}To force reinstall:''${C_GREEN}                                   ║"
              echo -e "  ║    touch /tmp/force-reinstall''${C_GREEN}                            ║"
              echo -e "  ║    systemctl restart msp-auto-install''${C_GREEN}                    ║"
              echo -e "  ║                                                           ║"
              echo -e "  ╚═══════════════════════════════════════════════════════════╝''${C_RESET}"
              echo ""
              exit 0
            fi
            log "Force reinstall requested"
          fi
          umount "$TMPDIR" 2>/dev/null
        fi
        rmdir "$TMPDIR" 2>/dev/null
      fi

      # Countdown
      echo ""
      echo -e "  ''${C_YELLOW}▸ Installing to $INTERNAL_DEV — ALL DATA WILL BE ERASED''${C_RESET}"
      echo ""
      for i in 10 9 8 7 6 5 4 3 2 1; do
        echo -ne "\r  ''${C_DIM}Starting in ''${C_WHITE}$i''${C_DIM} seconds... (Ctrl+C to cancel)''${C_RESET}  "
        sleep 1
      done
      echo ""

      # ── Partition ───────────────────────────────────────────────
      step "Partitioning drive"
      wipefs -a "$INTERNAL_DEV" >> "$LOG_FILE" 2>&1
      parted -s "$INTERNAL_DEV" -- mklabel gpt >> "$LOG_FILE" 2>&1
      parted -s "$INTERNAL_DEV" -- mkpart ESP fat32 1MiB 512MiB >> "$LOG_FILE" 2>&1
      parted -s "$INTERNAL_DEV" -- set 1 esp on >> "$LOG_FILE" 2>&1
      parted -s "$INTERNAL_DEV" -- mkpart MSP-DATA ext4 512MiB 2560MiB >> "$LOG_FILE" 2>&1
      parted -s "$INTERNAL_DEV" -- mkpart primary 2560MiB 100% >> "$LOG_FILE" 2>&1
      sleep 2
      partprobe "$INTERNAL_DEV" >> "$LOG_FILE" 2>&1
      sleep 2

      if [[ "$INTERNAL_DEV" == *"nvme"* ]]; then
        ESP_PART="''${INTERNAL_DEV}p1"
        DATA_PART="''${INTERNAL_DEV}p2"
        ROOT_PART="''${INTERNAL_DEV}p3"
      else
        ESP_PART="''${INTERNAL_DEV}1"
        DATA_PART="''${INTERNAL_DEV}2"
        ROOT_PART="''${INTERNAL_DEV}3"
      fi
      step_ok "ESP: $ESP_PART | Data: $DATA_PART | Root: $ROOT_PART"

      # ── Format ──────────────────────────────────────────────────
      step "Formatting partitions"
      mkfs.fat -F32 -n ESP "$ESP_PART" >> "$LOG_FILE" 2>&1
      step_ok "ESP (FAT32)"
      mkfs.ext4 -L MSP-DATA -F "$DATA_PART" >> "$LOG_FILE" 2>&1
      step_ok "MSP-DATA (ext4)"
      mkfs.ext4 -L nixos -F "$ROOT_PART" >> "$LOG_FILE" 2>&1
      step_ok "Root (ext4)"

      # ── Mount ───────────────────────────────────────────────────
      step "Mounting filesystems"
      mount "$ROOT_PART" /mnt
      mkdir -p /mnt/boot
      mount "$ESP_PART" /mnt/boot
      mkdir -p /mnt/var/lib/msp
      mount "$DATA_PART" /mnt/var/lib/msp
      step_ok "Mounted root, boot, and data partitions"

      # ── Hardware config ─────────────────────────────────────────
      step "Detecting hardware configuration"
      nixos-generate-config --root /mnt >> "$LOG_FILE" 2>&1
      step_ok "Hardware configuration generated"

      # ── Install (the big step) ──────────────────────────────────
      step "Installing OsirisCare appliance"
      FLAKE_URL="github:jbouey/msp-flake#osiriscare-appliance-disk"

      echo -e "  ''${C_DIM}Fetching from: $FLAKE_URL''${C_RESET}"
      echo -e "  ''${C_DIM}This may take 5-15 minutes depending on network speed...''${C_RESET}"
      echo ""

      # Show a spinner while nixos-install runs
      nixos-install --flake "$FLAKE_URL" --no-root-passwd >> "$LOG_FILE" 2>&1 &
      INSTALL_PID=$!

      SPINNER="⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
      ELAPSED=0
      while kill -0 $INSTALL_PID 2>/dev/null; do
        for ((i=0; i<''${#SPINNER}; i++)); do
          kill -0 $INSTALL_PID 2>/dev/null || break
          MINS=$((ELAPSED / 60))
          SECS=$((ELAPSED % 60))
          LAST_LINE=$(tail -1 "$LOG_FILE" 2>/dev/null | cut -c1-60)
          echo -ne "\r  ''${C_CYAN}''${SPINNER:$i:1}''${C_RESET} Installing... ''${C_DIM}(''${MINS}m ''${SECS}s)''${C_RESET}  ''${C_DIM}''${LAST_LINE}''${C_RESET}          "
          sleep 1
          ELAPSED=$((ELAPSED + 1))
        done
      done
      echo ""

      wait $INSTALL_PID
      INSTALL_EXIT=$?

      if [ $INSTALL_EXIT -ne 0 ]; then
        step_fail "Installation failed (exit code $INSTALL_EXIT)"
        echo ""
        echo -e "  ''${C_RED}Check the log: cat /tmp/msp-install.log''${C_RESET}"
        echo -e "  ''${C_YELLOW}Manual install: nixos-install --flake $FLAKE_URL --no-root-passwd''${C_RESET}"
        exit 1
      fi
      step_ok "OsirisCare appliance installed successfully"

      # ── Verify ──────────────────────────────────────────────────
      step "Verifying installation"
      STORE_COUNT=$(ls /mnt/nix/store 2>/dev/null | wc -l)
      BOOT_OK="no"
      [ -d /mnt/boot/EFI ] && BOOT_OK="yes"
      step_ok "Nix store: $STORE_COUNT packages | Boot: $BOOT_OK"
      ls -la /mnt/boot/ >> "$LOG_FILE" 2>/dev/null

      # ── Done ────────────────────────────────────────────────────
      umount -R /mnt

      banner
      progress_bar 100
      echo ""
      echo -e "  ''${C_GREEN}╔═══════════════════════════════════════════════════════════╗"
      echo -e "  ║                                                           ║"
      echo -e "  ║        ''${C_WHITE}INSTALLATION COMPLETE!''${C_GREEN}                            ║"
      echo -e "  ║                                                           ║"
      echo -e "  ╠═══════════════════════════════════════════════════════════╣"
      echo -e "  ║                                                           ║"
      echo -e "  ║  ''${C_WHITE}Next steps:''${C_GREEN}                                            ║"
      echo -e "  ║    1. Remove the USB drive                                ║"
      echo -e "  ║    2. System will reboot in 30 seconds                    ║"
      echo -e "  ║    3. Appliance will auto-provision on first boot         ║"
      echo -e "  ║                                                           ║"
      echo -e "  ║  ''${C_DIM}$STORE_COUNT packages installed to $INTERNAL_DEV''${C_GREEN}${C_RESET}"
      echo -e "  ''${C_GREEN}║                                                           ║"
      echo -e "  ╚═══════════════════════════════════════════════════════════╝''${C_RESET}"
      echo ""

      for i in 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1; do
        echo -ne "\r  ''${C_DIM}Rebooting in ''${C_WHITE}$i''${C_DIM} seconds... (remove USB now)''${C_RESET}   "
        sleep 1
      done
      echo ""

      systemctl reboot
    '';
  };

  # Post-login MOTD
  environment.etc."motd".text = ''

    OsirisCare MSP - Appliance Installer
    ─────────────────────────────────────
    The auto-install service partitions, formats, and installs
    the appliance via nixos-install from the GitHub flake.

    Useful commands:
      journalctl -u msp-auto-install -f    # Watch install progress
      systemctl status msp-auto-install     # Check install status
      ip addr                               # Show IP addresses

  '';

  # ============================================================================
  # Live Install Progress on tty2 (Alt+F2)
  # Shows real-time install output without needing to log in
  # ============================================================================
  systemd.services.msp-install-display = {
    description = "OsirisCare Install Debug Log (tty2)";
    after = [ "systemd-vconsole-setup.service" ];
    wantedBy = [ "multi-user.target" ];
    conflicts = [ "getty@tty2.service" ];

    serviceConfig = {
      Type = "simple";
      Restart = "always";
      RestartSec = "2s";
      StandardInput = "tty";
      StandardOutput = "tty";
      TTYPath = "/dev/tty2";
      TTYReset = "yes";
      TTYVHangup = "yes";
    };

    path = with pkgs; [ coreutils systemd util-linux iproute2 gnugrep gawk ];

    script = ''
      clear
      echo -e "\033[2m── OsirisCare Install Log (Alt+F1 for main display) ──\033[0m"
      echo ""

      # Follow the install log file in real time
      exec tail -f /tmp/msp-install.log 2>/dev/null || exec journalctl -u msp-auto-install -f --no-hostname -o cat
    '';
  };

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
      STATE_DIR = "/var/lib/msp";
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
      # Bind to localhost only - use reverse proxy for network access
      ExecStart = "${local-portal}/bin/local-portal --port 8083 --host 127.0.0.1";
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
  # NOTE: These mounts won't exist on the live ISO (nofail prevents boot failure)
  # They apply after nixos-install when booted from the installed system
  # ============================================================================

  # Data partition (2GB) for compliance evidence, config, and state
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
  # SSH for emergency access
  # Live ISO: password enabled for debugging
  # Installed: key-only auth
  # ============================================================================
  services.openssh = {
    enable = true;
    settings = {
      PermitRootLogin = lib.mkForce "yes";  # Allow root SSH on live ISO for debugging
      PasswordAuthentication = lib.mkForce true;  # Enable password on live ISO
      KbdInteractiveAuthentication = lib.mkForce false;
    };
  };

  # Root password for live ISO debugging (not for installed system)
  users.users.root.initialPassword = "osiris2024";

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
  # First-boot setup - SSH key provisioning and emergency access
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
      SSH_DIR="/home/msp/.ssh"
      CREDS_FILE="/var/lib/msp/.emergency-credentials"

      if [ -f "$MARKER" ]; then
        exit 0
      fi

      echo "=== MSP Compliance Appliance First Boot Setup ==="
      echo "Timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

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

      # Generate MAC-derived emergency password (first 8 chars of SHA256)
      # Format: osiris-XXXXXXXX where X is derived from MAC
      if [ -n "$MAC_ADDR" ]; then
        HASH=$(echo -n "osiriscare-emergency-$MAC_ADDR" | ${pkgs.coreutils}/bin/sha256sum | cut -c1-8)
        EMERGENCY_PASS="osiris-$HASH"
        echo "msp:$EMERGENCY_PASS" | ${pkgs.shadow}/bin/chpasswd
        echo "$EMERGENCY_PASS" > "$CREDS_FILE"
        chmod 600 "$CREDS_FILE"
        echo "Emergency password set for msp user"
      fi

      # Set hostname and apply SSH keys from config if available
      if [ -f "$CONFIG_PATH" ]; then
        SITE_ID=$(${pkgs.yq}/bin/yq -r '.site_id // empty' "$CONFIG_PATH")
        if [ -n "$SITE_ID" ]; then
          hostnamectl set-hostname "$SITE_ID"
          echo "Hostname set to: $SITE_ID"
        fi

        # Extract and apply SSH authorized keys from config
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
      cat > /etc/motd << MOTD

    ╔═══════════════════════════════════════════════════════════╗
    ║           OsirisCare Compliance Appliance                 ║
    ╚═══════════════════════════════════════════════════════════╝

    IP Address: $IP_ADDR
    SSH Access: ssh msp@$IP_ADDR
    Status:     http://$IP_ADDR

    Emergency console access:
      User: msp
      Password: See /var/lib/msp/.emergency-credentials
               (or derive: osiris-[first 8 of sha256("osiriscare-emergency-MAC")])

    To add SSH keys:
      1. Register MAC in dashboard with your public key
      2. Or add to /home/msp/.ssh/authorized_keys

MOTD

      touch "$MARKER"
      echo "=== First Boot Setup Complete ==="
    '';
  };
}

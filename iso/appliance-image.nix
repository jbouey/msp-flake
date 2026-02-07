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

  # Readable console font for the installer TUI
  console.earlySetup = lib.mkForce true;
  console.font = lib.mkForce "${pkgs.terminus_font}/share/consolefonts/ter-v22n.psf.gz";
  console.packages = lib.mkForce [ pkgs.terminus_font ];

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
      git curl coreutils gnugrep gawk procps ncurses
      nix  # Required - nixos-install calls nix and nix-build internally
      dialog  # Professional TUI installer (mixedgauge, gauge, msgbox)
      figlet  # ASCII art banner for splash/completion screens
    ];

    script = ''
      set -e
      LOG_FILE="/tmp/msp-install.log"
      export DIALOGRC=""
      export TERM=linux
      export LANG=en_US.UTF-8

      log() {
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $1" >> "$LOG_FILE"
      }

      # ── dialog helper: update the mixedgauge step display ──────
      # Status codes: 0=Succeeded 1=Failed 7=In Progress 8=(blank) -N=N%
      S_DETECT=8; S_PARTITION=8; S_FORMAT=8; S_MOUNT=8
      S_HWCONFIG=8; S_INSTALL=8; S_VERIFY=8; S_REBOOT=8

      show_progress() {
        local overall_pct=$1
        local msg="$2"
        dialog --backtitle "OsirisCare MSP Compliance Platform  |  Appliance Installer v1.1" \
          --title " Installing to ''${INTERNAL_DEV:-???} (''${DEV_SIZE:-detecting...}) " \
          --mixedgauge "\n''${msg}" \
          20 70 "$overall_pct" \
          " Detect Hardware"            "$S_DETECT" \
          " Partition Drive"            "$S_PARTITION" \
          " Format Filesystems"         "$S_FORMAT" \
          " Mount Partitions"           "$S_MOUNT" \
          " Generate Hardware Config"   "$S_HWCONFIG" \
          " Install NixOS Appliance"    "$S_INSTALL" \
          " Verify Installation"        "$S_VERIFY" \
          " Reboot"                     "$S_REBOOT" \
          2>/dev/null || true
      }

      # ── Start installer ─────────────────────────────────────────
      printf '\033c'
      printf '\033[?25l'  # Hide cursor

      # Log block devices
      lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,MODEL >> "$LOG_FILE" 2>&1

      # Check if we're running from live ISO
      if ! grep -q "squashfs" /proc/mounts; then
        log "Not running from live ISO, skipping auto-install"
        exit 0
      fi

      # ── Splash screen ──────────────────────────────────────────
      printf '\033c'
      echo ""
      echo ""
      figlet -f slant "OsirisCare" 2>/dev/null || echo "  OsirisCare"
      echo ""
      echo -e "  \033[1;36m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m"
      echo -e "  \033[1;37m  MSP Compliance Platform — Appliance Installer\033[0m"
      echo -e "  \033[2m  HIPAA compliance automation for healthcare SMBs\033[0m"
      echo -e "  \033[1;36m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m"
      echo ""
      echo -e "  \033[2mScanning hardware...\033[0m"
      sleep 2

      # ── Step 1: Detect hardware ────────────────────────────────
      S_DETECT=7
      show_progress 5 "Scanning for internal drive..."
      log "Step 1: Detecting hardware"

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
        S_DETECT=1
        show_progress 0 "ERROR: No suitable internal drive found (need >16GB)"
        dialog --backtitle "OsirisCare MSP Compliance Platform" \
          --title " Installation Failed " \
          --msgbox "\nNo suitable internal drive found.\n\nAvailable drives:\n$(lsblk -d -o NAME,SIZE,TYPE,MODEL 2>/dev/null)\n\nPlease attach an internal drive (>16GB) and reboot." \
          18 65 2>/dev/null || true
        exit 0
      fi

      DEV_SIZE=$(numfmt --to=iec $(blockdev --getsize64 "$INTERNAL_DEV"))
      DEV_MODEL=$(lsblk -dno MODEL "$INTERNAL_DEV" 2>/dev/null | xargs)
      S_DETECT=0
      log "Found: $INTERNAL_DEV ($DEV_SIZE) $DEV_MODEL"

      # ── Check for existing installation ─────────────────────────
      NIXOS_PART=$(lsblk -rno NAME,LABEL "$INTERNAL_DEV" | grep nixos | head -1 | awk '{print $1}')
      if [ -n "$NIXOS_PART" ]; then
        TMPDIR=$(mktemp -d)
        if mount -o ro "/dev/$NIXOS_PART" "$TMPDIR" 2>/dev/null; then
          if [ -d "$TMPDIR/nix/store" ]; then
            umount "$TMPDIR"
            rmdir "$TMPDIR"
            if [ ! -f /tmp/force-reinstall ]; then
              dialog --backtitle "OsirisCare MSP Compliance Platform" \
                --title " Existing Installation Detected " \
                --yesno "\nAn OsirisCare appliance is already installed on $INTERNAL_DEV ($DEV_SIZE).\n\n  Choose an action:\n\n    Yes  =  Wipe and reinstall (ALL DATA ERASED)\n    No   =  Cancel (remove USB and reboot)\n\n  Debug console: Alt+F2 (root / osiris2024)" \
                16 65 2>/dev/null
              CHOICE=$?
              if [ "$CHOICE" -ne 0 ]; then
                dialog --backtitle "OsirisCare MSP Compliance Platform" \
                  --title " Installation Cancelled " \
                  --msgbox "\nRemove the USB installer and reboot to start\nthe existing appliance." \
                  9 55 2>/dev/null || true
                exit 0
              fi
              log "User chose to reinstall over existing installation"
            fi
            log "Force reinstall requested"
          fi
          umount "$TMPDIR" 2>/dev/null || true
        fi
        rmdir "$TMPDIR" 2>/dev/null || true
      fi

      show_progress 10 "Found: $INTERNAL_DEV ($DEV_SIZE) — $DEV_MODEL"
      sleep 1

      # ── Countdown before destructive operation ──────────────────
      for i in 10 9 8 7 6 5 4 3 2 1; do
        show_progress 10 "⚠  ALL DATA ON $INTERNAL_DEV WILL BE ERASED\n\n   Starting installation in $i seconds...\n   (Press Ctrl+C to cancel)"
        sleep 1
      done

      # ── Step 2: Partition ──────────────────────────────────────
      S_PARTITION=7
      show_progress 15 "Partitioning $INTERNAL_DEV..."
      log "Step 2: Partitioning $INTERNAL_DEV"

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

      S_PARTITION=0
      log "Partitioned: ESP=$ESP_PART Data=$DATA_PART Root=$ROOT_PART"

      # ── Step 3: Format ─────────────────────────────────────────
      S_FORMAT=7
      show_progress 25 "Formatting ESP partition (FAT32)..."
      log "Step 3: Formatting partitions"
      mkfs.fat -F32 -n ESP "$ESP_PART" >> "$LOG_FILE" 2>&1

      show_progress 28 "Formatting MSP-DATA partition (ext4, 2GB)..."
      mkfs.ext4 -L MSP-DATA -F "$DATA_PART" >> "$LOG_FILE" 2>&1

      show_progress 32 "Formatting root partition (ext4, ''${DEV_SIZE})..."
      mkfs.ext4 -L nixos -F "$ROOT_PART" >> "$LOG_FILE" 2>&1

      S_FORMAT=0
      log "Formatted all partitions"

      # ── Step 4: Mount ──────────────────────────────────────────
      S_MOUNT=7
      show_progress 38 "Mounting filesystems to /mnt..."
      log "Step 4: Mounting filesystems"

      mount "$ROOT_PART" /mnt
      mkdir -p /mnt/boot
      mount "$ESP_PART" /mnt/boot
      mkdir -p /mnt/var/lib/msp
      mount "$DATA_PART" /mnt/var/lib/msp

      S_MOUNT=0
      log "Mounted root, boot, data"

      # ── Step 5: Hardware config ────────────────────────────────
      S_HWCONFIG=7
      show_progress 42 "Generating NixOS hardware configuration..."
      log "Step 5: Generating hardware config"

      nixos-generate-config --root /mnt >> "$LOG_FILE" 2>&1

      S_HWCONFIG=0
      log "Hardware configuration generated"

      # ── Step 6: nixos-install (the big one) ─────────────────────
      S_INSTALL="-0"
      FLAKE_URL="github:jbouey/msp-flake#osiriscare-appliance-disk"
      show_progress 45 "Fetching OsirisCare appliance from GitHub...\n   This step takes 5-15 minutes."
      log "Step 6: nixos-install --flake $FLAKE_URL"

      nixos-install --flake "$FLAKE_URL" --no-root-passwd >> "$LOG_FILE" 2>&1 &
      INSTALL_PID=$!

      START_TIME=$(date +%s)
      while kill -0 $INSTALL_PID 2>/dev/null; do
        ELAPSED=$(( $(date +%s) - START_TIME ))
        MINS=$((ELAPSED / 60))
        SECS=$((ELAPSED % 60))

        # Estimate progress: 0-100% over ~10 minutes (600s)
        INSTALL_PCT=$((ELAPSED * 100 / 600))
        [ "$INSTALL_PCT" -gt 95 ] && INSTALL_PCT=95

        OVERALL=$((45 + INSTALL_PCT * 45 / 100))
        [ "$OVERALL" -gt 90 ] && OVERALL=90

        LAST_LINE=$(tail -1 "$LOG_FILE" 2>/dev/null | cut -c1-55)
        S_INSTALL="-$INSTALL_PCT"
        show_progress "$OVERALL" "Installing NixOS appliance...  (''${MINS}m ''${SECS}s elapsed)\n\n   ''${LAST_LINE}"
        sleep 3
      done

      wait $INSTALL_PID
      INSTALL_EXIT=$?

      if [ $INSTALL_EXIT -ne 0 ]; then
        S_INSTALL=1
        show_progress 0 "Installation failed!"
        dialog --backtitle "OsirisCare MSP Compliance Platform" \
          --title " Installation Failed " \
          --msgbox "\nnixos-install exited with code $INSTALL_EXIT.\n\nTo debug:\n  Alt+F2 → login as root (password: osiris2024)\n  cat /tmp/msp-install.log\n\nManual retry:\n  nixos-install --flake $FLAKE_URL --no-root-passwd" \
          16 65 2>/dev/null || true
        exit 1
      fi

      S_INSTALL=0
      log "nixos-install completed successfully"

      # ── Step 7: Verify ─────────────────────────────────────────
      S_VERIFY=7
      show_progress 93 "Verifying installation..."
      log "Step 7: Verifying installation"

      STORE_COUNT=$(ls /mnt/nix/store 2>/dev/null | wc -l)
      BOOT_OK="no"
      [ -d /mnt/boot/EFI ] && BOOT_OK="yes"
      log "Verified: $STORE_COUNT packages, boot=$BOOT_OK"
      ls -la /mnt/boot/ >> "$LOG_FILE" 2>/dev/null

      if [ "$BOOT_OK" != "yes" ]; then
        S_VERIFY=1
        show_progress 93 "Boot partition verification failed"
        sleep 5
      else
        S_VERIFY=0
      fi

      # ── Step 8: Cleanup & Reboot ───────────────────────────────
      S_REBOOT=7
      show_progress 97 "Unmounting filesystems..."

      umount -R /mnt
      log "Unmounted all filesystems"

      show_progress 100 "Installation complete!"
      sleep 1

      # ── Completion screen ───────────────────────────────────────
      printf '\033c'
      printf '\033[?25l'
      echo ""
      echo ""
      echo -e "  \033[1;32m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m"
      echo ""
      figlet -f slant "  Complete!" 2>/dev/null || echo -e "  \033[1;32mINSTALLATION COMPLETE!\033[0m"
      echo ""
      echo -e "  \033[1;32m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m"
      echo ""
      echo -e "  \033[1;37m  OsirisCare Compliance Appliance\033[0m"
      echo -e "  \033[2m  $STORE_COUNT packages installed to $INTERNAL_DEV ($DEV_SIZE)\033[0m"
      echo ""
      echo -e "  \033[1;36m  ┌─────────────────────────────────────────────────┐\033[0m"
      echo -e "  \033[1;36m  │\033[0m                                                 \033[1;36m│\033[0m"
      echo -e "  \033[1;36m  │\033[0m   \033[1;33m▸ Remove the USB drive now\033[0m                      \033[1;36m│\033[0m"
      echo -e "  \033[1;36m  │\033[0m                                                 \033[1;36m│\033[0m"
      echo -e "  \033[1;36m  │\033[0m   On next boot the appliance will:              \033[1;36m│\033[0m"
      echo -e "  \033[1;36m  │\033[0m     • Connect to Central Command                \033[1;36m│\033[0m"
      echo -e "  \033[1;36m  │\033[0m     • Auto-provision via MAC address             \033[1;36m│\033[0m"
      echo -e "  \033[1;36m  │\033[0m     • Begin HIPAA compliance monitoring          \033[1;36m│\033[0m"
      echo -e "  \033[1;36m  │\033[0m                                                 \033[1;36m│\033[0m"
      echo -e "  \033[1;36m  └─────────────────────────────────────────────────┘\033[0m"
      echo ""

      # Countdown with visible timer
      for i in 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1; do
        # Build a visual countdown bar
        BAR_FILLED=$((30 - i))
        BAR_EMPTY=$i
        BAR=""
        for ((b=0; b<BAR_FILLED; b++)); do BAR+="█"; done
        for ((b=0; b<BAR_EMPTY; b++)); do BAR+="░"; done
        echo -ne "\r  \033[2mRebooting in \033[1;37m$i\033[0;2ms  \033[36m[''${BAR}]\033[0m  "
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
      printf '\033c'
      echo -e "\033[2m── OsirisCare Install Log (Alt+F1 for main display) ──\033[0m"
      echo ""

      # Wait for log file to appear, then follow it
      while [ ! -f /tmp/msp-install.log ]; do
        echo -e "\033[2mWaiting for installer to start...\033[0m"
        sleep 2
      done
      exec tail -f /tmp/msp-install.log
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

# iso/appliance-image.nix
# Builds bootable installer ISO for MSP Compliance Appliance
#
# ZERO FRICTION INSTALL (3 steps):
# 1. Write ISO to USB
# 2. Boot target hardware
# 3. Auto-installs from embedded disk image → halt → done
#
# OFFLINE INSTALL - raw disk image is embedded in the ISO.
# No network required. dd + zstd writes the full appliance in ~30 seconds.

{ config, pkgs, lib, appliance-raw-image, builtFrom ? { git_sha = "unknown"; dirty = false; }, ... }:

let
  # Installer telemetry config.
  # installerToken: shared secret embedded in ISO. Backend requires this in
  #   X-Install-Token header. Not a strong secret (anyone with the ISO has
  #   access), but keeps internet scanners out of install_reports.
  # installerApiBase: Central Command base URL for telemetry POSTs.
  # To rotate: rebuild ISO with new env vars or override via flake.
  installerToken = "osiriscare-installer-dev-only";
  installerApiBase = "https://api.osiriscare.net";

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

  # Build the Go appliance daemon (replaces Python compliance-agent)
  # Single source of truth for the installer-ISO daemon version. Bump here
  # AND in appliance-disk-image.nix when the daemon binary version changes.
  daemonVersion = "0.4.5";

  appliance-daemon-go = pkgs.buildGoModule {
    pname = "appliance-daemon";
    version = daemonVersion;
    src = ../appliance;

    vendorHash = null;

    ldflags = [
      "-s" "-w"
      "-X github.com/osiriscare/appliance/internal/daemon.Version=${daemonVersion}"
    ];

    subPackages = [
      "cmd/appliance-daemon"
      "cmd/grpc-server"
      "cmd/checkin-receiver"
    ];

    CGO_ENABLED = "0";

    meta = with lib; {
      description = "OsirisCare Appliance Daemon (Go) - gRPC, L1 healing, phone-home";
      license = licenses.unfree;
    };
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
    npmDepsHash = "sha256-JbeTjY0oNJsh6xlmjQFif45WAojjW/9Q+9YObtN/AfM=";
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

  # ============================================================================
  # Embedded raw disk image — the full appliance, compressed
  # The raw image + decompressed size are baked into the ISO at build time.
  # appliance-raw-image is passed via specialArgs from flake.nix.
  # ============================================================================
  environment.etc."installer/osiriscare-system.raw.zst".source =
    "${appliance-raw-image}/osiriscare-system.raw.zst";
  environment.etc."installer/decompressed-size".source =
    "${appliance-raw-image}/decompressed-size";

  # Hardware compatibility matrix consumed by the installer at boot.
  # Format: one block per dmidecode system-product-name. Boxes not in
  # this list are halted with a clear "open a support ticket"
  # message rather than being attempted blindly. Adding a new
  # supported model is a 5-line PR.
  environment.etc."installer/supported_hardware.yaml".source =
    ./supported_hardware.yaml;

  # Build provenance — every ISO embeds the git commit it was built from.
  # On a failed install the user can run `cat /etc/osiriscare-build.json`
  # from the live TTY and we know exactly which source tree to debug.
  # Closes the round-table finding: 'osiriscare-v57-abandonment.iso had
  # no commit hash; the source tree it was built from was lost.'
  #
  # builtFrom is passed via specialArgs from flake.nix using self.rev /
  # self.dirtyRev. If you see git_sha=unknown, the ISO was built outside
  # the flake (legacy `nix-build` path).
  environment.etc."osiriscare-build.json".text = builtins.toJSON {
    git_sha = builtFrom.git_sha;
    git_dirty = builtFrom.dirty;
    installer_version = "v29";
    builder = "nix";
    note = "Run `cat /etc/osiriscare-build.json` from the live TTY shell on a failed install — the git_sha tells us which source tree to debug.";
  };

  # Boot with serial console for debugging.
  #
  # nosoftlockup: prevent false watchdog alarms during heavy dd I/O.
  # audit=0: disable kernel audit on live ISO (configuration.nix enables
  # auditd with execve logging which causes kauditd hold queue overflow
  # during boot).
  #
  # v20 (Session 206): STOP overriding vanilla kernel params.
  #
  # After iterating v13 → v14 → v15 → v16 → v17 → v18 → v19 (seven
  # custom kernel-cmdline attempts with multiple hardware hangs and
  # zero successful install_reports rows in 48h), the audit revealed
  # the override itself is the problem.
  #
  # Vanilla `installation-cd-minimal.nix` already ships:
  #   * `root=LABEL=<volumeID>` (required — we've been SHIPPING
  #     WITHOUT this by virtue of list-merge semantics being more
  #     subtle than I thought)
  #   * `boot.shell_on_fail` (drops to emergency shell instead of
  #     hanging silently — operator-friendly)
  #   * A multi-entry GRUB boot menu with fallbacks:
  #       - Default
  #       - `nomodeset` (GPU-safe)
  #       - `copytoram` (eject USB early)
  #       - `loglevel=7` (debug)
  #       - `console=ttyS0,115200n8` (serial)
  #   * All tested against thousands of NixOS deployments on
  #     heterogeneous hardware, including AMD Ryzen Embedded
  #     (21/21 working reports on linux-hardware.org for R1505G).
  #
  # Our job is NOT to second-guess the vanilla defaults. Let them
  # rule. An operator who hits a hardware edge case selects the
  # matching entry from the GRUB menu — upstream already provides
  # the options.
  #
  # The install SCRIPT (userspace) is where our value-add lives.
  # That stays. The backgrounded network flow from v17 stays. The
  # embedded raw image + dd logic stays. The auto-install service
  # stays. Zero-friction for end clients preserved.
  #
  # Do NOT add a `boot.kernelParams = [...]` override here. If a
  # hardware bug surfaces, document the specific GRUB entry the
  # operator should select in the runbook — don't monkeypatch the
  # default cmdline across the whole fleet.
  # Blacklist noisy Logitech HID++ driver — spams battery protocol errors on tty
  boot.blacklistedKernelModules = [ "hid_logitech_hidpp" ];
  boot.loader.timeout = lib.mkForce 3;

  # Readable console font for the installer TUI
  console.earlySetup = lib.mkForce true;
  console.font = lib.mkForce "${pkgs.terminus_font}/share/consolefonts/ter-v22n.psf.gz";
  console.packages = lib.mkForce [ pkgs.terminus_font ];

  # Disable hardware watchdog on live ISO — dd+zstd can saturate I/O
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
  # ============================================================================
  # AUTO-WIFI-CONFIG (v14, Session 206)
  # If the installer USB has a wifi.conf in the squashfs root (shipped
  # at ISO build time) OR on a secondary FAT32 partition, install it
  # to wpa_supplicant's config before wpa_supplicant.service starts.
  # Expected format:
  #   ssid=MyNetwork
  #   psk=mypassword
  # (PSK in plaintext is accepted for simplicity; wpa_passphrase can
  # be run separately to produce a hashed form if desired.)
  # ============================================================================
  systemd.services.auto-wifi-config = {
    description = "MSP Installer: auto-configure wifi from /iso/wifi.conf if present";
    wantedBy = [ "multi-user.target" ];
    before = [ "wpa_supplicant.service" ];
    wants = [ "local-fs.target" ];
    after = [ "local-fs.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      StandardOutput = "journal";
    };
    path = with pkgs; [ coreutils gnused gawk gnugrep ];
    script = ''
      set -u  # treat unset vars as errors (but not set -e — errors are non-fatal here)
      CONF_SRC=""
      # Probe the usual locations for a wifi.conf. The squashfs /iso
      # mount is the most reliable since it's always mounted when the
      # live ISO boots. A secondary FAT32 partition is a nicer UX
      # (operator drops the file on a running USB) but isn't always
      # auto-mounted, so we fall back to the live ISO's root.
      for cand in /iso/wifi.conf /wifi.conf /etc/wifi.conf; do
        if [ -f "$cand" ]; then
          CONF_SRC="$cand"
          break
        fi
      done
      if [ -z "$CONF_SRC" ]; then
        echo "auto-wifi-config: no wifi.conf found — skipping wifi setup"
        exit 0
      fi
      echo "auto-wifi-config: found $CONF_SRC"
      SSID=$(grep -E '^ssid=' "$CONF_SRC" 2>/dev/null | head -1 | cut -d= -f2-)
      PSK=$(grep -E '^psk=' "$CONF_SRC" 2>/dev/null | head -1 | cut -d= -f2-)
      if [ -z "$SSID" ]; then
        echo "auto-wifi-config: $CONF_SRC has no ssid= line — skipping"
        exit 0
      fi
      # Write wpa_supplicant config at the path networking.wireless expects.
      # Using open network if no PSK (for open wifi test networks).
      if [ -z "$PSK" ]; then
        echo "auto-wifi-config: writing open-network config for SSID=$SSID"
        cat > /etc/wpa_supplicant.conf <<EOF
ctrl_interface=/run/wpa_supplicant
ctrl_interface_group=wheel
update_config=1

network={
  ssid="$SSID"
  key_mgmt=NONE
}
EOF
      else
        echo "auto-wifi-config: writing WPA2-PSK config for SSID=$SSID"
        cat > /etc/wpa_supplicant.conf <<EOF
ctrl_interface=/run/wpa_supplicant
ctrl_interface_group=wheel
update_config=1

network={
  ssid="$SSID"
  psk="$PSK"
  key_mgmt=WPA-PSK
}
EOF
      fi
      chmod 600 /etc/wpa_supplicant.conf
      echo "auto-wifi-config: /etc/wpa_supplicant.conf written"
    '';
  };

  # ============================================================================
  # ZERO FRICTION AUTO-INSTALL SERVICE
  # Detects internal drive, writes embedded raw image via dd+zstd
  # No network required — full NixOS closure is on the ISO
  # ============================================================================
  systemd.services.msp-auto-install = {
    description = "MSP Appliance Zero-Friction Auto-Install";
    wantedBy = [ "multi-user.target" ];
    after = [ "systemd-vconsole-setup.service" "local-fs.target" ];
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
      util-linux      # lsblk, blockdev, findmnt, partprobe, mount, umount, uuidgen, wipefs
      parted          # partprobe (kernel re-read after dd)
      gptfdisk        # sgdisk — zap GPT/MBR pre-install (v13, Session 206)
      wpa_supplicant  # v14: wifi support for operator fallback when eth dead
      iw              # v14: wifi scan/connect/debug
      wirelesstools   # v14: iwconfig for legacy cards
      dosfstools      # fsck.vfat (not used in dd flow but harmless)
      e2fsprogs       # e2fsck, resize2fs (root partition resize)
      coreutils gnugrep gawk procps
      pv              # pipe viewer — progress bar for dd
      zstd            # zstdcat for decompressing raw image
      jq              # JSON parsing for sfdisk --json partition table
      figlet          # ASCII art banner for splash/completion screens
      # --- Telemetry/diagnostics (installer v10) ---
      dmidecode       # BIOS vendor, product name, serial number
      smartmontools   # SMART status on internal drive
      iputils         # ping
      curl            # POST install reports, test API reachability
      dnsutils        # dig for DNS test
      ntp             # ntpdate for clock sync before HTTPS
      # sbctl not needed — installed system generates Secure Boot keys on first boot
    ];

    script = ''
      set -euo pipefail
      LOG_FILE="/tmp/msp-install.log"
      # v23 diagnostic: also mirror stdout+stderr to the shared log so
      # failures before the first progress-bar redraw are recoverable
      # after reboot. Systemd still sends stderr to the journal; stdout
      # still goes to tty1 for the live display. Tee adds the third
      # copy to /tmp/msp-install.log.
      mkdir -p /tmp
      exec >  >(tee -a "$LOG_FILE")
      exec 2> >(tee -a "$LOG_FILE" >&2)
      export TERM=linux
      export LANG=en_US.UTF-8
      INSTALLER_VERSION="v29"
      INSTALL_TOKEN="${installerToken}"
      API_BASE="${installerApiBase}"
      # v17 (Session 206): enterprise install flow — NEVER blocks on network.
      # All telemetry calls run in the background via this helper. If the
      # backgrounded call stalls forever, the install still completes.
      # The backgrounded PID gets reaped at install halt by systemd cleanup.
      run_bg_telemetry() {
        # $@ is the command to run. We fork twice (subshell + &) so the
        # backgrounded process is fully detached from the install shell.
        ( "$@" >> "$LOG_FILE" 2>&1 & ) >/dev/null 2>&1 || true
      }
      # v17: State directory for install phase transitions — readable by
      # future separate telemetry service, preserved across reboots on
      # the live ISO (tmpfs). Currently unused but structured for Phase 2
      # refactor where telemetry moves fully out of install.sh.
      mkdir -p /run/installer-state 2>/dev/null || true

      IMAGE="/etc/installer/osiriscare-system.raw.zst"
      DECOMPRESSED_SIZE_FILE="/etc/installer/decompressed-size"

      log() {
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $1" >> "$LOG_FILE"
      }

      # ── ANSI display helpers (NO dialog) ──────────────────────
      RED='\033[1;31m'
      GREEN='\033[1;32m'
      YELLOW='\033[1;33m'
      CYAN='\033[1;36m'
      WHITE='\033[1;37m'
      DIM='\033[2m'
      RESET='\033[0m'

      clear_screen() {
        printf '\033c'
        printf '\033[?25l'  # Hide cursor
      }

      # Draw the installer header + progress bar
      # Usage: draw_progress PERCENT "Status line..." ["Detail line..."]
      draw_progress() {
        local pct=$1
        local status="$2"
        local detail="''${3:-}"

        # Clamp percentage
        [ "$pct" -lt 0 ] 2>/dev/null && pct=0
        [ "$pct" -gt 100 ] 2>/dev/null && pct=100

        # Build progress bar (20 chars wide)
        local bar_width=20
        local filled=$(( pct * bar_width / 100 ))
        local empty=$(( bar_width - filled ))
        local bar=""
        for ((i=0; i<filled; i++)); do bar+="█"; done
        for ((i=0; i<empty; i++)); do bar+="░"; done

        # Move to top of screen and redraw (flicker-free)
        printf '\033[H'    # cursor home
        printf '\033[J'    # clear to end
        echo ""
        echo -e "  ''${CYAN}OsirisCare Installer ''${INSTALLER_VERSION}''${RESET}"
        echo -e "  ''${DIM}Target: ''${DEV_DESC:-detecting...}''${RESET}"
        echo ""
        echo -e "  ''${CYAN}''${bar}''${RESET}  ''${WHITE}''${pct}%''${RESET}"
        echo -e "  ''${status}"
        if [ -n "$detail" ]; then
          echo -e "  ''${DIM}''${detail}''${RESET}"
        fi
        echo ""
      }

      die() {
        local msg="$1"
        local step="''${2:-unknown}"
        clear_screen
        echo ""
        echo ""
        echo -e "  ''${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━''${RESET}"
        echo ""
        echo -e "  ''${RED}INSTALLATION FAILED''${RESET}"
        echo ""
        echo -e "  ''${WHITE}$msg''${RESET}"
        echo ""
        echo -e "  ''${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━''${RESET}"
        echo ""
        if [ -n "''${HW_SERIAL:-}" ]; then
          echo -e "  ''${DIM}Device serial: ''${HW_SERIAL}''${RESET}"
        fi
        if [ -n "''${INSTALLER_ID:-}" ]; then
          echo -e "  ''${DIM}Install ID:    ''${INSTALLER_ID}''${RESET}"
        fi
        echo -e "  ''${DIM}Support:       support@osiriscare.net''${RESET}"
        echo -e "  ''${DIM}Debug shell:   Alt+F2 → root / osiris2024''${RESET}"
        echo -e "  ''${DIM}Full log:      cat /tmp/msp-install.log''${RESET}"
        echo ""
        log "FATAL: $msg (step=$step)"
        # v17: Fire failure report in background so die() never stalls
        # on curl. If the curl hangs, the backgrounded subshell is
        # orphaned when sleep 86400 is finally SIGKILL'd by systemd.
        ( post_complete_report "false" "$step" "$msg" ) >> "$LOG_FILE" 2>&1 &
        disown 2>/dev/null || true
        # Halt — keep error visible, then exit
        sleep 86400
        exit 1
      }

      # ────────────────────────────────────────────────────────────
      # Installer v10 telemetry — pre-boot install reports
      # ────────────────────────────────────────────────────────────

      # Collect hardware fingerprint via dmidecode + lsblk + smartctl.
      # Sets INSTALLER_ID and HW_* vars used by install reports.
      collect_hw_info() {
        INSTALLER_ID=$(uuidgen 2>/dev/null || cat /proc/sys/kernel/random/uuid)
        HW_SERIAL=$(dmidecode -s system-serial-number 2>/dev/null | head -1 | tr -d '\n' || echo "")
        HW_PRODUCT=$(dmidecode -s system-product-name 2>/dev/null | head -1 | tr -d '\n' || echo "")
        HW_BIOS_VENDOR=$(dmidecode -s bios-vendor 2>/dev/null | head -1 | tr -d '\n' || echo "")
        HW_BIOS_VERSION=$(dmidecode -s bios-version 2>/dev/null | head -1 | tr -d '\n' || echo "")
        HW_CPU=$(grep -m1 "^model name" /proc/cpuinfo 2>/dev/null | sed 's/.*: //' | tr -d '\n' || echo "")
        local mem_kb=$(grep -m1 "^MemTotal" /proc/meminfo 2>/dev/null | awk '{print $2}')
        HW_MEMORY_GB=$(( mem_kb / 1024 / 1024 ))
        # MAC of primary interface (best-effort)
        HW_MAC=$(ip -o link show 2>/dev/null | awk -F'[ :]+' '$2 != "lo" && /link\/ether/ {print $20; exit}' || echo "")
        log "HW probe: serial=$HW_SERIAL product=$HW_PRODUCT bios=$HW_BIOS_VENDOR/$HW_BIOS_VERSION cpu=$HW_CPU ram=''${HW_MEMORY_GB}GB mac=$HW_MAC"
      }

      # Probe the target drive — must be called after INTERNAL_DEV is set.
      # v11 probe: sysfs-only. No blockdev ioctl, no smartctl. Both of
      # those can hang on misbehaving hardware; sysfs reads are kernel-
      # cached and cannot. We set DRIVE_SMART=SKIPPED to signal to the
      # telemetry payload that we did not probe SMART on this pre-flight.
      # Post-install SMART collection happens via the running daemon
      # after the system boots, where a hung smartctl is inconsequential.
      probe_drive() {
        local dev="$1"
        local name=$(basename "$dev")
        case "$name" in mmcblk*) ;; *) name=$(echo "$name" | sed 's/[0-9]*$//') ;; esac
        DRIVE_PATH="$dev"
        DRIVE_MODEL=$(sysfs_read "/sys/block/$name/device/model" "unknown" | tr -d ' \n')
        # /sys/block/*/size is in 512-byte sectors
        local sectors
        sectors=$(sysfs_read "/sys/block/$name/size" 0 | tr -d ' \n')
        DRIVE_SIZE_GB=$(( sectors * 512 / 1024 / 1024 / 1024 ))
        local removable_raw
        removable_raw=$(sysfs_read "/sys/block/$name/removable" "0" | tr -d ' \n')
        [ "$removable_raw" = "1" ] && DRIVE_REMOVABLE="true" || DRIVE_REMOVABLE="false"
        DRIVE_SMART="SKIPPED"
        log "Drive probe (sysfs): path=$DRIVE_PATH model=$DRIVE_MODEL size=''${DRIVE_SIZE_GB}GB removable=$DRIVE_REMOVABLE"
      }

      # Check network readiness — DHCP, gateway, DNS, NTP, API reachability.
      # Sets NET_* vars. Does NOT fail the install; records issues for telemetry.
      check_network() {
        NET_IFACE=$(ip -o -4 route show default 2>/dev/null | awk '{print $5; exit}' || echo "")
        NET_IP=$(ip -o -4 addr show "''${NET_IFACE:-eth0}" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1 || echo "")
        NET_GATEWAY=$(ip -o -4 route show default 2>/dev/null | awk '{print $3; exit}' || echo "")
        NET_DNS=$(grep -E '^nameserver ' /etc/resolv.conf 2>/dev/null | awk '{print $2}' | head -3 | tr '\n' ',' | sed 's/,$//' || echo "")
        NET_MTU=$(cat /sys/class/net/''${NET_IFACE:-eth0}/mtu 2>/dev/null || echo "0")

        # DHCP success = we got an IP
        if [ -n "$NET_IP" ]; then NET_DHCP="true"; else NET_DHCP="false"; fi

        # NTP sync — try once, best-effort
        if timeout 15 ntpdate -u pool.ntp.org >/dev/null 2>&1; then
          NET_NTP="true"
        else
          NET_NTP="false"
        fi

        # API reachability — tries curl with short timeout, accepts any 2xx/3xx/4xx
        # (the endpoint may require auth but reachability is what we're testing)
        if curl -s -m 10 -o /dev/null -w "%{http_code}" "''${API_BASE}/health" 2>/dev/null | grep -qE "^[234]"; then
          NET_API="true"
        else
          NET_API="false"
        fi

        log "Network probe: iface=$NET_IFACE ip=$NET_IP gw=$NET_GATEWAY dns=$NET_DNS mtu=$NET_MTU dhcp=$NET_DHCP ntp=$NET_NTP api=$NET_API"
      }

      # POST install_start report. Best-effort — never blocks install on failure.
      post_start_report() {
        [ "''${NET_API:-false}" = "true" ] || { log "Skipping start report — API unreachable"; return 0; }

        # Build DNS array as JSON
        local dns_json='[]'
        if [ -n "''${NET_DNS:-}" ]; then
          dns_json=$(echo "$NET_DNS" | awk -F, '{printf "["; for(i=1;i<=NF;i++){printf "\"%s\"%s", $i, (i<NF?",":"")}; printf "]"}')
        fi

        local body
        body=$(cat <<JSONEND
{
  "installer_id": "''${INSTALLER_ID}",
  "installer_version": "''${INSTALLER_VERSION}",
  "hardware": {
    "mac_address": "''${HW_MAC}",
    "serial_number": "''${HW_SERIAL}",
    "bios_vendor": "''${HW_BIOS_VENDOR}",
    "bios_version": "''${HW_BIOS_VERSION}",
    "product_name": "''${HW_PRODUCT}",
    "cpu_model": "''${HW_CPU}",
    "memory_gb": ''${HW_MEMORY_GB:-0},
    "drive_path": "''${DRIVE_PATH:-}",
    "drive_model": "''${DRIVE_MODEL:-}",
    "drive_size_gb": ''${DRIVE_SIZE_GB:-0},
    "drive_smart_status": "''${DRIVE_SMART:-UNKNOWN}",
    "drive_is_removable": ''${DRIVE_REMOVABLE:-false}
  },
  "network": {
    "iface": "''${NET_IFACE}",
    "ip": "''${NET_IP}",
    "gateway": "''${NET_GATEWAY}",
    "dns": $dns_json,
    "mtu": ''${NET_MTU:-0},
    "dhcp_success": ''${NET_DHCP:-false},
    "ntp_synced": ''${NET_NTP:-false},
    "api_reachable": ''${NET_API:-false}
  }
}
JSONEND
)
        echo "$body" > /tmp/msp-install-start.json
        # v17: tighter timeouts — connect 5s, total 10s. No retries.
        # curl failures are benign (install runs offline); we don't want
        # the backgrounded subshell to linger on bad networks.
        curl -sS --connect-timeout 5 -m 10 -X POST \
          -H "Content-Type: application/json" \
          -H "X-Install-Token: ''${INSTALL_TOKEN}" \
          --data @/tmp/msp-install-start.json \
          "''${API_BASE}/api/install/report/start" >> "$LOG_FILE" 2>&1 || log "post_start_report: curl failed (non-fatal)"
      }

      # Track install start time for duration calculation
      INSTALL_START_EPOCH=$(date +%s)

      # POST install_complete report. Non-blocking.
      # Args: success (true|false), error_step, error_msg, [image_sha256], [verify_sha256]
      post_complete_report() {
        [ "''${NET_API:-false}" = "true" ] || return 0
        local success="$1"
        local error_step="''${2:-}"
        local error_msg="''${3:-}"
        local image_sha="''${4:-}"
        local verify_sha="''${5:-}"
        local duration=$(( $(date +%s) - INSTALL_START_EPOCH ))

        # Escape quotes in error_msg for JSON
        error_msg=$(echo "$error_msg" | sed 's/"/\\"/g')

        local body
        body=$(cat <<JSONEND
{
  "installer_id": "''${INSTALLER_ID}",
  "success": ''${success},
  "error_step": "''${error_step}",
  "error_message": "''${error_msg}",
  "image_sha256": "''${image_sha}",
  "verify_sha256": "''${verify_sha}",
  "duration_s": ''${duration}
}
JSONEND
)
        echo "$body" > /tmp/msp-install-complete.json
        # v17: tighter timeouts matching post_start_report.
        curl -sS --connect-timeout 5 -m 10 -X POST \
          -H "Content-Type: application/json" \
          -H "X-Install-Token: ''${INSTALL_TOKEN}" \
          --data @/tmp/msp-install-complete.json \
          "''${API_BASE}/api/install/report/complete" >> "$LOG_FILE" 2>&1 || log "post_complete_report: curl failed (non-fatal)"
      }

      # Verify the image was written correctly by reading back and computing SHA256.
      # Compares first 100MB + last 100MB samples. Full-disk SHA would take minutes.
      # Args: device (e.g. /dev/sda)
      # Echoes the computed SHA256 to stdout.
      verify_post_write() {
        local dev="$1"
        log "Post-write verification: reading back samples from $dev"
        local head_sha
        local tail_sha
        head_sha=$(dd if="$dev" bs=1M count=100 status=none 2>/dev/null | sha256sum | awk '{print $1}')
        # Read the last 100MB — need to calculate offset
        local total_mb=$(blockdev --getsize64 "$dev" 2>/dev/null | awk '{print int($1/1024/1024)}')
        local skip=$(( total_mb - 100 ))
        tail_sha=$(dd if="$dev" bs=1M count=100 skip=$skip status=none 2>/dev/null | sha256sum | awk '{print $1}')
        log "Post-write verification: head_sha=$head_sha tail_sha=$tail_sha"
        # Composite: hash of both hashes concatenated
        echo "$head_sha+$tail_sha" | sha256sum | awk '{print $1}'
      }

      # ────────────────────────────────────────────────────────────
      # v11 STEP SUPERVISOR + HEARTBEAT DAEMON
      # ────────────────────────────────────────────────────────────
      # Every step through this installer is bounded and observable.
      # (a) `set_step N "text"` records state to a heartbeat file and logs.
      # (b) `run_bounded BUDGET cmd…` wraps any external call in `timeout`
      #     so a hung syscall cannot freeze the installer.
      # (c) A background daemon repaints the progress bar every second
      #     with a live "Ns elapsed" counter. If the main script blocks
      #     in an uninterruptible syscall, the screen still updates.
      # This replaces the v10 pattern where `draw_progress 4` painted
      # once and never refreshed — leading to the "stuck at 4%" class
      # of bug when a downstream call silently hung.
      HEARTBEAT_STATE=/tmp/msp-install.state
      HEARTBEAT_PID=""

      set_step() {
        local step="$1" status="$2"
        {
          echo "HB_STEP=$step"
          printf 'HB_STATUS=%q\n' "$status"
          echo "HB_STEP_START=$(date +%s)"
        } > "$HEARTBEAT_STATE"
        log "[STEP $step] $status"
      }

      # Hard-bound any command. SIGTERM at budget, SIGKILL 3s later.
      # Returns 124 on timeout. Logs every invocation with duration.
      # NOTE: this cannot save us from processes in uninterruptible sleep
      # (kernel state D). For that class of hang, use bounded_abandon
      # which forks + forgets rather than trying to kill.
      run_bounded() {
        local budget="$1"; shift
        local name="$1"; shift
        local start end rc duration
        start=$(date +%s)
        timeout --kill-after=3s "''${budget}s" "$@"
        rc=$?
        end=$(date +%s)
        duration=$(( end - start ))
        case $rc in
          0)    log "  [$name ok] ''${duration}s (budget ''${budget}s)" ;;
          124)  log "  [$name TIMEOUT] exceeded ''${budget}s — continuing" ;;
          *)    log "  [$name rc=$rc] ''${duration}s" ;;
        esac
        return $rc
      }

      # Fork a command, wait up to `budget` seconds, ABANDON on timeout.
      # Unlike run_bounded, this survives kernel state D: instead of trying
      # to kill a process that won't die (SIGKILL is deferred inside an
      # uninterruptible syscall), we simply leave the child behind and
      # continue the installer. The child gets reaped when the installer
      # process exits (or the box reboots).
      #
      # This is the ONLY correct way to bound mount, partprobe, sfdisk,
      # fsck, resize2fs, mkfs, smartctl, dmidecode, blockdev, and any
      # other syscall that does hardware I/O — all of which can enter
      # state D on misbehaving hardware.
      #
      # Return: 0 on success, <n> on child exit with code n, 124 on abandon.
      # Args: budget_seconds name cmd [args...]
      bounded_abandon() {
        local budget="$1"; shift
        local name="$1"; shift
        local start end duration waited pid rc
        start=$(date +%s)
        # Use setsid so the child is in its own process group — orphaned
        # cleanly when we abandon it (doesn't receive our signals).
        # Output goes to the install log so TTY2 shows command progress.
        setsid "$@" </dev/null >>"$LOG_FILE" 2>&1 &
        pid=$!
        waited=0
        while [ $waited -lt $budget ]; do
          if ! kill -0 "$pid" 2>/dev/null; then
            wait "$pid" 2>/dev/null
            rc=$?
            end=$(date +%s)
            duration=$(( end - start ))
            if [ "$rc" = "0" ]; then
              log "  [$name ok] ''${duration}s (budget ''${budget}s)"
            else
              log "  [$name rc=$rc] ''${duration}s"
            fi
            return $rc
          fi
          sleep 1
          waited=$((waited + 1))
        done
        end=$(date +%s)
        duration=$(( end - start ))
        log "  [$name ABANDONED] exceeded ''${budget}s — leaving pid=$pid behind, continuing"
        disown "$pid" 2>/dev/null || true
        return 124
      }

      # Read an integer from a sysfs file. Never hangs — sysfs is
      # kernel-cached; the cat is a userspace file read. Use this
      # instead of blockdev --getsize64 or similar ioctls that can
      # block on misbehaving hardware.
      sysfs_read() {
        # ALWAYS succeeds. Echos the default if the path is unreadable.
        # Previously returned 1 on missing path, which under
        # set -euo pipefail killed callers like
        # VAR=$(sysfs_read ... | tr ...) — observed on eMMC drives
        # (HP t740) where /sys/block/mmcblk0/device/{model,vendor}
        # don't exist.
        local path="$1" default="''${2:-0}"
        if [ -r "$path" ]; then
          cat "$path" 2>/dev/null || echo "$default"
        else
          echo "$default"
        fi
        return 0
      }

      start_heartbeat_daemon() {
        (
          # Subshell: run defensively so a single bad sourcing of the
          # state file (race during rewrite) can't crash the daemon.
          set +e
          set +u
          trap 'exit 0' SIGTERM SIGINT
          local last_log=0
          while true; do
            sleep 1
            [ -f "$HEARTBEAT_STATE" ] || continue
            local HB_STEP="" HB_STATUS="" HB_STEP_START=0
            # shellcheck disable=SC1090
            . "$HEARTBEAT_STATE" 2>/dev/null || continue
            local now=$(date +%s)
            local elapsed=$(( now - ''${HB_STEP_START:-$now} ))
            draw_progress "$HB_STEP" "$HB_STATUS" "''${elapsed}s elapsed"
            if [ $(( now - last_log )) -ge 15 ]; then
              log "[HEARTBEAT] step=$HB_STEP elapsed=''${elapsed}s"
              last_log=$now
            fi
          done
        ) &
        HEARTBEAT_PID=$!
        disown
        log "heartbeat daemon pid=$HEARTBEAT_PID"
      }

      stop_heartbeat_daemon() {
        [ -n "$HEARTBEAT_PID" ] && kill "$HEARTBEAT_PID" 2>/dev/null || true
        rm -f "$HEARTBEAT_STATE"
      }

      # Ensure the heartbeat daemon is cleaned up no matter how we exit.
      trap 'stop_heartbeat_daemon' EXIT

      # ────────────────────────────────────────────────────────────
      # v11 DRIVE DISCOVERY — enumerate /sys/block, pick the largest non-USB
      # disk ≥20GB. Enterprise rule: accept any drive that looks like a real
      # disk; the only things we exclude are pseudo-devices, the USB we
      # booted from, and anything too small to hold the system.
      # ────────────────────────────────────────────────────────────
      # Prior versions keyed off `/sys/block/*/removable`, which is unreliable
      # on laptop/SFF hardware (e.g. SSSTC CV8-8B128-HP reports removable=1
      # via its hot-pluggable SATA port). Enterprise hardware detection means
      # "use the boot source, not a kernel hint that's wrong in the field."
      detect_internal_drive() {
        local best_dev="" best_size=0 iso_src iso_disk
        # Resolve the physical disk holding the live ISO so we can exclude it.
        # `/iso` is where installation-cd-minimal.nix mounts the installer
        # squashfs source. `findmnt` gives us e.g. `/dev/sdb1`; we strip the
        # partition suffix to get the whole disk (`sdb`).
        iso_src=$(findmnt -n -o SOURCE /iso 2>/dev/null || true)
        if [ -z "$iso_src" ]; then
          iso_src=$(awk '$2 == "/iso" {print $1; exit}' /proc/mounts 2>/dev/null || true)
        fi
        iso_disk=""
        if [ -n "$iso_src" ]; then
          # /dev/sdb1 → sdb ; /dev/nvme0n1p1 → nvme0n1 ; /dev/mmcblk0p1 → mmcblk0
          iso_disk=$(echo "$iso_src" \
            | sed 's|/dev/||' \
            | sed -E 's/p?[0-9]+$//')
        fi
        log "drive-detect: iso_src=$iso_src iso_disk=$iso_disk"

        local block name dev size_bytes size_gb removable rotational bus
        for block in /sys/block/*; do
          name=$(basename "$block")
          dev="/dev/$name"

          case "$name" in
            loop*|zram*|sr*|fd*|ram*|dm-*|md*|nbd*)
              log "  [$name] skip: pseudo/ephemeral"
              continue ;;
          esac

          [ -b "$dev" ] || { log "  [$name] skip: not block device"; continue; }

          if [ -n "$iso_disk" ] && [ "$name" = "$iso_disk" ]; then
            log "  [$name] skip: boot USB (serving /iso from $iso_src)"
            continue
          fi

          # sysfs-first: /sys/block/*/size is in 512-byte sectors.
          # Avoids blockdev --getsize64 which can hang on flaky SATA/USB.
          local sectors
          sectors=$(sysfs_read "$block/size" 0)
          size_bytes=$(( sectors * 512 ))
          size_gb=$(( size_bytes / 1024 / 1024 / 1024 ))

          if [ "$size_bytes" -lt 20000000000 ]; then
            log "  [$name] skip: too small (''${size_gb}GB, need ≥20GB)"
            continue
          fi

          removable=$(sysfs_read "$block/removable" "?")
          rotational=$(sysfs_read "$block/queue/rotational" "?")
          bus=$(sysfs_read "$block/device/vendor" "?" | tr -d ' \n')
          log "  [$name] CANDIDATE size=''${size_gb}GB removable=$removable rotational=$rotational vendor=$bus"

          if [ "$size_bytes" -gt "$best_size" ]; then
            best_size=$size_bytes
            best_dev=$dev
          fi
        done

        if [ -n "$best_dev" ]; then
          log "drive-detect: selected $best_dev ($(( best_size / 1024 / 1024 / 1024 ))GB)"
          INTERNAL_DEV="$best_dev"
          return 0
        fi

        log "drive-detect: NO SUITABLE DRIVE FOUND"
        return 1
      }

      # ── Start installer ─────────────────────────────────────────
      clear_screen

      # Log block devices
      lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,MODEL >> "$LOG_FILE" 2>&1

      # Kick off the heartbeat daemon immediately so every subsequent step
      # inherits live repaint + logging.
      set_step 1 "''${WHITE}Collecting hardware profile...''${RESET}"
      start_heartbeat_daemon

      # NOTE: run_bounded uses `timeout`, which only works on external
      # commands — not shell functions. Shell functions are called
      # directly here; they rely on their own internal timeouts
      # (curl -m, ntpdate timeout, etc.) to stay bounded.

      # v10: Collect hardware fingerprint EARLY, before any install work.
      collect_hw_info || true

      # v25 hardware compatibility gate. Runs after collect_hw_info
      # so HW_PRODUCT is set. Reads /etc/installer/supported_hardware.yaml
      # (shipped in the ISO) and halts with a clear message if the
      # detected model isn't on the certified list. Better than
      # bricking a customer's box with an unverified install.
      check_hardware_compat() {
        local hw_yaml="/etc/installer/supported_hardware.yaml"
        if [ ! -f "$hw_yaml" ]; then
          log "HW compat: matrix file missing — skipping check (legacy ISO?)"
          return 0
        fi
        if [ -z "$HW_PRODUCT" ]; then
          log "HW compat: dmidecode product unknown — skipping check"
          return 0
        fi
        local entry
        entry=$(${pkgs.yq}/bin/yq -r --arg k "$HW_PRODUCT" '.models[$k] // empty' "$hw_yaml" 2>/dev/null || echo "")
        if [ -z "$entry" ]; then
          log "HW compat: $HW_PRODUCT NOT on certified list — halting install"
          # Pretty halt screen — operator can read it on tty1.
          clear_screen
          echo ""
          echo -e "  ''${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━''${RESET}"
          echo -e "  ''${WHITE}  HARDWARE NOT CERTIFIED — install aborted''${RESET}"
          echo -e "  ''${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━''${RESET}"
          echo ""
          echo -e "  Detected: ''${YELLOW}$HW_PRODUCT''${RESET}"
          echo ""
          echo -e "  This model is not on the OsirisCare certified hardware list."
          echo -e "  Installing anyway risks bricking the device."
          echo ""
          echo -e "  ''${WHITE}Action:''${RESET}"
          echo -e "    1. Open a support ticket at support@osiriscare.net"
          echo -e "    2. Include this product string in the body."
          echo -e "    3. We'll certify the model and ship a v''${INSTALLER_VERSION}+ ISO."
          echo ""
          echo -e "  ''${DIM}Drop to TTY shell with Alt+F2 to read /tmp/msp-install.log''${RESET}"
          echo ""
          # Keep TTY alive for operator inspection — no hard exit so
          # they can browse the log + share screen photos.
          sleep 86400
          exit 1
        fi
        local tested
        tested=$(${pkgs.yq}/bin/yq -r --arg k "$HW_PRODUCT" '.models[$k].tested' "$hw_yaml" 2>/dev/null || echo "false")
        if [ "$tested" != "true" ]; then
          log "HW compat: $HW_PRODUCT listed but tested=false — halting install"
          clear_screen
          echo ""
          echo -e "  ''${YELLOW}HARDWARE PARTIALLY KNOWN, NOT YET CERTIFIED''${RESET}"
          echo -e "  Model: $HW_PRODUCT"
          echo -e "  Status from matrix: tested=false"
          echo -e "  ''${DIM}Same support path as above.''${RESET}"
          sleep 86400
          exit 1
        fi
        log "HW compat: $HW_PRODUCT certified (tested=true) — proceeding"
      }
      check_hardware_compat

      # v17 (Session 206): enterprise install flow — network is 100%
      # OPTIONAL for install. The raw image is embedded in the ISO;
      # install completes fully offline. Telemetry is best-effort and
      # runs in the background.
      #
      # Old flow (v10-v16):
      #   DHCP wait gate (30s) → check_network (10s) → post_start_report (15s)
      #   = up to 55s in the hot path even on a successful install, and an
      #   UNBOUNDED stall if any curl retried past its timeout. Observed
      #   2026-04-14 on HP t640 where install appeared frozen at STEP 3
      #   even though v15 was "working."
      #
      # New flow:
      #   Network probe + start-report run DETACHED in background via a
      #   subshell (which inherits function definitions from this bash
      #   shell). Disk wipe + dd image write proceeds immediately.
      #   Install duration becomes independent of network state.
      set_step 3 "''${WHITE}Starting background network probe...''${RESET}"
      log "v17: network probe + start-report dispatched to background — install flow continues"
      ( check_network; post_start_report ) >> "$LOG_FILE" 2>&1 &
      disown 2>/dev/null || true

      # Check if we're running from live ISO
      if ! grep -q "squashfs" /proc/mounts; then
        log "Not running from live ISO, skipping auto-install"
        exit 0
      fi

      # Verify raw image exists on the ISO
      if [ ! -f "$IMAGE" ]; then
        die "Raw image not found at $IMAGE. ISO may be corrupt."
      fi
      if [ ! -f "$DECOMPRESSED_SIZE_FILE" ]; then
        die "Decompressed size file not found. ISO may be corrupt."
      fi
      DECOMPRESSED_BYTES=$(cat "$DECOMPRESSED_SIZE_FILE")
      log "Raw image: $IMAGE (decompressed: $DECOMPRESSED_BYTES bytes)"

      # ── Splash screen ──────────────────────────────────────────
      clear_screen
      echo ""
      echo ""
      figlet -f slant "OsirisCare" 2>/dev/null || echo "  OsirisCare"
      echo ""
      echo -e "  ''${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━''${RESET}"
      echo -e "  ''${WHITE}  MSP Compliance Platform — Appliance Installer ''${INSTALLER_VERSION}''${RESET}"
      echo -e "  ''${DIM}  Offline install — no network required''${RESET}"
      echo -e "  ''${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━''${RESET}"
      echo ""
      echo -e "  ''${DIM}Scanning hardware...''${RESET}"
      sleep 2

      # ── Step 1: Detect hardware ────────────────────────────────
      log "Step 1: Detecting hardware"

      set_step 4 "''${WHITE}Scanning for installation target...''${RESET}"
      INTERNAL_DEV=""
      # Shell function — called directly, not via run_bounded (timeout
      # can't invoke functions). Internal ops are all shell builtins
      # + blockdev/findmnt which don't hang.
      if ! detect_internal_drive; then
        stop_heartbeat_daemon
        clear_screen
        echo ""
        echo -e "  ''${RED}No suitable internal drive found (need ≥20GB)''${RESET}"
        echo ""
        lsblk -d -o NAME,SIZE,TYPE,MODEL 2>/dev/null | while IFS= read -r line; do
          echo "  $line"
        done
        echo ""
        echo -e "  ''${WHITE}Build provenance:''${RESET}"
        cat /etc/osiriscare-build.json 2>/dev/null | sed 's/^/  /' || echo "  (not present)"
        echo ""
        echo -e "  ''${WHITE}Install log:''${RESET} /tmp/msp-install.log"
        echo -e "  ''${WHITE}Drop-to-shell:''${RESET} dropping you into a bash login shell so you can investigate."
        echo -e "  ''${DIM}Type 'exit' or 'reboot' when done. The full install log is at /tmp/msp-install.log.''${RESET}"
        echo ""
        log "ERROR: No internal drive. Available: $(lsblk -d -o NAME,SIZE,TYPE,MODEL 2>/dev/null | tr '\n' ' ')"
        # v12: TTY escape hatch instead of sleep 86400. Operator can read
        # the log, run lsblk by hand, attach a USB and rerun, etc.
        bash --login || sleep 86400
        exit 0
      fi

      # Size/model from sysfs — no blockdev ioctl, no lsblk read.
      _dev_name=$(basename "$INTERNAL_DEV")
      case "$_dev_name" in mmcblk*) ;; *) _dev_name=$(echo "$_dev_name" | sed 's/[0-9]*$//') ;; esac
      _dev_sectors=$(sysfs_read "/sys/block/$_dev_name/size" 0)
      DEV_SIZE_BYTES=$(( _dev_sectors * 512 ))
      DEV_SIZE=$(numfmt --to=iec "$DEV_SIZE_BYTES" 2>/dev/null || echo "''${DEV_SIZE_BYTES}B")
      DEV_MODEL=$(sysfs_read "/sys/block/$_dev_name/device/model" "unknown" | tr -d ' \n')

      # Build a human-friendly description for the progress display
      # e.g. "MMC 32GB (/dev/mmcblk0)" or "Samsung SSD 128GB (/dev/sda)"
      case "$INTERNAL_DEV" in
        *nvme*)  DEV_TYPE="NVMe" ;;
        *mmcblk*) DEV_TYPE="MMC" ;;
        *vda*)   DEV_TYPE="VirtIO" ;;
        *)       DEV_TYPE="SATA" ;;
      esac
      DEV_DESC="''${DEV_MODEL:-$DEV_TYPE} ''${DEV_SIZE} ($INTERNAL_DEV)"
      log "Found: $INTERNAL_DEV ($DEV_SIZE) $DEV_MODEL"

      # v11: Probe the target drive via sysfs only. No SMART, no blockdev.
      # SMART pre-flight was removed: it adds a hang surface on drives with
      # flaky ATA firmware, and if the drive is truly bad, dd will fail
      # later. SMART telemetry is collected post-boot by the daemon where
      # a hang is non-blocking.
      probe_drive "$INTERNAL_DEV" || true

      # Size gate remains (cheap, sysfs-cached, cannot hang).
      if [ "$DRIVE_SIZE_GB" -lt 20 ] 2>/dev/null; then
        stop_heartbeat_daemon
        die "Target drive is only ''${DRIVE_SIZE_GB}GB. Minimum 20GB required." "hw_probe"
      fi

      # v17: Registration moved to the background subshell kicked off
      # in STEP 3. Install proceeds immediately into disk prep. If the
      # backgrounded post_start_report fails or stalls, install still
      # completes normally.

      # ── Check for existing installation ─────────────────────────
      # NO MOUNT PROBE. mount(2) on a corrupt FS can enter uninterruptible
      # sleep (state D) which survives even SIGKILL — making the installer
      # truly unkillable. Instead we trust lsblk's partition label (read
      # from /sys, not from disk blocks) as proof-of-prior-install.
      # If a partition labeled "nixos" exists on the target, we show the
      # 10-second reinstall countdown. The dd pass that follows overwrites
      # the disk regardless, so label-only detection is sufficient.
      set_step 4 "''${WHITE}Checking target disk...''${RESET}"
      # v13 (Session 206): the "reinstall countdown" that used to live
      # here read LABELs via lsblk, which triggered a blkid FS probe per
      # partition and hung indefinitely on BitLocker / corrupted NTFS /
      # unlocked LUKS headers (observed 2026-04-13 on HP t640 with prior
      # Windows install). The countdown was cosmetic UX — the dd pass
      # overwrites unconditionally — so we removed it.
      #
      # Instead, IMMEDIATELY neutralize the existing partition table so
      # nothing downstream accidentally probes a half-readable FS. All
      # operations bounded with timeout + --kill-after. Each failure is
      # non-fatal: if the disk refuses sgdisk or wipefs, dd will still
      # write the raw image and overwrite everything anyway.
      #
      # Research (Talos Linux install pattern, Rook/Ceph disk prep,
      # util-linux docs): `sgdisk --zap-all` nukes both GPT and MBR
      # structures; `wipefs --all -f` erases FS/RAID/partition signatures
      # so libblkid can no longer trip on them. Together they guarantee
      # a clean-slate disk before any downstream read.
      log "pre-wipe: neutralizing existing partition table on $INTERNAL_DEV"
      timeout --kill-after=3 10 sgdisk --zap-all --force "$INTERNAL_DEV" \
        >> "$LOG_FILE" 2>&1 || log "sgdisk --zap-all returned non-zero (non-fatal; dd will overwrite)"
      timeout --kill-after=3 10 wipefs --all --force "$INTERNAL_DEV" \
        >> "$LOG_FILE" 2>&1 || log "wipefs --all returned non-zero (non-fatal; dd will overwrite)"
      # Inform the kernel the partition table changed — bounded.
      timeout --kill-after=3 5 partprobe "$INTERNAL_DEV" \
        >> "$LOG_FILE" 2>&1 || log "partprobe returned non-zero (non-fatal)"
      log "pre-wipe complete — target disk is ready for dd"

      set_step 5 "''${WHITE}Target ready: ''${DEV_DESC}''${RESET}"
      sleep 1

      # ── Step 2: Write raw image ─────────────────────────────────
      log "Step 2: Writing raw image to $INTERNAL_DEV"

      # The dd loop below paints detailed MB/speed info at ~10 Hz. The
      # heartbeat daemon (which paints from HEARTBEAT_STATE once per second)
      # would race with it, so we pause the daemon during the write and
      # resume it once dd completes.
      stop_heartbeat_daemon

      # Calculate image size for progress reporting
      IMAGE_SIZE_BYTES=$(stat -c%s "$IMAGE" 2>/dev/null || echo "0")
      IMAGE_SIZE_MB=$(( IMAGE_SIZE_BYTES / 1048576 ))
      DECOMPRESSED_MB=$(( DECOMPRESSED_BYTES / 1048576 ))

      draw_progress 10 "''${WHITE}Writing system image...''${RESET}" \
        "0 MB / $DECOMPRESSED_MB MB"

      # dd the compressed raw image to the internal drive.
      # pv reads from the pipe and displays progress based on the decompressed size.
      # conv=fsync ensures data is flushed to disk before dd returns.
      zstdcat "$IMAGE" \
        | pv -f -s "$DECOMPRESSED_BYTES" -n 2>"$LOG_FILE.pv" \
        | dd of="$INTERNAL_DEV" bs=4M conv=fsync iflag=fullblock 2>>"$LOG_FILE" &
      DD_PID=$!

      START_TIME=$(date +%s)

      # Monitor pv's numeric output for real progress
      while kill -0 $DD_PID 2>/dev/null; do
        # pv -n writes percentage to stderr, one number per line
        PV_PCT=$(tail -1 "$LOG_FILE.pv" 2>/dev/null || echo "0")
        PV_PCT=''${PV_PCT:-0}

        # Clamp to integer
        PV_PCT=$(echo "$PV_PCT" | grep -o '^[0-9]*' || echo "0")
        PV_PCT=''${PV_PCT:-0}

        # Calculate written MB from percentage
        WRITTEN_MB=$(( DECOMPRESSED_MB * PV_PCT / 100 ))

        # Calculate speed and ETA
        ELAPSED=$(( $(date +%s) - START_TIME ))
        if [ "$ELAPSED" -gt 0 ] && [ "$WRITTEN_MB" -gt 0 ]; then
          SPEED_MB=$(( WRITTEN_MB / ELAPSED ))
          REMAINING_MB=$(( DECOMPRESSED_MB - WRITTEN_MB ))
          if [ "$SPEED_MB" -gt 0 ]; then
            ETA_SECS=$(( REMAINING_MB / SPEED_MB ))
            SPEED_INFO="Speed: ''${SPEED_MB} MB/s — about ''${ETA_SECS} seconds remaining"
          else
            SPEED_INFO="Speed: calculating..."
          fi
        else
          SPEED_INFO="Speed: calculating..."
        fi

        # Map dd progress (10%-80% of overall)
        OVERALL=$(( 10 + PV_PCT * 70 / 100 ))

        draw_progress "$OVERALL" \
          "''${WHITE}Writing system image...''${RESET}  ''${WRITTEN_MB} MB / ''${DECOMPRESSED_MB} MB" \
          "$SPEED_INFO"

        sleep 1
      done

      wait $DD_PID && DD_EXIT=0 || DD_EXIT=$?

      if [ $DD_EXIT -ne 0 ]; then
        die "Image write failed (dd exit code $DD_EXIT). Drive may be defective."
      fi

      log "Image write complete"

      # Force kernel to re-read partition table after dd.
      # partprobe has been seen to hang on flaky USB/SATA controllers; we
      # wrap each call in bounded_abandon with a 15s budget. If it hangs
      # we continue — subsequent ops will retry and the kernel usually
      # catches up on its own.
      sync
      bounded_abandon 15 partprobe_1 partprobe "$INTERNAL_DEV" || true
      sleep 2
      bounded_abandon 15 partprobe_2 partprobe "$INTERNAL_DEV" || true
      sleep 1

      # dd is done — resume the heartbeat daemon for post-write steps so
      # any subsequent slow operation (mount, resize2fs, e2fsck) keeps
      # repainting the screen.
      set_step 82 "''${GREEN}Image written successfully''${RESET}"
      start_heartbeat_daemon

      # v10: Post-write integrity verification — catches partial/corrupted writes.
      # We read back head+tail samples and compute a composite SHA256. The value
      # is reported to Central Command but not compared against an expected hash
      # (the image SHA would need to be computed pre-write, which is expensive
      # for a 1GB+ compressed image). The act of reading succeeds or fails —
      # a read failure flags bad storage.
      set_step 83 "''${WHITE}Verifying write integrity...''${RESET}"
      VERIFY_SHA=""
      if ! VERIFY_SHA=$(verify_post_write "$INTERNAL_DEV" 2>> "$LOG_FILE"); then
        die "Post-write verification failed: unable to read back from $INTERNAL_DEV. Storage may be failing." "verify_readback"
      fi
      log "Post-write verification sha256=$VERIFY_SHA"

      # ── Step 3: Resize root partition ───────────────────────────
      # Raw image layout: 1=ESP, 2=root, 3=MSP-DATA(2GB)
      # MSP-DATA sits right after root, blocking growpart. Strategy:
      #   a) Delete partition 3 (MSP-DATA)
      #   b) Grow partition 2 (root) to fill disk minus 2GB tail
      #   c) Recreate partition 3 (MSP-DATA, 2GB) at the end
      #   d) Format new partition 3
      log "Step 3: Resizing root partition to fill disk"
      set_step 83 "''${WHITE}Expanding root partition...''${RESET}"

      # Determine partition device names (NVMe/eMMC use p-suffix)
      case "$INTERNAL_DEV" in
        *nvme*|*mmcblk*)
          ROOT_PART="''${INTERNAL_DEV}p2"
          DATA_PART="''${INTERNAL_DEV}p3"
          ;;
        *)
          ROOT_PART="''${INTERNAL_DEV}2"
          DATA_PART="''${INTERNAL_DEV}3"
          ;;
      esac

      # Total disk size in sectors (512-byte)
      DISK_SECTORS=$(blockdev --getsz "$INTERNAL_DEV")
      # Reserve 2GB for MSP-DATA at the end (2 * 1024 * 1024 * 1024 / 512 = 4194304 sectors)
      DATA_SECTORS=4194304
      # 34 sectors for GPT backup at end of disk
      GPT_BACKUP=34

      # Where should root end? Disk end - GPT backup - MSP-DATA
      ROOT_END_SECTOR=$(( DISK_SECTORS - GPT_BACKUP - DATA_SECTORS ))
      DATA_START_SECTOR=$ROOT_END_SECTOR

      # Read current root partition geometry (partition index 1 = second partition = root)
      ROOT_START=$(sfdisk --json "$INTERNAL_DEV" 2>>"$LOG_FILE" \
        | jq '.partitiontable.partitions[1].start') || ROOT_START=0
      ROOT_CURRENT_SIZE=$(sfdisk --json "$INTERNAL_DEV" 2>>"$LOG_FILE" \
        | jq '.partitiontable.partitions[1].size') || ROOT_CURRENT_SIZE=0
      ROOT_CURRENT_END=$(( ROOT_START + ROOT_CURRENT_SIZE ))

      if [ "$ROOT_START" -gt 0 ] && [ "$ROOT_END_SECTOR" -gt "$ROOT_CURRENT_END" ] 2>/dev/null; then
        ROOT_NEW_SIZE=$(( ROOT_END_SECTOR - ROOT_START ))
        log "Disk has room: root sector $ROOT_START+$ROOT_CURRENT_SIZE → $ROOT_START+$ROOT_NEW_SIZE"

        # Every disk op below is wrapped in bounded_abandon with a budget.
        # If ANY of them hangs on a misbehaving drive/controller, we log
        # and continue. The installed system's first-boot will heal any
        # partition ops we skipped.
        bounded_abandon 20 sfdisk_del3 sfdisk --delete "$INTERNAL_DEV" 3 || true
        bounded_abandon 15 partprobe_3 partprobe "$INTERNAL_DEV" || true
        sleep 1

        log "Setting root: start=$ROOT_START size=$ROOT_NEW_SIZE sectors"
        echo "start=$ROOT_START, size=$ROOT_NEW_SIZE" > /tmp/sfdisk-root.in
        bounded_abandon 30 sfdisk_root_resize \
          bash -c 'sfdisk -N 2 --no-reread "$1" < /tmp/sfdisk-root.in' _ "$INTERNAL_DEV" || {
          log "WARNING: sfdisk resize of root partition failed/abandoned"
        }

        echo "start=$DATA_START_SECTOR, size=$DATA_SECTORS, type=L" > /tmp/sfdisk-data.in
        bounded_abandon 30 sfdisk_data_append \
          bash -c 'sfdisk --append --no-reread "$1" < /tmp/sfdisk-data.in' _ "$INTERNAL_DEV" || {
          log "WARNING: sfdisk append of MSP-DATA failed/abandoned"
        }

        bounded_abandon 15 sfdisk_label \
          sfdisk --part-label "$INTERNAL_DEV" 3 MSP-DATA || true

        bounded_abandon 15 partprobe_4 partprobe "$INTERNAL_DEV" || true
        sleep 1

        set_step 85 "''${WHITE}Formatting data partition...''${RESET}"
        bounded_abandon 90 mkfs_data \
          mkfs.ext4 -F -L MSP-DATA "$DATA_PART" || {
          log "WARNING: mkfs.ext4 on MSP-DATA failed/abandoned"
        }
      else
        log "Disk same size as image or smaller — skipping resize"
      fi

      # ── Step 3.5 (v28): MSP-DATA verification + recovery ────────
      # The resize branch above can silently abandon sfdisk-append or
      # mkfs.ext4 on misbehaving eMMC controllers (observed on .226 /
      # .227 where the installed system booted into a 90s systemd wait
      # for /dev/disk/by-partlabel/MSP-DATA that never appeared).
      # Always verify the partition exists, has the MSP-DATA partlabel,
      # and holds a usable ext4 filesystem — rebuild whatever is
      # missing with a tight timeout. Non-fatal on failure; the
      # installed system's msp-data-partition-recovery.service is the
      # second line of defense.
      set_step 85 "''${WHITE}Verifying MSP-DATA partition...''${RESET}"
      log "Step 3.5 (v28): Verifying MSP-DATA partition exists + is ext4-labeled"

      msp_data_ok() {
        # Partition must exist as a block device, have PARTLABEL=MSP-DATA,
        # and be a readable ext4 filesystem labeled MSP-DATA.
        [ -b "$DATA_PART" ] || return 1
        partlabel=$(blkid -o value -s PARTLABEL "$DATA_PART" 2>/dev/null || true)
        fslabel=$(blkid -o value -s LABEL "$DATA_PART" 2>/dev/null || true)
        fstype=$(blkid -o value -s TYPE "$DATA_PART" 2>/dev/null || true)
        [ "$partlabel" = "MSP-DATA" ] && [ "$fslabel" = "MSP-DATA" ] && [ "$fstype" = "ext4" ]
      }

      if msp_data_ok; then
        log "MSP-DATA partition verified: partlabel+fslabel match, ext4 OK"
      else
        log "MSP-DATA partition missing or incomplete — running recovery"

        # ── Pre-delete safety probe ──────────────────────────────
        # On a re-flashed appliance the operator already chose to destroy
        # prior state by writing the raw image. But belt-and-suspenders:
        # if partition 3 mounts read-only and contains non-empty data,
        # skip recovery and surface it to the operator instead of
        # blowing it away. This guards against the msp_data_ok() strict-
        # label check false-negating on a disk that actually has a
        # working (differently-labeled) filesystem.
        safe_to_reformat=1
        if [ -b "$DATA_PART" ]; then
          mkdir -p /tmp/msp-data-probe
          if bounded_abandon 15 probe_mount mount -o ro "$DATA_PART" /tmp/msp-data-probe 2>/dev/null; then
            probe_content=$(ls -A /tmp/msp-data-probe 2>/dev/null || true)
            umount /tmp/msp-data-probe 2>/dev/null || true
            if [ -n "$probe_content" ]; then
              log "WARNING: partition 3 is mountable AND non-empty — skipping recovery to preserve data. Operator must inspect manually."
              safe_to_reformat=0
            fi
          fi
        fi

        # Recompute sector geometry in case resize skipped (small disk path).
        DISK_SECTORS=$(blockdev --getsz "$INTERNAL_DEV")
        DATA_SECTORS=4194304  # 2GB / 512B sectors
        GPT_BACKUP=34
        # Only recover if there's room for a 2GB tail partition. Otherwise
        # the installed system will boot with /var/lib/msp on / (nofail
        # saves us) and the recovery service can re-attempt later.
        if [ "$safe_to_reformat" -eq 1 ] && [ "$DISK_SECTORS" -gt $(( DATA_SECTORS + GPT_BACKUP + 2048 )) ]; then
          # Partition 3 verified empty/unmountable above; safe to drop.
          bounded_abandon 15 recover_del3 sfdisk --delete "$INTERNAL_DEV" 3 || true
          bounded_abandon 15 recover_partprobe1 partprobe "$INTERNAL_DEV" || true

          # Allocate partition 3 at the tail of the disk using sgdisk,
          # which is more forgiving than sfdisk on partially-corrupt GPTs.
          # -n 3:$start:$end (0 means "end of disk minus backup GPT").
          DATA_START_SECTOR=$(( DISK_SECTORS - GPT_BACKUP - DATA_SECTORS ))
          bounded_abandon 30 recover_sgdisk_new \
            sgdisk -n 3:$DATA_START_SECTOR:0 -t 3:8300 -c 3:MSP-DATA "$INTERNAL_DEV" \
            >> "$LOG_FILE" 2>&1 || log "WARNING: sgdisk recovery failed/abandoned"

          bounded_abandon 15 recover_partprobe2 partprobe "$INTERNAL_DEV" || true
          sleep 1

          bounded_abandon 90 recover_mkfs \
            mkfs.ext4 -F -L MSP-DATA "$DATA_PART" \
            >> "$LOG_FILE" 2>&1 || log "WARNING: recovery mkfs.ext4 failed/abandoned"

          if msp_data_ok; then
            log "MSP-DATA recovery succeeded"
          else
            log "WARNING: MSP-DATA still missing after recovery; installed-system service will retry at first boot"
          fi
        elif [ "$safe_to_reformat" -eq 0 ]; then
          log "Skipping installer-side recovery — data preservation guard active. Installed-system service will re-evaluate on first boot."
        else
          log "Disk too small for 2GB MSP-DATA tail; deferring to installed-system recovery service"
        fi
      fi

      set_step 86 "''${WHITE}Checking root filesystem...''${RESET}"
      # e2fsck can take minutes on a large FS; 5-minute budget is generous.
      bounded_abandon 300 e2fsck_root e2fsck -f -y "$ROOT_PART" || true

      set_step 87 "''${WHITE}Resizing root filesystem...''${RESET}"
      bounded_abandon 300 resize2fs_root resize2fs "$ROOT_PART" || {
        log "WARNING: resize2fs failed/abandoned (non-fatal if partition was already full size)"
      }

      # Size from sysfs (partition size). Name construction handles nvme/mmc.
      _root_name=$(basename "$ROOT_PART")
      NEW_ROOT_SIZE_SECTORS=$(sysfs_read "/sys/class/block/$_root_name/size" 0)
      NEW_ROOT_SIZE=$(( NEW_ROOT_SIZE_SECTORS * 512 ))
      NEW_ROOT_SIZE_GB=$(( NEW_ROOT_SIZE / 1073741824 ))
      log "Root partition resized to ''${NEW_ROOT_SIZE_GB}GB"

      set_step 88 "''${GREEN}Root partition: ''${NEW_ROOT_SIZE_GB}GB''${RESET}"

      # ── Step 4: Post-write setup ─────────────────────────────────
      # Mount root to copy config and verify.
      # Secure Boot keys are generated on first boot by msp-secureboot-keygen.service
      # in the installed system (appliance-disk-image.nix) — no need to do it here.
      log "Step 4: Mounting installed system for post-write setup"
      set_step 89 "''${WHITE}Configuring installed system...''${RESET}"

      mkdir -p /mnt
      mount "$ROOT_PART" /mnt >> "$LOG_FILE" 2>&1

      # ── Step 5: Copy config from USB ────────────────────────────
      log "Step 5: Checking for USB config"
      set_step 91 "''${WHITE}Checking for deployment config...''${RESET}"

      # Look for config.yaml on the USB boot media
      CONFIG_SRC=""
      for candidate in /config/config.yaml /config/osiriscare/config.yaml /config/msp/config.yaml; do
        if [ -f "$candidate" ]; then
          CONFIG_SRC="$candidate"
          break
        fi
      done

      if [ -n "$CONFIG_SRC" ]; then
        # MSP-DATA is partition 3 — mount it to copy config
        case "$INTERNAL_DEV" in
          *nvme*|*mmcblk*) DATA_PART="''${INTERNAL_DEV}p3" ;;
          *)               DATA_PART="''${INTERNAL_DEV}3" ;;
        esac
        mkdir -p /mnt/var/lib/msp
        # Abandonable: mount can hang in state D on a fresh partition that
        # hasn't settled. 30s budget, then skip the config copy.
        if bounded_abandon 30 mount_data mount "$DATA_PART" /mnt/var/lib/msp; then
          cp "$CONFIG_SRC" /mnt/var/lib/msp/config.yaml
          chmod 600 /mnt/var/lib/msp/config.yaml
          log "Copied config from $CONFIG_SRC to MSP-DATA partition"
          set_step 92 "''${GREEN}Config copied from USB''${RESET}"
        else
          log "WARNING: could not mount $DATA_PART to copy config — will provision via MAC"
          set_step 92 "''${DIM}Config copy skipped — will provision via MAC''${RESET}"
        fi
      else
        log "No USB config found (will provision via MAC on first boot)"
        set_step 92 "''${DIM}No USB config — will provision via MAC''${RESET}"
      fi

      # ── Step 6: Verify ─────────────────────────────────────────
      log "Step 6: Verifying installation"
      set_step 93 "''${WHITE}Verifying installation...''${RESET}"

      STORE_COUNT=$(ls /mnt/nix/store 2>/dev/null | wc -l)
      BOOT_OK="no"

      # Mount ESP to check boot files
      case "$INTERNAL_DEV" in
        *nvme*|*mmcblk*) ESP_PART="''${INTERNAL_DEV}p1" ;;
        *)               ESP_PART="''${INTERNAL_DEV}1" ;;
      esac
      mkdir -p /mnt/boot
      bounded_abandon 30 mount_esp mount "$ESP_PART" /mnt/boot || true
      [ -d /mnt/boot/EFI ] && BOOT_OK="yes"
      log "Verified: $STORE_COUNT store paths, boot=$BOOT_OK"
      ls -la /mnt/boot/ >> "$LOG_FILE" 2>/dev/null

      # v25: Install systemd-boot at the UEFI "removable media" fallback
      # path (\EFI\BOOT\BOOTX64.EFI). Raw image only populates
      # \EFI\systemd\systemd-bootx64.efi, which HP thin clients (t740,
      # t640) don't auto-scan because NVRAM has no boot entry
      # (canTouchEfiVariables=false is correct for a multi-target raw
      # image). Without the fallback, BIOS reports "no boot device"
      # and falls through to PXE. Every UEFI firmware scans the
      # fallback path as a last resort — fixes the post-install
      # non-boot on HP thin clients.
      if [ -f /mnt/boot/EFI/systemd/systemd-bootx64.efi ]; then
        mkdir -p /mnt/boot/EFI/BOOT
        cp /mnt/boot/EFI/systemd/systemd-bootx64.efi /mnt/boot/EFI/BOOT/BOOTX64.EFI
        log "UEFI fallback: installed BOOTX64.EFI for removable-media auto-scan"
      else
        log "WARN: systemd-bootx64.efi not found at /mnt/boot/EFI/systemd/ — BOOTX64.EFI fallback NOT installed"
      fi

      if [ "$BOOT_OK" != "yes" ]; then
        bounded_abandon 15 umount_mnt_fail umount -R /mnt || true
        die "Boot partition verification failed — no EFI directory found."
      fi

      set_step 95 "''${GREEN}Verified: $STORE_COUNT store paths, EFI boot OK''${RESET}"

      # ── Step 7: Cleanup & Halt ─────────────────────────────────
      log "Step 7: Cleanup"
      set_step 97 "''${WHITE}Unmounting filesystems...''${RESET}"

      # Umount can hang in state D on a flaky drive; abandon after 15s.
      # Disk state still flushed by the prior syncs + conv=fsync on dd.
      bounded_abandon 15 umount_mnt umount -R /mnt || true
      sync
      log "Unmounted all filesystems"

      # Unmount USB filesystem to prevent I/O errors on removal.
      # The squashfs is still in RAM so this is safe.
      bounded_abandon 10 umount_iso umount -l /iso || true
      sync

      # ── Completion screen ───────────────────────────────────────
      clear_screen
      echo ""
      echo ""
      echo -e "  ''${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━''${RESET}"
      echo ""
      figlet -f slant "  Complete!" 2>/dev/null || echo -e "  ''${GREEN}INSTALLATION COMPLETE!''${RESET}"
      echo ""
      echo -e "  ''${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━''${RESET}"
      echo ""
      echo -e "  ''${WHITE}  OsirisCare Compliance Appliance''${RESET}"
      echo -e "  ''${DIM}  ''${STORE_COUNT} store paths — root ''${NEW_ROOT_SIZE_GB}GB — $INTERNAL_DEV ($DEV_SIZE)''${RESET}"
      echo ""
      echo -e "  ''${CYAN}  ┌─────────────────────────────────────────────────┐''${RESET}"
      echo -e "  ''${CYAN}  │''${RESET}                                                 ''${CYAN}│''${RESET}"
      echo -e "  ''${CYAN}  │''${RESET}   ''${GREEN}✓ Installation Complete''${RESET}                       ''${CYAN}│''${RESET}"
      echo -e "  ''${CYAN}  │''${RESET}                                                 ''${CYAN}│''${RESET}"
      echo -e "  ''${CYAN}  │''${RESET}   ''${YELLOW}Remove the USB drive.''${RESET}                         ''${CYAN}│''${RESET}"
      echo -e "  ''${CYAN}  │''${RESET}   ''${WHITE}Press the power button to start.''${RESET}               ''${CYAN}│''${RESET}"
      echo -e "  ''${CYAN}  │''${RESET}                                                 ''${CYAN}│''${RESET}"
      echo -e "  ''${CYAN}  │''${RESET}   ''${DIM}On first boot the appliance will:''${RESET}               ''${CYAN}│''${RESET}"
      echo -e "  ''${CYAN}  │''${RESET}     ''${DIM}• Connect to Central Command''${RESET}                   ''${CYAN}│''${RESET}"
      echo -e "  ''${CYAN}  │''${RESET}     ''${DIM}• Auto-provision via MAC address''${RESET}               ''${CYAN}│''${RESET}"
      echo -e "  ''${CYAN}  │''${RESET}     ''${DIM}• Begin compliance monitoring''${RESET}                  ''${CYAN}│''${RESET}"
      echo -e "  ''${CYAN}  │''${RESET}                                                 ''${CYAN}│''${RESET}"
      echo -e "  ''${CYAN}  └─────────────────────────────────────────────────┘''${RESET}"
      echo ""
      echo ""

      log "Installation complete. Halting."

      # v17: Fire install-complete report in the background + give it a
      # brief window (10s) to complete before halting. The install halt
      # is NOT gated on this — if the curl stalls, we still halt after
      # the window. Prior behavior blocked the entire halt on curl.
      ( post_complete_report "true" "complete" "" "" "$VERIFY_SHA" ) >> "$LOG_FILE" 2>&1 &
      _telemetry_pid=$!
      _telemetry_deadline=$(( $(date +%s) + 10 ))
      while [ "$(date +%s)" -lt "$_telemetry_deadline" ] && kill -0 "$_telemetry_pid" 2>/dev/null; do
        sleep 1
      done
      kill "$_telemetry_pid" 2>/dev/null || true
      log "v17: post_complete_report finished or timed out after 10s — proceeding to halt"

      # Halt — NOT reboot, NOT poweroff with countdown.
      # User removes USB, then presses power button.
      systemctl halt
    '';
  };

  # Post-login MOTD
  environment.etc."motd".text = ''

    OsirisCare MSP - Appliance Installer v9
    ────────────────────────────────────────
    Offline installer — writes embedded raw image via dd+zstd.
    No network required.

    Useful commands:
      journalctl -u msp-auto-install -f    # Watch install progress
      systemctl status msp-auto-install     # Check install status
      cat /tmp/msp-install.log             # Full install log

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
    before = [ "appliance-daemon.service" ];
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
  # Python Compliance Agent (DEPRECATED — kept for L2 sidecar only)
  # Go appliance-daemon is now the production agent. Python agent disabled by default.
  # ============================================================================
  systemd.services.compliance-agent = {
    description = "OsirisCare Compliance Agent (DEPRECATED)";
    # NOT in wantedBy — does not start by default
    after = [ "network-online.target" "msp-auto-provision.service" "msp-health-gate.service" ];
    requires = [ "msp-auto-provision.service" ];
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
  # Go Appliance Daemon — PRIMARY AGENT
  # Production agent: L1/L2/L3 healing, Windows+Linux scanning, evidence chain,
  # flap detection, learning flywheel, fleet updates, auto-deploy, AD discovery.
  # 6.6MB RAM | 102ms startup | 15MB binary
  # ============================================================================
  systemd.services.appliance-daemon = {
    description = "OsirisCare Appliance Daemon (Go)";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" "msp-auto-provision.service" "msp-health-gate.service" ];
    requires = [ "msp-auto-provision.service" ];
    wants = [ "network-online.target" ];

    # Include bash, python3, and tools needed for Linux self-scan scripts
    path = with pkgs; [ bash python3 coreutils gnugrep iproute2 systemd ];

    serviceConfig = {
      Type = "simple";
      ExecStart = "${appliance-daemon-go}/bin/appliance-daemon --config /var/lib/msp/config.yaml";
      Restart = "always";
      RestartSec = "10s";
      WorkingDirectory = "/var/lib/msp";
      StandardOutput = "journal";
      StandardError = "journal";
      SyslogIdentifier = "appliance-daemon";
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      ReadWritePaths = [ "/var/lib/msp" "/etc/msp" ];
      NoNewPrivileges = true;
      MemoryMax = "256M";
    };
  };

  # ============================================================================
  # Network Scanner Service (EYES) - Device Discovery
  # ============================================================================
  systemd.services.network-scanner = {
    description = "MSP Network Scanner (EYES) - Device Discovery";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" "msp-auto-provision.service" ];
    requires = [ "msp-auto-provision.service" ];
    wants = [ "network-online.target" ];

    # Include nmap, arp-scan, and standard tools in PATH
    path = with pkgs; [ nmap arp-scan iproute2 iputils coreutils bash ];

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

    # Compliance agent (Python — active unless Go daemon enabled)
    compliance-agent

    # Go appliance daemon (feature-flagged replacement)
    appliance-daemon-go

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

    # v15 (Session 206): wifi TOOLS are shipped (wpa_supplicant, iw,
    # wirelesstools in systemPackages) so an operator can manually
    # associate from TTY3 if wired eth is dead — but we do NOT enable
    # the wireless systemd service by default. Enabling it on hardware
    # WITHOUT a wifi card tries to associate on a non-existent
    # interface and pipe to failure, which in v14 appears to have
    # contributed to a hung boot.
    # To activate wifi at runtime:
    #   wpa_supplicant -B -i wlan0 -c /etc/wpa_supplicant.conf
    # The auto-wifi-config oneshot still writes the config file from
    # /iso/wifi.conf if present; operator just runs wpa_supplicant.
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
  # They apply when booted from the installed system (dd-written image)
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
    mkdir -p /var/lib/msp/ca
    mkdir -p /etc/msp/certs
    chmod 700 /var/lib/msp /var/lib/msp/ca /etc/msp/certs
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

  # Root password set via hashedPassword in configuration.nix (osiris2024)
  # Do NOT set initialPassword here — having both triggers NixOS warnings

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
    before = [ "appliance-daemon.service" ];

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
        log "Auto-provisioning failed - provision code entry available"
        log "Options:"
        log "  1. Run: compliance-provision (enter code from partner dashboard)"
        log "  2. Insert USB with config.yaml and reboot"

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
  ║  MAC: Run 'compliance-provision --mac' to display                 ║
  ║  See: https://docs.osiriscare.net/appliance-setup                 ║
  ╚═══════════════════════════════════════════════════════════════════╝

ISSUE
        exit 1
      fi

      log "MAC Address: $MAC_ADDR"

      # URL-encode the MAC (replace : with %3A)
      MAC_ENCODED=$(echo "$MAC_ADDR" | sed 's/:/%3A/g')
      PROVISION_URL="$API_URL/api/provision/$MAC_ENCODED"

      # Phase 1: Initial connectivity retries (6 attempts, 10s apart)
      # Handles network not ready yet after boot.
      INITIAL_RETRIES=6
      RETRY_DELAY=10
      REGISTERED=false

      for attempt in $(seq 1 $INITIAL_RETRIES); do
        log "Attempt $attempt/$INITIAL_RETRIES: Checking network connectivity..."

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
          # Check if response contains valid config (site_id is non-null)
          if ${pkgs.jq}/bin/jq -e '.site_id' /tmp/provision-response.json >/dev/null 2>&1; then
            ${pkgs.yq}/bin/yq -y '.' /tmp/provision-response.json > "$CONFIG_PATH"
            chmod 600 "$CONFIG_PATH"
            log "SUCCESS: Provisioning complete via MAC lookup"
            rm -f /tmp/provision-response.json
            exit 0
          fi
          # 200 but no site_id means unclaimed — MAC registered, awaiting claim
          REGISTERED=true
          log "Appliance registered but unclaimed — entering polling mode"
          break
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

      # Phase 2: Drop-ship polling (unclaimed appliance waits for admin to claim it).
      # Polls every 60s indefinitely. Once claimed, Central Command returns site_id + api_key.
      if [ "$REGISTERED" = true ] || [ "$HTTP_CODE" = "200" ]; then
        POLL_DELAY=60
        POLL_COUNT=0
        log "Drop-ship mode: polling every ''${POLL_DELAY}s for site assignment..."
        log "Claim this appliance in Central Command dashboard: MAC=$MAC_ADDR"

        # Write instructions to console showing MAC for easy claiming
        cat > /etc/issue.d/90-msp-provision.issue << ISSUE

  ╔═══════════════════════════════════════════════════════════════════╗
  ║  AWAITING SITE ASSIGNMENT                                         ║
  ╠═══════════════════════════════════════════════════════════════════╣
  ║  Appliance registered. Waiting for admin to claim it.             ║
  ║  MAC: $MAC_ADDR                                                   ║
  ║                                                                   ║
  ║  Claim in Central Command > Appliances > Unclaimed                ║
  ║  Polling every 60s — will auto-configure when claimed.            ║
  ╚═══════════════════════════════════════════════════════════════════╝

ISSUE

        while true; do
          POLL_COUNT=$((POLL_COUNT + 1))
          sleep $POLL_DELAY

          HTTP_CODE=$(${pkgs.curl}/bin/curl -s -w "%{http_code}" -o /tmp/provision-response.json \
            --connect-timeout 15 --max-time 45 \
            "$PROVISION_URL" 2>/dev/null || echo "000")

          if [ "$HTTP_CODE" = "200" ]; then
            if ${pkgs.jq}/bin/jq -e '.site_id' /tmp/provision-response.json >/dev/null 2>&1; then
              ${pkgs.yq}/bin/yq -y '.' /tmp/provision-response.json > "$CONFIG_PATH"
              chmod 600 "$CONFIG_PATH"
              log "SUCCESS: Provisioning complete via MAC lookup (poll #$POLL_COUNT)"
              rm -f /tmp/provision-response.json
              exit 0
            fi
          fi
          rm -f /tmp/provision-response.json

          if [ $((POLL_COUNT % 10)) -eq 0 ]; then
            log "Still waiting for site assignment (poll #$POLL_COUNT)... MAC=$MAC_ADDR"
          fi
        done
      fi

      log "Auto-provisioning failed - provision code entry available"
      log ""
      log "Options:"
      log "  1. Run: compliance-provision (enter code from partner dashboard)"
      log "  2. Insert USB with config.yaml and reboot"
      log "  3. Pre-register MAC in Central Command dashboard"
      log ""
      log "After provisioning: systemctl restart appliance-daemon"

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
    requires = [ "msp-auto-provision.service" ];
    wants = [ "network-online.target" ];

    path = with pkgs; [ systemd iproute2 gnugrep coreutils ];

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
          # NixOS rejects `hostnamectl set-hostname` AND the `hostname`
          # command is not in the unit's PATH. Write directly to
          # /proc/sys/kernel/hostname — this invokes sethostname(2)
          # without needing any external binary or /etc/hostname write.
          echo "$SITE_ID" > /proc/sys/kernel/hostname 2>/dev/null || true
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
      # /etc on NixOS is mostly read-only (populated via activation).
      # Write motd best-effort; skip if the FS rejects it — non-critical.
      cat > /etc/motd 2>/dev/null << MOTD || true

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

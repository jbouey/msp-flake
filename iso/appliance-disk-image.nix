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

  # Build the Go appliance daemon (replaces Python compliance-agent)
  # Feature-flagged: enabled by /var/lib/msp/.use-go-daemon or config.yaml use_go_daemon: true
  # Single source of truth for daemon version. Update this ONE line to bump.
  # Keep in sync with iso/appliance-image.nix's daemonVersion.
  daemonVersion = "0.4.8";

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
      "cmd/appliance-watchdog"
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

  # === WireGuard: OFF BY DEFAULT, TIME-BOUNDED EMERGENCY ONLY (Session 204) ===
  # The mesh operates entirely over outbound HTTPS — zero inbound connections.
  # WireGuard is emergency-only, technically enforced:
  #
  # 1. Services disabled at boot (wantedBy = [])
  # 2. Activation ONLY via fleet order "enable_emergency_access" (customer-approved)
  # 3. Fleet order includes max_duration_minutes (default 120, max 480)
  # 4. systemd timer auto-disables tunnel after expiry — NOT bypassable
  # 5. Daemon logs activation/deactivation to evidence chain (append-only)
  #
  # Manual override at physical console: systemctl start wireguard-tunnel
  # (requires root password = physical access = customer controls the machine)
  systemd.services.wireguard-tunnel.wantedBy = lib.mkForce [];
  systemd.services.wireguard-keygen.wantedBy = lib.mkForce [];

  # Auto-disable timer: kills WireGuard after the emergency window expires.
  # The daemon sets the timer duration via systemd-run when activating.
  # This is the technical enforcement — even if OsirisCare doesn't deactivate,
  # the tunnel dies automatically. Not bypassable without root console access.
  systemd.services.wireguard-auto-disable = {
    description = "Auto-disable WireGuard emergency access tunnel";
    serviceConfig = {
      Type = "oneshot";
      ExecStart = pkgs.writeShellScript "wg-auto-disable" ''
        echo "Emergency access window expired — disabling WireGuard tunnel"
        ${pkgs.systemd}/bin/systemctl stop wireguard-tunnel 2>/dev/null || true
        ${pkgs.iproute2}/bin/ip link del wg0 2>/dev/null || true
        echo "WireGuard tunnel disabled by auto-expiry timer"
      '';
    };
  };
  systemd.timers.wireguard-auto-disable = {
    description = "Timer to auto-disable WireGuard after emergency window";
    # Not enabled by default — activated dynamically by the daemon's
    # enable_emergency_access handler with OnActiveSec=<duration>
  };

  # System identification - mkForce ensures branding even if other modules set defaults
  networking.hostName = lib.mkForce "osiriscare";
  system.stateVersion = "24.05";

  # ════════════════════════════════════════════════════════════════════════
  # Brand scrub — hide underlying distro in user-facing places.
  # The internal Nix machinery stays, but anything a customer or auditor
  # sees on the CLI, systemd-boot menu, or /etc/os-release reads as
  # "OsirisCare Appliance".
  # ════════════════════════════════════════════════════════════════════════
  system.nixos.label = lib.mkForce "OsirisCare";
  system.nixos.distroName = lib.mkForce "OsirisCare Appliance";
  system.nixos.distroId = lib.mkForce "osiriscare";

  # /etc/os-release — the first thing SSH login, audit scripts, or CMDB
  # enumeration reads. Replace NAME/PRETTY_NAME/HOME_URL with OsirisCare.
  environment.etc."os-release".text = lib.mkForce ''
    NAME="OsirisCare Appliance"
    PRETTY_NAME="OsirisCare Appliance"
    ID=osiriscare
    ID_LIKE=
    VERSION="24.05"
    VERSION_ID="24.05"
    HOME_URL="https://osiriscare.net"
    DOCUMENTATION_URL="https://osiriscare.net/docs"
    SUPPORT_URL="https://osiriscare.net/support"
    BUG_REPORT_URL="https://osiriscare.net/support"
    LOGO=osiriscare
  '';

  # Quiet MOTD/issue files that sometimes leak branding
  environment.etc."issue".text = lib.mkForce ''
    OsirisCare Appliance \n \l

  '';
  users.motd = lib.mkDefault ''

    ────────────────────────────────────────
     OsirisCare Appliance
     Compliance attestation substrate
    ────────────────────────────────────────

  '';

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

  # Hardened /tmp — noexec prevents execution of dropped payloads
  fileSystems."/tmp" = {
    device = "tmpfs";
    fsType = "tmpfs";
    options = [ "noexec" "nosuid" "nodev" "mode=1777" "size=512M" ];
  };

  # ============================================================================
  # Boot configuration for installed system (not live ISO)
  # ============================================================================
  boot = {
    loader = {
      systemd-boot = {
        enable = true;
        consoleMode = lib.mkDefault "max";
        editor = lib.mkDefault false;   # auditors: no grub-edit-to-root
      };
      efi.canTouchEfiVariables = false;
      timeout = 3;
    };

    # Lanzaboote Secure Boot: disabled until BIOS Secure Boot is enabled.
    # Keys exist at /etc/secureboot/keys/ — re-enable once BIOS is configured:
    #   1. Enable Secure Boot in BIOS
    #   2. Run: sbctl enroll-keys --microsoft
    #   3. Set boot.lanzaboote.enable = true, systemd-boot.enable = mkForce false
    lanzaboote = {
      enable = false;
      pkiBundle = "/etc/secureboot";
    };

    # Kernel params — quiet boot, suppress noisy drivers, pin reliable clocksource
    #
    # clocksource=hpet: TSC is unreliable on HP T-series mini-PCs with certain
    #   BIOS versions. Symptom: /proc/uptime freezes or reports stale values,
    #   causing the appliance-daemon to report impossible uptime values and
    #   breaking health-gate/watchdog timers. HPET is slower but monotonic.
    # nowatchdog: disable generic hardware watchdogs. Some BIOS-level watchdogs
    #   reset the system if not fed, but our software watchdog is sufficient.
    # tsc=reliable: fallback if BIOS forces TSC — tells kernel to trust TSC
    #   without recalibration (prevents the "Marking TSC unstable" crash path).
    kernelParams = [
      "quiet" "loglevel=3" "console=tty1" "console=ttyS0,115200"
      "clocksource=hpet" "nowatchdog" "tsc=reliable"
      # Phase H2 (Session 207): kernel lockdown in `integrity` mode.
      # Blocks (a) loading unsigned kernel modules, (b) /dev/mem +
      # /dev/kmem + /dev/port raw access, (c) kexec_load_file,
      # (d) writes to MSR registers, (e) arbitrary module parameters,
      # (f) BPF calls that could read kernel memory. `integrity`
      # (not `confidentiality`) — narrower guarantee but doesn't
      # break eBPF observability, kprobes, or /proc/kcore which the
      # watchdog's diagnostic collector may need. Hardens the last
      # attack path left after Phase S removes remote SSH: a
      # compromised daemon with root can no longer patch the kernel
      # or modify boot-integrity signals to cover its tracks.
      "lockdown=integrity"
      # v38 (Session 208): disable kernel audit. modules/compliance-agent.nix
      # used to enable auditd with execve rules on the installed system, which
      # produced events faster than kauditd could drain → backlog overflow →
      # console spam → userspace starved → sshd + daemon never came up.
      # The live ISO already sets this (iso/appliance-image.nix:178). Every
      # box installed from v25–v37 inherited the bug; v38 fixes the installed
      # system to match. Evidence-chain + OTS proofs cover compliance
      # attestation without needing in-kernel audit.
      "audit=0"
    ];
    blacklistedKernelModules = [
      "hid_logitech_hidpp"
      "usb_storage" "uas"              # Block USB mass storage (HIPAA physical security)
      "thunderbolt"                     # Block Thunderbolt DMA attacks
      "firewire_core" "firewire_ohci"  # Block FireWire DMA attacks
    ];

    # Essential kernel modules for HP T640 and common hardware
    initrd.availableKernelModules = [
      "ahci" "xhci_pci" "ehci_pci" "usbhid" "usb_storage" "sd_mod"
      "nvme" "sata_nv" "sata_via"
      "mmc_block" "sdhci_pci" "sdhci_acpi"    # eMMC support (HP T740, thin clients)
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

  # Post-login MOTD (first-boot service overwrites this with IP-specific info).
  # Phase S: remote SSH is OFF by default. This static copy is shown at the
  # physical console BEFORE msp-first-boot.service has rewritten /run/motd.
  # Keep the advice consistent with the new substrate — no SSH prompt here
  # that would mislead an operator who just flashed v32+.
  environment.etc."motd".text = ''

    OsirisCare MSP - Compliance Appliance
    ──────────────────────────────────────
    Dashboard:  http://osiriscare.local
    Beacon:     http://osiriscare.local:8443/  (local diagnostics)
    Portal:     http://osiriscare.local:8084
    Remote SSH: DISABLED (Session 207 Phase S)
    Break-glass: physical console, `msp` user,
                 passphrase from /api/admin/appliance/<aid>/break-glass

    Agent:      journalctl -u appliance-daemon -f
    Health:     systemctl status msp-health-check

  '';

  # ============================================================================
  # Health Gate Service (A/B Update Verification)
  # ============================================================================
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
  # Python Compliance Agent (DEPRECATED — kept for L2 sidecar only)
  # Go appliance-daemon is now the production agent. Python agent disabled by default.
  # ============================================================================
  systemd.services.compliance-agent = {
    description = "OsirisCare Compliance Agent (DEPRECATED)";
    # NOT in wantedBy — does not start by default
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
  # Go Appliance Daemon — PRIMARY AGENT
  # Production agent: L1/L2/L3 healing, Windows+Linux scanning, evidence chain,
  # flap detection, learning flywheel, fleet updates, auto-deploy, AD discovery.
  # 6.6MB RAM | 102ms startup | 15MB binary
  # ============================================================================
  systemd.services.appliance-daemon = {
    description = "OsirisCare Appliance Daemon (Go)";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" "msp-auto-provision.service" "msp-health-gate.service" ];
    wants = [ "network-online.target" ];

    # Include bash, python3, and tools needed for Linux self-scan scripts
    path = with pkgs; [ bash python3 coreutils gnugrep iproute2 systemd ];

    serviceConfig = {
      Type = "notify";
      ExecStart = "${appliance-daemon-go}/bin/appliance-daemon --config /var/lib/msp/config.yaml";
      Restart = "always";
      RestartSec = "10s";
      WatchdogSec = "120s";
      StartLimitIntervalSec = 300;
      StartLimitBurst = 5;
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
  # Journal upload (Session 207 Phase H4) — tamper-evident forensics
  # ============================================================================
  # Every 15 minutes, ship the last 15-min slice of appliance-daemon +
  # appliance-daemon-watchdog journalctl output to /api/journal/upload.
  # PHI-scrubbed via the phiscrub package at egress (Session 204 rule).
  # Payload is base64'd zstd — one batch per upload, hash-chained
  # server-side into journal_upload_events (append-only, migration 219).
  #
  # After SSH is stripped (Phase S), this is the forensics path:
  # post-incident, an operator pulls the hash-chained journal archive
  # for a specific appliance_id + time window. The chain lets them
  # prove no retroactive tampering occurred — ANY modification to a
  # historical batch breaks the forward-hash from that point on.
  # ============================================================================
  environment.etc."msp-journal-upload.sh" = {
    mode = "0755";
    text = ''
      #!${pkgs.runtimeShell}
      set -euo pipefail

      CFG=/var/lib/msp/config.yaml
      [ -r "$CFG" ] || exit 0

      # Pull site_id / appliance_id / api_key / api_endpoint out of the
      # config without a YAML parser — grep the top-level scalars.
      SITE_ID=$(${pkgs.gnugrep}/bin/grep -E '^site_id:' "$CFG" | ${pkgs.gnused}/bin/sed 's/^site_id:[ ]*//;s/"//g;s/'\'''//g;s/[ ]*$//')
      APPL_ID=$(${pkgs.gnugrep}/bin/grep -E '^appliance_id:' "$CFG" 2>/dev/null | ${pkgs.gnused}/bin/sed 's/^appliance_id:[ ]*//;s/"//g;s/'\'''//g;s/[ ]*$//' || echo "")
      API_KEY=$(${pkgs.gnugrep}/bin/grep -E '^api_key:' "$CFG" | ${pkgs.gnused}/bin/sed 's/^api_key:[ ]*//;s/"//g;s/'\'''//g;s/[ ]*$//')
      API_ENDPOINT=$(${pkgs.gnugrep}/bin/grep -E '^api_endpoint:' "$CFG" | ${pkgs.gnused}/bin/sed 's/^api_endpoint:[ ]*//;s/"//g;s/'\'''//g;s/[ ]*$//')
      API_ENDPOINT=''${API_ENDPOINT:-https://api.osiriscare.net}

      if [ -z "$SITE_ID" ] || [ -z "$API_KEY" ]; then
        echo "journal-upload: site_id or api_key missing; skipping"
        exit 0
      fi
      if [ -z "$APPL_ID" ]; then
        # Legacy config without appliance_id — derive from MAC so the
        # backend can still bind. Not a hard requirement yet.
        APPL_ID="$SITE_ID"
      fi

      BATCH_END=$(date -u -d '0 sec' +%Y-%m-%dT%H:%M:%SZ)
      BATCH_START=$(date -u -d '-15 min' +%Y-%m-%dT%H:%M:%SZ)

      # Pull 15-min journal tail for both daemons. Scrub with phiscrub-
      # style sed so hostnames / IPs / usernames don't leave the box.
      # This is belt-and-suspenders — phiscrub Go package already runs
      # in the daemon's egress paths but journalctl output comes from
      # journald, not from our code.
      TMP=$(mktemp /tmp/msp-journal-upload.XXXXXX)
      trap 'rm -f "$TMP" "$TMP.zst"' EXIT

      ${pkgs.systemd}/bin/journalctl \
          -u appliance-daemon -u appliance-daemon-watchdog \
          --since="$BATCH_START" --until="$BATCH_END" \
          --no-pager -o short-iso 2>/dev/null \
        | ${pkgs.gnused}/bin/sed \
            -e 's/\b[0-9]\{1,3\}\.[0-9]\{1,3\}\.[0-9]\{1,3\}\.[0-9]\{1,3\}\b/<IP>/g' \
            -e 's/\bPATIENT-[A-Z0-9-]*\b/<HOST>/g' \
            -e 's/\b[0-9]\{3\}-[0-9]\{2\}-[0-9]\{4\}\b/<SSN>/g' \
          > "$TMP"

      LINE_COUNT=$(wc -l < "$TMP" | tr -d ' ')
      if [ "$LINE_COUNT" -eq 0 ]; then
        echo "journal-upload: 0 lines in 15-min window; skipping POST"
        exit 0
      fi

      SHA=$(${pkgs.coreutils}/bin/sha256sum "$TMP" | cut -c1-64)
      ${pkgs.zstd}/bin/zstd -19 -q -o "$TMP.zst" "$TMP"
      B64=$(${pkgs.coreutils}/bin/base64 -w0 "$TMP.zst")

      # Gate H4-A fix: scrubbed=false until the phiscrub Go binary is
      # invoked here. The inline sed does 3 patterns (IP / PATIENT-* /
      # SSN); phiscrub package has 14 (MRN, emails, phones, usernames,
      # IPv6, DNs, MACs, etc). Shipping scrubbed=true on the 3-pattern
      # output would bake a false compliance attestation into the
      # immutable hash-chained ledger. Honest signal = false today +
      # JOURNAL_UPLOAD_UNSCRUBBED audit row. Phase H4.1 wires phiscrub
      # and flips this to true.
      PAYLOAD=$(${pkgs.jq}/bin/jq -n \
        --arg site_id "$SITE_ID" \
        --arg aid "$APPL_ID" \
        --arg bs "$BATCH_START" \
        --arg be "$BATCH_END" \
        --argjson n "$LINE_COUNT" \
        --arg b64 "$B64" \
        --arg sha "$SHA" \
        '{site_id: $site_id, appliance_id: $aid,
          batch_start: $bs, batch_end: $be,
          line_count: $n, compressed: $b64, sha256: $sha,
          scrubbed: false}')

      echo "journal-upload: posting $LINE_COUNT lines ($(echo "$B64" | wc -c) bytes base64)"
      ${pkgs.curl}/bin/curl -sSfL --max-time 30 \
        -H "Authorization: Bearer $API_KEY" \
        -H "Content-Type: application/json" \
        -d "$PAYLOAD" \
        "$API_ENDPOINT/api/journal/upload" \
        > /dev/null || {
          echo "journal-upload: POST failed"; exit 1;
        }
      echo "journal-upload: OK"
    '';
  };

  systemd.services.msp-journal-upload = {
    description = "MSP journal upload — 15-min batch to Central Command";
    path = with pkgs; [ coreutils curl jq gnused gnugrep zstd systemd ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "/etc/msp-journal-upload.sh";
      User = "root";  # journalctl needs root for system unit logs
      StandardOutput = "journal";
      StandardError = "journal";
    };
  };
  systemd.timers.msp-journal-upload = {
    description = "Timer — run msp-journal-upload every 15 min";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "2min";
      OnUnitActiveSec = "15min";
      AccuracySec = "30s";
      Persistent = true;
    };
  };

  # ============================================================================
  # Appliance Watchdog (Session 207 Phase W2) — SSH-strip precondition
  # ============================================================================
  # Second systemd unit that runs ALONGSIDE appliance-daemon with its own
  # Ed25519 bearer + 2-minute checkin loop against /api/watchdog/*. When
  # the main daemon wedges, operators issue one of six signed fleet-
  # order types (watchdog_restart_daemon, watchdog_refetch_config,
  # watchdog_reset_pin_store, watchdog_reset_api_key,
  # watchdog_redeploy_daemon, watchdog_collect_diagnostics) and the
  # watchdog executes the recovery. Closes the brick scenarios that
  # today still require SSH.
  #
  # Intentionally isolated from the main daemon:
  #   - distinct ExecStart binary
  #   - distinct config file (/etc/msp-watchdog.yaml)
  #   - distinct bearer (api_keys row with appliance_id '<aid>-watchdog')
  #   - starts IF present but never Requires= the main daemon, so a
  #     crashed main daemon cannot cascade-stop the watchdog
  # ============================================================================
  systemd.services.appliance-daemon-watchdog = {
    description = "OsirisCare Appliance Watchdog (Go) — SSH-free recovery surface";
    wantedBy = [ "multi-user.target" ];
    after = [ "network.target" ];
    # NOTE: intentionally NOT `after = network-online.target` — the
    # watchdog must be able to run even when network-online never
    # fires, so it can collect diagnostics about why network is broken.

    # Coreutils + systemd + iproute2 + inetutils cover the diagnostic
    # collectors (journalctl, ip addr, host <name>). The watchdog shells
    # out rather than pulling in a Go sysctl dependency.
    path = with pkgs; [ coreutils systemd iproute2 inetutils ];

    serviceConfig = {
      Type = "simple";
      ExecStart = "${appliance-daemon-go}/bin/appliance-watchdog --config /etc/msp-watchdog.yaml";
      Restart = "on-failure";
      RestartSec = "15s";
      StartLimitIntervalSec = 300;
      StartLimitBurst = 10;
      WorkingDirectory = "/var/lib/msp";
      StandardOutput = "journal";
      StandardError = "journal";
      SyslogIdentifier = "appliance-watchdog";

      # Hardening — tighter than the main daemon because the watchdog
      # only needs to shell out to systemctl + read /var/lib/msp.
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      NoNewPrivileges = true;
      ReadWritePaths = [ "/var/lib/msp" ];

      # Intentionally modest memory cap — watchdog shouldn't grow.
      MemoryMax = "64M";
    };
  };

  # Placeholder config so the unit doesn't fail-start the first boot.
  # msp-first-boot.service populates real values after the main daemon's
  # config.yaml lands; until then, the watchdog `idles silently until
  # signal` per its LoadConfig error path.
  environment.etc."msp-watchdog.yaml".text = ''
    # Populated by msp-first-boot.service on first provisioning.
    # The appliance-watchdog service boots into idle mode when these
    # are empty, and picks up values on its next restart after
    # msp-first-boot rewrites the file.
    site_id: ""
    appliance_id: ""
    api_key: ""
    api_endpoint: "https://api.osiriscare.net"
  '';

  # ============================================================================
  # Daemon Zombie Watchdog (Session 205)
  #
  # The appliance-daemon has systemd WatchdogSec=120s — if the daemon's
  # sdnotify ping stops, systemd kills + restarts it. But we've seen a
  # failure mode where ONE goroutine (the HTTP checkin POST) is alive
  # and pinging watchdog, while the order-processing goroutine is
  # deadlocked. systemd sees "daemon healthy" but the daemon does zero
  # actual work. External watchdog: check that the daemon is BOTH
  # checking in AND processing something (incidents, bundles, or orders)
  # — if only checking in for 15+ minutes with zero other activity, force
  # a restart to clear the deadlock.
  # ============================================================================
  systemd.services.msp-daemon-zombie-watch = {
    description = "OsirisCare Daemon Zombie Watchdog";
    after = [ "appliance-daemon.service" ];

    path = with pkgs; [ coreutils gnugrep findutils systemd ];

    serviceConfig = {
      Type = "oneshot";
    };

    script = ''
      STATE_DIR="/var/lib/msp"
      ACTIVITY_MARKER="$STATE_DIR/.last-activity"
      THRESHOLD_SECONDS=900  # 15 minutes

      # The daemon updates this file on any real work (drift scan, evidence
      # bundle submit, order execution). Checkin-only activity does NOT
      # update it. If the file is older than THRESHOLD_SECONDS, the daemon
      # is zombie — force restart.
      if [ ! -f "$ACTIVITY_MARKER" ]; then
        # No marker yet — daemon is fresh or never ran. Don't restart.
        exit 0
      fi

      NOW=$(date +%s)
      LAST=$(stat -c %Y "$ACTIVITY_MARKER" 2>/dev/null || echo 0)
      AGE=$(( NOW - LAST ))

      if [ "$AGE" -gt "$THRESHOLD_SECONDS" ]; then
        echo "[zombie-watch] Activity marker is ''${AGE}s old (threshold ''${THRESHOLD_SECONDS}s) — restarting daemon"
        logger -t msp-zombie-watch "Daemon appears zombie (no activity for ''${AGE}s), force-restarting"
        systemctl restart appliance-daemon
      fi
    '';
  };

  systemd.timers.msp-daemon-zombie-watch = {
    description = "Check for zombie daemon every 5 minutes";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "10min";
      OnUnitActiveSec = "5min";
      AccuracySec = "30s";
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

    # Include nmap, arp-scan, and standard tools in PATH
    path = with pkgs; [ nmap arp-scan iproute2 iputils coreutils bash ];

    serviceConfig = {
      Type = "simple";
      ExecStart = "${network-scanner}/bin/network-scanner";
      Restart = "always";
      RestartSec = "30s";
      # NOTE: WatchdogSec requires Type=notify with periodic WATCHDOG=1 pings.
      # Python scanner does not emit sd_notify — a WatchdogSec= line here is
      # a silent no-op. Rely on Restart=always for crash recovery. If hang
      # detection is needed later, bolt sd_notify into the scanner loop and
      # flip Type to "notify" in the same PR.
      StartLimitIntervalSec = 300;
      StartLimitBurst = 5;
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
      NETWORK_RANGES = "auto";
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
      # See network-scanner.service: WatchdogSec requires Type=notify +
      # sd_notify WATCHDOG=1 pings. Python local-portal has neither, so the
      # watchdog silently no-ops. Dropped until portal grows sd_notify.
      StartLimitIntervalSec = 300;
      StartLimitBurst = 5;
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
    wireguard-tools
    compliance-agent appliance-daemon-go network-scanner local-portal
    jq yq
    sbctl  # UEFI Secure Boot key management
    # 2026-04-22 FIX-5: nixos-rebuild is normally pulled in by the NixOS
    # installer-tools module on disk images, but physical-appliance-pilot
    # -1aea78 surfaced `Failed to find executable nixos-rebuild` in Feb
    # 2026 — the installed-system closure was missing it entirely, so
    # every nixos_rebuild admin_order failed until an operator manually
    # rebuilt via SSH. Pinning it in systemPackages guarantees
    # /run/current-system/sw/bin/nixos-rebuild exists on every future
    # install, making NixOS-level remediation reliably deliverable.
    nixos-rebuild
    # Session 206 fix: msp-auto-provision's signature-verify script imports
    # `from nacl.signing import VerifyKey`. Previously pynacl wasn't in the
    # installed system's python path → import failed silently → script
    # looped forever on /api/provision polls. Ship python3 with pynacl so
    # /run/current-system/sw/bin/python3 has nacl available.
    (python3.withPackages (ps: [ ps.pynacl ]))
  ];

  # ============================================================================
  # Networking
  # ============================================================================
  networking = {
    useDHCP = true;

    # FIX-9 (v40, 2026-04-23) — pin origin names to the VPS IP deterministically.
    # Rationale: `networking.firewall.extraCommands` used to do `host -t A
    # api.osiriscare.net` at rule-apply time and pin whatever Cloudflare
    # returned. Cloudflare rotates frontend IPs under the name, so the
    # pinned set goes stale and the daemon silently loses egress. Appliance
    # traffic does not benefit from CF (authenticated, per-appliance API
    # keys + Ed25519 evidence sigs carry the security weight), so go
    # direct-to-origin. Origin VPS serves a valid TLS cert for these
    # names (verified 2026-04-23 via `curl --resolve`).
    extraHosts = ''
      178.156.162.116 api.osiriscare.net
    '';

    firewall = {
      enable = true;
      # 22=ssh, 80=status, 8080=compliance-agent-sensor, 50051=grpc, 8084=local-portal
      # 8090=agent-file-server (DC downloads agent binary for auto-deploy)
      # 8443=msp-status-beacon (LAN JSON diagnostics — Session 207 silent-install remediation)
      # 8081 (scanner-api) and 8082 (go-agent-metrics) bind to localhost only
      allowedTCPPorts = [ 22 80 8080 8443 50051 8084 8090 ];
      allowedUDPPorts = [ 5353 ];

      # ======================================================================
      # Egress allowlist (Session 207 Phase H1) — SSH-strip precondition
      # ======================================================================
      # Default OUTPUT is ACCEPT on nixpkgs's firewall module, which means
      # today a compromised daemon can exfiltrate to anywhere on the
      # internet. Post-SSH-strip, outbound is the only remaining attack
      # surface — narrow it to what the appliance actually needs:
      #
      #   - api.osiriscare.net:443        phonehome, watchdog, journal upload
      #   - LAN DNS (UDP/TCP 53)          hostname resolution for WinRM targets
      #   - LAN DHCP renewal (UDP 67/68)  lease maintenance
      #   - NTP (UDP 123)                 clock sync (OTS anchoring needs accurate time)
      #   - LAN RFC1918 (10/8, 172.16/12, 192.168/16, 169.254/16)
      #                                    WinRM (5985/5986) + SSH + agent deploy
      #   - ICMP echo                     reachability diagnostics
      #
      # Default DROP for everything else. A LAN-local attacker is out of
      # scope for this rule — LAN-scoped attacks need a different defense
      # layer (TPM-sealed disk + measured boot + ingress firewall).
      # ======================================================================
      extraCommands = ''
        # Gate H1-B fix: mirror the entire egress chain on both IPv4
        # (iptables) AND IPv6 (ip6tables). The previous cut covered only
        # v4 — any AAAA-capable LAN or IPv6-resolved api.osiriscare.net
        # would have left default-deny default-open on v6.
        #
        # Gate H1-F fix: LOG rule is rate-limited (10/s burst 50) so a
        # compromised daemon generating exfil traffic can't storm
        # journald and corrupt the very journal-upload channel that
        # captures the violation.

        # ───────────── IPv4 chain ─────────────
        iptables -F MSP_EGRESS 2>/dev/null || true
        iptables -X MSP_EGRESS 2>/dev/null || true
        iptables -N MSP_EGRESS

        iptables -A MSP_EGRESS -o lo -j RETURN
        iptables -A MSP_EGRESS -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN

        # FIX-9 (v40, 2026-04-23) — hardcoded origin allowlist, no runtime DNS.
        # The previous cut did `host -t A api.osiriscare.net` here and
        # pinned Cloudflare's rotating frontend IPs. When CF rotated, the
        # firewall went deaf. `networking.extraHosts` above ensures every
        # userspace resolver answers `api.osiriscare.net → 178.156.162.116`,
        # so hardcoding the origin IP here is a consistent allowlist, not
        # a divergent one. The `msp-egress-selfheal.service` (FIX-10) is
        # the self-healing layer if the origin VPS ever moves.
        iptables -A MSP_EGRESS -p tcp -d 178.156.162.116/32 --dport 443 -j RETURN

        iptables -A MSP_EGRESS -p udp --dport 53 -j RETURN
        iptables -A MSP_EGRESS -p tcp --dport 53 -j RETURN
        iptables -A MSP_EGRESS -p udp --dport 67:68 -j RETURN
        iptables -A MSP_EGRESS -p udp --dport 123 -j RETURN
        for cidr in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16; do
          iptables -A MSP_EGRESS -d "$cidr" -j RETURN
        done
        iptables -A MSP_EGRESS -p icmp --icmp-type echo-request -j RETURN
        iptables -A MSP_EGRESS -p icmp --icmp-type echo-reply -j RETURN

        # Rate-limited LOG (Gate H1-F)
        iptables -A MSP_EGRESS -m limit --limit 10/sec --limit-burst 50 \
            -j LOG --log-prefix "MSP_EGRESS_DROP: " --log-level 4
        iptables -A MSP_EGRESS -j DROP

        iptables -D OUTPUT -j MSP_EGRESS 2>/dev/null || true
        iptables -A OUTPUT -j MSP_EGRESS

        # ───────────── IPv6 chain (Gate H1-B fix) ─────────────
        ip6tables -F MSP_EGRESS6 2>/dev/null || true
        ip6tables -X MSP_EGRESS6 2>/dev/null || true
        ip6tables -N MSP_EGRESS6

        ip6tables -A MSP_EGRESS6 -o lo -j RETURN
        ip6tables -A MSP_EGRESS6 -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN

        # FIX-9 (v40, 2026-04-23) — no IPv6 egress for the origin.
        # VPS 178.156.162.116 is IPv4-only. A runtime `host -t AAAA` lookup
        # here previously returned Cloudflare's v6 frontends, pinned them,
        # and then went deaf on rotation — same bug class as v4. Since
        # the origin has no v6, there's nothing to allow. If an IPv6-
        # capable daemon tries the v6 path, it fails fast, and Go's
        # `net.DefaultResolver` happy-eyeballs back to v4 → `extraHosts`
        # pin → `178.156.162.116` → MSP_EGRESS allows it.

        # DNS + DHCPv6 + NTP + ICMPv6 (NDP is required for IPv6 to work at all)
        ip6tables -A MSP_EGRESS6 -p udp --dport 53 -j RETURN
        ip6tables -A MSP_EGRESS6 -p tcp --dport 53 -j RETURN
        ip6tables -A MSP_EGRESS6 -p udp --dport 546:547 -j RETURN
        ip6tables -A MSP_EGRESS6 -p udp --dport 123 -j RETURN
        ip6tables -A MSP_EGRESS6 -p ipv6-icmp -j RETURN

        # LAN ULA + link-local — WinRM / SSH on IPv6-capable LANs
        for cidr in fc00::/7 fe80::/10; do
          ip6tables -A MSP_EGRESS6 -d "$cidr" -j RETURN
        done

        ip6tables -A MSP_EGRESS6 -m limit --limit 10/sec --limit-burst 50 \
            -j LOG --log-prefix "MSP_EGRESS6_DROP: " --log-level 4
        ip6tables -A MSP_EGRESS6 -j DROP

        ip6tables -D OUTPUT -j MSP_EGRESS6 2>/dev/null || true
        ip6tables -A OUTPUT -j MSP_EGRESS6
      '';
      extraStopCommands = ''
        iptables -D OUTPUT -j MSP_EGRESS 2>/dev/null || true
        iptables -F MSP_EGRESS 2>/dev/null || true
        iptables -X MSP_EGRESS 2>/dev/null || true
        ip6tables -D OUTPUT -j MSP_EGRESS6 2>/dev/null || true
        ip6tables -F MSP_EGRESS6 2>/dev/null || true
        ip6tables -X MSP_EGRESS6 2>/dev/null || true
      '';
    };
  };

  # Gate H1-D: daily full re-apply of the egress firewall rules. Under
  # v40's FIX-9 (IPs pinned via networking.extraHosts, no runtime DNS
  # inside the firewall block), this is a safety-net — the MSP_EGRESS
  # chain is already deterministic at activation. The daily tick exists
  # to catch manual drift or partial service failures, and as a place
  # to hook a future VPS-migration update flow.
  systemd.services.msp-egress-refresh = {
    description = "Re-apply egress firewall rules (daily safety-net)";
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${pkgs.systemd}/bin/systemctl reload-or-restart firewall.service";
    };
  };
  systemd.timers.msp-egress-refresh = {
    description = "Timer: refresh egress allowlist once per day";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "10min";
      OnUnitActiveSec = "24h";
      AccuracySec = "5min";
      RandomizedDelaySec = "30min";  # scatter across the fleet
      Persistent = true;
    };
  };

  # FIX-10 (v40, 2026-04-23) — 60-second self-heal for MSP_EGRESS.
  #
  # The daily msp-egress-refresh above is coarse; if MSP_EGRESS gets
  # flushed (operator error, partial firewall service failure, NixOS
  # activation hiccup), the appliance loses egress and stays dark for
  # up to 24 h. This timer inspects the chain every 60 s and, if the
  # origin allow rule for 178.156.162.116 is missing, triggers a
  # firewall service reload that rebuilds the chain from config.
  #
  # The script writes a structured log line on every heal action so
  # the log shipper's ERROR alerting catches it — a silent heal is
  # just as dangerous as a silent drop.
  systemd.services.msp-egress-selfheal = {
    description = "Self-heal MSP_EGRESS if origin allow rule is missing";
    path = [ pkgs.iptables pkgs.systemd pkgs.gawk pkgs.gnugrep ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = pkgs.writeShellScript "msp-egress-selfheal" ''
        set -u
        ORIGIN_IP="178.156.162.116"

        # If the chain doesn't exist OR doesn't contain an allow rule
        # for the origin on :443, trigger a firewall reload. We look
        # for the hardcoded IP with or without /32 mask.
        if ! ${pkgs.iptables}/bin/iptables -S MSP_EGRESS 2>/dev/null \
             | ${pkgs.gnugrep}/bin/grep -Eq -- \
               "-A MSP_EGRESS .*-d ''${ORIGIN_IP}(/32)?.*--dport 443.*-j RETURN"; then
          echo "MSP_EGRESS_SELFHEAL: origin allow rule missing for ''${ORIGIN_IP} — reloading firewall" \
            | ${pkgs.systemd}/bin/systemd-cat -t msp-egress-selfheal -p err
          ${pkgs.systemd}/bin/systemctl reload-or-restart firewall.service || true
        fi
        exit 0
      '';
    };
  };
  systemd.timers.msp-egress-selfheal = {
    description = "Timer: verify MSP_EGRESS origin allow rule every 60 s";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "2min";
      OnUnitActiveSec = "60s";
      AccuracySec = "10s";
      # No RandomizedDelaySec — scatter is irrelevant; this is per-box
      # health, not a coordinated fleet operation.
    };
  };

  # Secondary link-local IP — deterministic fallback for agent connectivity.
  # Agents can target 169.254.88.1:50051 when mDNS is blocked and DHCP drifts.
  # Link-local (169.254.x.x) requires no router config and no collision risk.
  systemd.services.appliance-static-ip = {
    description = "Add secondary link-local IP for agent discovery";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = "${pkgs.bash}/bin/bash -c '${pkgs.iproute2}/bin/ip addr add 169.254.88.1/24 dev $(${pkgs.iproute2}/bin/ip -o route get 1.1.1.1 | ${pkgs.gawk}/bin/awk \"{print \\$5}\") 2>/dev/null || true'";
    };
  };

  services.avahi = {
    enable = true;
    nssmdns4 = true;
    publish = {
      enable = true;   # Publish gRPC service for agent mDNS discovery
      addresses = true; # Publish hostname.local → IP
      workstation = false;
    };
    extraServiceFiles = {
      osiris-grpc = ''
        <?xml version="1.0" standalone='no'?>
        <!DOCTYPE service-group SYSTEM "avahi-service.dtd">
        <service-group>
          <name>OsirisCare Appliance</name>
          <service>
            <type>_osiris-grpc._tcp</type>
            <port>50051</port>
          </service>
        </service-group>
      '';
      # v36: beacon mDNS advertisement. Operators on the LAN can now
      # `avahi-browse -t _osiriscare-beacon._tcp` or
      # `dns-sd -B _osiriscare-beacon._tcp` to locate the box without
      # needing the IP. Powers the "box is broken, let me curl the
      # beacon" troubleshooting path.
      osiris-beacon = ''
        <?xml version="1.0" standalone='no'?>
        <!DOCTYPE service-group SYSTEM "avahi-service.dtd">
        <service-group>
          <name>OsirisCare Status Beacon</name>
          <service>
            <type>_osiriscare-beacon._tcp</type>
            <port>8443</port>
            <txt-record>schema=1</txt-record>
            <txt-record>endpoints=/,/status,/net-survey,/boot-diag</txt-record>
          </service>
        </service-group>
      '';
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
  # Static IP from config.yaml (optional — falls back to DHCP if not set)
  # Add "static_ip: 192.168.88.247/24" and "gateway: 192.168.88.1" to config.yaml
  # Requires nixos-rebuild after adding the service; IP applied on every boot.
  # ============================================================================
  systemd.services.msp-static-ip = {
    description = "MSP Static IP from config.yaml";
    wantedBy = [ "network-online.target" ];
    after = [ "network.target" ];
    before = [ "network-online.target" ];

    path = with pkgs; [ iproute2 yq gnugrep ];

    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };

    script = ''
      CONFIG="/var/lib/msp/config.yaml"
      [ -f "$CONFIG" ] || exit 0

      STATIC_IP=$(yq -r '.static_ip // empty' "$CONFIG" 2>/dev/null)
      [ -z "$STATIC_IP" ] && exit 0

      GATEWAY=$(yq -r '.gateway // empty' "$CONFIG" 2>/dev/null)

      # Find first non-loopback interface
      IFACE=$(ip -o link show | grep -v lo: | head -1 | awk -F': ' '{print $2}')
      [ -z "$IFACE" ] && exit 1

      echo "Applying static IP $STATIC_IP on $IFACE (gateway: $GATEWAY)"

      # Flush DHCP addresses and set static
      ip addr flush dev "$IFACE"
      ip addr add "$STATIC_IP" dev "$IFACE"
      ip link set "$IFACE" up

      if [ -n "$GATEWAY" ]; then
        ip route replace default via "$GATEWAY" dev "$IFACE"
      fi
    '';
  };

  # ============================================================================
  # DNS: Extra hosts + AD DNS from config.yaml (survives rebuilds)
  # Reads "extra_hosts" map and "ad_dns_server" from config.yaml.
  # Writes entries to /etc/hosts (replacing NixOS symlink if needed).
  # Example config.yaml:
  #   ad_dns_server: "192.168.88.250"
  #   extra_hosts:
  #     NVDC01: "192.168.88.250"
  #     NVSRV01: "192.168.88.251"
  # ============================================================================
  systemd.services.msp-dns-hosts = {
    description = "MSP DNS/Hosts from config.yaml";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" ];

    path = with pkgs; [ yq coreutils gnused ];

    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };

    script = ''
      CONFIG="/var/lib/msp/config.yaml"
      [ -f "$CONFIG" ] || exit 0

      # Read extra_hosts map from config.yaml
      EXTRA_HOSTS=$(yq -r '.extra_hosts // {} | to_entries[] | .value + " " + .key' "$CONFIG" 2>/dev/null)
      [ -z "$EXTRA_HOSTS" ] && exit 0

      # Ensure /etc/hosts is a regular file (NixOS makes it a symlink)
      if [ -L /etc/hosts ]; then
        cp "$(readlink -f /etc/hosts)" /etc/hosts.tmp
        rm /etc/hosts
        mv /etc/hosts.tmp /etc/hosts
      fi

      # Remove old MSP-managed entries and append new ones
      sed -i '/# MSP-MANAGED/d' /etc/hosts
      echo "$EXTRA_HOSTS" | while read -r line; do
        echo "$line # MSP-MANAGED" >> /etc/hosts
      done

      echo "Applied extra hosts from config.yaml"
    '';
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
    mkdir -p /var/lib/msp/ca
    mkdir -p /etc/msp/certs
    chmod 700 /var/lib/msp /var/lib/msp/ca /etc/msp/certs

    # v37: ensure the status beacon has something to serve the instant
    # the HTTP listener binds, even before msp-beacon-refresh has fired.
    # Closes the "curl :8443 → 503 beacon.json not yet written" window
    # that confused the t740 install debug. This stub is overwritten by
    # the first beacon-refresh tick; operator never sees it after ~15s.
    if [ ! -f /var/lib/msp/beacon.json ]; then
      cat > /var/lib/msp/beacon.json <<'STUB'
    {
      "schema_version": 2,
      "state": "initializing",
      "last_error": "beacon-refresh has not yet run; this is the installed-system stub written at activation",
      "boot_stage": "installed_system",
      "daemon_status": "unknown",
      "dns_test": "unknown",
      "config_yaml_present": false,
      "msp_data_mounted": false,
      "last_phonehome_unix": 0,
      "uptime_seconds": 0,
      "network": [],
      "net_survey": null,
      "provision_log_tail": null
    }
    STUB
    fi
  '';

  # Central Command Ed25519 public key — used to verify provisioning config signatures
  # Replace with real hex-encoded public key before production deployment
  # Central Command Ed25519 public key — verifies provisioning config signatures.
  # This MUST match the signing key at /app/secrets/signing.key on the VPS.
  # If the key rotates, new ISOs must be built with the updated public key.
  environment.etc."msp/central-command.pub".text = "904b211dba3786764c3a3ab3723db8640295f390c196b8f3bc47ae0a47a0b0db";

  # ============================================================================
  # Phase S — SSH OFF by default, in closure for `enable_recovery_shell_24h`
  # ============================================================================
  # Out-of-the-box posture is SSH-OFF. The recovery substrate is watchdog
  # + local status beacon + boot-diag dump (Phase W/A/D) and physical-
  # console break-glass via the Phase R rotating passphrase. The
  # permanent MAC-derived backdoor + the committed operator public key
  # are both removed from the installed-system defaults.
  #
  # The `enable_recovery_shell_24h` fleet order (Session 207 Phase S
  # escape hatch) re-enables sshd for 1..24h when the watchdog's 6-order
  # whitelist can't recover a wedged main daemon. For that to work, the
  # sshd unit MUST be present in the NixOS closure. So:
  #
  #   - `services.openssh.enable = true` keeps the unit + binaries in
  #     the closure on disk, BUT
  #   - `systemd.services.sshd.wantedBy = lib.mkForce []` removes the
  #     multi-user.target wants-link, so it does NOT auto-start on boot.
  #
  # The watchdog does `systemctl start sshd` when the fleet order runs;
  # systemd-run arms a one-shot expire timer that does `systemctl stop
  # sshd` + wipes the authorized_keys file when it fires. Timer is
  # systemd-enforced — operator oversight can fail; the timer can't.
  #
  # AuthorizedKeysFile is pointed at a dedicated break-glass file —
  # NOT /root/.ssh/authorized_keys — so the watchdog's per-session
  # writes can't accidentally accumulate into a permanent key store.
  # v40.1 rescue posture: sshd ON BY DEFAULT, msp-user password auth.
  # Rationale: v40 Phase 0 bricked 3/3 reflashed boxes because the Phase 0
  # hang blocked multi-user.target AND the watchdog couldn't run a fleet
  # order to turn sshd on. Chicken-and-egg. Until 24h stable-soak passes,
  # SSH on boot is the only reliable rescue path. "Cut the cord" once v41
  # proves Phase 0 doesn't hang in the wild — flip these two back to the
  # pre-v40.1 values and re-test.
  #
  # msp-user password is the Phase 0 break-glass passphrase, retrievable
  # by authenticated admin via /api/admin/appliance/{aid}/break-glass
  # (5/hr rate limit, audit bundle on every retrieval).
  #
  # v40.2 escape hatch: a second AuthorizedKeysFile
  # (/etc/msp-rescue-authorized-keys) is baked into the image declaratively
  # with the operator's ed25519 pubkey. This gives a non-circular rescue
  # path when break-glass submit hasn't happened yet (no CC row → no
  # passphrase → no password auth). MUST be removed in v41 alongside the
  # sshd-on-boot flip.
  services.openssh = {
    enable = lib.mkForce true;
    settings = {
      PermitRootLogin = lib.mkForce "prohibit-password";
      PasswordAuthentication = lib.mkForce true;          # v40.1: rescue
      AuthorizedKeysFile = lib.mkForce "/etc/msp-recovery-authorized-keys /etc/msp-rescue-authorized-keys";
      AllowUsers = [ "msp" ];                              # v40.1: msp ONLY
      StrictModes = true;
    };
  };

  # v40.2: operator rescue pubkey, baked into the image. Read-only symlink
  # into the nix store. Watchdog's per-session writes still go to
  # /etc/msp-recovery-authorized-keys (mutable, separate file).
  environment.etc."msp-rescue-authorized-keys" = {
    text = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBv6abzJDSfxWt00y2jtmZiubAiehkiLe/7KBot+6JHH jbouey@osiriscare.net\n";
    mode = "0444";
  };

  # v40.1: sshd auto-starts at boot. Rescue posture — reverted in v41
  # once the Phase 0 fix is soaked.
  systemd.services.sshd.wantedBy = lib.mkForce [ "multi-user.target" ];

  # Root password intentionally left unset (lib.mkDefault allows a
  # top-level override for lab builds that want an override via SOPS).
  # Default installed image = no root password, no SSH. Break-glass is
  # via the `msp` user at the physical TTY with the Phase R passphrase.
  users.users.root.hashedPassword = lib.mkDefault "!";
  users.users.root.openssh.authorizedKeys.keys = lib.mkForce [];

  # ============================================================================
  # Reduce image size
  # ============================================================================
  documentation.enable = false;
  documentation.man.enable = false;
  documentation.nixos.enable = false;
  programs.command-not-found.enable = false;

  # ============================================================================
  # Auto-Install Service — REMOVED (Session 205 bug fix)
  #
  # Previous version was a clone-and-reboot service that ran on every boot.
  # On devices where the internal disk reported `removable=1` in sysfs
  # (some M.2/NVMe drives, eMMC, certain HP T740/T640 configurations),
  # the service INCORRECTLY identified the internal drive as a USB and
  # triggered a clone + reboot, creating an INFINITE BOOT LOOP.
  #
  # osiriscare-3 at 192.168.88.232 was stuck in this loop for 18+ hours:
  # uptime never exceeded ~107 seconds, rebooting every cycle.
  #
  # The installer ISO (`appliance-image.nix`) is solely responsible for
  # installation. The installed system (this file) should NEVER attempt
  # to reinstall itself. Removed entirely.
  #
  # If the marker file `/var/lib/msp/.installed-to-internal` exists on
  # previously-deployed appliances, it's harmless leftover state.
  # ============================================================================

  # ============================================================================
  # Secure Boot Key Generation (first boot only)
  # ============================================================================
  systemd.services.msp-secureboot-keygen = {
    description = "Generate UEFI Secure Boot keys if not present";
    wantedBy = [ "multi-user.target" ];
    after = [ "local-fs.target" ];
    unitConfig.ConditionPathExists = "!/etc/secureboot/keys/db/db.key";
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    script = ''
      echo "Generating UEFI Secure Boot keys..."
      ${pkgs.sbctl}/bin/sbctl create-keys
      echo "Secure Boot keys generated at /etc/secureboot/keys/"
      echo "Enroll keys with: sbctl enroll-keys --microsoft"
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
    # NOTE: no "before = appliance-daemon" — daemon starts regardless
    # and retries its own auth. This avoids blocking boot when
    # provisioning is delayed (DNS filter, offline subnet, etc.).

    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      # v40.4: self-heal on transient failure (DNS race at boot,
      # provision endpoint flap, Central Command 5xx). Prior to v40.4
      # this was a one-shot with no retry — a single exit!=0 left the
      # box in a permanently-failed state requiring a power-cycle to
      # retry. The StartLimitBurst/Interval caps prevent a truly-broken
      # config (bad pubkey, unreachable API) from restart-spinning.
      Restart = "on-failure";
      RestartSec = "30s";
    };
    unitConfig = {
      # 10 attempts over 10 min ~ covers DHCP lease acquisition, DNS
      # propagation, brief CC 5xx. After that the unit goes to
      # failed-permanent and the operator/watchdog takes over.
      StartLimitBurst = 10;
      StartLimitIntervalSec = "600";
    };

    script = ''
      set -euo pipefail
      CONFIG_PATH="/var/lib/msp/config.yaml"
      LOG_FILE="/var/lib/msp/provision.log"
      API_URL="https://api.osiriscare.net"
      PROVISION_SUCCESS_MARKER="/var/lib/msp/.first-provision-success"

      log() {
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $1" | tee -a "$LOG_FILE"
      }

      # v40.4: idempotency guard. If a prior boot successfully wrote
      # config.yaml AND left the marker, skip re-provisioning. The
      # daemon handles any needed key rotation via /api/provision/rekey
      # on its own — this script's only job is the FIRST provision.
      # Re-entering it on every boot would churn api_keys rows and
      # pointlessly burn /api/provision/{mac} server time.
      if [ -f "$PROVISION_SUCCESS_MARKER" ] && [ -f "$CONFIG_PATH" ]; then
        log "Idempotency: config.yaml + success marker present — skipping re-provision."
        exit 0
      fi

      # v40.3 (2026-04-23): fail loud if any referenced store binary is
      # missing. This catches the class of bug that bricked 3/3 v40.x
      # reflashed boxes: the pkgs.inetutils/bin/host reference silently
      # stopped resolving after nixpkgs moved the host tool to
      # bind.host (split output), which made the DNS-gate's first
      # external command exit 127. `set -euo pipefail` then killed the
      # whole script — no config.yaml written, daemon restart-looped
      # forever, no checkin ever, no beacon, no breakglass submit.
      # Five services blocked behind one wrong attribute path. Hard-
      # failing here surfaces the next such regression in the first
      # journal line instead of 29 minutes into a silent restart storm.
      _bg_bin_check() {
        local _bin
        for _bin in "$@"; do
          if [ ! -x "$_bin" ]; then
            log "FATAL: missing referenced binary $_bin — nixpkgs pin likely regressed; see iso/appliance-disk-image.nix v40.3 change log."
            exit 2
          fi
        done
      }
      _bg_bin_check \
        ${pkgs.bind.host}/bin/host \
        ${pkgs.gawk}/bin/awk \
        ${pkgs.curl}/bin/curl \
        ${pkgs.jq}/bin/jq \
        ${pkgs.yq}/bin/yq \
        ${pkgs.gnugrep}/bin/grep

      # Verify provisioning config signature from Central Command
      # Returns 0 if valid, 1 if invalid/missing
      verify_provision_signature() {
        local response_file="$1"
        CONFIG_SIG=$(${pkgs.jq}/bin/jq -r '.signature // empty' "$response_file" 2>/dev/null)
        if [ -z "$CONFIG_SIG" ]; then
          log "ERROR: Provisioning response not signed — rejecting"
          return 1
        fi

        # The Central Command Ed25519 public key is baked into the ISO
        VERIFY_RESULT=$(echo "$CONFIG_SIG" | /run/current-system/sw/bin/python3 -c "
import sys, json, hashlib
from nacl.signing import VerifyKey
from nacl.encoding import HexEncoder
PUBKEY = open('/etc/msp/central-command.pub').read().strip()
config = json.dumps(json.load(open('$response_file'))['config'], sort_keys=True)
try:
    vk = VerifyKey(PUBKEY, encoder=HexEncoder)
    vk.verify(config.encode(), bytes.fromhex(sys.stdin.read().strip()))
    print('VALID')
except Exception as e:
    print(f'INVALID: {e}')
" 2>/dev/null || echo "INVALID: python error")

        if [ "$VERIFY_RESULT" != "VALID" ]; then
          log "ERROR: Provisioning config signature $VERIFY_RESULT — rejecting"
          return 1
        fi
        log "Provisioning config signature verified"
        return 0
      }

      # Write SSH key from provisioning response to root authorized_keys
      write_ssh_key() {
        local response_file="$1"
        SSH_PUBKEY=$(${pkgs.jq}/bin/jq -r '.ssh_pubkey // empty' "$response_file" 2>/dev/null)
        if [ -n "$SSH_PUBKEY" ]; then
          mkdir -p /root/.ssh
          echo "$SSH_PUBKEY" > /root/.ssh/authorized_keys
          chmod 600 /root/.ssh/authorized_keys
          chmod 700 /root/.ssh
          log "SSH key provisioned from Central Command"
        fi
      }

      # v40 FIX-11 (2026-04-23) — install-gate network diagnostic.
      # Runs DNS → TCP/443 → TLS → GET /health and writes
      # /var/lib/msp/install_gate_status.json so the beacon on :8443
      # can tell the on-LAN operator EXACTLY which stage is failing.
      # Non-blocking by design: the installed system keeps retrying
      # provisioning even when the gate fails — the gate is for
      # OBSERVABILITY, not for halting install (the installer-side
      # gate is tracked separately, TBD). DNS-filter vs egress-ACL vs
      # TLS-intercept vs app-down are now distinguishable without
      # shelling in or reading journald.
      run_network_gate_check() {
        local status_file="/var/lib/msp/install_gate_status.json"
        local tmp="$status_file.tmp"
        local now
        now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
        local dns_ok="false" dns_ip=""
        local tcp_ok="false"
        local tls_ok="false"
        local health_ok="false" health_code=""
        local last_failed="none" last_error=""

        # Stage 1: DNS — does api.osiriscare.net resolve at all?
        # v40.4: trailing `|| true` honors the "non-blocking by design"
        # contract in this function's header. Without it, `set -euo
        # pipefail` (set at script top) made a DNS race at boot (host
        # temporarily unresolvable because resolvconf hadn't written
        # /etc/resolv.conf yet) fatal-exit the WHOLE provisioning run
        # with status 1. v40.3 shipped this bug: .242 bricked, .246 got
        # lucky by a few ms of DNS-ready timing. v40.4 makes the probe
        # honest — it records failure, sets dns_ok="false", and lets
        # the outer retry loop do its job.
        dns_ip=""
        dns_ip=$(${pkgs.bind.host}/bin/host -t A api.osiriscare.net 2>/dev/null \
                 | ${pkgs.gawk}/bin/awk '/has address/ {print $4; exit}') || true
        if [ -n "$dns_ip" ]; then
          dns_ok="true"
        else
          last_failed="dns"
          last_error="host -t A api.osiriscare.net returned no A record"
        fi

        # Stage 2: TCP + TLS (insecure) to origin IP via --resolve.
        # Bypasses DNS so we isolate "can we reach the origin at all?"
        # from "does DNS point somewhere we can reach?". -k so a
        # TLS-intercept proxy doesn't get counted as a TCP failure.
        local tcp_rc=0
        ${pkgs.curl}/bin/curl -sS -k -o /dev/null \
          --connect-timeout 5 --max-time 10 \
          --resolve api.osiriscare.net:443:178.156.162.116 \
          https://api.osiriscare.net/health >/dev/null 2>&1 \
          || tcp_rc=$?
        if [ "$tcp_rc" -eq 0 ]; then
          tcp_ok="true"
        elif [ "$last_failed" = "none" ]; then
          last_failed="tcp_443"
          last_error="TCP/443 to 178.156.162.116 failed (curl exit=$tcp_rc)"
        fi

        # Stage 3: TLS-verified HTTP GET /health via the real DNS path.
        # Only runs when DNS + TCP both passed — no point testing TLS
        # trust when we already know the earlier stage is broken.
        if [ "$dns_ok" = "true" ] && [ "$tcp_ok" = "true" ]; then
          local tls_out="" tls_rc=0
          tls_out=$(${pkgs.curl}/bin/curl -sS -o /dev/null \
                    -w "%{http_code}|%{ssl_verify_result}" \
                    --connect-timeout 5 --max-time 10 \
                    https://api.osiriscare.net/health 2>&1) \
                    || tls_rc=$?
          if [ "$tls_rc" -eq 0 ] && [ -n "$tls_out" ]; then
            health_code="''${tls_out%%|*}"
            local vr="''${tls_out##*|}"
            if [ "$vr" = "0" ]; then
              tls_ok="true"
              if [ "$health_code" = "200" ]; then
                health_ok="true"
              elif [ "$last_failed" = "none" ]; then
                last_failed="health"
                last_error="/health returned HTTP $health_code"
              fi
            elif [ "$last_failed" = "none" ]; then
              last_failed="tls"
              last_error="TLS verify_result=$vr (chain/cert/intercept)"
            fi
          elif [ "$last_failed" = "none" ]; then
            last_failed="tls"
            last_error="curl TLS-verified path exit=$tls_rc"
          fi
        fi

        local all_passed="false"
        if [ "$dns_ok" = "true" ] && [ "$tcp_ok" = "true" ] \
           && [ "$tls_ok" = "true" ] && [ "$health_ok" = "true" ]; then
          all_passed="true"
          last_failed="none"
          last_error=""
        fi

        ${pkgs.jq}/bin/jq -n \
          --arg now "$now" \
          --arg dns_ok "$dns_ok" --arg dns_ip "$dns_ip" \
          --arg tcp_ok "$tcp_ok" \
          --arg tls_ok "$tls_ok" \
          --arg health_ok "$health_ok" --arg health_code "$health_code" \
          --arg last_failed "$last_failed" \
          --arg last_error "$last_error" \
          --arg all_passed "$all_passed" \
          '{
            schema_version: 1,
            last_checked_at: $now,
            dns:     {ok: ($dns_ok == "true"),    ip: $dns_ip},
            tcp_443: {ok: ($tcp_ok == "true")},
            tls:     {ok: ($tls_ok == "true")},
            health:  {ok: ($health_ok == "true"), http_code: $health_code},
            last_stage_failed: $last_failed,
            last_error: $last_error,
            all_passed: ($all_passed == "true")
          }' > "$tmp" 2>/dev/null && mv "$tmp" "$status_file" || true

        if [ "$all_passed" = "true" ]; then
          log "NETWORK_GATE: all stages passed (DNS -> TCP/443 -> TLS -> /health=200)"
        else
          log "NETWORK_GATE_FAILED stage=$last_failed err=''${last_error}"
          echo "msp-install-gate: NETWORK_GATE_FAILED stage=$last_failed last_error=''${last_error}" \
            | ${pkgs.systemd}/bin/systemd-cat -t msp-install-gate -p err
        fi
        return 0
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

      # Get primary MAC address — sorted by interface name for deterministic selection.
      # Must match the Go daemon's getAllPhysicalMACs() which also sorts by name.
      # This prevents the ghost-appliance bug where different NICs register separately.
      MAC_ADDR=""
      ALL_MACS=""
      for iface in $(ls -1 /sys/class/net/ | sort); do
        [ "$iface" = "lo" ] && continue
        echo "$iface" | grep -qE '^(wg|docker|veth|br-)' && continue
        [ -f "/sys/class/net/$iface/address" ] || continue
        CANDIDATE=$(cat "/sys/class/net/$iface/address")
        [ "$CANDIDATE" = "00:00:00:00:00:00" ] && continue
        if [ -z "$MAC_ADDR" ]; then
          MAC_ADDR="$CANDIDATE"
          log "Primary interface: $iface with MAC $MAC_ADDR"
        fi
        ALL_MACS="$ALL_MACS,$CANDIDATE"
      done
      ALL_MACS=''${ALL_MACS#,}  # strip leading comma

      # Detect boot source — live USB vs installed disk
      BOOT_SOURCE="unknown"
      if grep -q 'squashfs\|copytoram\|boot.shell_on_fail' /proc/cmdline 2>/dev/null; then
        BOOT_SOURCE="live_usb"
      elif findmnt -n -o FSTYPE / 2>/dev/null | grep -q 'tmpfs\|ramfs'; then
        BOOT_SOURCE="live_usb"
      elif [ -e /dev/disk/by-label/MSP-DATA ]; then
        BOOT_SOURCE="installed_disk"
      else
        BOOT_SOURCE="installed_disk"
      fi
      log "Boot source: $BOOT_SOURCE"

      if [ -z "$MAC_ADDR" ]; then
        log "ERROR: Could not determine MAC address"
        log "Auto-provisioning failed - manual configuration required"
        log "Options: 1) Insert USB with config.yaml  2) Register MAC in dashboard"
        exit 1
      fi

      MAC_ENCODED=$(echo "$MAC_ADDR" | sed 's/:/%3A/g')
      PROVISION_URL="$API_URL/api/provision/$MAC_ENCODED"

      # v40 FIX-11: snapshot network gate BEFORE any provisioning curl.
      # The beacon surfaces which stage is failing to LAN operators.
      run_network_gate_check

      # Phase 1: Initial connectivity retries (6 attempts, 10s apart)
      # Handles network not ready yet after boot.
      INITIAL_RETRIES=6
      RETRY_DELAY=10
      REGISTERED=false

      for attempt in $(seq 1 $INITIAL_RETRIES); do
        log "Attempt $attempt/$INITIAL_RETRIES: Fetching config..."
        HTTP_CODE=$(${pkgs.curl}/bin/curl -s -w "%{http_code}" -o /tmp/provision-response.json \
          --connect-timeout 15 --max-time 45 "$PROVISION_URL" 2>/dev/null || echo "000")

        if [ "$HTTP_CODE" = "200" ]; then
          if ${pkgs.jq}/bin/jq -e '.site_id' /tmp/provision-response.json >/dev/null 2>&1; then
            if ! verify_provision_signature /tmp/provision-response.json; then
              rm -f /tmp/provision-response.json
              sleep $RETRY_DELAY
              continue
            fi
            ${pkgs.yq}/bin/yq -y '.config' /tmp/provision-response.json > "$CONFIG_PATH"
            chmod 600 "$CONFIG_PATH"
            write_ssh_key /tmp/provision-response.json
            log "SUCCESS: Provisioning complete via MAC lookup"
            touch "$PROVISION_SUCCESS_MARKER"
            rm -f /tmp/provision-response.json
            exit 0
          fi
          # 200 but no site_id means unclaimed — MAC registered, awaiting claim
          REGISTERED=true
          log "Appliance registered but unclaimed — entering polling mode"
          break
        elif [ "$HTTP_CODE" = "404" ]; then
          log "MAC not registered (HTTP 404). Register: $MAC_ADDR"
          break
        fi
        rm -f /tmp/provision-response.json
        sleep $RETRY_DELAY
      done

      # Phase 2: Drop-ship polling (unclaimed appliance waits for admin to claim it).
      # Polls every 60s, max 24 hours. Once claimed, Central Command returns site_id + api_key.
      if [ "$REGISTERED" = true ] || [ "$HTTP_CODE" = "200" ]; then
        POLL_DELAY=60
        POLL_COUNT=0
        POLL_START=$(date +%s)
        MAX_POLL_SECS=86400  # 24 hours
        log "Drop-ship mode: polling every ''${POLL_DELAY}s for site assignment (24h timeout)..."
        log "Claim this appliance in Central Command dashboard: MAC=$MAC_ADDR"

        while true; do
          ELAPSED=$(( $(date +%s) - POLL_START ))
          if [ $ELAPSED -gt $MAX_POLL_SECS ]; then
            log "ERROR: Provisioning timeout after 24 hours. Manual claim required."
            log "Register MAC $MAC_ADDR in Central Command, then reboot appliance."
            break
          fi
          POLL_COUNT=$((POLL_COUNT + 1))
          sleep $POLL_DELAY

          HTTP_CODE=$(${pkgs.curl}/bin/curl -s -w "%{http_code}" -o /tmp/provision-response.json \
            --connect-timeout 15 --max-time 45 "$PROVISION_URL" 2>/dev/null || echo "000")

          if [ "$HTTP_CODE" = "200" ]; then
            if ${pkgs.jq}/bin/jq -e '.site_id' /tmp/provision-response.json >/dev/null 2>&1; then
              if ! verify_provision_signature /tmp/provision-response.json; then
                rm -f /tmp/provision-response.json
                continue
              fi
              ${pkgs.yq}/bin/yq -y '.config' /tmp/provision-response.json > "$CONFIG_PATH"
              chmod 600 "$CONFIG_PATH"
              write_ssh_key /tmp/provision-response.json
              log "SUCCESS: Provisioning complete via MAC lookup (poll #$POLL_COUNT)"
              touch "$PROVISION_SUCCESS_MARKER"
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

      # ──────────────────────────────────────────────────────────────
      # Phase 3: Persistent retry on network failure (Session 207).
      #
      # If Phase 1 exhausted all 6 fast retries without reaching Central
      # Command at all (HTTP 000 = curl connect failed = DNS/network),
      # we don't give up — we enter a slow retry loop (every 5 min,
      # indefinitely). Common in healthcare deployments where the
      # network has a DNS filter (Pi-hole, Fortinet, Umbrella, Sophos)
      # that blocks api.osiriscare.net until the IT admin whitelists it.
      #
      # Once DNS is whitelisted and the next retry succeeds, config is
      # written and appliance-daemon is restarted to pick it up.
      # ──────────────────────────────────────────────────────────────
      if [ ! -f "$CONFIG_PATH" ]; then
        SLOW_DELAY=300  # 5 minutes
        log "================================================================"
        log "PROVISIONING DELAYED — entering persistent retry (every 5 min)"
        log ""
        log "  Most likely cause: DNS filter blocking api.osiriscare.net"
        log ""
        log "  FIX: whitelist api.osiriscare.net (178.156.162.116) on port 443"
        log "  in your DNS filter / web proxy / firewall."
        log ""
        log "  Appliance MAC: $MAC_ADDR"
        log "  Provisioning URL: $PROVISION_URL"
        log "================================================================"

        SLOW_ATTEMPT=0
        while [ ! -f "$CONFIG_PATH" ]; do
          sleep $SLOW_DELAY
          SLOW_ATTEMPT=$((SLOW_ATTEMPT + 1))

          # v40 FIX-11: keep install_gate_status.json fresh every 5 min
          # so the beacon's `state` reflects the current failure stage.
          run_network_gate_check

          HTTP_CODE=$(${pkgs.curl}/bin/curl -s -w "%{http_code}" -o /tmp/provision-response.json \
            --connect-timeout 15 --max-time 45 "$PROVISION_URL" 2>/dev/null || echo "000")

          # v36 telemetry: POST every failed retry to Central Command so
          # the dashboard sees stuck installs BEFORE first successful
          # provision. Best-effort — if reporting itself fails we
          # silently continue, since it means the same underlying
          # network issue as the provisioning failure.
          if [ "$HTTP_CODE" != "200" ]; then
            # v40.4: telemetry probes must not fatal-exit the retry
            # loop. Trailing `|| true` makes these truly best-effort.
            RESOLVER_IP=$(${pkgs.gnugrep}/bin/grep '^nameserver' /etc/resolv.conf 2>/dev/null | ${pkgs.gawk}/bin/awk '{print $2}' | head -1 || true)
            RESOLVED_IP=$(${pkgs.bind.host}/bin/host -t A api.osiriscare.net 2>/dev/null | ${pkgs.gawk}/bin/awk '/has address/ {print $4}' | head -1 || true)
            ${pkgs.curl}/bin/curl -s -m 8 -X POST \
              -H "Content-Type: application/json" \
              -H "X-Install-Token: ''${INSTALL_TOKEN:-osiriscare-installer-dev-only}" \
              -d "$(${pkgs.jq}/bin/jq -n \
                --arg http "$HTTP_CODE" \
                --arg resolver "''${RESOLVER_IP:-}" \
                --arg resolved "''${RESOLVED_IP:-}" \
                --argjson attempt $SLOW_ATTEMPT \
                --arg stage "installed_system" \
                '{http_code: $http, curl_exit: 0, dns_resolver: $resolver, resolved_ip: $resolved, attempt_number: $attempt, install_stage: $stage}')" \
              "$API_URL/api/install/failure-report/$MAC_ENCODED" \
              >/dev/null 2>&1 || true
          fi

          if [ "$HTTP_CODE" = "200" ]; then
            if ${pkgs.jq}/bin/jq -e '.site_id' /tmp/provision-response.json >/dev/null 2>&1; then
              if verify_provision_signature /tmp/provision-response.json; then
                ${pkgs.yq}/bin/yq -y '.config' /tmp/provision-response.json > "$CONFIG_PATH"
                chmod 600 "$CONFIG_PATH"
                write_ssh_key /tmp/provision-response.json
                log "SUCCESS: Provisioning complete via persistent retry (attempt #$SLOW_ATTEMPT)"
                touch "$PROVISION_SUCCESS_MARKER"
                rm -f /tmp/provision-response.json
                # Kick the daemon so it picks up the new config immediately
                systemctl restart appliance-daemon 2>/dev/null || true
                exit 0
              fi
            else
              # 200 without site_id = unclaimed. Switch to Phase 2 polling.
              log "Appliance reachable, registered but unclaimed (attempt #$SLOW_ATTEMPT)"
              log "Claim MAC $MAC_ADDR in Central Command dashboard"
            fi
          fi
          rm -f /tmp/provision-response.json

          if [ $((SLOW_ATTEMPT % 12)) -eq 0 ]; then
            HOURS=$((SLOW_ATTEMPT * 5 / 60))
            log "Still waiting for provisioning (''${HOURS}h elapsed)... whitelist api.osiriscare.net"
          fi
        done
      fi

      log "Auto-provisioning exited"
    '';
  };

  # ============================================================================
  # MSP-DATA partition recovery (Mesh Hardening Phase 1 / installer v28)
  # ============================================================================
  # Second line of defense against the "MSP-DATA partition didn't get created"
  # class of installer bug (observed on .226 / .227 where the installer
  # abandoned sfdisk/mkfs on flaky eMMC controllers and booted into a 90s
  # systemd wait for /dev/disk/by-partlabel/MSP-DATA). This service runs at
  # boot, checks whether partition 3 exists with the MSP-DATA label and a
  # live ext4 filesystem, and creates it if not. Idempotent — no-op when
  # MSP-DATA is already healthy.
  # ============================================================================
  systemd.services.msp-data-partition-recovery = {
    description = "MSP-DATA partition recovery (create if missing)";
    wantedBy = [ "local-fs.target" ];
    before = [ "var-lib-msp.mount" "local-fs.target" ];
    after = [ "sysinit.target" ];
    unitConfig = {
      DefaultDependencies = false;
    };
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    path = with pkgs; [ util-linux gptfdisk e2fsprogs coreutils ];
    script = ''
      set -u
      LOG="/var/log/msp-data-partition-recovery.log"
      mkdir -p /var/lib/msp /var/log
      exec >> "$LOG" 2>&1
      date -u +"=== %Y-%m-%dT%H:%M:%SZ msp-data-partition-recovery start ==="

      # Find the root block device (it's the parent of the mounted /).
      # e.g. /dev/sda2 → /dev/sda, /dev/mmcblk0p2 → /dev/mmcblk0.
      ROOT_SRC=$(findmnt -n -o SOURCE / | head -1)
      case "$ROOT_SRC" in
        */mapper/*|/dev/dm-*)
          echo "Root is on LVM/dm — recovery not applicable"
          exit 0
          ;;
      esac
      # Strip trailing partition suffix: nvme0n1p2 or mmcblk0p2 → …p, then drop.
      PARENT=$(lsblk -no PKNAME "$ROOT_SRC" 2>/dev/null || true)
      if [ -z "$PARENT" ]; then
        echo "Cannot determine parent disk of $ROOT_SRC — skipping"
        exit 0
      fi
      DISK="/dev/$PARENT"
      case "$DISK" in
        *nvme*|*mmcblk*) DATA_PART="''${DISK}p3" ;;
        *)               DATA_PART="''${DISK}3"  ;;
      esac
      echo "Disk=$DISK  DataPart=$DATA_PART"

      msp_data_ok() {
        [ -b "$DATA_PART" ] || return 1
        partlabel=$(blkid -o value -s PARTLABEL "$DATA_PART" 2>/dev/null || true)
        fslabel=$(blkid -o value -s LABEL "$DATA_PART" 2>/dev/null || true)
        fstype=$(blkid -o value -s TYPE "$DATA_PART" 2>/dev/null || true)
        [ "$partlabel" = "MSP-DATA" ] && [ "$fslabel" = "MSP-DATA" ] && [ "$fstype" = "ext4" ]
      }

      if msp_data_ok; then
        echo "MSP-DATA already healthy — no action"
        exit 0
      fi

      echo "MSP-DATA missing or unhealthy; checking if partition 3 has salvageable data before recovery"

      # ── Pre-delete safety probe ────────────────────────────────
      # msp_data_ok() returns false on a strict match (PARTLABEL+FSLABEL+
      # FSTYPE all equal MSP-DATA/ext4). A field appliance whose partition
      # 3 has a different label but still holds live evidence bundles must
      # NOT be blown away. Before running `sfdisk --delete`, mount partition
      # 3 read-only and abort the recovery if it contains any files.
      if [ -b "$DATA_PART" ]; then
        mkdir -p /tmp/msp-data-probe
        if mount -o ro "$DATA_PART" /tmp/msp-data-probe 2>/dev/null; then
          probe_content=$(ls -A /tmp/msp-data-probe 2>/dev/null || true)
          umount /tmp/msp-data-probe 2>/dev/null || true
          if [ -n "$probe_content" ]; then
            echo "ABORT: partition 3 is mountable AND non-empty — refusing to delete. Operator must inspect manually:"
            echo "  ls -la /tmp/msp-data-probe (after 'mount -o ro $DATA_PART /tmp/msp-data-probe')"
            echo "  If this is stale data, remove it and re-run: systemctl restart msp-data-partition-recovery"
            exit 0
          fi
          echo "partition 3 mountable but empty — safe to reformat"
        else
          echo "partition 3 not mountable — safe to reformat"
        fi
      fi

      DISK_SECTORS=$(blockdev --getsz "$DISK")
      DATA_SECTORS=4194304  # 2GB / 512-byte sectors
      GPT_BACKUP=34

      if [ "$DISK_SECTORS" -lt $(( DATA_SECTORS + GPT_BACKUP + 2048 )) ]; then
        echo "Disk smaller than 2GB + headroom; cannot create MSP-DATA tail"
        exit 0
      fi

      # Drop the half-built partition 3 (verified empty/unmountable above).
      sfdisk --delete "$DISK" 3 2>/dev/null || true
      partprobe "$DISK" 2>/dev/null || true
      sleep 1

      DATA_START_SECTOR=$(( DISK_SECTORS - GPT_BACKUP - DATA_SECTORS ))
      echo "Creating partition 3 at sector $DATA_START_SECTOR size $DATA_SECTORS"
      sgdisk -n 3:$DATA_START_SECTOR:0 -t 3:8300 -c 3:MSP-DATA "$DISK"
      partprobe "$DISK" 2>/dev/null || true
      sleep 1

      # Wait for the kernel to surface the new partition node.
      for _i in 1 2 3 4 5; do
        [ -b "$DATA_PART" ] && break
        sleep 1
      done

      if [ ! -b "$DATA_PART" ]; then
        echo "Partition $DATA_PART did not appear; giving up"
        exit 0
      fi

      mkfs.ext4 -F -L MSP-DATA "$DATA_PART"

      if msp_data_ok; then
        echo "MSP-DATA recovery successful"
      else
        echo "MSP-DATA still not healthy after recovery attempt"
      fi
    '';
  };

  # ============================================================================
  # Status beacon (Session 207 enterprise silent-install remediation, A)
  # ============================================================================
  # Local HTTP JSON status on :8443 reachable from LAN even when outbound
  # HTTPS to Central Command is broken. An operator hitting
  # http://<appliance-ip>:8443/ gets {boot_stage, daemon_status,
  # last_phonehome, network, dhcp_lease, dns_test, config_yaml_present,
  # msp_data_mounted, uptime_s}. Turns a black-box install into a
  # 60-second diagnosis.
  # ============================================================================
  systemd.services.msp-status-beacon = {
    description = "MSP local status beacon (LAN JSON on :8443)";
    wantedBy = [ "multi-user.target" ];
    # v37: also pull in msp-beacon-refresh so a fresh boot has real state
    # in beacon.json before the listener starts serving. Without this
    # ordering, the first ~10s window (OnBootSec on the refresh timer)
    # returned HTTP 503 "beacon.json not yet written" to an operator
    # actively curling for diagnostics. `wants` (not `requires`) so a
    # refresh-script failure doesn't take the listener down — the
    # activation-script stub below keeps the service serving useful JSON.
    wants = [ "msp-beacon-refresh.service" ];
    after = [ "network.target" "msp-beacon-refresh.service" ];
    # Not blocked by network-online.target — the beacon exists precisely
    # for cases where network-online never fires.
    serviceConfig = {
      Type = "simple";
      Restart = "always";
      RestartSec = 5;
      ExecStart = "${pkgs.python3}/bin/python3 /etc/msp-status-beacon.py";
      StandardOutput = "journal";
      StandardError = "journal";
    };
  };

  # Refresh the beacon JSON every 15s from the current system state.
  # v36: richer schema — includes state-machine classification, DoH
  # probe, provisioning error code (from msp-auto-provision's log),
  # and embedded net-survey from /var/lib/msp/net-survey.json. Lets a
  # LAN operator curl /status and immediately see which of the seven
  # failure modes is active, with no extra round-trips.
  systemd.services.msp-beacon-refresh = {
    description = "Refresh /var/lib/msp/beacon.json";
    serviceConfig = {
      Type = "oneshot";
    };
    path = with pkgs; [ coreutils iproute2 systemd util-linux gnugrep bash jq ];
    script = ''
      mkdir -p /var/lib/msp
      OUT=/var/lib/msp/beacon.json.tmp

      daemon_status=$(systemctl is-active appliance-daemon 2>/dev/null || echo unknown)
      network_ifaces=$(${pkgs.iproute2}/bin/ip -j addr 2>/dev/null || echo '[]')
      dns_test="fail"
      if ${pkgs.bind.host}/bin/host api.osiriscare.net 2>/dev/null | grep -q 'has address'; then
        dns_test="ok"
      fi
      config_present="false"
      if [ -f /var/lib/msp/config.yaml ]; then
        config_present="true"
      fi
      msp_data_mounted="false"
      if findmnt /var/lib/msp >/dev/null 2>&1; then
        msp_data_mounted="true"
      fi
      last_phonehome_ts=$(stat -c %Y /var/lib/msp/last_phonehome 2>/dev/null || echo 0)
      uptime_s=$(awk '{print int($1)}' /proc/uptime 2>/dev/null || echo 0)

      # v40 FIX-11: surface msp-auto-provision's network gate result.
      # install_gate_status.json is written by run_network_gate_check() in
      # msp-auto-provision.service. Absent until the first provision pass.
      install_gate="null"
      install_gate_failed_stage=""
      install_gate_all_passed="unknown"
      if [ -f /var/lib/msp/install_gate_status.json ]; then
        install_gate=$(cat /var/lib/msp/install_gate_status.json 2>/dev/null || echo "null")
        install_gate_failed_stage=$(echo "$install_gate" | ${pkgs.jq}/bin/jq -r '.last_stage_failed // "none"' 2>/dev/null || echo "none")
        install_gate_all_passed=$(echo "$install_gate" | ${pkgs.jq}/bin/jq -r '.all_passed // false' 2>/dev/null || echo "false")
      fi

      # v36 state-machine classifier (v40 FIX-11 extends with network_gate_failing).
      # Operator reads `state` + `last_error` and knows the next move.
      state="unknown"
      last_error=""
      if [ "$daemon_status" != "active" ] && [ "$daemon_status" != "activating" ]; then
        state="daemon_crashed"
        last_error="appliance-daemon.service is $daemon_status"
      elif [ "$config_present" = "false" ]; then
        state="awaiting_provision"
        last_error="config.yaml missing; msp-auto-provision has not completed"
      elif [ "$dns_test" = "fail" ]; then
        state="dns_filter_suspected"
        last_error="cannot resolve api.osiriscare.net via local DNS"
      elif [ "$install_gate_all_passed" = "false" ] && [ "$install_gate_failed_stage" != "none" ] && [ -n "$install_gate_failed_stage" ]; then
        # Install gate ran and failed at a specific stage — more specific
        # than the generic auth_or_network_failing fallback below. The
        # stage name (tcp_443, tls, health) tells the LAN operator which
        # layer is broken without them having to parse the JSON file.
        state="network_gate_failing"
        last_error="install gate failed at stage: $install_gate_failed_stage"
      elif [ "$last_phonehome_ts" -eq 0 ] || [ $(( $(date +%s) - $last_phonehome_ts )) -gt 900 ]; then
        state="auth_or_network_failing"
        last_error="last successful phonehome > 15 min ago; daemon may be auth-looping or egress-blocked"
      else
        state="online"
      fi

      # Optional payloads — include if present.
      net_survey="null"
      if [ -f /var/lib/msp/net-survey.json ]; then
        net_survey=$(cat /var/lib/msp/net-survey.json)
      fi
      provision_log_tail="null"
      if [ -f /var/lib/msp/provision.log ]; then
        # Last 20 lines, JSON-escaped (base64-safe wrapper via jq)
        provision_log_tail=$(tail -n 20 /var/lib/msp/provision.log | ${pkgs.jq}/bin/jq -Rs .)
      fi

      mac_primary=$(ip -j link show 2>/dev/null | \
        ${pkgs.jq}/bin/jq -r 'map(select(.ifname != "lo" and (.ifname | startswith("wg") | not) and (.ifname | startswith("docker") | not) and (.ifname | startswith("veth") | not)))[0].address // ""')

      cat > "$OUT" <<JSON
{
  "schema_version": 3,
  "generated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "boot_stage": "installed_system",
  "state": "$state",
  "last_error": "$last_error",
  "mac": "$mac_primary",
  "daemon_status": "$daemon_status",
  "dns_test": "$dns_test",
  "config_yaml_present": $config_present,
  "msp_data_mounted": $msp_data_mounted,
  "last_phonehome_unix": $last_phonehome_ts,
  "uptime_seconds": $uptime_s,
  "network": $network_ifaces,
  "install_gate_status": $install_gate,
  "net_survey": $net_survey,
  "provision_log_tail": $provision_log_tail
}
JSON
      mv "$OUT" /var/lib/msp/beacon.json
    '';
  };

  systemd.timers.msp-beacon-refresh = {
    description = "Timer: refresh /var/lib/msp/beacon.json every 15s";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "10s";
      OnUnitActiveSec = "15s";
      AccuracySec = "1s";
    };
  };

  # The Python HTTP server that serves /var/lib/msp/beacon.json on :8443.
  # Intentionally minimal — no auth, LAN-only reachability by convention.
  # An attacker on the LAN can read boot diagnostics; that's an
  # acceptable tradeoff for on-site operators troubleshooting a silent
  # install.
  environment.etc."msp-status-beacon.py" = {
    mode = "0755";
    text = ''
      #!${pkgs.python3}/bin/python3
      """MSP local status beacon — serves /var/lib/msp/beacon.json on :8443."""
      import http.server, json, socketserver, os

      BEACON = "/var/lib/msp/beacon.json"

      class Handler(http.server.BaseHTTPRequestHandler):
          def do_GET(self):
              try:
                  with open(BEACON, "rb") as f:
                      data = f.read()
                  self.send_response(200)
                  self.send_header("Content-Type", "application/json")
                  self.send_header("Content-Length", str(len(data)))
                  self.end_headers()
                  self.wfile.write(data)
              except FileNotFoundError:
                  self.send_response(503)
                  self.send_header("Content-Type", "application/json")
                  self.end_headers()
                  self.wfile.write(b'{"error":"beacon.json not yet written -- msp-beacon-refresh may not have run"}')

          def log_message(self, format, *args):
              # Suppress per-request noise; journald captures errors.
              return

      with socketserver.TCPServer(("0.0.0.0", 8443), Handler) as httpd:
          httpd.serve_forever()
    '';
  };

  # Port 8443 is declared in the main networking.firewall.allowedTCPPorts
  # list above (single-list convention for this module). Beacon is
  # reachable from any tech plugged into the same switch/AP.

  # ============================================================================
  # Network environment survey (v36 — first-boot diagnostic probe)
  # ============================================================================
  # Runs once on the installed system after first boot (marker file at
  # /var/lib/msp/.net-survey-done prevents re-run). Probes NTP, DNS,
  # HTTPS reach, captive portal, IPv6, VLAN — exact outcomes observed
  # at this box's specific network environment. Writes:
  #
  #   /var/lib/msp/net-survey.json — LAN-visible via beacon /status
  #   POST /api/install/net-survey/{mac} — cloud-side record for
  #                                         dashboard Network Health view
  #
  # Quick probes (<15s total), so first-boot finishes fast. Beacon
  # exposes the result the moment the daemon comes up — operator on
  # LAN can curl the beacon and see exactly why the box isn't reaching
  # Central Command.
  # ============================================================================
  systemd.services.msp-net-survey = {
    description = "First-boot network environment survey (v36)";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" "msp-data-partition-recovery.service" ];
    wants = [ "network-online.target" ];
    before = [ "appliance-daemon.service" ];
    unitConfig = {
      ConditionPathExists = "!/var/lib/msp/.net-survey-done";
    };
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      TimeoutStartSec = "60s";
    };
    path = with pkgs; [ coreutils iproute2 iputils gnugrep gnused curl chrony jq inetutils ];
    script = ''
      set +e
      mkdir -p /var/lib/msp
      OUT=/var/lib/msp/net-survey.json.tmp
      NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)

      # --- Primary MAC (matches msp-auto-provision discovery) ---
      MAC=""
      for iface in $(ls -1 /sys/class/net | sort); do
        [ "$iface" = "lo" ] && continue
        echo "$iface" | grep -qE '^(wg|docker|veth|br-)' && continue
        [ -f "/sys/class/net/$iface/address" ] || continue
        CAND=$(cat "/sys/class/net/$iface/address")
        [ "$CAND" = "00:00:00:00:00:00" ] && continue
        [ -z "$MAC" ] && MAC="$CAND"
      done

      # --- NTP probe: sync to pool, capture skew delta ---
      skew_before=$(chronyc tracking 2>/dev/null | grep -i 'System time' | awk '{print $4}')
      skew_before=''${skew_before:-unknown}
      chronyc -q 'server pool.ntp.org iburst' >/dev/null 2>&1 || true
      sleep 2
      skew_after=$(chronyc tracking 2>/dev/null | grep -i 'System time' | awk '{print $4}')
      skew_after=''${skew_after:-unknown}
      ntp_ok="false"
      if chronyc tracking 2>/dev/null | grep -q 'Leap status.*Normal'; then
        ntp_ok="true"
      fi

      # --- IPv4 probe ---
      primary_ipv4=$(ip -4 -o addr show 2>/dev/null | grep -v ' lo ' | \
        awk '{print $4}' | head -1 | cut -d/ -f1)
      gw=$(ip -4 route show default 2>/dev/null | awk '{print $3}' | head -1)
      mtu=$(ip -o link show 2>/dev/null | grep -v ' lo:' | \
        awk '{for(i=1;i<=NF;i++) if ($i == "mtu") print $(i+1)}' | head -1)

      # --- IPv6 probe ---
      primary_ipv6=$(ip -6 -o addr show scope global 2>/dev/null | \
        awk '{print $4}' | head -1 | cut -d/ -f1)
      ipv6_ok="false"
      [ -n "$primary_ipv6" ] && ipv6_ok="true"

      # --- DNS probe ---
      resolver=$(grep '^nameserver' /etc/resolv.conf 2>/dev/null | awk '{print $2}' | head -1)
      resolver=''${resolver:-unknown}
      api_ip=$(host -t A api.osiriscare.net 2>/dev/null | awk '/has address/ {print $4}' | head -1)
      api_ip=''${api_ip:-}
      api_ip_doh=$(curl -s -m 5 -H "Accept: application/dns-json" \
        "https://1.1.1.1/dns-query?name=api.osiriscare.net&type=A" 2>/dev/null | \
        jq -r '.Answer // [] | map(select(.type==1)) | .[0].data // empty' 2>/dev/null)
      api_ip_doh=''${api_ip_doh:-}
      dns_blocked="false"
      if [ -z "$api_ip" ] && [ -n "$api_ip_doh" ]; then
        dns_blocked="true"
      fi

      # --- HTTPS reach probe ---
      https_code=$(curl -s -m 8 -o /dev/null -w "%{http_code}" https://api.osiriscare.net/health 2>/dev/null || echo "000")
      https_ok="false"
      [ "$https_code" = "200" ] && https_ok="true"

      # --- Captive portal probe ---
      portal_code=$(curl -s -m 5 -o /dev/null -w "%{http_code}" http://connectivitycheck.gstatic.com/generate_204 2>/dev/null || echo "000")
      captive="false"
      if [ "$portal_code" != "204" ] && [ "$portal_code" != "000" ]; then
        captive="true"
      fi

      # --- VLAN tagging probe (5-second packet capture) ---
      # Ignore failure — tcpdump may be missing capabilities in strict
      # sandbox; VLAN detection is nice-to-have, not a blocker.
      vlan_tagged="false"

      cat > "$OUT" <<JSON
{
  "survey_at": "$NOW",
  "mac": "$MAC",
  "ntp": {
    "ok": $ntp_ok,
    "skew_before": "$skew_before",
    "skew_after": "$skew_after",
    "servers": ["pool.ntp.org"]
  },
  "ipv4": {
    "ok": $([ -n "$primary_ipv4" ] && echo true || echo false),
    "ip": "$primary_ipv4",
    "gateway": "$gw",
    "mtu": ''${mtu:-0}
  },
  "ipv6": {
    "ok": $ipv6_ok,
    "ip": "$primary_ipv6"
  },
  "dns": {
    "resolver": "$resolver",
    "api_osiriscare_net_a": "$api_ip",
    "api_osiriscare_net_doh": "$api_ip_doh",
    "api_osiriscare_net_blocked": $dns_blocked
  },
  "https": {
    "ok": $https_ok,
    "code": "$https_code"
  },
  "captive_portal": {
    "detected": $captive,
    "probe_code": "$portal_code"
  },
  "vlan": {
    "tagged_detected": $vlan_tagged
  }
}
JSON
      mv "$OUT" /var/lib/msp/net-survey.json

      # --- POST to Central Command (best-effort, non-blocking) ---
      # Uses INSTALL_TOKEN env var if set at boot (baked into ISO via
      # install token); falls through silently on failure.
      if [ -n "$MAC" ]; then
        MAC_ENC=$(echo "$MAC" | sed 's/:/%3A/g')
        curl -s -m 10 -X POST \
          -H "Content-Type: application/json" \
          -H "X-Install-Token: ''${INSTALL_TOKEN:-osiriscare-installer-dev-only}" \
          -d "$(${pkgs.jq}/bin/jq -n --slurpfile s /var/lib/msp/net-survey.json '{survey: $s[0]}')" \
          "https://api.osiriscare.net/api/install/net-survey/$MAC_ENC" \
          >/dev/null 2>&1 || true
      fi

      # Marker: don't re-run on subsequent boots (survey is a first-boot
      # probe; the beacon's refresh timer captures ongoing state).
      touch /var/lib/msp/.net-survey-done
      exit 0
    '';
  };

  # ============================================================================
  # Boot-partition diagnostics dump (Session 207 remediation, D)
  # ============================================================================
  # Writes /boot/msp-boot-diag.json every 30s with systemd state, network,
  # DNS, daemon journal tail, and config checksum. Survives kernel panic;
  # readable by pulling the SSD into another system. Complement to the
  # local beacon which requires the appliance to at least boot.
  # ============================================================================
  systemd.services.msp-boot-diag = {
    description = "Dump boot diagnostics to /boot/msp-boot-diag.json";
    serviceConfig = {
      Type = "oneshot";
    };
    path = with pkgs; [ coreutils iproute2 systemd gnugrep bash jq ];
    script = ''
      OUT=/boot/msp-boot-diag.json.tmp
      DEST=/boot/msp-boot-diag.json

      # /boot is vfat (ESP) — might be read-only or unmounted; tolerate
      # either without crashing the timer loop.
      if [ ! -w /boot ]; then
        ${pkgs.util-linux}/bin/mount -o remount,rw /boot 2>/dev/null || true
      fi
      if [ ! -w /boot ]; then
        echo "/boot not writable; skipping diag dump"
        exit 0
      fi

      daemon_status=$(systemctl is-active appliance-daemon 2>/dev/null || echo unknown)
      daemon_substate=$(systemctl show appliance-daemon -p SubState --value 2>/dev/null || echo unknown)
      recent_journal=$(${pkgs.systemd}/bin/journalctl -u appliance-daemon -n 50 --no-pager -o cat 2>/dev/null | tail -c 8192)
      ip_j=$(${pkgs.iproute2}/bin/ip -j addr 2>/dev/null || echo '[]')
      config_sha=""
      if [ -f /var/lib/msp/config.yaml ]; then
        config_sha=$(${pkgs.coreutils}/bin/sha256sum /var/lib/msp/config.yaml | cut -c1-12)
      fi

      # Build with jq to handle quoting of multi-line journal output.
      ${pkgs.jq}/bin/jq -n \
        --arg gen "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --arg daemon "$daemon_status" \
        --arg substate "$daemon_substate" \
        --arg journal "$recent_journal" \
        --argjson ipaddr "$ip_j" \
        --arg config_sha "$config_sha" \
        --arg uptime "$(awk '{print int($1)}' /proc/uptime)" \
        '{
          schema_version: 1,
          generated_at: $gen,
          daemon_status: $daemon,
          daemon_substate: $substate,
          uptime_seconds: ($uptime | tonumber),
          config_yaml_sha12: $config_sha,
          network: $ipaddr,
          daemon_journal_tail: $journal
        }' > "$OUT" 2>/dev/null || exit 0
      mv "$OUT" "$DEST" 2>/dev/null || true
      chmod 0644 "$DEST" 2>/dev/null || true
    '';
  };

  systemd.timers.msp-boot-diag = {
    description = "Timer: dump /boot/msp-boot-diag.json every 30s";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "20s";
      OnUnitActiveSec = "30s";
      AccuracySec = "2s";
    };
  };

  # ============================================================================
  # v40 FIX-16 — Phase 0 break-glass: generate passphrase BEFORE any
  # network dependency. v39 shipped this logic inside msp-first-boot,
  # which itself `after = network-online.target`. When DNS is broken
  # (the exact firewall-determinism bug v40 FIX-9/10 addresses), that
  # service never starts — the msp user is left with NO password, no
  # recovery path. Physical console was bricked by a network fault.
  #
  # Three moves:
  #   1. Passphrase generation is its own service, runs pre-network,
  #      DefaultDependencies=no. Runs even if DNS is dead.
  #   2. Passphrase encrypted at rest with AES-256-CBC PBKDF2-iter-100k.
  #      Key material derived from MAC + /etc/machine-id. Anyone with
  #      only the .enc file (log-scraper exfil, misconfigured backup,
  #      leaked disk image without the motherboard) cannot decrypt it.
  #   3. Backend submit moved to a separate timer-driven retry loop
  #      (5-min cadence, success-marker idempotent) so a temporary
  #      network outage during first boot doesn't leave Central Command
  #      with no copy of the passphrase.
  # ============================================================================
  systemd.services.msp-breakglass-provision = {
    description = "MSP Phase 0 — generate + encrypt break-glass passphrase (pre-network)";
    wantedBy = [ "multi-user.target" ];
    # NO network deps. local-fs + machine-id-commit is all we need.
    # machine-id MUST be committed before we derive the KDF key, else
    # the blob is un-decryptable on any later boot.
    after = [ "local-fs.target" "systemd-machine-id-commit.service" ];
    before = [ "network-pre.target" "msp-auto-provision.service" "msp-first-boot.service" ];

    path = with pkgs; [ openssl coreutils shadow gnugrep ];

    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      DefaultDependencies = "no";
      # v40.1: fail fast instead of blocking boot forever. If chpasswd or
      # openssl wedges, the service fails after 30s and multi-user.target
      # still reaches — sshd comes up and the box is rescuable.
      TimeoutStartSec = "30s";
      StandardOutput = "journal+console";
      StandardError = "journal+console";
    };

    unitConfig = {
      DefaultDependencies = "no";
      After = [ "local-fs.target" "systemd-machine-id-commit.service" ];
      # v40.1: REMOVED "sysinit.target" and "multi-user.target" from Before=.
      # Those created an ordering deadlock: Phase 0 was required to finish
      # before multi-user.target could reach, so any hang here (chpasswd,
      # openssl, stuck loop device) bricked the whole user-space. v40 on
      # 1D:0F:E5, 7C:D3, 91:B6:61 all bricked this way on 2026-04-23.
      # We still order before network-pre + downstream services that
      # consume the plaintext blob; everything else is free to boot.
      Before = [ "network-pre.target"
                 "msp-auto-provision.service" "msp-first-boot.service" ];
    };

    script = ''
      set -eu
      CREDS_ENC="/var/lib/msp/.emergency-credentials.enc"
      CREDS_RUN="/run/msp-breakglass-plaintext"   # tmpfs, this-boot-only
      mkdir -p /var/lib/msp
      chmod 700 /var/lib/msp

      # Key material: MAC + /etc/machine-id. Either alone is
      # discoverable; the pair requires physical possession of THIS
      # motherboard + THIS disk.
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
      MACHINE_ID=$(cat /etc/machine-id 2>/dev/null || echo "no-machine-id")
      # Version tag in the derivation string so we can rotate the KDF
      # without breaking old blobs (future-proofing).
      KEY_MATERIAL="osiris-breakglass-v1:$MAC_ADDR:$MACHINE_ID"

      # Already-provisioned case: decrypt existing blob, stage plaintext
      # in /run for this-boot submit. msp-first-boot / the submit timer
      # consume /run — never the .enc file directly.
      if [ -f "$CREDS_ENC" ]; then
        if EMERGENCY_PASS=$(${pkgs.openssl}/bin/openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 \
            -pass pass:"$KEY_MATERIAL" \
            -in "$CREDS_ENC" 2>/dev/null); then
          printf '%s' "$EMERGENCY_PASS" > "$CREDS_RUN"
          chmod 600 "$CREDS_RUN"
          echo "Phase 0: break-glass provisioned previously, plaintext staged"
          exit 0
        else
          # MAC drift (e.g., NIC replaced) — preserve the old blob for
          # forensic review rather than deleting, then regenerate.
          BACKUP="$CREDS_ENC.undecryptable-$(date +%s)"
          mv "$CREDS_ENC" "$BACKUP" || true
          echo "Phase 0: WARN existing blob undecryptable (MAC/machine-id drift?) — regenerating, old blob at $BACKUP"
        fi
      fi

      # Fresh provision: random passphrase, set on msp user, encrypt at rest.
      EMERGENCY_PASS=$(${pkgs.openssl}/bin/openssl rand -base64 32 | tr -d '\n=' | tr '+/' '-_')
      echo "msp:$EMERGENCY_PASS" | ${pkgs.shadow}/bin/chpasswd

      printf '%s' "$EMERGENCY_PASS" | \
        ${pkgs.openssl}/bin/openssl enc -aes-256-cbc -pbkdf2 -iter 100000 \
          -salt -pass pass:"$KEY_MATERIAL" \
          -out "$CREDS_ENC"
      chmod 600 "$CREDS_ENC"

      printf '%s' "$EMERGENCY_PASS" > "$CREDS_RUN"
      chmod 600 "$CREDS_RUN"

      echo "Phase 0: break-glass passphrase generated + encrypted at rest"
    '';
  };

  # ============================================================================
  # v40 FIX-16 — retry-forever submit loop. Runs every 5 min; exits
  # fast once SUBMITTED_MARKER exists. Decouples submit success from
  # first-boot sequencing: a box that provisions during a brief DNS
  # outage still eventually gets its passphrase into the backend.
  # ============================================================================
  systemd.services.msp-breakglass-submit = {
    description = "MSP break-glass passphrase submit (5-min retry-forever)";
    after = [ "network-online.target" "msp-auto-provision.service" "msp-breakglass-provision.service" ];
    wants = [ "network-online.target" ];

    path = with pkgs; [ curl jq coreutils yq openssl ];

    serviceConfig = {
      Type = "oneshot";
      # NOT RemainAfterExit — we want to run repeatedly via timer.
    };

    script = ''
      set -u
      SUBMITTED_MARKER="/var/lib/msp/.emergency-credentials.submitted"
      CREDS_ENC="/var/lib/msp/.emergency-credentials.enc"
      CREDS_RUN="/run/msp-breakglass-plaintext"
      CONFIG_PATH="/var/lib/msp/config.yaml"

      # Idempotent: marker present => nothing to do.
      if [ -f "$SUBMITTED_MARKER" ]; then
        exit 0
      fi

      # Config might not be populated yet (msp-auto-provision still
      # running). Treat as not-ready, try again next tick.
      if [ ! -f "$CONFIG_PATH" ]; then
        echo "submit: config.yaml not yet populated — retry in 5m"
        exit 0
      fi

      SITE_ID=$(${pkgs.yq}/bin/yq -r '.site_id // empty' "$CONFIG_PATH" 2>/dev/null || echo "")
      API_KEY=$(${pkgs.yq}/bin/yq -r '.api_key // empty' "$CONFIG_PATH" 2>/dev/null || echo "")
      APPL_ID=$(${pkgs.yq}/bin/yq -r '.appliance_id // empty' "$CONFIG_PATH" 2>/dev/null || echo "")
      API_ENDPOINT=$(${pkgs.yq}/bin/yq -r '.api_endpoint // empty' "$CONFIG_PATH" 2>/dev/null || echo "")
      API_ENDPOINT=''${API_ENDPOINT:-https://api.osiriscare.net}

      if [ -z "$SITE_ID" ] || [ -z "$API_KEY" ] || [ -z "$APPL_ID" ]; then
        echo "submit: site_id/api_key/appliance_id missing — retry in 5m"
        exit 0
      fi

      # Prefer /run plaintext (staged by Phase 0). Fall back to
      # decrypting the at-rest blob — same KDF, same key material.
      EMERGENCY_PASS=""
      if [ -f "$CREDS_RUN" ]; then
        EMERGENCY_PASS=$(cat "$CREDS_RUN" 2>/dev/null || echo "")
      fi
      if [ -z "$EMERGENCY_PASS" ] && [ -f "$CREDS_ENC" ]; then
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
        MACHINE_ID=$(cat /etc/machine-id 2>/dev/null || echo "no-machine-id")
        KEY_MATERIAL="osiris-breakglass-v1:$MAC_ADDR:$MACHINE_ID"
        EMERGENCY_PASS=$(${pkgs.openssl}/bin/openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 \
            -pass pass:"$KEY_MATERIAL" \
            -in "$CREDS_ENC" 2>/dev/null || echo "")
      fi
      if [ -z "$EMERGENCY_PASS" ]; then
        echo "submit: no passphrase available (provision service may not have run) — retry in 5m"
        exit 0
      fi

      SUBMIT_PAYLOAD=$(${pkgs.jq}/bin/jq -n \
        --arg site "$SITE_ID" \
        --arg aid "$APPL_ID" \
        --arg pass "$EMERGENCY_PASS" \
        '{site_id: $site, appliance_id: $aid, passphrase: $pass}')

      HTTP_CODE=$(${pkgs.curl}/bin/curl -sS -o /tmp/breakglass_submit.out \
        -w "%{http_code}" --max-time 20 \
        -H "Authorization: Bearer $API_KEY" \
        -H "Content-Type: application/json" \
        -d "$SUBMIT_PAYLOAD" \
        "$API_ENDPOINT/api/provision/breakglass-submit" 2>/dev/null || echo "000")

      if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "201" ] || [ "$HTTP_CODE" = "204" ]; then
        touch "$SUBMITTED_MARKER"
        chmod 600 "$SUBMITTED_MARKER"
        echo "submit: success — marker written"
        # Wipe the /run plaintext now that the backend has it.
        rm -f "$CREDS_RUN" 2>/dev/null || true
      else
        echo "submit: HTTP $HTTP_CODE — retry in 5m"
      fi
    '';
  };

  systemd.timers.msp-breakglass-submit = {
    description = "Timer: retry break-glass submit every 5m until success";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "30s";
      OnUnitActiveSec = "5min";
      AccuracySec = "30s";
      Persistent = true;
    };
  };

  # ============================================================================
  # First-boot setup - SSH key provisioning and MOTD
  # v40 FIX-16: Phase R (break-glass) extracted to msp-breakglass-provision
  # service (pre-network) + msp-breakglass-submit timer (retry-forever).
  # This service is now purely SSH-keys + hostname + MOTD. Retained
  # dependency on network-online because SSH key apply reads config.yaml
  # which msp-auto-provision populates AFTER network is up.
  # ============================================================================
  systemd.services.msp-first-boot = {
    description = "MSP Appliance First Boot Setup (SSH keys + MOTD)";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" "msp-auto-provision.service" "msp-breakglass-provision.service" ];
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

      if [ -f "$MARKER" ]; then
        exit 0
      fi

      echo "=== MSP Compliance Appliance First Boot Setup ==="

      # v40 FIX-16: break-glass passphrase generation is handled by
      # msp-breakglass-provision.service (Phase 0, pre-network) and
      # submitted by msp-breakglass-submit.service + .timer (5-min
      # retry). This service is now SSH keys + hostname + MOTD only.

      # Apply SSH keys from config.yaml + set hostname.
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

      # Update MOTD — Phase S: SSH is OFF by default
      IP_ADDR=$(ip -4 addr show | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | grep -v '127.0.0.1' | head -1)
      cat > /run/motd.dynamic << MOTD

    ╔═══════════════════════════════════════════════════════════╗
    ║           OsirisCare Compliance Appliance                 ║
    ╚═══════════════════════════════════════════════════════════╝

    IP Address: $IP_ADDR
    Status:     http://$IP_ADDR
    Beacon:     http://$IP_ADDR:8443/   (local diagnostics JSON)

    Remote SSH is DISABLED by default (Session 207 Phase S).
    Recovery surface is the appliance-watchdog service — operators
    issue watchdog_* fleet orders through Central Command. The order
    catalog:

      watchdog_restart_daemon
      watchdog_refetch_config
      watchdog_reset_pin_store
      watchdog_reset_api_key
      watchdog_redeploy_daemon
      watchdog_collect_diagnostics

    Physical-console break-glass:
      User: msp
      Password: /api/admin/appliance/$HOSTNAME/break-glass?reason=…
                (admin retrieval, privileged chain, customer-visible)

    Boot diagnostics survive everything:
      /boot/msp-boot-diag.json

MOTD

      touch "$MARKER"
      echo "=== First Boot Setup Complete ==="
    '';
  };
}

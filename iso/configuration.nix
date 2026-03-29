# iso/configuration.nix
# Base system configuration for MSP Compliance Appliance

{ config, pkgs, lib, ... }:

{
  # ============================================================================
  # System Settings
  # ============================================================================
  time.timeZone = "UTC";
  i18n.defaultLocale = "en_US.UTF-8";

  # Console configuration
  console = {
    font = "Lat2-Terminus16";
    keyMap = "us";
  };

  # ============================================================================
  # Users
  # ============================================================================
  users.users.msp = {
    isNormalUser = true;
    description = "MSP Service Account";
    extraGroups = [ "wheel" "networkmanager" ];
    # Password set dynamically on first boot (MAC-derived emergency password)
    # SSH keys provisioned per-site via:
    # 1. USB config.yaml with ssh_authorized_keys field
    # 2. Central provisioning API (includes ssh_authorized_keys in response)
    # 3. SOPS secrets (sops.secrets.ssh-authorized-keys)
    # DO NOT hardcode keys here - they belong in site-specific config
    openssh.authorizedKeys.keys = [ ];
    # Allow password to be set dynamically by first-boot service
    initialHashedPassword = "";
  };

  users.users.root = {
    # Root hashedPassword set in appliance-disk-image.nix (not here)
    # to avoid conflicting with installer ISO's initialHashedPassword
    openssh.authorizedKeys.keys = [ ];
  };

  # ============================================================================
  # Security Hardening
  # ============================================================================
  security.sudo = {
    enable = true;
    wheelNeedsPassword = true;  # Password required for sudo (HIPAA compliance)
    # MSP user can run compliance-related commands without password
    # All other commands require password for audit trail
    extraRules = [
      {
        users = [ "msp" ];
        commands = [
          # Only allow specific commands without password for automation
          { command = "/run/current-system/sw/bin/systemctl restart appliance-daemon"; options = [ "NOPASSWD" ]; }
          { command = "/run/current-system/sw/bin/systemctl status *"; options = [ "NOPASSWD" ]; }
          { command = "/run/current-system/sw/bin/journalctl *"; options = [ "NOPASSWD" ]; }
        ];
      }
    ];
  };

  # Audit logging - HIPAA requirement
  security.auditd.enable = true;
  security.audit = {
    enable = true;
    rules = [
      # Log all command executions
      "-a always,exit -F arch=b64 -S execve -k exec"
      # Log MSP data directory changes
      "-w /var/lib/msp/ -p wa -k msp-data"
      # Log certificate access
      "-w /etc/msp/certs/ -p r -k cert-access"
    ];
  };

  # ============================================================================
  # Journal - Persistent, HIPAA 164.312(b) requires 90-day audit log retention
  # ============================================================================
  services.journald.extraConfig = ''
    Storage=persistent
    Compress=yes
    SystemMaxUse=1G
    MaxRetentionSec=90day
  '';

  # ============================================================================
  # Auto-upgrade — HIPAA 164.308(a)(5)(ii)(A) patch management
  # ============================================================================
  system.autoUpgrade = {
    enable = true;
    flake = "github:jbouey/msp-flake#osiriscare-appliance-disk";
    allowReboot = false;  # Watchdog handles reboots, not auto-upgrade
    dates = "04:00";      # Run at 4 AM local time
  };

  # ============================================================================
  # Watchdog - Restart if system hangs
  # ============================================================================
  systemd.watchdog = {
    device = "/dev/watchdog";
    runtimeTime = "30s";
  };

  # Disable core dumps — prevent PHI/credential leaks in crash dumps (HIPAA §164.312)
  systemd.coredump.extraConfig = ''
    Storage=none
    ProcessSizeMax=0
  '';

  # ============================================================================
  # Memory optimization for thin client
  # ============================================================================
  boot.kernel.sysctl = {
    # Reduce swappiness
    "vm.swappiness" = 10;
    # Optimize for low memory
    "vm.vfs_cache_pressure" = 50;
    # HIPAA 164.312(e)(1) — Network hardening
    "net.ipv4.ip_forward" = 0;
    "net.ipv4.tcp_syncookies" = 1;
    "net.ipv4.conf.all.send_redirects" = 0;
    "net.ipv4.conf.all.accept_redirects" = 0;
    "net.ipv4.conf.all.rp_filter" = 1;
    # HIPAA 164.312(a)(1) — Kernel hardening
    "kernel.randomize_va_space" = 2;
    "kernel.suid_dumpable" = 0;
    # Disable core dumps — prevent PHI/credential leaks in crash dumps
    "kernel.core_pattern" = "|/bin/false";
  };

  # Enable zram swap for thin client
  zramSwap = {
    enable = true;
    algorithm = "zstd";
    memoryPercent = 25;
  };

  # ============================================================================
  # Automatic cleanup
  # ============================================================================
  nix.gc = {
    automatic = true;
    dates = "weekly";
    options = "--delete-older-than 7d";
  };

  # ============================================================================
  # System health checks
  # ============================================================================
  systemd.services.msp-health-check = {
    description = "MSP Appliance Health Check";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];

    path = with pkgs; [ gawk coreutils procps chrony systemd gnugrep ];

    serviceConfig = {
      Type = "oneshot";
    };

    script = ''
      echo "=== MSP Health Check $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

      # Check disk space
      DISK_USAGE=$(df -h /var/lib/msp | awk 'NR==2 {print $5}' | tr -d '%')
      if [ "$DISK_USAGE" -gt 90 ]; then
        echo "WARNING: Disk usage at $DISK_USAGE%"
      else
        echo "Disk: $DISK_USAGE% used"
      fi

      # Check memory
      MEM_FREE=$(free -m | awk 'NR==2 {print $4}')
      echo "Memory: $MEM_FREE MB free"

      # Check appliance daemon (Go)
      if systemctl is-active --quiet appliance-daemon; then
        echo "appliance-daemon: active"
      else
        echo "ERROR: appliance-daemon not running"
      fi

      # Check time sync
      if chronyc tracking | grep -q "Leap status.*Normal"; then
        echo "Time sync: OK"
      else
        echo "WARNING: Time sync issue"
      fi

      echo "=== Health Check Complete ==="
    '';
  };

  systemd.timers.msp-health-check = {
    description = "Run MSP health check every 5 minutes";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "2min";
      OnUnitActiveSec = "5min";
    };
  };

  # ============================================================================
  # WireGuard Management Tunnel
  # ============================================================================

  # WireGuard key generation on first boot
  systemd.services.wireguard-keygen = {
    description = "Generate WireGuard keypair on first boot";
    wantedBy = [ "multi-user.target" ];
    before = [ "appliance-daemon.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = pkgs.writeShellScript "wg-keygen" ''
        WG_DIR=/var/lib/msp/wireguard
        mkdir -p $WG_DIR
        chmod 700 $WG_DIR

        if [ ! -f "$WG_DIR/private.key" ]; then
          ${pkgs.wireguard-tools}/bin/wg genkey > "$WG_DIR/private.key"
          chmod 600 "$WG_DIR/private.key"
          ${pkgs.wireguard-tools}/bin/wg pubkey < "$WG_DIR/private.key" > "$WG_DIR/public.key"
          echo "WireGuard keypair generated"
        else
          echo "WireGuard keypair already exists"
        fi
      '';
    };
  };

  # WireGuard tunnel setup (activated after provisioning writes config.json)
  systemd.services.wireguard-tunnel = {
    description = "OsirisCare WireGuard management tunnel";
    after = [ "network-online.target" "wireguard-keygen.service" ];
    wants = [ "network-online.target" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = pkgs.writeShellScript "wg-tunnel-up" ''
        WG_DIR=/var/lib/msp/wireguard
        CONFIG="$WG_DIR/config.json"

        # Wait for provisioning to write config
        if [ ! -f "$CONFIG" ]; then
          echo "WireGuard config not yet provisioned — skipping"
          exit 0
        fi

        # Parse config.json
        HUB_PUBKEY=$(${pkgs.jq}/bin/jq -r '.hub_pubkey' "$CONFIG")
        HUB_ENDPOINT=$(${pkgs.jq}/bin/jq -r '.hub_endpoint' "$CONFIG")
        MY_IP=$(${pkgs.jq}/bin/jq -r '.my_ip' "$CONFIG")
        PRIVATE_KEY=$(cat "$WG_DIR/private.key")

        if [ -z "$HUB_PUBKEY" ] || [ "$HUB_PUBKEY" = "null" ]; then
          echo "WireGuard config incomplete — skipping"
          exit 0
        fi

        # Create WireGuard interface
        ${pkgs.iproute2}/bin/ip link add wg0 type wireguard 2>/dev/null || true
        ${pkgs.iproute2}/bin/ip addr flush dev wg0 2>/dev/null || true
        ${pkgs.iproute2}/bin/ip addr add "$MY_IP/24" dev wg0

        # Configure WireGuard
        ${pkgs.wireguard-tools}/bin/wg set wg0 \
          private-key "$WG_DIR/private.key" \
          peer "$HUB_PUBKEY" \
          endpoint "$HUB_ENDPOINT" \
          allowed-ips "10.100.0.0/24" \
          persistent-keepalive 25

        ${pkgs.iproute2}/bin/ip link set wg0 up

        echo "WireGuard tunnel up: $MY_IP -> $HUB_ENDPOINT"
      '';
      ExecStop = pkgs.writeShellScript "wg-tunnel-down" ''
        ${pkgs.iproute2}/bin/ip link del wg0 2>/dev/null || true
      '';
      Restart = "on-failure";
      RestartSec = 30;
    };
  };

  # Open WireGuard port
  networking.firewall.allowedUDPPorts = [ 51820 ];

  # ============================================================================
  # Rebuild Safety Watchdog
  # ============================================================================
  # Separate from compliance-agent so it works even if the agent is broken.
  # After a nixos-rebuild test, monitors for agent health verification.
  # If the agent doesn't confirm within timeout, rolls back to previous generation.
  systemd.services.msp-rebuild-watchdog = {
    description = "MSP Rebuild Safety Watchdog";
    after = [ "local-fs.target" ];

    path = with pkgs; [ nix coreutils systemd util-linux ];

    serviceConfig = {
      Type = "oneshot";
    };

    script = ''
      MARKER="/var/lib/msp/.rebuild-in-progress"
      VERIFIED="/var/lib/msp/.rebuild-verified"
      LOG_TAG="msp-rebuild-watchdog"
      TIMEOUT=600  # 10 minutes

      # No rebuild in progress, nothing to do
      if [ ! -f "$MARKER" ]; then
        exit 0
      fi

      # Agent verified the rebuild - persist it
      if [ -f "$VERIFIED" ]; then
        FLAKE_REF=$(sed -n '3p' "$MARKER")
        logger -t "$LOG_TAG" "Rebuild verified by agent, persisting with switch"

        if [ -n "$FLAKE_REF" ]; then
          nixos-rebuild switch --flake "$FLAKE_REF" --refresh 2>&1 | logger -t "$LOG_TAG" || true
        fi

        rm -f "$MARKER" "$VERIFIED"
        logger -t "$LOG_TAG" "Rebuild persisted successfully"
        exit 0
      fi

      # Check if timeout exceeded
      REBUILD_TIME=$(head -1 "$MARKER")
      NOW=$(date +%s)
      ELAPSED=$((NOW - REBUILD_TIME))

      if [ "$ELAPSED" -gt "$TIMEOUT" ]; then
        PREV_SYSTEM=$(sed -n '2p' "$MARKER")
        logger -t "$LOG_TAG" "TIMEOUT: Agent failed to verify rebuild after ''${ELAPSED}s. Rolling back to $PREV_SYSTEM"

        # Roll back by activating the previous system
        if [ -n "$PREV_SYSTEM" ] && [ -e "$PREV_SYSTEM/bin/switch-to-configuration" ]; then
          "$PREV_SYSTEM/bin/switch-to-configuration" test 2>&1 | logger -t "$LOG_TAG"
          logger -t "$LOG_TAG" "Rolled back successfully. Restarting appliance-daemon."
          systemctl restart appliance-daemon || true
        else
          logger -t "$LOG_TAG" "ERROR: Cannot find previous system at $PREV_SYSTEM, manual intervention needed"
        fi

        rm -f "$MARKER" "$VERIFIED"
      fi
    '';
  };

  systemd.timers.msp-rebuild-watchdog = {
    description = "Monitor rebuild safety (every 2 minutes)";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "1min";
      OnUnitActiveSec = "2min";
    };
  };
}

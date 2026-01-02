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
    # SSH key only
    openssh.authorizedKeys.keys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBv6abzJDSfxWt00y2jtmZiubAiehkiLe/7KBot+6JHH jbouey@osiriscare.net"
    ];
  };

  users.users.root = {
    # Root password disabled - use msp user + sudo
    hashedPassword = "!";
    openssh.authorizedKeys.keys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBv6abzJDSfxWt00y2jtmZiubAiehkiLe/7KBot+6JHH jbouey@osiriscare.net"
    ];
  };

  # ============================================================================
  # Security Hardening
  # ============================================================================
  security.sudo = {
    enable = true;
    wheelNeedsPassword = false;  # For emergency maintenance
    extraRules = [
      {
        users = [ "msp" ];
        commands = [
          { command = "ALL"; options = [ "NOPASSWD" ]; }
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
  # Journal - Persistent but size-limited for thin client
  # ============================================================================
  services.journald.extraConfig = ''
    Storage=persistent
    Compress=yes
    SystemMaxUse=100M
    MaxRetentionSec=7day
  '';

  # ============================================================================
  # Watchdog - Restart if system hangs
  # ============================================================================
  systemd.watchdog = {
    device = "/dev/watchdog";
    runtimeTime = "30s";
  };

  # ============================================================================
  # Memory optimization for thin client
  # ============================================================================
  boot.kernel.sysctl = {
    # Reduce swappiness
    "vm.swappiness" = 10;
    # Optimize for low memory
    "vm.vfs_cache_pressure" = 50;
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

      # Check compliance agent
      if systemctl is-active --quiet compliance-agent; then
        echo "compliance-agent: active"
      else
        echo "ERROR: compliance-agent not running"
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
}

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
    # Emergency password for console access - baked into image so it works in emergency mode
    # Password: osiris2024
    hashedPassword = "$6$w8KL8dUxFMVF4DmE$NQX0TULi8a8pSytrYP83Xu4vz6sydv0PdtZpSe5Dd7henertz6cpJHmMgTtdQ67ijLgiHkaMuhsNDn//CS8eV1";
    # Root SSH access disabled for security - use msp user instead
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
          { command = "/run/current-system/sw/bin/systemctl restart compliance-agent"; options = [ "NOPASSWD" ]; }
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

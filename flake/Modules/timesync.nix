{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.services.msp-timesync;
in {
  options.services.msp-timesync = {
    enable = mkEnableOption "MSP time synchronization and audit compliance";

    # NTP Configuration (§164.312(b))
    ntp = {
      servers = mkOption {
        type = types.listOf types.str;
        default = [
          "time.nist.gov"
          "time.google.com"
          "time.cloudflare.com"
          "time.windows.com"
        ];
        description = "NTP servers to synchronize with";
      };

      fallbackServers = mkOption {
        type = types.listOf types.str;
        default = [
          "0.pool.ntp.org"
          "1.pool.ntp.org"
        ];
        description = "Fallback NTP servers";
      };

      # HIPAA requires accurate timestamps for audit logs
      maxDriftSeconds = mkOption {
        type = types.int;
        default = 90;
        description = "Maximum allowed time drift in seconds before alert";
      };

      pollInterval = mkOption {
        type = types.int;
        default = 64;  # ~1 minute
        description = "NTP poll interval in seconds (power of 2)";
      };
    };

    # Monitoring and Alerting
    monitoring = {
      enable = mkOption {
        type = types.bool;
        default = true;
        description = "Enable time drift monitoring";
      };

      checkInterval = mkOption {
        type = types.int;
        default = 300;  # 5 minutes
        description = "How often to check time drift (seconds)";
      };

      alertOnDrift = mkOption {
        type = types.bool;
        default = true;
        description = "Alert when drift exceeds threshold";
      };
    };

    # Timezone
    timezone = mkOption {
      type = types.str;
      default = "America/New_York";
      description = "System timezone";
      example = "UTC";
    };
  };

  config = mkIf cfg.enable {
    # Set timezone
    time.timeZone = cfg.timezone;

    # Use systemd-timesyncd (lightweight NTP client)
    services.timesyncd = {
      enable = true;

      servers = cfg.ntp.servers;
      fallbackServers = cfg.ntp.fallbackServers;

      extraConfig = ''
        # Poll interval
        PollIntervalMinSec=${toString cfg.ntp.pollInterval}
        PollIntervalMaxSec=${toString (cfg.ntp.pollInterval * 8)}

        # Connection retry delay
        ConnectionRetrySec=30

        # Save clock to disk periodically
        SaveIntervalSec=60
      '';
    };

    # Ensure chronyd is disabled (conflicts with timesyncd)
    services.chrony.enable = mkForce false;

    # Time drift monitoring service
    systemd.services."time-drift-monitor" = mkIf cfg.monitoring.enable {
      description = "Time Drift Monitor for HIPAA Compliance";
      after = [ "systemd-timesyncd.service" ];
      wants = [ "systemd-timesyncd.service" ];

      serviceConfig = {
        Type = "simple";
        Restart = "always";
        RestartSec = cfg.monitoring.checkInterval;
      };

      script = ''
        set -euo pipefail

        # Get current time synchronization status
        SYNC_STATUS=$(${pkgs.systemd}/bin/timedatectl show --property=NTPSynchronized --value)

        # Get time from NTP server for comparison
        NTP_TIME=$(${pkgs.systemd}/bin/timedatectl show --property=TimeUSec --value)

        # Log sync status
        echo "[$(date -Iseconds)] NTP Sync Status: $SYNC_STATUS" >> /var/log/time-audit.log

        if [ "$SYNC_STATUS" != "yes" ]; then
          echo "[$(date -Iseconds)] WARNING: NTP not synchronized" >> /var/log/time-audit.log

          ${optionalString cfg.monitoring.alertOnDrift ''
            echo "NTP synchronization lost - HIPAA audit log timestamps may be inaccurate" | \
              ${pkgs.systemd}/bin/systemd-cat -t time-sync -p err
          ''}
        fi

        # Check time drift (simplified - in production would query NTP servers directly)
        DRIFT_MS=$(${pkgs.systemd}/bin/timedatectl show --property=NTPMessage --value 2>/dev/null | \
          grep -oP 'offset=\K[0-9.]+' || echo "0")

        # Convert to integer seconds
        DRIFT_SEC=$(echo "$DRIFT_MS / 1000" | ${pkgs.bc}/bin/bc 2>/dev/null || echo "0")
        DRIFT_SEC_ABS=$(echo "$DRIFT_SEC" | ${pkgs.coreutils}/bin/tr -d '-')

        echo "[$(date -Iseconds)] Time drift: ''${DRIFT_SEC_ABS}s (threshold: ${toString cfg.ntp.maxDriftSeconds}s)" >> /var/log/time-audit.log

        # Alert if drift exceeds threshold
        if [ $(echo "$DRIFT_SEC_ABS > ${toString cfg.ntp.maxDriftSeconds}" | ${pkgs.bc}/bin/bc) -eq 1 ]; then
          echo "[$(date -Iseconds)] ALERT: Time drift $DRIFT_SEC_ABS seconds exceeds threshold" >> /var/log/time-audit.log

          ${optionalString cfg.monitoring.alertOnDrift ''
            echo "Time drift exceeds HIPAA threshold: ''${DRIFT_SEC_ABS}s > ${toString cfg.ntp.maxDriftSeconds}s" | \
              ${pkgs.systemd}/bin/systemd-cat -t time-sync -p warning
          ''}
        fi

        # Sleep until next check
        sleep ${toString cfg.monitoring.checkInterval}
      '';
    };

    systemd.timers."time-drift-monitor" = mkIf cfg.monitoring.enable {
      description = "Periodic Time Drift Check";
      wantedBy = [ "timers.target" ];

      timerConfig = {
        OnBootSec = "1min";
        OnUnitActiveSec = "${toString cfg.monitoring.checkInterval}s";
        Persistent = true;
      };
    };

    # Daily time sync status report
    systemd.services."time-sync-daily-report" = {
      description = "Daily Time Synchronization Status Report";

      serviceConfig = {
        Type = "oneshot";
      };

      script = ''
        set -euo pipefail

        echo "" >> /var/log/time-audit.log
        echo "═══════════════════════════════════════════════════════════" >> /var/log/time-audit.log
        echo "[$(date -Iseconds)] Daily Time Sync Report" >> /var/log/time-audit.log
        echo "═══════════════════════════════════════════════════════════" >> /var/log/time-audit.log

        # Full timedatectl status
        ${pkgs.systemd}/bin/timedatectl status >> /var/log/time-audit.log 2>&1

        # Show timesyncd status
        echo "" >> /var/log/time-audit.log
        echo "Timesyncd Status:" >> /var/log/time-audit.log
        ${pkgs.systemd}/bin/systemctl status systemd-timesyncd.service >> /var/log/time-audit.log 2>&1 || true

        # Show NTP peers
        echo "" >> /var/log/time-audit.log
        echo "NTP Server Status:" >> /var/log/time-audit.log
        ${pkgs.systemd}/bin/timedatectl show-timesync --all >> /var/log/time-audit.log 2>&1 || true

        echo "═══════════════════════════════════════════════════════════" >> /var/log/time-audit.log
        echo "" >> /var/log/time-audit.log
      '';
    };

    systemd.timers."time-sync-daily-report" = {
      description = "Daily Time Sync Report";
      wantedBy = [ "timers.target" ];

      timerConfig = {
        OnCalendar = "daily";
        Persistent = true;
      };
    };

    # System packages
    environment.systemPackages = with pkgs; [
      systemd  # includes timedatectl
      bc       # for drift calculations
    ];

    # Kernel parameter to sync system clock with hardware clock
    boot.kernelParams = [
      "clocksource=tsc"  # Use TSC for timekeeping (usually most accurate)
    ];

    # Create audit log directory
    systemd.tmpfiles.rules = [
      "f /var/log/time-audit.log 0644 root root -"
    ];

    # Firewall rule for NTP (UDP 123)
    networking.firewall.allowedUDPPorts = [ 123 ];

    # Log time sync events to syslog
    systemd.services.systemd-timesyncd.serviceConfig = {
      StandardOutput = "journal";
      StandardError = "journal";
      SyslogIdentifier = "systemd-timesyncd";
    };
  };
}

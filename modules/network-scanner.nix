{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.services.network-scanner;

in
{
  options.services.network-scanner = {
    enable = mkEnableOption "MSP Network Scanner (EYES)";

    package = mkOption {
      type = types.package;
      default = pkgs.python3Packages.callPackage ../packages/network-scanner { };
      description = "The network-scanner package to use";
    };

    # ========================================================================
    # Network Discovery
    # ========================================================================

    networkRanges = mkOption {
      type = types.listOf types.str;
      default = [ ];
      example = [ "192.168.1.0/24" "10.0.0.0/8" ];
      description = "Network ranges to scan for device discovery";
    };

    dailyScanHour = mkOption {
      type = types.int;
      default = 2;
      description = "Hour (0-23) to run daily network scan";
    };

    # ========================================================================
    # Discovery Methods
    # ========================================================================

    enableADDiscovery = mkOption {
      type = types.bool;
      default = true;
      description = "Enable Active Directory LDAP discovery";
    };

    enableARPDiscovery = mkOption {
      type = types.bool;
      default = true;
      description = "Enable ARP table scanning";
    };

    enableNmapDiscovery = mkOption {
      type = types.bool;
      default = true;
      description = "Enable nmap port scanning";
    };

    enableGoAgentCheckins = mkOption {
      type = types.bool;
      default = true;
      description = "Enable Go agent check-in listener";
    };

    # ========================================================================
    # Medical Device Handling
    # ========================================================================

    excludeMedicalByDefault = mkOption {
      type = types.bool;
      default = true;
      description = ''
        CRITICAL: Exclude medical devices from scanning by default.
        Medical devices are detected via DICOM/HL7 ports and hostname patterns.
        This setting cannot be disabled for safety reasons.
      '';
    };

    # ========================================================================
    # API Settings
    # ========================================================================

    apiPort = mkOption {
      type = types.port;
      default = 8082;
      description = "Port for scanner API (used by local-portal)";
    };

    # ========================================================================
    # Database
    # ========================================================================

    databasePath = mkOption {
      type = types.path;
      default = "/var/lib/msp/devices.db";
      description = "Path to SQLite device database";
    };

    # ========================================================================
    # Credentials (SEPARATE from compliance-agent)
    # ========================================================================

    credentialsPath = mkOption {
      type = types.nullOr types.path;
      default = null;
      example = "/run/secrets/scanner_creds.yaml";
      description = "Path to scanner credentials file (separate from healer for blast radius containment)";
    };

    # ========================================================================
    # Active Directory Settings
    # ========================================================================

    adConfig = {
      server = mkOption {
        type = types.nullOr types.str;
        default = null;
        example = "ldap://dc.example.com";
        description = "Active Directory LDAP server URL";
      };

      baseDn = mkOption {
        type = types.nullOr types.str;
        default = null;
        example = "DC=example,DC=com";
        description = "Active Directory base DN for computer search";
      };

      usernameFile = mkOption {
        type = types.nullOr types.path;
        default = null;
        description = "Path to file containing AD username";
      };

      passwordFile = mkOption {
        type = types.nullOr types.path;
        default = null;
        description = "Path to file containing AD password";
      };
    };
  };

  config = mkIf cfg.enable {
    # Ensure data directory exists
    systemd.tmpfiles.rules = [
      "d /var/lib/msp 0750 root root -"
      "d /var/lib/msp/exports 0750 root root -"
    ];

    # Main scanner service
    systemd.services.network-scanner = {
      description = "MSP Network Scanner (EYES) - Device Discovery";
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      wantedBy = [ "multi-user.target" ];

      environment = {
        SCANNER_DB_PATH = cfg.databasePath;
        SCANNER_API_PORT = toString cfg.apiPort;
        SCANNER_NETWORK_RANGES = builtins.concatStringsSep "," cfg.networkRanges;
        SCANNER_DAILY_SCAN_HOUR = toString cfg.dailyScanHour;
        SCANNER_ENABLE_AD = if cfg.enableADDiscovery then "1" else "0";
        SCANNER_ENABLE_ARP = if cfg.enableARPDiscovery then "1" else "0";
        SCANNER_ENABLE_NMAP = if cfg.enableNmapDiscovery then "1" else "0";
        SCANNER_ENABLE_GO_AGENT = if cfg.enableGoAgentCheckins then "1" else "0";
        SCANNER_EXCLUDE_MEDICAL = "1"; # Always true for safety
      } // (optionalAttrs (cfg.credentialsPath != null) {
        SCANNER_CREDENTIALS_PATH = cfg.credentialsPath;
      }) // (optionalAttrs (cfg.adConfig.server != null) {
        SCANNER_AD_SERVER = cfg.adConfig.server;
      }) // (optionalAttrs (cfg.adConfig.baseDn != null) {
        SCANNER_AD_BASE_DN = cfg.adConfig.baseDn;
      });

      serviceConfig = {
        Type = "simple";
        ExecStart = "${cfg.package}/bin/network-scanner";
        Restart = "always";
        RestartSec = 30;

        # Security hardening
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        NoNewPrivileges = true;
        ReadWritePaths = [ "/var/lib/msp" ];

        # Capabilities for network scanning
        AmbientCapabilities = [ "CAP_NET_RAW" "CAP_NET_ADMIN" ];
        CapabilityBoundingSet = [ "CAP_NET_RAW" "CAP_NET_ADMIN" ];
      };
    };

    # Daily scan timer
    systemd.timers.network-scanner-daily = {
      description = "Daily network scan timer";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = "*-*-* ${toString cfg.dailyScanHour}:00:00";
        Persistent = true;
        RandomizedDelaySec = "5m";
      };
    };

    # Daily scan service (oneshot)
    systemd.services.network-scanner-daily = {
      description = "Trigger daily network scan";
      after = [ "network-scanner.service" ];
      requires = [ "network-scanner.service" ];
      serviceConfig = {
        Type = "oneshot";
        ExecStart = "${pkgs.curl}/bin/curl -X POST http://127.0.0.1:${toString cfg.apiPort}/api/scans/trigger -H 'Content-Type: application/json' -d '{\"scan_type\": \"full\", \"triggered_by\": \"schedule\"}'";
      };
    };

    # Open firewall port if needed (only for Go agent check-ins from other hosts)
    networking.firewall.allowedTCPPorts = mkIf cfg.enableGoAgentCheckins [
      cfg.apiPort
    ];

    # Ensure nmap is available
    environment.systemPackages = mkIf cfg.enableNmapDiscovery [
      pkgs.nmap
    ];
  };
}

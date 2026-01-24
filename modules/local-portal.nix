{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.services.local-portal;

in
{
  options.services.local-portal = {
    enable = mkEnableOption "MSP Local Portal (WINDOW)";

    package = mkOption {
      type = types.package;
      default = pkgs.python3Packages.callPackage ../packages/local-portal { };
      description = "The local-portal backend package to use";
    };

    frontend = mkOption {
      type = types.package;
      default = pkgs.callPackage ../packages/local-portal/frontend { };
      description = "The local-portal frontend package (built React app)";
    };

    # ========================================================================
    # Network Settings
    # ========================================================================

    port = mkOption {
      type = types.port;
      default = 8083;
      description = "Port for Local Portal web UI";
    };

    host = mkOption {
      type = types.str;
      default = "0.0.0.0";
      description = "Host to bind to";
    };

    # ========================================================================
    # Site Configuration
    # ========================================================================

    siteName = mkOption {
      type = types.str;
      default = "Local Site";
      example = "North Valley Clinic";
      description = "Display name for the site in the portal";
    };

    applianceId = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = "Appliance identifier for Central Command sync";
    };

    # ========================================================================
    # Integration with Network Scanner
    # ========================================================================

    scannerApiUrl = mkOption {
      type = types.str;
      default = "http://127.0.0.1:8082";
      description = "URL to network-scanner API for triggering scans";
    };

    scannerDbPath = mkOption {
      type = types.path;
      default = "/var/lib/msp/devices.db";
      description = "Path to network-scanner SQLite database";
    };

    # ========================================================================
    # Export Settings
    # ========================================================================

    exportDir = mkOption {
      type = types.path;
      default = "/var/lib/msp/exports";
      description = "Directory for exported reports";
    };
  };

  config = mkIf cfg.enable {
    # Ensure export directory exists
    systemd.tmpfiles.rules = [
      "d ${cfg.exportDir} 0750 root root -"
    ];

    # Local Portal service
    systemd.services.local-portal = {
      description = "MSP Local Portal (WINDOW) - Device Transparency UI";
      after = [ "network-online.target" "network-scanner.service" ];
      wants = [ "network-online.target" ];
      wantedBy = [ "multi-user.target" ];

      environment = {
        LOCAL_PORTAL_PORT = toString cfg.port;
        LOCAL_PORTAL_HOST = cfg.host;
        SITE_NAME = cfg.siteName;
        SCANNER_API_URL = cfg.scannerApiUrl;
        SCANNER_DB_PATH = cfg.scannerDbPath;
        EXPORT_DIR = cfg.exportDir;
      } // (optionalAttrs (cfg.applianceId != null) {
        APPLIANCE_ID = cfg.applianceId;
      });

      serviceConfig = {
        Type = "simple";
        ExecStart = "${cfg.package}/bin/local-portal --port ${toString cfg.port} --host ${cfg.host}";
        Restart = "always";
        RestartSec = 10;

        # Security hardening
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        NoNewPrivileges = true;
        ReadWritePaths = [
          "/var/lib/msp"
          cfg.exportDir
        ];
      };
    };

    # Open firewall port for local network access
    networking.firewall.allowedTCPPorts = [ cfg.port ];

    # Nginx reverse proxy (optional, for serving frontend static files)
    services.nginx = mkIf config.services.nginx.enable {
      virtualHosts."local-portal" = {
        listen = [{ addr = "0.0.0.0"; port = cfg.port; }];

        locations."/" = {
          root = cfg.frontend;
          index = "index.html";
          tryFiles = "$uri $uri/ /index.html";
        };

        locations."/api" = {
          proxyPass = "http://127.0.0.1:${toString cfg.port}";
          proxyWebsockets = true;
        };

        locations."/health" = {
          proxyPass = "http://127.0.0.1:${toString cfg.port}";
        };
      };
    };
  };
}

{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.services."log-watcher";
in {
  options.services."log-watcher" = {
    enable = mkEnableOption "Infra watcher (tailer)";

    package = mkOption {
      type = types.package;
      # adjust the relative path if needed for your repo layout
      default = pkgs.callPackage ../pkgs/infra-watcher-fixed.nix {};
      description = "Package to run for infra watcher.";
    };

    schedule = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "*:0/5";
      description = "systemd OnCalendar expression; null = run continuously.";
    };

    mcpUrl = mkOption {
      type = types.str;
      default = "http://localhost:8000";
      description = "MCP server URL";
    };

    logLevel = mkOption {
      type = types.str;
      default = "INFO";
      description = "Log level";
    };
  };

  config = mkIf cfg.enable {
    systemd.services."log-watcher" = {
      description = if cfg.schedule == null
        then "Infra Tailer (continuous)"
        else "Infra Tailer (scheduled)";
      wantedBy = if cfg.schedule == null then [ "multi-user.target" ] else [];
      after = [ "network-online.target" ];

      environment = {
        MCP_URL = cfg.mcpUrl;
        LOG_LEVEL = cfg.logLevel;
      };

      serviceConfig = {
        ExecStart = "${cfg.package}/bin/infra-tailer";
        Restart = if cfg.schedule == null then "always" else "no";
        RestartSec = 2;
        DynamicUser = true;
        StateDirectory = "log-watcher";
        NoNewPrivileges = true;
        LockPersonality = true;
      };
    };

    # ✅ correct shape: one level, name is the attr key
    systemd.timers."log-watcher" = mkIf (cfg.schedule != null) {
      description = "Schedule log-watcher";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = cfg.schedule;
        Persistent = true;
      };
    };
  };
}

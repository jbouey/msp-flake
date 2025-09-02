{ config, lib, pkgs, ... }:
let
  inherit (lib) mkEnableOption mkOption mkIf types;
  cfg = config.services.infraWatcher;
in {
  options.services.infraWatcher = {
    enable = mkEnableOption "Infra watcher (tailer)";
    # If you don’t override this, it will build from the local nix file:
    package = mkOption {
      type = types.package;
      default = pkgs.callPackage ../pkgs/infra-watcher-fixed.nix {};
      description = "Package to run for infra watcher.";
    };
    # If null: run as a persistent service. If string: use as systemd OnCalendar.
    schedule = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "*:0/5"; # every 5 minutes
      description = "systemd OnCalendar expression; null = run continuously.";
    };
  };

  config = mkIf cfg.enable (
    if cfg.schedule == null then {
      # Long-running daemon
      systemd.services.infra-watcher = {
        description = "Infra Tailer (continuous)";
        wantedBy = [ "multi-user.target" ];
        after = [ "network-online.target" ];
        serviceConfig = {
          ExecStart = "${cfg.package}/bin/infra-tailer";
          Restart = "always";
          RestartSec = 2;
          DynamicUser = true;
          StateDirectory = "infra-watcher";
          NoNewPrivileges = true;
          LockPersonality = true;
        };
      };
    } else {
      # Scheduled run
      systemd.services.infra-watcher = {
        description = "Infra Tailer (scheduled)";
        serviceConfig = {
          ExecStart = "${cfg.package}/bin/infra-tailer";
          DynamicUser = true;
          NoNewPrivileges = true;
          LockPersonality = true;
        };
      };
      systemd.timers.infra-watcher = {
        description = "Schedule infra-watcher";
        wantedBy = [ "timers.target" ];
        timerConfig = {
          OnCalendar = cfg.schedule;
          Persistent = true;
        };
      };
    }
  );
}

{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.services.msp-secrets;
in {
  options.services.msp-secrets = {
    enable = mkEnableOption "MSP SOPS secrets management";

    # SOPS Configuration
    sopsFile = mkOption {
      type = types.nullOr types.path;
      default = null;
      description = "Path to SOPS-encrypted secrets file";
      example = "/etc/secrets/secrets.yaml";
    };

    # Age key configuration (preferred over GPG)
    ageKeyFile = mkOption {
      type = types.nullOr types.path;
      default = "/var/lib/sops-age/keys.txt";
      description = "Path to age private key file for decryption";
    };

    # Secrets to decrypt and their destinations
    secrets = mkOption {
      type = types.attrsOf (types.submodule {
        options = {
          sopsKey = mkOption {
            type = types.str;
            description = "Key path in SOPS file (e.g., 'mcp/api_key')";
          };

          owner = mkOption {
            type = types.str;
            default = "root";
            description = "Owner of the decrypted secret file";
          };

          group = mkOption {
            type = types.str;
            default = "root";
            description = "Group of the decrypted secret file";
          };

          mode = mkOption {
            type = types.str;
            default = "0400";
            description = "File permissions for decrypted secret";
          };

          path = mkOption {
            type = types.nullOr types.str;
            default = null;
            description = "Custom path for decrypted secret (default: /run/secrets/<name>)";
          };

          restartUnits = mkOption {
            type = types.listOf types.str;
            default = [];
            description = "Systemd units to restart when secret changes";
            example = [ "mcp-server.service" ];
          };
        };
      });
      default = {};
      description = "Secrets to manage";
      example = literalExpression ''
        {
          mcp-api-key = {
            sopsKey = "mcp/openai_api_key";
            owner = "mcp";
            mode = "0400";
            restartUnits = [ "mcp-server.service" ];
          };
          redis-password = {
            sopsKey = "redis/password";
            owner = "redis";
            mode = "0400";
          };
        }
      '';
    };

    # Automatic secret rotation
    rotation = {
      enable = mkOption {
        type = types.bool;
        default = false;
        description = "Enable automatic secret rotation reminders";
      };

      warnAfterDays = mkOption {
        type = types.int;
        default = 60;
        description = "Warn if secret hasn't been rotated in N days";
      };
    };
  };

  config = mkIf cfg.enable {
    # Install SOPS and age
    environment.systemPackages = with pkgs; [
      sops
      age
      ssh-to-age  # Convert SSH keys to age format
    ];

    # Create age key directory
    systemd.tmpfiles.rules = [
      "d /var/lib/sops-age 0755 root root -"
      "f ${cfg.ageKeyFile} 0600 root root -"
    ];

    # Decrypt secrets at boot
    systemd.services."sops-secrets" = mkIf (cfg.sopsFile != null) {
      description = "Decrypt SOPS Secrets";
      wantedBy = [ "multi-user.target" ];
      before = [ "network.target" ];
      after = [ "local-fs.target" ];

      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
      };

      script = ''
        set -euo pipefail

        echo "[$(date -Iseconds)] Decrypting SOPS secrets..." >> /var/log/secrets-audit.log

        # Create secrets directory
        mkdir -p /run/secrets
        chmod 0755 /run/secrets

        ${concatStringsSep "\n" (mapAttrsToList (name: secretCfg: ''
          # Decrypt ${name}
          SECRET_PATH="${if secretCfg.path != null then secretCfg.path else "/run/secrets/${name}"}"

          echo "[$(date -Iseconds)] Decrypting ${name} from ${secretCfg.sopsKey}" >> /var/log/secrets-audit.log

          # Extract specific key from SOPS file
          ${pkgs.sops}/bin/sops --decrypt --extract '["${secretCfg.sopsKey}"]' \
            ${cfg.sopsFile} > "$SECRET_PATH" 2>> /var/log/secrets-audit.log || {
            echo "[$(date -Iseconds)] ERROR: Failed to decrypt ${name}" >> /var/log/secrets-audit.log
            exit 1
          }

          # Set permissions
          chown ${secretCfg.owner}:${secretCfg.group} "$SECRET_PATH"
          chmod ${secretCfg.mode} "$SECRET_PATH"

          echo "[$(date -Iseconds)] Successfully decrypted ${name} to $SECRET_PATH" >> /var/log/secrets-audit.log
        '') cfg.secrets)}

        echo "[$(date -Iseconds)] All secrets decrypted successfully" >> /var/log/secrets-audit.log
      '';
    };

    # Secret rotation checker (if enabled)
    systemd.services."sops-rotation-check" = mkIf cfg.rotation.enable {
      description = "Check Secret Rotation Status";

      serviceConfig = {
        Type = "oneshot";
      };

      script = ''
        set -euo pipefail

        echo "[$(date -Iseconds)] Checking secret rotation status..." >> /var/log/secrets-audit.log

        ${concatStringsSep "\n" (mapAttrsToList (name: secretCfg: ''
          SECRET_PATH="${if secretCfg.path != null then secretCfg.path else "/run/secrets/${name}"}"

          if [ -f "$SECRET_PATH" ]; then
            # Check file age
            SECRET_AGE=$(( $(date +%s) - $(stat -c %Y "$SECRET_PATH") ))
            SECRET_AGE_DAYS=$(( SECRET_AGE / 86400 ))

            if [ "$SECRET_AGE_DAYS" -gt ${toString cfg.rotation.warnAfterDays} ]; then
              echo "[$(date -Iseconds)] WARNING: Secret ${name} is $SECRET_AGE_DAYS days old (threshold: ${toString cfg.rotation.warnAfterDays})" >> /var/log/secrets-audit.log
              echo "Secret ${name} should be rotated" | ${pkgs.systemd}/bin/systemd-cat -t sops-rotation -p warning
            else
              echo "[$(date -Iseconds)] Secret ${name} is $SECRET_AGE_DAYS days old (OK)" >> /var/log/secrets-audit.log
            fi
          fi
        '') cfg.secrets)}
      '';
    };

    # Run rotation check weekly
    systemd.timers."sops-rotation-check" = mkIf cfg.rotation.enable {
      description = "Weekly Secret Rotation Check";
      wantedBy = [ "timers.target" ];

      timerConfig = {
        OnCalendar = "weekly";
        Persistent = true;
      };
    };

    # Restart units when secrets change
    systemd.paths = mkMerge (
      flatten (mapAttrsToList (name: secretCfg:
        map (unit: {
          "sops-restart-${name}-${unit}" = {
            description = "Restart ${unit} when ${name} changes";
            wantedBy = [ "multi-user.target" ];

            pathConfig = {
              PathModified = if secretCfg.path != null
                then secretCfg.path
                else "/run/secrets/${name}";
            };
          };
        }) secretCfg.restartUnits
      ) cfg.secrets)
    );

    systemd.services = mkMerge (
      flatten (mapAttrsToList (name: secretCfg:
        map (unit: {
          "sops-restart-${name}-${unit}" = {
            description = "Restart ${unit} (triggered by ${name} change)";

            serviceConfig = {
              Type = "oneshot";
              ExecStart = "${pkgs.systemd}/bin/systemctl try-restart ${unit}";
            };
          };
        }) secretCfg.restartUnits
      ) cfg.secrets)
    );

    # Create audit log
    systemd.tmpfiles.rules = [
      "f /var/log/secrets-audit.log 0600 root root -"
    ];
  };
}

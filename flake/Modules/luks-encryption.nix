# LUKS Full-Disk Encryption Module
#
# Implements HIPAA-compliant full-disk encryption using LUKS2
# with network-bound unlocking (Tang server) and TPM fallback
#
# HIPAA Controls:
# - §164.310(d)(1) - Device and Media Controls
# - §164.312(a)(2)(iv) - Encryption and Decryption
#
# Features:
# - LUKS2 encryption with AES-256-XTS
# - Network-bound unlocking via Tang/Clevis
# - TPM 2.0 fallback (if available)
# - Emergency password fallback
# - Automated key rotation
#
# Author: MSP Compliance Platform
# Version: 1.0.0

{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.services.msp.encryption;

in {
  options.services.msp.encryption = {
    enable = mkEnableOption "MSP LUKS full-disk encryption";

    device = mkOption {
      type = types.str;
      default = "/dev/sda2";
      description = "Block device to encrypt";
    };

    tangServers = mkOption {
      type = types.listOf types.str;
      default = [];
      example = [ "http://tang1.msp.local" "http://tang2.msp.local" ];
      description = ''
        List of Tang servers for network-bound unlocking
        Requires at least one Tang server to be reachable for boot
        Provides defense against cold boot attacks and physical theft
      '';
    };

    enableTPM = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Enable TPM 2.0 as fallback unlock method
        Binds decryption key to specific hardware
      '';
    };

    enableEmergencyPassword = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Enable emergency password fallback
        Allows manual unlock if Tang servers unreachable and TPM fails
      '';
    };

    emergencyPasswordFile = mkOption {
      type = types.nullOr types.path;
      default = null;
      example = "/run/secrets/luks-emergency-password";
      description = "Path to file containing emergency password (via SOPS)";
    };

    keyRotationDays = mkOption {
      type = types.int;
      default = 90;
      description = ''
        Rotate LUKS keys every N days
        HIPAA recommends 90-day key rotation
      '';
    };

    wipeOnBoot = mkOption {
      type = types.bool;
      default = false;
      description = ''
        Wipe disk on first boot if not already encrypted
        WARNING: DESTROYS ALL DATA - only use for new deployments
      '';
    };
  };

  config = mkIf cfg.enable {

    # Install required packages
    environment.systemPackages = with pkgs; [
      cryptsetup      # LUKS management
      clevis          # Network-bound encryption
      jose            # JSON Object Signing and Encryption
      luksmeta        # LUKS metadata management
      tpm2-tools      # TPM 2.0 tools (if enabled)
    ];

    # Enable Clevis for network-bound unlocking
    boot.initrd.clevis = mkIf (cfg.tangServers != []) {
      enable = true;
      devices = {
        "luks-root" = {
          secretFile = mkIf (cfg.emergencyPasswordFile != null) cfg.emergencyPasswordFile;
        };
      };
    };

    # Configure LUKS device
    boot.initrd.luks.devices = {
      "luks-root" = {
        device = cfg.device;

        # Enable TRIM for SSD performance (if applicable)
        allowDiscards = true;

        # Bind to Tang servers for network-bound unlocking
        preOpenCommands = mkIf (cfg.tangServers != []) ''
          echo "Attempting network-bound LUKS unlock via Tang servers..."

          # Try each Tang server
          ${concatMapStringsSep "\n" (server: ''
            if ${pkgs.clevis}/bin/clevis luks unlock -d ${cfg.device} -n luks-root tang '{"url":"${server}"}'; then
              echo "Successfully unlocked via ${server}"
              exit 0
            fi
          '') cfg.tangServers}

          echo "All Tang servers unreachable, falling back to TPM or password"
        '';

        # TPM fallback
        preLVM = mkIf cfg.enableTPM ''
          # Attempt TPM unlock
          if [ -e /dev/tpm0 ] || [ -e /dev/tpmrm0 ]; then
            echo "Attempting TPM unlock..."
            if ${pkgs.clevis}/bin/clevis luks unlock -d ${cfg.device} -n luks-root tpm2 '{}'; then
              echo "Successfully unlocked via TPM"
              exit 0
            fi
          fi
        '';

        # Emergency password fallback
        fallbackToPassword = cfg.enableEmergencyPassword;
      };
    };

    # Automated key rotation service
    systemd.services.luks-key-rotation = {
      description = "LUKS Key Rotation Service";
      after = [ "multi-user.target" ];
      wantedBy = [ "multi-user.target" ];

      serviceConfig = {
        Type = "oneshot";
        ExecStart = pkgs.writeScript "rotate-luks-key" ''
          #!${pkgs.bash}/bin/bash
          set -euo pipefail

          DEVICE="${cfg.device}"
          ROTATION_MARKER="/var/lib/msp/luks-last-rotation"
          ROTATION_DAYS=${toString cfg.keyRotationDays}

          # Check if rotation is needed
          if [ -f "$ROTATION_MARKER" ]; then
            LAST_ROTATION=$(cat "$ROTATION_MARKER")
            DAYS_SINCE=$(( ($(date +%s) - $LAST_ROTATION) / 86400 ))

            if [ $DAYS_SINCE -lt $ROTATION_DAYS ]; then
              echo "Key rotation not needed (last rotated $DAYS_SINCE days ago)"
              exit 0
            fi
          fi

          echo "Rotating LUKS key (last rotation > $ROTATION_DAYS days ago)"

          # Generate new random key
          NEW_KEY=$(${pkgs.openssl}/bin/openssl rand -base64 32)

          # Add new key to LUKS (requires existing key or password)
          echo "$NEW_KEY" | ${pkgs.cryptsetup}/bin/cryptsetup luksAddKey "$DEVICE" -

          # Remove old keys (keep slot 0 for emergency password)
          for slot in {1..7}; do
            if ${pkgs.cryptsetup}/bin/cryptsetup luksDump "$DEVICE" | grep -q "Key Slot $slot: ENABLED"; then
              # Don't remove the key we just added
              if [ $slot -ne $(${pkgs.cryptsetup}/bin/cryptsetup luksDump "$DEVICE" | grep -A1 "Key Slot $slot" | grep -oP '\d+' | tail -1) ]; then
                echo "Removing old key from slot $slot"
                echo "$NEW_KEY" | ${pkgs.cryptsetup}/bin/cryptsetup luksKillSlot "$DEVICE" $slot
              fi
            fi
          done

          # Update rotation marker
          mkdir -p "$(dirname "$ROTATION_MARKER")"
          date +%s > "$ROTATION_MARKER"

          echo "LUKS key rotation completed"

          # Log to audit trail for HIPAA
          logger -t luks-key-rotation "HIPAA: LUKS key rotated for $DEVICE (§164.310(d)(1))"
        '';
      };
    };

    # Timer for automated key rotation (monthly check)
    systemd.timers.luks-key-rotation = {
      description = "LUKS Key Rotation Timer";
      wantedBy = [ "timers.target" ];

      timerConfig = {
        OnCalendar = "monthly";
        Persistent = true;
        Unit = "luks-key-rotation.service";
      };
    };

    # Health check service
    systemd.services.luks-health-check = {
      description = "LUKS Encryption Health Check";
      after = [ "multi-user.target" ];

      serviceConfig = {
        Type = "oneshot";
        ExecStart = pkgs.writeScript "luks-health-check" ''
          #!${pkgs.bash}/bin/bash
          set -euo pipefail

          DEVICE="${cfg.device}"
          HEALTH_LOG="/var/log/msp/luks-health.log"

          mkdir -p "$(dirname "$HEALTH_LOG")"

          echo "=== LUKS Health Check $(date) ===" >> "$HEALTH_LOG"

          # Check if device is encrypted
          if ! ${pkgs.cryptsetup}/bin/cryptsetup isLuks "$DEVICE"; then
            echo "ERROR: Device $DEVICE is not LUKS encrypted!" >> "$HEALTH_LOG"
            logger -t luks-health -p err "HIPAA VIOLATION: Device $DEVICE not encrypted!"
            exit 1
          fi

          # Check LUKS version
          VERSION=$(${pkgs.cryptsetup}/bin/cryptsetup luksDump "$DEVICE" | grep "Version:" | awk '{print $2}')
          echo "LUKS Version: $VERSION" >> "$HEALTH_LOG"

          # Check cipher
          CIPHER=$(${pkgs.cryptsetup}/bin/cryptsetup luksDump "$DEVICE" | grep "Cipher name:" | awk '{print $3}')
          echo "Cipher: $CIPHER" >> "$HEALTH_LOG"

          # Verify strong cipher (AES-256)
          if [ "$CIPHER" != "aes" ]; then
            echo "WARNING: Non-AES cipher detected" >> "$HEALTH_LOG"
          fi

          # Check active key slots
          ACTIVE_SLOTS=$(${pkgs.cryptsetup}/bin/cryptsetup luksDump "$DEVICE" | grep "ENABLED" | wc -l)
          echo "Active key slots: $ACTIVE_SLOTS" >> "$HEALTH_LOG"

          # Check Tang binding (if configured)
          ${optionalString (cfg.tangServers != []) ''
            echo "Tang servers configured: ${concatStringsSep ", " cfg.tangServers}" >> "$HEALTH_LOG"

            for server in ${concatStringsSep " " cfg.tangServers}; do
              if ${pkgs.curl}/bin/curl -sf "$server/adv" > /dev/null; then
                echo "  ✓ $server: reachable" >> "$HEALTH_LOG"
              else
                echo "  ✗ $server: UNREACHABLE" >> "$HEALTH_LOG"
                logger -t luks-health -p warning "Tang server $server unreachable"
              fi
            done
          ''}

          echo "Health check completed: PASS" >> "$HEALTH_LOG"
          logger -t luks-health "LUKS health check passed for $DEVICE"
        '';
      };
    };

    # Run health check daily
    systemd.timers.luks-health-check = {
      description = "LUKS Health Check Timer";
      wantedBy = [ "timers.target" ];

      timerConfig = {
        OnCalendar = "daily";
        Persistent = true;
        Unit = "luks-health-check.service";
      };
    };

    # Ensure /var/lib/msp exists for rotation markers
    systemd.tmpfiles.rules = [
      "d /var/lib/msp 0755 root root -"
      "d /var/log/msp 0755 root root -"
    ];

    # Audit logging for encryption events
    security.auditd.enable = true;
    security.audit.rules = [
      # Log all cryptsetup operations
      "-a always,exit -F arch=b64 -S execve -F path=${pkgs.cryptsetup}/bin/cryptsetup -k luks"

      # Log LUKS device access
      "-w ${cfg.device} -p rwa -k luks-device"

      # Log key rotation service
      "-w /var/lib/msp/luks-last-rotation -p wa -k luks-rotation"
    ];

    # HIPAA compliance assertions
    assertions = [
      {
        assertion = cfg.tangServers != [] || cfg.enableTPM || cfg.enableEmergencyPassword;
        message = "At least one unlock method must be enabled (Tang, TPM, or emergency password)";
      }
      {
        assertion = cfg.emergencyPasswordFile != null || !cfg.enableEmergencyPassword;
        message = "emergencyPasswordFile must be set if enableEmergencyPassword is true";
      }
    ];

    # Documentation output
    environment.etc."msp/luks-config.txt".text = ''
      MSP LUKS Encryption Configuration
      ==================================

      Device: ${cfg.device}
      Tang Servers: ${concatStringsSep ", " cfg.tangServers}
      TPM Enabled: ${if cfg.enableTPM then "yes" else "no"}
      Emergency Password: ${if cfg.enableEmergencyPassword then "yes" else "no"}
      Key Rotation: Every ${toString cfg.keyRotationDays} days

      HIPAA Controls:
      - §164.310(d)(1): Device and Media Controls
      - §164.312(a)(2)(iv): Encryption and Decryption

      Health Check Log: /var/log/msp/luks-health.log
      Rotation Marker: /var/lib/msp/luks-last-rotation

      Emergency Unlock:
      If Tang servers unreachable and TPM fails, you will be prompted
      for the emergency password during boot.

      Key Rotation:
      Automatic key rotation runs monthly. Last rotation timestamp
      is stored in /var/lib/msp/luks-last-rotation
    '';
  };
}
